from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.model_client.gemini_client import _build_non_tool_parts
from src.llm_models.model_client.openai_client import _convert_messages
from src.llm_models.model_client.openai_responses_client import (
    OpenAIResponsesClient,
    _consume_response_stream,
    _convert_context_items,
    _parse_completed_response,
)
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItem,
    ContextItemBuilder,
    ContextItemMeta,
    ContextRefusalPart,
    FunctionCallItem,
    FunctionCallOutputItem,
    ProviderOpaqueItem,
    ReasoningItem,
    RoleType,
)
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolOption
from src.maisaka.context.messages import build_model_output_context_messages


def _build_provider() -> APIProvider:
    return APIProvider(
        name="responses-test",
        base_url="https://api.example.com/v1",
        auth_type="none",
        client_type="openai_responses",
    )


def _build_model(
    *,
    force_stream_mode: bool = False,
    prefill: bool = True,
    extra_params: dict[str, Any] | None = None,
) -> ModelInfo:
    return ModelInfo(
        name="responses-model",
        model_identifier="gpt-test",
        api_provider="responses-test",
        force_stream_mode=force_stream_mode,
        prefill=prefill,
        extra_params=extra_params or {},
    )


def _build_request(
    items: list[ContextItem],
    *,
    force_stream_mode: bool = False,
    prefill: bool = True,
    extra_params: dict[str, Any] | None = None,
    tool_options: list[ToolOption] | None = None,
    response_format: RespFormat | None = None,
) -> ResponseRequest:
    return ResponseRequest(
        model_info=_build_model(
            force_stream_mode=force_stream_mode,
            prefill=prefill,
            extra_params=extra_params,
        ),
        context_items=items,
        tool_options=tool_options,
        max_tokens=256,
        temperature=0.3,
        response_format=response_format,
        extra_params=extra_params or {},
    )


def test_refusal_item_has_portable_chat_responses_and_gemini_projection() -> None:
    refusal = AssistantMessageItem(
        meta=ContextItemMeta.create(),
        parts=(ContextRefusalPart("无法回答这个问题"),),
    )
    request = _build_request([refusal])

    assert _convert_messages([refusal]) == [{"role": "assistant", "content": "无法回答这个问题"}]
    assert _convert_context_items(
        [refusal],
        request,
        "responses-test",
        "https://api.example.com/v1",
    ) == [{"role": "assistant", "content": "无法回答这个问题"}]
    assert [part.text for part in _build_non_tool_parts(refusal)] == ["无法回答这个问题"]


def test_convert_items_converts_only_final_assistant_when_prefill_disabled() -> None:
    first_assistant = ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("历史回复").build()
    user_message = ContextItemBuilder().add_text_content("新的消息").build()
    final_assistant = ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("输出引导").build()
    request = _build_request([first_assistant, user_message, final_assistant], prefill=False)

    converted = _convert_context_items(
        request.context_items,
        request,
        "responses-test",
        "https://api.example.com/v1",
    )

    assert converted == [
        {"role": "assistant", "content": "历史回复"},
        {"role": "user", "content": [{"type": "input_text", "text": "新的消息"}]},
        {"role": "user", "content": "输出引导"},
    ]


def test_parse_response_preserves_output_items_and_usage() -> None:
    request = _build_request([ContextItemBuilder().add_text_content("你好").build()])
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
                "type": "file_search_call",
                "id": "fs_test",
                "status": "completed",
            },
            {
                "type": "future_provider_item",
                "id": "future_test",
                "status": "completed",
                "extension": {"kept": True},
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
    assert [
        item.replay.materialize() for item in response.output_items if item.replay is not None
    ] == raw_response.output
    assert len({item.meta.logical_turn_id for item in response.output_items}) == 1
    assert response.generation_trace is not None
    assert response.generation_trace.response_id == "resp_test"
    assert response.generation_trace.status == "completed"
    assert response.generation_trace.output_item_ids == tuple(item.meta.item_id for item in response.output_items)
    assert response.provider_response is not None
    assert response.provider_response["id"] == "resp_test"
    assert response.provider_response["output"] == raw_response.output
    assert response.provider_response["usage"] == raw_response.usage
    assert len(response.native_tool_calls) == 2
    assert response.native_tool_calls[0].tool_type == "web_search"
    assert response.native_tool_calls[0].call_id == "ws_test"
    assert response.native_tool_calls[0].action_type == "search"
    assert response.native_tool_calls[0].details == ["查询：上海今日天气", "查询：上海气温"]
    assert response.native_tool_calls[0].source_count == 2
    assert response.native_tool_calls[1].tool_type == "file_search"
    assert response.native_tool_calls[1].call_id == "fs_test"
    assert isinstance(response.output_items[3], ProviderOpaqueItem)
    assert response.output_items[3].replay is not None
    assert response.output_items[3].replay.materialize()["extension"] == {"kept": True}
    assert usage == (120, 30, 150, 80, 40)


def test_parse_response_extracts_multiple_plaintext_reasoning_parts() -> None:
    request = _build_request([ContextItemBuilder().add_text_content("计算一道题").build()])
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
    assert len(response.output_items) == 3
    assert all(item.replay is not None for item in response.output_items)
    assert usage is None


def test_chat_projection_folds_adjacent_model_output_across_provider_activity() -> None:
    request = _build_request([ContextItemBuilder().add_text_content("你好").build()])
    raw_response = SimpleNamespace(
        id="resp_grouped_messages",
        status="completed",
        model="gpt-test",
        output=[
            {"type": "message", "content": [{"type": "output_text", "text": "第一段"}]},
            {"type": "web_search_call", "id": "search_1", "status": "completed"},
            {"type": "message", "content": [{"type": "output_text", "text": "第二段"}]},
        ],
        usage=None,
    )

    response, _ = _parse_completed_response(
        raw_response,
        request,
        "responses-test",
        "https://api.example.com/v1",
        None,
    )

    assert _convert_messages(list(response.output_items)) == [{"role": "assistant", "content": "第一段第二段"}]
    assert response.content == "第一段第二段"
    assert len(response.output_items) == 3
    assert all(item.replay is not None for item in response.output_items)


def test_convert_items_replays_unchanged_fragments_and_projects_only_edited_item() -> None:
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
    request = _build_request([ContextItemBuilder().add_text_content("天气").build()])
    raw_response = SimpleNamespace(
        id="resp_replay",
        status="completed",
        output=native_output,
        usage=None,
    )
    parsed, _ = _parse_completed_response(
        raw_response,
        request,
        "responses-test",
        "https://api.example.com/v1",
        "strict",
    )
    tool_output = FunctionCallOutputItem(
        meta=parsed.output_items[-1].meta.__class__.create(logical_turn_id=request.logical_turn_id),
        call_id="call_weather",
        output="晴，26°C",
    )

    replayed = _convert_context_items(
        [*parsed.output_items, tool_output],
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

    function_item = parsed.output_items[1]
    assert isinstance(function_item, FunctionCallItem)
    edited_function = replace(
        function_item,
        tool_call=function_item.tool_call.__class__.create(
            call_id="call_weather",
            func_name="get_weather",
            args={"city": "杭州"},
        ),
        replay=None,
    )
    converted = _convert_context_items(
        [parsed.output_items[0], edited_function],
        request,
        "responses-test",
        "https://api.example.com/v1",
    )
    assert converted[0] == native_output[0]
    assert converted[1]["type"] == "function_call"
    assert converted[1]["arguments"] == '{"city":"杭州"}'


def test_cross_model_projection_omits_nonportable_reasoning() -> None:
    original_request = _build_request([ContextItemBuilder().add_text_content("天气").build()])
    raw_response = SimpleNamespace(
        id="resp_cross_model",
        status="completed",
        output=[
            {"type": "reasoning", "id": "rs", "encrypted_content": "opaque", "summary": []},
            {
                "type": "function_call",
                "id": "fc",
                "call_id": "call-weather",
                "name": "get_weather",
                "arguments": '{"city":"上海"}',
            },
        ],
        usage=None,
    )
    parsed, _ = _parse_completed_response(
        raw_response,
        original_request,
        "responses-test",
        "https://api.example.com/v1",
        "strict",
    )
    switched_request = ResponseRequest(
        model_info=ModelInfo(
            name="other-model",
            model_identifier="gpt-other",
            api_provider="responses-test",
        ),
        context_items=list(parsed.output_items),
    )

    converted = _convert_context_items(
        list(parsed.output_items),
        switched_request,
        "responses-test",
        "https://api.example.com/v1",
    )

    assert [item["type"] for item in converted] == ["function_call"]
    assert converted[0]["call_id"] == "call-weather"


def test_maisaka_history_uses_one_entry_per_output_item() -> None:
    request = _build_request([ContextItemBuilder().add_text_content("你好").build()])
    raw_response = SimpleNamespace(
        id="resp_history",
        status="completed",
        output=[
            {"type": "reasoning", "id": "rs", "summary": []},
            {
                "type": "message",
                "id": "msg",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "原生回复"}],
            },
        ],
        usage=None,
    )
    response, _ = _parse_completed_response(
        raw_response,
        request,
        "responses-test",
        "https://api.example.com/v1",
        "strict",
    )

    history_entries = build_model_output_context_messages(response.output_items)

    assert len(history_entries) == 2
    assert isinstance(history_entries[0].output_item, ReasoningItem)
    assert history_entries[1].content == "原生回复"
    assert history_entries[0].to_context_item() is response.output_items[0]


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
        [ContextItemBuilder().add_text_content("返回 JSON").build()],
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
    assert response.wire_protocol == "responses"
    assert response.request_wire_payload["input"] == captured_kwargs["input"]
    assert captured_kwargs["store"] is False
    assert captured_kwargs["max_output_tokens"] == 256
    assert captured_kwargs["text"]["verbosity"] == "low"
    assert captured_kwargs["text"]["format"]["type"] == "json_schema"
    assert [tool["type"] for tool in captured_kwargs["tools"]] == ["function", "web_search"]
    assert captured_kwargs["extra_body"] == {"reasoning": {"effort": "low"}}


@pytest.mark.asyncio
async def test_client_rejects_server_side_conversation_state(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _build_request(
        [ContextItemBuilder().set_role(RoleType.User).add_text_content("你好").build()],
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
