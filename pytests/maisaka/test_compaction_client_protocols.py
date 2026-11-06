"""Exercise real provider SDK serialization with an in-process HTTP transport.

No model service is called: mock HTTP responses exercise request projection,
SDK parsing and the shared response/context models for both streaming modes.
"""

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List

import json

from google.genai.types import GoogleSearch, HttpOptions, Tool
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


def _requests(
    protocol: str,
    *,
    stream: bool,
    tools: bool = True,
    extra_params: Dict[str, Any] | None = None,
    model_identifier: str = "mock-model",
):
    """Derive a summary request from synthetic history and a completed tool turn."""
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
            model_identifier=model_identifier,
            model_type="llm",
            api_provider="mock-provider",
            force_stream_mode=stream,
        ),
        context_items=items,
        tool_options=[ToolOption(name="stop", description="结束思考")] if tools else None,
        max_tokens=512,
        # The derived constraint must override a model-level choice as well.
        extra_params=(
            deepcopy(extra_params)
            if extra_params is not None
            else {"tool_choice": "auto"}
            if protocol != "gemini" and tools
            else {}
        ),
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
    """Represent the same valid summary in each provider's response schema."""
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
    """Encode fixed provider responses as JSON or the provider's SSE format."""
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
    """Construct a real provider SDK client with only its HTTP transport replaced."""
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
    """Close SDK resources and the test transport without leaking pooled clients."""
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
    """Keep the Planner prefix intact while the wire request forbids functions."""
    original, derived = _requests(protocol, stream=stream)
    before = deepcopy(original)
    captured: List[Dict[str, Any]] = []

    async def respond(request: httpx.Request):
        """Record serialized requests and return a fixed provider completion."""
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
@pytest.mark.parametrize("extra_params", [{}, {"tool_choice": "auto"}, {"body": {"tool_choice": "required"}}])
async def test_compaction_without_tools_does_not_add_a_tool_selection_parameter(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    extra_params: Dict[str, Any],
) -> None:
    """Omit orphan selection parameters even when Planner configuration supplied one."""
    original, derived = _requests(protocol, stream=False, tools=False, extra_params=extra_params)
    before = deepcopy(original)
    captured = []

    async def respond(request: httpx.Request):
        """Capture the tool-free wire payload without contacting a provider."""
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
    assert original == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_compaction_disables_chat_tools_from_extra_body(
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
) -> None:
    """Honor summary constraints when Chat tools bypass the common tool-options field."""
    configured_tools = [
        {
            "type": "function",
            "function": {"name": "configured_function", "parameters": {"type": "object", "properties": {}}},
        }
    ]
    original, derived = _requests(
        "openai",
        stream=stream,
        tools=False,
        extra_params={"body": {"tools": configured_tools, "tool_choice": "required"}},
    )
    before = deepcopy(original)
    captured = []

    async def respond(request: httpx.Request):
        """Observe the effective body after the SDK merges explicit tools and extra_body."""
        captured.append(json.loads(request.content))
        return _http_response("openai", stream)

    client, http_client = _client("openai", monkeypatch, httpx.MockTransport(respond))
    try:
        await client.get_response(original)
        result = await client.get_response(derived)
    finally:
        await _close(client, http_client, "openai")
    assert original == before
    assert derived.extra_params == before.extra_params
    assert result.content == SUMMARY
    assert not result.tool_calls and not result.native_tool_calls
    assert len(captured) == 2
    assert captured[0]["tool_choice"] == "required"
    assert captured[1]["tool_choice"] == "none"
    assert captured[1]["tools"] == captured[0]["tools"] == configured_tools
    assert captured[1]["messages"][:-1] == captured[0]["messages"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("location,declared_tools", [("body", False), ("top", False), ("body", True)])
async def test_compaction_disables_responses_native_tools(
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
    location: str,
    declared_tools: bool,
) -> None:
    """Override native-only and mixed Responses tool modes while retaining tool definitions."""
    configured_tools = [{"type": "web_search_preview"}]
    overrides = {"tools": configured_tools, "tool_choice": "required" if location == "body" else "auto"}
    extra_params = {"body": overrides} if location == "body" else overrides
    original, derived = _requests(
        "openai_responses",
        stream=stream,
        tools=declared_tools,
        extra_params=extra_params,
    )
    before = deepcopy(original)
    captured = []

    async def respond(request: httpx.Request):
        """Record both wire requests to compare native-tool configuration and input."""
        captured.append(json.loads(request.content))
        return _http_response("openai_responses", stream)

    client, http_client = _client("openai_responses", monkeypatch, httpx.MockTransport(respond))
    try:
        await client.get_response(original)
        result = await client.get_response(derived)
    finally:
        await _close(client, http_client, "openai_responses")
    assert original == before
    assert derived.extra_params == before.extra_params
    assert result.content == SUMMARY
    assert not result.tool_calls and not result.native_tool_calls
    assert len(captured) == 2
    assert captured[0]["tool_choice"] == overrides["tool_choice"]
    assert captured[1]["tool_choice"] == "none"
    assert captured[1]["tools"] == captured[0]["tools"]
    assert captured[1]["tools"][-1] == configured_tools[0]
    assert captured[1]["input"][:-1] == captured[0]["input"]


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_tools,stream", [(False, False), (True, True)])
@pytest.mark.parametrize("search_source", ["flag", "suffix", "dict", "typed", "mixed"])
async def test_compaction_disables_gemini_search_without_mutating_planner(
    monkeypatch: pytest.MonkeyPatch,
    declared_tools: bool,
    stream: bool,
    search_source: str,
) -> None:
    """Remove every search entry point, preserving only function declarations in summaries."""
    model_identifier = "mock-model"
    if search_source == "flag":
        extra_params = {"enable_google_search": True}
    elif search_source == "suffix":
        model_identifier += "-search"
        # The legacy suffix can override a false config flag before compaction.
        extra_params = {"enable_google_search": False}
    elif search_source == "dict":
        extra_params = {"tools": [{"google_search": {}}]}
    elif search_source == "typed":
        extra_params = {"tools": [Tool(google_search=GoogleSearch())]}
    else:
        extra_params = {
            "tools": [
                {
                    "google_search": {},
                    "function_declarations": [{"name": "configured_function", "description": "模型配置内的函数"}],
                }
            ]
        }
    original, derived = _requests(
        "gemini",
        stream=stream,
        tools=declared_tools,
        extra_params=extra_params,
        model_identifier=model_identifier,
    )
    before = deepcopy(original)
    captured = []

    async def respond(request: httpx.Request):
        """Capture SDK normalization of native search and function declarations."""
        captured.append({"path": request.url.path, "body": json.loads(request.content)})
        return _http_response("gemini", stream)

    client, http_client = _client("gemini", monkeypatch, httpx.MockTransport(respond))
    try:
        await client.get_response(original)
        result = await client.get_response(derived)
    finally:
        await _close(client, http_client, "gemini")
    assert original == before
    assert derived.extra_params == before.extra_params
    assert derived.model_info.model_identifier == before.model_info.model_identifier
    assert result.content == SUMMARY
    assert not result.tool_calls and not result.native_tool_calls
    assert len(captured) == 2
    planner = captured[0]["body"]
    summary = captured[1]["body"]
    assert any("googleSearch" in tool for tool in planner["tools"])
    assert all(set(tool) == {"functionDeclarations"} for tool in summary.get("tools", []))
    planner_functions = [function for tool in planner["tools"] for function in tool.get("functionDeclarations", [])]
    summary_functions = [
        function for tool in summary.get("tools", []) for function in tool.get("functionDeclarations", [])
    ]
    assert summary_functions == planner_functions
    if planner_functions:
        assert summary["toolConfig"]["functionCallingConfig"]["mode"] == "NONE"
    else:
        assert "tools" not in summary
        assert "toolConfig" not in summary
    assert summary["systemInstruction"] == planner["systemInstruction"]
    assert summary["contents"][:-1] == planner["contents"]
    assert captured[1]["path"] == captured[0]["path"]
    assert "-search" not in captured[1]["path"]
