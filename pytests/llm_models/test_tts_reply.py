from typing import Type
from unittest.mock import AsyncMock
import base64
import io
import json
import wave

import httpx
import pytest

from src.common.data_models.message_component_data_model import (
    AtComponent, EmojiComponent, MessageSequence, TextComponent, VoiceComponent,
)
from src.config.config import ModelConfig, config_manager
from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import client_registry
from src.llm_models.model_client.openai_client import OpenaiClient
from src.llm_models.model_client.openai_responses_client import OpenAIResponsesClient
from src.maisaka.builtin_tool.context import PostProcessedReplyMessage
from src.maisaka.builtin_tool.reply import get_tool_spec
from src.maisaka.builtin_tool.reply_voice import VoiceMode, attach_reply_voice
from src.webui.routers import model as model_router


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_type", "path", "mode", "identifier", "voice_id"), [
    (OpenaiClient, "", "voice_only", "deepseek-tts", "stella"),
    (OpenAIResponsesClient, "/v1/", "with_text", "deepseek-tts", "tide"),
    (OpenaiClient, "/step_plan/v1", "voice_only", "stepaudio-3-tts", "zixinnansheng"),
    (OpenaiClient, "/v1", "with_text", "step-tts-mini", "wenrounvsheng"),
])
async def test_reply_attachment_uses_assigned_voice_once(
    monkeypatch: pytest.MonkeyPatch, client_type: Type[OpenaiClient], path: str, mode: VoiceMode, identifier: str, voice_id: str,
) -> None:
    provider = APIProvider(name="语音账号", base_url="https://tts.test" + path, api_key="test-key")
    model = ModelInfo(name="朗读", model_type="tts", model_identifier=identifier, api_provider=provider.name,
                      extra_params={"voice_id": voice_id})
    config = ModelConfig(models=[model], api_providers=[provider])
    monkeypatch.setattr(config_manager, "model_config", config)
    assert "attach_voice" not in get_tool_spec().parameters_schema["properties"]
    config.model_task_config.tts.model_list = [model.name]
    assert mode in get_tool_spec().parameters_schema["properties"]["attach_voice"]["enum"]

    text = "你好。下次见。"
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", ""))
        wav.writeframes(bytes(480))
    audio = output.getvalue()
    requests = []

    def receive(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer test-key"
        if identifier == "deepseek-tts":
            assert request.url.path == "/v1/tools/tts"
            assert json.loads(request.content) == {"text": text, "voice_id": voice_id, "format": "wav"}
        else:
            assert request.url.path == path + "/audio/speech"
            assert json.loads(request.content) == {"input": text, "voice": voice_id, "model": identifier, "response_format": "wav"}
        return httpx.Response(200, headers={"Content-Type": "audio/wav"}, content=audio)

    at = AtComponent(target_user_id="12")
    emoji = EmojiComponent(binary_hash="emoji")
    items = [
        PostProcessedReplyMessage(MessageSequence([emoji])),
        PostProcessedReplyMessage(MessageSequence([at, TextComponent("你好。")]), quote_previous=True),
        PostProcessedReplyMessage(MessageSequence([TextComponent("下次见。")])),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http_client:
        monkeypatch.setattr("src.llm_models.model_client.openai_client.DefaultAsyncHttpxClient", lambda **kw: http_client)
        monkeypatch.setattr(client_registry, "get_client_class_instance_async", AsyncMock(return_value=client_type(provider)))
        result = await attach_reply_voice(items, text, mode)
    assert len(requests) == 1
    assert result[0].sequence.components == [emoji]
    assert result[1].quote_previous and result[1].sequence.components[0] is at
    if mode == "voice_only":
        assert len(result) == 2
        voice = result[1].sequence.components[1]
    else:
        assert result[:-1] == items
        voice = result[-1].sequence.components[0]
    assert isinstance(voice, VoiceComponent) and voice.content == text
    assert base64.b64decode((await voice.to_seg()).data) == audio


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b'{"error":"invalid"}', b"RIFF" + (1024).to_bytes(4, "little") + b"WAVE" + bytes(48)])
async def test_tts_rejects_non_audio_and_truncated_responses(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    async with httpx.AsyncClient(transport=transport) as http_client:
        monkeypatch.setattr("src.llm_models.model_client.openai_client.DefaultAsyncHttpxClient", lambda **kw: http_client)
        client = OpenaiClient(APIProvider(name="test", base_url="https://tts.test", api_key="test-key"))
        with pytest.raises(ValueError, match="WAV"):
            await client.synthesize_speech("deepseek-tts", "你好", "stella")


@pytest.mark.asyncio
@pytest.mark.parametrize("draft", [True, False])
async def test_preview_uses_form_connection_without_assignment(monkeypatch: pytest.MonkeyPatch, draft: bool) -> None:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", ""))
        wav.writeframes(bytes(480))
    provider = {"name": "套餐", "api_key": "saved-key", "base_url": "https://tts.test/step_plan/v1"}
    monkeypatch.setattr(model_router, "_get_provider_config", lambda name: provider if name == "套餐" else None)

    def receive(request: httpx.Request) -> httpx.Response:
        expected_url = "https://api.stepfun.com/v1/audio/speech" if draft else "https://tts.test/step_plan/v1/audio/speech"
        assert str(request.url) == expected_url
        assert request.headers["authorization"] == ("Bearer draft-key" if draft else "Bearer saved-key")
        body = json.loads(request.content)
        assert body["input"] == "你好，我是麦麦，很高兴和你聊天。"
        assert body["voice"] == "wenrounvsheng"
        return httpx.Response(200, content=output.getvalue())

    client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
    monkeypatch.setattr("src.llm_models.model_client.openai_client.DefaultAsyncHttpxClient", lambda **kw: client)
    source = {"draft_api_key": "draft-key"} if draft else {"api_provider": "套餐"}
    response = await model_router.preview_speech(model_router.SpeechPreviewRequest(
        model_identifier="stepaudio-2.5-tts", voice_id="wenrounvsheng", **source,
    ))
    assert response.body == output.getvalue() and response.media_type == "audio/wav"
    assert client.is_closed
