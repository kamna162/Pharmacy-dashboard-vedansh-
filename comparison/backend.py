import calendar
import re
from decimal import Decimal

import pandas as pd

TOTAL_BEDS = 155
DEFAULT_TARGET = 50_000_000
NIMMS_MONTHLY_REVENUE_TARGET = 25_500_000
SURYA_MONTHLY_REVENUE_TARGET = 50_000_000
PERIOD_OPTIONS = ["1-10 Days", "11-20 Days", "21-31 Days"]

REQUIRED_COLUMNS = [
    "DOA", "Month", "Dept. Name", "Doctor Name", "Panel Name",
    "Type", "Service Name", "Service Final Amount", "Cash Due",
    "Credit Due", "Bed Days", "UHID"
]

CANONICAL_COLUMNS = {
    "hospital name": "hospital_name",
    "source file": "source_file",
    "sourcefile": "source_file",
    "admission date": "DOA",
    "admissiondate": "DOA",
    "date of admission": "DOA",
    "doa": "DOA",
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
    "service name": "Service Name",
    "servicename": "Service Name",
    "service_name": "Service Name",
    "service name ": "Service Name",
    "service type": "Service Type",
    "servicetype": "Service Type",
    "service_type": "Service Type",
    "period": "Period",
    "discharge date": "DOD",
    "dischargedate": "DOD",
    "date of discharge": "DOD",
    "dod": "DOD",
    "service final amount": "Service Final Amount",
    "service final amounts": "Service Final Amount",
    "serviceamount": "Service Amount",
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


def normalize_header_name(value):
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"[_/\\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def canonicalize_column_name(value):
    text = normalize_header_name(value)
    if not text:
        return ""
    key = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    if not key:
        return text
    if key in CANONICAL_COLUMNS:
        return CANONICAL_COLUMNS[key]
    compact_key = re.sub(r"\s+", "", key)
    if compact_key in CANONICAL_COLUMNS:
        return CANONICAL_COLUMNS[compact_key]
    return text


def normalize_dataframe_columns(df):
    df = df.copy()
    if df.empty and not hasattr(df, "columns"):
        return df

    normalized_columns = []
    seen = set()
    for column in df.columns:
        canonical = canonicalize_column_name(column)
        if not canonical:
            canonical = "Unnamed"
        candidate = canonical
        suffix = 2
        while candidate in seen:
            candidate = f"{canonical}_{suffix}"
            suffix += 1
        seen.add(candidate)
        normalized_columns.append(candidate)

    df.columns = normalized_columns
    return df


def _coerce_datetime(values):
    """Parse mixed hospital date formats without exposing object values to .dt."""
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    text = series.astype("string").str.strip()
    numeric_text = text.str.fullmatch(r"[+-]?\d+(?:\.\d+)?").fillna(False)

    iso_year_first = text.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}").fillna(False)
    day_first = text.str.match(r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}").fillna(False)

    for mask, dayfirst in [
        (iso_year_first & ~numeric_text, False),
        (day_first & ~numeric_text, True),
    ]:
        if mask.any():
            try:
                parsed.loc[mask] = pd.to_datetime(
                    series.loc[mask],
                    errors="coerce",
                    dayfirst=dayfirst,
                    format="mixed",
                )
            except (TypeError, ValueError):
                parsed.loc[mask] = pd.to_datetime(
                    series.loc[mask], errors="coerce", dayfirst=dayfirst
                )

    remaining = parsed.isna() & ~numeric_text
    if remaining.any():
        try:
            parsed.loc[remaining] = pd.to_datetime(
                series.loc[remaining],
                errors="coerce",
                dayfirst=True,
                format="mixed",
            )
        except (TypeError, ValueError):
            parsed.loc[remaining] = pd.to_datetime(
                series.loc[remaining], errors="coerce", dayfirst=True
            )

    numeric_values = pd.to_numeric(series, errors="coerce")
    excel_serials = numeric_text & numeric_values.notna()
    if excel_serials.any():
        parsed.loc[excel_serials] = pd.to_datetime(
            numeric_values.loc[excel_serials],
            unit="D",
            origin="1899-12-30",
            errors="coerce",
        )
    return parsed


def _canonical_type_name(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    key = text.casefold().replace("_", " ").replace("-", " ")
    aliases = {
        "admission": "Admission",
        "admissions": "Admission",
        "opd": "OPD",
        "outpatient": "OPD",
        "opd consultation": "OPD",
        "consultation": "OPD",
        "discharge": "Discharge",
        "discharges": "Discharge",
    }
    return aliases.get(re.sub(r"\s+", " ", key), text.title())


def _canonical_month_name(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.notna(parsed):
        return parsed.strftime("%B")
    month_name = re.sub(r"\s+", " ", text)
    return month_name.title()


def clean_data(df):
    df = normalize_dataframe_columns(df)
    df = df.copy()

    if "Type" in df.columns:
        type_names = {
            value: _canonical_type_name(value) for value in df["Type"].unique()
        }
        df["Type"] = df["Type"].map(type_names).fillna("")

    for col in ["DOA", "DOD"]:
        if col in df.columns:
            df[col] = _coerce_datetime(df[col])

    numeric_cols = [
        "Service Amount", "Service Final Amount", "Cash Due", "Credit Due", "Bed Days"
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    text_cols = [
        "Month", "Dept. Name", "Doctor Name", "Panel Name",
        "Type", "Service Name", "UHID"
    ]
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    if "Month" in df.columns:
        month_names = {
            value: _canonical_month_name(value) for value in df["Month"].unique()
        }
        df["Month"] = df["Month"].map(month_names)

    return df


def data_date_range(df):
    if "Type" not in df.columns:
        return None, None

    report_type = df["Type"].astype(str).str.strip().str.casefold()
    date_series = []
    for type_names, date_column in [
        (["admission", "opd"], "DOA"),
        (["discharge"], "DOD"),
    ]:
        if date_column not in df.columns:
            continue
        rows = df[report_type.isin(type_names)]
        if not rows.empty:
            date_series.append(_coerce_datetime(rows[date_column]))
    if not date_series:
        return None, None

    dates = pd.concat(date_series, ignore_index=True).dropna()
    if dates.empty:
        return None, None
    return dates.min(), dates.max()


def _period_for_rows(df):
    """Return the month-day period using each report type's date field."""
    period = pd.Series(pd.NA, index=df.index, dtype="string")
    report_type = df["Type"].astype(str).str.strip().str.casefold()
    dates = comparison_event_dates(df)
    day = dates.dt.day
    valid_dates = dates.notna()
    relevant_rows = report_type.isin(["admission", "opd", "discharge"])
    valid_rows = relevant_rows & valid_dates
    period.loc[valid_rows & day.between(1, 10)] = PERIOD_OPTIONS[0]
    period.loc[valid_rows & day.between(11, 20)] = PERIOD_OPTIONS[1]
    period.loc[valid_rows & day.between(21, 31)] = PERIOD_OPTIONS[2]

    return period


def _month_numbers(values):
    month_numbers = set()
    for value in values or []:
        parsed = pd.to_datetime(str(value), format="%B", errors="coerce")
        if pd.isna(parsed):
            parsed = pd.to_datetime(str(value), format="%b", errors="coerce")
        if pd.notna(parsed):
            month_numbers.add(parsed.month)
    return month_numbers


def _month_filter_mask(df, values):
    """Filter each report type by its own event date."""
    selected_months = _month_numbers(values)
    if not selected_months:
        return pd.Series(True, index=df.index)

    report_type = df["Type"].astype(str).str.strip().str.casefold()
    mask = pd.Series(False, index=df.index)
    event_dates = comparison_event_dates(df)
    dated_types = report_type.isin(["admission", "opd", "discharge"])
    mask |= dated_types & event_dates.dt.month.isin(selected_months)

    other_types = ~report_type.isin(["admission", "opd", "discharge"])
    if "Month" in df.columns:
        mask |= other_types & df["Month"].isin(values)
    return mask


def apply_filters(df, month=None, period=None):
    mask = pd.Series(True, index=df.index)
    if month:
        mask &= _month_filter_mask(df, month)

    if period:
        mask &= _period_for_rows(df).isin(period)

    return df.loc[mask].copy()


def revenue_data(df):
    return df[df["Type"].isin(["OPD", "Discharge"])].copy()


def service_final_amounts(df):
    if "Service Final Amount" not in df.columns:
        return pd.Series(0.0, index=df.index, dtype="float64")
    return pd.to_numeric(df["Service Final Amount"], errors="coerce").fillna(0)


def precise_sum(values):
    return float(sum(
        (Decimal(str(value)) for value in values if pd.notna(value)),
        Decimal("0"),
    ))


def revenue_totals(df):
    if "Type" not in df.columns:
        return {"discharge": 0.0, "opd": 0.0, "admission": 0.0, "total": 0.0}

    report_type = df["Type"].astype(str).str.strip()
    amounts = service_final_amounts(df)
    discharge_revenue = precise_sum(amounts.loc[report_type.eq("Discharge")])
    opd_revenue = precise_sum(amounts.loc[report_type.eq("OPD")])
    admission_revenue = precise_sum(amounts.loc[report_type.eq("Admission")])
    return {
        "discharge": discharge_revenue,
        "opd": opd_revenue,
        "admission": admission_revenue,
        "total": discharge_revenue + opd_revenue,
    }


def opd_consultation_records(df):
    if "Type" not in df.columns:
        return df.iloc[0:0].copy()
    report_type = df["Type"].fillna("").astype(str).str.strip().str.casefold()
    opd_rows = report_type.eq("opd")
    service_type_values = (
        df["Service Type"].fillna("").astype(str).str.strip().str.casefold()
        if "Service Type" in df.columns
        else pd.Series("", index=df.index, dtype="object")
    )
    if "Service Type" in df.columns and service_type_values.ne("").any():
        service_type = service_type_values
        consultation = service_type.eq("consultation")
    elif "Service Name" in df.columns:
        consultation = df["Service Name"].fillna("").astype(str).str.contains(
            "consultation", case=False, na=False
        )
    else:
        consultation = pd.Series(True, index=df.index)
    return df.loc[opd_rows & consultation].copy()


def calculate_kpis(df, total_beds=TOTAL_BEDS, target=DEFAULT_TARGET):
    revenues = revenue_totals(df)
    revenue = revenues["total"]
    report_type = df["Type"].astype(str).str.strip().str.casefold()
    discharge_df = df[report_type.eq("discharge")]

    cash_due = precise_sum(discharge_df["Cash Due"])
    credit_due = precise_sum(discharge_df["Credit Due"])

    opd_consultation = len(opd_consultation_records(df))

    admissions = len(df[df["Type"] == "Admission"])
    discharges = len(discharge_df)

    bed_days = discharge_df["Bed Days"].sum()
    alos = bed_days / discharges if discharges else 0

    doa = (
        _coerce_datetime(df["DOA"])
        if "DOA" in df.columns else None
    )
    days = doa.dt.date.nunique() if doa is not None else 0
    occupancy_discharge = discharge_df.copy()
    occupancy_discharge["Bed Days"] = pd.to_numeric(
        occupancy_discharge["Bed Days"], errors="coerce"
    ).fillna(0)
    occupied_bed_days = occupancy_discharge["Bed Days"].sum()
    occupancy = (
        occupied_bed_days / (total_beds * days) * 100
        if days and total_beds else 0
    )

    achievement = revenue / target * 100 if target else 0

    return {
        "revenue": revenue,
        "ipd_revenue": revenues["discharge"],
        "opd_revenue": revenues["opd"],
        "cash_due": cash_due,
        "credit_due": credit_due,
        "opd_consultation": opd_consultation,
        "admissions": admissions,
        "discharges": discharges,
        "bed_days": bed_days,
        "alos": alos,
        "occupancy": occupancy,
        "achievement": achievement,
        "target": target,
    }


def monthly_revenue_target(hospital):
    hospital_name = str(hospital).strip().casefold()
    if hospital_name == "nimms hospital":
        return NIMMS_MONTHLY_REVENUE_TARGET
    if hospital_name == "surya hospital":
        return SURYA_MONTHLY_REVENUE_TARGET
    if hospital_name == "all hospitals":
        return SURYA_MONTHLY_REVENUE_TARGET + NIMMS_MONTHLY_REVENUE_TARGET
    return DEFAULT_TARGET


def calculate_dashboard_metrics(filtered_df, hospital):
    hospital_name = str(hospital).strip().casefold()
    if hospital_name == "nimms hospital":
        total_beds = 50
    elif hospital_name == "all hospitals":
        total_beds = 205
    else:
        total_beds = TOTAL_BEDS
    kpis = calculate_kpis(
        filtered_df, total_beds, monthly_revenue_target(hospital)
    )
    opd_gross, opd_discount = opd_gross_and_discount(filtered_df)
    kpis["opd_gross_amount"] = opd_gross
    kpis["opd_discount"] = opd_discount
    return kpis


def period_revenue(df, month=None, period=None):
    """Aggregate discharge revenue by the day of discharge."""
    if not {"Type", "DOD", "Service Final Amount"}.issubset(df.columns):
        return pd.DataFrame(columns=["Period", "Revenue"])

    discharge = df[
        df["Type"].astype(str).str.strip().str.casefold() == "discharge"
    ].copy()
    discharge["DOD"] = _coerce_datetime(discharge["DOD"])
    discharge = discharge.dropna(subset=["DOD"])

    if month:
        month_numbers = _month_numbers(month)
        if month_numbers:
            discharge = discharge[discharge["DOD"].dt.month.isin(month_numbers)]

    day = discharge["DOD"].dt.day
    discharge["Period"] = pd.cut(
        day,
        bins=[0, 10, 20, 31],
        labels=PERIOD_OPTIONS,
    )
    if period:
        discharge = discharge[discharge["Period"].isin(period)]

    return (
        discharge.groupby("Period", observed=False)["Service Final Amount"]
        .sum()
        .reindex(PERIOD_OPTIONS, fill_value=0)
        .rename("Revenue")
        .reset_index()
    )


def period_discharge_counts(df):
    columns = ["Period", "Count"]
    if "Type" not in df.columns or "DOD" not in df.columns:
        return pd.DataFrame({"Period": PERIOD_OPTIONS, "Count": [0, 0, 0]}), {
            "period_total": 0,
            "kpi_total": 0,
            "missing_records": pd.DataFrame(),
        }

    discharge = df[df["Type"] == "Discharge"].copy()
    kpi_total = len(discharge)
    discharge["DOD"] = _coerce_datetime(discharge["DOD"])
    missing_records = discharge[discharge["DOD"].isna()].copy()
    valid_discharge = discharge.dropna(subset=["DOD"]).copy()
    valid_discharge["Period"] = pd.cut(
        valid_discharge["DOD"].dt.day,
        bins=[0, 10, 20, 31],
        labels=PERIOD_OPTIONS,
    )
    counts = (
        valid_discharge.groupby("Period", observed=False)
        .size()
        .reindex(PERIOD_OPTIONS, fill_value=0)
        .rename("Count")
        .reset_index()
    )
    return counts[columns], {
        "period_total": int(counts["Count"].sum()),
        "kpi_total": kpi_total,
        "missing_records": missing_records,
    }


def daily_revenue(df):
    discharge = df[df["Type"] == "Discharge"].copy()
    if "DOD" not in discharge.columns:
        return pd.DataFrame(columns=["DOD", "Revenue"])

    discharge["DOD"] = _coerce_datetime(discharge["DOD"]).dt.normalize()
    discharge = discharge.dropna(subset=["DOD"])

    return (
        discharge.groupby("DOD")["Service Final Amount"]
        .sum()
        .reset_index(name="Revenue")
        .sort_values("DOD")
    )


def cumulative_revenue_vs_target(
    df, target=DEFAULT_TARGET, selected_periods=None, month=None
):
    columns = ["Period", "Achieve Revenue", "Target Revenue"]
    if not {"Type", "Service Final Amount"}.issubset(df.columns):
        return pd.DataFrame(columns=columns)

    report_type = df["Type"].astype(str).str.strip().str.casefold()
    revenue_parts = []
    for type_name, date_column in [("discharge", "DOD"), ("opd", "DOA")]:
        if date_column not in df.columns:
            continue
        rows = df[report_type.eq(type_name)].copy()
        rows[date_column] = _coerce_datetime(rows[date_column])
        rows = rows.dropna(subset=[date_column])
        if rows.empty:
            continue
        rows["Period"] = pd.cut(
            rows[date_column].dt.day,
            bins=[0, 10, 20, 31],
            labels=PERIOD_OPTIONS,
        )
        revenue_parts.append(rows)

    if revenue_parts:
        revenue = pd.concat(revenue_parts, ignore_index=True)
        revenue["Service Final Amount"] = pd.to_numeric(
            revenue["Service Final Amount"], errors="coerce"
        ).fillna(0)
        period_revenue = (
            revenue.groupby("Period", observed=True)["Service Final Amount"]
            .sum()
            .reindex(PERIOD_OPTIONS)
        )
    else:
        period_revenue = pd.Series(float("nan"), index=PERIOD_OPTIONS)

    cumulative_revenue = period_revenue.fillna(0).cumsum().where(
        period_revenue.notna()
    )
    month_numbers = _month_numbers(month)
    month_days = 31
    if len(month_numbers) == 1:
        month_days = pd.Timestamp(2024, next(iter(month_numbers)), 1).days_in_month
    milestones = [min(day, month_days) for day in [10, 20, 31]]
    cumulative_target = pd.Series(
        [target * milestone / month_days for milestone in milestones],
        index=PERIOD_OPTIONS,
        dtype="float64",
    )

    periods_to_show = PERIOD_OPTIONS
    return pd.DataFrame({
        "Period": periods_to_show,
        "Achieve Revenue": [cumulative_revenue[period] for period in periods_to_show],
        "Target Revenue": [cumulative_target[period] for period in periods_to_show],
    })


def _ipd_flow_rows(df, type_name, date_column):
    report_type = df["Type"].astype(str).str.strip().str.casefold()
    if date_column not in df.columns or "UHID" not in df.columns:
        return pd.DataFrame(columns=[date_column, "UHID"])

    rows = df[report_type.eq(type_name)].copy()
    rows[date_column] = _coerce_datetime(rows[date_column])
    rows = rows.dropna(subset=[date_column])
    if rows.empty:
        return rows
    rows["Date"] = rows[date_column].dt.normalize()
    return rows


def _unique_ipd_breakdown_rows(df, type_name, date_column):
    if type_name == "discharge":
        report_type = df["Type"].astype(str).str.strip().str.casefold()
        if date_column not in df.columns or "UHID" not in df.columns:
            return pd.DataFrame(columns=[date_column, "UHID"])
        rows = df[report_type.eq(type_name)].copy()
        rows[date_column] = _coerce_datetime(rows[date_column])
        rows["Date"] = rows[date_column].dt.normalize().fillna(pd.Timestamp("1900-01-01"))
    else:
        rows = _ipd_flow_rows(df, type_name, date_column)
    if rows.empty:
        return rows

    for column in ["Dept. Name", "Doctor Name"]:
        if column in rows.columns:
            rows[column] = rows[column].fillna("").astype(str).str.strip()
    available_dimensions = [
        column for column in ["Dept. Name", "Doctor Name"] if column in rows.columns
    ]
    if available_dimensions:
        rows["_missing_dimensions"] = rows[available_dimensions].eq("").sum(axis=1)
        rows = rows.sort_values("_missing_dimensions")
    # Breakdown totals must reconcile with the KPI, which counts every discharge row.
    return rows


def admission_discharge(df):
    series = []
    missing_date = pd.Timestamp("1900-01-01")

    for type_name, date_column, label in [
        ("admission", "DOA", "Admissions"),
        ("discharge", "DOD", "Discharges"),
    ]:
        if type_name == "admission":
            rows = df[df["Type"] == "Admission"].copy()
            if date_column not in rows.columns:
                rows = pd.DataFrame(columns=[date_column, "UHID"])
            else:
                rows[date_column] = _coerce_datetime(rows[date_column])
                rows["Date"] = rows[date_column].dt.normalize().fillna(missing_date)
        else:
            rows = df[df["Type"].astype(str).str.strip().str.casefold() == type_name].copy()
            if date_column not in rows.columns:
                rows["Date"] = missing_date
            else:
                rows[date_column] = _coerce_datetime(rows[date_column])
                rows["Date"] = rows[date_column].dt.normalize().fillna(missing_date)
        if rows.empty:
            continue
        if type_name == "admission":
            counts = rows.groupby("Date").size().reset_index(name="UHID Count")
        else:
            counts = rows.groupby("Date").size().reset_index(name="UHID Count")
        counts["Type"] = label
        series.append(counts[["Date", "Type", "UHID Count"]])

    if not series:
        return pd.DataFrame(columns=["Date", "Type", "UHID Count"])
    return pd.concat(series, ignore_index=True).sort_values("Date")


def discharge_uhid_breakdown(df):
    discharge = _unique_ipd_breakdown_rows(df, "discharge", "DOD")
    result = {}
    for column in ["Dept. Name", "Doctor Name"]:
        if discharge.empty or column not in discharge.columns:
            result[column] = pd.DataFrame(columns=[column, "UHID Count"])
            continue
        discharge[column] = discharge[column].replace("", "Unknown")
        result[column] = (
            discharge.groupby([column, "Date"])
            .size()
            .groupby(level=0)
            .sum()
            .reset_index(name="UHID Count")
            .sort_values("UHID Count")
        )
    return result


def admission_uhid_breakdown(df):
    admission = df[df["Type"] == "Admission"].copy()
    if "DOA" not in admission.columns:
        admission = pd.DataFrame(columns=["DOA", "UHID"])
    else:
        admission["DOA"] = _coerce_datetime(admission["DOA"])
        admission = admission.dropna(subset=["DOA"])
        admission["Date"] = admission["DOA"].dt.normalize()

    result = {}
    for column in ["Dept. Name", "Doctor Name"]:
        if admission.empty or column not in admission.columns:
            result[column] = pd.DataFrame(columns=[column, "UHID Count"])
            continue
        admission[column] = admission[column].replace("", "Unknown")
        result[column] = (
            admission.groupby([column, "Date"])
            .size()
            .groupby(level=0)
            .sum()
            .reset_index(name="UHID Count")
            .sort_values("UHID Count", ascending=False)
        )
    return result


def opd_uhid_breakdown(df):
    empty_daily = pd.DataFrame(columns=["Date", "UHID Count"])
    empty_doctor = pd.DataFrame(columns=["Doctor Name", "UHID Count"])
    if not {"Type", "Service Type"}.issubset(df.columns):
        return {"daily": empty_daily, "doctor": empty_doctor, "total": 0}

    consultation = opd_consultation_records(df)
    total = len(consultation)

    if "DOA" in consultation.columns:
        daily = consultation.copy()
        daily["DOA"] = _coerce_datetime(daily["DOA"])
        daily["Date"] = daily["DOA"].dt.normalize().fillna(pd.Timestamp("1900-01-01"))
        daily = (
            daily.groupby("Date")
            .size()
            .reset_index(name="UHID Count")
            .sort_values("Date")
        )
    else:
        daily = empty_daily

    if "Doctor Name" in consultation.columns:
        consultation["Doctor Name"] = (
            consultation["Doctor Name"].fillna("").astype(str).str.strip()
            .replace("", "Unknown / Not Assigned")
        )
        doctor = (
            consultation.groupby("Doctor Name")
            .size()
            .reset_index(name="UHID Count")
            .sort_values("UHID Count", ascending=False)
        )
    else:
        doctor = empty_doctor

    return {"daily": daily, "doctor": doctor, "total": total}


def opd_service_records(df):
    """Return non-consultation OPD service records used by dashboard reports."""
    if "Type" not in df.columns or "Service Name" not in df.columns:
        return df.iloc[0:0].copy()

    opd = df[df["Type"].astype(str).str.strip().str.casefold() == "opd"].copy()
    opd["Service Name"] = opd["Service Name"].fillna("").astype(str).str.strip()
    service_names = opd["Service Name"].str.casefold()
    opd = opd[(opd["Service Name"] != "") & (service_names != "consultation")]
    return opd


def opd_services_breakdown(df):
    columns = ["Service Name", "Count"]
    opd = opd_service_records(df)
    if opd.empty:
        return pd.DataFrame(columns=columns)

    services = (
        opd.groupby("Service Name", as_index=False)
        .size()
        .rename(columns={"size": "Count"})
    )
    singletons = services[services["Count"] == 1]
    services = services[services["Count"] >= 2].sort_values(
        "Count", ascending=False, kind="stable"
    )
    if not singletons.empty:
        services = pd.concat(
            [
                services,
                pd.DataFrame({"Service Name": ["Others"], "Count": [len(singletons)]}),
            ],
            ignore_index=True,
        )
    return services.reset_index(drop=True)


def opd_services_hierarchy(df):
    columns = ["Service Type", "Service Name", "Count"]
    normalized_columns = {
        str(column).strip().casefold(): column for column in df.columns
    }
    type_column = normalized_columns.get("type")
    service_type_column = normalized_columns.get("service type")
    service_name_column = normalized_columns.get("service name")
    if not all([type_column, service_type_column, service_name_column]):
        return pd.DataFrame(columns=columns)

    opd = df[df[type_column].astype(str).str.strip().str.casefold() == "opd"].copy()
    opd["Service Type"] = opd[service_type_column].fillna("").astype(str).str.strip()
    opd["Service Name"] = opd[service_name_column].fillna("").astype(str).str.strip()
    service_names = opd["Service Name"].str.casefold()
    opd = opd[
        (opd["Service Type"] != "")
        & (opd["Service Name"] != "")
        & (service_names != "consultation")
    ]
    if opd.empty:
        return pd.DataFrame(columns=columns)

    return (
        opd.groupby(["Service Type", "Service Name"], as_index=False)
        .size()
        .rename(columns={"size": "Count"})
    )


def ipd_revenue_breakdown(df):
    ipd = df[
        df["Type"].astype(str).str.strip().str.casefold() == "discharge"
    ].copy()
    empty = pd.DataFrame(columns=["Name", "IPD Revenue"])
    if ipd.empty or "Service Final Amount" not in ipd.columns:
        return {"doctor": empty.copy(), "department": empty.copy(), "total": 0}

    ipd["Service Final Amount"] = service_final_amounts(ipd)
    result = {}
    dimension_columns = {
        "Doctor Name": next(
            (column for column in ["Doctor Name", "Doctor"] if column in ipd.columns),
            None,
        ),
        "Dept. Name": next(
            (column for column in ["Dept. Name", "Department"] if column in ipd.columns),
            None,
        ),
    }
    for output_column, column in dimension_columns.items():
        if column is None:
            result[output_column] = empty.copy()
            continue
        grouped = (
            ipd.groupby(column, dropna=False)["Service Final Amount"]
            .sum()
            .reset_index(name="IPD Revenue")
            .rename(columns={column: "Name"})
            .sort_values("IPD Revenue", ascending=False)
        )
        result[output_column] = grouped

    total = float(ipd["Service Final Amount"].sum())

    return {
        "doctor": result.get("Doctor Name", empty.copy()),
        "department": result.get("Dept. Name", empty.copy()),
        "total": total,
    }


def panel_performance_outstanding(df):
    ipd = _ipd_flow_rows(df, "discharge", "DOD").copy()
    empty = pd.DataFrame(columns=["Panel", "IPD Revenue", "Credit Due"])
    required = {"Panel Name", "Service Final Amount", "Credit Due"}
    if ipd.empty or not required.issubset(ipd.columns):
        return empty

    ipd["Panel Name"] = ipd["Panel Name"].fillna("").astype(str).str.strip()
    ipd["Panel Name"] = ipd["Panel Name"].replace("", "Unknown")
    ipd["Service Final Amount"] = service_final_amounts(ipd)

    return (
        ipd.groupby("Panel Name", as_index=False)
        .agg(
            IPD_Revenue=("Service Final Amount", "sum"),
            Credit_Due=("Credit Due", "sum"),
        )
        .rename(columns={"Panel Name": "Panel", "IPD_Revenue": "IPD Revenue", "Credit_Due": "Credit Due"})
        .sort_values("IPD Revenue", ascending=False)
    )


def _insight_money(value):
    amount = float(value or 0)
    if abs(amount) >= 10_000_000:
        return f"₹{amount / 10_000_000:.2f} Cr"
    if abs(amount) >= 100_000:
        return f"₹{amount / 100_000:.2f} L"
    return f"₹{amount:,.0f}"


def _service_gross_amounts(rows):
    columns = (
        "Service Gross Amt",
        "Service Gross Amount",
        "Service Amount",
        "Gross Amount",
        "Gross Amt",
    )
    for column in columns:
        if column not in rows.columns:
            continue
        amounts = pd.to_numeric(rows[column], errors="coerce")
        if amounts.sum() > 0:
            return amounts.fillna(0)
    return None


def opd_gross_and_discount(df):
    if "Type" not in df.columns:
        return None, None
    opd_mask = df["Type"].astype(str).str.strip().str.casefold().eq("opd")
    opd = df.loc[opd_mask]
    gross_amounts = _service_gross_amounts(opd)
    if gross_amounts is None:
        return None, None
    final_amounts = service_final_amounts(opd)
    return float(gross_amounts.sum()), float((gross_amounts - final_amounts).sum())


def insights(df, monthly_target=DEFAULT_TARGET):
    empty = {
        "department": "—",
        "doctor": "—",
        "panel": "—",
        "day": "—",
        "items": [],
    }
    if "Type" not in df.columns:
        return empty

    types = df["Type"].astype(str).str.strip().str.casefold()
    revenue_rows = df[types.isin(["opd", "discharge"])].copy()
    if "Service Final Amount" not in revenue_rows.columns:
        revenue_rows["Service Final Amount"] = 0
    revenue_rows["Service Final Amount"] = pd.to_numeric(
        revenue_rows["Service Final Amount"], errors="coerce"
    ).fillna(0)
    ipd = revenue_rows[types.loc[revenue_rows.index].eq("discharge")].copy()
    opd = revenue_rows[types.loc[revenue_rows.index].eq("opd")].copy()
    ipd_total = float(ipd["Service Final Amount"].sum())
    opd_total = float(opd["Service Final Amount"].sum())
    total_revenue = ipd_total + opd_total

    def dimension_leader(rows, columns):
        column = next((name for name in columns if name in rows.columns), None)
        if column is None or rows.empty:
            return "—", 0.0
        names = rows[column].fillna("").astype(str).str.strip()
        names = names.replace("", "Unknown / Not Assigned")
        grouped = rows.assign(_insight_dimension=names).groupby(
            "_insight_dimension"
        )["Service Final Amount"].sum()
        if grouped.empty:
            return "—", 0.0
        name = str(grouped.idxmax())
        return name, float(grouped.loc[name])

    department, department_revenue = dimension_leader(ipd, ["Dept. Name", "Department"])
    doctor, doctor_revenue = dimension_leader(ipd, ["Doctor Name", "Doctor"])
    panel, panel_revenue = dimension_leader(ipd, ["Panel Name", "Panel"])

    event_dates = comparison_event_dates(revenue_rows)
    dated_revenue = revenue_rows.assign(_insight_date=event_dates.dt.normalize())
    daily_revenue = dated_revenue.dropna(subset=["_insight_date"]).groupby(
        "_insight_date"
    )["Service Final Amount"].sum()
    date_revenue = {day: float(amount) for day, amount in daily_revenue.items()}

    best_day = max(date_revenue, key=date_revenue.get) if date_revenue else None
    achieved_days = 0
    for day, amount in date_revenue.items():
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        if amount >= float(monthly_target or 0) / days_in_month:
            achieved_days += 1

    def share(amount, total):
        return amount / total * 100 if total > 0 else 0.0

    def leader_value(name, amount):
        if name == "—":
            return "—"
        return (
            f"{name} · {_insight_money(amount)} "
            f"({share(amount, ipd_total):.0f}% of IPD revenue)"
        )

    opd_gross, discount = opd_gross_and_discount(opd)
    gross_available = opd_gross is not None
    ipd_gross_amounts = _service_gross_amounts(ipd)
    ipd_billing = (
        float(ipd_gross_amounts.sum())
        if ipd_gross_amounts is not None else ipd_total
    )
    pending = (
        pd.to_numeric(ipd["Credit Due"], errors="coerce").fillna(0).sum()
        if "Credit Due" in ipd.columns else None
    )

    date_count = len(date_revenue)
    items = [
        {
            "icon": "🎯",
            "label": "Days daily target was met",
            "value": f"{achieved_days} of {date_count} days" if date_count else "—",
        },
        {
            "icon": "🏆",
            "label": "Best earning day",
            "value": (
                f"{best_day.strftime('%d %b').lstrip('0')} · {_insight_money(date_revenue[best_day])}"
                if best_day is not None else "—"
            ),
        },
        {
            "icon": "🔀",
            "label": "Revenue from IPD vs OPD",
            "value": (
                f"IPD {share(ipd_total, total_revenue):.0f}% · "
                f"OPD {share(opd_total, total_revenue):.0f}%"
                if total_revenue else "—"
            ),
        },
        {"icon": "🏥", "label": "Top earning department", "value": leader_value(department, department_revenue)},
        {"icon": "🩺", "label": "Top earning doctor", "value": leader_value(doctor, doctor_revenue)},
        {"icon": "🏷️", "label": "Top paying panel", "value": leader_value(panel, panel_revenue)},
        {
            "icon": "💳",
            "label": "Payment still pending",
            "value": (
                f"{_insight_money(pending)} ({share(pending, ipd_billing):.0f}% of IPD billing)"
                if pending is not None else "—"
            ),
        },
        {
            "icon": "🧾",
            "label": "OPD gross amount",
            "value": _insight_money(opd_gross) if opd_gross is not None else "—",
        },
        {
            "icon": "✅",
            "label": "OPD final amount (received)",
            "value": (
                f"{_insight_money(opd_total)} ({share(opd_total, opd_gross):.0f}% of gross)"
                if opd_gross is not None and opd_gross > 0 else _insight_money(opd_total)
            ),
        },
        {
            "icon": "🏷️",
            "label": "OPD discount / concession",
            "value": (
                f"{_insight_money(discount)} ({share(discount, opd_gross):.0f}% of gross)"
                if discount is not None and opd_gross and discount > 0 else (
                    _insight_money(discount) if discount is not None else "—"
                )
            ),
        },
    ]
    if best_day is not None:
        day_label = best_day.strftime("%d-%b-%Y")
    else:
        day_label = "—"
    return {
        "department": department,
        "doctor": doctor,
        "panel": panel,
        "day": day_label,
        "items": items,
    }


def validate_columns(df):
    normalized = normalize_dataframe_columns(df)
    missing = [c for c in REQUIRED_COLUMNS if c not in normalized.columns]
    return missing


def comparison_event_dates(df):
    """Get each record's event date: DOA for admission/OPD, DOD for discharge."""
    if "__comparison_event_date" in df.columns:
        return df["__comparison_event_date"]

    rows = normalize_dataframe_columns(df)
    dates = pd.Series(pd.NaT, index=rows.index, dtype="datetime64[ns]")
    if rows.empty or "Type" not in rows.columns:
        return dates

    report_type = rows["Type"].astype(str).str.strip().str.casefold()
    for type_names, date_column in [
        (["admission", "opd"], "DOA"),
        (["discharge"], "DOD"),
    ]:
        if date_column not in rows.columns:
            continue
        type_mask = report_type.isin(type_names)
        dates.loc[type_mask] = _coerce_datetime(rows.loc[type_mask, date_column])
    return dates


def infer_years(df):
    if df is None or df.empty:
        return []
    dates = comparison_event_dates(df).dropna()
    return sorted(int(year) for year in dates.dt.year.unique())


def filter_by_year_month(df, year=None, month=None):
    if df is None or df.empty:
        return df.copy()
    rows = df.copy()
    mask = pd.Series(True, index=rows.index)
    if year is not None:
        date_mask = pd.Series(False, index=rows.index)
        date_found = False
        for date_column in ["DOA", "DOD"]:
            if date_column in rows.columns:
                date_found = True
                date_mask |= _coerce_datetime(rows[date_column]).dt.year.eq(int(year))
        if not date_found:
            date_mask = pd.Series(True, index=rows.index)
        mask &= date_mask

    if month is not None and month != "All Months":
        month_value = str(month).strip().title()
        month_mask = pd.Series(False, index=rows.index)
        if "Month" in rows.columns:
            month_mask |= rows["Month"].fillna("").astype(str).str.strip().str.title().eq(month_value)
        for date_column in ["DOA", "DOD"]:
            if date_column in rows.columns:
                month_mask |= _coerce_datetime(rows[date_column]).dt.strftime("%B").eq(month_value)
        mask &= month_mask

    return rows.loc[mask].copy()


def filter_dashboard_comparison(
    df, year, month=None, week=None, period=None, day=None
):
    rows = (
        df
        if "__comparison_event_date" in df.columns
        else normalize_dataframe_columns(df)
    )
    if rows.empty or "Type" not in rows.columns:
        return rows.copy()

    event_dates = comparison_event_dates(rows)
    mask = event_dates.dt.year.eq(int(year)).fillna(False)
    if week is not None:
        week_of_month = ((event_dates.dt.day - 1) // 7 + 1).astype("Int64")
        mask &= week_of_month.eq(int(week)).fillna(False)
    if day is not None:
        mask &= event_dates.dt.day.eq(int(day)).fillna(False)

    months = [month] if month and month != "All Months" else None
    if months:
        mask &= _month_filter_mask(rows, months)
    if period:
        mask &= _period_for_rows(rows).isin([period])
    return rows.loc[mask].copy()


def filter_comparison_range(df, start_date, end_date):
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        return df.iloc[0:0].copy()
    event_dates = comparison_event_dates(df)
    mask = event_dates.between(start, end).fillna(False)
    return df.loc[mask].copy()


def comparison_change(current_value, previous_value, metric_kind="percent"):
    if pd.isna(current_value) or pd.isna(previous_value):
        return None
    current_value = float(current_value)
    previous_value = float(previous_value)
    if previous_value == 0:
        return 0.0 if current_value == 0 else "New" if current_value > 0 else None
    return ((current_value - previous_value) / previous_value) * 100


def comparison_label(value, metric_kind="percent"):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    if value == "New":
        return "New"
    if value == 0:
        return "0.0%"
    return f"{value:+,.1f}%"


def comparison_table_rows(
    left_df, right_df, dimension_key, additional_frames=None, period_labels=None
):
    left_breakdown = left_df if isinstance(left_df, pd.DataFrame) else pd.DataFrame()
    right_breakdown = right_df if isinstance(right_df, pd.DataFrame) else pd.DataFrame()
    if left_breakdown.empty and right_breakdown.empty and period_labels is None:
        return pd.DataFrame(columns=[dimension_key, "2025", "2026", "Change"])

    def normalize_frame(frame):
        if frame.empty:
            return pd.DataFrame(columns=[dimension_key, "Value"])

        value_column = next(
            (column for column in ["UHID Count", "IPD Revenue", "Value"] if column in frame.columns),
            None,
        )
        if value_column is None:
            numeric_columns = [
                column for column in frame.columns
                if pd.api.types.is_numeric_dtype(frame[column])
            ]
            if not numeric_columns:
                return pd.DataFrame(columns=[dimension_key, "Value"])
            value_column = numeric_columns[0]

        if dimension_key not in frame.columns:
            dimension_candidates = [
                column for column in frame.columns if column not in [value_column, "Value"]
            ]
            if not dimension_candidates:
                return pd.DataFrame(columns=[dimension_key, "Value"])
            dimension_key_name = "Name" if "Name" in frame.columns else dimension_candidates[0]
            frame = frame.rename(columns={dimension_key_name: dimension_key})

        return frame[[dimension_key, value_column]].rename(columns={value_column: "Value"})

    left_rows = normalize_frame(left_breakdown)
    right_rows = normalize_frame(right_breakdown)
    if period_labels is not None:
        additional_rows = [normalize_frame(frame) for frame in (additional_frames or [])]
        period_rows = [left_rows, right_rows, *additional_rows][:len(period_labels)]
        lookups = [rows.set_index(dimension_key)["Value"].to_dict() for rows in period_rows]
        all_names = list(dict.fromkeys(
            name for rows in period_rows for name in rows[dimension_key].tolist()
        ))
        result = []
        for name in all_names:
            values = [float(lookup.get(name, 0) or 0) for lookup in lookups]
            result.append({
                dimension_key: name,
                **dict(zip(period_labels, values)),
                "Change": comparison_change(values[-1], values[0]),
            })
        return pd.DataFrame(result, columns=[dimension_key, *period_labels, "Change"])

    left_lookup = left_rows.set_index(dimension_key)["Value"].to_dict()
    right_lookup = right_rows.set_index(dimension_key)["Value"].to_dict()
    all_names = list(dict.fromkeys([*left_rows[dimension_key].tolist(), *right_rows[dimension_key].tolist()]))
    rows = []
    for name in all_names:
        left_value = float(left_lookup.get(name, 0) or 0)
        right_value = float(right_lookup.get(name, 0) or 0)
        change = comparison_change(right_value, left_value)
        rows.append({
            dimension_key: name,
            "2025": left_value,
            "2026": right_value,
            "Change": change,
        })
    return pd.DataFrame(rows)


def department_doctor_comparison(left_df, right_df, metric="discharge"):
    if metric == "discharge":
        left_breakdown = discharge_uhid_breakdown(left_df)
        right_breakdown = discharge_uhid_breakdown(right_df)
        key_name = "Dept. Name" if "Dept. Name" in left_breakdown else "Doctor Name"
        left_values = left_breakdown.get(key_name)
        right_values = right_breakdown.get(key_name)
        if left_values is None and right_values is None:
            return pd.DataFrame(columns=["Department/Doctor", "2025", "2026", "Change"])
    else:
        left_breakdown = admission_uhid_breakdown(left_df)
        right_breakdown = admission_uhid_breakdown(right_df)
        key_name = "Dept. Name" if "Dept. Name" in left_breakdown else "Doctor Name"
        left_values = left_breakdown.get(key_name)
        right_values = right_breakdown.get(key_name)
        if left_values is None and right_values is None:
            return pd.DataFrame(columns=["Department/Doctor", "2025", "2026", "Change"])

    if left_values is None:
        left_values = pd.DataFrame(columns=[key_name, "UHID Count"])
    if right_values is None:
        right_values = pd.DataFrame(columns=[key_name, "UHID Count"])
    left_values = left_values[[key_name, "UHID Count"]].rename(columns={key_name: "Name", "UHID Count": "Value"})
    right_values = right_values[[key_name, "UHID Count"]].rename(columns={key_name: "Name", "UHID Count": "Value"})
    order = list(dict.fromkeys([*left_values["Name"].tolist(), *right_values["Name"].tolist()]))
    rows = []
    for name in order:
        left_value = float(left_values[left_values["Name"] == name]["Value"].sum()) if name in left_values["Name"].tolist() else 0
        right_value = float(right_values[right_values["Name"] == name]["Value"].sum()) if name in right_values["Name"].tolist() else 0
        rows.append({
            "Department/Doctor": name,
            "2025": left_value,
            "2026": right_value,
            "Change": comparison_change(right_value, left_value),
        })
    return pd.DataFrame(rows)