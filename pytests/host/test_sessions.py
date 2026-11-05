"""会话跨重启恢复与撤销：使用真实临时数据库和认证路由。"""

from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import pytest

from src.host.authentication import HostCredentials
from src.host.sessions import HostSessions
from src.host.webui_auth import register_host_auth


PASSWORD = "Testing!Password1"


def credentials_at(root: Path) -> HostCredentials:
    return HostCredentials(root / "host" / "webui.json")


def client_for(sessions: HostSessions, credentials: HostCredentials) -> TestClient:
    app = FastAPI()

    async def require_session(request: Request) -> None:
        if not sessions.valid(request.cookies.get(sessions.cookie_name)):
            raise HTTPException(401)

    register_host_auth(app, sessions, credentials, require_session)
    return TestClient(app)


def test_login_cookie_survives_restart_and_logout_is_durable(tmp_path: Path) -> None:
    credentials = credentials_at(tmp_path)
    credentials.update(PASSWORD)
    sessions = HostSessions(tmp_path, credentials=credentials)
    with client_for(sessions, credentials) as client:
        assert client.post("/api/webui/auth/verify", json={"token": PASSWORD}).json()["valid"]
        cookie = client.cookies.get(sessions.cookie_name)
    credentials = credentials_at(tmp_path)
    restarted = HostSessions(tmp_path, credentials=credentials)
    assert restarted.valid(cookie)
    other_cookie = restarted.create_session()
    with client_for(restarted, credentials) as client:
        client.cookies.set(restarted.cookie_name, cookie)
        assert client.get("/api/webui/auth/check").json()["authenticated"]
        assert client.post("/api/webui/auth/logout").status_code == 200
        assert not client.get("/api/webui/auth/check").json()["authenticated"]
    restarted = HostSessions(tmp_path, credentials=credentials_at(tmp_path))
    assert not restarted.valid(cookie)
    assert restarted.valid(other_cookie)
    assert cookie.encode() not in (tmp_path / "host" / "sessions.sqlite3").read_bytes()


@pytest.mark.parametrize("route,body", [
    ("update", {"new_token": "Different!Password2"}),
    ("regenerate", {}),
    ("setup/reset", {"reset_password": True}),
])
def test_password_changes_revoke_all_sessions(tmp_path: Path, route: str, body: Dict[str, Any]) -> None:
    credentials = credentials_at(tmp_path)
    credentials.update(PASSWORD)
    sessions = HostSessions(tmp_path, credentials=credentials)
    cookies = [sessions.create_session(), sessions.create_session()]
    with client_for(sessions, credentials) as client:
        client.cookies.set(sessions.cookie_name, cookies[0])
        path = "/api/webui/setup/reset" if route == "setup/reset" else f"/api/webui/auth/{route}"
        assert client.post(path, json=body).json()["success"]
    restarted = HostSessions(tmp_path, credentials=credentials_at(tmp_path))
    assert all(not sessions.valid(cookie) and not restarted.valid(cookie) for cookie in cookies)


def test_expiration_is_not_extended_by_restart(tmp_path: Path, monkeypatch) -> None:
    now = 1_800_000_000.0
    monkeypatch.setattr("src.host.sessions.time.time", lambda: now)
    credentials = credentials_at(tmp_path)
    credentials.update(PASSWORD)
    sessions = HostSessions(tmp_path, credentials=credentials)
    cookie = sessions.create_session()
    now += 8 * 60 * 60 - 1
    restarted = HostSessions(tmp_path, credentials=credentials_at(tmp_path))
    assert restarted.valid(cookie)
    now += 1
    assert not restarted.valid(cookie)
    assert not HostSessions(tmp_path, credentials=credentials_at(tmp_path)).valid(cookie)


def test_temporary_password_and_handoff_tickets_do_not_survive_restart(tmp_path: Path) -> None:
    sessions = HostSessions(tmp_path, credentials=credentials_at(tmp_path))
    cookie = sessions.create_session()
    ticket = sessions.issue_ticket()
    restarted = HostSessions(tmp_path, credentials=credentials_at(tmp_path))
    assert not restarted.valid(cookie)
    with pytest.raises(HTTPException) as error:
        restarted.exchange(ticket)
    assert error.value.status_code == 401
    ticket = restarted.issue_ticket()
    assert restarted.valid(restarted.exchange(ticket))
    with pytest.raises(HTTPException):
        restarted.exchange(ticket)


def test_password_change_invalidates_cookie_even_before_session_cleanup(tmp_path: Path) -> None:
    credentials = credentials_at(tmp_path)
    credentials.update(PASSWORD)
    sessions = HostSessions(tmp_path, credentials=credentials)
    cookie = sessions.create_session()
    credentials.update("Changed!Password2")
    assert not sessions.valid(cookie)
    assert not HostSessions(tmp_path, credentials=credentials_at(tmp_path)).valid(cookie)
