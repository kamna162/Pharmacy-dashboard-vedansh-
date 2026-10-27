from __future__ import annotations

import csv
import hashlib
import math
import re
import threading
from collections import Counter, OrderedDict
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import Path

import pandas as pd


OUTPUT_COLUMNS = [
    "S No.",
    "DOA",
    "DOD",
    "Month",
    "Week",
    "Period",
    "Type",
    "UHID",
    "Patient Name",
    "Doctor Name",
    "Dept. Name",
    "Panel Name",
    "Cash Due",
    "Credit Due",
    "Bed Days",
    "Service Name",
    "Service Type",
    "Service Amount",
    "Service Final Amount",
]

_HEADER_WORDS = {
    "fileid",
    "uhid",
    "doa",
    "dod",
    "servicedate",
    "servicetype",
    "servicename",
    "doctor",
    "patientname",
    "deptname",
    "department",
    "panelname",
    "finalamt",
    "grossamt",
    "cashdue",
    "creditdue",
}

_FILE_ID_HEADERS = ("fileid", "fileidentifier")
_UHID_HEADERS = ("uhid", "patientid", "mrn", "medicalrecordnumber")
_PATIENT_HEADERS = ("patientname",)
_DOCTOR_HEADERS = ("doctorname", "doctor")
_DEPARTMENT_HEADERS = ("deptname", "departmentname", "department", "dept")
_PANEL_HEADERS = ("panelname", "panel")
_ADMISSION_DATE_HEADERS = ("doa", "dateofadmission", "admissiondate")
_DISCHARGE_DATE_HEADERS = ("dod", "dateofdischarge", "dischargedate")
_SERVICE_DATE_HEADERS = ("servicedate",)
_SERVICE_NAME_HEADERS = ("servicename",)
_SERVICE_TYPE_HEADERS = ("servicetype",)
_GROSS_AMOUNT_HEADERS = (
    "servicegrossa",
    "servicegrossamt",
    "servicegrossamount",
    "grossamt",
    "grossamount",
)
_FINAL_AMOUNT_HEADERS = (
    "servicefinala",
    "servicefinalamt",
    "servicefinalamount",
    "finalamt",
    "finalamount",
    "netamount",
    "netamt",
)
_CASH_DUE_HEADERS = ("cashdue", "cashdueamount")
_CREDIT_DUE_HEADERS = ("creditdue", "creditdueamount")
_PARSED_FRAME_CACHE_LIMIT = 128 * 1024 * 1024
_PARSED_FRAME_CACHE = OrderedDict()
_PARSED_FRAME_CACHE_SIZE = 0
_PARSED_FRAME_CACHE_LOCK = threading.Lock()


def read_raw_file(filename: str, file_bytes: bytes) -> pd.DataFrame:
    """Read a raw xlsx, xls, or csv report and detect a lower header row."""
    global _PARSED_FRAME_CACHE_SIZE
    if not file_bytes:
        raise ValueError("The selected report is empty.")

    suffix = Path(filename).suffix.casefold()
    cache_key = (suffix, hashlib.sha256(file_bytes).digest())
    with _PARSED_FRAME_CACHE_LOCK:
        cached = _PARSED_FRAME_CACHE.pop(cache_key, None)
        if cached is not None:
            _PARSED_FRAME_CACHE[cache_key] = cached
            return cached.copy()

    frame = _read_raw_file(filename, file_bytes, suffix)
    frame_size = int(frame.memory_usage(index=True, deep=True).sum())
    if frame_size <= _PARSED_FRAME_CACHE_LIMIT:
        with _PARSED_FRAME_CACHE_LOCK:
            previous = _PARSED_FRAME_CACHE.pop(cache_key, None)
            if previous is not None:
                _PARSED_FRAME_CACHE_SIZE -= int(
                    previous.memory_usage(index=True, deep=True).sum()
                )
            while (
                _PARSED_FRAME_CACHE
                and _PARSED_FRAME_CACHE_SIZE + frame_size > _PARSED_FRAME_CACHE_LIMIT
            ):
                _, oldest = _PARSED_FRAME_CACHE.popitem(last=False)
                _PARSED_FRAME_CACHE_SIZE -= int(
                    oldest.memory_usage(index=True, deep=True).sum()
                )
            _PARSED_FRAME_CACHE[cache_key] = frame.copy()
            _PARSED_FRAME_CACHE_SIZE += frame_size
    return frame


def _read_raw_file(filename: str, file_bytes: bytes, suffix: str) -> pd.DataFrame:
    if suffix in {".xlsx", ".xls"}:
        try:
            raw = pd.read_excel(BytesIO(file_bytes), header=None, dtype=object)
        except (ImportError, OSError, ValueError) as error:
            raise ValueError(f"Could not read the Excel report: {error}") from error
    elif suffix == ".csv":
        raw = _read_csv_rows(file_bytes)
    else:
        raise ValueError("Raw reports must be .xlsx, .xls, or .csv files.")

    if raw.empty:
        return pd.DataFrame()

    header_row = _header_row_index(raw)
    columns = _unique_headers(raw.iloc[header_row].tolist())
    frame = raw.iloc[header_row + 1 :].copy()
    frame.columns = columns
    nonempty_rows = frame.apply(
        lambda row: any(not _is_empty(value) for value in row), axis=1
    )
    return frame.loc[nonempty_rows].reset_index(drop=True)


def combine_reports(
    files: list[tuple[str, bytes]],
    *,
    parsed_frames: dict[str, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """Combine raw admission, discharge, and service reports into dashboard rows."""
    report: list[dict[str, object]] = []
    file_data: list[dict[str, object]] = []

    for file_index, (filename, file_bytes) in enumerate(files):
        item: dict[str, object] = {
            "file_name": filename,
            "detected_kind": None,
            "rows_read": 0,
            "rows_used": 0,
            "columns": [],
            "message": "",
        }
        report.append(item)
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        frame = parsed_frames.get(content_hash) if parsed_frames is not None else None
        if frame is None:
            try:
                frame = read_raw_file(filename, file_bytes)
            except ValueError as error:
                item["message"] = f"{error} Columns: (none)."
                continue

        columns = [str(column) for column in frame.columns]
        item["columns"] = columns
        item["rows_read"] = len(frame)
        kind = _detect_report_kind(filename, frame)
        item["detected_kind"] = kind
        if kind is None:
            item["message"] = (
                "Not recognized as a raw report; already-merged workbooks should "
                "use the existing upload path. Columns: "
                + _column_list(columns)
                + "."
            )
            continue

        if kind == "Service":
            records, latest_date, reason = _service_records(frame, file_index)
        else:
            records, latest_date, reason = _visit_records(frame, kind, file_index)
        for record in records:
            record["latest_date"] = latest_date
        file_data.append({
            "file_index": file_index,
            "kind": kind,
            "latest_date": latest_date,
            "records": records,
        })
        if not records:
            item["_empty_reason"] = reason

    admission_records = [
        record
        for item in file_data
        if item["kind"] == "Admission"
        for record in item["records"]
    ]
    discharge_records = [
        record
        for item in file_data
        if item["kind"] == "Discharge"
        for record in item["records"]
    ]
    service_records = [
        record
        for item in file_data
        if item["kind"] == "Service"
        for record in item["records"]
    ]

    admissions = _latest_visit_versions(admission_records)
    discharges = _latest_visit_versions(discharge_records)
    services = _deduplicate_services(service_records)

    admissions_by_id = {record["file_id"]: record for record in admissions}
    discharges_by_id = {record["file_id"]: record for record in discharges}
    output_rows = []

    for record in admissions:
        discharge = discharges_by_id.get(record["file_id"])
        output_rows.append(_dashboard_row(
            record,
            "Admission",
            doa=record["doa"],
            dod=discharge["dod"] if discharge else None,
        ))

    for record in discharges:
        admission = admissions_by_id.get(record["file_id"])
        output_rows.append(_dashboard_row(
            record,
            "Discharge",
            doa=admission["doa"] if admission else record["doa"],
            dod=record["dod"],
        ))

    output_rows.extend(
        _dashboard_row(
            record,
            "OPD",
            doa=record["service_date"],
            dod=record["service_date"],
            bed_days=0,
            cash_due=0,
            credit_due=0,
        )
        for record in services
    )

    for file_index, item in enumerate(report):
        used_count = sum(
            record["file_index"] == file_index for record in admissions
        )
        used_count += sum(
            record["file_index"] == file_index for record in discharges
        )
        used_count += sum(
            record["file_index"] == file_index for record in services
        )
        item["rows_used"] = used_count
        empty_reason = item.pop("_empty_reason", None)
        if used_count:
            item["message"] = f"Contributed {used_count} row(s) to the combined data."
        elif item["detected_kind"] is not None:
            if empty_reason is None:
                empty_reason = "all rows were superseded by newer reports or deduplicated"
            item["message"] = (
                f"Recognized as {item['detected_kind']} but contributed no rows: "
                f"{empty_reason}. Columns: {_column_list(item['columns'])}."
            )

    output_rows.sort(key=lambda row: (
        row["_sort_date"] is None,
        row["_sort_date"] or pd.Timestamp.max,
        row["_sort_order"],
    ))
    for sequence, row in enumerate(output_rows, start=1):
        row["S No."] = sequence
        row.pop("_sort_date", None)
        row.pop("_sort_order", None)

    merged = pd.DataFrame(output_rows, columns=OUTPUT_COLUMNS)
    return merged, report


def inspect_reports(
    files: list[tuple[str, bytes]], *, include_frames: bool = False
) -> list[dict[str, object]]:
    """Classify incoming reports without doing work that the rebuild repeats."""
    report = []
    for filename, file_bytes in files:
        item: dict[str, object] = {
            "file_name": filename,
            "detected_kind": None,
            "rows_read": 0,
            "rows_used": 0,
            "columns": [],
            "message": "",
        }
        report.append(item)
        try:
            frame = read_raw_file(filename, file_bytes)
        except ValueError as error:
            item["message"] = f"{error} Columns: (none)."
            if include_frames:
                item["_frame"] = None
            continue
        if include_frames:
            item["_frame"] = frame

        columns = [str(column) for column in frame.columns]
        item["columns"] = columns
        item["rows_read"] = len(frame)
        item["detected_kind"] = _detect_report_kind(filename, frame)
        if item["detected_kind"] is None:
            item["message"] = (
                "Not recognized as a raw report; already-merged workbooks should "
                "use the existing upload path. Columns: "
                + _column_list(columns)
                + "."
            )
    return report


def _read_csv_rows(file_bytes: bytes) -> pd.DataFrame:
    decoded = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            decoded = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("Could not decode the CSV report as UTF-8 or Windows-1252.")

    try:
        rows = list(csv.reader(StringIO(decoded, newline="")))
    except csv.Error as error:
        raise ValueError(f"Could not read the CSV report: {error}") from error
    if not rows:
        return pd.DataFrame()

    width = max(len(row) for row in rows)
    return pd.DataFrame(
        [row + [None] * (width - len(row)) for row in rows],
        dtype=object,
    )


def _header_row_index(raw: pd.DataFrame) -> int:
    first_row = raw.iloc[0].tolist()
    unnamed = sum(
        _is_empty(value)
        or str(value).strip().casefold().startswith("unnamed:")
        for value in first_row
    )
    if unnamed <= len(first_row) / 2:
        return 0

    for row_index in range(min(25, len(raw))):
        if _header_word_count(raw.iloc[row_index].tolist()) >= 3:
            return row_index
    return 0


def _header_word_count(values: list[object]) -> int:
    count = 0
    for value in values:
        key = _column_key(value)
        if any(
            key == known or key.startswith(known) or known in key
            for known in _HEADER_WORDS
        ):
            count += 1
    return count


def _unique_headers(values: list[object]) -> list[str]:
    columns = []
    seen: Counter[str] = Counter()
    for index, value in enumerate(values):
        name = "" if _is_empty(value) else str(value).strip()
        if not name:
            name = f"Unnamed: {index}"
        seen[name.casefold()] += 1
        if seen[name.casefold()] > 1:
            name = f"{name}_{seen[name.casefold()]}"
        columns.append(name)
    return columns


def _column_key(value: object) -> str:
    if _is_empty(value):
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _find_column(columns, aliases: tuple[str, ...]) -> str | None:
    by_key = {_column_key(column): str(column) for column in columns}
    for alias in aliases:
        if alias in by_key:
            return by_key[alias]
    return None


def _detect_report_kind(filename: str, frame: pd.DataFrame) -> str | None:
    columns = list(frame.columns)
    file_id = _find_column(columns, _FILE_ID_HEADERS)
    admission_date = _find_column(columns, _ADMISSION_DATE_HEADERS)
    discharge_date = _find_column(columns, _DISCHARGE_DATE_HEADERS)

    service_date = _find_column(columns, _SERVICE_DATE_HEADERS)
    service_kind = (
        _find_column(columns, _SERVICE_TYPE_HEADERS)
        or _find_column(columns, _SERVICE_NAME_HEADERS)
    )
    if service_date and service_kind and not file_id:
        return "Service"

    if not file_id:
        return None
    if admission_date and not discharge_date:
        return "Admission"
    if discharge_date and not admission_date:
        return "Discharge"
    if not admission_date or not discharge_date:
        return None

    filename_key = re.sub(
        r"[^a-z]+", " ", Path(filename).stem.casefold()
    )
    if re.search(r"\bdischarge\b", filename_key):
        return "Discharge"
    if re.search(r"\badmission\b", filename_key):
        return "Admission"

    values = frame[discharge_date]
    filled = sum(not _is_empty(value) for value in values)
    return "Discharge" if len(values) and filled / len(values) >= 0.5 else "Admission"


def _visit_records(
    frame: pd.DataFrame, kind: str, file_index: int
) -> tuple[list[dict[str, object]], pd.Timestamp | None, str]:
    file_id_column = _find_column(frame.columns, _FILE_ID_HEADERS)
    admission_column = _find_column(frame.columns, _ADMISSION_DATE_HEADERS)
    discharge_column = _find_column(frame.columns, _DISCHARGE_DATE_HEADERS)
    uhid_column = _find_column(frame.columns, _UHID_HEADERS)
    patient_column = _find_column(frame.columns, _PATIENT_HEADERS)
    doctor_column = _find_column(frame.columns, _DOCTOR_HEADERS)
    department_column = _find_column(frame.columns, _DEPARTMENT_HEADERS)
    panel_column = _find_column(frame.columns, _PANEL_HEADERS)
    gross_column = _find_column(frame.columns, _GROSS_AMOUNT_HEADERS)
    final_column = _find_column(frame.columns, _FINAL_AMOUNT_HEADERS)
    cash_column = _find_column(frame.columns, _CASH_DUE_HEADERS)
    credit_column = _find_column(frame.columns, _CREDIT_DUE_HEADERS)
    column_positions = {
        column: index for index, column in enumerate(frame.columns)
    }

    parsed_dates = {}
    for column in (admission_column, discharge_column):
        if column and column not in parsed_dates:
            parsed_dates[column] = _parse_date_column(frame[column].tolist())

    grouped: dict[str, dict[str, object]] = {}
    for row_index, row in enumerate(frame.itertuples(index=False, name=None)):
        file_id = _identifier_text(
            _first_value(row, file_id_column, column_positions)
        )
        if not file_id:
            continue
        record = grouped.get(file_id)
        if record is None:
            record = {
                "file_id": file_id,
                "uhid": None,
                "patient_name": None,
                "doctor_name": None,
                "department_name": None,
                "panel_name": None,
                "doa": None,
                "dod": None,
                "service_name": None,
                "service_type": None,
                "service_amount": 0.0,
                "service_final_amount": 0.0,
                "cash_due": 0.0,
                "credit_due": 0.0,
                "file_index": file_index,
            }
            grouped[file_id] = record

        for field, column in (
            ("uhid", uhid_column),
            ("patient_name", patient_column),
            ("doctor_name", doctor_column),
            ("department_name", department_column),
            ("panel_name", panel_column),
        ):
            if record[field] is None:
                value = _first_value(row, column, column_positions)
                if not _is_empty(value):
                    record[field] = _text_value(value)

        if admission_column and record["doa"] is None:
            record["doa"] = parsed_dates[admission_column][row_index]
        if discharge_column and record["dod"] is None:
            record["dod"] = parsed_dates[discharge_column][row_index]

        record["service_amount"] += _amount(
            _first_value(row, gross_column, column_positions)
        )
        record["service_final_amount"] += _amount(
            _first_value(row, final_column, column_positions)
        )
        record["cash_due"] += _amount(
            _first_value(row, cash_column, column_positions)
        )
        record["credit_due"] += _amount(
            _first_value(row, credit_column, column_positions)
        )

    all_dates = [
        parsed
        for dates in parsed_dates.values()
        for parsed in dates
        if parsed is not None
    ]
    latest_date = max(all_dates) if all_dates else None
    records = list(grouped.values())
    reason = (
        "no rows with a non-empty File Id"
        if not records else ""
    )
    return records, latest_date, reason


def _service_records(
    frame: pd.DataFrame, file_index: int
) -> tuple[list[dict[str, object]], pd.Timestamp | None, str]:
    service_date_column = _find_column(frame.columns, _SERVICE_DATE_HEADERS)
    uhid_column = _find_column(frame.columns, _UHID_HEADERS)
    patient_column = _find_column(frame.columns, _PATIENT_HEADERS)
    doctor_column = _find_column(frame.columns, _DOCTOR_HEADERS)
    department_column = _find_column(frame.columns, _DEPARTMENT_HEADERS)
    panel_column = _find_column(frame.columns, _PANEL_HEADERS)
    service_name_column = _find_column(frame.columns, _SERVICE_NAME_HEADERS)
    service_type_column = _find_column(frame.columns, _SERVICE_TYPE_HEADERS)
    gross_column = _find_column(frame.columns, _GROSS_AMOUNT_HEADERS)
    final_column = _find_column(frame.columns, _FINAL_AMOUNT_HEADERS)
    column_positions = {
        column: index for index, column in enumerate(frame.columns)
    }
    dates = _parse_date_column(
        frame[service_date_column].tolist(),
        normalize=False,
    )
    records = []
    slots: Counter[tuple[object, ...]] = Counter()

    for row_index, row in enumerate(frame.itertuples(index=False, name=None)):
        service_date = dates[row_index]
        if service_date is None:
            continue
        patient_name = _text_value(
            _first_value(row, patient_column, column_positions)
        )
        uhid = _first_value(row, uhid_column, column_positions)
        if _is_empty(uhid):
            uhid = patient_name
        record = {
            "file_id": None,
            "uhid": _identifier_text(uhid),
            "patient_name": patient_name,
            "doctor_name": _text_value(
                _first_value(row, doctor_column, column_positions)
            ),
            "department_name": _text_value(
                _first_value(row, department_column, column_positions)
            ),
            "panel_name": _text_value(
                _first_value(row, panel_column, column_positions)
            ),
            "doa": service_date,
            "dod": service_date,
            "service_date": service_date,
            "service_name": _text_value(
                _first_value(row, service_name_column, column_positions)
            ),
            "service_type": _text_value(
                _first_value(row, service_type_column, column_positions)
            ),
            "service_amount": _amount(
                _first_value(row, gross_column, column_positions)
            ),
            "service_final_amount": _amount(
                _first_value(row, final_column, column_positions)
            ),
            "cash_due": 0.0,
            "credit_due": 0.0,
            "file_index": file_index,
        }
        signature = _service_signature(record)
        slot = slots[signature]
        slots[signature] += 1
        record["service_slot"] = slot
        record["service_signature"] = signature
        records.append(record)

    valid_dates = [parsed for parsed in dates if parsed is not None]
    latest_date = max(valid_dates) if valid_dates else None
    reason = (
        "no service rows had a valid Service Date"
        if not records else ""
    )
    return records, latest_date, reason


def _latest_visit_versions(records: list[dict[str, object]]):
    winners = {}
    ordered = sorted(
        records,
        key=lambda record: (
            record.get("latest_date") is not None,
            record.get("latest_date") or pd.Timestamp.min,
            record["file_index"],
        ),
    )
    for record in ordered:
        winners[record["file_id"]] = record
    return list(winners.values())


def _deduplicate_services(records: list[dict[str, object]]):
    file_latest_dates = {}
    for record in records:
        current = file_latest_dates.get(record["file_index"])
        service_date = record["service_date"]
        if current is None or service_date > current:
            file_latest_dates[record["file_index"]] = service_date

    ordered = sorted(
        records,
        key=lambda record: (
            file_latest_dates.get(record["file_index"], pd.Timestamp.min),
            record["file_index"],
        ),
        reverse=True,
    )
    clusters: dict[
        tuple[tuple[object, ...], int], list[list[dict[str, object]]]
    ] = {}
    winners = []
    for record in ordered:
        cluster_key = (
            record["service_signature"],
            record["service_slot"],
        )
        candidates = clusters.setdefault(cluster_key, [])
        matching = [
            group
            for group in candidates
            if group[0]["file_index"] != record["file_index"]
            and abs((group[0]["service_date"] - record["service_date"]).total_seconds())
            <= 90
        ]
        if matching:
            continue
        candidates.append([record])
        winners.append(record)
    return winners


def _service_signature(record: dict[str, object]) -> tuple[object, ...]:
    fields = (
        "uhid",
        "patient_name",
        "doctor_name",
        "department_name",
        "panel_name",
        "service_name",
        "service_type",
        "service_amount",
        "service_final_amount",
    )
    return tuple(_signature_value(record[field]) for field in fields)


def _signature_value(value: object):
    if isinstance(value, (int, float)):
        return float(value)
    return _text_value(value)


def _parse_date_column(values: list[object], today=None, normalize=True):
    day_first = _day_first_is_proven(values)
    reference_date = (
        pd.Timestamp.today().normalize()
        if today is None else pd.Timestamp(today).normalize()
    )
    parsed_values = []
    for value in values:
        parsed = _parse_one_date(value, day_first)
        if (
            parsed is not None
            and _is_ambiguous_date_text(value)
            and parsed.normalize() > reference_date + pd.Timedelta(days=1)
        ):
            if 1 <= parsed.day <= 12:
                try:
                    swapped = pd.Timestamp(
                        year=parsed.year,
                        month=parsed.day,
                        day=parsed.month,
                    ) + (parsed - parsed.normalize())
                except (ValueError, OverflowError):
                    swapped = None
                if (
                    swapped is not None
                    and swapped.normalize() <= reference_date + pd.Timedelta(days=1)
                ):
                    parsed = swapped
        if parsed is not None and normalize:
            parsed = parsed.normalize()
        parsed_values.append(parsed)
    return parsed_values


def _day_first_is_proven(values: list[object]) -> bool:
    first_above_twelve = False
    second_above_twelve = False
    for value in values:
        text = _text_value(value)
        match = re.match(
            r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.]\d{4}(?:\b|[ T])",
            text,
        )
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        first_above_twelve |= first > 12
        second_above_twelve |= second > 12
    return first_above_twelve and not second_above_twelve


def _is_ambiguous_date_text(value: object) -> bool:
    return bool(re.match(
        r"^\s*\d{1,2}[-/.]\d{1,2}[-/.]\d{4}(?:\b|[ T])",
        _text_value(value),
    ))


def _parse_one_date(value: object, day_first: bool):
    if _is_empty(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        parsed = pd.Timestamp(value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)) or not 0 < float(value) < 100_000:
            return None
        parsed = pd.Timestamp("1899-12-30") + pd.to_timedelta(
            float(value), unit="D"
        )
    else:
        text = str(value).strip()
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
            number = float(text)
            if not math.isfinite(number) or not 0 < number < 100_000:
                return None
            parsed = pd.Timestamp("1899-12-30") + pd.to_timedelta(number, unit="D")
        else:
            is_iso = bool(re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", text))
            try:
                parsed = pd.to_datetime(
                    text,
                    errors="coerce",
                    dayfirst=False if is_iso else day_first,
                    format="mixed",
                )
            except (TypeError, ValueError):
                parsed = pd.to_datetime(
                    text,
                    errors="coerce",
                    dayfirst=False if is_iso else day_first,
                )
            if pd.isna(parsed):
                return None
            parsed = pd.Timestamp(parsed)
    return parsed


def _dashboard_row(
    record: dict[str, object],
    report_type: str,
    doa,
    dod,
    bed_days=None,
    cash_due=None,
    credit_due=None,
):
    if bed_days is None:
        bed_days = (
            (dod - doa).days
            if doa is not None and dod is not None else ""
        )
    reference_date = dod if report_type == "Discharge" else doa
    normalized_reference_date = (
        reference_date.normalize() if reference_date is not None else None
    )
    return {
        "S No.": 0,
        "DOA": _date_text(doa),
        "DOD": _date_text(dod),
        "Month": reference_date.strftime("%B") if reference_date is not None else "",
        "Week": (
            f"Week {(reference_date.day - 1) // 7 + 1}"
            if reference_date is not None else ""
        ),
        "Period": _period_name(reference_date),
        "Type": report_type,
        "UHID": _identifier_text(record.get("uhid")),
        "Patient Name": _text_value(record.get("patient_name")),
        "Doctor Name": _text_value(record.get("doctor_name")),
        "Dept. Name": _text_value(record.get("department_name")),
        "Panel Name": _panel_category(record.get("panel_name")),
        "Cash Due": record["cash_due"] if cash_due is None else cash_due,
        "Credit Due": record["credit_due"] if credit_due is None else credit_due,
        "Bed Days": bed_days,
        "Service Name": _text_value(record.get("service_name")),
        "Service Type": _text_value(record.get("service_type")),
        "Service Amount": record["service_amount"],
        "Service Final Amount": record["service_final_amount"],
        "_sort_date": normalized_reference_date,
        "_sort_order": {"Admission": 0, "Discharge": 1, "OPD": 2}[report_type],
    }


def _period_name(value):
    if value is None:
        return ""
    if value.day <= 10:
        return "1-10 Days"
    if value.day <= 20:
        return "11-20 Days"
    return "21-31 Days"


def _date_text(value):
    return value.strftime("%Y-%m-%d") if value is not None else ""


def _panel_category(value):
    text = _text_value(value).strip().casefold()
    if text == "cash":
        return "Cash"
    for keyword, category in (
        ("ayushman", "Ayushman"),
        ("echs", "ECHS"),
        ("capf", "CAPF"),
        ("cghs", "CGHS"),
    ):
        if keyword in text:
            return category
    return "TPA"


def _first_value(row, column, column_positions=None):
    if column is None:
        return None
    value = (
        row[column_positions[column]]
        if column_positions is not None else row[column]
    )
    return None if _is_empty(value) else value


def _amount(value) -> float:
    if _is_empty(value):
        return 0.0
    text = str(value).strip().replace(",", "").replace("₹", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    number = pd.to_numeric(text, errors="coerce")
    if pd.isna(number):
        return 0.0
    amount = float(number)
    return -amount if negative else amount


def _identifier_text(value) -> str:
    text = _text_value(value).strip()
    if not text or text.casefold().startswith("opd-"):
        return text
    if re.fullmatch(r"[+-]?\d+\.0+", text):
        return text.split(".", maxsplit=1)[0]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(float(value)) and float(value).is_integer():
            return str(int(value))
    return text


def _text_value(value) -> str:
    if _is_empty(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        missing = pd.isna(value)
        return bool(missing)
    except (TypeError, ValueError):
        return False


def _column_list(columns) -> str:
    return ", ".join(str(column) for column in columns) or "(none)"
