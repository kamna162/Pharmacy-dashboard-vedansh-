from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from comparison import backend, database


class DatabaseComparisonPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp_dir.name) / "hospital_data.db"
        database.init_database()

        self.records = pd.DataFrame([
            {"Type": "Admission", "DOA": "15/12/2024", "DOD": "01/01/2025", "Month": "January", "Service Final Amount": 100},
            {"Type": "Discharge", "DOA": "05/01/2025", "DOD": "20/12/2024", "Month": "January", "Service Final Amount": 200},
            {"Type": "Admission", "DOA": "03/01/2025", "DOD": None, "Month": "December", "Service Final Amount": 10},
            {"Type": "Admission", "DOA": "10/01/2025", "DOD": None, "Month": "December", "Service Final Amount": 20},
            {"Type": "Admission", "DOA": "15/01/2025", "DOD": None, "Month": "December", "Service Final Amount": 30},
            {"Type": "Admission", "DOA": "24/01/2025", "DOD": None, "Month": "December", "Service Final Amount": 40},
            {"Type": "Admission", "DOA": "31/01/2025", "DOD": None, "Month": "December", "Service Final Amount": 50},
            {"Type": "Admission", "DOA": "03/02/2025", "DOD": None, "Month": "January", "Service Final Amount": 60},
            {"Type": "Discharge", "DOA": None, "DOD": "03/12/2025", "Month": "January", "Service Final Amount": 300},
        ])
        database.save_hospital_data(
            self.records, "Surya Hospital", "initial.xlsx", b"initial-upload"
        )
        other_hospital = pd.DataFrame([
            {"Type": "Admission", "DOA": "06/01/2025", "DOD": None, "Month": "January", "Service Final Amount": 500},
        ])
        database.save_hospital_data(
            other_hospital, "NIMMS Hospital", "nimms.xlsx", b"nimms-upload"
        )

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def load_master(self):
        return backend.clean_data(database.load_all_hospital_data())

    def test_database_initialization_is_safe_for_concurrent_requests(self):
        original_database_path = database.DATABASE_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = Path(temp_dir) / "concurrent.db"
            try:
                with ThreadPoolExecutor(max_workers=8) as executor:
                    list(executor.map(lambda _: database.init_database(), range(16)))

                with database._connect() as connection:
                    tables = {
                        row["name"]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                    journal_mode = connection.execute(
                        "PRAGMA journal_mode"
                    ).fetchone()[0]
            finally:
                database.DATABASE_PATH = original_database_path

        self.assertIn("hospital_data", tables)
        self.assertIn("raw_report_uploads", tables)
        self.assertEqual(journal_mode.casefold(), "wal")

    def test_dataset_fetch_uses_dataset_and_row_order_index(self):
        dataset_id = database.list_saved_datasets()[0]["dataset_id"]
        with database._connect() as connection:
            indexes = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA index_list(hospital_data)"
                ).fetchall()
            }
            plan = connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT "Type", "DOA"
                FROM hospital_data
                WHERE dataset_id = ?
                ORDER BY id
                """,
                (dataset_id,),
            ).fetchall()

        self.assertIn("idx_hospital_data_dataset_id", indexes)
        self.assertNotIn("idx_hospital_data_dataset", indexes)
        self.assertTrue(any(
            "idx_hospital_data_dataset_id" in row["detail"] for row in plan
        ))

    def test_loader_combines_datasets_and_refreshes_after_upload(self):
        master = self.load_master()
        self.assertEqual(len(master), len(self.records) + 1)
        self.assertEqual(set(master["hospital_name"]), {"Surya Hospital", "NIMMS Hospital"})

        added = pd.DataFrame([
            {"Type": "Admission", "DOA": "05/01/2026", "DOD": None, "Month": "January", "Service Final Amount": 700},
        ])
        database.save_hospital_data(
            added, "Surya Hospital", "2026.xlsx", b"2026-upload"
        )
        refreshed = self.load_master()
        self.assertEqual(len(refreshed), len(master) + 1)
        self.assertEqual(backend.infer_years(refreshed), [2024, 2025, 2026])

    def test_year_and_month_comparisons_are_independent_and_use_dates(self):
        master = self.load_master()
        december_2024 = backend.filter_dashboard_comparison(
            master, 2024, month="December"
        )
        december_2025 = backend.filter_dashboard_comparison(
            master, 2025, month="December"
        )
        self.assertEqual(set(backend.comparison_event_dates(december_2024).dt.year), {2024})
        self.assertEqual(set(backend.comparison_event_dates(december_2025).dt.year), {2025})
        self.assertEqual(december_2024["Service Final Amount"].sum(), 300)
        self.assertEqual(december_2025["Service Final Amount"].sum(), 300)

        january_2025 = backend.filter_dashboard_comparison(
            master, 2025, month="January"
        )
        february_2025 = backend.filter_dashboard_comparison(
            master, 2025, month="February"
        )
        self.assertEqual(len(january_2025), 6)
        self.assertEqual(len(february_2025), 1)

    def test_precomputed_event_dates_preserve_dashboard_filters(self):
        master = self.load_master()
        event_dates = backend.comparison_event_dates(master)
        cached = master.copy()
        cached["__comparison_event_date"] = event_dates

        pd.testing.assert_series_equal(
            backend.comparison_event_dates(cached),
            event_dates,
            check_names=False,
        )
        for apply_filter in (
            lambda frame: backend.apply_filters(
                frame, month=["January"], period=[backend.PERIOD_OPTIONS[0]]
            ),
            lambda frame: backend.filter_dashboard_comparison(
                frame, 2025, month="January", period=backend.PERIOD_OPTIONS[0]
            ),
        ):
            expected = apply_filter(master)
            actual = apply_filter(cached).drop(
                columns=["__comparison_event_date"], errors="ignore"
            )
            pd.testing.assert_frame_equal(
                actual.reset_index(drop=True),
                expected.reset_index(drop=True),
            )

    def test_period_week_date_range_and_hospital_filters(self):
        master = self.load_master()
        january_periods = {
            period: backend.filter_dashboard_comparison(
                master, 2025, month="January", period=period
            )
            for period in backend.PERIOD_OPTIONS
        }
        self.assertEqual(
            [len(january_periods[period]) for period in backend.PERIOD_OPTIONS],
            [3, 1, 2],
        )

        january_weeks = {
            week: backend.filter_dashboard_comparison(
                master, 2025, month="January", week=week
            )
            for week in range(1, 6)
        }
        self.assertEqual(
            [len(january_weeks[week]) for week in range(1, 6)],
            [2, 1, 1, 1, 1],
        )

        exact_date = backend.filter_dashboard_comparison(
            master, 2025, month="January", day=10
        )
        self.assertEqual(len(exact_date), 1)
        date_range = backend.filter_comparison_range(
            master, "2024-12-19", "2025-01-10"
        )
        self.assertEqual(len(date_range), 4)

        surya = master.loc[master["hospital_name"] == "Surya Hospital"]
        nimms = master.loc[master["hospital_name"] == "NIMMS Hospital"]
        self.assertEqual(len(surya), len(self.records))
        self.assertEqual(len(nimms), 1)
        self.assertEqual(
            backend.filter_dashboard_comparison(nimms, 2025, month="January")[
                "Service Final Amount"
            ].sum(),
            500,
        )

    def test_comparison_revenue_uses_filtered_service_final_amounts(self):
        rows = pd.DataFrame([
            {"Type": "Discharge", "DOA": "01/01/2024", "DOD": "05/01/2025", "Service Final Amount": "100", "Panel Name": "Panel A", "UHID": "D25", "Cash Due": 0, "Credit Due": 10, "Bed Days": 1},
            {"Type": "OPD", "DOA": "06/01/2025", "DOD": None, "Service Final Amount": "40", "Panel Name": "Panel A", "UHID": "O25", "Cash Due": 0, "Credit Due": 0, "Bed Days": 0},
            {"Type": "Admission", "DOA": "07/01/2025", "DOD": None, "Service Final Amount": "1000", "Panel Name": "Panel B", "UHID": "A25", "Cash Due": 0, "Credit Due": 0, "Bed Days": 0},
            {"Type": "Discharge", "DOA": "01/01/2026", "DOD": "05/01/2026", "Service Final Amount": "200", "Panel Name": "Panel A", "UHID": "D26", "Cash Due": 0, "Credit Due": 20, "Bed Days": 1},
            {"Type": "OPD", "DOA": "06/01/2026", "DOD": None, "Service Final Amount": "60", "Panel Name": "Panel A", "UHID": "O26", "Cash Due": 0, "Credit Due": 0, "Bed Days": 0},
            {"Type": "Admission", "DOA": "07/01/2026", "DOD": None, "Service Final Amount": "2000", "Panel Name": "Panel B", "UHID": "A26", "Cash Due": 0, "Credit Due": 0, "Bed Days": 0},
            {"Type": "OPD", "DOA": "08/01/2026", "DOD": None, "Service Final Amount": "", "Panel Name": "Panel A", "UHID": "O26-blank", "Cash Due": 0, "Credit Due": 0, "Bed Days": 0},
        ])
        cleaned = backend.clean_data(rows)
        year_frames = [
            backend.filter_dashboard_comparison(cleaned, year, month="January")
            for year in [2025, 2026]
        ]

        self.assertEqual(
            [backend.calculate_kpis(frame)["revenue"] for frame in year_frames],
            [140, 260],
        )
        for frame, expected_total, expected_discharge in zip(
            year_frames, [140, 260], [100, 200]
        ):
            totals = backend.revenue_totals(frame)
            self.assertEqual(totals["total"], expected_total)
            self.assertEqual(totals["discharge"], expected_discharge)
            self.assertEqual(totals["total"], totals["discharge"] + totals["opd"])

            ipd = backend.ipd_revenue_breakdown(frame)
            self.assertEqual(ipd["total"], expected_discharge)
            panels = backend.panel_performance_outstanding(frame)
            self.assertEqual(panels["IPD Revenue"].sum(), expected_discharge)

        panel_comparison = backend.comparison_table_rows(
            backend.panel_performance_outstanding(year_frames[0]).rename(
                columns={"Panel": "Name", "IPD Revenue": "Value"}
            ),
            backend.panel_performance_outstanding(year_frames[1]).rename(
                columns={"Panel": "Name", "IPD Revenue": "Value"}
            ),
            "Name",
        )
        self.assertEqual(panel_comparison["2025"].sum(), 100)
        self.assertEqual(panel_comparison["2026"].sum(), 200)

    def test_all_saved_uploads_contribute_to_comparison_year_data(self):
        january_to_august = pd.DataFrame([
            {
                "Type": "OPD",
                "DOA": f"15/{month:02d}/2026",
                "Month": pd.Timestamp(2026, month, 1).strftime("%B"),
                "Service Final Amount": month * 10,
            }
            for month in range(1, 9)
        ])
        september = pd.DataFrame([{
            "Type": "Discharge",
            "DOA": "10/09/2026",
            "DOD": "30/09/2026",
            "Month": "September",
            "Service Final Amount": 900,
        }])
        database.save_hospital_data(
            january_to_august,
            "Surya Hospital",
            "january-august.xlsx",
            b"january-august-upload",
        )
        database.save_hospital_data(
            september,
            "Surya Hospital",
            "september.xlsx",
            b"september-upload",
        )

        all_saved = self.load_master()
        year_2026 = backend.filter_dashboard_comparison(all_saved, 2026)
        event_dates = backend.comparison_event_dates(year_2026)
        self.assertEqual(len(year_2026), 9)
        self.assertEqual(
            set(event_dates.dt.month),
            set(range(1, 10)),
        )
        self.assertEqual(
            backend.revenue_totals(year_2026)["total"],
            sum(month * 10 for month in range(1, 9)) + 900,
        )


if __name__ == "__main__":
    unittest.main()
