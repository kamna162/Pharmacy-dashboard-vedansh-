import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import auth
import server
from comparison import database
from pharmacy import server as pharmacy_server


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class ModuleSelectionRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.auth_db_path_patch = patch.object(
            auth, "AUTH_DB_PATH", Path(self.temp_directory.name) / "dashboard_auth.db"
        )
        self.auth_db_path_patch.start()
        auth.reset_sessions()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:{}".format(self.httpd.server_address[1])

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        auth.reset_sessions()
        self.auth_db_path_patch.stop()
        self.temp_directory.cleanup()

    def request(self, path, method="GET", payload=None, cookie=None, follow_redirects=True):
        headers = {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        opener = None if follow_redirects else build_opener(NoRedirectHandler())
        try:
            response = opener.open(request, timeout=5) if opener else urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        return response.status, response.headers, response.read().decode("utf-8")

    def test_home_page_selects_hospital_or_pharmacy_and_has_login_controls(self):
        status, _, page = self.request("/")

        self.assertEqual(status, 200)
        self.assertIn('href="/dashboard"', page)
        self.assertIn('href="/pharmacy/"', page)
        self.assertIn("Hospital operations", page)
        self.assertIn("Pharmacy performance", page)
        self.assertIn('id="authToggle"', page)
        self.assertIn('id="authDialog"', page)
        self.assertNotIn("LOCAL DASHBOARD", page)

    def test_landing_page_styles_and_script_are_available(self):
        status, _, styles = self.request("/assets/module-selector.css")
        self.assertEqual(status, 200)
        self.assertIn("overflow: hidden", styles)
        self.assertIn("100svh", styles)
        self.assertIn("repeat(2, minmax(0, 1fr))", styles)

        status, _, script = self.request("/assets/module-selector.js")
        self.assertEqual(status, 200)
        self.assertIn("/api/auth/status", script)
        self.assertIn("/api/dev/version", script)

    def test_module_assets_are_served_from_their_module_folders(self):
        status, _, body = self.request("/assets/dashboard.js")
        self.assertEqual(status, 200)
        self.assertTrue(body)

        status, headers, _ = self.request(
            "/api/auth/setup",
            method="POST",
            payload={"username": "manager", "password": "private-pass-123"},
        )
        self.assertEqual(status, 201)
        cookie = headers.get("Set-Cookie").split(";", 1)[0]

        for path in (
            "/assets/dashboard.js",
            "/assets/dashboard.css",
            "/pharmacy/app.js",
            "/pharmacy/styles.css",
        ):
            with self.subTest(path=path):
                status, _, body = self.request(path, cookie=cookie)
                self.assertEqual(status, 200)
                self.assertTrue(body)

    def test_pharmacy_server_serves_its_assets_from_its_package(self):
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), pharmacy_server.Handler
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = "http://127.0.0.1:{}".format(httpd.server_address[1])
            for path in ("/", "/app.js", "/styles.css"):
                with self.subTest(path=path):
                    with urlopen(base_url + path, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertTrue(response.read())
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_comparison_data_paths_remain_at_project_root(self):
        project_root = Path(server.ROOT)
        self.assertEqual(database.DATABASE_PATH, project_root / "hospital_data.db")
        self.assertEqual(
            database.UPLOADS_DIR,
            project_root / "uploads" / "raw_reports",
        )

    def test_development_reload_status_is_quiet_when_disabled(self):
        with patch.dict(os.environ, {"DASHBOARD_DEV_RELOAD": ""}):
            status, _, body = self.request("/api/dev/version")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["enabled"])

    def test_authentication_protects_both_modules_and_data_apis(self):
        for path in ("/dashboard", "/pharmacy/"):
            with self.subTest(path=path):
                status, headers, _ = self.request(path, follow_redirects=False)
                self.assertEqual(status, 303)
                self.assertEqual(headers.get("Location"), "/?login=required")

        for path in ("/api/dashboard", "/pharmacy-api/summary"):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 401)
                self.assertIn("log in", body)

        status, headers, body = self.request(
            "/api/auth/setup",
            method="POST",
            payload={"username": "manager", "password": "private-pass-123"},
        )
        self.assertEqual(status, 201)
        cookie = headers.get("Set-Cookie").split(";", 1)[0]
        self.assertIn("HttpOnly", headers.get("Set-Cookie"))
        self.assertIn("SameSite=Strict", headers.get("Set-Cookie"))
        self.assertEqual(json.loads(body)["username"], "manager")

        for path in ("/dashboard", "/pharmacy/"):
            with self.subTest(authenticated_path=path):
                status, _, _ = self.request(path, cookie=cookie)
                self.assertEqual(status, 200)

    def test_first_run_setup_login_and_logout(self):
        status, _, body = self.request("/api/auth/status")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["setup_required"])

        status, _, _ = self.request(
            "/api/auth/setup",
            method="POST",
            payload={"username": "manager", "password": "short"},
        )
        self.assertEqual(status, 400)

        status, headers, _ = self.request(
            "/api/auth/setup",
            method="POST",
            payload={"username": "manager", "password": "private-pass-123"},
        )
        self.assertEqual(status, 201)
        setup_cookie = headers.get("Set-Cookie").split(";", 1)[0]

        status, _, _ = self.request(
            "/api/auth/setup",
            method="POST",
            payload={"username": "second", "password": "another-pass-456"},
        )
        self.assertEqual(status, 409)

        status, _, _ = self.request(
            "/api/auth/login",
            method="POST",
            payload={"username": "manager", "password": "wrong-password"},
        )
        self.assertEqual(status, 401)

        status, headers, _ = self.request(
            "/api/auth/login",
            method="POST",
            payload={"username": "manager", "password": "private-pass-123"},
        )
        self.assertEqual(status, 200)
        login_cookie = headers.get("Set-Cookie").split(";", 1)[0]
        self.assertNotEqual(setup_cookie, login_cookie)

        status, _, body = self.request("/api/auth/status", cookie=login_cookie)
        self.assertTrue(json.loads(body)["authenticated"])

        status, headers, _ = self.request(
            "/api/auth/logout", method="POST", cookie=login_cookie
        )
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers.get("Set-Cookie"))
        status, _, body = self.request("/api/auth/status", cookie=login_cookie)
        self.assertFalse(json.loads(body)["authenticated"])
        status, _, body = self.request("/api/auth/status", cookie=setup_cookie)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["authenticated"])


if __name__ == "__main__":
    unittest.main()
