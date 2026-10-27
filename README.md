# Vedansh Medicare Executive Dashboard

A local dashboard for hospital reporting, comparison, prediction, and pharmacy analytics.

## Run locally on Windows

Requires Python 3.13.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python dev_server.py
```

The module-selection dashboard opens in your browser after the server starts
(or visit <http://127.0.0.1:8010>) to select the Hospital or Pharmacy module.
On first visit, create a dashboard username and password. Both modules and their
data endpoints require login; use **Log out** in the top-right corner when
finished. The password is stored locally as a salted hash in
`dashboard_auth.db`, and login sessions expire when the server restarts or
after eight hours. Keep the dashboard on this computer and do not expose it to
the public internet.
The application creates `hospital_data.db` locally the first time it starts.
The pharmacy API is started alongside the dashboard server on
`127.0.0.1:8001`; do not start `pharmacy/server.py` separately while the
combined server is running.

`dev_server.py` watches Python and frontend files during local development.
Changes to Python code restart the server automatically; changes to frontend
files reload the open dashboard automatically. Run `python server.py` instead
if you do not want automatic reloads. To enable this after running the regular
server, stop it once and launch `python dev_server.py`; subsequent code edits
will reload automatically.

## Project layout

- `comparison/` contains the Hospital/Comparison Python services, dashboard
  assets, and the comparison update utility.
- `pharmacy/` contains the Pharmacy API server and its frontend assets.
- `server.py` and `dev_server.py` are shared entry points that start and serve
  both modules. `static/` contains the shared module selector and branding.
- Local databases and report uploads remain in their existing locations; moving
  source files does not move or delete that data.

Raw admission, discharge, and service reports are stored under the local
`uploads/raw_reports/` directory and combined into a separate dataset for the
selected hospital. The upload-management API can list reports with
`GET /api/uploads?hospitalName=...`, and include or remove reports with
`POST /api/uploads/include` and `POST /api/uploads/delete`. These files and
the database are local application data and must not be committed.

Select multiple reports in the file picker to upload them together. The
dashboard's date pickers and Year/Month choices use event dates from included
data for the selected hospital. Picker dates before 2000 or more than 366 days
in the future are excluded; Discharge rows use DOD, and Admission/OPD rows use
DOA. These picker bounds do not remove records: Full Range continues to include
all saved rows, including rows with outlier dates.

## NuvertOS scraper storage

The existing Windows Task Scheduler task can continue running
`website_data_scraper.py` at 10:30; do not create a second schedule. After all
three NIMMS reports finish downloading, the script invokes the dashboard
importer. This integration is for NIMMS only; Surya remains on the existing
manual upload flow until its separate source is provided. The default
dashboard path is
`C:\Users\ASUS\Downloads\rishu (2)\rishu`; set `DASHBOARD_APP_DIR` if the
project is installed elsewhere. The scraper output folder defaults to
`C:\Users\ASUS\Desktop\nvos_to_db`; set `DASHBOARD_SCRAPER_FOLDER` in both
the dashboard and scheduled-task environments if that folder is moved.

The importer recognizes the timestamped Admission, Discharge, and Service
workbooks and selects only the newest complete batch; other recognized files
in the scraper folder are never imported. That batch replaces the previous
scraper data and archived scraper rows, while separately uploaded reports are
preserved. The workbooks are combined through the same report-combining path
used by manual uploads. Recognized inbox workbooks are removed only after the
replacement has been persisted successfully.

Scraper status, the latest complete batch, and the manual **Import reports**
retry action are available inside **Manage data**. If an import fails, source
workbooks remain in the scraper folder for retry. The database and downloaded
workbooks are local application data and must not be committed.

## Keep hospital data private

This application can store patient-identifying information and uploaded
hospital reports in its local database. Do not commit the database, source
spreadsheets, CSV files, generated reports, or downloaded interactive HTML
exports. The `.gitignore` excludes common local data files, but review every
file before publishing because ignore rules cannot protect files already
tracked by Git.

The dashboard is intended for local use. Do not expose it to the public
internet or use it with real patient data on a public hosting service without
appropriate authentication, access controls, and privacy review.
