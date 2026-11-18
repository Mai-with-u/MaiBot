"""从 SDK 声明到 Host 注册、实际操作分发与卸载；仅替换进程间传输。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock

from maibot_sdk import LLMProvider, MaiBotPlugin
import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.core.tooling import ToolInvocation
from src.llm_models.model_client.base_client import AudioTranscriptionRequest, client_registry
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.runner.plugin_loader import PluginMeta
from src.plugin_runtime.runner.runner_main import PluginRunner
from src.web_search.config import WebRuntimeConfig, WebSearchConfigError
from src.web_search.provider import WebSearchToolProvider
from src.web_search.service import WebSearchService
from src.webui.routers.model import SpeechPreviewRequest, _preview_audio, get_registered_client_types


class FormatFixture(MaiBotPlugin):
    @LLMProvider("fixture.media", formats={
        "tts": {"say": {"label": "朗读", "voices": {"soft": "轻声"}}},
        "asr": {"hear": {"label": "转写"}},
        "web": ["web_search", "web_fetch"],
    })
    async def handle_format(self, operation: str, request: Dict[str, Any]) -> Dict[str, Any]:
        if operation == "audio_synthesis":
            assert request["voice_id"] == "soft" and request["model_identifier"] == "say"
            return {"media_type": "audio/mpeg", "audio_base64": "SUQz"}
        if operation == "audio_transcription":
            assert request["audio_base64"] == "SUQz" and request["model_info"]["model_identifier"] == "hear"
            return {"content": "听见了"}
        assert request["api_provider"]["api_key"] == "web-key"
        if operation == "web_search":
            return {"results": [{"url": "https://example.org/", "title": request["query"], "snippet": "摘要"}]}
        assert operation == "web_fetch"
        return {"url": request["url"], "title": "网页", "content": "正文"}


@pytest.mark.asyncio
async def test_plugin_formats_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = "fixture.media-plugin"
    runner = PluginRunner("", "", [])
    supervisor = PluginRunnerSupervisor(plugin_dirs=[])
    manifest = SimpleNamespace(name="fixture", description="", version="1.0.0", plugin_type="extension",
                               capabilities=[], plugin_dependency_ids=[], llm_provider_client_types=["fixture.media"],
                               author=SimpleNamespace(name="fixture"))
    meta = PluginMeta(owner, str(tmp_path), "fixture", FormatFixture(), manifest)
    monkeypatch.setattr(runner._loader, "get_plugin", lambda plugin_id: meta if plugin_id == owner else None)

    def envelope(method: str, payload: Dict[str, Any]) -> Envelope:
        return Envelope(request_id=1, message_type=MessageType.REQUEST, method=method, plugin_id=owner, payload=payload)

    async def send_request(method: str, plugin_id: str, payload: Dict[str, Any], timeout_ms: int) -> Envelope:
        assert plugin_id == owner
        assert method == "plugin.invoke_llm_provider"
        return await runner._handle_llm_provider_invoke(envelope(method, payload))

    monkeypatch.setattr(supervisor, "_running", True)
    monkeypatch.setattr(supervisor._rpc_server, "send_request", send_request)
    registration = await runner._build_plugin_registration_payload(meta)
    response = await supervisor._handle_register_plugin(envelope("plugin.register_components", registration.model_dump()))
    assert not response.error
    try:
        entry = next(row for row in get_registered_client_types()["client_types"] if row["client_type"] == "fixture.media")
        assert entry["formats"]["tts"]["say"]["voices"] == {"soft": "轻声"}
        connection = APIProvider(name="test", client_type="fixture.media", base_url="https://example.org", api_key="speech-key")
        client = await client_registry.get_client_class_instance_async(connection)
        preview = await _preview_audio(connection, SpeechPreviewRequest(model_identifier="say", voice_id="soft"))
        assert (preview.data, preview.media_type) == (b"ID3", "audio/mpeg")
        model = ModelInfo(model_type="asr", model_identifier="hear", name="听写", api_provider="test")
        assert (await client.get_audio_transcriptions(AudioTranscriptionRequest(model, "SUQz"))).content == "听见了"

        config = WebRuntimeConfig(enabled=True, search="w", fetch="w", services={
            "w": {"adapter": f"plugin:{owner}/fixture.media", "api_key": "web-key"},
        })
        service = WebSearchService(AsyncMock())
        provider = WebSearchToolProvider(service, lambda: config)
        assert [tool.name for tool in await provider.list_tools()] == ["web_search", "web_fetch"]
        hits = await service.execute(config, ToolInvocation("web_search", {"query": "测试"}))
        assert hits.results[0].title == "测试"
        page = await service.execute(config, ToolInvocation("web_fetch", {"url": "https://example.org/"}))
        assert page.content == "正文"
        registration.llm_providers[0].formats.web = ["web_search"]
        response = await supervisor._handle_register_plugin(envelope("plugin.register_components", registration.model_dump()))
        assert not response.error
        assert [tool.name for tool in await provider.list_tools()] == ["web_search"]
        with pytest.raises(WebSearchConfigError, match="web_fetch"):
            await service.execute(config, ToolInvocation("web_fetch", {"url": "https://example.org/"}))
        response = await supervisor._handle_unregister_plugin(envelope("plugin.unregister", {"plugin_id": owner}))
        assert not response.error
        assert await provider.list_tools() == []
        with pytest.raises(WebSearchConfigError):
            await service.execute(config, ToolInvocation("web_search", {"query": "测试"}))
    finally:
        client_registry.unregister_plugin_providers(owner)
