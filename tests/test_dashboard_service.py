import tempfile
import unittest
import warnings
import base64
import json
import re
import zlib
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from comparison import backend, dashboard_service, database


class WebDashboardServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp_dir.name) / "dashboard-test.db"
        database.init_database()
        self.rows_2025 = pd.DataFrame([
            {
                "DOA": "05/01/2025", "DOD": "08/01/2025", "Month": "January",
                "Dept. Name": "Medicine", "Doctor Name": "Dr A", "Panel Name": "Panel A",
                "Type": "Discharge", "Service Type": "Consultation",
                "Service Name": "Consultation", "Service Final Amount": 1000,
                "Cash Due": 200, "Credit Due": 100, "Bed Days": 3, "UHID": "U1",
            },
            {
                "DOA": "09/01/2025", "DOD": None, "Month": "January",
                "Dept. Name": "Medicine", "Doctor Name": "Dr A", "Panel Name": "Panel A",
                "Type": "OPD", "Service Type": "Consultation",
                "Service Name": "Consultation", "Service Final Amount": 250,
                "Cash Due": 0, "Credit Due": 0, "Bed Days": 0, "UHID": "U2",
            },
        ])
        self.rows_2026 = pd.DataFrame([
            {
                "DOA": "05/01/2026", "DOD": "08/01/2026", "Month": "January",
                "Dept. Name": "Surgery", "Doctor Name": "Dr B", "Panel Name": "Panel B",
                "Type": "Discharge", "Service Type": "Consultation",
                "Service Name": "Consultation", "Service Final Amount": 2000,
                "Cash Due": 100, "Credit Due": 50, "Bed Days": 2, "UHID": "U3",
            },
            {
                "DOA": "09/01/2026", "DOD": None, "Month": "January",
                "Dept. Name": "Surgery", "Doctor Name": "Dr B", "Panel Name": "Panel B",
                "Type": "OPD", "Service Type": "Lab",
                "Service Name": "Blood Test", "Service Final Amount": 500,
                "Cash Due": 0, "Credit Due": 0, "Bed Days": 0, "UHID": "U4",
            },
        ])
        database.save_hospital_data(self.rows_2025, "Surya Hospital", "surya-2025.xlsx", b"2025")
        database.save_hospital_data(self.rows_2026, "Surya Hospital", "surya-2026.xlsx", b"2026")

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_opd_consultation_filter_normalizes_type_and_service_values(self):
        frame = pd.DataFrame([
            {"Type": " opd ", "Service Type": " Consultation ", "UHID": "U1"},
            {"Type": "OPD", "Service Type": "Laboratory", "UHID": "U2"},
            {"Type": "Discharge", "Service Type": "Consultation", "UHID": "U3"},
            {"Type": None, "Service Type": None, "UHID": "U4"},
        ])

        consultations = backend.opd_consultation_records(frame)

        self.assertEqual(consultations["UHID"].tolist(), ["U1"])

    def test_opd_consultation_filter_falls_back_to_service_name_without_service_type(self):
        frame = pd.DataFrame([
            {"Type": "OPD", "Service Name": "Gastro Consultation", "UHID": "U1"},
            {"Type": "OPD", "Service Name": "CBC", "UHID": "U2"},
            {"Type": "Discharge", "Service Name": "Consultation", "UHID": "U3"},
        ])

        consultations = backend.opd_consultation_records(frame)

        self.assertEqual(consultations["UHID"].tolist(), ["U1"])

    def test_opd_consultation_filter_uses_service_type_when_available(self):
        frame = pd.DataFrame([
            {
                "Type": "OPD", "Service Type": "Laboratory",
                "Service Name": "Consultation", "UHID": "U1",
            },
            {
                "Type": "OPD", "Service Type": "Consultation",
                "Service Name": "CBC", "UHID": "U2",
            },
        ])

        consultations = backend.opd_consultation_records(frame)

        self.assertEqual(consultations["UHID"].tolist(), ["U2"])

    def test_opd_consultation_filter_falls_back_when_service_type_is_empty(self):
        frame = pd.DataFrame([
            {
                "Type": "OPD", "Service Type": "",
                "Service Name": "Gastro Consultation", "UHID": "U1",
            },
            {
                "Type": "OPD", "Service Type": "",
                "Service Name": "CBC", "UHID": "U2",
            },
        ])

        consultations = backend.opd_consultation_records(frame)

        self.assertEqual(consultations["UHID"].tolist(), ["U1"])

    def test_revenue_totals_keep_fractional_amounts_until_display(self):
        frame = pd.DataFrame([
            {"Type": "Discharge", "Service Final Amount": 0.1},
            {"Type": "Discharge", "Service Final Amount": 0.2},
            {"Type": "OPD", "Service Final Amount": 0.05},
        ])

        totals = backend.revenue_totals(frame)

        self.assertEqual(totals["discharge"], 0.3)
        self.assertEqual(totals["opd"], 0.05)
        self.assertEqual(totals["total"], 0.35)

    def test_dashboard_per_day_revenue_uses_inclusive_selected_date_range(self):
        result = dashboard_service.get_dashboard({
            "hospital": "Surya Hospital",
            "startDate": "2025-01-08",
            "endDate": "2025-01-09",
        })

        self.assertEqual(result["kpis"]["revenue"], 1250)
        self.assertEqual(result["kpis"]["per_day_revenue"], 625)

    def test_dashboard_per_day_revenue_uses_full_doa_span_without_selected_range(self):
        frame = pd.DataFrame([
            {"Type": "Admission", "DOA": "01/01/2025"},
            {"Type": "OPD", "DOA": "10/01/2025"},
            {"Type": "Discharge", "DOA": "01/01/2025", "DOD": "01/01/2025"},
        ])

        self.assertEqual(
            dashboard_service._dashboard_selected_days(frame),
            10,
        )

    def test_opd_service_records_use_type_and_service_name_only(self):
        frame = pd.DataFrame([
            {
                "Type": " opd ", "Service Name": " CBC ",
                "Service Type": "Laboratory", "UHID": "OPD-SERVICE",
            },
            {
                "Type": "OPD", "Service Name": " Consultation ",
                "Service Type": "Laboratory", "UHID": "OPD-CONSULTATION",
            },
            {
                "Type": "OPD", "Service Name": "Ultrasound",
                "Service Type": "Consultation", "UHID": "OPD-NONCONSULT-TYPE",
            },
            {
                "Type": "Discharge", "Service Name": "CBC",
                "Service Type": "Laboratory", "UHID": "IPD-SERVICE",
            },
            {
                "Type": "OPD", "Service Name": "",
                "Service Type": "Laboratory", "UHID": "OPD-NO-NAME",
            },
        ])

        services = backend.opd_service_records(frame)

        self.assertEqual(
            services["UHID"].tolist(),
            ["OPD-SERVICE", "OPD-NONCONSULT-TYPE"],
        )

    def test_dashboard_uses_existing_kpi_and_filter_calculations(self):
        datasets = database.list_saved_datasets()
        result = dashboard_service.get_dashboard({
            "datasetId": datasets[-1]["dataset_id"],
            "month": ["January"],
            "period": ["1-10 Days"],
        })
        self.assertTrue(result["hasData"])
        self.assertEqual(result["kpis"]["revenue"], 1250)
        self.assertEqual(result["kpis"]["ipd_revenue"], 1000)
        self.assertEqual(result["kpis"]["opd_revenue"], 250)
        filtered_target = backend.DEFAULT_TARGET * 10 / 31
        self.assertAlmostEqual(result["kpis"]["target"], filtered_target)
        self.assertAlmostEqual(result["kpis"]["achievement"], 1250 / filtered_target * 100)
        self.assertEqual(result["kpis"]["cash_due"], 200)
        self.assertEqual(result["kpis"]["credit_due"], 100)
        self.assertEqual(result["kpis"]["discharges"], 1)
        self.assertEqual(result["kpis"]["bed_days"], 3)
        self.assertEqual(result["kpis"]["arpp"], 1000)
        self.assertAlmostEqual(result["kpis"]["per_day_patient_charge"], 1000 / 3)
        self.assertEqual(result["kpis"]["per_day_revenue"], 625)
        self.assertEqual(result["kpis"]["admissions"], 0)
        self.assertEqual(result["kpis"]["opd_consultation"], 1)
        self.assertEqual(result["recordCount"], 2)
        self.assertAlmostEqual(
            sum(row["target"] for row in result["charts"]["targetProgress"]),
            result["kpis"]["target"],
        )
        self.assertAlmostEqual(
            result["charts"]["totals"]["targetProgress"]["target"],
            result["kpis"]["target"],
        )
        self.assertAlmostEqual(
            sum(row["actual"] for row in result["charts"]["targetProgress"]),
            result["kpis"]["revenue"],
        )
        self.assertEqual(
            [row["actual"] for row in result["charts"]["targetProgress"]],
            [1250, 0, 0],
        )
        self.assertEqual(
            [row["target"] for row in result["charts"]["targetProgress"]][1:],
            [0, 0],
        )
        self.assertIn("revenueTrend", result["charts"])
        self.assertEqual(result["charts"]["totals"]["revenueTrend"]["value"], 1000)
        self.assertEqual(
            result["charts"]["totals"]["patientFlow"],
            {"Admissions": 0, "Discharges": 1},
        )
        self.assertEqual(result["charts"]["totals"]["opdDoctors"]["value"], 1)
        self.assertEqual(result["charts"]["insights"]["department"], "Medicine")
        self.assertEqual(
            [item["label"] for item in result["charts"]["insights"]["items"]],
            [
                "Days daily target was met",
                "Best earning day",
                "Revenue from IPD vs OPD",
                "Top earning department",
                "Top earning doctor",
                "Top paying panel",
                "Payment still pending",
                "OPD gross amount",
                "OPD final amount (received)",
                "OPD discount / concession",
            ],
        )

    def test_full_range_dashboard_trends_are_grouped_by_year(self):
        full_range = dashboard_service.get_dashboard({
            "hospital": "Surya Hospital",
        })
        charts = full_range["charts"]

        self.assertEqual(charts["granularity"], "yearly")
        self.assertEqual(
            [(row["label"], row["value"]) for row in charts["revenueTrend"]],
            [("2025", 1000), ("2026", 2000)],
        )
        self.assertEqual(
            [
                (
                    row["label"],
                    row.get("Admissions", 0),
                    row.get("Discharges", 0),
                )
                for row in charts["patientFlow"]
            ],
            [("2025", 0, 1), ("2026", 0, 1)],
        )
        self.assertEqual(
            [(row["label"], row["value"]) for row in charts["opdDaily"]],
            [("2025", 1)],
        )

        selected_range = dashboard_service.get_dashboard({
            "hospital": "Surya Hospital",
            "startDate": "2025-01-08",
            "endDate": "2026-01-08",
        })
        self.assertEqual(selected_range["charts"]["granularity"], "daily")
        self.assertNotEqual(
            [row["label"] for row in selected_range["charts"]["revenueTrend"]],
            ["2025", "2026"],
        )

    def test_custom_month_range_trends_include_all_filtered_chart_values(self):
        result = dashboard_service.get_dashboard({
            "hospital": "Surya Hospital",
            "startDate": "2025-01-08",
            "endDate": "2025-01-09",
        })
        charts = result["charts"]

        self.assertEqual(charts["granularity"], "daily")
        self.assertEqual(
            sum(row["value"] for row in charts["revenueTrend"]),
            result["kpis"]["ipd_revenue"],
        )
        self.assertEqual(
            sum(row.get("Discharges", 0) for row in charts["patientFlow"]),
            result["kpis"]["discharges"],
        )
        self.assertEqual(
            sum(row.get("Admissions", 0) for row in charts["patientFlow"]),
            result["kpis"]["admissions"],
        )
        self.assertEqual(
            sum(row["value"] for row in charts["opdDaily"]),
            result["kpis"]["opd_consultation"],
        )
        self.assertEqual(
            [row["label"] for row in charts["revenueTrend"]],
            ["08 Jan"],
        )
        self.assertEqual(
            [row["label"] for row in charts["opdDaily"]],
            ["09 Jan"],
        )

    def test_dashboard_selected_days_respect_month_period_and_calendar_boundaries(self):
        frame = pd.DataFrame([
            {
                "Type": "Discharge",
                "DOA": "2024-01-08",
                "DOD": "2024-01-08",
            },
            {"Type": "OPD", "DOA": "2024-02-25"},
            {
                "Type": "Discharge",
                "DOA": "2024-03-05",
                "DOD": "2024-03-05",
            },
        ])

        self.assertEqual(dashboard_service._dashboard_selected_days(frame), 58)
        self.assertEqual(
            dashboard_service._dashboard_selected_days(
                frame, ["January", "March"], ["1-10 Days"]
            ),
            8,
        )

    def test_dashboard_chart_groups_keep_all_categories_and_full_names(self):
        frame = pd.DataFrame([
            {"Department": f"Department {index:02d} with a full name", "Revenue": index}
            for index in range(1, 13)
        ])

        groups = dashboard_service._group_top(frame, "Department", "Revenue")

        self.assertEqual(len(groups), 12)
        self.assertEqual(groups[0]["label"], "Department 12 with a full name")
        self.assertEqual(
            sum(row["value"] for row in groups),
            frame["Revenue"].sum(),
        )

    def test_dashboard_opd_gross_and_discount_kpis_use_filtered_opd_rows(self):
        rows = pd.DataFrame([
            {
                "Type": "Discharge", "DOA": "01/01/2025", "DOD": "02/01/2025",
                "Service Gross Amt": 99999, "Service Final Amount": 90000,
                "Dept. Name": "Medicine", "Doctor Name": "Dr A",
                "Panel Name": "Panel A", "Credit Due": 0,
            },
            {
                "Type": "OPD", "DOA": "03/01/2025",
                "Service Gross Amt": 500, "Service Final Amount": 400,
            },
            {
                "Type": "OPD", "DOA": "04/01/2025",
                "Service Gross Amt": 300, "Service Final Amount": 200,
            },
        ])
        dataset_id = database.save_hospital_data(
            rows, "Surya Hospital", "opd-kpi-test.xlsx", b"opd-kpi-test"
        )["dataset_id"]

        result = dashboard_service.get_dashboard({
            "datasetId": dataset_id,
            "month": ["January"],
            "period": ["1-10 Days"],
        })

        self.assertEqual(result["kpis"]["opd_gross_amount"], 800)
        self.assertEqual(result["kpis"]["opd_discount"], 200)

    def test_overview_target_chart_scales_with_year_month_and_period_filters(self):
        monthly_target = backend.monthly_revenue_target("Surya Hospital")
        annual = dashboard_service._dashboard_target_by_period(
            monthly_target,
            pd.Timestamp("2025-01-01"),
            pd.Timestamp("2025-12-31"),
            [],
            [],
        )
        self.assertAlmostEqual(
            sum(annual.values()), monthly_target * 12, places=5
        )
        partial_year = dashboard_service._dashboard_target_by_period(
            monthly_target,
            pd.Timestamp("2025-01-01"),
            pd.Timestamp("2025-08-31"),
            [],
            [],
        )
        self.assertAlmostEqual(
            sum(partial_year.values()), monthly_target * 12, places=5
        )

        monthly = dashboard_service._dashboard_target_by_period(
            monthly_target,
            pd.Timestamp("2025-01-01"),
            pd.Timestamp("2025-12-31"),
            ["January"],
            [],
        )
        self.assertAlmostEqual(sum(monthly.values()), monthly_target, places=5)

        period = dashboard_service._dashboard_target_by_period(
            monthly_target,
            pd.Timestamp("2025-01-01"),
            pd.Timestamp("2025-12-31"),
            ["January"],
            ["1-10 Days"],
        )
        self.assertAlmostEqual(
            sum(period.values()), monthly_target * 10 / 31, places=5
        )
        self.assertAlmostEqual(
            period["1-10 Days"], monthly_target * 10 / 31, places=5
        )
        self.assertEqual(period["11-20 Days"], 0)
        self.assertEqual(period["21-31 Days"], 0)

    def test_full_range_target_uses_hospital_monthly_target_and_plausible_dates(self):
        old_rows = self.rows_2025.iloc[[0]].copy()
        old_rows["DOA"] = "1900-01-01"
        old_rows["DOD"] = "1900-01-01"
        old_rows["UHID"] = "LEGACY-OUTLIER"

        for hospital, monthly_target in (
            ("Surya Hospital", 50_000_000),
            ("NIMMS Hospital", 25_500_000),
        ):
            if hospital == "NIMMS Hospital":
                database.save_hospital_data(
                    self.rows_2025, hospital, "nimms-2025-target.xlsx",
                    b"nimms-2025-target",
                )
                database.save_hospital_data(
                    self.rows_2026, hospital, "nimms-2026-target.xlsx",
                    b"nimms-2026-target",
                )
            database.save_hospital_data(
                old_rows, hospital, f"{hospital}-legacy-target.xlsx",
                f"{hospital}-legacy-target".encode(),
            )

            with self.subTest(hospital=hospital):
                result = dashboard_service.get_dashboard({"hospital": hospital})
                self.assertAlmostEqual(
                    result["charts"]["totals"]["targetProgress"]["target"],
                    monthly_target * 24,
                    delta=0.01,
                )
                self.assertEqual(
                    result["charts"]["totals"]["targetProgress"]["monthlyTarget"],
                    monthly_target,
                )
                self.assertEqual(
                    result["dateRange"]["start"],
                    "2025-01-08",
                )
                self.assertEqual(
                    result["dateRange"]["end"],
                    "2026-01-09",
                )

        all_hospitals = dashboard_service.get_dashboard({
            "hospital": "All Hospitals",
        })
        self.assertEqual(
            all_hospitals["charts"]["totals"]["targetProgress"]["monthlyTarget"],
            75_500_000,
        )

    def test_monthly_revenue_targets_are_fixed_per_hospital(self):
        self.assertEqual(
            backend.monthly_revenue_target("NIMMS Hospital"), 25_500_000
        )
        self.assertEqual(
            backend.monthly_revenue_target("Surya Hospital"), 50_000_000
        )
        self.assertEqual(
            backend.monthly_revenue_target("All Hospitals"), 75_500_000
        )

    def test_three_year_nimms_full_range_target_uses_three_calendar_years(self):
        for year in (2024, 2025, 2026):
            rows = self.rows_2025.copy()
            rows["DOA"] = rows["DOA"].str.replace("2025", str(year))
            rows["DOD"] = rows["DOD"].fillna("").str.replace("2025", str(year))
            database.save_hospital_data(
                rows,
                "NIMMS Hospital",
                f"nimms-{year}.xlsx",
                f"nimms-{year}".encode(),
            )

        result = dashboard_service.get_dashboard({
            "hospital": "NIMMS Hospital",
        })
        target = result["charts"]["totals"]["targetProgress"]

        self.assertEqual(result["dateRange"]["start"], "2024-01-08")
        self.assertEqual(result["dateRange"]["end"], "2026-01-09")
        self.assertEqual(target["monthlyTarget"], 25_500_000)
        self.assertAlmostEqual(target["target"], 25_500_000 * 36, delta=0.01)

    @patch("comparison.prediction_service.get_prediction", return_value={"hasData": False, "message": "No data"})
    def test_interactive_export_is_self_contained_and_omits_patient_ids(self, get_prediction):
        html = dashboard_service.export_interactive_html()
        text = html.decode("utf-8")
        self.assertIn("Self-contained interactive report", text)
        self.assertIn("function offlineComparison", text)
        self.assertIn("function offlineComparisonCountTrend", text)
        self.assertIn('"Last Months": "IPD revenue trend by month"', text)
        self.assertIn('pathname === "/api/prediction"', text)
        self.assertIn("function loadPrediction()", text)
        self.assertIn(".comparison-prediction-only", text)
        self.assertIn("window.__PREDICTION_REPORT_DATA__ = data", text)
        self.assertIn('url.pathname.replace(/^\\/[A-Za-z]:/, "")', text)
        self.assertIn('id="print-context"', text)
        self.assertIn('src="data:image/png;base64,', text)
        self.assertIn('window.addEventListener("beforeprint", updatePrintContext)', text)
        self.assertIn(".comparison-controls .comparison-filter-grid", text)
        self.assertIn(
            ".comparison-compact-chart .comparison-column-value { display: flex; }",
            text,
        )
        self.assertNotIn("Total revenue: ", text)
        self.assertNotIn('label: "Monthly target"', text)
        self.assertNotIn('label: "Target for this range"', text)
        self.assertIn("<style>", text)
        self.assertNotIn('/assets/dashboard.js', text)
        self.assertNotIn('/assets/dashboard.css', text)
        self.assertNotIn('/assets/prediction.js', text)
        self.assertNotIn('/assets/prediction.css', text)
        match = re.search(r'window\.__OFFLINE_BUNDLE__="([A-Za-z0-9+/=]+)"', text)
        self.assertIsNotNone(match)
        bundle = json.loads(zlib.decompress(base64.b64decode(match.group(1))))
        self.assertEqual(len(bundle["datasets"]), 2)
        self.assertEqual(len(bundle["rows"]), 4)
        self.assertEqual(len(bundle["rows"][0]), 15)
        self.assertNotIn("U1", json.dumps(bundle))
        self.assertEqual(len(bundle["predictions"]), 6)
        self.assertIn('["Surya Hospital","12"]', bundle["predictions"])
        self.assertEqual(get_prediction.call_count, 1)

    def test_offline_prediction_horizon_variants_match_live_predictions(self):
        from comparison import prediction_service

        for mode, month, period, full_horizon, shorter_horizon in (
            ("Year", None, None, 12, 3),
            ("Month", "January", None, 3, 1),
            ("Period", None, "1-10 Days", 12, 6),
        ):
            with self.subTest(mode=mode):
                full_filters = {
                    "predictBy": mode,
                    "horizon": str(full_horizon),
                }
                short_filters = {
                    "predictBy": mode,
                    "horizon": str(shorter_horizon),
                }
                if month:
                    full_filters["month"] = short_filters["month"] = month
                if period:
                    full_filters["period"] = short_filters["period"] = period
                full = prediction_service.get_prediction({
                    "hospital": "Surya Hospital",
                    "filters": full_filters,
                })
                actual_short = prediction_service.get_prediction({
                    "hospital": "Surya Hospital",
                    "filters": short_filters,
                })
                self.assertEqual(
                    dashboard_service._prediction_horizon_variant(full, shorter_horizon),
                    actual_short,
                )

    def test_prediction_uses_selected_hospital_and_supports_one_year_horizon(self):
        from comparison import prediction_service

        other_hospital = pd.DataFrame([{
            "DOA": "05/01/2024", "DOD": "08/01/2024", "Month": "January",
            "Dept. Name": "Other Hospital", "Doctor Name": "Dr Other",
            "Panel Name": "Other Panel", "Type": "Discharge",
            "Service Type": "Consultation", "Service Name": "Consultation",
            "Service Final Amount": 999999, "Cash Due": 0, "Credit Due": 0,
            "Bed Days": 1, "UHID": "OTHER",
        }])
        database.save_hospital_data(
            other_hospital, "NIMMS Hospital", "nimms-2024.xlsx", b"nimms-2024"
        )

        result = prediction_service.get_prediction({
            "hospital": "Surya Hospital",
            "filters": {"horizon": "12"},
        })

        self.assertTrue(result["hasData"])
        self.assertEqual(result["hospital"], "Surya Hospital")
        self.assertEqual(result["horizon"], 12)
        self.assertEqual(result["unit"], "months")
        self.assertEqual(result["dataRange"], ["2025-01-08", "2026-01-09"])
        revenue = next(metric for metric in result["metrics"] if metric["key"] == "revenue")
        self.assertEqual(sum(row["forecast"] is not None for row in revenue["rows"]), 12)
        for dimension, expected_name in (
            ("departments", "Surgery"),
            ("doctors", "Dr B"),
            ("panels", "Panel B"),
        ):
            services = result["breakdown"][dimension]["services"]
            self.assertEqual([row["name"] for row in services], [expected_name])
            self.assertEqual(services[0]["previous"], 1)
            self.assertEqual(
                sum(row["previous"] for row in services),
                len(backend.opd_service_records(self.rows_2026)),
            )
        with self.assertRaisesRegex(ValueError, "between 1 and 12"):
            prediction_service.get_prediction({
                "hospital": "Surya Hospital",
                "filters": {"horizon": "24"},
            })
        self.assertNotIn(999999, [row["actual"] for row in revenue["rows"]])

    def test_revenue_insights_match_dashboard_summary_fields(self):
        rows = pd.DataFrame([
            {
                "Type": "Discharge", "DOA": "01/01/2025", "DOD": "02/01/2025",
                "Dept. Name": "Medicine", "Doctor Name": "Dr A", "Panel Name": "Panel A",
                "Service Amount": 1200, "Service Final Amount": 1000,
                "Credit Due": 200,
            },
            {
                "Type": "OPD", "DOA": "03/01/2025", "DOD": None,
                "Dept. Name": "Medicine", "Doctor Name": "Dr A", "Panel Name": "Panel A",
                "Service Amount": 500, "Service Final Amount": 400,
                "Credit Due": 0,
            },
        ])
        result = backend.insights(rows, monthly_target=3100)
        insights_by_label = {item["label"]: item["value"] for item in result["items"]}

        self.assertEqual(len(result["items"]), 10)
        self.assertEqual(insights_by_label["Days daily target was met"], "2 of 2 days")
        self.assertEqual(insights_by_label["Revenue from IPD vs OPD"], "IPD 71% · OPD 29%")
        self.assertEqual(
            insights_by_label["Top earning department"],
            "Medicine · ₹1,000 (100% of IPD revenue)",
        )
        self.assertEqual(insights_by_label["Payment still pending"], "₹200 (17% of IPD billing)")
        self.assertEqual(insights_by_label["OPD gross amount"], "₹500")
        self.assertEqual(
            insights_by_label["OPD final amount (received)"], "₹400 (80% of gross)"
        )
        self.assertEqual(
            insights_by_label["OPD discount / concession"], "₹100 (20% of gross)"
        )

    def test_revenue_insights_read_gross_amount_aliases(self):
        for gross_column in ("Service Gross Amt", "Service Gross Amount"):
            with self.subTest(gross_column=gross_column):
                rows = pd.DataFrame([
                    {
                        "Type": "Discharge", "DOA": "01/01/2025",
                        "DOD": "02/01/2025", "Doctor Name": "Dr A",
                        "Dept. Name": "Medicine", "Panel Name": "Panel A",
                        gross_column: 1200, "Service Final Amount": 1000,
                        "Credit Due": 200,
                    },
                    {
                        "Type": "OPD", "DOA": "03/01/2025",
                        gross_column: 500, "Service Final Amount": 400,
                    },
                    {
                        "Type": "OPD", "DOA": "04/01/2025",
                        gross_column: 300, "Service Final Amount": 200,
                    },
                ])
                insights_by_label = {
                    item["label"]: item["value"]
                    for item in backend.insights(rows)["items"]
                }

                self.assertEqual(
                    insights_by_label["Payment still pending"],
                    "₹200 (17% of IPD billing)",
                )
                self.assertEqual(insights_by_label["OPD gross amount"], "₹800")
                self.assertEqual(
                    insights_by_label["OPD final amount (received)"],
                    "₹600 (75% of gross)",
                )
                self.assertEqual(
                    insights_by_label["OPD discount / concession"],
                    "₹200 (25% of gross)",
                )

    def test_comparison_returns_period_kpis_and_department_rows(self):
        result = dashboard_service.get_comparison({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Year",
                "year1": "2025",
                "year2": "2026",
                "month": "All Months",
            },
        })
        self.assertEqual(result["recordCounts"], [2, 2])
        revenue = next(metric for metric in result["metrics"] if metric["label"] == "Revenue")
        self.assertEqual((revenue["first"], revenue["second"]), (1250, 2500))
        self.assertEqual(len(result["metrics"]), 13)
        ipd_revenue = next(metric for metric in result["metrics"] if metric["label"] == "IPD revenue")
        opd_revenue = next(metric for metric in result["metrics"] if metric["label"] == "OPD revenue")
        target = next(metric for metric in result["metrics"] if metric["label"] == "Revenue target")
        achievement = next(metric for metric in result["metrics"] if metric["label"] == "Target achievement")
        self.assertEqual((ipd_revenue["first"], ipd_revenue["second"]), (1000, 2000))
        self.assertEqual((opd_revenue["first"], opd_revenue["second"]), (250, 500))
        self.assertEqual((revenue["first"], revenue["second"]), (
            ipd_revenue["first"] + opd_revenue["first"],
            ipd_revenue["second"] + opd_revenue["second"],
        ))
        annual_target = backend.DEFAULT_TARGET * 12
        self.assertEqual(target["first"], annual_target)
        self.assertEqual(target["second"], annual_target)
        self.assertAlmostEqual(achievement["first"], 1250 / annual_target * 100)
        self.assertAlmostEqual(achievement["second"], 2500 / annual_target * 100)
        self.assertEqual(result["summary"]["periodDays"], [365, 365])
        self.assertEqual(result["summary"]["totalRevenue"], [1250, 2500])
        self.assertEqual(result["summary"]["ipdRevenue"], [1000, 2000])

    def test_comparison_summary_uses_selected_date_ranges(self):
        result = dashboard_service.get_comparison({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "From-To",
                "start1": "2025-01-05",
                "end1": "2025-01-08",
                "start2": "2026-01-05",
                "end2": "2026-01-08",
            },
        })

        revenue = next(
            metric for metric in result["metrics"]
            if metric["label"] == "Revenue"
        )
        self.assertEqual([revenue["first"], revenue["second"]], [1000, 2000])
        self.assertEqual(result["summary"]["totalRevenue"], [1000, 2000])
        self.assertEqual(result["summary"]["periodDays"], [4, 4])
        self.assertEqual(
            [value / days for value, days in zip(
                result["summary"]["totalRevenue"],
                result["summary"]["periodDays"],
            )],
            [250, 500],
        )

    def test_comparison_includes_populated_trend_series(self):
        result = dashboard_service.get_comparison({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Year",
                "year1": "2025",
                "year2": "2026",
                "month": "All Months",
            },
        })
        self.assertTrue(result["hasData"])
        self.assertEqual(
            set(result["trends"]),
            {"admissions", "discharges", "opdConsultations", "revenue"},
        )
        for trend in result["trends"].values():
            self.assertTrue(trend["rows"])
            self.assertEqual(len(trend["rows"]), 12)
        january_revenue = next(
            row for row in result["trends"]["revenue"]["rows"]
            if row["label"] == "Jan"
        )
        self.assertEqual((january_revenue["first"], january_revenue["second"]), (1000, 2000))
        self.assertEqual(result["summary"]["opdRevenue"], [250, 500])
        self.assertEqual(result["summary"]["ipdDischargeEffect"], 0)
        self.assertEqual(result["summary"]["ipdBillPerDischargeEffect"], 1000)
        self.assertEqual(result["summary"]["creditDue"], [100, 50])
        self.assertEqual(result["summary"]["collectionRate"], [90, 97.5])
        self.assertEqual(result["departments"][0]["Department"], "Medicine")
        self.assertEqual(result["chartTotals"]["ipdRevenueDepartments"], [1000, 2000])
        self.assertEqual(result["chartTotals"]["dischargeDepartments"], [1, 1])
        self.assertEqual(result["chartTotals"]["opdDoctors"], [1, 0])
        medicine_mover = next(
            row for row in result["moverData"]["ipdRevenue"]["departments"]
            if row["name"] == "Medicine"
        )
        self.assertEqual(medicine_mover["values"], [1000, 0])
        medicine_discharge_mover = next(
            row for row in result["moverData"]["ipdDischarges"]["departments"]
            if row["name"] == "Medicine"
        )
        self.assertEqual(medicine_discharge_mover["values"], [1, 0])
        medicine_consultation_mover = next(
            row for row in result["moverData"]["opdConsultations"]["departments"]
            if row["name"] == "Medicine"
        )
        self.assertEqual(medicine_consultation_mover["values"], [1, 0])
        medicine_consultation_doctor = next(
            row for row in result["moverData"]["opdConsultations"]["doctors"]
            if row["name"] == "Dr A"
        )
        self.assertEqual(medicine_consultation_doctor["values"], [1, 0])
        self.assertEqual(
            set(result["chartTotals"]),
            {
                "dischargeDepartments", "dischargeDoctors",
                "admissionDepartments", "admissionDoctors",
                "ipdRevenueDepartments", "ipdRevenueDoctors",
                "opdDoctors", "opdServices", "panels",
            },
        )
        self.assertEqual(result["revenueTrend"]["totals"], [1000, 2000])

    def test_comparison_restores_revenue_and_operational_breakdowns(self):
        for year, department, doctor, panel in [
            (2025, "Medicine", "Dr A", "Panel A"),
            (2026, "Surgery", "Dr B", "Panel B"),
        ]:
            additions = pd.DataFrame([
                {
                    "DOA": f"10/01/{year}", "DOD": None, "Month": "January",
                    "Dept. Name": department, "Doctor Name": doctor,
                    "Panel Name": panel, "Type": "Admission",
                    "Service Type": "", "Service Name": "",
                    "Service Final Amount": 0, "Cash Due": 0, "Credit Due": 0,
                    "Bed Days": 0, "UHID": f"Admission-{year}",
                },
                {
                    "DOA": f"11/01/{year}", "DOD": None, "Month": "January",
                    "Dept. Name": department, "Doctor Name": doctor,
                    "Panel Name": panel, "Type": "OPD",
                    "Service Type": "Lab", "Service Name": "Blood Test",
                    "Service Final Amount": 300, "Cash Due": 0, "Credit Due": 0,
                    "Bed Days": 0, "UHID": f"Service-{year}",
                },
            ])
            database.save_hospital_data(
                additions, "Surya Hospital", f"additional-{year}.xlsx",
                f"additional-{year}".encode(),
            )

        result = dashboard_service.get_comparison({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Year", "year1": "2025", "year2": "2026",
                "month": "All Months",
            },
        })

        for metric in ("ipdDischarges", "ipdAdmissions", "opdConsultations"):
            with self.subTest(metric=metric):
                panel_row = next(
                    row for row in result["moverData"][metric]["panels"]
                    if row["name"] == "Panel A"
                )
                self.assertEqual(panel_row["values"], [1, 0])

        self.assertEqual(result["recordCounts"], [4, 4])
        self.assertEqual(
            set(result["charts"]),
            {
                "dischargeDepartments", "dischargeDoctors", "admissionDepartments",
                "admissionDoctors", "ipdRevenueDepartments", "ipdRevenueDoctors",
                "opdDoctors", "panels", "services",
            },
        )
        self.assertEqual(result["revenueTrend"]["totals"], [1000, 2000])
        self.assertEqual(result["revenueTrend"]["rows"], [
            {"label": "Jan", "first": 1000.0, "second": 2000.0},
            {"label": "Feb", "first": 0, "second": 0},
            {"label": "Mar", "first": 0, "second": 0},
            {"label": "Apr", "first": 0, "second": 0},
            {"label": "May", "first": 0, "second": 0},
            {"label": "Jun", "first": 0, "second": 0},
            {"label": "Jul", "first": 0, "second": 0},
            {"label": "Aug", "first": 0, "second": 0},
            {"label": "Sep", "first": 0, "second": 0},
            {"label": "Oct", "first": 0, "second": 0},
            {"label": "Nov", "first": 0, "second": 0},
            {"label": "Dec", "first": 0, "second": 0},
        ])
        medicine_admissions = next(
            row for row in result["admissionDepartments"]
            if row["Dept. Name"] == "Medicine"
        )
        self.assertEqual(medicine_admissions[result["labels"][0]], 1)
        medicine_discharges = next(
            row for row in result["dischargeDepartments"]
            if row["Dept. Name"] == "Medicine"
        )
        self.assertEqual(medicine_discharges[result["labels"][0]], 1)
        chart_medicine_discharges = next(
            row for row in result["charts"]["dischargeDepartments"]
            if row["label"] == "Medicine"
        )
        self.assertEqual(
            (chart_medicine_discharges["first"], chart_medicine_discharges["second"]),
            (1, 0),
        )
        chart_dr_a_discharges = next(
            row for row in result["charts"]["dischargeDoctors"]
            if row["label"] == "Dr A"
        )
        self.assertEqual(
            (chart_dr_a_discharges["first"], chart_dr_a_discharges["second"]),
            (1, 0),
        )
        chart_medicine_admissions = next(
            row for row in result["charts"]["admissionDepartments"]
            if row["label"] == "Medicine"
        )
        self.assertEqual(
            (chart_medicine_admissions["first"], chart_medicine_admissions["second"]),
            (1, 0),
        )
        chart_dr_a_consultations = next(
            row for row in result["charts"]["opdDoctors"]
            if row["label"] == "Dr A"
        )
        self.assertEqual(
            (chart_dr_a_consultations["first"], chart_dr_a_consultations["second"]),
            (1, 0),
        )
        chart_surgery_revenue = next(
            row for row in result["charts"]["ipdRevenueDepartments"]
            if row["label"] == "Surgery"
        )
        self.assertEqual(
            (chart_surgery_revenue["first"], chart_surgery_revenue["second"]),
            (0, 2000),
        )
        chart_surgery_services = next(
            row for row in result["charts"]["services"]
            if row["label"] == "Blood Test"
        )
        self.assertEqual(
            (chart_surgery_services["first"], chart_surgery_services["second"]),
            (0, 2),
        )
        chart_panel_a = next(
            row for row in result["charts"]["panels"]
            if row["label"] == "Panel A"
        )
        self.assertEqual((chart_panel_a["first"], chart_panel_a["second"]), (1000, 0))
        panel_a_mover = next(
            row for row in result["moverData"]["ipdRevenue"]["panels"]
            if row["name"] == "Panel A"
        )
        self.assertEqual(panel_a_mover["values"], [1000, 0])
        self.assertEqual(
            result["summary"]["biggestIpdRevenueMovers"]["panels"]["name"],
            "Panel B",
        )
        self.assertEqual(
            result["summary"]["biggestIpdRevenueMovers"]["panels"]["change"],
            2000,
        )
        self.assertEqual(result["opdDoctors"][0]["Doctor Name"], "Dr A")
        self.assertIn("Panel A", [row["Panel"] for row in result["panels"]])
        self.assertTrue(result["services"])
        self.assertIn("Blood Test", [row["Service"] for row in result["services"]])
        first_period_insights = result["managementInsights"][result["labels"][0]]
        self.assertEqual(first_period_insights["department"], "Medicine")
        self.assertEqual(first_period_insights["doctor"], "Dr A")
        self.assertEqual(first_period_insights["panel"], "Panel A")
        self.assertEqual(first_period_insights["day"], "08-Jan-2025")
        exported = dashboard_service.export_comparison_csv({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Year", "year1": "2025", "year2": "2026",
                "month": "All Months",
            },
        }).decode("utf-8-sig")
        self.assertIn("IPD revenue trend", exported)
        self.assertIn("Panel IPD revenue", exported)
        self.assertIn("Admissions by department", exported)
        self.assertIn("OPD services", exported)
        self.assertIn("Management insight", exported)

    def test_comparison_supports_every_period_selection_mode(self):
        selections = [
            {
                "compareBy": "Year", "year1": "2025", "year2": "2026",
                "month": "All Months",
            },
            {
                "compareBy": "Month", "year": "2026",
                "month1": "January", "month2": "January",
            },
            {
                "compareBy": "Week", "year": "2026", "month": "January",
                "week1": "2", "week2": "2",
            },
            {
                "compareBy": "Period", "year": "2026", "month": "January",
                "period1": "1-10 Days", "period2": "1-10 Days",
            },
            {
                "compareBy": "Date", "date1": "2026-01-08", "date2": "2026-01-08",
            },
            {
                "compareBy": "From-To",
                "start1": "2026-01-01", "end1": "2026-01-31",
                "start2": "2026-01-01", "end2": "2026-01-31",
            },
        ]
        expected_targets = [
            backend.DEFAULT_TARGET * 12,
            backend.DEFAULT_TARGET,
            backend.DEFAULT_TARGET * 7 / 31,
            backend.DEFAULT_TARGET * 10 / 31,
            backend.DEFAULT_TARGET / 31,
            backend.DEFAULT_TARGET,
        ]
        for filters, expected_target in zip(selections, expected_targets):
            with self.subTest(mode=filters["compareBy"]):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    result = dashboard_service.get_comparison({
                        "hospital": "Surya Hospital",
                        "filters": filters,
                    })
                self.assertTrue(result["hasData"])
                self.assertEqual(len(result["metrics"]), 13)
                target = next(
                    metric for metric in result["metrics"]
                    if metric["label"] == "Revenue target"
                )
                self.assertAlmostEqual(target["first"], expected_target)
                self.assertAlmostEqual(target["second"], expected_target)
                self.assertNotEqual(result["labels"][0], result["labels"][1])
                if filters["compareBy"] == "From-To":
                    self.assertEqual(
                        result["labels"], ["First period", "Second period"]
                    )
                elif filters["compareBy"] != "Year":
                    self.assertIn("Period 1", result["labels"][0])
                    self.assertIn("Period 2", result["labels"][1])
                self.assertFalse(any(
                    "DataFrame columns are not unique" in str(item.message)
                    for item in caught
                ))

    def test_last_months_comparison_compares_consecutive_month_windows(self):
        additions = []
        for year, months, amount in (
            (2025, (10, 11, 12), 1000),
            (2026, (1, 2, 3), 2000),
        ):
            for month in months:
                date_value = f"15/{month:02d}/{year}"
                additions.append({
                    "DOA": date_value, "DOD": date_value,
                    "Month": pd.Timestamp(year, month, 1).strftime("%B"),
                    "Dept. Name": "Medicine", "Doctor Name": "Dr A",
                    "Panel Name": "Panel A", "Type": "Discharge",
                    "Service Type": "Consultation", "Service Name": "Consultation",
                    "Service Final Amount": amount, "Cash Due": 0, "Credit Due": 0,
                    "Bed Days": 1, "UHID": f"U-{year}-{month}",
                })
        database.save_hospital_data(
            pd.DataFrame(additions), "Surya Hospital", "monthly-comparison.xlsx",
            b"monthly-comparison",
        )

        result = dashboard_service.get_comparison({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Last Months", "endYear": "2026",
                "endMonth": "March", "monthsCount": "3",
            },
        })

        self.assertTrue(result["hasData"])
        self.assertEqual(result["labels"], ["Oct 2025 – Dec 2025", "Jan 2026 – Mar 2026"])
        self.assertEqual(result["periodRanges"], [
            ["2025-10-01", "2025-12-31"], ["2026-01-01", "2026-03-31"],
        ])
        ipd_revenue = next(
            metric for metric in result["metrics"] if metric["label"] == "IPD revenue"
        )
        self.assertEqual([ipd_revenue["first"], ipd_revenue["second"]], [3000, 8000])
        self.assertEqual(
            [row["label"] for row in result["revenueTrend"]["rows"]],
            ["Jan 2026", "Feb 2026", "Mar 2026"],
        )
        self.assertEqual(
            [row["first"] for row in result["revenueTrend"]["rows"]],
            [1000, 1000, 1000],
        )
        self.assertEqual(
            [row["second"] for row in result["revenueTrend"]["rows"]],
            [4000, 2000, 2000],
        )
        self.assertEqual(dashboard_service.comparison_options()["latestDate"], "2026-03-15")
        expected_ranges = {
            3: [["2025-10-01", "2025-12-31"], ["2026-01-01", "2026-03-31"]],
            4: [["2025-09-01", "2025-12-31"], ["2026-01-01", "2026-04-30"]],
            6: [["2025-07-01", "2025-12-31"], ["2026-01-01", "2026-06-30"]],
        }
        for count, expected in expected_ranges.items():
            with self.subTest(months=count):
                filters = {
                    "compareBy": "Last Months", "endYear": "2026",
                    "endMonth": {3: "March", 4: "April", 6: "June"}[count],
                    "monthsCount": str(count),
                }
                self.assertEqual(
                    [
                        [
                            start.strftime("%Y-%m-%d"),
                            end.strftime("%Y-%m-%d"),
                        ]
                        for start, end in (
                            dashboard_service._comparison_target_range(filters, "1"),
                            dashboard_service._comparison_target_range(filters, "2"),
                        )
                    ],
                    expected,
                )
                comparison = dashboard_service.get_comparison({
                    "hospital": "Surya Hospital",
                    "filters": filters,
                })
                self.assertTrue(comparison["hasData"])
                self.assertEqual(len(comparison["revenueTrend"]["rows"]), count)
        with self.assertRaisesRegex(ValueError, "3-, 4-, or 6-month"):
            dashboard_service.get_comparison({
                "hospital": "Surya Hospital",
                "filters": {
                    "compareBy": "Last Months", "endYear": "2026",
                    "endMonth": "March", "monthsCount": "2",
                },
            })

    def test_period_comparison_shows_selected_billing_ranges_together(self):
        existing_periods = dashboard_service.get_comparison({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Period",
                "year": "2026",
                "month": "January",
                "period": "All Periods",
            },
        })
        self.assertEqual(existing_periods["labels"], backend.PERIOD_OPTIONS)
        self.assertEqual(existing_periods["summary"]["periodDays"], [10, 11])
        self.assertEqual(
            existing_periods["summary"]["ipdRevenue"], [2000, 0]
        )
        self.assertEqual(
            next(
                metric for metric in existing_periods["metrics"]
                if metric["label"] == "IPD revenue"
            )["values"],
            [2000, 0, 0],
        )
        self.assertEqual(existing_periods["recordCounts"], [2, 0, 0])
        existing_revenue_trend = existing_periods["trends"]["revenue"]
        self.assertEqual(
            [row["label"] for row in existing_revenue_trend["rows"]],
            [str(day) for day in range(1, 32)],
        )
        self.assertEqual(
            next(
                row for row in existing_revenue_trend["rows"]
                if row["label"] == "8"
            )["first"],
            2000,
        )
        self.assertEqual(
            existing_periods["revenueTrend"]["rows"][0]["first"], 2000
        )
        self.assertEqual(
            existing_periods["revenueTrend"]["rows"][0]["second"], 0
        )
        first_period_mover = next(
            row for row in existing_periods["moverData"]["ipdRevenue"]["departments"]
            if row["name"] == "Surgery"
        )
        self.assertEqual(first_period_mover["values"], [2000, 0, 0])

        extra_rows = pd.DataFrame([
            {
                "DOA": "14/01/2026", "DOD": "15/01/2026", "Month": "January",
                "Dept. Name": "Medicine", "Doctor Name": "Dr A", "Panel Name": "Panel A",
                "Type": "Discharge", "Service Type": "Consultation",
                "Service Name": "Consultation", "Service Final Amount": 1500,
                "Cash Due": 100, "Credit Due": 0, "Bed Days": 1, "UHID": "U5",
            },
            {
                "DOA": "24/01/2026", "DOD": "25/01/2026", "Month": "January",
                "Dept. Name": "Surgery", "Doctor Name": "Dr B", "Panel Name": "Panel B",
                "Type": "Discharge", "Service Type": "Consultation",
                "Service Name": "Consultation", "Service Final Amount": 2500,
                "Cash Due": 200, "Credit Due": 0, "Bed Days": 1, "UHID": "U6",
            },
            {
                "DOA": "05/01/2026", "DOD": None, "Month": "January",
                "Dept. Name": "Lab Department", "Doctor Name": "Dr Lab",
                "Panel Name": "Panel Lab", "Type": "OPD",
                "Service Type": "Laboratory", "Service Name": "CBC",
                "Service Final Amount": 100, "Cash Due": 0, "Credit Due": 0,
                "Bed Days": 0, "UHID": "U7",
            },
            {
                "DOA": "15/01/2026", "DOD": None, "Month": "January",
                "Dept. Name": "Lab Department", "Doctor Name": "Dr Lab",
                "Panel Name": "Panel Lab", "Type": "OPD",
                "Service Type": "Laboratory", "Service Name": "CBC",
                "Service Final Amount": 100, "Cash Due": 0, "Credit Due": 0,
                "Bed Days": 0, "UHID": "U8",
            },
            {
                "DOA": "25/01/2026", "DOD": None, "Month": "January",
                "Dept. Name": "Lab Department", "Doctor Name": "Dr Lab",
                "Panel Name": "Panel Lab", "Type": "OPD",
                "Service Type": "Laboratory", "Service Name": "CBC",
                "Service Final Amount": 100, "Cash Due": 0, "Credit Due": 0,
                "Bed Days": 0, "UHID": "U9",
            },
        ])
        database.save_hospital_data(
            extra_rows, "Surya Hospital", "surya-january-periods.xlsx", b"periods"
        )

        result = dashboard_service.get_comparison({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Period",
                "year": "2026",
                "month": "January",
                "period": "All Periods",
            },
        })

        self.assertTrue(result["hasData"])
        self.assertEqual(result["labels"], backend.PERIOD_OPTIONS)
        revenue = next(metric for metric in result["metrics"] if metric["label"] == "IPD revenue")
        self.assertEqual(revenue["values"], [2000, 1500, 2500])
        medicine_mover = next(
            row for row in result["moverData"]["ipdRevenue"]["departments"]
            if row["name"] == "Medicine"
        )
        self.assertEqual(medicine_mover["values"], [0, 1500, 0])
        lab_service_mover = next(
            row for row in result["moverData"]["opdServices"]["departments"]
            if row["name"] == "Lab Department"
        )
        self.assertEqual(lab_service_mover["values"], [1, 1, 1])
        self.assertEqual(len(result["chartTotals"]["dischargeDepartments"]), 3)
        self.assertEqual(len(result["revenueTrend"]["totals"]), 3)
        revenue_trend = result["trends"]["revenue"]["rows"]
        self.assertEqual(
            [row["label"] for row in revenue_trend],
            [str(day) for day in range(1, 32)],
        )
        self.assertEqual(
            {
                row["label"]: (
                    row.get("first", 0),
                    row.get("second", 0),
                    row.get("third", 0),
                )
                for row in revenue_trend
                if any(row.get(key, 0) for key in ("first", "second", "third"))
            },
            {"8": (2000, 0, 0), "15": (0, 1500, 0), "25": (0, 0, 2500)},
        )
        discharge_rows = result["charts"]["dischargeDepartments"]
        self.assertTrue(all(
            any(key in row for row in discharge_rows)
            for key in ("first", "second", "third")
        ))
        exported = pd.read_csv(BytesIO(dashboard_service.export_comparison_csv({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Period",
                "year": "2026",
                "month": "January",
                "period": "All Periods",
            },
        })))
        revenue_export = exported.loc[
            exported["Label"] == "IPD revenue"
        ].iloc[0]
        self.assertEqual(
            [float(revenue_export[period]) for period in backend.PERIOD_OPTIONS],
            [2000, 1500, 2500],
        )

        one_period = dashboard_service.get_comparison({
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Period",
                "year": "2026",
                "month": "January",
                "period": "11-20 Days",
            },
        })
        self.assertTrue(one_period["hasData"])
        self.assertEqual(one_period["labels"], ["11-20 Days"])
        self.assertEqual(
            next(metric for metric in one_period["metrics"] if metric["label"] == "IPD revenue")["values"],
            [1500],
        )
        one_period_revenue_trend = one_period["trends"]["revenue"]["rows"]
        self.assertEqual(
            next(
                row for row in one_period_revenue_trend
                if row["label"] == "15"
            )["first"],
            1500,
        )

    def test_comparison_uses_hospital_specific_monthly_revenue_targets(self):
        database.save_hospital_data(
            self.rows_2025, "NIMMS Hospital", "nimms-2025.xlsx", b"nimms-2025"
        )
        database.save_hospital_data(
            self.rows_2026, "NIMMS Hospital", "nimms-2026.xlsx", b"nimms-2026"
        )
        result = dashboard_service.get_comparison({
            "hospital": "NIMMS Hospital",
            "filters": {
                "compareBy": "Year", "year1": "2025", "year2": "2026",
                "month": "All Months",
            },
        })
        target = next(
            metric for metric in result["metrics"]
            if metric["label"] == "Revenue target"
        )
        self.assertEqual(target["first"], 25_500_000 * 12)
        self.assertEqual(target["second"], 25_500_000 * 12)
        self.assertEqual(backend.monthly_revenue_target("Surya Hospital"), 50_000_000)
        self.assertEqual(backend.monthly_revenue_target("All Hospitals"), 75_500_000)

    def test_upload_excel_validates_and_saves_workbook(self):
        workbook = BytesIO()
        self.rows_2025.to_excel(workbook, index=False)
        result = dashboard_service.upload_excel("NIMMS-upload.xlsx", workbook.getvalue())
        self.assertEqual(result["hospital_name"], "NIMMS Hospital")
        self.assertEqual(result["inserted"], 2)
        self.assertTrue(any(item["dataset_id"] == result["dataset_id"] for item in database.list_saved_datasets()))

    def test_upload_rejects_non_excel_files(self):
        with self.assertRaisesRegex(ValueError, "Excel workbook"):
            dashboard_service.upload_excel("records.csv", b"record")

    def test_comparison_frame_is_reused_between_dashboard_requests(self):
        dashboard_service._cached_master_frame.cache_clear()
        dashboard_service._cached_comparison_options.cache_clear()
        years = [2025, 2026]
        comparison_query = {
            "hospital": "Surya Hospital",
            "filters": {
                "compareBy": "Year",
                "year1": str(years[0]),
                "year2": str(years[1]),
                "month": "All Months",
            },
        }
        with patch.object(
            database, "load_all_hospital_data", wraps=database.load_all_hospital_data
        ) as load_master:
            options = dashboard_service.dashboard_options()
            self.assertEqual(options["comparison"]["years"], years)
            dashboard_service.comparison_options()
            dashboard_service.get_comparison(comparison_query)
            self.assertEqual(load_master.call_count, 1)

    def test_initial_dashboard_options_include_comparison_years(self):
        dashboard_service._cached_comparison_options.cache_clear()
        options = dashboard_service.dashboard_options()
        self.assertEqual(options["comparison"]["years"], [2025, 2026])
        self.assertIn("January", options["comparison"]["months"])


if __name__ == "__main__":
    unittest.main()
