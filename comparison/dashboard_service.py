from __future__ import annotations

import base64
import copy
import hashlib
from datetime import date
from io import BytesIO
from functools import lru_cache
import json
import re
import threading
import zlib
from pathlib import Path

import pandas as pd

from comparison import backend, database, report_combiner


_OFFLINE_ROW_COLUMNS = [
    "DOA", "DOD", "Type", "Month", "Dept. Name", "Doctor Name",
    "Panel Name", "Service Type", "Service Name", "Service Final Amount",
    "Cash Due", "Credit Due", "Bed Days", "Service Amount",
]
_REPORT_UPLOAD_LOCK = threading.RLock()
_KNOWN_HOSPITALS = ("NIMMS Hospital", "Surya Hospital", "Default Hospital")


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if not isinstance(value, (list, tuple, dict)):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, float) and (value == float("inf") or value == float("-inf")):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return [_json_value(item) for item in value]


def _records(frame, limit=None):
    if frame is None or frame.empty:
        return []
    rows = frame if limit is None else frame.head(limit)
    return [_json_value(record) for record in rows.to_dict(orient="records")]


def export_interactive_html():
    datasets = _dataset_metadata()
    if not datasets:
        raise ValueError("Upload a workbook before downloading the dashboard.")

    offline_datasets = []
    offline_rows = []
    for item in datasets:
        frame = backend.clean_data(
            database.load_hospital_data(dataset_id=item["dataset_id"])
        )
        date_range = backend.data_date_range(frame)
        offline_datasets.append({
            "dataset_id": item["dataset_id"],
            "source_file": item["source_file"],
            "hospital_name": item["hospital_name"],
            "upload_date": item["upload_date"],
            "record_count": len(frame),
            "date_start": _json_value(date_range[0]),
            "date_end": _json_value(date_range[1]),
            "service_type_available": bool(
                "Service Type" in frame.columns
                and frame["Service Type"].fillna("").astype(str).str.strip().ne("").any()
            ),
            "service_name_available": bool(
                "Service Name" in frame.columns
                and frame["Service Name"].fillna("").astype(str).str.strip().ne("").any()
            ),
        })
        frame = frame.reindex(columns=_OFFLINE_ROW_COLUMNS)
        for values in frame.itertuples(index=False, name=None):
            record = [len(offline_datasets) - 1]
            for column, value in zip(_OFFLINE_ROW_COLUMNS, values):
                if column in {"DOA", "DOD"}:
                    record.append(
                        value.strftime("%Y-%m-%d")
                        if pd.notna(value) else None
                    )
                elif column in {
                    "Service Final Amount", "Cash Due", "Credit Due", "Bed Days",
                    "Service Amount",
                }:
                    numeric = _json_value(float(value)) if pd.notna(value) else 0
                    record.append(numeric if numeric is not None else 0)
                else:
                    record.append(str(value) if pd.notna(value) else "")
            offline_rows.append(record)

    prediction_cache = _offline_prediction_cache(datasets)
    bundle = {
        "datasets": offline_datasets,
        "rows": offline_rows,
        "periods": backend.PERIOD_OPTIONS,
        "comparison": comparison_options(),
        "predictions": prediction_cache,
    }
    compressed = zlib.compress(
        json.dumps(
            bundle, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8"),
        level=9,
    )
    encoded_bundle = base64.b64encode(compressed).decode("ascii")

    static_dir = Path(__file__).resolve().parent / "static"
    page = (static_dir / "dashboard.html").read_text(encoding="utf-8")
    css = (static_dir / "dashboard.css").read_text(encoding="utf-8")
    dashboard_js = (static_dir / "dashboard.js").read_text(encoding="utf-8")
    offline_js = (static_dir / "offline-dashboard.js").read_text(encoding="utf-8")
    prediction_js = (static_dir / "prediction.js").read_text(encoding="utf-8")
    prediction_css = (static_dir / "prediction.css").read_text(encoding="utf-8")
    logo_path = Path(__file__).resolve().parents[1] / "static" / "vedansh-logo.png"
    logo = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    page = page.replace(
        'src="/assets/vedansh-logo.png"',
        f'src="data:image/png;base64,{logo}"',
        1,
    )
    page = re.sub(
        r'  <link rel="stylesheet" href="/assets/dashboard\.css[^"]*">\r?\n',
        f"  <style>\n{css}\n  </style>\n",
        page,
        count=1,
    )
    page = re.sub(
        r'  <script src="/assets/dashboard\.js[^"]*" defer></script>\r?\n',
        "",
        page,
        count=1,
    )
    page = re.sub(
        r'  <link rel="stylesheet" href="/assets/prediction\.css[^"]*">\r?\n',
        f"  <style>\n{prediction_css}\n  </style>\n",
        page,
        count=1,
    )
    page = re.sub(
        r'<script src="/assets/prediction\.js[^"]*" defer></script>\r?\n',
        "",
        page,
        count=1,
    )
    offline_js = offline_js.replace("</script", "<\\/script")
    dashboard_js = dashboard_js.replace("</script", "<\\/script")
    prediction_js = prediction_js.replace("</script", "<\\/script")
    scripts = (
        f'<script>window.__OFFLINE_BUNDLE__="{encoded_bundle}";</script>\n'
        f"<script>\n{offline_js}\n</script>\n"
        f"<script>\n{dashboard_js}\n</script>\n"
        f"<script>\n{prediction_js}\n</script>\n"
    )
    page = page.replace("</body>", f"{scripts}</body>")
    return page.encode("utf-8")


def _offline_prediction_cache(datasets):
    from comparison import prediction_service

    hospitals = sorted({
        str(item["hospital_name"]).strip()
        for item in datasets
        if item.get("hospital_name")
    })
    horizons = (1, 2, 3, 4, 6, 12)
    results = {}
    for hospital in hospitals:
        base = prediction_service.get_prediction({
            "hospital": hospital,
            "filters": {"predictBy": "Year", "horizon": "12"},
        })
        for horizon in horizons:
            key = json.dumps([hospital, str(horizon)], separators=(",", ":"))
            results[key] = (
                base if horizon == 12
                else _prediction_horizon_variant(base, horizon)
            )
    return results


def _prediction_horizon_variant(result, horizon):
    from comparison import prediction_service

    variant = copy.deepcopy(result)
    if not variant.get("hasData"):
        return variant
    base_horizon = variant["horizon"]
    variant["horizon"] = horizon
    mode = variant["mode"]
    for metric in variant["metrics"]:
        rows = metric["rows"]
        if mode == "Month":
            forecast_years = [
                int(row["start"][:4])
                for row in rows
                if row["forecast"] is not None
            ]
            if forecast_years:
                last_year = max(forecast_years) - (base_horizon - horizon)
                rows[:] = [
                    row for row in rows if int(row["start"][:4]) <= last_year
                ]
        else:
            forecast_rows = [row for row in rows if row["forecast"] is not None]
            if forecast_rows:
                cutoff = forecast_rows[min(horizon, len(forecast_rows)) - 1]["start"]
                rows[:] = [row for row in rows if row["start"] <= cutoff]
        history_rows = [
            row for row in rows
            if row["actual"] is not None
            and (mode != "Month" or row["focus"])
        ]
        recent_count = min(variant["points"], horizon)
        complete_history = [
            row["actual"] for row in history_rows if not row["partial"]
        ]
        history = complete_history if len(complete_history) >= recent_count else [
            row["actual"] for row in history_rows
        ]
        forecasts = [row["forecast"] for row in rows if row["forecast"] is not None]
        metric["summary"]["horizonTotal"] = sum(forecasts)
        metric["summary"]["recentTotal"] = sum(history[-horizon:])
        metric["summary"]["recentCount"] = recent_count

    metrics = {metric["key"]: metric for metric in variant["metrics"]}
    variant["summary"] = prediction_service._summary(
        horizon,
        variant["unit"],
        metrics,
        variant["services"],
        variant["confidence"],
        variant["points"],
        variant["accuracy"] / 100 if variant["accuracy"] is not None else None,
    )
    return variant


def _dataset_metadata():
    database.init_database()
    database_path = database.DATABASE_PATH.resolve()
    database_state = database_path.stat()
    return _cached_dataset_metadata(
        str(database_path), database_state.st_mtime_ns, database_state.st_size
    )


@lru_cache(maxsize=4)
def _cached_dataset_metadata(database_path, modified_ns, size):
    return database.list_saved_datasets()


def _data_version(datasets):
    return (
        str(database.DATABASE_PATH.resolve()),
        tuple(
            (
                item["dataset_id"],
                item["upload_hash"],
                int(item["record_count"]),
                str(item["upload_date"]),
            )
            for item in datasets
        ),
    )


def _dataset_frame(dataset_id):
    version = _data_version(_dataset_metadata())
    return _cached_dataset_frame(dataset_id, version).copy()


def _master_frame():
    version = _data_version(_dataset_metadata())
    return _cached_master_frame(version).copy()


@lru_cache(maxsize=16)
def _cached_dataset_frame(dataset_id, version):
    frame = backend.clean_data(database.load_hospital_data(dataset_id=dataset_id))
    frame["__comparison_event_date"] = backend.comparison_event_dates(frame)
    return frame


@lru_cache(maxsize=16)
def _cached_hospital_frame(hospital_name, version):
    frame = backend.clean_data(
        database.load_hospital_data(hospital_name=hospital_name)
    )
    frame["__comparison_event_date"] = backend.comparison_event_dates(frame)
    return frame


@lru_cache(maxsize=4)
def _cached_master_frame(version):
    frame = backend.clean_data(database.load_all_hospital_data())
    frame["__comparison_event_date"] = backend.comparison_event_dates(frame)
    return frame


def _month_names(frame, today=None):
    dates = _plausible_event_dates(frame, today)
    available = set(int(month) for month in dates.dt.month.unique())
    return [
        pd.Timestamp(2000, month, 1).strftime("%B")
        for month in range(1, 13)
        if month in available
    ]


def _plausible_event_dates(frame, today=None):
    dates = backend.comparison_event_dates(frame).dropna()
    if dates.empty:
        return dates
    reference_date = (
        pd.Timestamp.today().normalize()
        if today is None else pd.Timestamp(today).normalize()
    )
    return dates.loc[
        dates.dt.year.ge(2000)
        & dates.le(reference_date + pd.Timedelta(days=366))
    ]


def _date_picker_options(frame, today=None):
    dates = _plausible_event_dates(frame, today)
    year_months = []
    if not dates.empty:
        month_numbers = (
            pd.DataFrame({"year": dates.dt.year, "month": dates.dt.month})
            .drop_duplicates()
            .sort_values(["year", "month"])
        )
        for year, month_group in month_numbers.groupby("year", sort=True):
            year_months.append({
                "year": int(year),
                "months": [
                    {
                        "value": int(month),
                        "label": pd.Timestamp(2000, int(month), 1).strftime("%B"),
                    }
                    for month in month_group["month"]
                ],
            })
    return {
        "years": [item["year"] for item in year_months],
        "monthsByYear": year_months,
        "dateRange": {
            "start": dates.min().strftime("%Y-%m-%d") if not dates.empty else None,
            "end": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
        },
    }


def _target_for_ranges(monthly_target, ranges):
    target = 0.0
    for start, end in ranges:
        start = pd.Timestamp(start).normalize()
        end = pd.Timestamp(end).normalize()
        if end < start:
            continue
        month_start = start.replace(day=1)
        while month_start <= end:
            month_end = month_start + pd.offsets.MonthEnd(0)
            overlap_start = max(start, month_start)
            overlap_end = min(end, month_end)
            covered_days = (overlap_end - overlap_start).days + 1
            target += monthly_target * covered_days / month_end.day
            month_start += pd.offsets.MonthBegin(1)
    return target


def _dashboard_target_for_range(monthly_target, start, end, months, periods):
    target_by_period = dict.fromkeys(backend.PERIOD_OPTIONS, 0.0)
    if start is None or end is None:
        return target_by_period

    selected_months = set()
    for month in months:
        parsed = pd.to_datetime(str(month), format="%B", errors="coerce")
        if pd.isna(parsed):
            parsed = pd.to_datetime(str(month), format="%b", errors="coerce")
        if pd.notna(parsed):
            selected_months.add(parsed.month)

    selected_periods = set(periods) or set(backend.PERIOD_OPTIONS)
    period_bounds = {
        backend.PERIOD_OPTIONS[0]: (1, 10),
        backend.PERIOD_OPTIONS[1]: (11, 20),
        backend.PERIOD_OPTIONS[2]: (21, 31),
    }
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    month_start = start.replace(day=1)
    while month_start <= end:
        month_end = month_start + pd.offsets.MonthEnd(0)
        if not selected_months or month_start.month in selected_months:
            for period in selected_periods:
                first_day, last_day = period_bounds[period]
                period_start = max(
                    start,
                    month_start.replace(day=first_day),
                )
                period_end = min(
                    end,
                    month_start.replace(day=min(last_day, month_end.day)),
                )
                if period_end >= period_start:
                    target_by_period[period] += _target_for_ranges(
                        monthly_target, [(period_start, period_end)]
                    )
        month_start += pd.offsets.MonthBegin(1)
    return target_by_period


def _dashboard_target_by_period(monthly_target, start, end, months, periods):
    target_by_period = dict.fromkeys(backend.PERIOD_OPTIONS, 0.0)
    if start is None or end is None:
        target_by_period[backend.PERIOD_OPTIONS[0]] = monthly_target * 10 / 31
        target_by_period[backend.PERIOD_OPTIONS[1]] = monthly_target * 10 / 31
        target_by_period[backend.PERIOD_OPTIONS[2]] = monthly_target * 11 / 31
        return target_by_period
    allowed_months = set()
    for month in months:
        parsed = pd.to_datetime(str(month), format="%B", errors="coerce")
        if pd.isna(parsed):
            parsed = pd.to_datetime(str(month), format="%b", errors="coerce")
        if pd.notna(parsed):
            allowed_months.add(parsed.month)
    selected_periods = set(periods)
    selected_periods = selected_periods or set(backend.PERIOD_OPTIONS)
    bounds = {
        backend.PERIOD_OPTIONS[0]: (1, 10),
        backend.PERIOD_OPTIONS[1]: (11, 20),
        backend.PERIOD_OPTIONS[2]: (21, 31),
    }
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    month_start = (
        pd.Timestamp(start.year, 1, 1)
        if not allowed_months
        else start.replace(day=1)
    )
    if not allowed_months:
        end = pd.Timestamp(end.year, 12, 31)
    while month_start <= end:
        month_end = month_start + pd.offsets.MonthEnd(0)
        if not allowed_months or month_start.month in allowed_months:
            for period in selected_periods:
                first_day, last_day = bounds[period]
                covered_days = min(last_day, month_end.day) - first_day + 1
                if covered_days > 0:
                    target_by_period[period] += (
                        monthly_target * covered_days / month_end.day
                    )
        month_start += pd.offsets.MonthBegin(1)
    return target_by_period


def _dashboard_selected_days(frame, months=(), periods=(), start=None, end=None):
    if start is None or end is None:
        if "DOA" not in frame:
            return 1
        doa_dates = backend._coerce_datetime(frame["DOA"]).dropna()
        if doa_dates.empty:
            return 1
        start = doa_dates.min().normalize()
        end = doa_dates.max().normalize()
    else:
        start = pd.Timestamp(start).normalize()
        end = pd.Timestamp(end).normalize()
    selected_months = set()
    for month in months:
        parsed = pd.to_datetime(str(month), format="%B", errors="coerce")
        if pd.isna(parsed):
            parsed = pd.to_datetime(str(month), format="%b", errors="coerce")
        if pd.notna(parsed):
            selected_months.add(parsed.month)

    period_bounds = {
        backend.PERIOD_OPTIONS[0]: (1, 10),
        backend.PERIOD_OPTIONS[1]: (11, 20),
        backend.PERIOD_OPTIONS[2]: (21, 31),
    }
    selected_periods = set(periods) or set(backend.PERIOD_OPTIONS)
    selected_days = 0
    month_start = start.replace(day=1)
    while month_start <= end:
        month_end = month_start + pd.offsets.MonthEnd(0)
        if not selected_months or month_start.month in selected_months:
            for period in selected_periods:
                first_day, last_day = period_bounds[period]
                period_start = month_start + pd.Timedelta(days=first_day - 1)
                period_end = min(
                    month_end, month_start + pd.Timedelta(days=last_day - 1)
                )
                covered_start = max(start, period_start)
                covered_end = min(end, period_end)
                if covered_end >= covered_start:
                    selected_days += (covered_end - covered_start).days + 1
        month_start += pd.offsets.MonthBegin(1)
    return selected_days


def _dashboard_target_progress(frame, target_by_period, selected_periods=()):
    period_revenue = dict.fromkeys(backend.PERIOD_OPTIONS, 0.0)
    report_type = frame.get(
        "Type", pd.Series(index=frame.index, dtype="object")
    ).fillna("").astype(str).str.strip().str.casefold()
    revenue_columns = (
        ("discharge", "DOD"),
        ("opd", "DOA"),
    )
    for type_name, date_column in revenue_columns:
        if date_column not in frame or "Service Final Amount" not in frame:
            continue
        rows = frame.loc[
            report_type.eq(type_name),
            [date_column, "Service Final Amount"],
        ].copy()
        rows[date_column] = pd.to_datetime(rows[date_column], errors="coerce")
        rows = rows.dropna(subset=[date_column])
        rows["Service Final Amount"] = pd.to_numeric(
            rows["Service Final Amount"], errors="coerce"
        ).fillna(0)
        labels = pd.cut(
            rows[date_column].dt.day,
            bins=[0, 10, 20, 31],
            labels=backend.PERIOD_OPTIONS,
        )
        period_amounts = rows.groupby(
            labels, observed=True
        )["Service Final Amount"].sum()
        for period, amount in period_amounts.items():
            period_revenue[period] += float(amount)

    selected_periods = set(selected_periods)
    if selected_periods:
        return [
            {
                "label": period,
                "actual": period_revenue[period] if period in selected_periods else 0,
                "target": target_by_period[period],
            }
            for period in backend.PERIOD_OPTIONS
        ]

    actual_total = 0.0
    target_total = 0.0
    progress = []
    for period in backend.PERIOD_OPTIONS:
        actual_total += period_revenue[period]
        target_total += target_by_period[period]
        progress.append({
            "label": period,
            "actual": actual_total,
            "target": target_total,
        })
    return progress


def _hospital_scope(frame, name):
    if not name or name == "All Hospitals":
        return frame
    if "hospital_name" not in frame:
        return frame.iloc[0:0]
    return frame.loc[
        frame["hospital_name"].fillna("").astype(str).str.strip().str.casefold()
        == str(name).strip().casefold()
    ]


def dashboard_options():
    database.init_database()
    datasets = _dataset_metadata()
    dataset_hospitals = {
        item["hospital_name"]
        for item in datasets
        if item.get("hospital_name")
    }
    report_hospitals = database.list_report_hospital_names()
    hospitals = sorted(
        dataset_hospitals | set(report_hospitals) | set(_KNOWN_HOSPITALS)
    )
    result = {
        "datasets": datasets,
        "hospitals": hospitals,
        "periods": list(backend.PERIOD_OPTIONS),
    }
    default_hospital = "All Hospitals"
    if default_hospital:
        frame = database.load_comparison_date_data()
        date_options = _date_picker_options(frame)
        result["defaultHospital"] = default_hospital
        result["defaultDatasetId"] = None
        result["months"] = _month_names(frame)
        result["dateRange"] = date_options["dateRange"]
        result["dateOptions"] = date_options
    else:
        result.update(
            defaultHospital=None,
            defaultDatasetId=None,
            months=[],
            dateRange=None,
            dateOptions={
                "years": [],
                "monthsByYear": [],
                "dateRange": {"start": None, "end": None},
            },
        )

    result["comparison"] = _comparison_options_for_datasets(datasets)
    return _json_value(result)


@lru_cache(maxsize=4)
def _cached_comparison_options(version):
    frame = database.load_comparison_date_data()
    dates = _plausible_event_dates(frame)
    available_months = set(int(month) for month in dates.dt.month.unique())
    return {
        "years": sorted(int(year) for year in dates.dt.year.unique()),
        "months": [
            pd.Timestamp(2000, month, 1).strftime("%B")
            for month in range(1, 13)
            if month in available_months
        ],
        "latestDate": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
    }


def _comparison_options_for_datasets(datasets):
    return _cached_comparison_options(_data_version(datasets))


def comparison_options():
    return _json_value(_comparison_options_for_datasets(_dataset_metadata()))


def _bucket_dates(frame, column, value_column, granularity="daily"):
    if column not in frame or value_column not in frame:
        return []
    rows = frame[[column, value_column]].copy()
    rows[column] = pd.to_datetime(rows[column], errors="coerce")
    rows = rows.dropna(subset=[column])
    if rows.empty:
        return []
    if granularity == "yearly":
        rows["label"] = rows[column].dt.strftime("%Y")
    elif granularity == "monthly":
        rows["label"] = rows[column].dt.to_period("M").dt.strftime("%b %Y")
    else:
        rows["label"] = rows[column].dt.strftime("%d %b")
    grouped = rows.groupby("label", sort=False)[value_column].sum().reset_index()
    return _records(grouped.rename(columns={"label": "label", value_column: "value"}))


def _group_top(frame, dimension, value):
    if dimension not in frame or value not in frame or frame.empty:
        return []
    rows = frame[[dimension, value]].copy()
    rows[dimension] = rows[dimension].fillna("").astype(str).str.strip()
    rows[dimension] = rows[dimension].replace("", "Unknown / Not Assigned")
    rows[value] = pd.to_numeric(rows[value], errors="coerce").fillna(0)
    grouped = (
        rows.groupby(dimension, as_index=False)[value]
        .sum()
        .sort_values(value, ascending=False, kind="stable")
    )
    return _records(grouped.rename(columns={dimension: "label", value: "value"}))


def _sum_column(frame, column):
    if frame is None or frame.empty or column not in frame:
        return 0
    return _json_value(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def _sum_records(rows, column):
    return sum(
        pd.to_numeric(pd.Series([row.get(column, 0)]), errors="coerce").fillna(0).iloc[0]
        for row in rows
    )


def _dashboard_charts(frame, kpis, month_filter, monthly_target=None,
                      target_by_period=None, period_filter=(),
                      yearly_trends=False):
    trend_rows = backend.daily_revenue(frame)
    granularity = (
        "yearly" if yearly_trends else
        "monthly" if len(trend_rows) > 45 else "daily"
    )
    if not trend_rows.empty:
        trend = _bucket_dates(trend_rows, "DOD", "Revenue", granularity)
    else:
        trend = []

    flow = backend.admission_discharge(frame)
    if not flow.empty:
        flow = flow[flow["Date"].dt.year.ne(1900)].copy()
        if not flow.empty:
            flow["Date"] = pd.to_datetime(flow["Date"], errors="coerce")
            flow["label"] = (
                flow["Date"].dt.strftime("%Y")
                if granularity == "yearly"
                else flow["Date"].dt.to_period("M").dt.strftime("%b %Y")
                if granularity == "monthly"
                else flow["Date"].dt.strftime("%d %b")
            )
            flow = flow.pivot_table(
                index="label", columns="Type", values="UHID Count",
                aggfunc="sum", fill_value=0, sort=False,
            ).reset_index()
        flow_records = _records(flow)
    else:
        flow_records = []

    discharges = frame[
        frame.get("Type", pd.Series(index=frame.index, dtype="object"))
        .astype(str).str.strip().str.casefold().eq("discharge")
    ]
    discharge_breakdowns = backend.discharge_uhid_breakdown(frame)
    admissions = frame[
        frame.get("Type", pd.Series(index=frame.index, dtype="object"))
        .astype(str).str.strip().str.casefold().eq("admission")
    ]
    admission_breakdowns = backend.admission_uhid_breakdown(frame)
    ipd = backend.ipd_revenue_breakdown(frame)
    services = backend.opd_services_breakdown(frame)
    panels = backend.panel_performance_outstanding(frame)
    opd_breakdown = backend.opd_uhid_breakdown(frame)

    period_revenue = backend.period_revenue(frame, month=month_filter)
    period_discharges, _ = backend.period_discharge_counts(frame)
    if target_by_period is None:
        cumulative = backend.cumulative_revenue_vs_target(
            frame,
            target=monthly_target if monthly_target is not None else kpis["target"],
            month=month_filter,
        ).rename(columns={
            "Period": "label",
            "Achieve Revenue": "actual",
            "Target Revenue": "target",
        })
        target_progress = _records(cumulative)
    else:
        target_progress = _dashboard_target_progress(
            frame, target_by_period, period_filter
        )

    charts = {
        "revenueTrend": trend,
        "patientFlow": flow_records,
        "opdDaily": _bucket_dates(
            opd_breakdown["daily"], "Date", "UHID Count", granularity
        ),
        "dischargeDepartments": _group_top(
            discharge_breakdowns["Dept. Name"], "Dept. Name", "UHID Count"
        ),
        "dischargeDoctors": _group_top(
            discharge_breakdowns["Doctor Name"], "Doctor Name", "UHID Count"
        ),
        "admissionDepartments": _group_top(
            admission_breakdowns["Dept. Name"], "Dept. Name", "UHID Count"
        ),
        "admissionDoctors": _group_top(
            admission_breakdowns["Doctor Name"], "Doctor Name", "UHID Count"
        ),
        "ipdRevenueDepartments": _group_top(
            ipd["department"], "Name", "IPD Revenue"
        ),
        "ipdRevenueDoctors": _group_top(ipd["doctor"], "Name", "IPD Revenue"),
        "opdDoctors": _group_top(opd_breakdown["doctor"], "Doctor Name", "UHID Count"),
        "opdServices": _group_top(services, "Service Name", "Count"),
        "panels": _records(panels),
        "periodRevenue": _records(
            period_revenue.rename(columns={"Period": "label", "Revenue": "value"})
        ),
        "periodDischarges": _records(
            period_discharges.rename(columns={"Period": "label", "Count": "value"})
        ),
        "targetProgress": target_progress,
    }
    if period_filter:
        target_progress_actual = _sum_records(target_progress, "actual")
        target_progress_target = _sum_records(target_progress, "target")
    elif target_progress:
        target_progress_actual = target_progress[-1]["actual"]
        target_progress_target = target_progress[-1]["target"]
    else:
        target_progress_actual = 0
        target_progress_target = 0
    charts["totals"] = {
        "revenueTrend": {"value": _sum_records(charts["revenueTrend"], "value")},
        "patientFlow": {
            key: _sum_records(charts["patientFlow"], key)
            for key in ("Admissions", "Discharges")
        },
        "opdDaily": {"value": _sum_records(charts["opdDaily"], "value")},
        "dischargeDepartments": {
            "value": _sum_column(discharge_breakdowns["Dept. Name"], "UHID Count")
        },
        "dischargeDoctors": {
            "value": _sum_column(discharge_breakdowns["Doctor Name"], "UHID Count")
        },
        "admissionDepartments": {
            "value": _sum_column(admission_breakdowns["Dept. Name"], "UHID Count")
        },
        "admissionDoctors": {
            "value": _sum_column(admission_breakdowns["Doctor Name"], "UHID Count")
        },
        "ipdRevenueDepartments": {"value": ipd["total"]},
        "ipdRevenueDoctors": {"value": ipd["total"]},
        "opdDoctors": {
            "value": _sum_column(opd_breakdown["doctor"], "UHID Count")
        },
        "opdServices": {"value": _sum_column(services, "Count")},
        "periodRevenue": {"value": _sum_records(charts["periodRevenue"], "value")},
        "periodDischarges": {"value": _sum_records(charts["periodDischarges"], "value")},
        "targetProgress": {
            "actual": target_progress_actual,
            "target": target_progress_target,
            "monthlyTarget": monthly_target or backend.DEFAULT_TARGET,
        },
    }
    return {
        **charts,
        "insights": backend.insights(
            frame, monthly_target=monthly_target or backend.DEFAULT_TARGET
        ),
        "granularity": granularity,
        "dischargeCount": len(discharges),
        "admissionCount": len(admissions),
    }


def get_dashboard(query):
    datasets = _dataset_metadata()
    if not datasets:
        return {"hasData": False, "datasets": []}
    legacy_dataset_id = None
    hospitals = sorted({
        item["hospital_name"] for item in datasets if item.get("hospital_name")
    } | set(database.list_report_hospital_names()))
    if query.get("hospital"):
        hospital_name = str(query["hospital"]).strip()
        if hospital_name.casefold() == "all hospitals":
            hospital_name = "All Hospitals"
    elif query.get("datasetId"):
        legacy_dataset_id = query["datasetId"]
        selected_dataset = next(
            (
                item for item in datasets
                if item["dataset_id"] == legacy_dataset_id
            ),
            None,
        )
        if selected_dataset is None:
            raise ValueError("The selected dataset is not available.")
        hospital_name = selected_dataset["hospital_name"]
    else:
        hospital_name = "All Hospitals"
    if hospital_name != "All Hospitals" and hospital_name not in hospitals:
        raise ValueError("The selected hospital is not available.")
    months = query.get("month", [])
    periods = query.get("period", [])
    if isinstance(months, str):
        months = [months] if months else []
    if isinstance(periods, str):
        periods = [periods] if periods else []
    invalid_periods = set(periods) - set(backend.PERIOD_OPTIONS)
    if invalid_periods:
        raise ValueError("One or more selected periods are invalid.")
    start_date = query.get("startDate")
    end_date = query.get("endDate")
    if bool(start_date) != bool(end_date):
        raise ValueError("Both range start and end dates are required.")
    if start_date:
        try:
            start_date = pd.Timestamp(start_date).normalize()
            end_date = pd.Timestamp(end_date).normalize()
        except (TypeError, ValueError) as error:
            raise ValueError("The selected date range is invalid.") from error
        if pd.isna(start_date) or pd.isna(end_date) or end_date < start_date:
            raise ValueError("The selected date range is invalid.")
        start_date = start_date.strftime("%Y-%m-%d")
        end_date = end_date.strftime("%Y-%m-%d")
    return _get_dashboard_cached(
        hospital_name,
        tuple(months),
        tuple(periods),
        start_date or "",
        end_date or "",
        legacy_dataset_id or "",
        _data_version(datasets),
        pd.Timestamp.today().strftime("%Y-%m-%d"),
    )


@lru_cache(maxsize=16)
def _get_dashboard_cached(
    hospital_name, months, periods, start_date, end_date, dataset_id, version,
    picker_today,
):
    datasets = _dataset_metadata()
    if not datasets:
        return {"hasData": False, "datasets": []}

    selected_dataset = next(
        (
            item for item in datasets
            if dataset_id and item["dataset_id"] == dataset_id
        ),
        None,
    )
    frame = (
        _cached_dataset_frame(dataset_id, version).copy()
        if selected_dataset
        else _cached_master_frame(version).copy()
        if hospital_name == "All Hospitals"
        else _cached_hospital_frame(hospital_name, version).copy()
    )
    date_options = _date_picker_options(frame, today=picker_today)
    picker_range = date_options["dateRange"]
    min_date = (
        pd.Timestamp(picker_range["start"])
        if picker_range["start"] else None
    )
    max_date = (
        pd.Timestamp(picker_range["end"])
        if picker_range["end"] else None
    )
    if frame.empty:
        return {
            "hasData": False,
            "datasets": datasets,
            "dataset": selected_dataset or {
                "hospital_name": hospital_name,
                "source_file": "All saved data",
                "dataset_id": database.raw_dataset_id(hospital_name),
            },
            "dateRange": {"start": None, "end": None},
            "selectedRange": {"start": None, "end": None},
            "recordCount": 0,
            "filters": {"month": list(months), "period": list(periods)},
            "options": {"months": [], "periods": backend.PERIOD_OPTIONS},
            "dateOptions": date_options,
        }
    if start_date and end_date:
        selected_start = pd.Timestamp(start_date)
        selected_end = pd.Timestamp(end_date)
        if not picker_range["start"] or not picker_range["end"]:
            raise ValueError("This hospital has no dates available for range selection.")
        if selected_start < pd.Timestamp(picker_range["start"]):
            raise ValueError("The selected range starts before this hospital's data.")
        if selected_end > pd.Timestamp(picker_range["end"]):
            raise ValueError("The selected range ends after this hospital's data.")
    else:
        selected_start, selected_end = min_date, max_date

    if start_date and end_date:
        ranged = backend.filter_comparison_range(
            frame, selected_start, selected_end
        )
    else:
        ranged = frame.copy()
    filtered = backend.apply_filters(
        ranged, month=list(months), period=list(periods)
    )
    occupancy_rows = filtered.copy()
    if selected_start is not None and selected_end is not None:
        doa_dates = pd.to_datetime(
            occupancy_rows["DOA"], errors="coerce"
        )
        occupancy_rows.loc[
            ~doa_dates.between(selected_start, selected_end).fillna(False), "DOA"
        ] = pd.NaT
    kpis = backend.calculate_dashboard_metrics(occupancy_rows, hospital_name)
    monthly_target = backend.monthly_revenue_target(hospital_name)
    if not start_date and min_date is None:
        target_by_period = dict.fromkeys(backend.PERIOD_OPTIONS, 0.0)
    elif not start_date:
        target_start = (
            pd.Timestamp(picker_range["start"])
            if picker_range["start"] else min_date
        )
        target_end = (
            pd.Timestamp(picker_range["end"])
            if picker_range["end"] else max_date
        )
        target_by_period = _dashboard_target_by_period(
            monthly_target, target_start, target_end, months, periods
        )
    else:
        target_by_period = _dashboard_target_for_range(
            monthly_target, selected_start, selected_end, months, periods
        )
    kpis["target"] = sum(target_by_period.values())
    kpis["achievement"] = (
        kpis["revenue"] / kpis["target"] * 100 if kpis["target"] else 0
    )
    kpis["arpp"] = _ratio(kpis["ipd_revenue"], kpis["discharges"])
    kpis["per_day_patient_charge"] = _ratio(
        kpis["ipd_revenue"], kpis["bed_days"]
    )
    kpis["per_day_revenue"] = _ratio(
        kpis["revenue"],
        _dashboard_selected_days(
            frame,
            months,
            periods,
            selected_start,
            selected_end,
        ),
    )
    return _json_value({
        "hasData": True,
        "dataset": selected_dataset or {
                "hospital_name": hospital_name,
                "source_file": "All saved data",
                "dataset_id": database.raw_dataset_id(hospital_name),
            },
        "dateRange": {
            "start": picker_range["start"],
            "end": picker_range["end"],
        },
        "dateOptions": date_options,
        "selectedRange": {
            "start": (
                selected_start.strftime("%Y-%m-%d")
                if selected_start is not None else None
            ),
            "end": (
                selected_end.strftime("%Y-%m-%d")
                if selected_end is not None else None
            ),
        },
        "recordCount": len(filtered),
        "filters": {"month": list(months), "period": list(periods)},
        "options": {
            "months": _month_names(frame, today=picker_today),
            "periods": backend.PERIOD_OPTIONS,
        },
        "kpis": kpis,
        "charts": _dashboard_charts(
            filtered,
            kpis,
            months,
            monthly_target=monthly_target,
            target_by_period=target_by_period,
            period_filter=periods,
            yearly_trends=(
                not selected_dataset
                and not start_date
                and not months
                and not periods
            ),
        ),
    })


def _comparison_period_frame(frame, filters, side):
    mode = filters.get("compareBy", "Year")
    if mode == "Year":
        return backend.filter_dashboard_comparison(
            frame,
            int(filters[f"year{side}"]),
            month=filters.get("month"),
        )
    if mode == "Month":
        month_name = filters[f"month{side}"]
        return backend.filter_dashboard_comparison(
            frame, int(filters["year"]), month=month_name
        )
    if mode == "Last Months":
        start, end = _comparison_target_range(filters, side)
        return backend.filter_comparison_range(frame, start, end)
    if mode == "Week":
        return backend.filter_dashboard_comparison(
            frame, int(filters["year"]), month=filters["month"],
            week=int(filters[f"week{side}"]),
        )
    if mode == "Period":
        return backend.filter_dashboard_comparison(
            frame, int(filters["year"]), month=filters["month"],
            period=filters[f"period{side}"],
        )
    if mode == "Date":
        selected_date = pd.Timestamp(filters[f"date{side}"])
        return backend.filter_dashboard_comparison(
            frame, selected_date.year, month=selected_date.strftime("%B"),
            day=selected_date.day,
        )
    if mode == "From-To":
        start = pd.Timestamp(filters[f"start{side}"])
        end = pd.Timestamp(filters[f"end{side}"])
        return backend.filter_comparison_range(frame, start, end)
    raise ValueError("Unsupported comparison type.")


def warm_up():
    """Load and cache the data used by the first page view, off the request path."""
    try:
        dashboard_options()
        comparison_options()
        _master_frame()
    except Exception:  # warming is only an optimisation
        pass


def _ratio(numerator, denominator):
    try:
        return float(numerator) / float(denominator) if denominator else 0.0
    except (TypeError, ValueError):
        return 0.0


def _comparison_period_days(filters, frames=None):
    """Days in each selected range, stopping at the last day that has data."""
    days = []
    for index, side in enumerate(("1", "2")):
        start, end = _comparison_target_range(filters, side)
        rows = frames[index] if frames is not None else None
        if rows is not None and not rows.empty:
            dates = pd.to_datetime(
                backend.comparison_event_dates(rows), errors="coerce"
            ).dropna()
            if not dates.empty:
                end = min(end, dates.max().normalize())
        days.append(max((end - start).days + 1, 1))
    return days


def _comparison_period_ranges(filters):
    return [
        [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]
        for start, end in (
            _comparison_target_range(filters, "1"),
            _comparison_target_range(filters, "2"),
        )
    ]


def _comparison_metric_rows(left_kpis, right_kpis, period_days=None):
    metric_keys = [
        ("Revenue", "revenue", "currency"),
        ("IPD revenue", "ipd_revenue", "currency"),
        ("OPD revenue", "opd_revenue", "currency"),
        ("Revenue target", "target", "currency"),
        ("Target achievement", "achievement", "percent"),
        ("Admissions", "admissions", "count"),
        ("Discharges", "discharges", "count"),
        ("OPD consultations", "opd_consultation", "count"),
        ("Occupancy", "occupancy", "percent"),
        ("Occupied bed days", "bed_days", "count"),
        ("Average stay", "alos", "days"),
        ("Cash due", "cash_due", "currency"),
        ("Credit due", "credit_due", "currency"),
    ]
    result = []
    for title, key, kind in metric_keys:
        first, second = left_kpis[key], right_kpis[key]
        change = backend.comparison_change(second, first)
        result.append({
            "label": title,
            "first": first,
            "second": second,
            "change": backend.comparison_label(change),
            "changeValue": change,
            "kind": kind,
        })
    days = list(period_days or [0, 0])
    derived = [
        ("IPD ARPP", "currency", lambda k, d: _ratio(k["ipd_revenue"], k["discharges"])),
        ("Per day revenue", "currency", lambda k, d: _ratio(k["revenue"], d)),
        ("Per day per patient charge", "currency",
         lambda k, d: _ratio(k["ipd_revenue"], k["bed_days"])),
    ]
    for title, kind, calculate in derived:
        first = calculate(left_kpis, days[0])
        second = calculate(right_kpis, days[1])
        change = backend.comparison_change(second, first)
        result.append({
            "label": title,
            "first": first,
            "second": second,
            "change": backend.comparison_label(change),
            "changeValue": change,
            "kind": kind,
        })
    return result


def _comparison_summary_data(metrics, mover_data, period_days):
    def values_for(label):
        metric = next((item for item in metrics if item["label"] == label), {})
        if "values" in metric:
            return metric["values"]
        return [metric.get("first", 0), metric.get("second", 0)]

    def endpoints(values):
        values = list(values or [])
        if not values:
            return 0, 0
        return float(values[0] or 0), float(values[-1] or 0)

    total_revenue = endpoints(values_for("Revenue"))
    ipd_revenue = endpoints(values_for("IPD revenue"))
    opd_revenue = endpoints(values_for("OPD revenue"))
    discharges = endpoints(values_for("Discharges"))
    credit_due = endpoints(values_for("Credit due"))
    average_ipd = tuple(
        revenue / count if count else 0
        for revenue, count in zip(ipd_revenue, discharges)
    )
    if discharges[0]:
        discharge_effect = (discharges[1] - discharges[0]) * average_ipd[0]
        bill_effect = discharges[1] * (average_ipd[1] - average_ipd[0])
    else:
        discharge_effect = ipd_revenue[1] - ipd_revenue[0]
        bill_effect = 0

    collected = tuple(
        max(0, revenue - due)
        for revenue, due in zip(ipd_revenue, credit_due)
    )
    collection_rates = tuple(
        amount / revenue * 100 if revenue else 0
        for amount, revenue in zip(collected, ipd_revenue)
    )
    net_ipd_change = ipd_revenue[1] - ipd_revenue[0]
    biggest_movers = {}
    for dimension in ("departments", "doctors", "panels"):
        candidates = []
        for row in mover_data.get("ipdRevenue", {}).get(dimension, []):
            row_values = row.get("values") or []
            first = float(row_values[0] or 0) if row_values else 0
            last = float(row_values[-1] or 0) if row_values else 0
            change = last - first
            if change:
                candidates.append((abs(change), row.get("name") or "Unknown", change))
        if candidates:
            _, name, change = max(candidates)
            biggest_movers[dimension] = {
                "name": name,
                "change": change,
                "netChangePercent": (
                    change / net_ipd_change * 100 if net_ipd_change else None
                ),
            }
        else:
            biggest_movers[dimension] = None

    return {
        "periodDays": list(endpoints(period_days)),
        "totalRevenue": list(total_revenue),
        "ipdRevenue": list(ipd_revenue),
        "opdRevenue": list(opd_revenue),
        "discharges": list(discharges),
        "averageIpdRevenuePerDischarge": list(average_ipd),
        "ipdDischargeEffect": discharge_effect,
        "ipdBillPerDischargeEffect": bill_effect,
        "creditDue": list(credit_due),
        "collected": list(collected),
        "collectionRate": list(collection_rates),
        "biggestIpdRevenueMovers": biggest_movers,
    }


def _comparison_breakdown(first, second, labels, dimension, report_type,
                          value_column=None, extra_filter=None,
                          consultation_only=False):
    grouped = []
    for frame in (first, second):
        rows = frame.copy()
        if consultation_only:
            rows = backend.opd_consultation_records(rows)
        elif "Type" not in rows:
            rows = rows.iloc[0:0]
        else:
            rows = rows.loc[
                rows["Type"].fillna("").astype(str).str.strip().str.casefold()
                == report_type.casefold()
            ]
        if extra_filter is not None:
            rows = rows.loc[extra_filter(rows)]
        if rows.empty:
            grouped.append({})
            continue

        names = (
            rows[dimension].fillna("").astype(str).str.strip()
            if dimension in rows
            else pd.Series("", index=rows.index)
        ).replace("", "Unknown")
        values = (
            pd.to_numeric(rows[value_column], errors="coerce").fillna(0)
            if value_column and value_column in rows
            else pd.Series(1, index=rows.index, dtype="int64")
        )
        grouped.append(values.groupby(names).sum().to_dict())

    rows = []
    for name in sorted(set(grouped[0]) | set(grouped[1])):
        left, right = grouped[0].get(name, 0), grouped[1].get(name, 0)
        rows.append({
            dimension: name,
            labels[0]: left,
            labels[1]: right,
            "Change": backend.comparison_change(right, left),
        })
    rows.sort(key=lambda item: item[labels[1]], reverse=True)
    return rows


def _comparison_panel_rows(first, second, labels):
    panels = []
    for frame in (first, second):
        breakdown = backend.panel_performance_outstanding(frame)
        panels.append({
            row["Panel"]: row
            for row in _records(breakdown)
        })
    result = []
    for name in sorted(set(panels[0]) | set(panels[1])):
        left = panels[0].get(name, {})
        right = panels[1].get(name, {})
        left_revenue = left.get("IPD Revenue", 0)
        right_revenue = right.get("IPD Revenue", 0)
        left_credit = left.get("Credit Due", 0)
        right_credit = right.get("Credit Due", 0)
        result.append({
            "Panel": name,
            f"{labels[0]} IPD revenue": left_revenue,
            f"{labels[1]} IPD revenue": right_revenue,
            "Revenue change": backend.comparison_change(right_revenue, left_revenue),
            f"{labels[0]} credit due": left_credit,
            f"{labels[1]} credit due": right_credit,
            "Credit change": backend.comparison_change(right_credit, left_credit),
        })
    result.sort(key=lambda item: item[f"{labels[1]} IPD revenue"], reverse=True)
    return result


def _comparison_target_range(filters, side):
    mode = filters.get("compareBy", "Year")
    if mode == "Year":
        year = int(filters[f"year{side}"])
        month = filters.get("month")
        if month and month != "All Months":
            month_number = pd.to_datetime(month, format="%B").month
            start = pd.Timestamp(year, month_number, 1)
            return start, start + pd.offsets.MonthEnd(0)
        return pd.Timestamp(year, 1, 1), pd.Timestamp(year, 12, 31)
    if mode == "Month":
        year = int(filters["year"])
        month_number = pd.to_datetime(filters[f"month{side}"], format="%B").month
        start = pd.Timestamp(year, month_number, 1)
        return start, start + pd.offsets.MonthEnd(0)
    if mode == "Last Months":
        month_count = int(filters["monthsCount"])
        month_number = pd.to_datetime(filters["endMonth"], format="%B").month
        selected_month = pd.Timestamp(int(filters["endYear"]), month_number, 1)
        recent_start = selected_month - pd.DateOffset(months=month_count - 1)
        if side == "2":
            return recent_start, selected_month + pd.offsets.MonthEnd(0)
        previous_end = recent_start - pd.Timedelta(days=1)
        return (
            previous_end.replace(day=1) - pd.DateOffset(months=month_count - 1),
            previous_end,
        )
    if mode == "Week":
        year = int(filters["year"])
        month_number = pd.to_datetime(filters["month"], format="%B").month
        month_start = pd.Timestamp(year, month_number, 1)
        week = int(filters[f"week{side}"])
        return (
            month_start + pd.Timedelta(days=(week - 1) * 7),
            min(
                month_start + pd.Timedelta(days=week * 7 - 1),
                month_start + pd.offsets.MonthEnd(0),
            ),
        )
    if mode == "Period":
        year = int(filters["year"])
        month_number = pd.to_datetime(filters["month"], format="%B").month
        month_start = pd.Timestamp(year, month_number, 1)
        first_day, last_day = {
            backend.PERIOD_OPTIONS[0]: (1, 10),
            backend.PERIOD_OPTIONS[1]: (11, 20),
            backend.PERIOD_OPTIONS[2]: (21, month_start.days_in_month),
        }[filters[f"period{side}"]]
        return (
            month_start.replace(day=first_day),
            month_start.replace(day=last_day),
        )
    if mode == "Date":
        selected_date = pd.Timestamp(filters[f"date{side}"]).normalize()
        return selected_date, selected_date
    return (
        pd.Timestamp(filters[f"start{side}"]),
        pd.Timestamp(filters[f"end{side}"]),
    )


def _comparison_month_range_label(start, end):
    return f"{start:%b %Y} – {end:%b %Y}"


def _comparison_service_rows(first, second, labels):
    grouped = []
    for frame in (first, second):
        services = backend.opd_services_breakdown(frame)
        grouped.append(dict(zip(services["Service Name"], services["Count"])))
    result = []
    for name in sorted(set(grouped[0]) | set(grouped[1])):
        left, right = grouped[0].get(name, 0), grouped[1].get(name, 0)
        result.append({
            "Service": name,
            labels[0]: left,
            labels[1]: right,
            "Change": backend.comparison_change(right, left),
        })
    result.sort(key=lambda item: item[labels[1]], reverse=True)
    return result


def _comparison_revenue_trend(first, second, mode, filters):
    month_labels = None
    if mode == "Last Months":
        month_count = int(filters["monthsCount"])
        recent_start, _ = _comparison_target_range(filters, "2")
        month_labels = [
            (recent_start + pd.DateOffset(months=index)).strftime("%b %Y")
            for index in range(month_count)
        ]

    def values(frame, side):
        if "Type" not in frame or "DOD" not in frame:
            return {}
        rows = frame.loc[
            frame["Type"].fillna("").astype(str).str.strip().str.casefold().eq("discharge")
            & frame["DOD"].notna()
        ]
        if rows.empty:
            return {}
        dates = pd.to_datetime(rows["DOD"], errors="coerce")
        amounts = pd.to_numeric(
            rows.get("Service Final Amount", pd.Series(0, index=rows.index)),
            errors="coerce",
        ).fillna(0)
        if mode == "Year":
            keys = dates.dt.strftime("%b")
            order = [pd.Timestamp(2000, month, 1).strftime("%b") for month in range(1, 13)]
        elif mode == "Month":
            keys = dates.dt.day.map(
                lambda day: backend.PERIOD_OPTIONS[min((int(day) - 1) // 10, 2)]
            )
            order = list(backend.PERIOD_OPTIONS)
        elif mode == "Last Months":
            start, _ = _comparison_target_range(filters, side)
            keys = (dates.dt.year - start.year) * 12 + dates.dt.month - start.month
            grouped = amounts.groupby(keys).sum().to_dict()
            return {
                month_labels[index]: float(grouped.get(index, 0))
                for index in range(len(month_labels))
            }
        elif mode == "Week":
            keys = dates.dt.day.map(lambda day: f"Week {(int(day) - 1) // 7 + 1}")
            order = [f"Week {week}" for week in range(1, 6)]
        elif mode == "Period":
            key = filters[f"period{side}"]
            return {key: float(amounts.sum())}
        elif mode == "From-To":
            keys = dates.dt.strftime("%b %Y")
            order = sorted(
                keys.dropna().unique(),
                key=lambda key: pd.to_datetime(key, format="%b %Y"),
            )
        else:
            selected_date = filters.get(f"date{side}", "")
            return {str(selected_date): float(amounts.sum())}
        grouped = amounts.groupby(keys).sum().to_dict()
        return {key: float(grouped.get(key, 0)) for key in order}

    left, right = values(first, "1"), values(second, "2")
    labels = list(dict.fromkeys([*left.keys(), *right.keys()]))
    return {
        "title": {
            "Year": "IPD revenue trend by month",
            "Month": "IPD revenue trend by billing period",
            "Last Months": "IPD revenue trend by month",
            "Week": "IPD revenue by week",
            "Period": "IPD revenue by billing period",
            "Date": "IPD revenue on selected dates",
            "From-To": "IPD revenue trend by month",
        }.get(mode, "IPD revenue trend"),
        "rows": [
            {"label": label, "first": left.get(label, 0), "second": right.get(label, 0)}
            for label in labels
        ],
        "totals": [sum(left.values()), sum(right.values())],
    }


def _comparison_count_trend(first, second, mode, filters, kind, title_name):
    """Counts per month (Year, all months) or per day (everything else)."""
    ranges = [
        _comparison_target_range(filters, "1"),
        _comparison_target_range(filters, "2"),
    ]
    monthly = mode == "Year" and filters.get("month") in (None, "", "All Months")
    long_range = mode == "Last Months" or (
        mode == "From-To" and max(
            (end - start).days + 1 for start, end in ranges
        ) > 62
    )

    def select(frame):
        if frame.empty or "Type" not in frame:
            return frame.iloc[0:0]
        if kind == "opd":
            return backend.opd_consultation_records(frame)
        report_type = frame["Type"].fillna("").astype(str).str.strip().str.casefold()
        return frame.loc[report_type.eq("discharge" if kind == "revenue" else kind)]

    def counts(frame, start):
        rows = select(frame)
        if rows.empty:
            return {}
        dates = pd.to_datetime(
            backend.comparison_event_dates(rows), errors="coerce"
        )
        valid = dates.notna()
        dates = dates[valid]
        if dates.empty:
            return {}
        if monthly:
            keys = dates.dt.month
        elif long_range:
            keys = (dates.dt.year - start.year) * 12 + dates.dt.month - start.month + 1
        else:
            keys = (dates.dt.normalize() - start.normalize()).dt.days + 1
        if kind == "revenue":
            amounts = pd.to_numeric(
                rows.get("Service Final Amount", pd.Series(0, index=rows.index)),
                errors="coerce",
            ).fillna(0)[valid]
            return {int(key): float(value) for key, value in amounts.groupby(keys).sum().items()}
        return {int(key): int(value) for key, value in keys.value_counts().items()}

    left = counts(first, ranges[0][0])
    right = counts(second, ranges[1][0])
    if monthly:
        order = list(range(1, 13))
        labels = {
            number: pd.Timestamp(2000, number, 1).strftime("%b") for number in order
        }
        granularity = "month"
    else:
        if long_range:
            size = max(
                ((end.year - start.year) * 12 + end.month - start.month + 1)
                for start, end in ranges
            )
            granularity = "month"
        else:
            size = max((end - start).days + 1 for start, end in ranges)
            granularity = "day"
        order = list(range(1, size + 1))
        if mode == "Last Months":
            labels = {
                number: (
                    ranges[1][0] + pd.DateOffset(months=number - 1)
                ).strftime("%b %Y")
                for number in order
            }
        else:
            labels = {
                number: (f"Month {number}" if long_range else str(number))
                for number in order
            }
    return {
        "title": f"{title_name} trend by {granularity}",
        "granularity": granularity,
        "rows": [
            {
                "label": labels[number],
                "first": left.get(number, 0),
                "second": right.get(number, 0),
            }
            for number in order
        ],
        "totals": [sum(left.values()), sum(right.values())],
    }


def _opd_service_mask(rows):
    column = "Service Type" if "Service Type" in rows else "Service Name"
    if column not in rows:
        return pd.Series(False, index=rows.index)
    values = rows[column].fillna("").astype(str).str.strip()
    return values.ne("") & values.str.casefold().ne("consultation")


def _comparison_chart_rows(first_rows, second_rows):
    def values(rows):
        return {
            row.get("label"): row.get("value", 0)
            for row in rows or []
            if row.get("label") is not None
        }

    first_values, second_values = values(first_rows), values(second_rows)
    rows = [
        {
            "label": label,
            "first": first_values.get(label, 0),
            "second": second_values.get(label, 0),
        }
        for label in sorted(set(first_values) | set(second_values))
    ]
    return sorted(
        rows,
        key=lambda row: max(row["first"], row["second"]),
        reverse=True,
    )[:10]


def _comparison_mover_rows(rows, dimension, labels, value_columns=None):
    return [
        {
            "name": row.get(dimension) or "Unknown",
            "values": [
                row.get(value_columns[index] if value_columns else label, 0)
                for index, label in enumerate((labels[0], labels[-1]))
            ],
        }
        for row in rows or []
    ]


def _comparison_chart_totals(first_charts, second_charts):
    chart_keys = (
        "dischargeDepartments",
        "dischargeDoctors",
        "admissionDepartments",
        "admissionDoctors",
        "ipdRevenueDepartments",
        "ipdRevenueDoctors",
        "opdDoctors",
        "opdServices",
    )
    totals = {
        key: [
            first_charts["totals"][key]["value"],
            second_charts["totals"][key]["value"],
        ]
        for key in chart_keys
    }
    totals["panels"] = [
        first_charts["totals"]["ipdRevenueDepartments"]["value"],
        second_charts["totals"]["ipdRevenueDepartments"]["value"],
    ]
    return totals


def get_comparison(query):
    datasets = _dataset_metadata()
    filters = query.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError("Comparison filters must be a JSON object.")
    version = _data_version(datasets)
    if filters.get("compareBy") == "Period" and filters.get("period"):
        return _comparison_period_breakdown(
            query.get("hospital", "All Hospitals"), filters, version
        )
    serialized_filters = json.dumps(
        filters, sort_keys=True, separators=(",", ":")
    )
    return _get_comparison_cached(
        query.get("hospital", "All Hospitals"),
        serialized_filters,
        version,
    )


def _comparison_period_breakdown(selected_hospital, filters, version):
    if filters["period"] != "All Periods" and filters["period"] not in backend.PERIOD_OPTIONS:
        raise ValueError("Select a valid billing period.")
    selected = (
        backend.PERIOD_OPTIONS
        if filters["period"] == "All Periods"
        else [filters["period"]]
    )
    results = []
    for period in selected:
        period_filters = {
            **filters,
            "period1": period,
            "period2": period,
        }
        result = _get_comparison_cached(
            selected_hospital,
            json.dumps(period_filters, sort_keys=True, separators=(",", ":")),
            version,
        )
        results.append((period, result if result.get("hasData") else None))
    available_results = [
        result for _, result in results if result is not None
    ]
    if not available_results:
        return {
            "hasData": False,
            "message": "No records found for the selected billing period.",
        }

    labels = [period for period, _ in results]
    series_keys = ("first", "second", "third")
    base_result = available_results[0]

    def merge_chart(chart_name):
        merged = {}
        for index, (_, result) in enumerate(results):
            if result is None:
                continue
            for row in result["charts"].get(chart_name, []):
                merged.setdefault(row["label"], {})[
                    series_keys[index]
                ] = row.get("first", 0)
        rows = [
            {
                "label": label,
                **{key: values.get(key, 0) for key in series_keys[:len(labels)]},
            }
            for label, values in merged.items()
        ]
        return sorted(
            rows,
            key=lambda row: max(
                (row.get(key, 0) for key in ("first", "second", "third")),
                default=0,
            ),
            reverse=True,
        )[:10]

    def merge_named_rows(rows_key, dimension, value_for_row):
        merged = {}
        for index, (period, result) in enumerate(results):
            if result is None:
                continue
            for row in result.get(rows_key, []):
                name = row.get(dimension)
                if name:
                    merged.setdefault(name, {})[period] = value_for_row(
                        row, period, result
                    )
        return [
            {
                dimension: name,
                **{period: values.get(period, 0) for period in labels},
            }
            for name, values in merged.items()
        ]

    metrics_by_label = {
        metric["label"]: [
            result["metrics"][index]["first"] if result is not None else 0
            for _, result in results
        ]
        for index, metric in enumerate(base_result["metrics"])
    }
    metric_metadata = {
        metric["label"]: metric
        for metric in base_result["metrics"]
    }
    metrics = [
        {
            "label": label,
            "kind": metric_metadata[label]["kind"],
            "values": values,
        }
        for label, values in metrics_by_label.items()
    ]
    chart_keys = base_result["chartTotals"]
    chart_totals = {
        key: [
            result["chartTotals"].get(key, [0])[0] if result is not None else 0
            for _, result in results
        ]
        for key in chart_keys
    }
    trend_totals = [
        next(
            (metric["first"] for metric in result["metrics"]
             if metric["label"] == "IPD revenue"),
            0,
        ) if result is not None else 0
        for _, result in results
    ]
    management_insights = {}
    panel_rows = {}
    for period, result in results:
        if result is None:
            management_insights[period] = {}
            continue
        management_insights[period] = result["managementInsights"].get(
            result["labels"][0], {}
        )
        for row in result.get("panels", []):
            name = row.get("Panel")
            if name:
                panel = panel_rows.setdefault(name, {
                    "Panel": name,
                    **{
                        f"{label} {suffix}": 0
                        for label in labels
                        for suffix in ("IPD revenue", "credit due")
                    },
                })
                child_label = result["labels"][0]
                panel[f"{period} IPD revenue"] = row.get(
                    f"{child_label} IPD revenue", 0
                )
                panel[f"{period} credit due"] = row.get(
                    f"{child_label} credit due", 0
                )

    mover_data = {}
    for metric_key, dimensions in (base_result.get("moverData") or {}).items():
        mover_data[metric_key] = {}
        for dimension, _ in dimensions.items():
            values_by_name = {}
            for index, (_, result) in enumerate(results):
                if result is None:
                    continue
                result_rows = (
                    result.get("moverData", {})
                    .get(metric_key, {})
                    .get(dimension, [])
                )
                for row in result_rows:
                    name = row.get("name")
                    if not name:
                        continue
                    values = values_by_name.setdefault(name, [0] * len(labels))
                    source_values = row.get("values", [])
                    values[index] = source_values[0] if source_values else 0
            mover_data[metric_key][dimension] = [
                {"name": name, "values": values}
                for name, values in values_by_name.items()
            ]

    def merge_trend(name):
        period_mode = filters.get("compareBy") == "Period"
        if period_mode:
            month_start = pd.Timestamp(
                int(filters["year"]),
                pd.to_datetime(filters["month"], format="%B").month,
                1,
            )
            order = [str(day) for day in range(1, month_start.days_in_month + 1)]
        else:
            order = []
        values = {}
        title = ""
        for index, (_, result) in enumerate(results):
            if result is None:
                continue
            trend = (result.get("trends") or {}).get(name) or {}
            title = title or trend.get("title", "")
            for row in trend.get("rows", []):
                label = row["label"]
                if period_mode:
                    period_filter = {
                        **filters,
                        "period1": labels[index],
                        "period2": labels[index],
                    }
                    start, _ = _comparison_target_range(period_filter, "1")
                    label = str(start.day + int(label) - 1)
                if label not in order:
                    order.append(label)
                values.setdefault(label, {})[series_keys[index]] = row.get(
                    "first", 0
                )
        order.sort(key=lambda label: int(label) if str(label).isdigit() else 0)
        return {
            "title": title,
            "granularity": "day",
            "rows": [
                {
                    "label": label,
                    **{
                        key: values.get(label, {}).get(key, 0)
                        for key in series_keys[:len(labels)]
                    },
                }
                for label in order
            ],
            "totals": [
                ((result.get("trends") or {}).get(name) or {}).get("totals", [0])[0]
                if result is not None else 0
                for _, result in results
            ],
        }

    period_ranges = []
    for period, result in results:
        if result is not None and result.get("periodRanges"):
            period_ranges.append(result["periodRanges"][0])
            continue
        period_filters = {**filters, "period1": period, "period2": period}
        start, end = _comparison_target_range(period_filters, "1")
        period_ranges.append([start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")])

    period_days = []
    for period, result in results:
        if result is not None and result.get("summary"):
            period_days.append(result["summary"]["periodDays"][0])
            continue
        period_filters = {**filters, "period1": period, "period2": period}
        start, end = _comparison_target_range(period_filters, "1")
        period_days.append((end - start).days + 1)
    return _json_value({
        "hasData": True,
        "hospital": selected_hospital,
        "labels": labels,
        "recordCounts": [
            result["recordCounts"][0] if result is not None else 0
            for _, result in results
        ],
        "metrics": metrics,
        "periodRanges": period_ranges,
        "monthlyTarget": base_result.get("monthlyTarget", 0),
        "trends": {
            name: merge_trend(name)
            for name in ("admissions", "discharges", "opdConsultations", "revenue")
        },
        "summary": _comparison_summary_data(metrics, mover_data, period_days),
        "chartTotals": chart_totals,
        "revenueTrend": {
            "title": "IPD revenue by billing period",
            "rows": [{
                "label": "IPD revenue",
                **{
                    key: trend_totals[index]
                    for index, key in enumerate(series_keys[:len(labels)])
                },
            }],
            "totals": trend_totals,
        },
        "charts": {
            key: merge_chart(key)
            for key in base_result["charts"]
        },
        "departments": merge_named_rows(
            "departments", "Department",
            lambda row, period, result: row.get(result["labels"][0], 0),
        ),
        "doctors": merge_named_rows(
            "doctors", "Doctor",
            lambda row, period, result: row.get(result["labels"][0], 0),
        ),
        "panels": list(panel_rows.values()),
        "moverData": mover_data,
        "services": merge_named_rows(
            "services", "Service",
            lambda row, period, result: row.get(result["labels"][0], 0),
        ),
        "managementInsights": management_insights,
        "insights": [],
    })


@lru_cache(maxsize=12)
def _get_comparison_cached(selected_hospital, serialized_filters, version):
    frame = _master_frame()
    if frame.empty:
        return {"hasData": False}
    available_hospitals = set(
        frame["hospital_name"].dropna().astype(str).str.strip().unique()
    ) if "hospital_name" in frame else set()
    if selected_hospital != "All Hospitals" and selected_hospital not in available_hospitals:
        raise ValueError("The selected hospital is not available.")
    scoped = _hospital_scope(frame, selected_hospital)
    filters = json.loads(serialized_filters)
    mode = filters.get("compareBy", "Year")
    required_fields = {
        "Year": ("year1", "year2"),
        "Month": ("year", "month1", "month2"),
        "Last Months": ("endYear", "endMonth", "monthsCount"),
        "Week": ("year", "month", "week1", "week2"),
        "Period": ("year", "month", "period1", "period2"),
        "Date": ("date1", "date2"),
        "From-To": ("start1", "end1", "start2", "end2"),
    }
    if mode not in required_fields:
        raise ValueError("Choose a supported comparison type.")
    missing_fields = [
        field for field in required_fields[mode]
        if filters.get(field) in (None, "")
    ]
    if missing_fields:
        raise ValueError("Choose both comparison periods before comparing.")
    if mode == "Period":
        invalid_periods = {
            filters[field] for field in ("period1", "period2")
            if filters[field] not in backend.PERIOD_OPTIONS
        }
        if invalid_periods:
            raise ValueError("One or more selected periods are invalid.")
    if mode == "Last Months":
        try:
            month_count = int(filters["monthsCount"])
        except (TypeError, ValueError) as error:
            raise ValueError("Choose a 3-, 4-, or 6-month comparison period.") from error
        if month_count not in (3, 4, 6):
            raise ValueError("Choose a 3-, 4-, or 6-month comparison period.")
        valid_months = {
            pd.Timestamp(2000, month, 1).strftime("%B")
            for month in range(1, 13)
        }
        if filters["endMonth"] not in valid_months:
            raise ValueError("Choose a valid ending month.")
    if mode == "Week":
        for field in ("week1", "week2"):
            if int(filters[field]) not in range(1, 6):
                raise ValueError("Week values must be between 1 and 5.")
    first = _comparison_period_frame(scoped, filters, "1")
    second = _comparison_period_frame(scoped, filters, "2")
    if first.empty or second.empty:
        empty_periods = [
            label for label, period_frame in zip(
                ("first", "second"), (first, second)
            ) if period_frame.empty
        ]
        return {
            "hasData": False,
            "message": "No records found for the " + " and ".join(empty_periods) + " comparison period.",
        }
    if mode == "Year":
        labels = [
            str(filters["year1"]) if filters.get("month") in (None, "", "All Months")
            else f"{filters['month']} {filters['year1']}",
            str(filters["year2"]) if filters.get("month") in (None, "", "All Months")
            else f"{filters['month']} {filters['year2']}",
        ]
    elif mode == "Month":
        labels = [filters["month1"], filters["month2"]]
    elif mode == "Last Months":
        labels = [
            _comparison_month_range_label(*_comparison_target_range(filters, side))
            for side in ("1", "2")
        ]
    elif mode == "Week":
        labels = [f"Week {filters['week1']}", f"Week {filters['week2']}"]
    elif mode == "Period":
        labels = [filters["period1"], filters["period2"]]
    elif mode == "Date":
        labels = [filters["date1"], filters["date2"]]
    else:
        labels = ["First period", "Second period"]
    if labels[0] == labels[1]:
        labels = [f"{labels[0]} · Period 1", f"{labels[1]} · Period 2"]

    if selected_hospital == "NIMMS Hospital":
        beds = 50
    elif selected_hospital == "All Hospitals":
        beds = 205
    else:
        beds = backend.TOTAL_BEDS
    monthly_target = backend.monthly_revenue_target(selected_hospital)
    left_target = _target_for_ranges(
        monthly_target, [_comparison_target_range(filters, "1")]
    )
    right_target = _target_for_ranges(
        monthly_target, [_comparison_target_range(filters, "2")]
    )
    left_kpis = backend.calculate_kpis(first, beds, left_target)
    right_kpis = backend.calculate_kpis(second, beds, right_target)
    left_charts = _dashboard_charts(
        first, left_kpis, filters.get("month"), monthly_target
    )
    right_charts = _dashboard_charts(
        second, right_kpis, filters.get("month"), monthly_target
    )

    left_dept = backend.ipd_revenue_breakdown(first)["department"]
    right_dept = backend.ipd_revenue_breakdown(second)["department"]
    department_rows = backend.comparison_table_rows(
        left_dept, right_dept, "Department", period_labels=labels
    )
    left_doctor = backend.ipd_revenue_breakdown(first)["doctor"]
    right_doctor = backend.ipd_revenue_breakdown(second)["doctor"]
    doctor_rows = backend.comparison_table_rows(
        left_doctor, right_doctor, "Doctor", period_labels=labels
    )
    admission_department_rows = _comparison_breakdown(
        first, second, labels, "Dept. Name", "Admission"
    )
    admission_doctor_rows = _comparison_breakdown(
        first, second, labels, "Doctor Name", "Admission"
    )
    discharge_department_rows = _comparison_breakdown(
        first, second, labels, "Dept. Name", "Discharge"
    )
    discharge_doctor_rows = _comparison_breakdown(
        first, second, labels, "Doctor Name", "Discharge"
    )
    discharge_panel_rows = _comparison_breakdown(
        first, second, labels, "Panel Name", "Discharge"
    )
    admission_panel_rows = _comparison_breakdown(
        first, second, labels, "Panel Name", "Admission"
    )
    opd_doctor_rows = _comparison_breakdown(
        first, second, labels, "Doctor Name", "OPD", consultation_only=True
    )
    opd_department_rows = _comparison_breakdown(
        first, second, labels, "Dept. Name", "OPD", consultation_only=True
    )
    opd_panel_rows = _comparison_breakdown(
        first, second, labels, "Panel Name", "OPD", consultation_only=True
    )

    if not department_rows.empty:
        result_column = next(
            (column for column in department_rows if str(column).casefold() == "change"),
            None,
        )
        if result_column:
            department_rows = department_rows.sort_values(
                result_column, ascending=False, key=lambda values: pd.to_numeric(values, errors="coerce")
            )
    comparison_metrics = _comparison_metric_rows(
        left_kpis, right_kpis, _comparison_period_days(filters, [first, second])
    )
    opd_service_mover_rows = {
        dimension_key: _comparison_mover_rows(
            _comparison_breakdown(
                first, second, labels, column, "OPD",
                extra_filter=_opd_service_mask,
            ),
            column,
            labels,
        )
        for dimension_key, column in (
            ("departments", "Dept. Name"),
            ("doctors", "Doctor Name"),
            ("panels", "Panel Name"),
        )
    }
    mover_data = {
        "opdServices": opd_service_mover_rows,
        "ipdRevenue": {
            "departments": _comparison_mover_rows(
                _records(department_rows), "Department", labels
            ),
            "doctors": _comparison_mover_rows(
                _records(doctor_rows), "Doctor", labels
            ),
            "panels": _comparison_mover_rows(
                _comparison_panel_rows(first, second, labels),
                "Panel",
                labels,
                [f"{label} IPD revenue" for label in (labels[0], labels[-1])],
            ),
        },
        "ipdDischarges": {
            "departments": _comparison_mover_rows(
                discharge_department_rows, "Dept. Name", labels
            ),
            "doctors": _comparison_mover_rows(
                discharge_doctor_rows, "Doctor Name", labels
            ),
            "panels": _comparison_mover_rows(
                discharge_panel_rows, "Panel Name", labels
            ),
        },
        "ipdAdmissions": {
            "departments": _comparison_mover_rows(
                admission_department_rows, "Dept. Name", labels
            ),
            "doctors": _comparison_mover_rows(
                admission_doctor_rows, "Doctor Name", labels
            ),
            "panels": _comparison_mover_rows(
                admission_panel_rows, "Panel Name", labels
            ),
        },
        "opdConsultations": {
            "departments": _comparison_mover_rows(
                opd_department_rows, "Dept. Name", labels
            ),
            "doctors": _comparison_mover_rows(
                opd_doctor_rows, "Doctor Name", labels
            ),
            "panels": _comparison_mover_rows(
                opd_panel_rows, "Panel Name", labels
            ),
        },
    }
    period_days = _comparison_period_days(filters)
    chart_totals = _comparison_chart_totals(left_charts, right_charts)
    return _json_value({
        "hasData": True,
        "hospital": selected_hospital,
        "labels": labels,
        "recordCounts": [len(first), len(second)],
        "periodRanges": _comparison_period_ranges(filters),
        "monthlyTarget": monthly_target,
        "trends": {
            "admissions": _comparison_count_trend(
                first, second, mode, filters, "admission", "IPD admission"
            ),
            "discharges": _comparison_count_trend(
                first, second, mode, filters, "discharge", "IPD discharge"
            ),
            "opdConsultations": _comparison_count_trend(
                first, second, mode, filters, "opd", "OPD consultation"
            ),
            "revenue": _comparison_count_trend(
                first, second, mode, filters, "revenue", "IPD revenue"
            ),
        },
        "metrics": comparison_metrics,
        "summary": _comparison_summary_data(
            comparison_metrics, mover_data, period_days
        ),
        "chartTotals": chart_totals,
        "departments": _records(department_rows.head(12)),
        "doctors": _records(doctor_rows.head(12)),
        "revenueTrend": _comparison_revenue_trend(first, second, mode, filters),
        "charts": {
            "dischargeDepartments": _comparison_chart_rows(
                left_charts["dischargeDepartments"],
                right_charts["dischargeDepartments"],
            ),
            "dischargeDoctors": _comparison_chart_rows(
                left_charts["dischargeDoctors"], right_charts["dischargeDoctors"]
            ),
            "admissionDepartments": _comparison_chart_rows(
                left_charts["admissionDepartments"],
                right_charts["admissionDepartments"],
            ),
            "admissionDoctors": _comparison_chart_rows(
                left_charts["admissionDoctors"], right_charts["admissionDoctors"]
            ),
            "ipdRevenueDepartments": _comparison_chart_rows(
                left_charts["ipdRevenueDepartments"],
                right_charts["ipdRevenueDepartments"],
            ),
            "ipdRevenueDoctors": _comparison_chart_rows(
                left_charts["ipdRevenueDoctors"],
                right_charts["ipdRevenueDoctors"],
            ),
            "opdDoctors": _comparison_chart_rows(
                left_charts["opdDoctors"], right_charts["opdDoctors"]
            ),
            "panels": _comparison_chart_rows(
                [
                    {"label": row["Panel"], "value": row["IPD Revenue"]}
                    for row in left_charts["panels"]
                ],
                [
                    {"label": row["Panel"], "value": row["IPD Revenue"]}
                    for row in right_charts["panels"]
                ],
            ),
            "services": _comparison_chart_rows(
                left_charts["opdServices"], right_charts["opdServices"]
            ),
        },
        "dischargeDepartments": discharge_department_rows,
        "dischargeDoctors": discharge_doctor_rows,
        "admissionDepartments": admission_department_rows,
        "admissionDoctors": admission_doctor_rows,
        "opdDoctors": opd_doctor_rows,
        "opdDepartments": opd_department_rows,
        "panels": _comparison_panel_rows(first, second, labels),
        "services": _comparison_service_rows(first, second, labels),
        "moverData": mover_data,
        "managementInsights": {
            labels[0]: backend.insights(first, monthly_target=monthly_target),
            labels[1]: backend.insights(second, monthly_target=monthly_target),
        },
        "insights": [
            {
                "label": metric["label"],
                "message": (
                    f"{labels[1]} is {metric['change']} versus {labels[0]}."
                    if metric["change"] not in {"0.0%", "N/A"}
                    else f"{metric['label']} is broadly unchanged between periods."
                ),
                "positive": (
                    isinstance(metric["changeValue"], (int, float))
                    and metric["changeValue"] > 0
                    and metric["label"] not in {"Occupancy", "Average stay"}
                ),
            }
            for metric in _comparison_metric_rows(left_kpis, right_kpis)[:4]
        ],
    })


def export_comparison_csv(query):
    result = get_comparison(query)
    if not result.get("hasData"):
        raise ValueError(result.get("message") or "No data is available for the selected comparison.")

    labels = result.get("labels") or ["Period 1", "Period 2"]
    rows = []

    def comparison_row(section, label, values, change=None):
        row = {"Section": section, "Label": label}
        row.update({
            period: values[index] if index < len(values) else None
            for index, period in enumerate(labels)
        })
        if change is not None:
            row["Change"] = change
        return row

    for metric in result.get("metrics", []):
        values = metric.get("values") or [
            metric.get("first"), metric.get("second")
        ]
        rows.append(comparison_row(
            "Metric", metric.get("label", ""), values, metric.get("change")
        ))
    for department in result.get("departments", []):
        rows.append(comparison_row(
            "Department",
            department.get("Department", department.get("Doctor", "")),
            [department.get(period) for period in labels],
            department.get("Change"),
        ))
    for doctor in result.get("doctors", []):
        rows.append(comparison_row(
            "Doctor",
            doctor.get("Doctor", ""),
            [doctor.get(period) for period in labels],
            doctor.get("Change"),
        ))
    for section, key, dimension in [
        ("Discharges by department", "dischargeDepartments", "Dept. Name"),
        ("Discharges by doctor", "dischargeDoctors", "Doctor Name"),
        ("Admissions by department", "admissionDepartments", "Dept. Name"),
        ("Admissions by doctor", "admissionDoctors", "Doctor Name"),
        ("OPD consultations by doctor", "opdDoctors", "Doctor Name"),
        ("OPD services", "services", "Service"),
    ]:
        for item in result.get(key, []):
            rows.append(comparison_row(
                section,
                item.get(dimension, ""),
                [item.get(period) for period in labels],
                item.get("Change"),
            ))
    for item in result.get("revenueTrend", {}).get("rows", []):
        values = [
            item.get(key)
            for key in ("first", "second", "third")[:len(labels)]
        ]
        rows.append(comparison_row(
            "IPD revenue trend", item.get("label", ""), values
        ))
    for item in result.get("panels", []):
        rows.extend([
            comparison_row(
                "Panel IPD revenue", item.get("Panel", ""),
                [item.get(f"{period} IPD revenue") for period in labels],
                item.get("Revenue change"),
            ),
            comparison_row(
                "Panel credit due", item.get("Panel", ""),
                [item.get(f"{period} credit due") for period in labels],
                item.get("Credit change"),
            ),
        ])
    for insight, key in [
        ("Leading revenue department", "department"),
        ("Top revenue doctor", "doctor"),
        ("Leading panel", "panel"),
        ("Highest IPD revenue day", "day"),
    ]:
        periods = result.get("managementInsights", {})
        rows.append(comparison_row(
            "Management insight",
            insight,
            [periods.get(period, {}).get(key) for period in labels],
        ))

    if not rows:
        return b"Section,Label,Period 1,Period 2,Change\n"

    frame = pd.DataFrame(rows)
    return frame.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def upload_excel(filename, file_bytes, hospital_name=None):
    if not filename or not filename.lower().endswith((".xlsx", ".xls")):
        raise ValueError("Upload an Excel workbook (.xlsx or .xls).")
    if not file_bytes:
        raise ValueError("The selected workbook is empty.")
    try:
        raw = pd.read_excel(BytesIO(file_bytes))
    except (ValueError, ImportError, OSError) as error:
        raise ValueError(f"Could not read the Excel workbook: {error}") from error
    cleaned = backend.clean_data(raw)
    missing = backend.validate_columns(cleaned)
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    if cleaned.empty:
        raise ValueError("The uploaded workbook contains no records.")

    hospital_name = _hospital_for_upload(filename, hospital_name)
    result = database.save_hospital_data(
        cleaned, hospital_name, filename, file_bytes
    )
    result["hospital_name"] = hospital_name
    return result


def _hospital_for_upload(filename, hospital_name=None):
    if hospital_name is not None:
        selected = str(hospital_name).strip()
        if not selected:
            raise ValueError("Choose a hospital for this upload.")
        if len(selected) > 200:
            raise ValueError("Hospital names cannot exceed 200 characters.")
        return selected
    filename_lower = filename.casefold()
    if "nimms" in filename_lower:
        return "NIMMS Hospital"
    if "surya" in filename_lower:
        return "Surya Hospital"
    return "Default Hospital"


def upload_files(files, hospital_name=None):
    """Save merged workbooks through the existing path and rebuild raw reports."""
    if not files:
        raise ValueError("Choose at least one report to upload.")
    if any(not file_bytes for _, file_bytes in files):
        raise ValueError("One or more selected reports are empty.")

    selected_hospital = _hospital_for_upload(files[0][0], hospital_name)
    incoming_report = report_combiner.inspect_reports(files, include_frames=True)
    raw_files = []
    merged_files = []
    merged_results = []
    response_report = []

    for (filename, file_bytes), entry in zip(files, incoming_report):
        parsed_frame = entry.pop("_frame", None)
        report_item = dict(entry)
        if entry["detected_kind"] is not None:
            raw_files.append({
                "index": len(response_report),
                "file_name": filename,
                "file_bytes": file_bytes,
                "kind": entry["detected_kind"],
                "rows_read": entry["rows_read"],
                "rows_used": 0,
                "parsed_frame": parsed_frame,
            })
        elif _is_merged_workbook(filename, file_bytes, parsed_frame):
            merged_files.append({
                "filename": filename,
                "file_bytes": file_bytes,
                "report_item": report_item,
            })
        response_report.append(report_item)

    raw_result = None
    if raw_files:
        raw_result = _upload_raw_reports(selected_hospital, raw_files, response_report)

    for item in merged_files:
        result = upload_excel(
            item["filename"], item["file_bytes"], selected_hospital
        )
        merged_results.append(result)
        item["report_item"]["detected_kind"] = "Merged"
        item["report_item"]["rows_read"] = result["total"]
        item["report_item"]["rows_used"] = result["inserted"]
        item["report_item"]["message"] = (
            "Saved through the existing merged-workbook upload path."
            if result["inserted"] else "This merged workbook is already saved."
        )

    if not raw_result and not merged_results:
        messages = [
            f"{item['file_name']}: {item['message']}"
            for item in response_report
        ]
        raise ValueError(
            "No recognized raw reports or valid merged workbook were uploaded. "
            + " ".join(messages)
        )

    inserted = sum(item["inserted"] for item in merged_results)
    if raw_result and raw_result["new_upload_count"]:
        inserted += raw_result["record_count"]
    total = (
        (raw_result["record_count"] if raw_result else 0)
        + sum(item["total"] for item in merged_results)
    )
    dataset_id = (
        raw_result["dataset_id"] if raw_result
        else merged_results[-1]["dataset_id"]
    )
    public_raw_result = None
    if raw_result:
        public_raw_result = {
            key: value for key, value in raw_result.items()
            if key not in {"report_uploads", "included_uploads"}
        }
    return {
        "inserted": inserted,
        "total": total,
        "duplicate": (
            raw_result["new_upload_count"] == 0
            and all(item["duplicate"] for item in merged_results)
            if raw_result
            else all(item["duplicate"] for item in merged_results)
        ),
        "dataset_id": dataset_id,
        "hospital_name": selected_hospital,
        "upload_hash": raw_result["combined_hash"] if raw_result else (
            merged_results[-1]["upload_hash"]
        ),
        "report": response_report,
        "raw_dataset": public_raw_result,
        "merged_uploads": merged_results,
    }


def _is_merged_workbook(filename, file_bytes, parsed_frame=None):
    if Path(filename).suffix.casefold() not in {".xlsx", ".xls"}:
        return False
    if parsed_frame is None:
        try:
            parsed_frame = report_combiner.read_raw_file(filename, file_bytes)
        except ValueError:
            return False
    cleaned = backend.clean_data(parsed_frame.copy())
    required = {"Type", "DOA", "DOD", "Dept. Name", "Doctor Name", "Panel Name"}
    return required.issubset(cleaned.columns) and not backend.validate_columns(cleaned)


def _combined_report_hash(hospital_name, uploads):
    payload = json.dumps({
        "hospital": hospital_name.strip().casefold(),
        "reports": sorted(item["content_hash"] for item in uploads),
    }, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rebuild_hospital_raw_reports(
    hospital_name,
    *,
    uploads_to_add=(),
    upload_to_delete=None,
    upload_ids_to_delete=(),
    include_change=None,
    parsed_frames=None,
    scraper_snapshot=None,
):
    upload_ids_to_delete = tuple(dict.fromkeys(upload_ids_to_delete))
    if upload_to_delete:
        upload_ids_to_delete = tuple(dict.fromkeys(
            (*upload_ids_to_delete, upload_to_delete)
        ))
    if upload_ids_to_delete and include_change is not None:
        raise ValueError("A report upload cannot be deleted and toggled together.")
    selected_hospital = _hospital_for_upload("", hospital_name)
    with _REPORT_UPLOAD_LOCK:
        existing_uploads = database.load_report_uploads(
            selected_hospital, included_only=False
        )
        deleted_ids = set(upload_ids_to_delete)
        proposed = [
            item for item in existing_uploads
            if item["upload_id"] not in deleted_ids
        ]
        if include_change is not None:
            target_id, included = include_change
            target = next(
                (item for item in proposed if item["upload_id"] == target_id),
                None,
            )
            if target is None:
                raise ValueError("The selected report upload is not available.")
            target["included"] = bool(included)

        new_uploads = list(uploads_to_add)
        known_hashes = {item["content_hash"] for item in proposed}
        staged = []
        for item in new_uploads:
            content_hash = hashlib.sha256(item["file_bytes"]).hexdigest()
            if content_hash in known_hashes:
                continue
            known_hashes.add(content_hash)
            staged.append({
                **item,
                "content_hash": content_hash,
                "included": True,
                "upload_id": None,
            })
        proposed.extend(staged)

        included_uploads = [item for item in proposed if item["included"]]
        merged_frame, combined_report = report_combiner.combine_reports([
            (item["file_name"], item["file_bytes"])
            for item in included_uploads
        ], parsed_frames=parsed_frames)
        if scraper_snapshot is not None and merged_frame.empty:
            raise ValueError("The scraper reports did not produce dashboard rows.")
        report_stats = []
        staged_by_hash = {item["content_hash"]: item for item in staged}
        for item, result in zip(included_uploads, combined_report):
            item["rows_read"] = result["rows_read"]
            item["rows_used"] = result["rows_used"]
            if item["upload_id"] is not None:
                report_stats.append({
                    "upload_id": item["upload_id"],
                    "rows_read": result["rows_read"],
                    "rows_used": result["rows_used"],
                })
            else:
                staged_by_hash[item["content_hash"]]["rows_read"] = result["rows_read"]
                staged_by_hash[item["content_hash"]]["rows_used"] = result["rows_used"]

        for item in proposed:
            if not item["included"] and item["upload_id"] is not None:
                report_stats.append({
                    "upload_id": item["upload_id"],
                    "rows_read": item["rows_read"],
                    "rows_used": 0,
                })

        combined_hash = _combined_report_hash(selected_hospital, included_uploads)
        result = database.commit_report_upload_changes(
            selected_hospital,
            merged_frame,
            combined_hash,
            uploads_to_add=staged,
            upload_ids_to_delete=upload_ids_to_delete,
            include_change=include_change,
            report_stats=report_stats,
            scraper_snapshot=scraper_snapshot,
        )
        result["combined_hash"] = combined_hash
        result["new_upload_count"] = sum(
            not item["duplicate"] for item in result["uploads"]
        )
        result["report"] = combined_report
        result["report_uploads"] = proposed
        result["included_uploads"] = included_uploads
        _invalidate_data_caches()
        return result


def _upload_raw_reports(hospital_name, incoming_files, response_report):
    with _REPORT_UPLOAD_LOCK:
        existing_uploads = database.load_report_uploads(
            hospital_name, included_only=False
        )
        known_hashes = {item["content_hash"] for item in existing_uploads}
        new_uploads = []
        parsed_frames = {}
        for item in incoming_files:
            content_hash = hashlib.sha256(item["file_bytes"]).hexdigest()
            if content_hash in known_hashes:
                response_report[item["index"]]["rows_used"] = 0
                response_report[item["index"]]["message"] = (
                    "This report's content is already uploaded and was skipped."
                )
                continue
            known_hashes.add(content_hash)
            new_uploads.append(item)
            if item["parsed_frame"] is not None:
                parsed_frames[content_hash] = item["parsed_frame"]

        if not new_uploads:
            dataset_id = database.raw_dataset_id(hospital_name)
            dataset = next(
                (
                    item for item in _dataset_metadata()
                    if item["dataset_id"] == dataset_id
                ),
                None,
            )
            return {
                "dataset_id": dataset_id,
                "record_count": dataset["record_count"] if dataset else 0,
                "new_upload_count": 0,
                "combined_hash": _combined_report_hash(
                    hospital_name,
                    [item for item in existing_uploads if item["included"]],
                ),
                "report": [],
            }

        result = rebuild_hospital_raw_reports(
            hospital_name,
            uploads_to_add=new_uploads,
            parsed_frames=parsed_frames,
        )
        report_by_hash = {
            upload["content_hash"]: report_item
            for upload, report_item in zip(
                result["included_uploads"], result["report"]
            )
        }
        for item in new_uploads:
            content_hash = hashlib.sha256(item["file_bytes"]).hexdigest()
            matching = report_by_hash.get(content_hash)
            if matching is not None:
                target = response_report[item["index"]]
                target["rows_used"] = matching["rows_used"]
                target["message"] = matching["message"]

        result["report"] = response_report
        return result


def list_report_uploads(hospital_name):
    return database.list_report_uploads(
        _hospital_for_upload("", hospital_name)
    )


def delete_report_upload(upload_id):
    upload = database.get_report_upload(upload_id)
    result = rebuild_hospital_raw_reports(
        upload["hospital_name"],
        upload_to_delete=upload_id,
    )
    return _public_rebuild_result(result)


def set_report_upload_included(upload_id, included):
    if not isinstance(included, bool):
        raise ValueError("The included value must be true or false.")
    upload = database.get_report_upload(upload_id)
    result = rebuild_hospital_raw_reports(
        upload["hospital_name"],
        include_change=(upload_id, included),
    )
    return _public_rebuild_result(result)


def _public_rebuild_result(result):
    return {
        key: value for key, value in result.items()
        if key not in {"report_uploads", "included_uploads"}
    }


def _invalidate_data_caches():
    for cache in (
        _cached_dataset_metadata,
        _cached_dataset_frame,
        _cached_hospital_frame,
        _cached_master_frame,
        _get_dashboard_cached,
        _cached_comparison_options,
        _get_comparison_cached,
    ):
        cache.cache_clear()