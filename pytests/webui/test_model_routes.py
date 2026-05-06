"""模型路由测试

验证 Gemini 提供商连接测试会使用查询参数传递 API Key，
并且不会回退到 OpenAI 兼容接口使用的 Bearer 认证方式。
"""

import importlib
import sys
from types import ModuleType
from typing import Any

import pytest


def load_model_routes(monkeypatch: pytest.MonkeyPatch):
    """在导入路由前 stub 配置与认证依赖模块，避免测试时触发真实初始化。"""
    config_module = ModuleType("src.config.config")
    config_module.__dict__["CONFIG_DIR"] = "."
    monkeypatch.setitem(sys.modules, "src.config.config", config_module)

    dependencies_module = ModuleType("src.webui.dependencies")

    async def require_auth():
        return "test-token"

    dependencies_module.__dict__["require_auth"] = require_auth
    monkeypatch.setitem(sys.modules, "src.webui.dependencies", dependencies_module)

    sys.modules.pop("src.webui.routers.model", None)
    return importlib.import_module("src.webui.routers.model")


class FakeResponse:
    """简化版 HTTP 响应对象。"""

    def __init__(self, status_code: int):
        self.status_code = status_code


def build_async_client_factory(
    responses: list[FakeResponse],
    calls: list[dict[str, Any]],
):
    """构造一个可记录请求参数的 AsyncClient 替身。"""

    response_iter = iter(responses)

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(
            self,
            url: str,
            headers: dict[str, Any] | None = None,
            params: dict[str, Any] | None = None,
        ) -> FakeResponse:
            calls.append(
                {
                    "url": url,
                    "headers": headers or {},
                    "params": params or {},
                }
            )
            return next(response_iter)

    return FakeAsyncClient


@pytest.mark.asyncio
async def test_test_provider_connection_uses_query_api_key_for_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini 连接测试应通过查询参数传递 API Key。"""
    model_routes = load_model_routes(monkeypatch)
    calls: list[dict[str, Any]] = []
    fake_client_class = build_async_client_factory(
        responses=[FakeResponse(200), FakeResponse(200)],
        calls=calls,
    )
    monkeypatch.setattr(model_routes.httpx, "AsyncClient", fake_client_class)

    result = await model_routes.test_provider_connection(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="valid-gemini-key",
        client_type="gemini",
    )

    assert result["network_ok"] is True
    assert result["api_key_valid"] is True
    assert len(calls) == 2

    network_call = calls[0]
    validation_call = calls[1]

    assert network_call["url"] == "https://generativelanguage.googleapis.com/v1beta"
    assert network_call["headers"] == {}
    assert network_call["params"] == {}

    assert validation_call["url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert validation_call["params"] == {"key": "valid-gemini-key"}
    assert validation_call["headers"] == {"Content-Type": "application/json"}
    assert "Authorization" not in validation_call["headers"]


@pytest.mark.asyncio
async def test_test_provider_connection_uses_bearer_auth_for_openai_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 Gemini 提供商连接测试应继续使用 Bearer 认证。"""
    model_routes = load_model_routes(monkeypatch)
    calls: list[dict[str, Any]] = []
    fake_client_class = build_async_client_factory(
        responses=[FakeResponse(200), FakeResponse(200)],
        calls=calls,
    )
    monkeypatch.setattr(model_routes.httpx, "AsyncClient", fake_client_class)

    result = await model_routes.test_provider_connection(
        base_url="https://example.com/v1",
        api_key="valid-openai-key",
        client_type="openai",
    )

    assert result["network_ok"] is True
    assert result["api_key_valid"] is True
    assert len(calls) == 2

    validation_call = calls[1]

    assert validation_call["url"] == "https://example.com/v1/models"
    assert validation_call["params"] == {}
    assert validation_call["headers"]["Content-Type"] == "application/json"
    assert validation_call["headers"]["Authorization"] == "Bearer valid-openai-key"


@pytest.mark.asyncio
async def test_test_provider_connection_by_name_forwards_provider_client_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """按提供商名称测试连接时，应透传配置中的 client_type。"""
    model_routes = load_model_routes(monkeypatch)
    config_path = tmp_path / "model_config.toml"
    config_path.write_text(
        """
[[api_providers]]
name = "Gemini"
base_url = "https://generativelanguage.googleapis.com/v1beta"
api_key = "valid-gemini-key"
client_type = "gemini"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(model_routes, "CONFIG_DIR", str(tmp_path))

    captured_kwargs: dict[str, Any] = {}

    async def fake_test_provider_connection(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "network_ok": True,
            "api_key_valid": True,
            "latency_ms": 12.34,
            "error": None,
            "http_status": 200,
        }

    monkeypatch.setattr(model_routes, "test_provider_connection", fake_test_provider_connection)

    result = await model_routes.test_provider_connection_by_name(provider_name="Gemini")

    assert result["network_ok"] is True
    assert result["api_key_valid"] is True
    assert captured_kwargs == {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "valid-gemini-key",
        "client_type": "gemini",
    }