# Pharmacy Analytics Dashboard

An independent, local-first dashboard for comparing pharmacy Sales Revenue,
Collection, and New Purchase reports across three hospitals. Surya Hospital
has separate IPD and OPD pharmacies; NIMMS Hospital (Haldoni) and Vedansh
Hospital (Dadri) each have one Pharmacy. It does not read or modify the older
hospital dashboard.

## Run on Windows

1. Install Python 3.10 or newer.
2. Open PowerShell in this folder.
3. Install the Excel reader once:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Start the app:

   ```powershell
   python server.py
   ```

5. Open <http://127.0.0.1:8000>.

The app binds to `127.0.0.1`, so it is only available on this computer by
default. SQLite data is stored in `pharmacy.db` beside `server.py`.

## Dashboard

The single-page dashboard compares Sales Revenue, Collection, and New Purchase
for the selected hospital and pharmacy. Its default range is the financial year
from April 1, 2025 through March 31, 2026, compared with the same dates last
year. Choose **Year to date** to switch to January 1 through the latest
available transaction date (up to today). The exact
dates for both periods are shown above the dashboard and alongside the chart.
The dashboard also shows the latest imported transaction date within the
selected period; confirm that all expected reports have been imported.
Choose **Custom range** to compare any selected dates with the corresponding
dates one year earlier. **Pending amount** is the sum of Cash Due and Credit Due
from the selected hospital/pharmacy's Sales Revenue report. It is shown only
when both due columns are populated for the report rows. A decrease versus the
same dates last year is favorable from the hospital's point of view.
Monetary amounts retain source precision up to two decimal places. The facility
table shows both periods side by side
with a grand total for each, including pending amount. Select a report in the monthly comparison chart
to focus the dashboard on that month; the chart compares each month with the
same month one year earlier and ends with the selected report's grand totals
for both periods. Select a hospital or pharmacy name
in the facility table to filter the dashboard to that facility. **Download
dashboard** creates a self-contained offline copy with the same dashboard
layout and styling. It starts with the currently applied filters and lets you
change hospital, pharmacy, exact date range, and chart report; daily facility
totals are embedded in the file so the dashboard can be recalculated offline.
The blue top header contains the module navigation, **Refresh**, and
**Download dashboard** controls. The dashboard has separate **Pharmacy
dashboard** and **Hospital comparison** modules. The pharmacy module contains
the selected-hospital/pharmacy metrics, trends, and suppliers. **Data management**
contains report imports and stored files. The hospital comparison module
presents the all-hospital summary, month-by-month comparison, and hospital supplier charts without the
individual hospital/pharmacy filters. The selected date range is shared by both
modules. The hospital comparison shows side-by-side cards for all three
hospitals with Sales, Collection, Purchase, Pending, Cash Due, and Credit Due, including changes
versus the same dates last year, followed by a month-by-month chart. Pending
is Cash Due plus Credit Due and is shown only when both due columns are complete
for all included sales rows. The chart compares all hospitals and their pharmacies
for the selected Sales, Collection, or Purchase report sheet, regardless of
the pharmacy dashboard's hospital/pharmacy selection. The selected date range
applies to the hospital cards and all hospital bars, so all three hospitals
remain visible while their values update for that range. Exact values are available on each
bar; “—” means report data is unavailable for that month. Supplier purchase
charts appear below the monthly graph for each hospital, use the same date
range, and can show the top, lowest, or all suppliers. This comparison remains
independent of the selected hospital and pharmacy filters.
Date filters and chart ranges are limited to the latest transaction date in the
imported data.
The month-by-month chart shows all months without horizontal scrolling. It
adapts to narrower screens by displaying fewer month columns per row, and uses
hospital-matched colors for the values.
The downloaded dashboard has a **Print / Save as PDF** button that formats the
report to fit on one A4 landscape page, including longer date ranges.
Percentage change is for the selected current-year period only, compared with
the same dates in the previous year. It is calculated as (current period −
same period last year) ÷ the absolute value of last year's amount × 100.
Increases above 100% are shown
as their actual percentage rather than capped at “>100%”. The chart shows the
selected date range alongside the matching dates one year earlier, with
separate colors and visible values.

For each import, select one hospital, its pharmacy, and one or more (up to
three) Excel or CSV files (50 MB maximum for the complete batch). You can upload
a single Sales, Collection, or Purchase report; missing report types remain
empty and do not block import. Surya Hospital lets you select IPD or OPD; the
other hospitals have one Pharmacy. Include the report type in each filename (for
example, `Sales Report.xlsx`, `Collection Report.xlsx`, or `Purchase
Report.xlsx`). The app detects the type from the filename and maps date/amount
columns using the report headers, so there are no per-file settings to repeat.
Purchase reports with a recognized Supplier/Vendor/Party Name column are included
in the supplier purchase chart, which ranks suppliers by the report's final amount.
The batch is validated as a whole: if a file fails validation or duplicates an
existing report, none of the batch is stored. On success, imported rows update
the dashboard, and the original files are retained in SQLite and listed under
**Stored reports** for download. Hospital names and pharmacy assignments are
validated on the server. Older imports already in `pharmacy.db` are displayed
and aggregated using the new hospital/pharmacy names without rewriting their
historical records; their original files were not archived and are not
available to download.

The dashboard's **Monthly report totals** table groups imported report amounts
by calendar month and year, summing Final Amt for each report type. These
summed values are labeled as Sales Revenue, Collection Revenue, and Purchase
Value. The online dashboard's date range and year-over-year comparison controls
and standalone HTML dashboard export use exact dates for filtering and
year-over-year comparisons. If the dashboard is served by an older server
version without the export-data route, the download remains available using
the older monthly summary endpoint; that compatibility export clearly switches
its date filters to month precision.

The automatic column matcher expects date and amount columns for Sales and
Collection. Purchase accepts an amount column or both quantity and unit cost.
Common headers such as Bill Date, Final Amt, Net Amount, Qty, and Rate are
recognized. If a required column cannot be detected, rename the report headers
to a recognizable form and try again.
Dates in reports are normalized on import; common ISO, `DD-MM-YYYY`,
`DD/MM/YYYY`, dotted, month-name, and Excel serial date values are supported.
Date ranges shown in the dashboard use `DD-MM-YYYY`. Slash-separated ambiguous
dates are interpreted day-first.

## Financial definitions and limitations

- Sales Revenue, Collection, and New Purchase are separate measures.
- `Collections - purchases` is a cash-flow indicator only; it is **not profit**.
- New Purchase totals do not equal the cost of medicines sold. Profit requires
  a confirmed cost-of-goods-sold method plus opening inventory/stock valuation
  (and any other costs included in the requested profit definition). Profit is
  therefore intentionally not calculated by this starter app.
- Product-level margin also needs consistent SKU/product matching and stock
  costing rules across sales and purchase reports.
- Reports should have dates and monetary values. Date formats are normalized
  during import; dates that cannot be parsed reject the report instead of being
  silently skipped.
- `.xlsx` and `.csv` are supported. Save legacy `.xls` files as `.xlsx` or CSV.
- One exact file can only be imported once per hospital, pharmacy, and report
  type. Row-level cross-file duplicate reconciliation is not yet implemented.

## Data protection and backup

Use anonymized exports; patient names, phone numbers, UHIDs, or other personal
data are not needed. Keep the app on a trusted computer, restrict access to the
Windows account, and back up `pharmacy.db` regularly while the app is stopped.
The database contains imported transaction data and the original uploaded
files, so protect the backup as carefully as the source reports. Do not expose
this starter app to the public internet.

## Run tests

```powershell
python -m unittest
```
