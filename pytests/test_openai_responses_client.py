from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.model_client.openai_responses_client import (
    OpenAIResponsesClient,
    _consume_response_stream,
    _convert_messages,
    _parse_completed_response,
)
from src.llm_models.payload_content.message import MessageBuilder, RoleType
from src.llm_models.payload_content.provider_state import (
    ProviderState,
    build_assistant_message_fingerprint,
    build_provider_endpoint_fingerprint,
)
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolCall, ToolOption
from src.maisaka.context.messages import AssistantMessage


def _build_provider() -> APIProvider:
    return APIProvider(
        name="responses-test",
        base_url="https://api.example.com/v1",
        auth_type="none",
        client_type="openai_responses",
    )


def _build_model(*, force_stream_mode: bool = False, extra_params: dict[str, Any] | None = None) -> ModelInfo:
    return ModelInfo(
        name="responses-model",
        model_identifier="gpt-test",
        api_provider="responses-test",
        force_stream_mode=force_stream_mode,
        extra_params=extra_params or {},
    )


def _build_request(
    messages: list[Any],
    *,
    force_stream_mode: bool = False,
    extra_params: dict[str, Any] | None = None,
    tool_options: list[ToolOption] | None = None,
    response_format: RespFormat | None = None,
) -> ResponseRequest:
    return ResponseRequest(
        model_info=_build_model(force_stream_mode=force_stream_mode, extra_params=extra_params),
        message_list=messages,
        tool_options=tool_options,
        max_tokens=256,
        temperature=0.3,
        response_format=response_format,
        extra_params=extra_params or {},
    )


def test_parse_response_preserves_output_items_and_usage() -> None:
    request = _build_request([MessageBuilder().add_text_content("你好").build()])
    raw_response = SimpleNamespace(
        id="resp_test",
        model="gpt-test",
        status="completed",
        output=[
            {
                "type": "reasoning",
                "id": "rs_test",
                "summary": [{"type": "summary_text", "text": "检查天气参数"}],
                "content": [{"type": "reasoning_text", "text": "不应覆盖可展示摘要"}],
                "encrypted_content": "encrypted-state",
            },
            {
                "type": "web_search_call",
                "id": "ws_test",
                "status": "completed",
                "action": {
                    "type": "search",
                    "queries": ["上海今日天气", "上海气温"],
                    "sources": [
                        {"type": "url", "url": "https://weather.example.com"},
                        {"type": "url", "url": "https://news.example.com"},
                    ],
                },
            },
            {
                "type": "function_call",
                "id": "fc_test",
                "call_id": "call_weather",
                "name": "get_weather",
                "arguments": '{"city":"上海"}',
                "status": "completed",
            },
        ],
        usage={
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 80},
        },
    )

    response, usage = _parse_completed_response(
        raw_response,
        request,
        "responses-test",
        "https://api.example.com/v1",
        "strict",
    )

    assert response.reasoning_content == "检查天气参数"
    assert response.tool_calls is not None
    assert response.tool_calls[0].call_id == "call_weather"
    assert response.tool_calls[0].args == {"city": "上海"}
    assert response.provider_state is not None
    assert response.provider_state.output_items == raw_response.output
    assert response.provider_response is not None
    assert response.provider_response["id"] == "resp_test"
    assert response.provider_response["output"] == raw_response.output
    assert response.provider_response["usage"] == raw_response.usage
    assert len(response.native_tool_calls) == 1
    assert response.native_tool_calls[0].tool_type == "web_search"
    assert response.native_tool_calls[0].call_id == "ws_test"
    assert response.native_tool_calls[0].action_type == "search"
    assert response.native_tool_calls[0].details == ["查询：上海今日天气", "查询：上海气温"]
    assert response.native_tool_calls[0].source_count == 2
    assert usage == (120, 30, 150, 80, 40)


def test_parse_response_extracts_multiple_plaintext_reasoning_parts() -> None:
    request = _build_request([MessageBuilder().add_text_content("计算一道题").build()])
    raw_response = SimpleNamespace(
        id="resp_deepseek",
        model="deepseek-v4-flash",
        status="completed",
        output=[
            {
                "type": "reasoning",
                "id": "rs_first",
                "status": "completed",
                "content": [
                    {"type": "reasoning_text", "text": "第一段推理"},
                    {"type": "reasoning_text", "text": "第二段推理"},
                ],
                "summary": [],
            },
            {
                "type": "reasoning",
                "id": "rs_second",
                "status": "completed",
                "content": [{"type": "reasoning_text", "text": "第三段推理"}],
                "summary": [],
            },
            {
                "type": "message",
                "id": "msg_answer",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "最终答案"}],
            },
        ],
        usage=None,
    )

    response, usage = _parse_completed_response(
        raw_response,
        request,
        "responses-test",
        "https://api.example.com/v1",
        "strict",
    )

    assert response.reasoning_content == "第一段推理\n第二段推理\n第三段推理"
    assert response.content == "最终答案"
    assert response.provider_state is not None
    assert response.provider_state.output_items == raw_response.output
    assert usage is None


def test_convert_messages_replays_matching_state_and_falls_back_after_edit() -> None:
    tool_call = ToolCall(call_id="call_weather", func_name="get_weather", args={"city": "上海"})
    native_output = [
        {"type": "reasoning", "id": "rs_test", "encrypted_content": "encrypted-state", "summary": []},
        {
            "type": "function_call",
            "id": "fc_test",
            "call_id": "call_weather",
            "name": "get_weather",
            "arguments": '{"city":"上海"}',
        },
    ]
    state = ProviderState(
        client_type="openai_responses",
        provider_name="responses-test",
        endpoint_fingerprint=build_provider_endpoint_fingerprint(
            "openai_responses",
            "https://api.example.com/v1",
        ),
        model_identifier="gpt-test",
        message_fingerprint=build_assistant_message_fingerprint("", [tool_call]),
        output_items=native_output,
    )
    assistant_message = (
        MessageBuilder().set_role(RoleType.Assistant).set_tool_calls([tool_call]).set_provider_state(state).build()
    )
    tool_message = (
        MessageBuilder().set_role(RoleType.Tool).set_tool_call_id("call_weather").add_text_content("晴，26°C").build()
    )
    request = _build_request([assistant_message, tool_message])

    replayed = _convert_messages(
        request.message_list,
        request,
        "responses-test",
        "https://api.example.com/v1",
    )
    assert replayed[:2] == native_output
    assert replayed[2] == {
        "type": "function_call_output",
        "call_id": "call_weather",
        "output": "晴，26°C",
    }

    edited_message = (
        MessageBuilder()
        .set_role(RoleType.Assistant)
        .add_text_content("Hook 修改后的正文")
        .set_tool_calls([tool_call])
        .set_provider_state(state)
        .build()
    )
    edited_request = _build_request([edited_message])
    converted = _convert_messages(
        edited_request.message_list,
        edited_request,
        "responses-test",
        "https://api.example.com/v1",
    )
    assert converted[0] == {"role": "assistant", "content": "Hook 修改后的正文"}
    assert converted[1]["type"] == "function_call"
    assert all(item.get("type") != "reasoning" for item in converted)


def test_maisaka_assistant_message_transparently_carries_provider_state() -> None:
    state = ProviderState(
        client_type="openai_responses",
        provider_name="responses-test",
        endpoint_fingerprint="endpoint",
        model_identifier="gpt-test",
        message_fingerprint=build_assistant_message_fingerprint("原生回复", None),
        output_items=[{"type": "message", "role": "assistant", "content": []}],
    )
    context_message = AssistantMessage(
        content="原生回复",
        timestamp=datetime.now(),
        provider_state=state,
    )

    llm_message = context_message.to_llm_message()

    assert llm_message is not None
    assert llm_message.provider_state is state


@pytest.mark.asyncio
async def test_client_merges_structured_output_and_native_tools() -> None:
    provider = _build_provider()
    client = OpenAIResponsesClient(provider)
    captured_kwargs: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            id="resp_text",
            model="gpt-test",
            status="completed",
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": '{"ok":true}'}],
                }
            ],
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    client.client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
    response_format = RespFormat(
        RespFormatType.JSON_SCHEMA,
        {
            "name": "result",
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    )
    request = _build_request(
        [MessageBuilder().add_text_content("返回 JSON").build()],
        extra_params={
            "body": {
                "reasoning": {"effort": "low"},
                "text": {"verbosity": "low"},
                "tools": [{"type": "web_search"}],
            }
        },
        tool_options=[ToolOption(name="local_tool", description="本地工具")],
        response_format=response_format,
    )

    response = await client.get_response(request)

    assert response.content == '{"ok":true}'
    assert response.usage is not None
    assert response.usage.total_tokens == 15
    assert captured_kwargs["store"] is False
    assert captured_kwargs["max_output_tokens"] == 256
    assert captured_kwargs["text"]["verbosity"] == "low"
    assert captured_kwargs["text"]["format"]["type"] == "json_schema"
    assert [tool["type"] for tool in captured_kwargs["tools"]] == ["function", "web_search"]
    assert captured_kwargs["extra_body"] == {"reasoning": {"effort": "low"}}


@pytest.mark.asyncio
async def test_client_rejects_server_side_conversation_state(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _build_request(
        [MessageBuilder().set_role(RoleType.User).add_text_content("你好").build()],
        extra_params={"previous_response_id": "resp_old"},
    )
    client = object.__new__(OpenAIResponsesClient)
    client.api_provider = _build_provider()
    client.tool_argument_parse_mode = "strict"
    monkeypatch.setattr(client, "_attach_failure_snapshot", lambda *args, **kwargs: None)

    with pytest.raises(ValueError, match="previous_response_id"):
        await client.get_response(request)


class _FakeResponseStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = iter(events)
        self.closed = False

    def __aiter__(self) -> "_FakeResponseStream":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stream_uses_completed_response_as_source_of_truth() -> None:
    completed_response = SimpleNamespace(id="resp_stream", status="completed", output=[])
    stream = _FakeResponseStream(
        [
            {"type": "response.output_text.delta", "delta": "临时增量"},
            {"type": "response.completed", "response": completed_response},
        ]
    )

    result = await _consume_response_stream(stream, None)  # type: ignore[arg-type]

    assert result is completed_response
    assert stream.closed is True
