from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "hospital_data.db"
_INITIALIZED_DATABASES = set()
_DATABASE_INITIALIZATION_LOCK = threading.Lock()
UPLOADS_DIR = PROJECT_ROOT / "uploads" / "raw_reports"
BASE_COLUMNS = [
    "id",
    "hospital_name",
    "source_file",
    "upload_date",
    "upload_hash",
    "dataset_id",
    "record_hash",
]


def _normalize_header_name(value):
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"[_/\\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _canonicalize_column_name(value):
    text = _normalize_header_name(value)
    if not text:
        return ""
    key = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    aliases = {
        "service name": "Service Name",
        "service name ": "Service Name",
        "servicename": "Service Name",
        "service name": "Service Name",
        "service type": "Service Type",
        "servicetype": "Service Type",
        "period": "Period",
        "admission date": "DOA",
        "admissiondate": "DOA",
        "date of admission": "DOA",
        "doa": "DOA",
        "discharge date": "DOD",
        "dischargedate": "DOD",
        "date of discharge": "DOD",
        "dod": "DOD",
        "month": "Month",
        "dept name": "Dept. Name",
        "department": "Dept. Name",
        "department name": "Dept. Name",
        "dept": "Dept. Name",
        "doctor name": "Doctor Name",
        "doctor": "Doctor Name",
        "panel name": "Panel Name",
        "panel": "Panel Name",
        "type": "Type",
        "record type": "Type",
        "service final amount": "Service Final Amount",
        "service final amounts": "Service Final Amount",
        "service amount": "Service Amount",
        "cash due": "Cash Due",
        "cash due amount": "Cash Due",
        "credit due": "Credit Due",
        "credit due amount": "Credit Due",
        "bed days": "Bed Days",
        "bed days count": "Bed Days",
        "beddays": "Bed Days",
        "uhid": "UHID",
        "uhid number": "UHID",
        "patient id": "UHID",
    }
    return aliases.get(key, text)


def _quote_identifier(identifier):
    return '"' + str(identifier).replace('"', '""') + '"'


@contextmanager
def _connect():
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        yield connection
    finally:
        connection.close()


def init_database():
    database_path = Path(DATABASE_PATH).resolve()
    with _DATABASE_INITIALIZATION_LOCK:
        if database_path in _INITIALIZED_DATABASES and database_path.is_file():
            return

        database_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hospital_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hospital_name TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    upload_date TIMESTAMP NOT NULL,
                    upload_hash TEXT NOT NULL,
                    dataset_id TEXT,
                    record_hash TEXT NOT NULL UNIQUE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_hospital_data_hospital "
                "ON hospital_data (hospital_name)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_hospital_data_upload "
                "ON hospital_data (upload_hash)"
            )
            existing_columns = _existing_columns(connection)
            if "dataset_id" not in {column.casefold() for column in existing_columns}:
                connection.execute(
                    "ALTER TABLE hospital_data ADD COLUMN dataset_id TEXT"
                )
            legacy_rows = connection.execute(
                """
                SELECT id, hospital_name, source_file, upload_hash, dataset_id
                FROM hospital_data
                WHERE dataset_id IS NULL OR dataset_id = ''
                """
            ).fetchall()
            for row in legacy_rows:
                legacy_key = "|".join(
                    [row["hospital_name"], row["source_file"], row["upload_hash"]]
                )
                legacy_id = "legacy-" + hashlib.sha256(
                    legacy_key.encode("utf-8")
                ).hexdigest()
                connection.execute(
                    "UPDATE hospital_data SET dataset_id = ? WHERE id = ?",
                    (legacy_id, row["id"]),
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_hospital_data_dataset_id "
                "ON hospital_data (dataset_id, id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_report_uploads (
                    upload_id TEXT PRIMARY KEY,
                    hospital_name TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    included INTEGER NOT NULL DEFAULT 1,
                    storage_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    rows_read INTEGER NOT NULL DEFAULT 0,
                    rows_used INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (hospital_name, content_hash)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_raw_report_uploads_hospital "
                "ON raw_report_uploads (hospital_name, uploaded_at, upload_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scraper_archive_days (
                    hospital_name TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (hospital_name, event_date)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scraper_import_state (
                    hospital_name TEXT PRIMARY KEY,
                    as_of_date TEXT NOT NULL,
                    cutoff_date TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    source_files TEXT NOT NULL,
                    active_row_count INTEGER NOT NULL,
                    archived_row_count INTEGER NOT NULL
                )
                """
            )
            connection.execute("DROP INDEX IF EXISTS idx_hospital_data_dataset")
            connection.commit()
        _INITIALIZED_DATABASES.add(database_path)


def _existing_columns(connection):
    return [
        row[1]
        for row in connection.execute("PRAGMA table_info(hospital_data)").fetchall()
    ]


def _prepare_frame(frame, existing_columns):
    reserved = {column.casefold() for column in BASE_COLUMNS}
    existing_by_key = {column.casefold(): column for column in existing_columns}
    columns = []
    positions = []
    used = set()

    for position, value in enumerate(frame.columns):
        name = _canonicalize_column_name(value)
        if not name or name.casefold() in reserved:
            continue
        normalized_name = _normalize_header_name(name)
        existing_match = existing_by_key.get(normalized_name.casefold(), normalized_name)
        candidate = existing_match
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{existing_match}_{suffix}"
            suffix += 1
        used.add(candidate.casefold())
        positions.append(position)
        columns.append(candidate)

    prepared = frame.iloc[:, positions].copy()
    prepared.columns = columns
    return prepared


def _ensure_columns(connection, columns, existing_columns):
    existing = {column.casefold() for column in existing_columns}
    for column in columns:
        if column.casefold() in existing:
            continue
        connection.execute(
            f"ALTER TABLE hospital_data ADD COLUMN {_quote_identifier(column)} TEXT"
        )
        existing.add(column.casefold())


def _normalise_for_hash(value):
    if pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def _record_hash(row, columns, hospital_name, upload_hash, row_number):
    values = {
        column: _normalise_for_hash(row.get(column))
        for column in columns
    }
    values["hospital_name"] = hospital_name
    values["upload_hash"] = upload_hash
    values["row_number"] = row_number
    payload = json.dumps(values, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _upload_hash(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def check_duplicate_upload(file_bytes, hospital_name):
    init_database()
    upload_hash = _upload_hash(file_bytes)
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM hospital_data
            WHERE hospital_name = ? AND upload_hash = ?
            """,
            (hospital_name, upload_hash),
        ).fetchone()
    return bool(row["count"])


def save_hospital_data(df, hospital_name, source_file, file_bytes):
    if df is None or df.empty:
        raise ValueError("The uploaded Excel file is empty.")

    init_database()
    upload_hash = _upload_hash(file_bytes)
    upload_date = datetime.now(timezone.utc).isoformat()

    with _connect() as connection:
        existing_dataset = connection.execute(
            """
            SELECT dataset_id
            FROM hospital_data
            WHERE hospital_name = ? AND upload_hash = ?
            LIMIT 1
            """,
            (hospital_name, upload_hash),
        ).fetchone()
        dataset_id = (
            existing_dataset["dataset_id"]
            if existing_dataset and existing_dataset["dataset_id"]
            else str(uuid.uuid4())
        )
        existing_columns = _existing_columns(connection)
        frame = _prepare_frame(df.copy(), existing_columns)
        if frame.empty or not len(frame.columns):
            raise ValueError("The uploaded file has no usable data columns.")
        frame = frame.where(pd.notna(frame), None)
        columns = list(frame.columns)
        _ensure_columns(connection, columns, existing_columns)

        existing_count = connection.execute(
            "SELECT COUNT(*) AS count FROM hospital_data WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()["count"]
        if existing_dataset and existing_count != len(frame):
            connection.execute(
                "DELETE FROM hospital_data WHERE dataset_id = ?",
                (dataset_id,),
            )

        records = []
        for row_number, (_, row) in enumerate(frame.iterrows()):
            values = {
                column: _normalise_for_hash(row[column])
                if row[column] is not None else None
                for column in columns
            }
            values["record_hash"] = _record_hash(
                row, columns, hospital_name, upload_hash, row_number
            )
            records.append(values)

        table_columns = BASE_COLUMNS + columns
        quoted_columns = ", ".join(_quote_identifier(column) for column in table_columns)
        placeholders = ", ".join("?" for _ in table_columns)
        sql = (
            f"INSERT OR IGNORE INTO hospital_data ({quoted_columns}) "
            f"VALUES ({placeholders})"
        )
        row_values = (
            [
                None,
                hospital_name,
                source_file,
                upload_date,
                upload_hash,
                dataset_id,
                record["record_hash"],
            ] + [record.get(column) for column in columns]
            for record in records
        )
        cursor = connection.executemany(sql, row_values)
        inserted = cursor.rowcount
        connection.commit()

    return {
        "inserted": inserted,
        "total": len(records),
        "duplicate": inserted == 0,
        "upload_hash": upload_hash,
        "dataset_id": dataset_id,
    }


def latest_hospital():
    init_database()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT hospital_name
            FROM hospital_data
            ORDER BY upload_date DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    return row["hospital_name"] if row else None


def list_saved_datasets():
    init_database()
    with _connect() as connection:
        rows = connection.execute(
            """
              SELECT dataset_id, source_file, hospital_name, upload_hash,
                   COUNT(*) AS record_count, MAX(upload_date) AS upload_date
            FROM hospital_data
              GROUP BY dataset_id, source_file, hospital_name, upload_hash
            ORDER BY MAX(id) DESC
            """
        ).fetchall()
        archives = connection.execute(
            """
            SELECT hospital_name, dataset_id, SUM(row_count) AS record_count,
                   MAX(updated_at) AS upload_date, MAX(content_hash) AS upload_hash
            FROM scraper_archive_days
            GROUP BY hospital_name, dataset_id
            """
        ).fetchall()
    datasets = [dict(row) for row in rows]
    for archive in archives:
        existing = next(
            (
                item for item in datasets
                if item["dataset_id"] == archive["dataset_id"]
                and item["hospital_name"] == archive["hospital_name"]
                and item["source_file"] == "Combined raw reports"
            ),
            None,
        )
        if existing is None:
            datasets.append({
                "dataset_id": archive["dataset_id"],
                "source_file": "Combined raw reports",
                "hospital_name": archive["hospital_name"],
                "upload_hash": archive["upload_hash"],
                "record_count": int(archive["record_count"]),
                "upload_date": archive["upload_date"],
            })
        else:
            existing["record_count"] += int(archive["record_count"])
            existing["upload_date"] = max(
                str(existing["upload_date"]), str(archive["upload_date"])
            )
    return datasets


def load_hospital_data(
    hospital_name=None, source_file=None, upload_hash=None, dataset_id=None,
    include_archives=True,
):
    init_database()
    with _connect() as connection:
        existing_columns = _existing_columns(connection)
        reserved = {column.casefold() for column in BASE_COLUMNS}
        data_columns = [
            column for column in existing_columns
            if column.casefold() not in reserved
        ]
        conditions = []
        parameters = []
        if hospital_name is not None:
            conditions.append("hospital_name = ?")
            parameters.append(hospital_name)
        if source_file is not None:
            conditions.append("source_file = ?")
            parameters.append(source_file)
        if upload_hash is not None:
            conditions.append("upload_hash = ?")
            parameters.append(upload_hash)
        if dataset_id is not None:
            conditions.append("dataset_id = ?")
            parameters.append(dataset_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        if data_columns:
            columns = ", ".join(_quote_identifier(column) for column in data_columns)
            frame = pd.read_sql_query(
                f"SELECT {columns} FROM hospital_data{where} ORDER BY id",
                connection,
                params=parameters,
            )
        else:
            frame = pd.DataFrame()
    if frame.empty:
        frame = pd.DataFrame()
    if not include_archives or (
        upload_hash is not None
        or source_file not in (None, "Combined raw reports")
    ):
        return frame
    archives = _load_scraper_archive_frames(
        hospital_name=hospital_name,
        dataset_id=dataset_id,
    )
    if not archives:
        return frame
    return pd.concat([frame, *archives], ignore_index=True, sort=False)


def load_all_hospital_data():
    """Load every saved record while retaining its stored hospital and source."""
    init_database()
    with _connect() as connection:
        existing_columns = _existing_columns(connection)
        internal = {
            "id", "upload_date", "upload_hash", "dataset_id", "record_hash",
        }
        selected_columns = [
            column for column in existing_columns
            if column.casefold() not in internal
        ]
        if selected_columns:
            columns = ", ".join(_quote_identifier(column) for column in selected_columns)
            frame = pd.read_sql_query(
                f"SELECT {columns} FROM hospital_data ORDER BY id", connection
            )
        else:
            frame = pd.DataFrame()
    archives = _load_scraper_archive_frames()
    if archives:
        frame = pd.concat([frame, *archives], ignore_index=True, sort=False)
    if frame.empty:
        return pd.DataFrame()
    return frame


def load_comparison_date_data():
    """Load only the columns needed to infer comparison years and months."""
    init_database()
    with _connect() as connection:
        existing = {column.casefold(): column for column in _existing_columns(connection)}
        type_column = existing.get("type")
        doa_column = existing.get("doa")
        dod_column = existing.get("dod")
        if type_column and (doa_column or dod_column):
            type_sql = _quote_identifier(type_column)
            doa_sql = _quote_identifier(doa_column) if doa_column else "NULL"
            dod_sql = _quote_identifier(dod_column) if dod_column else "NULL"
            rows = connection.execute(
                f"""
                SELECT DISTINCT CASE
                    WHEN lower(trim({type_sql})) IN ('admission', 'opd')
                        THEN substr(CAST({doa_sql} AS TEXT), 1, 10)
                    WHEN lower(trim({type_sql})) = 'discharge'
                        THEN substr(CAST({dod_sql} AS TEXT), 1, 10)
                END AS event_date
                FROM hospital_data
                WHERE lower(trim({type_sql})) IN ('admission', 'opd', 'discharge')
                """
            ).fetchall()
        else:
            rows = []
        archived = connection.execute(
            "SELECT event_date FROM scraper_archive_days"
        ).fetchall()
    dates = [row["event_date"] for row in rows if row["event_date"]]
    dates.extend(row["event_date"] for row in archived)
    return pd.DataFrame({
        "Type": ["Admission"] * len(dates),
        "DOA": dates,
        "DOD": [None] * len(dates),
    })


def delete_dataset(dataset_id):
    if not dataset_id:
        raise ValueError("A dataset ID is required.")
    init_database()
    archive_paths = []
    with _connect() as connection:
        archive_rows = connection.execute(
            "SELECT COALESCE(SUM(row_count), 0) FROM scraper_archive_days "
            "WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()[0]
        archive_hospitals = [
            row["hospital_name"]
            for row in connection.execute(
                "SELECT DISTINCT hospital_name FROM scraper_archive_days "
                "WHERE dataset_id = ?",
                (dataset_id,),
            ).fetchall()
        ]
        state_hospitals = [
            row["hospital_name"]
            for row in connection.execute(
                "SELECT hospital_name FROM scraper_import_state"
            ).fetchall()
            if raw_dataset_id(row["hospital_name"]) == dataset_id
        ]
        archive_paths = [
            _archive_storage_path(row["storage_path"])
            for row in connection.execute(
                "SELECT storage_path FROM scraper_archive_days WHERE dataset_id = ?",
                (dataset_id,),
            ).fetchall()
        ]
        cursor = connection.execute(
            "DELETE FROM hospital_data WHERE dataset_id = ?",
            (dataset_id,),
        )
        connection.execute(
            "DELETE FROM scraper_archive_days WHERE dataset_id = ?",
            (dataset_id,),
        )
        for hospital_name in set(archive_hospitals + state_hospitals):
            connection.execute(
                "DELETE FROM scraper_import_state WHERE hospital_name = ?",
                (hospital_name,),
            )
        connection.commit()
    for path in archive_paths:
        if path.exists():
            path.unlink()
    return cursor.rowcount + int(archive_rows)


def raw_dataset_id(hospital_name):
    hospital_key = str(hospital_name).strip().casefold()
    if not hospital_key:
        raise ValueError("A hospital name is required.")
    digest = hashlib.sha256(hospital_key.encode("utf-8")).hexdigest()
    return "raw-" + digest


def list_report_uploads(hospital_name):
    if not hospital_name:
        raise ValueError("A hospital name is required.")
    init_database()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT upload_id, hospital_name, file_name, kind, content_hash,
                   uploaded_at, included, file_size, rows_read, rows_used
            FROM raw_report_uploads
            WHERE hospital_name = ?
            ORDER BY uploaded_at, upload_id
            """,
            (hospital_name,),
        ).fetchall()
    return [
        {**dict(row), "included": bool(row["included"])}
        for row in rows
    ]


def list_report_hospital_names():
    init_database()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT hospital_name, MAX(uploaded_at) AS latest_upload
            FROM raw_report_uploads
            GROUP BY hospital_name
            ORDER BY latest_upload DESC, hospital_name
            """
        ).fetchall()
    return [row["hospital_name"] for row in rows]


def get_report_upload(upload_id):
    if not upload_id:
        raise ValueError("A report upload ID is required.")
    init_database()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT upload_id, hospital_name, file_name, kind, content_hash,
                   uploaded_at, included, storage_path, file_size, rows_read,
                   rows_used
            FROM raw_report_uploads
            WHERE upload_id = ?
            """,
            (upload_id,),
        ).fetchone()
    if row is None:
        raise ValueError("The selected report upload is not available.")
    item = dict(row)
    item["included"] = bool(item["included"])
    return item


def load_report_uploads(hospital_name, included_only=True):
    if not hospital_name:
        raise ValueError("A hospital name is required.")
    init_database()
    conditions = ["hospital_name = ?"]
    parameters = [hospital_name]
    if included_only:
        conditions.append("included = 1")
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT upload_id, hospital_name, file_name, kind, content_hash,
                   uploaded_at, included, storage_path, file_size, rows_read,
                   rows_used
            FROM raw_report_uploads
            WHERE """ + " AND ".join(conditions) + """
            ORDER BY uploaded_at, upload_id
            """,
            parameters,
        ).fetchall()
    uploads = []
    for row in rows:
        item = dict(row)
        path = _report_storage_path(item["storage_path"])
        item["file_bytes"] = path.read_bytes()
        item["included"] = bool(item["included"])
        uploads.append(item)
    return uploads


def commit_report_upload_changes(
    hospital_name,
    merged_frame,
    combined_hash,
    uploads_to_add=(),
    upload_to_delete=None,
    upload_ids_to_delete=(),
    include_change=None,
    report_stats=(),
    scraper_snapshot=None,
):
    """Persist report-file changes and replace only this hospital's raw dataset."""
    if not hospital_name:
        raise ValueError("A hospital name is required.")
    upload_ids_to_delete = tuple(dict.fromkeys(upload_ids_to_delete))
    if upload_to_delete:
        upload_ids_to_delete = tuple(dict.fromkeys(
            (*upload_ids_to_delete, upload_to_delete)
        ))
    if upload_ids_to_delete and include_change is not None:
        raise ValueError("A report upload cannot be deleted and toggled together.")

    init_database()
    created_paths = []
    deleted_paths = []
    deleted_archive_paths = []
    added_uploads = []
    dataset_id = raw_dataset_id(hospital_name)
    upload_date = datetime.now(timezone.utc).isoformat()

    with _connect() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for upload_id in upload_ids_to_delete:
                existing = connection.execute(
                    """
                    SELECT storage_path
                    FROM raw_report_uploads
                    WHERE upload_id = ? AND hospital_name = ?
                    """,
                    (upload_id, hospital_name),
                ).fetchone()
                if existing is None:
                    raise ValueError("The selected report upload is not available.")
                deleted_paths.append(_report_storage_path(existing["storage_path"]))
                connection.execute(
                    "DELETE FROM raw_report_uploads WHERE upload_id = ?",
                    (upload_id,),
                )

            for upload in uploads_to_add:
                file_bytes = upload["file_bytes"]
                content_hash = _upload_hash(file_bytes)
                existing = connection.execute(
                    """
                    SELECT upload_id
                    FROM raw_report_uploads
                    WHERE hospital_name = ? AND content_hash = ?
                    """,
                    (hospital_name, content_hash),
                ).fetchone()
                if existing:
                    added_uploads.append({
                        "upload_id": existing["upload_id"],
                        "duplicate": True,
                    })
                    continue

                storage_path, created = _store_report_bytes(
                    hospital_name, upload["file_name"], content_hash, file_bytes
                )
                if created:
                    created_paths.append(_report_storage_path(storage_path))
                upload_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO raw_report_uploads (
                        upload_id, hospital_name, file_name, kind, content_hash,
                        uploaded_at, included, storage_path, file_size,
                        rows_read, rows_used
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (
                        upload_id,
                        hospital_name,
                        upload["file_name"],
                        upload["kind"],
                        content_hash,
                        upload_date,
                        storage_path,
                        len(file_bytes),
                        int(upload.get("rows_read", 0)),
                        int(upload.get("rows_used", 0)),
                    ),
                )
                added_uploads.append({"upload_id": upload_id, "duplicate": False})

            if include_change is not None:
                upload_id, included = include_change
                cursor = connection.execute(
                    """
                    UPDATE raw_report_uploads
                    SET included = ?
                    WHERE upload_id = ? AND hospital_name = ?
                    """,
                    (int(bool(included)), upload_id, hospital_name),
                )
                if cursor.rowcount != 1:
                    raise ValueError("The selected report upload is not available.")

            for item in report_stats:
                connection.execute(
                    """
                    UPDATE raw_report_uploads
                    SET rows_read = ?, rows_used = ?
                    WHERE upload_id = ? AND hospital_name = ?
                    """,
                    (
                        int(item["rows_read"]),
                        int(item["rows_used"]),
                        item["upload_id"],
                        hospital_name,
                    ),
                )

            merged_count = _replace_raw_dataset(
                connection,
                merged_frame,
                hospital_name,
                dataset_id,
                combined_hash,
                upload_date,
            )
            if scraper_snapshot is not None:
                deleted_archive_paths = [
                    _archive_storage_path(row["storage_path"])
                    for row in connection.execute(
                        """
                        SELECT storage_path FROM scraper_archive_days
                        WHERE hospital_name = ?
                        """,
                        (hospital_name,),
                    ).fetchall()
                ]
                connection.execute(
                    "DELETE FROM scraper_archive_days WHERE hospital_name = ?",
                    (hospital_name,),
                )
                connection.execute(
                    """
                    INSERT INTO scraper_import_state (
                        hospital_name, as_of_date, cutoff_date, imported_at,
                        source_files, active_row_count, archived_row_count
                    ) VALUES (?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(hospital_name) DO UPDATE SET
                        as_of_date = excluded.as_of_date,
                        cutoff_date = excluded.cutoff_date,
                        imported_at = excluded.imported_at,
                        source_files = excluded.source_files,
                        active_row_count = excluded.active_row_count,
                        archived_row_count = 0
                    """,
                    (
                        hospital_name,
                        scraper_snapshot["as_of_date"],
                        scraper_snapshot["cutoff_date"],
                        upload_date,
                        json.dumps(
                            scraper_snapshot["source_files"],
                            ensure_ascii=True,
                        ),
                        merged_count,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            for path in created_paths:
                if path.exists():
                    path.unlink()
            raise

    with _connect() as connection:
        retained_report_paths = {
            _report_storage_path(row["storage_path"])
            for row in connection.execute(
                "SELECT storage_path FROM raw_report_uploads WHERE hospital_name = ?",
                (hospital_name,),
            ).fetchall()
        }
    cleanup_warnings = []
    for path in deleted_paths + deleted_archive_paths:
        if path in retained_report_paths:
            continue
        try:
            if path.exists():
                path.unlink()
        except OSError as error:
            cleanup_warnings.append(str(error))
    return {
        "dataset_id": dataset_id,
        "record_count": merged_count,
        "uploads": added_uploads,
        "deleted": bool(upload_ids_to_delete),
        "deleted_upload_count": len(upload_ids_to_delete),
        "cleanup_warnings": cleanup_warnings,
        "imported_at": upload_date if scraper_snapshot is not None else None,
    }


def _replace_raw_dataset(
    connection, frame, hospital_name, dataset_id, upload_hash, upload_date
):
    connection.execute(
        "DELETE FROM hospital_data WHERE dataset_id = ? AND source_file = ?",
        (dataset_id, "Combined raw reports"),
    )
    if frame is None or frame.empty:
        return 0

    existing_columns = _existing_columns(connection)
    prepared = _prepare_frame(frame.copy(), existing_columns)
    if prepared.empty or not len(prepared.columns):
        raise ValueError("The combined raw reports have no usable data columns.")
    prepared = prepared.where(pd.notna(prepared), None)
    columns = list(prepared.columns)
    _ensure_columns(connection, columns, existing_columns)
    table_columns = BASE_COLUMNS + columns
    quoted_columns = ", ".join(
        _quote_identifier(column) for column in table_columns
    )
    placeholders = ", ".join("?" for _ in table_columns)
    statement = (
        f"INSERT INTO hospital_data ({quoted_columns}) "
        f"VALUES ({placeholders})"
    )
    records = []
    for row_number, (_, row) in enumerate(prepared.iterrows()):
        values = {
            column: _normalise_for_hash(row[column])
            if row[column] is not None else None
            for column in columns
        }
        values["record_hash"] = _record_hash(
            row, columns, hospital_name, upload_hash, row_number
        )
        records.append(
            [
                None,
                hospital_name,
                "Combined raw reports",
                upload_date,
                upload_hash,
                dataset_id,
                values["record_hash"],
            ] + [values.get(column) for column in columns]
        )
    connection.executemany(statement, records)
    return len(records)


def _store_report_bytes(hospital_name, file_name, content_hash, file_bytes):
    hospital_hash = hashlib.sha256(
        str(hospital_name).strip().casefold().encode("utf-8")
    ).hexdigest()
    suffix = Path(file_name).suffix.casefold()
    if suffix not in {".xlsx", ".xls", ".csv"}:
        raise ValueError("Raw reports must be .xlsx, .xls, or .csv files.")
    target = UPLOADS_DIR / hospital_hash / f"{content_hash}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _upload_hash(target.read_bytes()) != content_hash:
            raise OSError("Stored report content does not match its content hash.")
        return target.relative_to(UPLOADS_DIR).as_posix(), False

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=target.name + ".",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(file_bytes)
        os.replace(temporary_path, target)
    except Exception:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        raise
    return target.relative_to(UPLOADS_DIR).as_posix(), True


def _report_storage_path(storage_path):
    relative = Path(storage_path)
    root = UPLOADS_DIR.resolve()
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root):
        raise ValueError("The stored report path is invalid.")
    return path


def _archive_storage_path(storage_path):
    relative = Path(storage_path)
    root = (UPLOADS_DIR / "scraper_archive").resolve()
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root):
        raise ValueError("The scraper archive path is invalid.")
    return path


def _load_scraper_archive_frames(hospital_name=None, dataset_id=None):
    init_database()
    conditions = []
    parameters = []
    if hospital_name is not None:
        conditions.append("hospital_name = ?")
        parameters.append(hospital_name)
    if dataset_id is not None:
        conditions.append("dataset_id = ?")
        parameters.append(dataset_id)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT hospital_name, storage_path FROM scraper_archive_days"
            + where + " ORDER BY event_date",
            parameters,
        ).fetchall()
    frames = []
    for row in rows:
        path = _archive_storage_path(row["storage_path"])
        frame = pd.read_parquet(path)
        if hospital_name is None and "hospital_name" not in frame:
            frame["hospital_name"] = row["hospital_name"]
        if hospital_name is None and "source_file" not in frame:
            frame["source_file"] = "Combined raw reports"
        frames.append(frame)
    return frames


def list_scraper_archive_days(hospital_name):
    init_database()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT event_date, row_count, updated_at, content_hash
            FROM scraper_archive_days
            WHERE hospital_name = ?
            ORDER BY event_date
            """,
            (hospital_name,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_scraper_import_state(hospital_name):
    init_database()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM scraper_import_state WHERE hospital_name = ?",
            (hospital_name,),
        ).fetchone()
    return dict(row) if row else None


def commit_scraper_archive_snapshot(
    hospital_name,
    active_frame,
    upload_hash,
    archive_partitions,
    *,
    as_of_date,
    cutoff_date,
    source_files,
):
    if not hospital_name or not upload_hash:
        raise ValueError("Hospital and report hash are required for scraper import.")
    init_database()
    hospital_key = hashlib.sha256(
        hospital_name.strip().casefold().encode("utf-8")
    ).hexdigest()
    archive_root = UPLOADS_DIR / "scraper_archive"
    created_paths = []
    prepared_archives = []
    previous_paths = []
    for event_date, frame in sorted(archive_partitions.items()):
        if frame.empty:
            continue
        try:
            parsed_date = datetime.strptime(event_date, "%Y-%m-%d")
        except (TypeError, ValueError) as error:
            raise ValueError("A scraper archive date is invalid.") from error
        date_parts = event_date.split("-")
        if parsed_date.strftime("%Y-%m-%d") != event_date:
            raise ValueError("A scraper archive date is invalid.")
        folder = Path(hospital_key) / date_parts[0] / date_parts[1]
        folder_path = archive_root / folder
        folder_path.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with _connect() as connection:
                previous = connection.execute(
                    """
                    SELECT storage_path FROM scraper_archive_days
                    WHERE hospital_name = ? AND event_date = ?
                    """,
                    (hospital_name, event_date),
                ).fetchone()
            archive_frame = frame
            if previous:
                previous_frame = pd.read_parquet(
                    _archive_storage_path(previous["storage_path"])
                )
                archive_frame = pd.concat(
                    [previous_frame, frame], ignore_index=True, sort=False
                ).drop_duplicates(ignore_index=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=folder_path,
                prefix=event_date + ".",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            archive_frame.to_parquet(
                temporary_path, index=False, compression="zstd"
            )
            content_hash = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
            relative_path = (
                folder / f"{event_date}-{content_hash}.parquet"
            ).as_posix()
            target = _archive_storage_path(relative_path)
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != content_hash:
                    raise OSError("Stored scraper archive does not match its hash.")
                temporary_path.unlink()
            else:
                os.replace(temporary_path, target)
                created_paths.append(target)
            prepared_archives.append({
                "event_date": event_date,
                "storage_path": relative_path,
                "content_hash": content_hash,
                "row_count": len(archive_frame),
            })
        except Exception:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
            for path in created_paths:
                if path.exists():
                    path.unlink()
            raise

    dataset_id = raw_dataset_id(hospital_name)
    upload_date = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for archive in prepared_archives:
                previous = connection.execute(
                    """
                    SELECT storage_path FROM scraper_archive_days
                    WHERE hospital_name = ? AND event_date = ?
                    """,
                    (hospital_name, archive["event_date"]),
                ).fetchone()
                if previous:
                    previous_paths.append(
                        _archive_storage_path(previous["storage_path"])
                    )
                connection.execute(
                    """
                    INSERT INTO scraper_archive_days (
                        hospital_name, event_date, dataset_id, storage_path,
                        content_hash, row_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(hospital_name, event_date) DO UPDATE SET
                        dataset_id = excluded.dataset_id,
                        storage_path = excluded.storage_path,
                        content_hash = excluded.content_hash,
                        row_count = excluded.row_count,
                        updated_at = excluded.updated_at
                    """,
                    (
                        hospital_name,
                        archive["event_date"],
                        dataset_id,
                        archive["storage_path"],
                        archive["content_hash"],
                        archive["row_count"],
                        upload_date,
                    ),
                )

            _replace_raw_dataset(
                connection,
                active_frame,
                hospital_name,
                dataset_id,
                upload_hash,
                upload_date,
            )
            archived_count = connection.execute(
                """
                SELECT COALESCE(SUM(row_count), 0)
                FROM scraper_archive_days WHERE hospital_name = ?
                """,
                (hospital_name,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO scraper_import_state (
                    hospital_name, as_of_date, cutoff_date, imported_at,
                    source_files, active_row_count, archived_row_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hospital_name) DO UPDATE SET
                    as_of_date = excluded.as_of_date,
                    cutoff_date = excluded.cutoff_date,
                    imported_at = excluded.imported_at,
                    source_files = excluded.source_files,
                    active_row_count = excluded.active_row_count,
                    archived_row_count = excluded.archived_row_count
                """,
                (
                    hospital_name,
                    as_of_date,
                    cutoff_date,
                    upload_date,
                    json.dumps(source_files, ensure_ascii=True),
                    len(active_frame),
                    int(archived_count),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            for path in created_paths:
                if path.exists():
                    path.unlink()
            raise

    referenced_paths = {
        _archive_storage_path(item["storage_path"])
        for item in prepared_archives
    }
    for path in previous_paths:
        if path not in referenced_paths and path.exists():
            path.unlink()
    return {
        "dataset_id": dataset_id,
        "active_rows": len(active_frame),
        "archived_rows": int(archived_count),
        "archived_days_added": len(prepared_archives),
        "imported_at": upload_date,
    }


def prune_scraper_report_uploads(hospital_name, keep_hashes):
    keep_hashes = set(keep_hashes)
    filename_pattern = re.compile(
        r"^(Admission|Discharge|Service)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.xlsx$",
        re.IGNORECASE,
    )
    init_database()
    deleted_paths = []
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT upload_id, file_name, content_hash, storage_path
            FROM raw_report_uploads WHERE hospital_name = ?
            """,
            (hospital_name,),
        ).fetchall()
        to_delete = [
            row for row in rows
            if filename_pattern.fullmatch(row["file_name"])
            and row["content_hash"] not in keep_hashes
        ]
        if to_delete:
            connection.executemany(
                "DELETE FROM raw_report_uploads WHERE upload_id = ?",
                [(row["upload_id"],) for row in to_delete],
            )
            connection.commit()
            deleted_paths = [
                _report_storage_path(row["storage_path"])
                for row in to_delete
            ]
    cleanup_warnings = []
    for path in deleted_paths:
        try:
            if path.exists():
                path.unlink()
        except OSError as error:
            cleanup_warnings.append(str(error))
    return {
        "deleted_uploads": len(deleted_paths),
        "cleanup_warnings": cleanup_warnings,
    }


def export_hospital_data(destination, hospital_name=None, file_format="xlsx"):
    frame = load_hospital_data(hospital_name)
    destination = Path(destination)
    if file_format.casefold() == "csv":
        frame.to_csv(destination, index=False)
    else:
        frame.to_excel(destination, index=False)
    return destination
