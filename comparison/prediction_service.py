"""Forecasting for the Comparison dashboard.

Engine modes
------------
Year    every month of data, drawn year-on-year; projected month by month
Month   the chosen month taken from every year; projected to future years
Recent  the last N complete months only; projected from that short trend
Period  the chosen billing period (1-10 / 11-20 / 21-31) from every month

Method: least-squares linear trend (+ seasonal index for Year mode with 24+
months), 80% prediction band. Incomplete buckets are left out of the fit; the
bucket that is still running is projected from its daily run-rate so far.

`get_comparison_forecast` is the single entry point used by the server: it
returns the forecast AND the like-for-like comparison used by the breakdown
charts (previous year same dates, previous N months, ...).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache

import numpy as np
import pandas as pd

from comparison import backend, dashboard_service

MAX_HORIZON = 12
CONFIDENCE_Z = 1.2816  # 80% interval
_HISTORY_LIMIT = {"Year": 60, "Month": 10, "Period": 24}
_ENGINE_MODES = ("Year", "Month", "Recent", "Period")

_MONTHS = [pd.Timestamp(2000, m, 1).strftime("%B") for m in range(1, 13)]
_PERIOD_BOUNDS = {
    backend.PERIOD_OPTIONS[0]: (1, 10),
    backend.PERIOD_OPTIONS[1]: (11, 20),
    backend.PERIOD_OPTIONS[2]: (21, 31),
}

# key, label, kind, higher-is-better
_METRICS = [
    ("admissions", "IPD admissions", "count", True),
    ("discharges", "IPD discharges", "count", True),
    ("opd", "OPD consultations", "count", True),
    ("revenue", "Total revenue", "currency", True),
    ("services", "OPD services (excl. consultation)", "count", True),
    ("ipd_revenue", "IPD revenue", "currency", True),
    ("opd_revenue", "OPD revenue", "currency", True),
    ("credit_due", "Credit due", "currency", False),
]


# --------------------------------------------------------------------------
# Data preparation
# --------------------------------------------------------------------------
def _events(frame):
    """One row per record: event date plus one numeric column per metric."""
    frame = frame.reset_index(drop=True)
    if "__comparison_event_date" in frame:
        dates = frame["__comparison_event_date"]
    else:
        dates = backend.comparison_event_dates(frame)
    dates = pd.to_datetime(dates, errors="coerce")
    blank = pd.Series("", index=frame.index)

    def number(column):
        if column not in frame:
            return pd.Series(0.0, index=frame.index)
        return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

    types = (frame["Type"] if "Type" in frame else blank).fillna("").astype(str)
    types = types.str.strip().str.casefold()
    admission, discharge, opd = (
        types.eq("admission"), types.eq("discharge"), types.eq("opd")
    )
    name_column = next(
        (column for column in ("Service Name", "Service Type") if column in frame),
        None,
    )
    names = frame[name_column].fillna("").astype(str).str.strip() if name_column else blank
    type_column = "Service Type" if "Service Type" in frame else name_column
    kinds = (
        frame[type_column].fillna("").astype(str).str.strip().str.casefold()
        if type_column else blank
    )
    consultation = opd & kinds.eq("consultation")
    service_indexes = set(backend.opd_service_records(frame).index)
    service = pd.Series(frame.index.isin(service_indexes), index=frame.index)
    amount = number("Service Final Amount")

    def label(column):
        if column not in frame:
            return pd.Series("Unknown", index=frame.index)
        return frame[column].fillna("").astype(str).str.strip().replace("", "Unknown")

    events = pd.DataFrame({
        "date": dates,
        "department": label("Dept. Name"),
        "doctor": label("Doctor Name"),
        "panel": label("Panel Name"),
        "admissions": admission.astype(float),
        "discharges": discharge.astype(float),
        "opd": consultation.astype(float),
        "services": service.astype(float),
        "revenue": amount.where(discharge | opd, 0.0),
        "ipd_revenue": amount.where(discharge, 0.0),
        "opd_revenue": amount.where(opd, 0.0),
        "bed_days": number("Bed Days").where(discharge, 0.0),
        "credit_due": number("Credit Due").where(discharge, 0.0),
        "service_name": names.where(service, ""),
    })
    return events.loc[events["date"].notna() & events["date"].dt.year.ge(2000)]


def _assign_buckets(events, mode, month_number, period):
    """Bucket id `bx`: the year (Month mode) or year*12+month-1 (all others)."""
    if mode == "Month":
        events = events.loc[events["date"].dt.month.eq(month_number)].copy()
        events["bx"] = events["date"].dt.year
        return events
    if mode == "Period":
        first, last = _PERIOD_BOUNDS[period]
        events = events.loc[events["date"].dt.day.between(first, last)].copy()
    else:
        events = events.copy()
    events["bx"] = events["date"].dt.year * 12 + events["date"].dt.month - 1
    return events


def _bucket_info(mode, x, month_number, period):
    """(start, end, label) of a bucket."""
    x = int(x)
    if mode == "Month":
        start = pd.Timestamp(x, month_number, 1)
        return start, start + pd.offsets.MonthEnd(0), start.strftime("%b %Y")
    year, month0 = divmod(x, 12)
    month_start = pd.Timestamp(year, month0 + 1, 1)
    month_end = month_start + pd.offsets.MonthEnd(0)
    if mode == "Period":
        first, last = _PERIOD_BOUNDS[period]
        start = month_start.replace(day=first)
        end = month_start.replace(day=min(last, month_end.day))
        return start, end, start.strftime("%b %Y")
    return month_start, month_end, month_start.strftime("%b %Y")


# --------------------------------------------------------------------------
# Forecast engine
# --------------------------------------------------------------------------
def _forecast(xs, ys, future_xs, seasonal=False):
    """[(value, lower, upper), ...] for each future x."""
    if not future_xs:
        return []
    n = len(xs)
    if n == 0:
        return [(0.0, 0.0, 0.0) for _ in future_xs]
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    if n == 1:
        value = float(y[0])
        return [(value, value * 0.8, value * 1.2) for _ in future_xs]

    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    season = np.ones(12)
    if seasonal and n >= 24:
        ratio = np.divide(y, fitted, out=np.full(n, np.nan), where=fitted > 0)
        keys = x.astype(int) % 12
        for key in range(12):
            values = ratio[keys == key]
            values = values[~np.isnan(values)]
            if len(values):
                season[key] = values.mean()
        season = season / season.mean()
        fitted = fitted * season[x.astype(int) % 12]

    residual = y - fitted
    if n > 2:
        sigma = math.sqrt(float((residual ** 2).sum()) / (n - 2))
    else:
        sigma = 0.2 * abs(float(y.mean()))
    x_mean = float(x.mean())
    sxx = float(((x - x_mean) ** 2).sum())
    ceiling = 3 * float(y.max()) if y.max() > 0 else None

    result = []
    for fx in future_xs:
        value = max(0.0, (slope * fx + intercept) * season[int(fx) % 12])
        if ceiling is not None:
            value = min(value, ceiling)
        spread = 1 + 1 / n + ((fx - x_mean) ** 2 / sxx if sxx else 0)
        half = CONFIDENCE_Z * sigma * math.sqrt(spread)
        result.append((float(value), float(max(0.0, value - half)), float(value + half)))
    return result


# --------------------------------------------------------------------------
# Growth-aware forecasting: several models compete, the one that would have
# predicted the most recent months best is used.
# --------------------------------------------------------------------------
def _linear_values(xs, ys, targets, weights=None):
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    if len(x) == 1:
        return [float(y[0])] * len(targets)
    if weights is None:
        slope, intercept = np.polyfit(x, y, 1)
    else:
        slope, intercept = np.polyfit(x, y, 1, w=np.sqrt(weights))
    return [float(max(0.0, slope * t + intercept)) for t in targets]


def _yoy_values(xs, ys, targets):
    """Same month last year x the recent year-on-year growth (growth fades over time)."""
    lookup = dict(zip(xs, ys))
    last = xs[-1]
    pairs = [(lookup[x], lookup[x - 12]) for x in xs[-6:] if (x - 12) in lookup]
    if len(pairs) < 3:
        return None
    current, earlier = sum(a for a, _ in pairs), sum(b for _, b in pairs)
    if earlier <= 0:
        return None
    growth = min(1.8, max(0.6, current / earlier))
    result = []
    for target in targets:
        years_ahead = max(1, math.ceil((target - last) / 12))
        base = target - 12 * years_ahead
        if base not in lookup:
            return None
        value = lookup[base]
        for step in range(years_ahead):
            value *= 1 + (growth - 1) * (0.6 ** step)
        result.append(max(0.0, value))
    return result


def _single_model(model, xs, ys, targets):
    n = len(xs)
    if model == "trend":
        return [item[0] for item in _forecast(xs, ys, targets, seasonal=n >= 24)]
    if model == "weighted":        # recent months count more
        if n < 4:
            return None
        age = xs[-1] - np.asarray(xs, dtype=float)
        return _linear_values(xs, ys, targets, weights=0.5 ** (age / 6.0))
    if model == "recent":          # only the latest 12 months
        if n < 8:
            return None
        return _linear_values(xs[-12:], ys[-12:], targets)
    if model == "yoy":
        return _yoy_values(xs, ys, targets)
    return None


def _model_values(model, xs, ys, targets):
    if model != "blend":
        return _single_model(model, xs, ys, targets)
    parts = [
        values for values in (_single_model(name, xs, ys, targets)
                              for name in ("trend", "weighted", "recent", "yoy"))
        if values is not None
    ]
    if not parts:
        return None
    return [float(np.mean(column)) for column in zip(*parts)]


_MODEL_NAMES = ("weighted", "recent", "yoy", "trend", "blend")


def _backtest(model, xs, ys, origins=8, steps=3, min_train=6):
    """Pretend to forecast the last months; return (WAPE, RMSE, points) or None."""
    n = len(xs)
    errors, actual_total, count = [], 0.0, 0
    for train in range(max(min_train, n - origins), n):
        targets = xs[train:train + steps]
        if not targets:
            continue
        predicted = _model_values(model, xs[:train], ys[:train], targets)
        if predicted is None:
            continue
        for value, actual in zip(predicted, ys[train:train + steps]):
            errors.append(value - actual)
            actual_total += abs(actual)
            count += 1
    if count < 3 or actual_total <= 0:
        return None
    errors = np.asarray(errors)
    return float(np.abs(errors).sum() / actual_total), float(np.sqrt((errors ** 2).mean())), count


def _smart_forecast(xs, ys, future_xs):
    """([(value, lower, upper), ...], model name, typical error as a fraction or None)."""
    xs, ys = list(xs), [float(value) for value in ys]
    if not future_xs:
        return [], None, None
    if len(xs) < 4:
        return _forecast(xs, ys, future_xs), "trend", None

    best = None
    if len(xs) >= 9:
        for name in _MODEL_NAMES:
            result = _backtest(name, xs, ys)
            if result is not None and (best is None or result[0] < best[1][0]):
                best = (name, result)
    if best is None:
        name, result = "weighted", None
    else:
        name, result = best
    values = _model_values(name, xs, ys, future_xs)
    if values is None:
        return _forecast(xs, ys, future_xs), "trend", None

    last = xs[-1]
    output = []
    if result is not None:
        rmse = result[1]
        for target, value in zip(future_xs, values):
            half = CONFIDENCE_Z * rmse * min(3.5, 1 + 0.12 * (target - last - 1))
            output.append((value, max(0.0, value - half), value + half))
    else:
        fallback = _forecast(xs, ys, future_xs)
        for value, (_, lower, upper) in zip(values, fallback):
            output.append((value, max(0.0, min(lower, value)), max(upper, value)))
    return output, name, (result[0] if result is not None else None)


def _run_rate(actual, start, end, data_end):
    """Project a still-running bucket from its daily pace so far."""
    total = (end - start).days + 1
    elapsed = (data_end - start).days + 1
    if actual <= 0 or elapsed < 1 or elapsed >= total:
        return None
    fraction = elapsed / total
    if fraction < 0.1:
        return None
    value = actual / fraction
    half = value * 0.3 * (1 - fraction)
    return value, max(actual, value - half), value + half


def _pct(new, old):
    return (new - old) / old * 100 if old else None


# key, label, kind
_BREAKDOWN_METRICS = [
    ("ipd_revenue", "IPD revenue", "currency"),
    ("discharges", "IPD discharges", "count"),
    ("admissions", "IPD admissions", "count"),
    ("opd", "OPD consultations", "count"),
    ("services", "OPD Services", "count"),
]
_BREAKDOWN_DIMENSIONS = [("departments", "department"), ("doctors", "doctor"), ("panels", "panel")]


def _entity_rows(events, column, metric, bucket_col, hist_xs, next_x, prev_x,
                 predict, runrate, data_end, limit=10):
    """Top entities (department / doctor / panel): last value and next forecast."""
    scoped = events.loc[events[metric] > 0]
    if scoped.empty:
        return []
    totals = (
        scoped.loc[scoped[bucket_col].isin(hist_xs[-12:])]
        .groupby(column)[metric].sum().sort_values(ascending=False).head(limit)
    )
    if totals.empty:
        return []
    table = (
        scoped.loc[scoped[column].isin(totals.index)]
        .pivot_table(index=column, columns=bucket_col, values=metric, aggfunc="sum", fill_value=0)
    )
    rows = []
    for name in totals.index:
        series = table.loc[name]
        ys = [float(series.get(x, 0.0)) for x in hist_xs]
        value, lower, upper = predict(hist_xs, ys, [next_x])[0]
        if runrate is not None:
            projected = _run_rate(float(series.get(next_x, 0.0)), runrate[0], runrate[1], data_end)
            if projected:
                value, lower, upper = projected
        previous = float(series.get(prev_x, 0.0))
        rows.append({
            "name": name, "previous": previous,
            "next": value, "lower": lower, "upper": upper,
            "changePct": _pct(value, previous),
        })
    return rows


# --------------------------------------------------------------------------
# Plain-language summary of what the forecast charts show
# --------------------------------------------------------------------------
def _money(value):
    value = float(value)
    if abs(value) >= 1e7:
        return f"₹{value / 1e7:.2f} Cr"
    if abs(value) >= 1e5:
        return f"₹{value / 1e5:.2f} L"
    return f"₹{value:,.0f}"


def _signed(pct):
    if pct is None:
        return "n/a"
    return f"{'+' if pct > 0 else '−' if pct < 0 else ''}{abs(pct):.1f}%"


def _tone(pct, good_up, flat=1.0):
    if pct is None or abs(pct) < flat:
        return "neutral"
    return "positive" if (pct > 0) == good_up else "negative"


def _summary(horizon, unit, metrics, service_rows, confidence, points, accuracy=None):
    """Short sentences describing what is going to change in the charts."""

    def fmt(metric, value):
        return _money(value) if metric["kind"] == "currency" else f"{value:,.0f}"

    def forecast_rows(metric):
        return [row for row in metric["rows"] if row["forecast"] is not None]

    highlights, charts = [], []

    def add(tone, title, text):
        highlights.append({"tone": tone, "title": title, "text": text})

    # ---- one note per chart -------------------------------------------------
    for metric in metrics.values():
        info, rows = metric["summary"], forecast_rows(metric)
        text = f"{fmt(metric, info['next'])} expected in {info['nextLabel']}"
        if info["changePct"] is not None:
            text += f" ({_signed(info['changePct'])} vs {info['previousLabel']})"
        trend = None
        if len(rows) >= 2:
            first, last = rows[0], rows[-1]
            trend = _pct(last["forecast"], first["forecast"])
            if trend is not None and trend >= 1:
                text += f"; rising to {fmt(metric, last['forecast'])} by {last['label']} ({_signed(trend)})"
            elif trend is not None and trend <= -1:
                text += f"; falling to {fmt(metric, last['forecast'])} by {last['label']} ({_signed(trend)})"
            else:
                text += f"; staying near {fmt(metric, last['forecast'])} through {last['label']}"
        lead = info["changePct"] if info["changePct"] is not None else trend
        charts.append({
            "key": metric["key"], "label": metric["label"],
            "tone": _tone(lead, metric["goodWhenUp"]), "text": text + ".",
        })

    # ---- highlights ---------------------------------------------------------
    revenue = metrics["revenue"]
    rev, rev_rows = revenue["summary"], forecast_rows(revenue)
    if rev_rows:
        text = (
            f"{_money(rev['horizonTotal'])} is forecast over the next {horizon} {unit} "
            f"({rev_rows[0]['label']} – {rev_rows[-1]['label']})"
        )
        change = _pct(rev["horizonTotal"], rev["recentTotal"]) if rev.get("recentTotal") else None
        if change is not None:
            text += (
                f", {_signed(change)} against {_money(rev['recentTotal'])} in the previous "
                f"{rev['recentCount']} {unit}"
            )
        add(_tone(change, True), "Revenue outlook", text + ".")

        with_target = [row for row in rev_rows if row.get("target")]
        if with_target:
            met = sum(1 for row in with_target if row["forecast"] >= row["target"])
            text = (
                f"Revenue is expected to reach the target in {met} of {len(with_target)} forecast "
                f"periods"
            )
            if rev.get("targetPct") is not None:
                text += f"; {rev['nextLabel']} is projected at {rev['targetPct']:.0f}% of target"
            add("positive" if met == len(with_target) else "negative" if met == 0 else "neutral",
                "Revenue vs target", text + ".")

        if len(rev_rows) >= 3:
            high = max(rev_rows, key=lambda row: row["forecast"])
            low = min(rev_rows, key=lambda row: row["forecast"])
            add("neutral", "Peak and low",
                f"Highest revenue is expected in {high['label']} ({_money(high['forecast'])}) and "
                f"lowest in {low['label']} ({_money(low['forecast'])}).")

    movers = [
        (metric, metric["summary"]["changePct"]) for metric in metrics.values()
        if metric["summary"]["changePct"] is not None
        and metric["summary"]["previous"] > 0 and metric["key"] != "revenue"
    ]
    rises = [item for item in movers if item[1] >= 5]
    falls = [item for item in movers if item[1] <= -5]
    if rises:
        metric, pct = max(rises, key=lambda item: item[1])
        add(_tone(pct, metric["goodWhenUp"]), "Biggest expected rise",
            f"{metric['label']} ({_signed(pct)} in {metric['summary']['nextLabel']}).")
    if falls:
        metric, pct = min(falls, key=lambda item: item[1])
        add(_tone(pct, metric["goodWhenUp"]), "Biggest expected fall",
            f"{metric['label']} ({_signed(pct)} in {metric['summary']['nextLabel']}).")
    if not rises and not falls and movers:
        add("neutral", "Overall movement", "No metric is expected to move by more than 5% next period.")

    adm, dis = metrics["admissions"]["summary"], metrics["discharges"]["summary"]
    if adm["changePct"] is not None and dis["changePct"] is not None:
        gap = adm["changePct"] - dis["changePct"]
        if gap >= 5:
            add("neutral", "Patient flow",
                f"Admissions ({_signed(adm['changePct'])}) are growing faster than discharges "
                f"({_signed(dis['changePct'])}), so more patients will be in beds.")
        elif gap <= -5:
            add("neutral", "Patient flow",
                f"Discharges ({_signed(dis['changePct'])}) are growing faster than admissions "
                f"({_signed(adm['changePct'])}), so bed occupancy should ease.")

    credit, ipd = metrics["credit_due"]["summary"], metrics["ipd_revenue"]["summary"]
    if credit["changePct"] is not None and abs(credit["changePct"]) >= 5:
        share = credit["next"] / ipd["next"] * 100 if ipd["next"] else None
        text = (
            f"Pending credit due is forecast to {'rise' if credit['changePct'] > 0 else 'fall'} "
            f"{_signed(credit['changePct'])} to {_money(credit['next'])} in {credit['nextLabel']}"
        )
        if share is not None:
            text += f" ({share:.0f}% of forecast IPD revenue)"
        add(_tone(credit["changePct"], False), "Collections", text + ".")

    changes = [
        (row, row["next"] - row["previous"]) for row in service_rows if row["previous"] or row["next"]
    ]
    if len(changes) >= 2:
        grow = max(changes, key=lambda item: item[1])
        shrink = min(changes, key=lambda item: item[1])
        parts = []
        if grow[1] >= 1:
            parts.append(f"{grow[0]['name']} should grow most (+{grow[1]:,.0f})")
        if shrink[1] <= -1:
            parts.append(f"{shrink[0]['name']} should fall most (−{abs(shrink[1]):,.0f})")
        if parts:
            add("neutral", "OPD services", "Among the top services, " + " and ".join(parts) + ".")

    if confidence != "good":
        basis = (
            f"on the latest months this method was off by about {accuracy * 100:.0f}% on average"
            if accuracy is not None else f"only {points} data points are available"
        )
        add("neutral", "How reliable is this",
            f"Confidence is {confidence}: {basis}, so treat these figures as a guide.")

    return {"highlights": highlights, "charts": charts}


# --------------------------------------------------------------------------
# Forecast request
# --------------------------------------------------------------------------
def get_prediction(query):
    datasets = dashboard_service._dataset_metadata()
    if not datasets:
        return {"hasData": False, "message": "Upload a workbook before predicting."}
    filters = query.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError("Prediction filters must be a JSON object.")
    mode = filters.get("predictBy", "Year")
    if mode not in _ENGINE_MODES:
        raise ValueError("Choose Year, Month, Last months or Period.")
    month_name = filters.get("month") or ""
    period = filters.get("period") or ""
    if mode == "Month" and month_name not in _MONTHS:
        raise ValueError("Choose a month to predict.")
    if mode == "Period" and period not in _PERIOD_BOUNDS:
        raise ValueError("Choose a billing period to predict.")
    try:
        horizon = int(filters.get("horizon", 12))
        window = int(filters.get("window", 3)) if mode == "Recent" else 0
    except (TypeError, ValueError) as error:
        raise ValueError("Forecast length and look-back must be numbers.") from error
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"Forecast length must be between 1 and {MAX_HORIZON}.")
    if mode == "Recent" and not 2 <= window <= 12:
        raise ValueError("Look-back must be between 2 and 12 months.")
    normalized = {
        "predictBy": mode,
        "month": month_name if mode == "Month" else "",
        "period": period if mode == "Period" else "",
        "horizon": horizon,
        "window": window,
    }
    return _cached_prediction(
        query.get("hospital", "All Hospitals"),
        json.dumps(normalized, sort_keys=True),
        dashboard_service._data_version(datasets),
    )


@lru_cache(maxsize=16)
def _cached_prediction(hospital, serialized_filters, version):
    frame = dashboard_service._master_frame()
    if frame.empty:
        return {"hasData": False, "message": "No saved hospital records are available."}
    available = set(
        frame["hospital_name"].dropna().astype(str).str.strip().unique()
    ) if "hospital_name" in frame else set()
    if hospital != "All Hospitals" and hospital not in available:
        raise ValueError("The selected hospital is not available.")
    scoped = dashboard_service._hospital_scope(frame, hospital)
    return _build(scoped, hospital, json.loads(serialized_filters))


def _build(frame, hospital, filters):
    mode, horizon, window = filters["predictBy"], filters["horizon"], filters["window"]
    month_name, period = filters["month"], filters["period"]
    month_number = _MONTHS.index(month_name) + 1 if mode == "Month" else None

    all_events = _events(frame)
    if all_events.empty:
        return {"hasData": False, "message": "No dated records found for this hospital."}
    data_start = all_events["date"].min().normalize()
    data_end = all_events["date"].max().normalize()

    events = _assign_buckets(all_events, mode, month_number, period)
    if events.empty:
        return {"hasData": False, "message": "No records found for the selected month or period."}

    def info(x):
        return _bucket_info(mode, x, month_number, period)

    keys = [metric[0] for metric in _METRICS]
    grouped = events.groupby("bx")[keys].sum()
    all_xs = sorted(int(x) for x in grouped.index)
    partial = {x: info(x)[0] < data_start or info(x)[1] > data_end for x in all_xs}
    complete = [x for x in all_xs if not partial[x]]
    uses_partial = len(complete) < 2
    pool = all_xs if uses_partial else complete
    fit_xs = pool[-window:] if mode == "Recent" else pool
    future_xs = [fit_xs[-1] + step for step in range(1, horizon + 1)]
    first_future = future_xs[0]
    seasonal = mode == "Year"
    monthly_target = backend.monthly_revenue_target(hospital)

    timeline = sorted(set(all_xs) | set(future_xs))
    if mode == "Recent":
        row_xs = [x for x in timeline if x >= fit_xs[0]]
    else:
        row_xs = timeline[-(_HISTORY_LIMIT[mode] + horizon):]

    # Bucket that is running right now -> projected from its daily pace.
    runrate_x = None
    if partial.get(first_future):
        start, end, _ = info(first_future)
        if start <= data_end < end:
            runrate_x = first_future

    def target_for(x):
        start, end, _ = info(x)
        return dashboard_service._target_for_ranges(monthly_target, [(start, end)])

    def value_at(series, x):
        if x in series.index and not partial.get(x, False):
            return float(series[x])
        return None

    ago_x = first_future - 1 if mode == "Month" else first_future - 12
    if mode == "Month":
        ago_label = info(ago_x)[2]
    elif mode == "Period":
        ago_label = "same period last year"
    else:
        ago_label = "same month last year"

    month_context = None
    if mode == "Month":
        stamps = all_events["date"]
        monthly = all_events.groupby(stamps.dt.year * 12 + stamps.dt.month - 1)[keys].sum()

        def month_info(mx):
            return _bucket_info("Year", mx, None, None)

        def month_partial(mx):
            start, end, _ = month_info(mx)
            return start < data_start or end > data_end

        complete_months = [int(m) for m in monthly.index if not month_partial(int(m))]
        running = [
            int(m) for m in monthly.index
            if month_partial(int(m)) and month_info(int(m))[0] <= data_end < month_info(int(m))[1]
        ]
        focus_offset = month_number - 1
        month_xs = sorted(
            set(complete_months[-12:]) | set(running)
            | {x * 12 + focus_offset for x in future_xs}
        )
        month_context = (monthly, month_info, month_partial, month_xs, focus_offset)

    metrics = {}
    for key, label, kind, good_up in _METRICS:
        series = grouped[key]
        ys = [float(series.get(x, 0.0)) for x in fit_xs]
        model, error = None, None
        if mode == "Month":
            month_values = [float(monthly[key].get(m, 0.0)) for m in complete_months]
            targets = [x * 12 + focus_offset for x in future_xs]
            results, model, error = _smart_forecast(complete_months, month_values, targets)
            forecasts = dict(zip(future_xs, results))
        elif mode == "Recent":
            forecasts = dict(zip(future_xs, _forecast(fit_xs, ys, future_xs)))
        else:
            results, model, error = _smart_forecast(fit_xs, ys, future_xs)
            forecasts = dict(zip(future_xs, results))
        if runrate_x is not None:
            start, end, _ = info(runrate_x)
            projected = _run_rate(float(series.get(runrate_x, 0.0)), start, end, data_end)
            if projected:
                forecasts[runrate_x] = projected

        rows = []
        if month_context is not None:
            monthly, month_info, month_partial, month_xs, focus_offset = month_context
            monthly_series = monthly[key]
            for mx in month_xs:
                start, end, text = month_info(mx)
                known = mx in monthly_series.index
                row = {
                    "label": text,
                    "start": start.strftime("%Y-%m-%d"),
                    "end": end.strftime("%Y-%m-%d"),
                    "actual": float(monthly_series[mx]) if known else None,
                    "partial": bool(month_partial(mx)) if known else False,
                    "focus": mx % 12 == focus_offset,
                    "forecast": None, "lower": None, "upper": None,
                    "previousYear": None,
                }
                if row["focus"] and mx // 12 in forecasts:
                    row["forecast"], row["lower"], row["upper"] = forecasts[mx // 12]
                if key == "revenue":
                    row["target"] = dashboard_service._target_for_ranges(
                        monthly_target, [(start, end)]
                    )
                rows.append(row)
        for x in ([] if month_context is not None else row_xs):
            start, end, text = info(x)
            row = {
                "label": text,
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "actual": float(series[x]) if x in series.index else None,
                "partial": bool(partial.get(x, False)),
                "forecast": None, "lower": None, "upper": None,
                "previousYear": None if mode == "Month" else value_at(series, x - 12),
            }
            if x in forecasts:
                row["forecast"], row["lower"], row["upper"] = forecasts[x]
            if key == "revenue":
                row["target"] = target_for(x)
            rows.append(row)

        next_value = forecasts[first_future][0]
        year_ago = value_at(series, ago_x)
        summary = {
            "previous": ys[-1],
            "previousLabel": info(fit_xs[-1])[2],
            "next": next_value,
            "nextLabel": info(first_future)[2],
            "changePct": _pct(next_value, ys[-1]),
            "yearAgo": year_ago,
            "yoyPct": _pct(next_value, year_ago) if year_ago is not None else None,
            "yoyLabel": ago_label,
            "horizonTotal": sum(value[0] for value in forecasts.values()),
            "recentTotal": sum(ys[-horizon:]),
            "recentCount": len(ys[-horizon:]),
        }
        if key == "revenue":
            target = target_for(first_future)
            summary["targetPct"] = next_value / target * 100 if target else None
        metrics[key] = {
            "key": key, "label": label, "kind": kind, "goodWhenUp": good_up,
            "rows": rows, "summary": summary, "model": model, "error": error,
        }

    # Top services: forecast each separately for the next bucket.
    services = events.loc[events["services"] > 0]
    top = (
        services.loc[services["bx"].isin(fit_xs)]
        .groupby("service_name").size().sort_values(ascending=False).head(5)
    )
    service_rows = []
    for name in top.index:
        counts = services.loc[services["service_name"].eq(name)].groupby("bx").size()
        ys = [float(counts.get(x, 0)) for x in fit_xs]
        value, lower, upper = _forecast(fit_xs, ys, future_xs[:1])[0]
        service_rows.append({
            "name": name or "Unknown",
            "previous": ys[-1],
            "next": value, "lower": lower, "upper": upper,
        })

    runrate = info(runrate_x)[:2] if runrate_x is not None else None
    if mode == "Month":
        entity_events = all_events.assign(
            mx=all_events["date"].dt.year * 12 + all_events["date"].dt.month - 1
        )
        bucket_col, hist_xs = "mx", complete_months
        next_x = first_future * 12 + focus_offset
        prev_x = fit_xs[-1] * 12 + focus_offset
    else:
        entity_events, bucket_col, hist_xs = events, "bx", fit_xs
        next_x, prev_x = first_future, fit_xs[-1]
    if mode == "Recent":
        predict = lambda xs, ys, targets: _forecast(xs, ys, targets)
    else:
        predict = lambda xs, ys, targets: _smart_forecast(xs, ys, targets)[0]
    breakdown = {
        dimension: {
            key: _entity_rows(entity_events, column, key, bucket_col, hist_xs,
                              next_x, prev_x, predict, runrate, data_end)
            for key, _, _ in _BREAKDOWN_METRICS
        }
        for dimension, column in _BREAKDOWN_DIMENSIONS
    }

    points = len(fit_xs)
    first_label, last_label = info(fit_xs[0])[2], info(fit_xs[-1])[2]
    unit = "years" if mode == "Month" else "months"
    description = {
        "Year": f"All months of data ({points} months, {first_label} – {last_label})",
        "Month": f"{month_name} of every year ({points} years, {first_label} – {last_label})",
        "Recent": f"Last {points} complete months ({first_label} – {last_label})",
        "Period": f"{period} of every month ({points} months, {first_label} – {last_label})",
    }[mode]

    notes = [
        "Method: linear trend fitted to the history"
        + (" with a seasonal index" if seasonal and points >= 24 else "")
        + "; the shaded band is an 80% likely range. Forecasts are estimates, not guarantees."
    ]
    skipped = [info(x)[2] for x in all_xs if partial[x]]
    if runrate_x is not None:
        notes.append(
            f"{info(runrate_x)[2]} is still running (data ends {data_end.strftime('%d %b %Y')}); "
            "its forecast is the daily pace so far, extended to the full period."
        )
    elif skipped and not uses_partial:
        notes.append(
            f"Incomplete data left out of the trend: {', '.join(skipped)} "
            f"(records end on {data_end.strftime('%d %b %Y')})."
        )
    if uses_partial:
        notes.append(
            "There are fewer than 2 complete periods, so incomplete ones were used. "
            "Treat these numbers with extra caution."
        )
    if mode == "Recent" and points < window:
        notes.append(f"Only {points} complete months exist, fewer than the {window} requested.")
    errors = [
        metrics[key]["error"] for key in ("admissions", "discharges", "opd", "revenue", "credit_due")
        if metrics[key]["error"] is not None
    ]
    accuracy = float(np.median(errors)) if errors else None
    if accuracy is not None:
        confidence = "good" if accuracy <= 0.12 else "moderate" if accuracy <= 0.25 else "low"
    else:
        confidence = "good" if points >= 12 else "moderate" if points >= 6 else "low"

    return dashboard_service._json_value({
        "hasData": True,
        "hospital": hospital,
        "mode": mode,
        "layout": "calendar" if mode == "Year" else "timeline",
        "connectForecast": mode != "Month",
        "window": window,
        "horizon": horizon,
        "unit": unit,
        "description": f"{hospital} · {description}",
        "points": points,
        "confidence": confidence,
        "accuracy": round(accuracy * 100, 1) if accuracy is not None else None,
        "dataRange": [data_start.strftime("%Y-%m-%d"), data_end.strftime("%Y-%m-%d")],
        "metrics": [metrics[key] for key in keys],
        "services": service_rows,
        "breakdown": breakdown,
        "notes": notes,
        "summary": _summary(horizon, unit, metrics, service_rows, confidence, points, accuracy),
    })


# --------------------------------------------------------------------------
# Comparison + forecast (single entry point used by the server)
# --------------------------------------------------------------------------
def get_comparison_forecast(query):
    hospital = query.get("hospital", "All Hospitals")
    filters = query.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError("Comparison filters must be a JSON object.")
    mode = filters.get("compareBy", "Year")

    engine = None
    if mode == "Year":
        engine = {"predictBy": "Year"}
    elif mode == "Month":
        engine = {"predictBy": "Month", "month": filters.get("month")}
    elif mode == "Recent":
        engine = {"predictBy": "Recent", "window": filters.get("window", 3)}
    elif mode == "Period" and filters.get("period") in _PERIOD_BOUNDS:
        engine = {"predictBy": "Period", "period": filters["period"]}

    forecast, message = None, ""
    if engine is not None:
        engine["horizon"] = filters.get("horizon", 6)
        result = get_prediction({"hospital": hospital, "filters": engine})
        if result.get("hasData"):
            watch_year = filters.get("year")
            forecast = {
                **result,
                "watch": {
                    "year": int(watch_year) if str(watch_year or "").isdigit() else None,
                    "month": filters.get("month"),
                },
            }
        else:
            message = result.get("message", "")

    data_end = (
        pd.Timestamp(forecast["dataRange"][1]) if forecast
        else _hospital_data_end(hospital)
    )
    comparison = dashboard_service.get_comparison({
        "hospital": hospital,
        "filters": _comparison_filters(mode, filters, data_end),
    })
    return {"comparison": comparison, "forecast": forecast, "forecastMessage": message}


def _hospital_data_end(hospital):
    frame = dashboard_service._master_frame()
    scoped = dashboard_service._hospital_scope(frame, hospital)
    events = _events(scoped) if not scoped.empty else None
    if events is None or events.empty:
        return pd.Timestamp.today().normalize()
    return events["date"].max().normalize()


def _other_year(year, years):
    earlier = [item for item in years if item < year]
    if earlier:
        return max(earlier)
    later = [item for item in years if item > year]
    return min(later) if later else year


def _same_day(year, reference):
    month_days = pd.Timestamp(year, reference.month, 1).days_in_month
    return pd.Timestamp(year, reference.month, min(reference.day, month_days))


def _span_text(start, end):
    if start.year == end.year:
        return f"{start:%b}–{end:%b %Y}"
    return f"{start:%b %Y}–{end:%b %Y}"


def _range_filters(first, second):
    """first/second = (start, end, label) -> legacy From-To filters."""
    return {
        "compareBy": "From-To",
        "start1": first[0].strftime("%Y-%m-%d"), "end1": first[1].strftime("%Y-%m-%d"),
        "start2": second[0].strftime("%Y-%m-%d"), "end2": second[1].strftime("%Y-%m-%d"),
        "labels": [first[2], second[2]],
    }


def _comparison_filters(mode, filters, data_end):
    """Translate the new compare modes into the existing two-period comparison."""
    if mode in ("Period", "From-To"):
        return {key: value for key, value in filters.items() if key != "horizon"}

    years = sorted(int(year) for year in dashboard_service.comparison_options().get("years", []))

    if mode == "Recent":
        window = int(filters.get("window", 3))
        last_month_end = (
            data_end if data_end + pd.offsets.MonthEnd(0) == data_end
            else data_end.replace(day=1) - pd.Timedelta(days=1)
        )
        second_start = last_month_end.replace(day=1) - pd.DateOffset(months=window - 1)
        first_end = second_start - pd.Timedelta(days=1)
        first_start = second_start - pd.DateOffset(months=window)
        return _range_filters(
            (first_start, first_end, f"Previous {window} months ({_span_text(first_start, first_end)})"),
            (second_start, last_month_end, f"Last {window} months ({_span_text(second_start, last_month_end)})"),
        )

    if not str(filters.get("year", "")).isdigit():
        raise ValueError("Choose a year.")
    year = int(filters["year"])
    other = _other_year(year, years)
    first_year, second_year = (other, year) if other <= year else (year, other)

    if mode == "Year":
        full_end = pd.Timestamp(data_end.year, 12, 31)
        capped = data_end < full_end and data_end.year in (year, other)

        def span(y):
            end = _same_day(y, data_end) if capped else pd.Timestamp(y, 12, 31)
            label = f"{y} (to {end:%d %b})" if capped else str(y)
            return pd.Timestamp(y, 1, 1), end, label
        return _range_filters(span(first_year), span(second_year))

    if mode == "Month":
        month_name = filters.get("month")
        if month_name not in _MONTHS:
            raise ValueError("Choose a month.")
        month = _MONTHS.index(month_name) + 1
        month_end = pd.Timestamp(data_end.year, month, 1) + pd.offsets.MonthEnd(0)
        capped = (
            data_end.month == month and data_end < month_end
            and data_end.year in (year, other)
        )

        def span(y):
            start = pd.Timestamp(y, month, 1)
            end = _same_day(y, data_end) if capped else start + pd.offsets.MonthEnd(0)
            label = f"{month_name[:3]} {y}" + (f" (1–{end.day})" if capped else "")
            return start, end, label
        return _range_filters(span(first_year), span(second_year))

    raise ValueError("Choose a supported comparison type.")