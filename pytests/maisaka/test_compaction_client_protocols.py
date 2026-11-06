"""Exercise real provider SDK serialization with an in-process HTTP transport.

No model service is called: mock HTTP responses exercise request projection,
SDK parsing and the shared response/context models for both streaming modes.
"""

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List

import json

from google.genai.types import HttpOptions
import httpx
import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client import gemini_client, openai_client
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.model_client.openai_responses_client import OpenAIResponsesClient
from src.llm_models.payload_content.context_item import (
    ContextItemBuilder,
    ContextItemMeta,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    RoleType,
)
from src.llm_models.payload_content.tool_option import ToolOption
from src.maisaka.context.compaction import ConversationState, PlannerRequestSnapshot, _build_compaction_request
from src.maisaka.context.messages import ReferenceMessage


SUMMARY = json.dumps(
    {
        "topics": ["申请材料"],
        "background": [],
        "progress": [],
        "pending": ["周五前提交 A 学校材料"],
        "agreements": [],
    },
    ensure_ascii=False,
)


def _requests(protocol: str, *, stream: bool, tools: bool = True):
    source = ReferenceMessage(content="周五前提交 A 学校材料", timestamp=datetime(2026, 11, 6))
    items = [
        ContextItemBuilder().set_role(RoleType.System).add_text_content("Planner 可以使用 stop 工具").build(),
        ContextItemBuilder().set_role(RoleType.User).add_text_content(source.content).build(),
    ]
    if tools:
        # A completed tool turn must remain valid and unchanged in the prefix.
        items.extend(
            [
                FunctionCallItem(
                    meta=ContextItemMeta.create(logical_turn_id="previous-turn"),
                    tool_call=ContextToolCall.create(call_id="previous-stop", func_name="stop", args={}),
                ),
                FunctionCallOutputItem(
                    meta=ContextItemMeta.create(logical_turn_id="previous-turn"),
                    call_id="previous-stop",
                    tool_name="stop",
                    output="已结束上一轮思考",
                ),
            ]
        )
    original = ResponseRequest(
        model_info=ModelInfo(
            name="mock-model",
            model_identifier="mock-model",
            model_type="llm",
            api_provider="mock-provider",
            force_stream_mode=stream,
        ),
        context_items=items,
        tool_options=[ToolOption(name="stop", description="结束思考")] if tools else None,
        max_tokens=512,
        # The derived constraint must override a model-level choice as well.
        extra_params={"tool_choice": "auto"} if protocol != "gemini" and tools else {},
    )
    snapshot = PlannerRequestSnapshot(request=original, history=(source,))
    derived = _build_compaction_request(
        [source],
        planner_snapshot=snapshot,
        previous_summary=None,
        recent_messages=[],
    )
    return original, derived


def _response_body(protocol: str) -> Dict[str, Any]:
    if protocol == "openai":
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": "mock-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": SUMMARY}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
        }
    if protocol == "openai_responses":
        return {
            "id": "resp-mock",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "mock-model",
            "output": [
                {
                    "id": "msg-mock",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": SUMMARY, "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50},
        }
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": SUMMARY}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 30, "totalTokenCount": 50},
        "modelVersion": "mock-model",
        "responseId": "gemini-mock",
    }


def _http_response(protocol: str, stream: bool) -> httpx.Response:
    payload = _response_body(protocol)
    if not stream:
        return httpx.Response(200, json=payload)
    if protocol == "openai":
        events = [
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "mock-model",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": SUMMARY}, "finish_reason": "stop"}],
                "usage": payload["usage"],
            }
        ]
    elif protocol == "openai_responses":
        events = [{"type": "response.completed", "sequence_number": 1, "response": payload}]
    else:
        events = [payload]
    body = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events)
    if protocol == "openai":
        body += "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})


def _client(protocol: str, monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport):
    provider = APIProvider(
        name="mock-provider",
        base_url="https://mock.invalid/v1beta" if protocol == "gemini" else "https://mock.invalid/v1",
        api_key="offline-test-key",
        client_type=protocol,
        timeout=5,
    )
    http_client = httpx.AsyncClient(transport=transport, trust_env=False)
    if protocol == "gemini":
        monkeypatch.setattr(
            gemini_client,
            "_build_http_options",
            lambda _: HttpOptions(
                base_url="https://mock.invalid/",
                api_version="v1beta",
                httpx_async_client=http_client,
            ),
        )
        client = gemini_client.GeminiClient(provider)
    else:
        monkeypatch.setattr(openai_client, "DefaultAsyncHttpxClient", lambda **kwargs: http_client)
        client_class = openai_client.OpenaiClient if protocol == "openai" else OpenAIResponsesClient
        client = client_class(provider)
    return client, http_client


async def _close(client, http_client, protocol: str) -> None:
    if protocol == "gemini":
        await client.client.aio.aclose()
        client.client.close()
    else:
        await client.client.close()
    await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["openai", "openai_responses", "gemini"])
@pytest.mark.parametrize("stream", [False, True])
async def test_compaction_constraint_reaches_real_sdk_wire_without_changing_prefix(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    stream: bool,
) -> None:
    original, derived = _requests(protocol, stream=stream)
    before = deepcopy(original)
    captured: List[Dict[str, Any]] = []

    async def respond(request: httpx.Request):
        assert request.url.host == "mock.invalid"
        captured.append(json.loads(request.content))
        return _http_response(protocol, stream)

    client, http_client = _client(protocol, monkeypatch, httpx.MockTransport(respond))
    try:
        baseline = await client.get_response(original)
        result = await client.get_response(derived)
    finally:
        await _close(client, http_client, protocol)
    assert len(captured) == 2
    assert original == before
    assert result.content == baseline.content == SUMMARY
    assert not result.tool_calls and not result.native_tool_calls
    assert ConversationState.model_validate_json(result.content).pending == ["周五前提交 A 学校材料"]
    assert result.usage.total_tokens == 50
    assert captured[1]["tools"] == captured[0]["tools"]
    if protocol == "gemini":
        assert captured[1]["toolConfig"]["functionCallingConfig"]["mode"] == "NONE"
        assert captured[1]["systemInstruction"] == captured[0]["systemInstruction"]
        assert captured[1]["contents"][:-1] == captured[0]["contents"]
    else:
        assert captured[0]["tool_choice"] == "auto"
        assert captured[1]["tool_choice"] == "none"
        content_key = "messages" if protocol == "openai" else "input"
        assert captured[1][content_key][:-1] == captured[0][content_key]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["openai", "openai_responses", "gemini"])
async def test_compaction_without_tools_does_not_add_a_tool_selection_parameter(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
) -> None:
    original, derived = _requests(protocol, stream=False, tools=False)
    captured = []

    async def respond(request: httpx.Request):
        captured.append(json.loads(request.content))
        return _http_response(protocol, False)

    client, http_client = _client(protocol, monkeypatch, httpx.MockTransport(respond))
    try:
        result = await client.get_response(derived)
    finally:
        await _close(client, http_client, protocol)
    assert result.content == SUMMARY
    assert len(captured) == 1
    assert "tool_choice" not in captured[0]
    assert "toolConfig" not in captured[0]
    assert not captured[0].get("tools")
    assert original.tool_options is None
