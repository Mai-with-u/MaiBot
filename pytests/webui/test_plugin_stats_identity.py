from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from src.webui.dependencies import require_auth
from src.webui.routers.plugin import stats_proxy


@pytest.fixture
def client(monkeypatch):
    forwarded = []

    async def request_stats(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> JSONResponse:
        forwarded.append((method, path, payload))
        return JSONResponse({"success": True})

    monkeypatch.setattr(stats_proxy, "_request_stats_service", request_stats)
    app = FastAPI()
    app.include_router(stats_proxy.router)
    app.dependency_overrides[require_auth] = lambda: "test-token"
    with TestClient(app) as test_client:
        yield test_client, forwarded


def test_anonymous_review_never_forwards_username(client):
    test_client, forwarded = client
    response = test_client.post("/stats-proxy/stats/rate", json={
        "plugin_id": "test.demo", "user_id": "test-user", "comment": "好用",
        "anonymous": True, "username": "不应发送的昵称",
    })
    assert response.status_code == 200
    assert forwarded == [("POST", "/stats/rate", {
        "plugin_id": "test.demo", "user_id": "test-user", "comment": "好用", "anonymous": True,
    })]


def test_named_review_forwards_trimmed_username(client):
    test_client, forwarded = client
    response = test_client.post("/stats-proxy/stats/rate", json={
        "plugin_id": "test.demo", "user_id": "test-user", "rating": 5,
        "anonymous": False, "username": " 麦麦 ",
    })
    assert response.status_code == 200
    assert forwarded[0][2]["username"] == "麦麦"


def test_default_username_is_local_only(client, monkeypatch):
    test_client, forwarded = client
    monkeypatch.setattr(stats_proxy.global_config.bot, "nickname", "测试麦麦")
    response = test_client.get("/stats-proxy/identity")
    assert response.json() == {"username": "测试麦麦"}
    assert forwarded == []


def test_existing_review_payload_remains_supported(client):
    test_client, forwarded = client
    payload = {"plugin_id": "test.demo", "user_id": "test-user", "comment": "好用"}
    assert test_client.post("/stats-proxy/stats/rate", json=payload).status_code == 200
    assert forwarded[0][2] == payload


def test_blank_named_review_username_is_rejected(client):
    test_client, forwarded = client
    response = test_client.post("/stats-proxy/stats/rate", json={
        "plugin_id": "test.demo", "user_id": "test-user", "comment": "好用",
        "anonymous": False, "username": "   ",
    })
    assert response.status_code == 422
    assert forwarded == []
