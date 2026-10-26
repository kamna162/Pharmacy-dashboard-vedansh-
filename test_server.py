import json
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from datetime import date
from pathlib import Path
from http.server import ThreadingHTTPServer
from io import BytesIO

from openpyxl import Workbook
import server


class PharmacyDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        server.DB_PATH = Path(self.temp_dir.name) / "test.db"
        server.init_db()

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_reads_csv_and_suggests_common_columns(self):
        content = (
            "Bill Date,Medicine Name,Qty,Net Amount,Purchase Rate\n"
            "2025-09-18,Medicine A,2,150,50\n"
        ).encode("utf-8")
        headers, rows = server.read_rows("report.csv", content)
        self.assertEqual(headers, ["Bill Date", "Medicine Name", "Qty", "Net Amount", "Purchase Rate"])
        self.assertEqual(rows[0]["Medicine Name"], "Medicine A")
        suggestions = server.suggested_mapping(headers)
        self.assertEqual(suggestions["date"], "Bill Date")
        self.assertEqual(suggestions["product"], "Medicine Name")
        self.assertEqual(suggestions["quantity"], "Qty")
        self.assertEqual(suggestions["amount"], "Net Amount")
        self.assertEqual(suggestions["unit_cost"], "Purchase Rate")

    def test_suggests_final_amount_column(self):
        suggestions = server.suggested_mapping(["Bill Date", "FINAL AMT"])
        self.assertEqual(suggestions["date"], "Bill Date")
        self.assertEqual(suggestions["amount"], "FINAL AMT")

    def test_suggests_supplier_column_and_groups_filtered_purchase_totals(self):
        content = (
            "Bill Date,Supplier Name,Final Amt\n"
            "2026-09-01,Alpha Supplies,120\n"
            "2026-09-02,Beta Medical,80\n"
        ).encode("utf-8")
        headers, _ = server.read_rows("Purchase.csv", content)
        mapping = server.suggested_mapping(headers)
        self.assertEqual(mapping["supplier"], "Supplier Name")
        report = server.prepare_import("Purchase.csv", content, "purchase", mapping)
        self.assertEqual(report["supplier_by_row"][2], "Alpha Supplies")
        with server.connect() as connection:
            upload_id = connection.execute(
                """INSERT INTO uploads
                   (hospital, pharmacy, report_type, filename, sha256, row_count,
                    source_file, supplier_data_loaded)
                   VALUES ('Surya Hospital', 'IPD', 'purchase', 'Purchase.csv', 'suppliers', 2, ?, 1)""",
                (content,),
            ).lastrowid
            connection.executemany(
                """INSERT INTO transactions
                   (upload_id, row_number, txn_date, amount, supplier)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (upload_id, row[0], row[1], row[4], report["supplier_by_row"][row[0]])
                    for row in report["prepared"]
                ],
            )
        summary = server.aggregate("2026-09-01", "2026-09-30", "Surya Hospital", "IPD")
        self.assertEqual(
            [(row["supplier"], row["amount"]) for row in summary["supplier_totals"]],
            [("Alpha Supplies", 120), ("Beta Medical", 80)],
        )

    def test_backfills_supplier_for_existing_purchase_upload(self):
        content = (
            "Bill Date,Vendor Name,Final Amt\n"
            "2026-09-01,Legacy Supplier,45\n"
        ).encode("utf-8")
        with server.connect() as connection:
            upload_id = connection.execute(
                """INSERT INTO uploads
                   (hospital, pharmacy, report_type, filename, sha256, row_count, source_file)
                   VALUES ('Surya Hospital', 'IPD', 'purchase', 'Purchase.csv', 'legacy-purchase', 1, ?)""",
                (content,),
            ).lastrowid
            connection.execute(
                """INSERT INTO transactions (upload_id, row_number, txn_date, amount)
                   VALUES (?, 2, '2026-09-01', 45)""",
                (upload_id,),
            )
        server.backfill_supplier_data()
        summary = server.aggregate("2026-09-01", "2026-09-30", "all", "all")
        self.assertEqual(summary["supplier_totals"][0]["supplier"], "Legacy Supplier")
        self.assertEqual(summary["supplier_totals"][0]["amount"], 45)

    def test_imports_cash_and_credit_due_and_sums_all_hospitals(self):
        content = (
            "Bill Date,Net Amount,Cash Due,Credit Due\n"
            "2026-09-01,100,40,60\n"
        ).encode("utf-8")
        headers, _ = server.read_rows("Sales Revenue.csv", content)
        mapping = server.suggested_mapping(headers)
        self.assertEqual(mapping["cash_due"], "Cash Due")
        self.assertEqual(mapping["credit_due"], "Credit Due")
        report = server.prepare_import("Sales Revenue.csv", content, "sales", mapping)
        self.assertEqual(report["due_by_row"][2], (40, 60))

        with server.connect() as connection:
            for hospital, pharmacy, cash_due, credit_due in (
                ("Surya Hospital", "IPD", 40, 60),
                ("NIMMS Hospital (Haldoni)", "Pharmacy", 20, 80),
                ("Vedansh Hospital (Dadri)", "Pharmacy", 10, 90),
            ):
                upload_id = connection.execute(
                    """INSERT INTO uploads (hospital, pharmacy, report_type, filename, sha256, row_count)
                       VALUES (?, ?, 'sales', ?, ?, 1)""",
                    (hospital, pharmacy, hospital + ".csv", hospital + "-hash"),
                ).lastrowid
                connection.execute(
                    """INSERT INTO transactions
                       (upload_id, row_number, txn_date, amount, cash_due, credit_due)
                       VALUES (?, 2, '2026-09-01', 100, ?, ?)""",
                    (upload_id, cash_due, credit_due),
                )

        summary = server.aggregate("2026-09-01", "2026-09-30", "Surya Hospital", "IPD")
        self.assertEqual(summary["due_totals"]["cash_due"]["amount"], 40)
        self.assertEqual(summary["due_totals"]["credit_due"]["amount"], 60)
        all_hospitals = server.aggregate("2026-09-01", "2026-09-30", "all", "all")
        self.assertEqual(all_hospitals["due_totals"]["cash_due"]["amount"], 70)
        self.assertEqual(all_hospitals["due_totals"]["credit_due"]["amount"], 230)
        self.assertEqual(summary["facilities"][0]["cash_due"], 40)
        self.assertEqual(summary["facilities"][0]["credit_due"], 60)
        self.assertEqual(summary["facilities"][0]["sales_rows"], 1)
        self.assertEqual(summary["facilities"][0]["cash_due_rows"], 1)
        self.assertEqual(summary["facilities"][0]["credit_due_rows"], 1)

    def test_hospital_monthly_trend_groups_all_three_report_types(self):
        records = (
            ("Surya Hospital", "IPD", "sales", "2026-01-05", 100),
            ("Surya Hospital", "OPD", "sales", "2026-01-20", 150),
            ("Surya Hospital", "IPD", "collection", "2026-01-08", 80),
            ("Surya Hospital", "OPD", "purchase", "2026-01-12", 60),
            ("NIMMS Hospital (Haldoni)", "Pharmacy", "sales", "2026-01-09", 200),
            ("NIMMS Hospital (Haldoni)", "Pharmacy", "collection", "2026-02-03", 90),
            ("NIMMS Hospital (Haldoni)", "Pharmacy", "purchase", "2026-01-15", 75),
        )
        with server.connect() as connection:
            for index, (hospital, pharmacy, report_type, txn_date, amount) in enumerate(records):
                upload_id = connection.execute(
                    """INSERT INTO uploads
                       (hospital, pharmacy, report_type, filename, sha256, row_count)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (hospital, pharmacy, report_type, "report-{}.csv".format(index),
                     "monthly-{}".format(index)),
                ).lastrowid
                connection.execute(
                    """INSERT INTO transactions (upload_id, row_number, txn_date, amount)
                       VALUES (?, 2, ?, ?)""",
                    (upload_id, txn_date, amount),
                )

        trend = server.hospital_monthly_trend("2026-01-01", "2026-02-28")
        self.assertEqual(trend, [
            {
                "month": "2026-01", "hospital": "NIMMS Hospital (Haldoni)",
                "sales": 200, "collection": 0, "purchase": 75,
                "sales_rows": 1, "collection_rows": 0, "purchase_rows": 1,
            },
            {
                "month": "2026-01", "hospital": "Surya Hospital",
                "sales": 250, "collection": 80, "purchase": 60,
                "sales_rows": 2, "collection_rows": 1, "purchase_rows": 1,
            },
            {
                "month": "2026-02", "hospital": "NIMMS Hospital (Haldoni)",
                "sales": 0, "collection": 90, "purchase": 0,
                "sales_rows": 0, "collection_rows": 1, "purchase_rows": 0,
            },
        ])

    def test_backfills_due_columns_from_existing_sales_upload(self):
        content = (
            "Bill Date,Net Amount,Cash Due,Credit Due\n"
            "2026-09-01,100,35,65\n"
        ).encode("utf-8")
        with server.connect() as connection:
            upload_id = connection.execute(
                """INSERT INTO uploads
                   (hospital, pharmacy, report_type, filename, sha256, row_count, source_file)
                   VALUES ('Surya Hospital', 'IPD', 'sales', 'sales.csv', 'legacy-sales', 1, ?)""",
                (content,),
            ).lastrowid
            connection.execute(
                """INSERT INTO transactions (upload_id, row_number, txn_date, amount)
                   VALUES (?, 2, '2026-09-01', 100)""",
                (upload_id,),
            )

        server.backfill_due_data()
        summary = server.aggregate("2026-09-01", "2026-09-30", "all", "all")
        self.assertEqual(summary["due_totals"]["cash_due"]["amount"], 35)
        self.assertEqual(summary["due_totals"]["credit_due"]["amount"], 65)

    def test_prefers_bill_date_and_final_amt_and_sums_valid_rows_by_date(self):
        headers = ["Date", "Bill Date", "Amount", "Final Amt"]
        suggestions = server.suggested_mapping(headers)
        self.assertEqual(suggestions["date"], "Bill Date")
        self.assertEqual(suggestions["amount"], "Final Amt")

        content = (
            "Date,Bill Date,Amount,Final Amt\n"
            "ignored,2026-09-01,999,100\n"
            "ignored,2026-09-01,777,50\n"
            "ignored,0,100,25\n"
            "ignored,2026-09-02,500,invalid\n"
        ).encode("utf-8")
        report = server.prepare_import(
            "Sales Report.csv",
            content,
            "sales",
            {"date": suggestions["date"], "amount": suggestions["amount"]},
        )
        self.assertEqual(len(report["prepared"]), 2)
        self.assertEqual(report["skipped_count"], 2)
        self.assertEqual(
            sum(row[4] for row in report["prepared"] if row[1] == "2026-09-01"),
            150,
        )
        with server.connect() as connection:
            upload_id = connection.execute(
                """INSERT INTO uploads
                   (hospital, pharmacy, report_type, filename, sha256, row_count, source_file, skipped_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "Surya Hospital", "IPD", "sales", report["filename"],
                    report["digest"], len(report["prepared"]), content, report["skipped_count"],
                ),
            ).lastrowid
            connection.executemany(
                """INSERT INTO transactions
                   (upload_id, row_number, txn_date, product, quantity, amount, unit_cost, reference)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [(upload_id, *row) for row in report["prepared"]],
            )
        monthly_totals = server.aggregate("2026-09-01", "2026-09-30", "Surya Hospital", "IPD")
        self.assertEqual(monthly_totals["totals"]["sales"]["amount"], 150)
        monthly_trend = server.trend("2026-09-01", "2026-09-30", "Surya Hospital", "IPD")
        self.assertEqual(monthly_trend, [{
            "month": "2026-09",
            "sales": 150,
            "collection": 0,
            "purchase": 0,
            "cash_due": 0,
            "credit_due": 0,
            "cash_due_rows": 0,
            "credit_due_rows": 0,
            "sales_rows": 2,
        }])
        with server.connect() as connection:
            connection.execute(
                """INSERT INTO transactions (upload_id, row_number, txn_date, product, quantity, amount)
                   VALUES (?, 10, '2026-09-20', 'Medicine B', 1, 25)""",
                (upload_id,),
            )
            connection.execute(
                """INSERT INTO transactions (upload_id, row_number, txn_date, product, quantity, amount)
                   VALUES (?, 11, '2026-10-01', 'Medicine C', 1, 40)""",
                (upload_id,),
            )
        grouped_months = server.trend("2026-09-01", "2026-10-31", "Surya Hospital", "IPD")
        self.assertEqual(grouped_months, [
            {
                "month": "2026-09", "sales": 175, "collection": 0, "purchase": 0,
                "cash_due": 0, "credit_due": 0, "cash_due_rows": 0, "credit_due_rows": 0,
                "sales_rows": 3,
            },
            {
                "month": "2026-10", "sales": 40, "collection": 0, "purchase": 0,
                "cash_due": 0, "credit_due": 0, "cash_due_rows": 0, "credit_due_rows": 0,
                "sales_rows": 1,
            },
        ])

    def test_reads_xlsx_date_cells(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Invoice Date", "Item", "Qty", "Net Amount"])
        sheet.append([date(2025, 9, 29), "Medicine B", 3, 450])
        content = BytesIO()
        workbook.save(content)

        headers, rows = server.read_rows("purchase.xlsx", content.getvalue())
        self.assertEqual(rows[0]["Invoice Date"], "2025-09-29")
        self.assertEqual(server.suggested_mapping(headers)["date"], "Invoice Date")

    def test_parses_dates_and_indian_currency_values(self):
        self.assertEqual(server.parse_date("29/09/2025"), "2025-09-29")
        self.assertEqual(server.parse_date("2025-09-29"), "2025-09-29")
        self.assertEqual(server.parse_date("2025-09-29T10:30:00"), "2025-09-29")
        self.assertEqual(server.parse_date("2026/09/27 23:05:11"), "2026-09-27")
        self.assertEqual(server.parse_date("2026/09/27 23:05:11.123"), "2026-09-27")
        self.assertEqual(server.parse_date("27/09/2026 23:05:11"), "2026-09-27")
        for value in (
            "29-09-2025",
            "29.09.2025",
            "2025/09/29",
            "29-Sep-2025",
            "29 September 2025",
            "Sep 29, 2025",
        ):
            with self.subTest(value=value):
                self.assertEqual(server.parse_date(value), "2025-09-29")
        self.assertEqual(server.parse_date("45929"), "2025-09-29")
        self.assertEqual(server.parse_number("₹1,25,000.50"), 125000.5)
        self.assertEqual(server.parse_number("(1,250.00)"), -1250.0)
        self.assertIsNone(server.parse_number("invalid amount"))

    def test_aggregates_by_report_and_hospital_pharmacy(self):
        with server.connect() as connection:
            upload_id = connection.execute(
                """INSERT INTO uploads (hospital, pharmacy, report_type, filename, sha256, row_count)
                   VALUES ('Surya Hospital', 'IPD', 'sales', 'sales.csv', 'sales-hash', 1)"""
            ).lastrowid
            connection.execute(
                """INSERT INTO transactions (upload_id, row_number, txn_date, product, quantity, amount)
                   VALUES (?, 2, '2025-09-29', 'Medicine A', 2, 500)""",
                (upload_id,),
            )
            upload_id = connection.execute(
                """INSERT INTO uploads (hospital, pharmacy, report_type, filename, sha256, row_count)
                   VALUES ('Surya Hospital', 'IPD', 'collection', 'collection.csv', 'collection-hash', 1)"""
            ).lastrowid
            connection.execute(
                """INSERT INTO transactions (upload_id, row_number, txn_date, amount)
                   VALUES (?, 2, '2025-09-29', 300)""",
                (upload_id,),
            )

        summary = server.aggregate("2025-01-01", "2025-09-29", "all", "all")
        self.assertEqual(summary["totals"]["sales"]["amount"], 500)
        self.assertEqual(summary["totals"]["collection"]["amount"], 300)
        self.assertEqual(summary["totals"]["purchase"]["rows"], 0)
        self.assertEqual(summary["facilities"][0]["hospital"], "Surya Hospital")
        self.assertEqual(summary["facilities"][0]["pharmacy"], "IPD")

    def test_import_rejects_duplicate_file_and_upload_can_be_deleted(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        boundary = "test-boundary"
        csv_content = b"Sale Date,Medicine,Qty,Net Sale\n2025-09-29,Medicine A,2,125.50\n"
        mapping = json.dumps({
            "date": "Sale Date",
            "amount": "Net Sale",
            "product": "Medicine",
            "quantity": "Qty",
        })
        parts = [
            (
                'Content-Disposition: form-data; name="file"; filename="sales.csv"\r\n'
                "Content-Type: text/csv\r\n\r\n"
            ).encode() + csv_content,
            b'Content-Disposition: form-data; name="hospital"\r\n\r\nSurya Hospital',
            b'Content-Disposition: form-data; name="pharmacy"\r\n\r\nIPD',
            b'Content-Disposition: form-data; name="report_type"\r\n\r\nsales',
            (
                'Content-Disposition: form-data; name="mapping"\r\n\r\n' + mapping
            ).encode(),
        ]
        body = b"".join(b"--" + boundary.encode() + b"\r\n" + part + b"\r\n" for part in parts)
        body += b"--" + boundary.encode() + b"--\r\n"
        request = urllib.request.Request(
            "http://127.0.0.1:{}/api/import".format(httpd.server_port),
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        )
        try:
            with urllib.request.urlopen(request) as response:
                imported = json.load(response)
            self.assertEqual(imported["row_count"], 1)
            summary = server.aggregate("2025-01-01", "2025-12-31", "Surya Hospital", "IPD")
            self.assertEqual(summary["totals"]["sales"]["amount"], 125.5)

            with self.assertRaises(urllib.error.HTTPError) as duplicate:
                urllib.request.urlopen(request)
            self.assertEqual(duplicate.exception.code, 400)
            self.assertIn("pehle upload", duplicate.exception.read().decode("utf-8"))

            uploads = urllib.request.urlopen(
                "http://127.0.0.1:{}/api/uploads".format(httpd.server_port)
            )
            upload_id = json.load(uploads)[0]["id"]
            deletion = urllib.request.Request(
                "http://127.0.0.1:{}/api/uploads/{}".format(httpd.server_port, upload_id),
                method="DELETE",
            )
            with urllib.request.urlopen(deletion) as response:
                self.assertEqual(json.load(response)["message"], "Upload aur uske imported rows delete ho gaye.")
            summary = server.aggregate("2025-01-01", "2025-12-31", "Surya Hospital", "IPD")
            self.assertEqual(summary["totals"]["sales"]["rows"], 0)
            with urllib.request.urlopen(request) as response:
                replacement = json.load(response)
            self.assertEqual(replacement["filename"], "sales.csv")
            summary = server.aggregate("2025-01-01", "2025-12-31", "Surya Hospital", "IPD")
            self.assertEqual(summary["totals"]["sales"]["amount"], 125.5)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_hospital_pharmacies_and_legacy_record_compatibility(self):
        self.assertEqual(server.HOSPITAL_PHARMACIES["Surya Hospital"], ("IPD", "OPD"))
        self.assertEqual(server.HOSPITAL_PHARMACIES["NIMMS Hospital (Haldoni)"], ("Pharmacy",))
        self.assertEqual(server.HOSPITAL_PHARMACIES["Vedansh Hospital (Dadri)"], ("Pharmacy",))
        with server.connect() as connection:
            upload_id = connection.execute(
                """INSERT INTO uploads
                   (hospital, pharmacy, report_type, filename, sha256, row_count)
                   VALUES ('Hospital 2', 'IPD', 'sales', 'legacy.csv', 'legacy-hash', 1)"""
            ).lastrowid
            connection.execute(
                """INSERT INTO transactions (upload_id, row_number, txn_date, amount)
                   VALUES (?, 2, '2025-09-29', 150)""",
                (upload_id,),
            )
        server.init_db()
        with server.connect() as connection:
            stored = connection.execute(
                "SELECT hospital, pharmacy FROM uploads WHERE sha256 = 'legacy-hash'"
            ).fetchone()
        self.assertEqual(stored["hospital"], "Hospital 2")
        self.assertEqual(stored["pharmacy"], "IPD")
        summary = server.aggregate(
            "2025-01-01", "2025-12-31", "NIMMS Hospital (Haldoni)", "Pharmacy"
        )
        self.assertEqual(summary["facilities"][0]["hospital"], "NIMMS Hospital (Haldoni)")
        self.assertEqual(summary["facilities"][0]["pharmacy"], "Pharmacy")
        self.assertEqual(summary["totals"]["sales"]["amount"], 150)
        with self.assertRaises(ValueError):
            server.selected_filters({
                "hospital": ["NIMMS Hospital (Haldoni)"],
                "pharmacy": ["IPD"],
            })

    def test_batch_import_archives_originals_and_rolls_back_on_duplicate(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base_url = "http://127.0.0.1:{}".format(httpd.server_port)

        def post_batch(file_names, reports, hospital="Surya Hospital"):
            boundary = "batch-boundary"
            parts = []
            for filename, content in file_names:
                parts.append(
                    (
                        'Content-Disposition: form-data; name="file"; filename="{}"\r\n'
                        "Content-Type: text/csv\r\n\r\n".format(filename)
                    ).encode() + content
                )
            parts.extend([
                b'Content-Disposition: form-data; name="hospital"\r\n\r\n' + hospital.encode(),
                (
                    'Content-Disposition: form-data; name="reports"\r\n\r\n'
                    + json.dumps(reports)
                ).encode(),
            ])
            body = b"".join(
                b"--" + boundary.encode() + b"\r\n" + part + b"\r\n" for part in parts
            )
            body += b"--" + boundary.encode() + b"--\r\n"
            request = urllib.request.Request(
                base_url + "/api/batch-import",
                data=body,
                headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
            )
            return urllib.request.urlopen(request)

        files = [
            ("sales.csv", b"Date,Amount\n2025-09-29,100\n2025-09-29,25\n0,5\n"),
            ("collection.csv", b"Date,Amount\n2025-09-29,80\n"),
            ("purchase.csv", b"Date,Amount\n2025-09-29,50\n"),
        ]
        reports = [
            {"pharmacy": "IPD", "report_type": kind, "mapping": {"date": "Date", "amount": "Amount"}}
            for kind in ("sales", "collection", "purchase")
        ]
        try:
            with post_batch(files, reports) as response:
                result = json.load(response)
            self.assertEqual(result["file_count"], 3)
            self.assertEqual(result["row_count"], 4)
            self.assertEqual(result["skipped_count"], 1)

            with urllib.request.urlopen(base_url + "/api/uploads?hospital=Surya%20Hospital") as response:
                uploads = json.load(response)
            self.assertEqual(len(uploads), 3)
            self.assertTrue(all(upload["source_available"] for upload in uploads))
            sales_upload = next(upload for upload in uploads if upload["report_type"] == "sales")
            self.assertEqual(sales_upload["row_count"], 2)
            self.assertEqual(sales_upload["skipped_count"], 1)
            sales_totals = server.aggregate(
                "2025-09-29", "2025-09-29", "Surya Hospital", "IPD"
            )
            self.assertEqual(sales_totals["totals"]["sales"]["amount"], 125)
            stored_file = urllib.request.urlopen(
                base_url + "/api/uploads/{}/file".format(uploads[0]["id"])
            )
            self.assertIn(b"Date,Amount", stored_file.read())

            repeated_type_reports = [dict(report) for report in reports]
            repeated_type_reports[2]["report_type"] = "sales"
            with self.assertRaises(urllib.error.HTTPError) as repeated_type:
                post_batch(
                    [("new-sales.csv", b"Date,Amount\n2025-09-28,25\n"), *files[1:]],
                    repeated_type_reports,
                )
            self.assertEqual(repeated_type.exception.code, 400)

            mixed_pharmacy_reports = [dict(report) for report in reports]
            mixed_pharmacy_reports[1]["pharmacy"] = "OPD"
            with self.assertRaises(urllib.error.HTTPError) as mixed_pharmacy:
                post_batch(
                    [("new-sales.csv", b"Date,Amount\n2025-09-28,25\n"), *files[1:]],
                    mixed_pharmacy_reports,
                )
            self.assertEqual(mixed_pharmacy.exception.code, 400)

            with self.assertRaises(urllib.error.HTTPError) as duplicate:
                post_batch(
                    [("new.csv", b"Date,Amount\n2025-09-28,25\n"), *files[1:]],
                    reports,
                )
            self.assertEqual(duplicate.exception.code, 400)
            with urllib.request.urlopen(base_url + "/api/uploads") as response:
                self.assertEqual(len(json.load(response)), 3)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_single_report_import_updates_only_its_kpi(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        boundary = "single-report-boundary"
        content = (
            b"Bill Date,Amount,Final Amt\n"
            b"2026-09-15,900,125\n"
            b"2026-09-15,800,75\n"
            b"0,300,50\n"
        )
        report = {
            "pharmacy": "Pharmacy",
            "report_type": "sales",
            "mapping": server.suggested_mapping(["Bill Date", "Amount", "Final Amt"]),
        }
        parts = [
            b'Content-Disposition: form-data; name="file"; filename="Sales Report.csv"\r\n'
            b"Content-Type: text/csv\r\n\r\n" + content,
            b'Content-Disposition: form-data; name="hospital"\r\n\r\nNIMMS Hospital (Haldoni)',
            (
                'Content-Disposition: form-data; name="reports"\r\n\r\n'
                + json.dumps([report])
            ).encode(),
        ]
        body = b"".join(
            b"--" + boundary.encode() + b"\r\n" + part + b"\r\n" for part in parts
        )
        body += b"--" + boundary.encode() + b"--\r\n"
        request = urllib.request.Request(
            "http://127.0.0.1:{}/api/batch-import".format(httpd.server_port),
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        )
        try:
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertEqual(result["file_count"], 1)
            self.assertEqual(result["row_count"], 2)
            self.assertEqual(result["skipped_count"], 1)

            summary = server.aggregate(
                "2026-09-15", "2026-09-15", "NIMMS Hospital (Haldoni)", "Pharmacy"
            )
            self.assertEqual(summary["totals"]["sales"]["amount"], 200)
            self.assertEqual(summary["totals"]["sales"]["rows"], 2)
            self.assertEqual(summary["totals"]["collection"]["rows"], 0)
            self.assertEqual(summary["totals"]["purchase"]["rows"], 0)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_summary_facility_comparison_respects_hospital_and_shows_all_its_pharmacies(self):
        with server.connect() as connection:
            for hospital, pharmacy, amount in (
                ("Surya Hospital", "IPD", 100),
                ("Surya Hospital", "OPD", 200),
                ("NIMMS Hospital (Haldoni)", "Pharmacy", 300),
                ("Vedansh Hospital (Dadri)", "Pharmacy", 400),
            ):
                upload_id = connection.execute(
                    """INSERT INTO uploads
                       (hospital, pharmacy, report_type, filename, sha256, row_count)
                       VALUES (?, ?, 'sales', ?, ?, 1)""",
                    (hospital, pharmacy, "{}.csv".format(pharmacy), "{}-hash".format(pharmacy)),
                ).lastrowid
                connection.execute(
                    """INSERT INTO transactions
                       (upload_id, row_number, txn_date, amount)
                       VALUES (?, 2, '2026-01-15', ?)""",
                    (upload_id, amount),
                )
                if hospital == "Vedansh Hospital (Dadri)":
                    connection.execute(
                        """INSERT INTO transactions
                           (upload_id, row_number, txn_date, amount)
                           VALUES (?, 3, '2026-02-15', 900)""",
                        (upload_id,),
                    )
            for hospital, pharmacy, supplier, amount in (
                ("Surya Hospital", "IPD", "Surya Supplier", 1200),
                ("NIMMS Hospital (Haldoni)", "Pharmacy", "NIMMS Supplier", 2300),
                ("Vedansh Hospital (Dadri)", "Pharmacy", "Vedansh Supplier", 3400),
            ):
                upload_id = connection.execute(
                    """INSERT INTO uploads
                       (hospital, pharmacy, report_type, filename, sha256, row_count)
                       VALUES (?, ?, 'purchase', ?, ?, 1)""",
                    (
                        hospital, pharmacy, "{}-purchase.csv".format(pharmacy),
                        "{}-purchase-hash".format(pharmacy),
                    ),
                ).lastrowid
                connection.execute(
                    """INSERT INTO transactions
                       (upload_id, row_number, txn_date, supplier, amount)
                       VALUES (?, 2, '2026-01-15', ?, ?)""",
                    (upload_id, supplier, amount),
                )
            for hospital, pharmacy, amount in (
                ("Surya Hospital", "IPD", 700),
                ("NIMMS Hospital (Haldoni)", "Pharmacy", 800),
                ("Vedansh Hospital (Dadri)", "Pharmacy", 900),
            ):
                upload_id = connection.execute(
                    """INSERT INTO uploads
                       (hospital, pharmacy, report_type, filename, sha256, row_count)
                       VALUES (?, ?, 'collection', ?, ?, 1)""",
                    (
                        hospital, pharmacy, "{}-collection.csv".format(pharmacy),
                        "{}-collection-hash".format(pharmacy),
                    ),
                ).lastrowid
                connection.execute(
                    """INSERT INTO transactions
                       (upload_id, row_number, txn_date, amount)
                       VALUES (?, 2, '2026-01-15', ?)""",
                    (upload_id, amount),
                )
            outside_range_upload = connection.execute(
                """INSERT INTO uploads
                   (hospital, pharmacy, report_type, filename, sha256, row_count)
                   VALUES ('Surya Hospital', 'IPD', 'purchase', 'outside-range.csv',
                           'outside-range-hash', 1)"""
            ).lastrowid
            connection.execute(
                """INSERT INTO transactions
                   (upload_id, row_number, txn_date, supplier, amount)
                   VALUES (?, 2, '2026-02-15', 'February Supplier', 9999)""",
                (outside_range_upload,),
            )
            ipd_upload_id = connection.execute(
                """SELECT id FROM uploads
                   WHERE hospital = 'Surya Hospital' AND pharmacy = 'IPD'
                   AND report_type = 'sales'"""
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO transactions
                   (upload_id, row_number, txn_date, amount)
                   VALUES (?, 3, '2025-01-15', 50)""",
                (ipd_upload_id,),
            )

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            query = (
                "start=2026-01-01&end=2026-01-31"
                "&compare_start=2025-01-01&compare_end=2025-01-31"
                "&hospital=Surya%20Hospital&pharmacy=IPD"
            )
            with urllib.request.urlopen(
                "http://127.0.0.1:{}/api/summary?{}".format(httpd.server_port, query)
            ) as response:
                summary = json.load(response)

            self.assertEqual(summary["current"]["totals"]["sales"]["amount"], 100)
            self.assertEqual(summary["current"]["latest_date"], "2026-01-15")
            self.assertEqual(summary["hospital_monthly"], [
                {
                    "month": "2026-01", "hospital": "NIMMS Hospital (Haldoni)",
                    "sales": 300, "collection": 800, "purchase": 2300,
                    "sales_rows": 1, "collection_rows": 1, "purchase_rows": 1,
                },
                {
                    "month": "2026-01", "hospital": "Surya Hospital",
                    "sales": 300, "collection": 700, "purchase": 1200,
                    "sales_rows": 2, "collection_rows": 1, "purchase_rows": 1,
                },
                {
                    "month": "2026-01", "hospital": "Vedansh Hospital (Dadri)",
                    "sales": 400, "collection": 900, "purchase": 3400,
                    "sales_rows": 1, "collection_rows": 1, "purchase_rows": 1,
                },
            ])
            self.assertEqual(summary["trend_comparison"], [{
                "month": "2025-01",
                "sales": 50,
                "collection": 0,
                "purchase": 0,
                "cash_due": 0,
                "credit_due": 0,
                "cash_due_rows": 0,
                "credit_due_rows": 0,
                "sales_rows": 1,
            }])
            self.assertEqual(
                {
                    (facility["hospital"], facility["pharmacy"], facility["sales"])
                    for facility in summary["facility_comparison"]["current"]
                },
                {
                    ("Surya Hospital", "IPD", 100),
                    ("Surya Hospital", "OPD", 200),
                },
            )
            hospital_facilities = summary["hospital_comparison"]["current"]["facilities"]
            self.assertEqual(
                {facility["hospital"] for facility in hospital_facilities},
                {
                    "NIMMS Hospital (Haldoni)",
                    "Surya Hospital",
                    "Vedansh Hospital (Dadri)",
                },
            )
            self.assertEqual(
                sum(facility["sales"] for facility in hospital_facilities),
                1000,
            )
            self.assertEqual(sum(facility["sales_rows"] for facility in hospital_facilities), 4)
            self.assertEqual(sum(facility["purchase_rows"] for facility in hospital_facilities), 3)
            self.assertEqual(sum(facility["collection"] for facility in hospital_facilities), 2400)
            self.assertEqual(sum(facility["collection_rows"] for facility in hospital_facilities), 3)
            self.assertEqual(
                {
                    (row["hospital"], row["supplier"], row["amount"])
                    for row in summary["hospital_comparison"]["current"]["supplier_totals"]
                },
                {
                    ("Surya Hospital", "Surya Supplier", 1200),
                    ("NIMMS Hospital (Haldoni)", "NIMMS Supplier", 2300),
                    ("Vedansh Hospital (Dadri)", "Vedansh Supplier", 3400),
                },
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_export_data_returns_monthly_and_daily_totals_for_offline_dashboard(self):
        with server.connect() as connection:
            for hospital, pharmacy, report_type, txn_date, amount in (
                ("Surya Hospital", "IPD", "sales", "2026-01-05", 120),
                ("Surya Hospital", "IPD", "sales", "2026-01-28", 80),
                ("Surya Hospital", "IPD", "collection", "2026-01-15", 75),
                ("NIMMS Hospital (Haldoni)", "Pharmacy", "purchase", "2025-12-10", 400),
            ):
                upload_id = connection.execute(
                    """INSERT INTO uploads
                       (hospital, pharmacy, report_type, filename, sha256, row_count)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (
                        hospital, pharmacy, report_type,
                        "{}-{}.csv".format(pharmacy, txn_date),
                        "{}-{}-hash".format(pharmacy, txn_date),
                    ),
                ).lastrowid
                connection.execute(
                    """INSERT INTO transactions (upload_id, row_number, txn_date, amount)
                       VALUES (?, 2, ?, ?)""",
                    (upload_id, txn_date, amount),
                )
            connection.execute(
                """UPDATE transactions SET supplier = 'NIMMS Supplier'
                   WHERE txn_date = '2025-12-10' AND upload_id = (
                     SELECT id FROM uploads WHERE hospital = 'NIMMS Hospital (Haldoni)'
                       AND report_type = 'purchase'
                   )"""
            )

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:{}/api/export-data".format(httpd.server_port)
            ) as response:
                exported = json.load(response)
            self.assertEqual(exported["latest_date"], "2026-01-28")
            self.assertEqual(len(exported["monthly"]), 2)
            self.assertEqual(exported["monthly"][0], {
                "month": "2025-12",
                "hospital": "NIMMS Hospital (Haldoni)",
                "pharmacy": "Pharmacy",
                "sales": 0,
                "collection": 0,
                "purchase": 400,
                "cash_due": 0,
                "credit_due": 0,
                "cash_due_rows": 0,
                "credit_due_rows": 0,
            })
            self.assertEqual(exported["monthly"][1]["month"], "2026-01")
            self.assertEqual(exported["monthly"][1]["sales"], 200)
            self.assertEqual(exported["monthly"][1]["collection"], 75)
            self.assertEqual(exported["pharmacies"]["Surya Hospital"], ["IPD", "OPD"])
            self.assertEqual(len(exported["daily"]), 4)
            self.assertEqual(exported["suppliers"], [{
                "date": "2025-12-10",
                "hospital": "NIMMS Hospital (Haldoni)",
                "pharmacy": "Pharmacy",
                "supplier": "NIMMS Supplier",
                "amount": 400,
            }])
            self.assertEqual(exported["daily"][0], {
                "date": "2025-12-10",
                "hospital": "NIMMS Hospital (Haldoni)",
                "pharmacy": "Pharmacy",
                "sales": 0,
                "collection": 0,
                "purchase": 400,
                "sales_rows": 0,
                "collection_rows": 0,
                "purchase_rows": 1,
                "cash_due": 0,
                "credit_due": 0,
                "cash_due_rows": 0,
                "credit_due_rows": 0,
            })
            january_sales_days = [
                point for point in exported["daily"]
                if point["date"].startswith("2026-01") and point["sales"]
            ]
            self.assertEqual(
                [(point["date"], point["sales"], point["sales_rows"]) for point in january_sales_days],
                [("2026-01-05", 120, 1), ("2026-01-28", 80, 1)],
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_preview_returns_mapping_metadata_without_sample_rows(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        boundary = "preview-boundary"
        csv_content = (
            b"Bill Date,Patient Name,FINAL AMT\n"
            b"2026-03-25,Private Patient,1458\n"
        )
        body = (
            b"--" + boundary.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="file"; filename="sales.csv"\r\n'
            b"Content-Type: text/csv\r\n\r\n" + csv_content +
            b"\r\n--" + boundary.encode() + b"--\r\n"
        )
        request = urllib.request.Request(
            "http://127.0.0.1:{}/api/preview".format(httpd.server_port),
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        )
        try:
            with urllib.request.urlopen(request) as response:
                preview = json.load(response)
            self.assertEqual(preview["suggestions"]["date"], "Bill Date")
            self.assertEqual(preview["suggestions"]["amount"], "FINAL AMT")
            self.assertEqual(preview["row_count"], 1)
            self.assertNotIn("preview", preview)
            self.assertNotIn("Private Patient", json.dumps(preview))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
