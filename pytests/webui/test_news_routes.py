"""测试首页资讯路由与资讯拉取服务。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services import news_service
from src.webui.dependencies import require_auth
from src.webui.routers.news import router as news_router


@pytest.fixture(name="app")
def app_fixture() -> FastAPI:
    app = FastAPI()
    app.include_router(news_router, prefix="/api/webui")
    app.dependency_overrides[require_auth] = lambda: "test-token"
    return app


@pytest.fixture(name="client")
def client_fixture(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_news_cache() -> None:
    """资讯缓存是进程级状态，逐用例清理避免相互影响。"""

    news_service._cache = None


def make_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": True,
        "updated_at": "2026-01-03T10:00:00+00:00",
        "total": 1,
        "items": [
            {
                "id": "welcome",
                "title": "欢迎使用麦麦资讯",
                "summary": "摘要",
                "content": "# 正文",
                "source": "MaiBot 官方",
                "published_at": "2026-01-01T00:00:00+08:00",
                "pinned": True,
                "url": "https://example.com",
            }
        ],
    }
    payload.update(overrides)
    return payload


def stub_news_source(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> list[str]:
    """把远程资讯源替换为固定响应，并记录请求过的 URL。"""

    requested: list[str] = []

    class StubResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return payload

    class StubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "StubClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str) -> StubResponse:
            requested.append(url)
            return StubResponse()

    monkeypatch.setattr(news_service.httpx, "AsyncClient", StubClient)
    return requested


def test_get_news_returns_items_and_meta(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    requested = stub_news_source(monkeypatch, make_payload())

    response = client.get("/api/webui/news")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["updated_at"] == "2026-01-03T10:00:00+00:00"
    item = payload["items"][0]
    assert item["id"] == "welcome"
    assert item["title"] == "欢迎使用麦麦资讯"
    assert item["content"] == "# 正文"
    assert item["pinned"] is True
    assert requested and "limit=" in requested[0]


def test_get_news_reuses_cache_and_force_bypasses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = stub_news_source(monkeypatch, make_payload())

    client.get("/api/webui/news")
    client.get("/api/webui/news")
    assert len(requested) == 1

    client.get("/api/webui/news", params={"force": True})
    assert len(requested) == 2


def test_get_news_reports_source_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_connect_error(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(news_service.httpx, "AsyncClient", raise_connect_error)

    response = client.get("/api/webui/news")

    assert response.status_code == 502
    assert "资讯服务请求失败" in response.json()["detail"]


def test_get_news_keeps_last_good_items_on_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回源失败时沿用上一次成功结果，首页不至于整块变空。"""

    stub_news_source(monkeypatch, make_payload())
    assert client.get("/api/webui/news").json()["items"][0]["id"] == "welcome"

    def raise_connect_error(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(news_service.httpx, "AsyncClient", raise_connect_error)

    response = client.get("/api/webui/news", params={"force": True})

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "welcome"


def test_get_news_rejects_invalid_payload(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_news_source(monkeypatch, {"success": True})

    response = client.get("/api/webui/news")

    assert response.status_code == 502
    assert "items" in response.json()["detail"]


def test_get_news_skips_items_without_title(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_news_source(
        monkeypatch,
        make_payload(
            items=[
                {"id": "no-title", "title": "", "summary": "无标题"},
                {"id": "ok", "title": "有标题"},
            ],
            total=2,
        ),
    )

    items = client.get("/api/webui/news").json()["items"]

    assert [item["id"] for item in items] == ["ok"]


def test_get_news_requires_auth(app: FastAPI) -> None:
    app.dependency_overrides.pop(require_auth, None)
    client = TestClient(app)

    response = client.get("/api/webui/news")

    assert response.status_code == 401
