import csv
import hashlib
from io import BytesIO, StringIO
import unittest
from unittest.mock import patch

import pandas as pd

from comparison import backend
from comparison.report_combiner import (
    OUTPUT_COLUMNS,
    _parse_date_column,
    combine_reports,
    inspect_reports,
    read_raw_file,
)


def csv_report(headers, rows, title_rows=()):
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    for title in title_rows:
        writer.writerow(title)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def admission_headers():
    return [
        "File Id",
        "UHID",
        "Patient Name",
        "Doctor Name",
        "Dept. Name",
        "Panel Name",
        "Doa",
        "Service Gross Amt",
        "Service Final Amt",
        "Cash Due",
        "Credit Due",
    ]


class TestReportCombiner(unittest.TestCase):
    def test_inspects_incoming_reports_without_combining_rows(self):
        admission = csv_report(
            admission_headers(),
            [["A1", "00123", "A Patient", "Dr A", "Medicine", "Cash", "2026-08-01", "10", "9", "1", "0"]],
        )

        report = inspect_reports([("admissions.csv", admission)])

        self.assertEqual(report[0]["detected_kind"], "Admission")
        self.assertEqual(report[0]["rows_read"], 1)
        self.assertEqual(report[0]["rows_used"], 0)
        self.assertEqual(report[0]["columns"], admission_headers())

    def test_raw_file_cache_reuses_parsed_frame_without_sharing_mutations(self):
        content = csv_report(
            admission_headers(),
            [["CACHE-UNIQUE-1", "00123", "A Patient", "Dr A", "Medicine", "Cash", "2026-08-01", "10", "9", "1", "0"]],
        )
        first = read_raw_file("cached-admissions.csv", content)
        first.iloc[0, 0] = "mutated"

        with patch("comparison.report_combiner._read_raw_file", side_effect=AssertionError):
            cached = read_raw_file("cached-admissions.csv", content)

        self.assertEqual(cached.iloc[0, 0], "CACHE-UNIQUE-1")

    def test_combining_reuses_preparsed_upload_frames(self):
        content = csv_report(
            admission_headers(),
            [["A1", "00123", "A Patient", "Dr A", "Medicine", "Cash", "2026-08-01", "10", "9", "1", "0"]],
        )
        frame = read_raw_file("admissions.csv", content)

        with patch("comparison.report_combiner.read_raw_file", side_effect=AssertionError):
            merged, report = combine_reports(
                [("admissions.csv", content)],
                parsed_frames={hashlib.sha256(content).hexdigest(): frame},
            )

        self.assertEqual(len(merged), 1)
        self.assertEqual(report[0]["detected_kind"], "Admission")

    def test_detects_report_kinds_and_lower_header_rows(self):
        admission = csv_report(
            admission_headers(),
            [["A1", "00123", "A Patient", "Dr A", "Medicine", "Cash", "2026-08-01", "10", "9", "1", "0"]],
            title_rows=(("Hospital monthly report",), ("August export",)),
        )
        discharge_with_both_dates = csv_report(
            ["File Id", "DOA", "DOD"],
            [["D1", "2026-07-30", "2026-08-02"]],
        )
        service = csv_report(
            ["UHID", "Patient Name", "Service Name", "Service Date"],
            [["U1", "A Patient", "Consultation", "2026-08-01"]],
        )

        merged, report = combine_reports([
            ("admissions.csv", admission),
            ("discharge-export.csv", discharge_with_both_dates),
            ("services.csv", service),
        ])

        self.assertEqual(
            [item["detected_kind"] for item in report],
            ["Admission", "Discharge", "Service"],
        )
        self.assertEqual(report[0]["rows_read"], 1)
        self.assertEqual(report[0]["rows_used"], 1)
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged.loc[merged["Type"] == "Admission", "DOA"].iloc[0], "2026-08-01")
        discharge_row = merged.loc[merged["Type"] == "Discharge"].iloc[0]
        self.assertEqual(discharge_row["DOA"], "2026-07-30")
        self.assertEqual(discharge_row["Bed Days"], 3)
        opd_row = merged.loc[merged["Type"] == "OPD"].iloc[0]
        self.assertEqual(opd_row["DOA"], "2026-08-01")
        self.assertEqual(opd_row["DOD"], "2026-08-01")
        self.assertEqual(opd_row["Bed Days"], 0)
        self.assertEqual(opd_row["Cash Due"], 0)

    def test_both_date_columns_use_filename_then_discharge_fill_ratio(self):
        named_admission = csv_report(
            ["File Id", "DOA", "DOD"],
            [["A1", "2026-08-01", "2026-08-04"]],
        )
        discharge_by_fill_ratio = csv_report(
            ["File Id", "DOA", "DOD"],
            [
                ["D1", "2026-07-01", "2026-07-05"],
                ["D2", "2026-07-02", "2026-07-06"],
                ["A2", "2026-07-03", ""],
            ],
        )
        discharge_by_name = csv_report(
            ["File Id", "DOA", "DOD"],
            [["D3", "2026-07-01", ""]],
        )

        _, report = combine_reports([
            ("admission-summary.csv", named_admission),
            ("overlap-window.csv", discharge_by_fill_ratio),
            ("discharge-window.csv", discharge_by_name),
        ])

        self.assertEqual(
            [item["detected_kind"] for item in report],
            ["Admission", "Discharge", "Discharge"],
        )

    def test_joins_visits_sums_bill_parts_and_groups_panels(self):
        admission = csv_report(
            admission_headers(),
            [
                ["F1", "123.0", "Patient One", "Dr A", "Medicine", "Bajaj Allianz General Insurance", "2026-08-01", "100", "90", "5", "10"],
                ["F1", "", "", "", "", "", "", "50", "45", "2", "3"],
                ["F2", "456", "Patient Two", "Dr B", "Surgery", "Cash", "2026-08-02", "20", "18", "4", "0"],
                ["F3", "789", "Patient Three", "Dr C", "Medicine", "Ayushman Bharat", "2026-08-03", "30", "28", "0", "0"],
                ["F4", "101", "Patient Four", "Dr D", "Medicine", "ECHS", "2026-08-04", "40", "38", "0", "0"],
                ["F5", "102", "Patient Five", "Dr E", "Medicine", "CAPF Card", "2026-08-05", "50", "48", "0", "0"],
                ["F6", "103", "Patient Six", "Dr F", "Medicine", "CGHS", "2026-08-06", "60", "58", "0", "0"],
                ["F7", "104", "Patient Seven", "Dr G", "Medicine", "", "2026-08-07", "70", "68", "0", "0"],
                ["", "", "Total", "", "", "", "", "420", "395", "11", "13"],
            ],
        )
        discharge = csv_report(
            ["File Id", "UHID", "Patient Name", "Doctor Name", "Dept. Name", "Panel Name", "DOA", "DOD", "Service Gross Amt", "Service Final Amt", "Cash Due", "Credit Due"],
            [["F1", "123", "Patient One", "Dr A", "Medicine", "Cash", "", "2026-08-05", "200", "180", "15", "20"]],
        )

        merged, report = combine_reports([
            ("admission.csv", admission),
            ("discharge.csv", discharge),
        ])

        admission_row = merged.loc[merged["Type"] == "Admission"].iloc[0]
        discharge_row = merged.loc[merged["Type"] == "Discharge"].iloc[0]
        unpaired_admission = merged.loc[
            (merged["Type"] == "Admission") & (merged["UHID"] == "456")
        ].iloc[0]
        self.assertEqual(admission_row["UHID"], "123")
        self.assertEqual(admission_row["Service Amount"], 150)
        self.assertEqual(admission_row["Service Final Amount"], 135)
        self.assertEqual(admission_row["Cash Due"], 7)
        self.assertEqual(admission_row["Credit Due"], 13)
        self.assertEqual(admission_row["DOD"], "2026-08-05")
        self.assertEqual(admission_row["Bed Days"], 4)
        self.assertEqual(discharge_row["DOA"], "2026-08-01")
        self.assertEqual(discharge_row["Bed Days"], 4)
        self.assertEqual(unpaired_admission["DOD"], "")
        self.assertEqual(unpaired_admission["Bed Days"], "")
        self.assertEqual(admission_row["Panel Name"], "TPA")
        self.assertEqual(
            merged.loc[merged["Type"] == "Admission", "Panel Name"].tolist(),
            ["TPA", "Cash", "Ayushman", "ECHS", "CAPF", "CGHS", "TPA"],
        )
        self.assertEqual(report[0]["rows_read"], 9)
        self.assertEqual(report[0]["rows_used"], 7)
        self.assertEqual(merged.columns.tolist(), OUTPUT_COLUMNS)
        self.assertEqual(merged["S No."].tolist(), list(range(1, len(merged) + 1)))

        self.assertEqual(backend.validate_columns(merged), [])
        cleaned = backend.clean_data(merged)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(cleaned["DOA"]))
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(cleaned["DOD"]))

    def test_newest_overlapping_visit_wins_after_in_file_sums(self):
        older = csv_report(
            admission_headers(),
            [
                ["F1", "U1", "Patient", "Dr A", "Medicine", "Cash", "2026-07-01", "100", "90", "10", "20"],
                ["F1", "", "", "", "", "", "", "25", "20", "2", "3"],
            ],
        )
        newer = csv_report(
            admission_headers(),
            [["F1", "U1", "Patient", "Dr A", "Medicine", "Cash", "2026-08-01", "300", "275", "30", "40"]],
        )

        merged, report = combine_reports([
            ("older.csv", older),
            ("newer.csv", newer),
        ])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged.iloc[0]["DOA"], "2026-08-01")
        self.assertEqual(merged.iloc[0]["Service Amount"], 300)
        self.assertEqual(merged.iloc[0]["Service Final Amount"], 275)
        self.assertEqual([item["rows_used"] for item in report], [0, 1])
        self.assertIn("superseded", report[0]["message"])

    def test_service_duplicates_use_slots_and_ninety_second_window(self):
        headers = [
            "UHID",
            "Patient Name",
            "Doctor Name",
            "Dept. Name",
            "Panel Name",
            "Service Name",
            "Service Type",
            "Service Date",
            "Service Gross Amt",
            "Service Final Amt",
            "DOA",
        ]
        older = csv_report(
            headers,
            [
                ["U1", "Patient", "Dr A", "Medicine", "Cash", "Consultation", "Consultation", "2026-08-01 09:00:00", "100", "90", "2010-01-01"],
                ["U1", "Patient", "Dr A", "Medicine", "Cash", "Consultation", "Consultation", "2026-08-01 09:00:10", "100", "90", "2010-01-01"],
                ["U1", "Patient", "Dr A", "Medicine", "Cash", "Consultation", "Consultation", "2026-08-01 09:00:20", "100", "90", "2010-01-01"],
            ],
        )
        newer = csv_report(
            headers,
            [
                ["U1", "Patient", "Dr A", "Medicine", "Cash", "Consultation", "Consultation", "2026-08-01 09:01:30", "100", "90", "2010-01-01"],
                ["U1", "Patient", "Dr A", "Medicine", "Cash", "Consultation", "Consultation", "2026-08-01 09:01:40", "100", "90", "2010-01-01"],
            ],
        )

        merged, report = combine_reports([
            ("older-services.csv", older),
            ("newer-services.csv", newer),
        ])

        self.assertEqual(merged["Type"].tolist(), ["OPD", "OPD", "OPD"])
        self.assertEqual(merged["DOA"].tolist(), ["2026-08-01"] * 3)
        self.assertEqual(merged["DOD"].tolist(), ["2026-08-01"] * 3)
        self.assertEqual([item["rows_used"] for item in report], [1, 2])
        self.assertEqual(merged["Service Name"].tolist(), ["Consultation"] * 3)

    def test_service_lines_more_than_ninety_seconds_apart_are_not_collapsed(self):
        headers = [
            "UHID",
            "Patient Name",
            "Service Name",
            "Service Type",
            "Service Date",
            "Service Gross Amt",
            "Service Final Amt",
        ]
        older = csv_report(
            headers,
            [["U1", "Patient", "Consultation", "Consultation", "2026-08-01 09:00:00", "100", "90"]],
        )
        newer = csv_report(
            headers,
            [["U1", "Patient", "Consultation", "Consultation", "2026-08-01 09:01:31", "100", "90"]],
        )

        merged, _ = combine_reports([
            ("older-services.csv", older),
            ("newer-services.csv", newer),
        ])

        self.assertEqual(len(merged), 2)

    def test_service_gross_a_headers_and_case_sensitive_signatures(self):
        headers = [
            "UHID",
            "Service Name",
            "Service Type",
            "Service Date",
            "Service Gross A",
            "Service Final A",
        ]
        upper_case = csv_report(
            headers,
            [["U1", "Consultation", "Consultation", "2026-08-01 09:00:00", "100", "90"]],
        )
        lower_case = csv_report(
            headers,
            [["U1", "consultation", "Consultation", "2026-08-01 09:00:10", "100", "90"]],
        )

        merged, _ = combine_reports([
            ("first-services.csv", upper_case),
            ("second-services.csv", lower_case),
        ])

        self.assertEqual(len(merged), 2)
        self.assertCountEqual(
            merged["Service Name"].tolist(), ["Consultation", "consultation"]
        )
        self.assertEqual(merged["Service Amount"].tolist(), [100, 100])

    def test_service_uhid_falls_back_to_patient_name(self):
        data = csv_report(
            ["Patient Name", "Service Type", "Service Date"],
            [["A Patient", "Consultation", "2026-08-01"]],
        )

        merged, _ = combine_reports([("services.csv", data)])

        self.assertEqual(merged.iloc[0]["UHID"], "A Patient")

    def test_unrecognized_file_reports_columns_and_merged_files_are_not_combined(self):
        merged_workbook = csv_report(
            ["Type", "DOA", "DOD", "Dept. Name", "Doctor Name", "Panel Name"],
            [["Admission", "2026-08-01", "", "Medicine", "Dr A", "Cash"]],
        )
        unknown = csv_report(["Facility", "Amount"], [["Ward A", "20"]])

        combined, report = combine_reports([
            ("already-merged.csv", merged_workbook),
            ("unknown.csv", unknown),
        ])

        self.assertTrue(combined.empty)
        self.assertIsNone(report[0]["detected_kind"])
        self.assertIn("already-merged workbooks", report[0]["message"])
        self.assertIn("Type", report[0]["message"])
        self.assertIn("Facility", report[1]["message"])
        self.assertEqual(report[1]["rows_used"], 0)

    def test_date_parsing_day_first_iso_serial_and_future_repair(self):
        parsed = _parse_date_column(
            ["13/07/2026", "01/08/2026", "2026-08-02 12:30:00"],
            today="2026-10-20",
        )
        self.assertEqual(
            [value.strftime("%Y-%m-%d") for value in parsed],
            ["2026-07-13", "2026-08-01", "2026-08-02"],
        )

        default_month_first = _parse_date_column(
            ["01-07-2026"], today="2026-10-20"
        )
        self.assertEqual(default_month_first[0].strftime("%Y-%m-%d"), "2026-01-07")

        serial = (pd.Timestamp("2026-08-01") - pd.Timestamp("1899-12-30")).days
        excel_date = _parse_date_column([serial], today="2026-10-20")
        self.assertEqual(excel_date[0].strftime("%Y-%m-%d"), "2026-08-01")

        repaired = _parse_date_column(
            ["12/01/2026"], today="2026-10-20"
        )
        self.assertEqual(repaired[0].strftime("%Y-%m-%d"), "2026-01-12")
        iso_future = _parse_date_column(
            ["2026-12-01"], today="2026-10-20"
        )
        self.assertEqual(iso_future[0].strftime("%Y-%m-%d"), "2026-12-01")

    def test_future_date_is_repaired_during_report_merge(self):
        admission = csv_report(
            admission_headers(),
            [["A1", "00123", "A Patient", "Dr A", "Medicine", "Cash",
              "12/01/2026", "10", "9", "1", "0"]],
        )

        with patch(
            "comparison.report_combiner.pd.Timestamp.today",
            return_value=pd.Timestamp("2026-10-20"),
        ):
            merged, _ = combine_reports([("admissions.csv", admission)])

        self.assertEqual(merged.iloc[0]["DOA"], "2026-01-12")

    def test_lower_header_detection_requires_at_least_three_known_words(self):
        content = csv_report(
            ["File Id", "UHID", "Patient Name", "Doa", "Dept. Name"],
            [["A1", "U1", "Patient", "2026-08-01", "Medicine"]],
            title_rows=(("Report title",), ("Range: August",)),
        )

        frame = read_raw_file("admission.csv", content)

        self.assertEqual(
            frame.columns.tolist(),
            ["File Id", "UHID", "Patient Name", "Doa", "Dept. Name"],
        )
        self.assertEqual(frame.iloc[0]["File Id"], "A1")

    def test_reads_xlsx_files_with_a_lower_header_row(self):
        headers = admission_headers()
        rows = [
            ["Monthly report"] + [None] * (len(headers) - 1),
            headers,
            ["A1", "U1", "Patient", "Dr A", "Medicine", "Cash", "2026-08-01", "10", "9", "1", "0"],
        ]
        workbook = BytesIO()
        pd.DataFrame(rows).to_excel(
            workbook,
            index=False,
            header=False,
        )

        frame = read_raw_file("admissions.xlsx", workbook.getvalue())

        self.assertEqual(frame.columns.tolist(), headers)
        self.assertEqual(frame.iloc[0]["File Id"], "A1")
if __name__ == "__main__":
    unittest.main()
