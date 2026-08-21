from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.webui.dependencies import require_auth
from src.webui.routers.plugin import pages as pages_module
from src.webui.services.plugin_page_registry import PluginPageRecord


class _FakeRuntime:
    def __init__(self) -> None:
        self.api_enabled = True
        self.rpc_error: Optional[RPCError] = None
        self.result: Any = {"status": "ok"}
        self.calls: List[Dict[str, Any]] = []

    def get_plugin_api(self, plugin_id: str, component_name: str, *, enabled_only: bool = True) -> object | None:
        if plugin_id != "example.first" or component_name != "webui.hello.get_status":
            return None
        if enabled_only and not self.api_enabled:
            return None
        return SimpleNamespace(timeout_ms=30000)

    async def invoke_api(
        self,
        plugin_id: str,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Any:
        self.calls.append(
            {
                "plugin_id": plugin_id,
                "component_name": component_name,
                "args": args,
                "timeout_ms": timeout_ms,
            }
        )
        if self.rpc_error is not None:
            raise self.rpc_error
        return self.result


@pytest.fixture
def page_api_app(monkeypatch: pytest.MonkeyPatch) -> Tuple[FastAPI, _FakeRuntime]:
    runtime = _FakeRuntime()
    page = PluginPageRecord(
        plugin_id="example.first",
        page_id="hello",
        title="Hello World",
        route="/plugin-pages/example.first/hello",
        entry_url="/api/webui/plugins/example.first/assets/webui/dist/index.js?v=1.0.0",
        component="mount",
        icon=None,
        order=0,
        permissions=(),
        api={"get_status": "webui.hello.get_status"},
        api_base="/api/webui/plugins/example.first/pages/hello/api",
        plugin_path=Path("."),
        entry_path=Path("webui/dist/index.js"),
        plugin_version="1.0.0",
    )
    monkeypatch.setattr(pages_module, "_get_page_records", lambda: [page])
    monkeypatch.setattr(pages_module, "get_plugin_runtime_manager", lambda: runtime)

    app = FastAPI()
    app.include_router(pages_module.router, prefix="/api/webui/plugins")
    app.dependency_overrides[require_auth] = lambda: "test-token"
    return app, runtime


def test_page_api_invokes_manifest_allowlisted_component(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    app, runtime = page_api_app

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={"verbose": True},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"status": "ok"}}
    assert runtime.calls[0]["component_name"] == "webui.hello.get_status"


def test_page_api_debug_mode_returns_request_id(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    """debug=true 时页面 API 应返回可用于查日志的 request_id。"""

    app, _runtime = page_api_app

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status?debug=true",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["request_id"]


def test_page_api_rejects_operation_missing_from_manifest(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    app, _runtime = page_api_app

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/delete_all",
        json={},
    )

    assert response.status_code == 404


def test_page_api_rejects_disabled_registry_entry(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    app, runtime = page_api_app
    runtime.api_enabled = False

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={},
    )

    assert response.status_code == 403


def test_page_api_maps_rpc_timeout_to_gateway_timeout(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    app, runtime = page_api_app
    runtime.rpc_error = RPCError(ErrorCode.E_TIMEOUT, "internal timeout")

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={},
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "插件页面 API 调用超时"


def test_page_api_rejects_body_over_64_kib(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    app, _runtime = page_api_app

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        content=("x" * (64 * 1024 + 1)).encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


def test_page_api_rejects_non_json_result(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    app, runtime = page_api_app
    runtime.result = {"value": object()}

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "插件页面 API 返回了不可序列化的数据"


def test_page_api_requires_authentication(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    app, _runtime = page_api_app
    app.dependency_overrides.clear()

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={},
    )

    assert response.status_code == 401


def test_page_api_hides_runner_failure_details(page_api_app: Tuple[FastAPI, _FakeRuntime]) -> None:
    app, runtime = page_api_app
    runtime.rpc_error = RPCError(ErrorCode.E_UNKNOWN, "secret runner stack trace")

    response = TestClient(app).post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "插件页面 API 调用失败"
    assert "secret runner stack trace" not in response.text
