from __future__ import annotations

import hashlib
import itertools
import os
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import database
import dashboard_service


HOSPITAL_NAME = "NIMMS Hospital"
DEFAULT_SCRAPER_FOLDER = Path(r"C:\Users\ASUS\Desktop\nvos_to_db")
SCRAPER_FOLDER = Path(os.environ.get(
    "DASHBOARD_SCRAPER_FOLDER", str(DEFAULT_SCRAPER_FOLDER)
))
REPORT_PATTERN = re.compile(
    r"^(Admission|Discharge|Service)_(\d{4}-\d{2}-\d{2})_"
    r"(\d{2}-\d{2}-\d{2})\.xlsx$",
    re.IGNORECASE,
)
REPORT_KINDS = ("Admission", "Discharge", "Service")
MAX_BATCH_SPAN = timedelta(hours=2)
_IMPORT_LOCK = threading.Lock()


def _report_files(folder):
    if not folder.is_dir():
        return []
    files = []
    for path in folder.iterdir():
        match = REPORT_PATTERN.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        timestamp = datetime.strptime(
            f"{match.group(2)}_{match.group(3)}", "%Y-%m-%d_%H-%M-%S"
        )
        files.append({
            "path": path,
            "file_name": path.name,
            "kind": match.group(1).title(),
            "run_date": match.group(2),
            "timestamp": timestamp,
            "size": path.stat().st_size,
        })
    return sorted(files, key=lambda item: (item["timestamp"], item["file_name"]))


def _latest_complete_batch(files):
    grouped = {}
    for item in files:
        grouped.setdefault(item["run_date"], {}).setdefault(
            item["kind"], []
        ).append(item)

    for run_date in sorted(grouped, reverse=True):
        kinds = grouped[run_date]
        if not all(kind in kinds for kind in REPORT_KINDS):
            continue
        best = None
        for choices in itertools.product(
            *(kinds[kind] for kind in REPORT_KINDS)
        ):
            timestamps = [item["timestamp"] for item in choices]
            span = max(timestamps) - min(timestamps)
            if span > MAX_BATCH_SPAN:
                continue
            candidate = (max(timestamps), -span.total_seconds(), choices)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is not None:
            return list(best[2])
    return []


def _uploaded_hashes():
    return {
        item["content_hash"]
        for item in database.list_report_uploads(HOSPITAL_NAME)
    }


def get_scraper_status(folder=None):
    folder = Path(folder) if folder is not None else SCRAPER_FOLDER
    files = _report_files(folder)
    batch = _latest_complete_batch(files)
    known_hashes = _uploaded_hashes()
    batch_hashes = [
        hashlib.sha256(item["path"].read_bytes()).hexdigest()
        for item in batch
    ]
    pending_count = sum(
        content_hash not in known_hashes for content_hash in batch_hashes
    )
    state = database.get_scraper_import_state(HOSPITAL_NAME)
    archive_days = database.list_scraper_archive_days(HOSPITAL_NAME)
    candidate_date = batch[0]["run_date"] if batch else None
    needs_import = bool(batch) and (
        bool(pending_count)
        or state is None
        or candidate_date > state["as_of_date"]
        or bool(files)
    )
    return {
        "hospital": HOSPITAL_NAME,
        "folder": str(folder),
        "folderExists": folder.is_dir(),
        "reportFileCount": len(files),
        "pendingFileCount": pending_count,
        "ready": bool(batch),
        "needsImport": needs_import,
        "candidateDate": candidate_date,
        "candidateFiles": [
            {
                "fileName": item["file_name"],
                "kind": item["kind"],
                "size": item["size"],
                "imported": hashlib.sha256(
                    item["path"].read_bytes()
                ).hexdigest() in known_hashes,
            }
            for item in batch
        ],
        "lastImport": state,
        "archive": {
            "days": len(archive_days),
            "rows": sum(int(item["row_count"]) for item in archive_days),
            "from": archive_days[0]["event_date"] if archive_days else None,
            "to": archive_days[-1]["event_date"] if archive_days else None,
        },
    }


def import_scraper_folder(folder=None):
    if not _IMPORT_LOCK.acquire(blocking=False):
        raise ValueError("A scraper import is already running.")
    try:
        return _import_scraper_folder(
            Path(folder) if folder is not None else SCRAPER_FOLDER
        )
    finally:
        _IMPORT_LOCK.release()


def _import_scraper_folder(folder):
    files = _report_files(folder)
    if not files:
        raise ValueError(f"No scraper Excel reports were found in {folder}.")
    batch = _latest_complete_batch(files)
    if not batch:
        raise ValueError(
            "A complete Admission, Discharge, and Service report set was not "
            "found for the same scrape date."
        )
    source_files = [
        (item["file_name"], item["path"].read_bytes())
        for item in batch
    ]
    latest_reports = {
        (file_name, hashlib.sha256(file_bytes).hexdigest())
        for file_name, file_bytes in source_files
    }
    as_of_date = date.fromisoformat(batch[0]["run_date"])
    source_file_names = [item["file_name"] for item in batch]
    stale_upload_ids = [
        upload["upload_id"]
        for upload in database.list_report_uploads(HOSPITAL_NAME)
        if REPORT_PATTERN.fullmatch(upload["file_name"])
        and (upload["file_name"], upload["content_hash"]) not in latest_reports
    ]
    uploads_to_add = [
        {
            "file_name": item["file_name"],
            "file_bytes": file_bytes,
            "kind": item["kind"],
        }
        for item, (_, file_bytes) in zip(batch, source_files)
    ]
    rebuild_result = dashboard_service.rebuild_hospital_raw_reports(
        HOSPITAL_NAME,
        uploads_to_add=uploads_to_add,
        upload_ids_to_delete=stale_upload_ids,
        scraper_snapshot={
            "as_of_date": as_of_date.isoformat(),
            "cutoff_date": as_of_date.isoformat(),
            "source_files": source_file_names,
        },
    )

    cleanup_warnings = list(rebuild_result["cleanup_warnings"])
    removed_files = 0
    for item in files:
        try:
            item["path"].unlink()
            removed_files += 1
        except OSError as error:
            cleanup_warnings.append(
                f"Could not remove {item['file_name']}: {error}"
            )
    dashboard_service._invalidate_data_caches()
    return {
        "dataset_id": rebuild_result["dataset_id"],
        "active_rows": rebuild_result["record_count"],
        "archived_rows": 0,
        "archived_days_added": 0,
        "imported_at": rebuild_result["imported_at"],
        "sourceFiles": source_file_names,
        "sourceFilesProcessed": len(source_files),
        "sourceFilesRemoved": removed_files,
        "rawReportsPruned": rebuild_result["deleted_upload_count"],
        "cleanupWarnings": cleanup_warnings,
        "insertedRows": rebuild_result["record_count"],
        "report": rebuild_result["report"],
    }


def main():
    result = import_scraper_folder()
    print(
        "Latest scraper batch imported: "
        f"{result['sourceFilesProcessed']} source files, "
        f"{result['active_rows']} rows, "
        f"{result['rawReportsPruned']} previous reports removed."
    )
    for warning in result["cleanupWarnings"]:
        print(f"Cleanup warning: {warning}")


if __name__ == "__main__":
    main()
