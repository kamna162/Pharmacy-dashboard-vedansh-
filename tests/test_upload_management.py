import csv
import base64
import http.client
import json
import re
import tempfile
import threading
import unittest
import zlib
from http.server import ThreadingHTTPServer
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import auth
from comparison import backend, dashboard_service, database
from server import DashboardHandler, MAX_REQUEST_BYTES, MAX_UPLOAD_BYTES


def make_csv(headers, rows):
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


class UploadManagementTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch.object(
            auth, "username_from_cookie", return_value="test-user"
        )
        self.auth_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        self.original_uploads_dir = database.UPLOADS_DIR
        database.DATABASE_PATH = Path(self.temp_dir.name) / "test.db"
        database.UPLOADS_DIR = Path(self.temp_dir.name) / "uploads"
        database.init_database()
        dashboard_service._invalidate_data_caches()

    def tearDown(self):
        dashboard_service._invalidate_data_caches()
        database.DATABASE_PATH = self.original_database_path
        database.UPLOADS_DIR = self.original_uploads_dir
        self.temp_dir.cleanup()
        self.auth_patch.stop()

    def test_development_version_endpoint_requires_dev_mode(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict("os.environ", {"DASHBOARD_DEV_RELOAD": "1"}):
                connection = http.client.HTTPConnection(
                    "127.0.0.1", httpd.server_port, timeout=10
                )
                connection.request("GET", "/api/dev/version")
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertTrue(payload["enabled"])
                self.assertTrue(payload["version"])
                connection.close()

            with patch.dict("os.environ", {"DASHBOARD_DEV_RELOAD": ""}):
                connection = http.client.HTTPConnection(
                    "127.0.0.1", httpd.server_port, timeout=10
                )
                connection.request("GET", "/api/dev/version")
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertFalse(payload["enabled"])
                connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_empty_database_still_offers_hospitals_for_upload(self):
        options = dashboard_service.dashboard_options()

        self.assertIn("NIMMS Hospital", options["hospitals"])
        self.assertIn("Surya Hospital", options["hospitals"])
        self.assertEqual(options["defaultHospital"], "All Hospitals")
        self.assertEqual(options["datasets"], [])
        self.assertEqual(options["dateRange"], {"start": None, "end": None})

    def test_all_hospitals_dashboard_combines_saved_hospital_data(self):
        shared = {
            "DOA": "2024-01-15",
            "DOD": "2024-01-15",
            "Month": "January",
            "Dept. Name": "Medicine",
            "Doctor Name": "Dr A",
            "Panel Name": "Cash",
            "Type": "OPD",
            "Service Name": "Consultation",
            "Service Final Amount": 125,
            "Cash Due": 0,
            "Credit Due": 0,
            "Bed Days": 0,
        }
        database.save_hospital_data(
            pd.DataFrame([{**shared, "UHID": "ALL-HOSPITAL-1"}]),
            "NIMMS Hospital",
            "nimms.xlsx",
            b"all-hospitals-nimms",
        )
        database.save_hospital_data(
            pd.DataFrame([{**shared, "UHID": "ALL-HOSPITAL-2"}]),
            "Surya Hospital",
            "surya.xlsx",
            b"all-hospitals-surya",
        )
        dashboard_service._invalidate_data_caches()

        result = dashboard_service.get_dashboard({"hospital": "All Hospitals"})

        self.assertTrue(result["hasData"])
        self.assertEqual(result["recordCount"], 2)
        self.assertEqual(result["kpis"]["revenue"], 250)

        unfiltered = dashboard_service.get_dashboard({
            "hospital": "  all hospitals  ",
        })

        self.assertTrue(unfiltered["hasData"])
        self.assertEqual(unfiltered["recordCount"], 2)
        self.assertEqual(unfiltered["filters"]["month"], [])
        self.assertEqual(unfiltered["filters"]["period"], [])

    def test_plausible_picker_dates_ignore_outliers_but_full_range_keeps_rows(self):
        shared = {
            "Month": "January",
            "Dept. Name": "Medicine",
            "Doctor Name": "Dr A",
            "Panel Name": "Cash",
            "Service Name": "Consultation",
            "Cash Due": 0,
            "Credit Due": 0,
            "Bed Days": 0,
        }
        rows = pd.DataFrame([
            {
                **shared, "DOA": "1900-01-01", "DOD": "1900-01-01",
                "Type": "OPD", "Service Final Amount": 100,
                "UHID": "DATE-OUTLIER",
            },
            {
                **shared, "DOA": "2024-01-15", "DOD": "",
                "Type": "Admission", "Service Final Amount": 0,
                "UHID": "DATE-ADMISSION",
            },
            {
                **shared, "DOA": "2024-03-10", "DOD": "2023-06-01",
                "Type": "OPD", "Service Final Amount": 200,
                "UHID": "OPD-LEFTOVER-DOD",
            },
            {
                **shared, "DOA": "1900-01-01", "DOD": "2024-06-20",
                "Type": "Discharge", "Service Final Amount": 300,
                "UHID": "DATE-DISCHARGE",
            },
            {
                **shared, "DOA": "", "DOD": "2023-06-01",
                "Type": "OPD", "Service Final Amount": 50,
                "UHID": "MISSING-OPD-DATE",
            },
        ])
        database.save_hospital_data(
            rows, "Plausible Dates Hospital", "dates.xlsx", b"plausible-dates"
        )
        dashboard_service._invalidate_data_caches()

        options = dashboard_service.dashboard_options()
        dashboard = dashboard_service.get_dashboard({
            "hospital": "Plausible Dates Hospital",
        })

        expected_bounds = {"start": "2024-01-15", "end": "2024-06-20"}
        self.assertEqual(options["dateRange"], expected_bounds)
        self.assertEqual(options["dateOptions"]["dateRange"], expected_bounds)
        self.assertEqual(options["months"], ["January", "March", "June"])
        self.assertEqual(dashboard["dateRange"], expected_bounds)
        self.assertEqual(dashboard["selectedRange"], expected_bounds)
        self.assertEqual(dashboard["dateOptions"]["years"], [2024])
        self.assertEqual(dashboard["options"]["months"], ["January", "March", "June"])
        self.assertEqual(
            dashboard["dateOptions"]["monthsByYear"],
            [{
                "year": 2024,
                "months": [
                    {"value": 1, "label": "January"},
                    {"value": 3, "label": "March"},
                    {"value": 6, "label": "June"},
                ],
            }],
        )
        self.assertEqual(dashboard["recordCount"], len(rows))
        self.assertEqual(dashboard["kpis"]["revenue"], 650)
        self.assertEqual(dashboard["kpis"]["opd_revenue"], 350)
        self.assertAlmostEqual(dashboard["kpis"]["per_day_revenue"], 650 / 158)

    def test_picker_date_window_uses_2000_through_today_plus_366_days(self):
        frame = pd.DataFrame({
            "Type": ["Admission", "Admission", "Admission"],
            "DOA": ["1999-12-31", "2027-10-22", "2027-10-23"],
            "DOD": ["", "", ""],
        })

        options = dashboard_service._date_picker_options(
            frame, today="2026-10-21"
        )

        self.assertEqual(
            options["dateRange"],
            {"start": "2027-10-22", "end": "2027-10-22"},
        )
        self.assertEqual(options["years"], [2027])

    def test_nimms_date_range_expands_when_second_half_is_uploaded(self):
        headers = [
            "UHID", "Patient Name", "Service Name", "Service Type",
            "Service Date", "Service Final Amt",
        ]
        first_half = make_csv(headers, [
            ["FIRST-HALF-START", "Patient One", "Consultation", "Consultation", "2024-01-01", "100"],
            ["FIRST-HALF-END", "Patient Two", "Consultation", "Consultation", "2024-06-30", "100"],
        ])
        second_half = make_csv(headers, [
            ["SECOND-HALF-START", "Patient Three", "Consultation", "Consultation", "2024-07-01", "100"],
            ["SECOND-HALF-END", "Patient Four", "Consultation", "Consultation", "2024-12-31", "100"],
        ])
        first_upload = dashboard_service.upload_files(
            [("first-half-services.csv", first_half)],
            hospital_name="NIMMS Hospital",
        )

        first_options = dashboard_service.dashboard_options()
        second_upload = dashboard_service.upload_files(
            [("second-half-services.csv", second_half)],
            hospital_name="NIMMS Hospital",
        )

        options = dashboard_service.dashboard_options()
        result = dashboard_service.get_dashboard({
            "hospital": "NIMMS Hospital",
        })
        self.assertEqual(first_upload["dataset_id"], second_upload["dataset_id"])
        self.assertEqual(
            first_options["dateRange"]["start"].split("T")[0],
            "2024-01-01",
        )
        self.assertEqual(
            first_options["dateRange"]["end"].split("T")[0],
            "2024-06-30",
        )
        self.assertEqual(
            options["dateRange"]["start"].split("T")[0],
            "2024-01-01",
        )
        self.assertEqual(
            options["dateRange"]["end"].split("T")[0],
            "2024-12-31",
        )
        self.assertEqual(
            result["dateRange"],
            {"start": "2024-01-01", "end": "2024-12-31"},
        )
        self.assertEqual(result["recordCount"], 4)
        self.assertEqual(result["kpis"]["revenue"], 400)
        self.assertAlmostEqual(result["kpis"]["per_day_revenue"], 400 / 366)
        with patch(
            "comparison.prediction_service.get_prediction",
            return_value={"hasData": False, "message": "No data"},
        ):
            html = dashboard_service.export_interactive_html().decode("utf-8")
        match = re.search(
            r'window\.__OFFLINE_BUNDLE__="([A-Za-z0-9+/=]+)"', html
        )
        self.assertIsNotNone(match)
        bundle = json.loads(zlib.decompress(base64.b64decode(match.group(1))))
        self.assertEqual(sum(item["record_count"] for item in bundle["datasets"]), 4)
        self.assertEqual(len(bundle["rows"]), 4)
        self.assertIn('id="range-period-year-select"', html)
        self.assertIn("function offlineDatePickerOptions(rows)", html)

    def test_uploads_raw_reports_into_one_stable_hospital_dataset(self):
        admission = make_csv(
            [
                "File Id", "UHID", "Patient Name", "Doctor Name", "Dept. Name",
                "Panel Name", "Doa", "Service Gross Amt", "Service Final Amt",
                "Cash Due", "Credit Due",
            ],
            [[
                "F1", "123", "Patient One", "Dr A", "Medicine", "Cash",
                "2026-08-01", "100", "90", "5", "10",
            ]],
        )
        discharge = make_csv(
            [
                "File Id", "UHID", "Patient Name", "Doctor Name", "Dept. Name",
                "Panel Name", "DOA", "DOD", "Service Gross Amt",
                "Service Final Amt", "Cash Due", "Credit Due",
            ],
            [[
                "F1", "123", "Patient One", "Dr A", "Medicine", "Cash",
                "", "2026-08-03", "100", "90", "5", "10",
            ]],
        )
        service = make_csv(
            [
                "UHID", "Patient Name", "Doctor Name", "Dept. Name", "Panel Name",
                "Service Name", "Service Type", "Service Date",
                "Service Gross Amt", "Service Final Amt",
            ],
            [[
                "123", "Patient One", "Dr A", "Medicine", "Cash",
                "Consultation", "Consultation", "2026-08-01 09:00:00", "25", "20",
            ]],
        )

        result = dashboard_service.upload_files(
            [
                ("admissions.csv", admission),
                ("discharge.csv", discharge),
                ("services.csv", service),
            ],
            hospital_name="Surya Hospital",
        )

        dataset_id = database.raw_dataset_id("Surya Hospital")
        self.assertEqual(result["dataset_id"], dataset_id)
        self.assertEqual(result["raw_dataset"]["new_upload_count"], 3)
        self.assertEqual(len(result["report"]), 3)
        rows = database.load_hospital_data(dataset_id=dataset_id)
        self.assertEqual(set(rows["Type"]), {"Admission", "Discharge", "OPD"})
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows.loc[rows["Type"] == "Admission", "DOD"].iloc[0], "2026-08-03")
        self.assertEqual(len(database.list_report_uploads("Surya Hospital")), 3)

    def test_existing_merged_rows_survive_raw_rebuilds_and_duplicates_are_skipped(self):
        old_rows = pd.DataFrame([{
            "DOA": "2025-02-01",
            "DOD": "",
            "Month": "February",
            "Dept. Name": "Legacy",
            "Doctor Name": "Dr Legacy",
            "Panel Name": "Cash",
            "Type": "Admission",
            "Service Name": "",
            "Service Final Amount": 0,
            "Cash Due": 0,
            "Credit Due": 0,
            "Bed Days": 0,
            "UHID": "LEGACY-1",
        }])
        old_result = database.save_hospital_data(
            old_rows,
            "NIMMS Hospital",
            "existing-merged.xlsx",
            b"existing-merged-workbook",
        )
        report_bytes = make_csv(
            ["File Id", "UHID", "Doa", "Service Gross Amt", "Service Final Amt"],
            [["F1", "1001", "2026-08-01", "30", "25"]],
        )

        first = dashboard_service.upload_files(
            [("admission.csv", report_bytes)],
            hospital_name="NIMMS Hospital",
        )
        second = dashboard_service.upload_files(
            [("renamed-copy.csv", report_bytes)],
            hospital_name="NIMMS Hospital",
        )

        self.assertNotEqual(old_result["dataset_id"], first["dataset_id"])
        self.assertEqual(first["dataset_id"], second["dataset_id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["total"], first["total"])
        old_saved = database.load_hospital_data(dataset_id=old_result["dataset_id"])
        self.assertEqual(old_saved["UHID"].tolist(), ["LEGACY-1"])
        combined = database.load_all_hospital_data()
        self.assertEqual(
            combined.loc[combined["UHID"] == "LEGACY-1", "Type"].tolist(),
            ["Admission"],
        )
        self.assertEqual(len(database.list_saved_datasets()), 2)

    def test_include_and_delete_rebuild_only_raw_report_rows(self):
        old_rows = pd.DataFrame([{
            "DOA": "2025-02-01",
            "DOD": "",
            "Month": "February",
            "Dept. Name": "Legacy",
            "Doctor Name": "Dr Legacy",
            "Panel Name": "Cash",
            "Type": "Admission",
            "Service Name": "",
            "Service Final Amount": 0,
            "Cash Due": 0,
            "Credit Due": 0,
            "Bed Days": 0,
            "UHID": "LEGACY-2",
        }])
        old_result = database.save_hospital_data(
            old_rows, "Surya Hospital", "legacy.xlsx", b"legacy-source"
        )
        report_bytes = make_csv(
            ["File Id", "UHID", "Doa"],
            [["F1", "2001", "2026-08-01"]],
        )
        uploaded = dashboard_service.upload_files(
            [("admissions.csv", report_bytes)],
            hospital_name="Surya Hospital",
        )
        upload = dashboard_service.list_report_uploads("Surya Hospital")[0]

        disabled = dashboard_service.set_report_upload_included(
            upload["upload_id"], False
        )
        self.assertEqual(disabled["record_count"], 0)
        self.assertTrue(
            database.load_hospital_data(dataset_id=uploaded["dataset_id"]).empty
        )
        self.assertEqual(
            database.load_hospital_data(dataset_id=old_result["dataset_id"])[
                "UHID"
            ].tolist(),
            ["LEGACY-2"],
        )

        enabled = dashboard_service.set_report_upload_included(
            upload["upload_id"], True
        )
        self.assertEqual(enabled["record_count"], 1)
        removed = dashboard_service.delete_report_upload(upload["upload_id"])
        self.assertEqual(removed["record_count"], 0)
        self.assertEqual(dashboard_service.list_report_uploads("Surya Hospital"), [])
        self.assertTrue(
            database.load_hospital_data(dataset_id=uploaded["dataset_id"]).empty
        )

    def test_hospital_remains_manageable_when_all_raw_reports_are_excluded(self):
        report_bytes = make_csv(
            ["File Id", "UHID", "Doa"],
            [["F1", "2002", "2026-08-01"]],
        )
        dashboard_service.upload_files(
            [("admissions.csv", report_bytes)], hospital_name="Excluded Hospital"
        )
        upload = dashboard_service.list_report_uploads("Excluded Hospital")[0]

        dashboard_service.set_report_upload_included(upload["upload_id"], False)
        options = dashboard_service.dashboard_options()

        self.assertIn("Excluded Hospital", options["hospitals"])
        self.assertEqual(options["defaultHospital"], "All Hospitals")
        self.assertFalse(
            dashboard_service.get_dashboard(
                {"hospital": "Excluded Hospital"}
            )["hasData"]
        )
        self.assertFalse(
            dashboard_service.list_report_uploads("Excluded Hospital")[0]["included"]
        )

    def test_excluded_raw_only_hospital_shows_empty_when_another_hospital_has_data(self):
        report_bytes = make_csv(
            ["File Id", "UHID", "Doa"],
            [["F1", "2003", "2026-08-01"]],
        )
        dashboard_service.upload_files(
            [("admissions.csv", report_bytes)], hospital_name="Empty Hospital"
        )
        upload = dashboard_service.list_report_uploads("Empty Hospital")[0]
        dashboard_service.set_report_upload_included(upload["upload_id"], False)
        database.save_hospital_data(
            pd.DataFrame([{
                "DOA": "2026-08-03",
                "DOD": "",
                "Month": "August",
                "Dept. Name": "Medicine",
                "Doctor Name": "Dr Other",
                "Panel Name": "Cash",
                "Type": "Admission",
                "Service Name": "",
                "Service Final Amount": 0,
                "Cash Due": 0,
                "Credit Due": 0,
                "Bed Days": 0,
                "UHID": "OTHER-EMPTY-TEST",
            }]),
            "Populated Hospital",
            "other.xlsx",
            b"other-empty-test",
        )
        dashboard_service._invalidate_data_caches()

        result = dashboard_service.get_dashboard({"hospital": "Empty Hospital"})

        self.assertFalse(result["hasData"])
        self.assertEqual(result["recordCount"], 0)
        self.assertEqual(result["selectedRange"], {"start": None, "end": None})

    def test_admission_and_discharge_uploaded_separately_still_join(self):
        admission = make_csv(
            [
                "File Id", "UHID", "Patient Name", "Doctor Name", "Dept. Name",
                "Panel Name", "Doa", "Service Gross Amt", "Service Final Amt",
            ],
            [["F1", "3001", "Patient Three", "Dr C", "Medicine", "Cash",
              "2026-08-01", "100", "90"]],
        )
        discharge = make_csv(
            [
                "File Id", "UHID", "Patient Name", "Doctor Name", "Dept. Name",
                "Panel Name", "DOA", "DOD", "Service Gross Amt",
                "Service Final Amt",
            ],
            [["F1", "3001", "Patient Three", "Dr C", "Medicine", "Cash",
              "", "2026-08-04", "100", "90"]],
        )

        first = dashboard_service.upload_files(
            [("admissions.csv", admission)], hospital_name="Join Hospital"
        )
        second = dashboard_service.upload_files(
            [("discharge.csv", discharge)], hospital_name="Join Hospital"
        )

        self.assertEqual(first["dataset_id"], second["dataset_id"])
        rows = database.load_hospital_data(dataset_id=second["dataset_id"])
        admission_row = rows.loc[rows["Type"] == "Admission"].iloc[0]
        self.assertEqual(admission_row["DOA"], "2026-08-01")
        self.assertEqual(admission_row["DOD"], "2026-08-04")
        self.assertEqual(len(rows.loc[rows["Type"] == "Discharge"]), 1)

    def test_excluding_and_deleting_a_raw_file_preserves_other_report_rows(self):
        admissions = make_csv(
            ["File Id", "UHID", "Doa"],
            [
                ["F1", "4001", "2026-08-01"],
                ["F2", "4002", "2026-08-02"],
            ],
        )
        services = make_csv(
            ["UHID", "Service Type", "Service Date", "Service Name"],
            [["4001", "Consultation", "2026-08-01", "Consultation"]],
        )
        uploaded = dashboard_service.upload_files(
            [("admissions.csv", admissions)], hospital_name="Manage Hospital"
        )
        dashboard_service.upload_files(
            [("services.csv", services)], hospital_name="Manage Hospital"
        )
        uploads = dashboard_service.list_report_uploads("Manage Hospital")
        service_upload = next(item for item in uploads if item["kind"] == "Service")

        excluded = dashboard_service.set_report_upload_included(
            service_upload["upload_id"], False
        )
        rows = database.load_hospital_data(dataset_id=uploaded["dataset_id"])
        self.assertEqual(excluded["record_count"], 2)
        self.assertEqual(rows["Type"].tolist(), ["Admission", "Admission"])
        self.assertEqual(set(rows["UHID"]), {"4001", "4002"})

        dashboard_service.set_report_upload_included(
            service_upload["upload_id"], True
        )
        deleted = dashboard_service.delete_report_upload(service_upload["upload_id"])
        rows = database.load_hospital_data(dataset_id=uploaded["dataset_id"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["record_count"], 2)
        self.assertEqual(rows["Type"].tolist(), ["Admission", "Admission"])
        self.assertEqual(
            [item["kind"] for item in dashboard_service.list_report_uploads(
                "Manage Hospital"
            )],
            ["Admission"],
        )

    def test_merged_workbook_upload_preserves_its_rows_after_raw_rebuild(self):
        merged_rows = pd.DataFrame([
            {
                "DOA": "2025-02-01",
                "DOD": "",
                "Month": "February",
                "Dept. Name": "Legacy",
                "Doctor Name": "Dr Legacy",
                "Panel Name": "Cash",
                "Type": "Admission",
                "Service Name": "",
                "Service Final Amount": 900,
                "Cash Due": 100,
                "Credit Due": 200,
                "Bed Days": 0,
                "UHID": "MERGED-1",
            },
        ])
        workbook = BytesIO()
        merged_rows.to_excel(workbook, index=False)
        upload = dashboard_service.upload_files(
            [("legacy.xlsx", workbook.getvalue())],
            hospital_name="Workbook Hospital",
        )
        saved_before = database.load_hospital_data(
            dataset_id=upload["dataset_id"]
        ).reset_index(drop=True)
        self.assertEqual(upload["inserted"], 1)
        self.assertEqual(saved_before["UHID"].tolist(), ["MERGED-1"])

        raw = make_csv(
            ["File Id", "UHID", "Doa"],
            [["F1", "5001", "2026-08-01"]],
        )
        dashboard_service.upload_files(
            [("admissions.csv", raw)], hospital_name="Workbook Hospital"
        )

        saved_after = database.load_hospital_data(
            dataset_id=upload["dataset_id"]
        ).reset_index(drop=True)
        pd.testing.assert_frame_equal(
            saved_after.loc[:, saved_before.columns], saved_before
        )

    def test_multipart_ui_upload_path_accepts_merged_workbook(self):
        rows = pd.DataFrame([{
            "DOA": "2025-02-01",
            "DOD": "",
            "Month": "February",
            "Dept. Name": "Legacy",
            "Doctor Name": "Dr Legacy",
            "Panel Name": "Cash",
            "Type": "Admission",
            "Service Name": "",
            "Service Final Amount": 900,
            "Cash Due": 100,
            "Credit Due": 200,
            "Bed Days": 0,
            "UHID": "UI-MERGED-1",
        }])
        workbook = BytesIO()
        rows.to_excel(workbook, index=False)
        workbook_bytes = workbook.getvalue()
        boundary = "phase3-merged-ui-upload"
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                'filename="legacy.xlsx"\r\n'
                "Content-Type: application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet\r\n\r\n"
            ).encode(),
            workbook_bytes,
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="hospitalName"\r\n\r\n',
            b"UI Workbook Hospital\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=10
            )
            connection.request(
                "POST",
                "/api/upload",
                body=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 201, payload)
            self.assertEqual(
                payload["upload"]["hospital_name"], "UI Workbook Hospital"
            )
            self.assertEqual(payload["upload"]["inserted"], 1)
            saved = database.load_hospital_data(
                dataset_id=payload["upload"]["dataset_id"]
            )
            self.assertEqual(saved["UHID"].tolist(), ["UI-MERGED-1"])
            self.assertEqual(
                pd.to_numeric(saved["Service Final Amount"]).tolist(), [900]
            )
            connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_interactive_export_keeps_stable_raw_dataset_id(self):
        raw = make_csv(
            ["Service Date", "UHID", "Service Name"],
            [["2026-08-01", "6001", "Gastro Consultation"]],
        )
        uploaded = dashboard_service.upload_files(
            [("services.csv", raw)], hospital_name="Export Hospital"
        )

        with patch(
            "comparison.prediction_service.get_prediction",
            return_value={"hasData": False, "message": "No data"},
        ):
            html = dashboard_service.export_interactive_html().decode("utf-8")

        match = re.search(
            r'window\.__OFFLINE_BUNDLE__="([A-Za-z0-9+/=]+)"', html
        )
        self.assertIsNotNone(match)
        bundle = json.loads(zlib.decompress(base64.b64decode(match.group(1))))
        self.assertEqual(bundle["datasets"][0]["dataset_id"], uploaded["dataset_id"])
        self.assertTrue(uploaded["dataset_id"].startswith("raw-"))
        self.assertFalse(bundle["datasets"][0]["service_type_available"])
        self.assertTrue(bundle["datasets"][0]["service_name_available"])
        self.assertTrue(bundle["rows"])
        self.assertTrue(all(row[0] == 0 for row in bundle["rows"]))
        self.assertIn("function offlineDatasetRows(datasetId)", html)
        self.assertIn(
            "item.dataset_id === datasetId",
            html,
        )
        self.assertIn("function offlineDoaRange(rows)", html)
        self.assertIn("function offlineConsultationMatcher(schemaRows)", html)
        self.assertNotIn('/assets/offline-dashboard.js', html)

    def test_interactive_export_comparison_options_ignore_outlier_years(self):
        rows = pd.DataFrame([
            {"Type": "Admission", "DOA": "1900-01-01", "UHID": "OLD"},
            {"Type": "Admission", "DOA": "2024-06-10", "UHID": "VALID-1"},
            {"Type": "Discharge", "DOA": "2024-06-01", "DOD": "2026-09-30", "UHID": "VALID-2"},
            {"Type": "Admission", "DOA": "2029-01-01", "UHID": "FUTURE"},
        ])
        database.save_hospital_data(
            rows, "Outlier Export Hospital", "outlier-dates.xlsx", b"outlier-export"
        )
        dashboard_service._invalidate_data_caches()

        with patch(
            "comparison.prediction_service.get_prediction",
            return_value={"hasData": False, "message": "No data"},
        ):
            html = dashboard_service.export_interactive_html().decode("utf-8")

        match = re.search(
            r'window\.__OFFLINE_BUNDLE__="([A-Za-z0-9+/=]+)"', html
        )
        self.assertIsNotNone(match)
        bundle = json.loads(zlib.decompress(base64.b64decode(match.group(1))))
        self.assertEqual(
            bundle["comparison"],
            {
                "years": [2024, 2026],
                "months": ["June", "September"],
                "latestDate": "2026-09-30",
            },
        )

    def test_dashboard_uses_all_hospital_datasets_and_prorates_selected_range(self):
        shared = {
            "Month": "August",
            "Dept. Name": "Medicine",
            "Doctor Name": "Dr A",
            "Panel Name": "Cash",
            "Service Type": "Consultation",
            "Service Name": "Consultation",
            "Service Final Amount": 100,
            "Cash Due": 10,
            "Credit Due": 5,
            "Bed Days": 2,
        }
        first_workbook = pd.DataFrame([
            {
                **shared,
                "DOA": "2026-08-03",
                "DOD": "",
                "Type": "Admission",
                "UHID": "RANGE-1",
            },
            {
                **shared,
                "DOA": "2026-08-03",
                "DOD": "2026-08-15",
                "Type": "Discharge",
                "UHID": "RANGE-2",
            },
        ])
        second_workbook = pd.DataFrame([{
            **shared,
            "DOA": "2026-08-22",
            "DOD": "2026-08-22",
            "Type": "OPD",
            "UHID": "RANGE-3",
        }])
        unrelated_hospital = pd.DataFrame([{
            **shared,
            "DOA": "2026-08-15",
            "DOD": "2026-08-15",
            "Type": "Discharge",
            "UHID": "OTHER-1",
        }])
        database.save_hospital_data(
            first_workbook, "Range Hospital", "first.xlsx", b"range-first"
        )
        database.save_hospital_data(
            second_workbook, "Range Hospital", "second.xlsx", b"range-second"
        )
        database.save_hospital_data(
            unrelated_hospital, "Other Hospital", "other.xlsx", b"range-other"
        )
        dashboard_service._invalidate_data_caches()

        complete = dashboard_service.get_dashboard({"hospital": "Range Hospital"})
        selected = dashboard_service.get_dashboard({
            "hospital": "Range Hospital",
            "startDate": "2026-08-12",
            "endDate": "2026-08-16",
        })
        month_period = dashboard_service.get_dashboard({
            "hospital": "Range Hospital",
            "startDate": "2026-08-03",
            "endDate": "2026-08-22",
            "month": ["August"],
            "period": ["11-20 Days"],
        })

        self.assertEqual(complete["recordCount"], 3)
        self.assertEqual(
            complete["dataset"]["dataset_id"],
            database.raw_dataset_id("Range Hospital"),
        )
        self.assertEqual(selected["recordCount"], 1)
        self.assertEqual(selected["kpis"]["revenue"], 100)
        self.assertAlmostEqual(selected["kpis"]["target"], 50_000_000 * 5 / 31)
        self.assertAlmostEqual(
            selected["kpis"]["occupancy"],
            0,
        )
        self.assertEqual(month_period["recordCount"], 1)
        self.assertAlmostEqual(
            month_period["kpis"]["target"], 50_000_000 * 10 / 31
        )

    def test_occupancy_excludes_doa_dates_before_selected_range(self):
        rows = pd.DataFrame([
            {
                "DOA": "2026-07-20",
                "DOD": "",
                "Month": "July",
                "Dept. Name": "Medicine",
                "Doctor Name": "Dr A",
                "Panel Name": "Cash",
                "Type": "Admission",
                "Service Name": "",
                "Service Final Amount": 0,
                "Cash Due": 0,
                "Credit Due": 0,
                "Bed Days": 0,
                "UHID": "OCCUPANCY-JULY",
            },
            {
                "DOA": "2026-07-20",
                "DOD": "2026-08-05",
                "Month": "August",
                "Dept. Name": "Medicine",
                "Doctor Name": "Dr A",
                "Panel Name": "Cash",
                "Type": "Discharge",
                "Service Name": "",
                "Service Final Amount": 100,
                "Cash Due": 0,
                "Credit Due": 0,
                "Bed Days": 4,
                "UHID": "OCCUPANCY-JULY",
            },
            {
                "DOA": "2026-08-06",
                "DOD": "2026-08-06",
                "Month": "August",
                "Dept. Name": "Medicine",
                "Doctor Name": "Dr B",
                "Panel Name": "Cash",
                "Type": "Discharge",
                "Service Name": "",
                "Service Final Amount": 100,
                "Cash Due": 0,
                "Credit Due": 0,
                "Bed Days": 2,
                "UHID": "OCCUPANCY-AUGUST",
            },
        ])
        database.save_hospital_data(
            rows, "Occupancy Hospital", "occupancy.xlsx", b"occupancy-range"
        )
        dashboard_service._invalidate_data_caches()

        selected_range = dashboard_service.get_dashboard({
            "hospital": "Occupancy Hospital",
            "startDate": "2026-08-01",
            "endDate": "2026-08-06",
        })
        full_range = dashboard_service.get_dashboard({
            "hospital": "Occupancy Hospital",
        })

        self.assertEqual(selected_range["recordCount"], 2)
        self.assertEqual(
            selected_range["selectedRange"],
            {"start": "2026-08-01", "end": "2026-08-06"},
        )
        self.assertEqual(selected_range["kpis"]["bed_days"], 6)
        self.assertAlmostEqual(
            selected_range["kpis"]["occupancy"],
            6 / backend.TOTAL_BEDS * 100,
        )
        self.assertEqual(full_range["recordCount"], 3)
        self.assertEqual(
            full_range["selectedRange"],
            {"start": "2026-07-20", "end": "2026-08-06"},
        )
        self.assertEqual(full_range["kpis"]["bed_days"], 6)
        self.assertAlmostEqual(
            full_range["kpis"]["occupancy"],
            6 / (backend.TOTAL_BEDS * 2) * 100,
        )

    def test_multipart_upload_accepts_three_reports_and_hospital_field(self):
        admission = make_csv(
            ["File Id", "UHID", "Doa"],
            [["F1", "123", "2026-08-01"]],
        )
        discharge = make_csv(
            ["File Id", "DOA", "DOD"],
            [["F1", "2026-08-01", "2026-08-02"]],
        )
        service = make_csv(
            ["UHID", "Service Type", "Service Date"],
            [["123", "Consultation", "2026-08-01"]],
        )
        boundary = "phase2-test-boundary"
        parts = []
        for filename, content in (
            ("admissions.csv", admission),
            ("discharge.csv", discharge),
            ("service.csv", service),
        ):
            parts.extend([
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="file"; '
                    f'filename="{filename}"\r\n'
                    "Content-Type: text/csv\r\n\r\n"
                ).encode(),
                content,
                b"\r\n",
            ])
        parts.extend([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="hospitalName"\r\n\r\n',
            b"Selected Hospital\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        body = b"".join(parts)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=10
            )
            connection.request(
                "POST",
                "/api/upload",
                body=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 201, payload)
            self.assertEqual(payload["upload"]["hospital_name"], "Selected Hospital")
            self.assertEqual(len(payload["upload"]["report"]), 3)

            connection.request("GET", "/api/uploads?hospitalName=Selected%20Hospital")
            listing = json.loads(connection.getresponse().read().decode("utf-8"))
            self.assertEqual(len(listing["uploads"]), 3)
            upload_id = listing["uploads"][0]["upload_id"]

            include_body = json.dumps({
                "uploadId": upload_id,
                "included": False,
            }).encode("utf-8")
            connection.request(
                "POST",
                "/api/uploads/include",
                body=include_body,
                headers={"Content-Type": "application/json"},
            )
            include_response = connection.getresponse()
            include_payload = json.loads(
                include_response.read().decode("utf-8")
            )
            self.assertEqual(include_response.status, 200)
            self.assertTrue(include_payload["success"])
            self.assertFalse(include_payload["result"]["deleted"])

            delete_body = json.dumps({"uploadId": upload_id}).encode("utf-8")
            connection.request(
                "POST",
                "/api/uploads/delete",
                body=delete_body,
                headers={"Content-Type": "application/json"},
            )
            delete_response = connection.getresponse()
            delete_payload = json.loads(delete_response.read().decode("utf-8"))
            self.assertEqual(delete_response.status, 200)
            self.assertTrue(delete_payload["success"])
            self.assertTrue(delete_payload["result"]["deleted"])

            connection.request("GET", "/api/uploads?hospitalName=Selected%20Hospital")
            remaining = json.loads(connection.getresponse().read().decode("utf-8"))
            self.assertEqual(len(remaining["uploads"]), 2)
            connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertEqual(MAX_UPLOAD_BYTES, 25 * 1024 * 1024)
        self.assertGreaterEqual(MAX_REQUEST_BYTES, 3 * MAX_UPLOAD_BYTES)


if __name__ == "__main__":
    unittest.main()
