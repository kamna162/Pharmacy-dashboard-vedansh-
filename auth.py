from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path


AUTH_DB_PATH = Path(__file__).resolve().parent / "dashboard_auth.db"
SESSION_COOKIE = "dashboard_session"
SESSION_LIFETIME_SECONDS = 8 * 60 * 60
PASSWORD_ITERATIONS = 310_000
_sessions = {}
_sessions_lock = threading.Lock()


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@contextmanager
def _connect():
    connection = sqlite3.connect(AUTH_DB_PATH, timeout=10)
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()


def initialize():
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_credentials (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL,
                salt BLOB NOT NULL,
                password_hash BLOB NOT NULL
            )
            """
        )


def is_setup_complete():
    initialize()
    with _connect() as connection:
        return connection.execute(
            "SELECT 1 FROM dashboard_credentials WHERE id = 1"
        ).fetchone() is not None


def create_account(username, password):
    username = username.strip() if isinstance(username, str) else ""
    if not username or len(username) > 64:
        raise AuthError("Enter a username between 1 and 64 characters.")
    if not isinstance(password, str) or not 8 <= len(password) <= 1024:
        raise AuthError("Choose a password between 8 and 1024 characters.")

    salt = secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    initialize()
    try:
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO dashboard_credentials (id, username, salt, password_hash)
                VALUES (1, ?, ?, ?)
                """,
                (username, salt, password_hash),
            )
    except sqlite3.IntegrityError as error:
        raise AuthError("The dashboard account has already been created.", 409) from error
    return username


def verify_credentials(username, password):
    username = username if isinstance(username, str) else ""
    password = password if isinstance(password, str) else ""
    initialize()
    with _connect() as connection:
        row = connection.execute(
            "SELECT username, salt, password_hash FROM dashboard_credentials WHERE id = 1"
        ).fetchone()

    if row is None:
        return None
    expected_username, salt, expected_hash = row
    actual_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    if hmac.compare_digest(username, expected_username) and hmac.compare_digest(
        actual_hash, expected_hash
    ):
        return expected_username
    return None


def create_session(username):
    token = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + SESSION_LIFETIME_SECONDS
    with _sessions_lock:
        _remove_expired_sessions()
        _sessions[token] = (username, expires_at)
    return token


def _remove_expired_sessions():
    now = time.monotonic()
    for token, (_, expires_at) in list(_sessions.items()):
        if expires_at <= now:
            del _sessions[token]


def username_from_cookie(cookie_header):
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header or "")
    except (TypeError, ValueError):
        return None
    morsel = cookie.get(SESSION_COOKIE)
    if morsel is None:
        return None
    token = morsel.value
    with _sessions_lock:
        _remove_expired_sessions()
        session = _sessions.get(token)
    return session[0] if session else None


def destroy_session(cookie_header):
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header or "")
    except (TypeError, ValueError):
        return
    morsel = cookie.get(SESSION_COOKIE)
    if morsel is None:
        return
    with _sessions_lock:
        _sessions.pop(morsel.value, None)


def reset_sessions():
    with _sessions_lock:
        _sessions.clear()
