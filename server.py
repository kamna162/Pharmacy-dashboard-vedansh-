from __future__ import annotations

import json
import hashlib
import logging
import mimetypes
import os
import re
import threading
import traceback
import webbrowser
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.request
import urllib.error

import pandas as pd

import auth
from comparison import backend
from comparison import database
from comparison import dashboard_service
from comparison import prediction_service
from pharmacy import server as pharmacy_server


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
COMPARISON_STATIC = ROOT / "comparison" / "static"
PHARMACY_STATIC = ROOT / "pharmacy" / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = 80 * 1024 * 1024
logger = logging.getLogger("hospital_dashboard")


def _development_version():
    digest = hashlib.sha256()
    ignored_directories = {
        ".git", ".pytest_cache", ".venv", "__pycache__",
        "backup", "tests", "uploads",
    }
    for base, python_only in (
        (ROOT, True),
        (STATIC, False),
        (COMPARISON_STATIC, False),
        (PHARMACY_STATIC, False),
    ):
        for current, directories, files in os.walk(base):
            directories[:] = [
                name for name in directories if name not in ignored_directories
            ]
            for filename in files:
                if python_only and not filename.endswith(".py"):
                    continue
                path = Path(current) / filename
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                digest.update(
                    path.relative_to(ROOT).as_posix().encode("utf-8")
                )
                digest.update(str(stat.st_mtime_ns).encode("ascii"))
                digest.update(str(stat.st_size).encode("ascii"))
    return digest.hexdigest()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "HospitalDashboard/1.0"

    def do_GET(self):
        request = urlparse(self.path)
        try:
            if request.path == "/api/auth/status":
                username = auth.username_from_cookie(self.headers.get("Cookie"))
                self._json_response({
                    "setup_required": not auth.is_setup_complete(),
                    "authenticated": username is not None,
                    "username": username,
                })
            elif request.path == "/api/dev/version":
                enabled = os.environ.get("DASHBOARD_DEV_RELOAD") == "1"
                payload = {"enabled": enabled}
                if enabled:
                    payload["version"] = _development_version()
                self._json_response(payload)
            elif not self._is_public_path(request.path) and not self._authenticated():
                if request.path.startswith("/api/") or request.path.startswith("/pharmacy-api/"):
                    self._json_response({"error": "Please log in to continue."}, status=401)
                else:
                    self.send_response(303)
                    self.send_header("Location", "/?login=required")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
            elif request.path == "/api/health":
                self._json_response({"status": "ok"})
            elif request.path == "/api/options":
                self._json_response(dashboard_service.dashboard_options())
            elif request.path == "/api/uploads":
                parameters = self._query_dict(request.query)
                hospital_name = (
                    parameters.get("hospitalName")
                    or parameters.get("hospital")
                )
                self._json_response({
                    "uploads": dashboard_service.list_report_uploads(hospital_name)
                })
            elif request.path == "/api/scraper/status":
                self._json_response(scraper_importer.get_scraper_status())
            elif request.path == "/api/comparison/options":
                self._json_response(dashboard_service.comparison_options())
            elif request.path == "/api/dashboard":
                self._json_response(
                    dashboard_service.get_dashboard(self._query_dict(request.query))
                )
            elif request.path == "/api/comparison":
                parameters = self._query_dict(request.query)
                filters = json.loads(parameters.pop("filters", "{}"))
                parameters["filters"] = filters
                self._json_response(dashboard_service.get_comparison(parameters))
            elif request.path == "/api/prediction":
                parameters = self._query_dict(request.query)
                parameters["filters"] = json.loads(parameters.pop("filters", "{}"))
                self._json_response(prediction_service.get_prediction(parameters))
            elif request.path == "/api/comparison/export.csv":
                parameters = self._query_dict(request.query)
                filters = json.loads(parameters.pop("filters", "{}"))
                parameters["filters"] = filters
                content = dashboard_service.export_comparison_csv(parameters)
                filename = "hospital_comparison_report.csv"
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
            elif request.path == "/api/export.csv":
                self._export_csv(self._query_dict(request.query))
            elif request.path == "/api/export/interactive.html":
                content = dashboard_service.export_interactive_html()
                filename = "Vedansh_Medicare_Interactive_Dashboard.html"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)

            elif request.path == "/":
                self._static_response("index.html")
            elif request.path == "/dashboard":
                self._static_response("dashboard.html", (COMPARISON_STATIC,))
            elif request.path in ("/pharmacy", "/pharmacy/"):
                self._static_response("index.html", (PHARMACY_STATIC,))
            elif request.path == "/pharmacy/app.js":
                self._static_response("app.js", (PHARMACY_STATIC,))
            elif request.path == "/pharmacy/styles.css":
                self._static_response("styles.css", (PHARMACY_STATIC,))
            elif request.path.startswith("/pharmacy-api/"):
                self._proxy_pharmacy_request("GET", request)
            elif request.path.startswith("/assets/"):
                relative_path = request.path.removeprefix("/assets/")
                self._static_response(relative_path)
            else:
                self._json_response({"error": "Not found."}, status=404)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self._json_response({"error": str(error)}, status=400)
        except Exception as error:
            logger.error("GET %s failed: %s\n%s", request.path, error, traceback.format_exc())
            self._json_response(
                {"error": "The dashboard request failed. Check the server log for details."},
                status=500,
            )

    def _proxy_pharmacy_request(self, method, request):
        path = "/api" + request.path.removeprefix("/pharmacy-api")
        target = f"http://127.0.0.1:8001{path}"

        if request.query:
            target += "?" + request.query

        data = None
        if method in ("POST", "DELETE"):
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length)

        headers = {"Origin": "http://127.0.0.1:8001"}
        for name in ("Content-Type", "Accept"):
            value = self.headers.get(name)
            if value:
                headers[name] = value

        proxy_request = urllib.request.Request(
            target, data=data, headers=headers, method=method
        )

        try:
            response = urllib.request.urlopen(proxy_request, timeout=120)
        except urllib.error.HTTPError as error:
            response = error

        with response:
            body = response.read()
            self.send_response(response.status)
            self.send_header(
                "Content-Type",
                response.headers.get("Content-Type", "application/json"),
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)



    def do_POST(self):
        request = urlparse(self.path)
        try:
            if request.path == "/api/auth/setup":
                payload = self._json_body()
                if not isinstance(payload, dict):
                    raise ValueError("Account details must be a JSON object.")
                username = auth.create_account(
                    payload.get("username"), payload.get("password")
                )
                token = auth.create_session(username)
                self._json_response(
                    {"authenticated": True, "username": username},
                    status=201,
                    set_cookie=self._session_cookie(token),
                )
            elif request.path == "/api/auth/login":
                payload = self._json_body()
                if not isinstance(payload, dict):
                    raise ValueError("Login details must be a JSON object.")
                username = auth.verify_credentials(
                    payload.get("username"), payload.get("password")
                )
                if username is None:
                    self._json_response(
                        {"error": "The username or password is incorrect."},
                        status=401,
                    )
                else:
                    token = auth.create_session(username)
                    self._json_response(
                        {"authenticated": True, "username": username},
                        set_cookie=self._session_cookie(token),
                    )
            elif request.path == "/api/auth/logout":
                auth.destroy_session(self.headers.get("Cookie"))
                self._json_response(
                    {"authenticated": False},
                    set_cookie=self._session_cookie("", expired=True),
                )
            elif not self._authenticated():
                self._json_response({"error": "Please log in to continue."}, status=401)
            elif request.path.startswith("/pharmacy-api/"):
                self._proxy_pharmacy_request("POST", request)
            elif request.path == "/api/upload":
                files, fields = self._multipart_files()
                result = dashboard_service.upload_files(
                    files,
                    hospital_name=fields.get("hospitalName"),
                )
                database.init_database()
                self._json_response({"success": True, "upload": result}, status=201)
            elif request.path == "/api/datasets/delete":
                payload = self._json_body()
                deleted = database.delete_dataset(payload.get("datasetId"))
                self._json_response({"success": True, "deletedRows": deleted})
            elif request.path == "/api/uploads/delete":
                payload = self._json_body()
                result = dashboard_service.delete_report_upload(
                    payload.get("uploadId")
                )
                self._json_response({"success": True, "result": result})
            elif request.path == "/api/uploads/include":
                payload = self._json_body()
                result = dashboard_service.set_report_upload_included(
                    payload.get("uploadId"),
                    payload.get("included"),
                )
                self._json_response({"success": True, "result": result})
            elif request.path == "/api/scraper/import":
                result = scraper_importer.import_scraper_folder()
                self._json_response({"success": True, "result": result})
            else:
                self._json_response({"error": "Not found."}, status=404)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self._json_response({"error": str(error)}, status=400)
        except auth.AuthError as error:
            self._json_response({"error": str(error)}, status=error.status)
        except Exception as error:
            logger.error("POST %s failed: %s\n%s", request.path, error, traceback.format_exc())
            self._json_response(
                {"error": "The dashboard request failed. Check the server log for details."},
                status=500,
            )

    def do_DELETE(self):
        request = urlparse(self.path)
        try:
            if not self._authenticated():
                self._json_response({"error": "Please log in to continue."}, status=401)
            elif request.path.startswith("/pharmacy-api/"):
                self._proxy_pharmacy_request("DELETE", request)
            else:
                self._json_response({"error": "Not found."}, status=404)
        except Exception as error:
            logger.error("DELETE %s failed: %s\n%s", request.path, error, traceback.format_exc())
            self._json_response(
                {"error": "The dashboard request failed. Check the server log for details."},
                status=500,
            )

    def _authenticated(self):
        return auth.username_from_cookie(self.headers.get("Cookie")) is not None

    @staticmethod
    def _is_public_path(path):
        return (
            path == "/"
            or path.startswith("/assets/")
            or path in {
                "/api/health",
                "/api/dev/version",
                "/api/auth/status",
                "/api/auth/setup",
                "/api/auth/login",
                "/api/auth/logout",
            }
        )

    @staticmethod
    def _session_cookie(token, expired=False):
        max_age = 0 if expired else auth.SESSION_LIFETIME_SECONDS
        return (
            f"{auth.SESSION_COOKIE}={token}; Path=/; HttpOnly; "
            f"SameSite=Strict; Max-Age={max_age}"
        )

    def _query_dict(self, query):
        parameters = parse_qs(query, keep_blank_values=True)
        return {
            key: values if key in {"month", "period"} else values[-1]
            for key, values in parameters.items()
        }

    def _json_body(self):
        length = self._content_length()
        if length > MAX_REQUEST_BYTES:
            raise ValueError("The request is too large.")
        payload = self.rfile.read(length)
        return json.loads(payload.decode("utf-8"))

    def _multipart_files(self):
        length = self._content_length()
        if length > MAX_REQUEST_BYTES:
            raise ValueError("The upload request exceeds the 80 MB request limit.")
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Upload must use a multipart form.")
        body = self.rfile.read(length)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
            + body
        )
        files = []
        fields = {}
        for part in message.iter_parts():
            field_name = part.get_param("name", header="content-disposition")
            if field_name == "file":
                filename = part.get_filename()
                if not filename:
                    raise ValueError("Each uploaded report must have a file name.")
                file_bytes = part.get_payload(decode=True) or b""
                if len(file_bytes) > MAX_UPLOAD_BYTES:
                    raise ValueError("Each report must be no larger than 25 MB.")
                safe_name = re.sub(r"[\r\n]", "", Path(filename).name)
                files.append((safe_name, file_bytes))
            elif field_name:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                fields[field_name] = payload.decode(charset).strip()
        if not files:
            raise ValueError("Choose at least one report to upload.")
        return files, fields

    def _multipart_file(self):
        files, _ = self._multipart_files()
        return files[0]

    def _content_length(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid request size.") from error
        if length < 0:
            raise ValueError("Invalid request size.")
        return length

    def _export_csv(self, query):
        hospital_name = query.get("hospital")
        dataset_id = query.get("datasetId", "")
        if hospital_name:
            datasets = [
                item for item in database.list_saved_datasets()
                if item["hospital_name"] == hospital_name
            ]
            if not datasets:
                raise ValueError("The selected hospital is not available.")
            frame = database.load_hospital_data(hospital_name=hospital_name)
            export_name = hospital_name
        else:
            if not dataset_id:
                raise ValueError("Choose a dataset before exporting.")
            dataset = next(
                (
                    item for item in database.list_saved_datasets()
                    if item["dataset_id"] == dataset_id
                ),
                None,
            )
            if dataset is None:
                raise ValueError("The selected dataset is not available.")
            frame = database.load_hospital_data(dataset_id=dataset_id)
            export_name = Path(dataset["source_file"]).stem
        months = query.get("month", "")
        periods = query.get("period", "")
        month_filter = (
            [part for item in months for part in item.split(",") if part]
            if isinstance(months, list) else [item for item in months.split(",") if item]
        )
        period_filter = (
            [part for item in periods for part in item.split(",") if part]
            if isinstance(periods, list) else [item for item in periods.split(",") if item]
        )
        cleaned = backend.clean_data(frame)
        filtered = backend.apply_filters(
            cleaned, month=month_filter, period=period_filter
        )
        start_date = query.get("startDate")
        end_date = query.get("endDate")
        if bool(start_date) != bool(end_date):
            raise ValueError("Both range start and end dates are required.")
        if start_date:
            filtered = backend.filter_comparison_range(
                filtered, start_date, end_date
            )
        content = filtered.to_csv(index=False).encode("utf-8-sig")
        filename = re.sub(
            r"[^A-Za-z0-9._ -]+",
            "_",
            export_name + "_dashboard_export.csv",
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"',
        )
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _static_response(self, relative_path, roots=None):
        static_path = None
        for root in roots or (STATIC, COMPARISON_STATIC):
            candidate = (root / relative_path).resolve()
            if candidate.is_relative_to(root.resolve()) and candidate.is_file():
                static_path = candidate
                break
        if static_path is None:
            self._json_response({"error": "Not found."}, status=404)
            return
        content = static_path.read_bytes()
        content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _json_response(self, payload, status=200, set_cookie=None):
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format_string, *args):
        logger.info("%s - %s", self.address_string(), format_string % args)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database.init_database()
    # Load and clean the data in the background so the first page opens quickly.
    threading.Thread(target=dashboard_service.warm_up, daemon=True).start()
    host = "127.0.0.1"
    port = int(os.environ.get("DASHBOARD_PORT", "8010"))
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    pharmacy_httpd = None
    try:
        pharmacy_server.init_db()
        pharmacy_httpd = ThreadingHTTPServer(
            (pharmacy_server.HOST, pharmacy_server.PORT),
            pharmacy_server.Handler,
        )
        threading.Thread(target=pharmacy_httpd.serve_forever, daemon=True).start()
        dashboard_url = f"http://{host}:{port}"
        print(f"Executive dashboard running at {dashboard_url}")
        if os.environ.get("DASHBOARD_DEV_RELOAD") != "1":
            try:
                if not webbrowser.open(dashboard_url):
                    logger.warning("Could not open a browser automatically; visit %s", dashboard_url)
            except webbrowser.Error as error:
                logger.warning("Could not open a browser automatically: %s", error)
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        httpd.server_close()
        if pharmacy_httpd is not None:
            pharmacy_httpd.shutdown()
            pharmacy_httpd.server_close()


if __name__ == "__main__":
    main()
