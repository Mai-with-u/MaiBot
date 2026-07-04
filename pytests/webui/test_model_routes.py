"""模型路由测试

验证 Gemini 提供商连接测试会使用查询参数传递 API Key，
并且不会回退到 OpenAI 兼容接口使用的 Bearer 认证方式。
"""

import importlib
import base64
import struct
import sys
import zlib
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def load_model_routes(monkeypatch: pytest.MonkeyPatch):
    """在导入路由前 stub 配置与认证依赖模块，避免测试时触发真实初始化。"""
    class DummyConfigManager:
        def get_model_config(self):
            return SimpleNamespace(api_providers=[])

        def register_reload_callback(self, callback: Any) -> None:
            return None

    config_module = ModuleType("src.config.config")
    config_module.__dict__["CONFIG_DIR"] = "."
    config_module.__dict__["config_manager"] = DummyConfigManager()
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


@pytest.mark.asyncio
async def test_test_model_capability_requires_tool_call_and_keeps_visual_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """模型能力测试应要求测试工具调用，并按模型 visual 配置附带视觉测试标记。"""
    model_routes = load_model_routes(monkeypatch)
    config_path = tmp_path / "model_config.toml"
    config_path.write_text(
        """
[[models]]
name = "vision-model"
model_identifier = "vision-model-id"
api_provider = "Fake"
visual = true
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(model_routes, "CONFIG_DIR", str(tmp_path))

    captured: dict[str, Any] = {}

    class FakeOrchestrator:
        def __init__(self, model_name: str):
            captured["model_name"] = model_name

        async def generate_response_with_message_async(self, **kwargs: Any):
            captured.update(kwargs)
            return model_routes.LLMResponseResult(
                response="",
                model_name="vision-model",
                tool_calls=[
                    model_routes.ToolCall(
                        call_id="call-1",
                        func_name=model_routes.MODEL_TEST_TOOL_NAME,
                        args={"status": "ok", "echo": "maibot model test", "saw_image": True},
                    )
                ],
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )

    monkeypatch.setattr(model_routes, "_SingleModelTestOrchestrator", FakeOrchestrator)

    result = await model_routes.test_model_capability(
        model_routes.ModelTestRequest(model_name="vision-model")
    )

    assert result.success is True
    assert result.visual_tested is True
    assert result.tool_call_ok is True
    assert result.total_tokens == 15
    assert captured["model_name"] == "vision-model"
    assert captured["tools"][0]["name"] == model_routes.MODEL_TEST_TOOL_NAME


@pytest.mark.asyncio
async def test_test_model_capability_tests_embedding_task_model_via_embedding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """嵌入任务中的模型应通过嵌入接口测试，而不是对话接口。"""
    model_routes = load_model_routes(monkeypatch)
    config_path = tmp_path / "model_config.toml"
    config_path.write_text(
        """
[[models]]
name = "embed-model"
model_identifier = "embed-model-id"
api_provider = "Fake"

[model_task_config.embedding]
model_list = ["embed-model"]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(model_routes, "CONFIG_DIR", str(tmp_path))

    captured: dict[str, Any] = {}

    class FakeOrchestrator:
        def __init__(self, model_name: str):
            captured["model_name"] = model_name

        async def get_embedding(self, embedding_input: str, **kwargs: Any):
            captured["embedding_input"] = embedding_input
            return SimpleNamespace(embedding=[0.1] * 8, model_name="embed-model")

        async def generate_response_with_message_async(self, **kwargs: Any):
            raise AssertionError("嵌入模型测试不应调用对话接口")

    monkeypatch.setattr(model_routes, "_SingleModelTestOrchestrator", FakeOrchestrator)

    result = await model_routes.test_model_capability(model_routes.ModelTestRequest(model_name="embed-model"))

    assert result.success is True
    assert result.tool_call_ok is False
    assert result.visual_tested is False
    assert result.response == "嵌入向量维度: 8"
    assert captured["model_name"] == "embed-model"
    assert captured["embedding_input"]


@pytest.mark.asyncio
async def test_test_model_capability_keeps_chat_test_for_non_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """未配置在嵌入任务中的模型应保持原有对话测试流程。"""
    model_routes = load_model_routes(monkeypatch)
    config_path = tmp_path / "model_config.toml"
    config_path.write_text(
        """
[[models]]
name = "chat-model"
model_identifier = "chat-model-id"
api_provider = "Fake"

[model_task_config.embedding]
model_list = ["other-embed-model"]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(model_routes, "CONFIG_DIR", str(tmp_path))

    class FakeOrchestrator:
        def __init__(self, model_name: str):
            self.model_name = model_name

        async def get_embedding(self, embedding_input: str, **kwargs: Any):
            raise AssertionError("对话模型测试不应调用嵌入接口")

        async def generate_response_with_message_async(self, **kwargs: Any):
            return model_routes.LLMResponseResult(
                response="",
                model_name="chat-model",
                tool_calls=[
                    model_routes.ToolCall(
                        call_id="call-1",
                        func_name=model_routes.MODEL_TEST_TOOL_NAME,
                        args={"status": "ok", "echo": "maibot model test", "saw_image": False},
                    )
                ],
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )

    monkeypatch.setattr(model_routes, "_SingleModelTestOrchestrator", FakeOrchestrator)

    result = await model_routes.test_model_capability(model_routes.ModelTestRequest(model_name="chat-model"))

    assert result.success is True
    assert result.tool_call_ok is True


def test_model_test_image_base64_is_valid_png(monkeypatch: pytest.MonkeyPatch) -> None:
    """内置视觉测试图应是可被服务商正常解析的有效 PNG。"""
    model_routes = load_model_routes(monkeypatch)
    image_bytes = base64.b64decode(model_routes.MODEL_TEST_IMAGE_BASE64)

    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    position = 8
    chunk_types: list[bytes] = []
    while position < len(image_bytes):
        chunk_length = struct.unpack(">I", image_bytes[position : position + 4])[0]
        chunk_type = image_bytes[position + 4 : position + 8]
        chunk_data = image_bytes[position + 8 : position + 8 + chunk_length]
        chunk_crc = struct.unpack(">I", image_bytes[position + 8 + chunk_length : position + 12 + chunk_length])[0]

        assert chunk_crc == zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        chunk_types.append(chunk_type)
        position += 12 + chunk_length

    assert chunk_types == [b"IHDR", b"IDAT", b"IEND"]
