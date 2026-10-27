from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pandas as pd

import backend
import dashboard_service
import database
import scraper_importer
from server import DashboardHandler


def make_workbook(columns, rows):
    output = BytesIO()
    pd.DataFrame(rows, columns=columns).to_excel(output, index=False)
    return output.getvalue()


class ScraperImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        self.original_uploads_dir = database.UPLOADS_DIR
        self.original_scraper_folder = scraper_importer.SCRAPER_FOLDER
        database.DATABASE_PATH = Path(self.temp_dir.name) / "hospital_data.db"
        database.UPLOADS_DIR = Path(self.temp_dir.name) / "uploads"
        scraper_importer.SCRAPER_FOLDER = Path(self.temp_dir.name) / "inbox"
        scraper_importer.SCRAPER_FOLDER.mkdir()
        database.init_database()
        dashboard_service._invalidate_data_caches()

    def tearDown(self):
        dashboard_service._invalidate_data_caches()
        database.DATABASE_PATH = self.original_database_path
        database.UPLOADS_DIR = self.original_uploads_dir
        scraper_importer.SCRAPER_FOLDER = self.original_scraper_folder
        self.temp_dir.cleanup()

    def _write_batch(self, run_date, *, include_incremental_old=False):
        admissions = [
            ["OLD-1", "U-OLD", "2026-08-01", 100],
            ["NEW-1", "U-NEW", "2026-10-05", 200],
        ]
        if include_incremental_old:
            admissions.append(["OLD-2", "U-OLD-2", "2026-08-01", 150])
        discharge = [
            ["OLD-1", "U-OLD", "2026-08-01", "2026-08-05", 100],
            ["NEW-1", "U-NEW", "2026-10-05", "2026-10-10", 200],
        ]
        service = [
            ["U-OLD", "Consultation", "2026-08-01", 50],
            ["U-NEW", "Consultation", "2026-10-05", 75],
        ]
        reports = {
            "Admission": make_workbook(
                ["File Id", "UHID", "Doa", "Service Final Amt"], admissions
            ),
            "Discharge": make_workbook(
                ["File Id", "UHID", "Doa", "Dod", "Service Final Amt"],
                discharge,
            ),
            "Service": make_workbook(
                ["UHID", "Service Name", "Service Date", "Service Final Amt"],
                service,
            ),
        }
        for index, (kind, content) in enumerate(reports.items()):
            file_path = scraper_importer.SCRAPER_FOLDER / (
                f"{kind}_{run_date}_10-3{index}-00.xlsx"
            )
            file_path.write_bytes(content)

    def test_import_replaces_old_scraper_data_with_latest_complete_batch(self):
        manual_report = make_workbook(
            ["File Id", "UHID", "Doa", "Service Final Amt"],
            [["MANUAL-1", "U-MANUAL", "2026-10-24", 30]],
        )
        dashboard_service.upload_files(
            [("manual-admissions.xlsx", manual_report)],
            hospital_name="NIMMS Hospital",
        )
        self._write_batch("2026-10-22", include_incremental_old=True)
        scraper_importer.import_scraper_folder()

        previous_archive = pd.DataFrame([{
            "Type": "Admission",
            "DOA": "2025-12-01",
            "UHID": "ARCHIVED-OLD",
            "Service Final Amount": 500,
        }])
        database.commit_scraper_archive_snapshot(
            "NIMMS Hospital",
            pd.DataFrame(),
            "previous-scraper-hash",
            {"2025-12-01": previous_archive},
            as_of_date="2026-10-20",
            cutoff_date="2026-09-19",
            source_files=["previous.xlsx"],
        )
        previous_archive_path = next(
            (database.UPLOADS_DIR / "scraper_archive").rglob("*.parquet")
        )
        self._write_batch("2026-10-23", include_incremental_old=True)
        self._write_batch("2026-10-24")
        latest_files = {
            item["file_name"]: item["path"].read_bytes()
            for item in scraper_importer._latest_complete_batch(
                scraper_importer._report_files(scraper_importer.SCRAPER_FOLDER)
            )
        }
        status = scraper_importer.get_scraper_status()

        self.assertTrue(status["ready"])
        self.assertEqual(status["candidateDate"], "2026-10-24")
        self.assertEqual(len(status["candidateFiles"]), 3)

        imported = scraper_importer.import_scraper_folder()
        dataset_id = database.raw_dataset_id("NIMMS Hospital")
        active = database.load_hospital_data(
            dataset_id=dataset_id,
            source_file="Combined raw reports",
            include_archives=False,
        )
        complete = database.load_hospital_data(
            dataset_id=dataset_id,
            source_file="Combined raw reports",
        )
        all_hospitals = database.load_all_hospital_data()
        comparison_dates = database.load_comparison_date_data()

        self.assertEqual(imported["sourceFilesProcessed"], 3)
        self.assertEqual(imported["sourceFilesRemoved"], 6)
        self.assertEqual(imported["archived_rows"], 0)
        self.assertEqual(imported["rawReportsPruned"], 3)
        self.assertEqual(len(complete), 7)
        self.assertEqual(len(active), 7)
        self.assertNotIn("U-OLD-2", set(complete["UHID"]))
        self.assertIn("U-MANUAL", set(complete["UHID"]))
        self.assertNotIn("ARCHIVED-OLD", set(all_hospitals["UHID"]))
        self.assertTrue(
            complete["DOA"].fillna("").astype(str).str.contains("2026-08-01").any()
        )
        self.assertIn("NIMMS Hospital", set(all_hospitals["hospital_name"]))
        self.assertIn(
            "2026-08-01",
            set(backend.comparison_event_dates(comparison_dates).dt.strftime("%Y-%m-%d")),
        )
        self.assertNotIn(
            "2025-12-01",
            set(backend.comparison_event_dates(comparison_dates).dt.strftime("%Y-%m-%d")),
        )
        saved = next(
            item for item in database.list_saved_datasets()
            if item["dataset_id"] == dataset_id
        )
        self.assertEqual(saved["record_count"], len(complete))
        self.assertEqual(len(database.list_report_uploads("NIMMS Hospital")), 4)
        self.assertEqual(list(scraper_importer.SCRAPER_FOLDER.glob("*.xlsx")), [])
        self.assertEqual(database.list_scraper_archive_days("NIMMS Hospital"), [])
        self.assertFalse(previous_archive_path.exists())

        for file_name, content in latest_files.items():
            (scraper_importer.SCRAPER_FOLDER / file_name).write_bytes(content)
        repeated_import = scraper_importer.import_scraper_folder()
        repeated_data = database.load_hospital_data(
            dataset_id=dataset_id,
            source_file="Combined raw reports",
        )

        self.assertEqual(repeated_import["active_rows"], 7)
        self.assertEqual(len(repeated_data), 7)
        self.assertEqual(len(database.list_report_uploads("NIMMS Hospital")), 4)
        self.assertEqual(list(scraper_importer.SCRAPER_FOLDER.glob("*.xlsx")), [])

    def test_incomplete_batch_is_rejected_without_deleting_source_files(self):
        self._write_batch("2026-10-24")
        service_path = next(scraper_importer.SCRAPER_FOLDER.glob("Service_*.xlsx"))
        service_path.unlink()
        remaining_files = list(scraper_importer.SCRAPER_FOLDER.glob("*.xlsx"))

        with self.assertRaisesRegex(ValueError, "complete Admission"):
            scraper_importer.import_scraper_folder()

        self.assertEqual(
            set(scraper_importer.SCRAPER_FOLDER.glob("*.xlsx")),
            set(remaining_files),
        )

    def test_archive_snapshot_merges_new_rows_for_an_existing_day(self):
        first_day = pd.DataFrame([{
            "Type": "Admission",
            "DOA": "2026-08-01",
            "UHID": "U-OLD-1",
            "Service Final Amount": 100,
        }])
        database.commit_scraper_archive_snapshot(
            "NIMMS Hospital",
            pd.DataFrame(),
            "first-hash",
            {"2026-08-01": first_day},
            as_of_date="2026-10-24",
            cutoff_date="2026-09-23",
            source_files=["first.xlsx"],
        )
        added_day = pd.DataFrame([{
            "Type": "Admission",
            "DOA": "2026-08-01",
            "UHID": "U-OLD-2",
            "Service Final Amount": 150,
        }])
        database.commit_scraper_archive_snapshot(
            "NIMMS Hospital",
            pd.DataFrame(),
            "second-hash",
            {"2026-08-01": added_day},
            as_of_date="2026-10-25",
            cutoff_date="2026-09-24",
            source_files=["second.xlsx"],
        )

        archived = database.load_hospital_data(
            hospital_name="NIMMS Hospital",
            source_file="Combined raw reports",
        )
        self.assertEqual(set(archived["UHID"]), {"U-OLD-1", "U-OLD-2"})

    def test_deleting_archive_dataset_removes_archive_files_and_state(self):
        old_row = pd.DataFrame([{
            "Type": "Admission",
            "DOA": "2026-08-01",
            "UHID": "U-OLD",
            "Service Final Amount": 100,
        }])
        result = database.commit_scraper_archive_snapshot(
            "NIMMS Hospital",
            pd.DataFrame(),
            "archive-hash",
            {"2026-08-01": old_row},
            as_of_date="2026-10-24",
            cutoff_date="2026-09-23",
            source_files=["report.xlsx"],
        )
        archive_path = next(
            (database.UPLOADS_DIR / "scraper_archive").rglob("*.parquet")
        )

        self.assertEqual(database.delete_dataset(result["dataset_id"]), 1)

        self.assertFalse(archive_path.exists())
        self.assertIsNone(database.get_scraper_import_state("NIMMS Hospital"))
        self.assertEqual(database.list_scraper_archive_days("NIMMS Hospital"), [])

    def test_scraper_endpoints_are_available_for_manage_data(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=10
            )
            connection.request("GET", "/api/scraper/status")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["hospital"], "NIMMS Hospital")
            self.assertTrue(payload["folderExists"])
            self.assertFalse(payload["ready"])
            self._write_batch("2026-10-24")
            connection.request("POST", "/api/scraper/import")
            import_response = connection.getresponse()
            import_payload = json.loads(
                import_response.read().decode("utf-8")
            )
            self.assertEqual(import_response.status, 200)
            self.assertTrue(import_payload["success"])
            self.assertEqual(
                list(scraper_importer.SCRAPER_FOLDER.glob("*.xlsx")), []
            )
            connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
