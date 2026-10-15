"""自更新路由：状态缓存、登记更新后才重启、登记失败不重启。"""

from typing import List
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from src.common.self_update import (
    LocalState,
    PendingUpdate,
    SelfUpdateError,
    UpdateCheck,
    UpdateCommit,
    UpdateSource,
)
from src.webui.dependencies import require_auth
from src.webui.routers import self_update as self_update_router

BASE = "a" * 40
TARGET = "b" * 40


def make_local(**overrides) -> LocalState:
    values = {"version": "1.3.7", "branch": "main", "commit": BASE, "dependency_tool": "uv"}
    values.update(overrides)
    return LocalState(**values)


def make_check(local: LocalState) -> UpdateCheck:
    return UpdateCheck(
        local=local,
        checked_at=1_760_000_000.0,
        latest_commit=TARGET,
        latest_version="1.3.8",
        behind=1,
        commits=[UpdateCommit(short_id="bbbbbbb", summary="修复断句", committed_at="2026-10-13T10:00:00+08:00")],
        source=UpdateSource(id="ghproxy-vip", name="ghproxy.vip 镜像", url="https://ghproxy.vip/x.git"),
    )


@pytest.fixture
def client(monkeypatch):
    restarts: List[str] = []
    monkeypatch.setattr(self_update_router, "_last_check", None)
    monkeypatch.setattr(self_update_router, "inspect_local", make_local)
    monkeypatch.setattr(self_update_router, "read_last_result", lambda: None)
    monkeypatch.setattr(self_update_router, "check_for_update", lambda: make_check(make_local()))
    monkeypatch.setattr(self_update_router, "evaluate_update", lambda local, target, checked_at: make_check(local))
    monkeypatch.setattr(self_update_router, "schedule_restart", lambda: restarts.append("restart"))
    monkeypatch.setattr(self_update_router, "get_git_mirror_service", lambda: None)

    app = FastAPI()
    app.include_router(self_update_router.router, prefix="/api/webui")
    app.dependency_overrides[require_auth] = lambda: "test-token"
    with TestClient(app) as test_client:
        yield test_client, restarts


def test_status_uses_last_check_without_network(client, monkeypatch):
    test_client, _ = client
    status = test_client.get("/api/webui/system/self-update/status").json()
    assert (status["supported"], status["checked_at"], status["update_available"]) == (True, None, False)

    checked = test_client.post("/api/webui/system/self-update/check").json()
    assert (checked["latest_version"], checked["behind"], checked["can_apply"]) == ("1.3.8", 1, True)
    assert checked["commits"][0]["summary"] == "修复断句"
    assert (checked["source_id"], checked["source_name"]) == ("ghproxy-vip", "ghproxy.vip 镜像")

    def no_network():
        raise AssertionError("状态接口不应联网")

    monkeypatch.setattr(self_update_router, "check_for_update", no_network)
    status = test_client.get("/api/webui/system/self-update/status").json()
    assert (status["latest_commit"], status["update_available"]) == (TARGET, True)


def test_unsupported_deployment_reports_blocker(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        self_update_router,
        "inspect_local",
        lambda: LocalState(version="1.3.7", blocker="not_git_repo"),
    )
    status = test_client.get("/api/webui/system/self-update/status").json()
    assert (status["supported"], status["blockers"], status["can_apply"]) == (False, ["not_git_repo"], False)


def test_package_status_reuses_cached_version_and_pypi_source(client, monkeypatch):
    from src.common.self_update import evaluate_update

    test_client, _ = client
    local = LocalState(version="1.3.8", dependency_tool="uv", deployment_type="package")
    monkeypatch.setattr(self_update_router, "inspect_local", lambda: local)
    monkeypatch.setattr(self_update_router, "evaluate_update", evaluate_update)
    monkeypatch.setattr(self_update_router, "check_for_update", lambda: evaluate_update(local, "1.3.9", 123.0))
    mirror = Mock(side_effect=AssertionError("包安装不应初始化 Git 镜像"))
    monkeypatch.setattr(self_update_router, "get_git_mirror_service", mirror)
    checked = test_client.post("/api/webui/system/self-update/check").json()
    assert checked["can_apply"] and checked["deployment_type"] == "package"
    assert (checked["source_id"], checked["latest_version"]) == ("pypi", "1.3.9")
    status = test_client.get("/api/webui/system/self-update/status").json()
    assert status["can_apply"] and status["latest_commit"] == "1.3.9"
    assert status["repository_url"] == "https://pypi.org/project/maibot/"


def test_apply_registers_request_then_restarts(client, monkeypatch):
    test_client, restarts = client
    requested: List[str] = []

    def request_update(expected_commit: str) -> PendingUpdate:
        requested.append(expected_commit)
        return PendingUpdate(target_commit=expected_commit, base_commit=BASE, branch="main", requested_at=0.0)

    monkeypatch.setattr(self_update_router, "request_update", request_update)
    response = test_client.post("/api/webui/system/self-update/apply", json={"expected_commit": TARGET})
    assert response.status_code == 200 and response.json()["success"] is True
    assert (requested, restarts) == ([TARGET], ["restart"])


def test_apply_refused_does_not_restart(client, monkeypatch):
    test_client, restarts = client

    def request_update(expected_commit: str) -> PendingUpdate:
        raise SelfUpdateError("dirty_worktree", "存在未提交的本地改动，请先提交或还原后再更新")

    monkeypatch.setattr(self_update_router, "request_update", request_update)
    response = test_client.post("/api/webui/system/self-update/apply", json={"expected_commit": TARGET})
    assert response.status_code == 409
    assert "未提交的本地改动" in response.json()["detail"]
    assert restarts == []


def test_routes_require_auth():
    app = FastAPI()
    app.include_router(self_update_router.router, prefix="/api/webui")
    with TestClient(app) as test_client:
        assert test_client.get("/api/webui/system/self-update/status").status_code == 401
        response = test_client.post("/api/webui/system/self-update/apply", json={"expected_commit": TARGET})
        assert response.status_code == 401
