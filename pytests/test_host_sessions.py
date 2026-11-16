from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import sqlite3
import time

from src.host.authentication import HostCredentials
from src.host.sessions import HostSessions
from src.host.webui_auth import register_host_auth


def fixed_credentials(tmp_path: Path) -> HostCredentials:
    credentials = HostCredentials(tmp_path / "host" / "webui.json")
    credentials.update("ValidPassword!123")
    return credentials


def test_fixed_password_session_survives_time_and_restart(tmp_path, monkeypatch):
    credentials = fixed_credentials(tmp_path)
    sessions = HostSessions(tmp_path, credentials=credentials)
    session = sessions.create_session("http://localhost:7999")
    later = time.time() + 2 * 365 * 24 * 60 * 60
    monkeypatch.setattr(time, "time", lambda: later)

    assert sessions.valid(session)
    restarted = HostSessions(tmp_path, credentials=HostCredentials(credentials.path))
    assert restarted.valid(session)
    assert restarted.login_origin(session) == "http://localhost:7999"
    restarted.revoke(session)
    assert not HostSessions(tmp_path, credentials=credentials).valid(session)


def test_legacy_migration_keeps_only_unexpired_sessions(tmp_path, monkeypatch):
    credentials = fixed_credentials(tmp_path)
    sessions = HostSessions(tmp_path, credentials=credentials)
    now = time.time()
    with closing(sqlite3.connect(credentials.path.parent / "sessions.sqlite3")) as db, db:
        db.execute("DROP TABLE sessions")
        db.execute("CREATE TABLE sessions (digest TEXT PRIMARY KEY, expires_at REAL NOT NULL)")
        db.executemany("INSERT INTO sessions VALUES (?, ?)", [
            (sessions._digest("active"), now + 60),
            (sessions._digest("expired"), now - 60),
        ])

    migrated = HostSessions(tmp_path, credentials=credentials)
    assert migrated.valid("active")
    assert migrated.login_origin("active") is None
    assert not migrated.valid("expired")
    later = now + 365 * 24 * 60 * 60
    monkeypatch.setattr(time, "time", lambda: later)
    assert HostSessions(tmp_path, credentials=credentials).valid("active")
    assert migrated.valid(migrated.create_session())


def test_password_change_invalidates_persistent_sessions(tmp_path):
    credentials = fixed_credentials(tmp_path)
    sessions = HostSessions(tmp_path, credentials=credentials)
    session = sessions.create_session()
    other = sessions.create_session()

    assert sessions.update_password(session, "ChangedPassword!123") is None
    restarted = HostSessions(tmp_path, credentials=HostCredentials(credentials.path))
    assert not restarted.valid(session)
    assert not restarted.valid(other)


def test_temporary_password_sessions_still_expire_on_restart(tmp_path):
    credentials = HostCredentials(tmp_path / "host" / "webui.json")
    session = HostSessions(tmp_path, credentials=credentials).create_session()

    restarted = HostSessions(tmp_path, credentials=HostCredentials(credentials.path))
    assert not restarted.valid(session)


def test_seven_day_cookie_is_not_renewed_by_auth_check_and_logout_revokes_it(tmp_path):
    credentials = fixed_credentials(tmp_path)
    sessions = HostSessions(tmp_path, credentials=credentials)
    app = FastAPI()
    register_host_auth(app, sessions, credentials, lambda: None, lambda request: "127.0.0.1")
    with TestClient(app) as client:
        response = client.post("/api/webui/auth/verify", json={"token": credentials.token})
        assert response.json()["valid"]
        assert "Max-Age=604800" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"]
        cookie = client.cookies.get(sessions.cookie_name)
        response = client.get("/api/webui/auth/check")
        assert response.json()["authenticated"]
        assert client.cookies.get(sessions.cookie_name) == cookie
        assert "set-cookie" not in response.headers

        assert client.post("/api/webui/auth/logout").json()["success"]
        assert not sessions.valid(cookie)
        response = client.get("/api/webui/auth/check")
        assert not response.json()["authenticated"]
        assert "set-cookie" not in response.headers
