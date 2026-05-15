from types import SimpleNamespace

import pytest

from src.config.model_configs import APIProvider, ReasoningParseMode, ToolArgumentParseMode
from src.llm_models.model_client.openai_client import (
    _OpenAIStreamAccumulator,
    _build_reasoning_key,
    _convert_messages,
    _default_normal_response_parser,
    _parse_tool_arguments,
    _sanitize_messages_for_toolless_request,
)
from src.llm_models.payload_content.message import Message, RoleType, TextMessagePart
from src.llm_models.payload_content.tool_option import ToolCall


@pytest.mark.parametrize("parse_mode", list(ToolArgumentParseMode))
def test_parse_tool_arguments_treats_blank_arguments_as_empty_dict(parse_mode: ToolArgumentParseMode) -> None:
    assert _parse_tool_arguments("", parse_mode, None) == {}
    assert _parse_tool_arguments("   ", parse_mode, None) == {}


def test_normal_response_parser_accepts_empty_string_arguments_for_parameterless_tool() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="finish-call",
                            type="function",
                            function=SimpleNamespace(name="finish", arguments=""),
                        )
                    ],
                ),
            )
        ],
        usage=None,
        model="glm-5.1",
    )

    api_response, usage_record = _default_normal_response_parser(
        response,
        reasoning_parse_mode=ReasoningParseMode.AUTO,
        tool_argument_parse_mode=ToolArgumentParseMode.AUTO,
        reasoning_key=None,
    )

    assert len(api_response.tool_calls) == 1
    assert api_response.tool_calls[0].func_name == "finish"
    assert api_response.tool_calls[0].args == {}
    assert usage_record is None


def test_sanitize_messages_for_toolless_request_drops_assistant_tool_call_without_parts() -> None:
    messages = [
        Message(
            role=RoleType.Assistant,
            tool_calls=[
                ToolCall(
                    call_id="call_1",
                    func_name="mute_user",
                    args={"target": "alice"},
                )
            ],
        ),
        Message(
            role=RoleType.User,
            parts=[TextMessagePart(text="继续")],
        ),
    ]

    sanitized_messages = _sanitize_messages_for_toolless_request(messages)

    assert len(sanitized_messages) == 1
    assert sanitized_messages[0].role == RoleType.User


def test_sanitize_messages_for_toolless_request_keeps_assistant_reasoning_content() -> None:
    messages = [
        Message(
            role=RoleType.Assistant,
            parts=[TextMessagePart(text="正式回复")],
            tool_calls=[
                ToolCall(
                    call_id="call_1",
                    func_name="reply",
                    args={},
                )
            ],
            reasoning_content="上一轮推理内容",
        )
    ]

    sanitized_messages = _sanitize_messages_for_toolless_request(messages)

    assert len(sanitized_messages) == 1
    assert sanitized_messages[0].tool_calls is None
    assert sanitized_messages[0].reasoning_content == "上一轮推理内容"


def test_normal_response_parser_ignores_reasoning_field_for_non_openrouter_provider() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="正式回复",
                    reasoning="推理内容",
                    tool_calls=None,
                ),
            )
        ],
        usage=None,
        model="openrouter/test-model",
    )

    api_response, usage_record = _default_normal_response_parser(
        response,
        reasoning_parse_mode=ReasoningParseMode.AUTO,
        tool_argument_parse_mode=ToolArgumentParseMode.AUTO,
        reasoning_key=_build_reasoning_key(
            APIProvider(name="test", base_url="https://openrouter.ai.example.com/api/v1", api_key="test")
        ),
    )

    assert api_response.content == "正式回复"
    assert api_response.reasoning_content is None
    assert usage_record is None


def test_normal_response_parser_reads_provider_reasoning_field_for_reasoning_domains() -> None:
    provider_urls = [
        "https://openrouter.ai/compatible-api",
        "https://api.groq.com/openai/v1",
    ]

    for provider_url in provider_urls:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="正式回复",
                        reasoning="推理内容",
                        tool_calls=None,
                    ),
                )
            ],
            usage=None,
            model="test-model",
        )

        api_response, usage_record = _default_normal_response_parser(
            response,
            reasoning_parse_mode=ReasoningParseMode.AUTO,
            tool_argument_parse_mode=ToolArgumentParseMode.AUTO,
            reasoning_key=_build_reasoning_key(
                APIProvider(name="reasoning-provider", base_url=provider_url, api_key="test")
            ),
        )

        assert api_response.content == "正式回复"
        assert api_response.reasoning_content == "推理内容"
        assert usage_record is None


def test_stream_accumulator_reads_openrouter_reasoning_delta_field() -> None:
    accumulator = _OpenAIStreamAccumulator(
        reasoning_parse_mode=ReasoningParseMode.AUTO,
        tool_argument_parse_mode=ToolArgumentParseMode.AUTO,
        reasoning_key=_build_reasoning_key(
            APIProvider(name="openrouter", base_url="https://openrouter.ai/compatible-api", api_key="test")
        ),
    )
    try:
        accumulator.process_delta(SimpleNamespace(reasoning="流式推理", content=None, tool_calls=None))
        accumulator.process_delta(SimpleNamespace(content="正式回复", tool_calls=None))

        api_response = accumulator.build_response()
    finally:
        accumulator.close()

    assert api_response.content == "正式回复"
    assert api_response.reasoning_content == "流式推理"


def test_convert_messages_passes_back_assistant_reasoning_content() -> None:
    messages = [
        Message(
            role=RoleType.Assistant,
            parts=[TextMessagePart(text="正式回复")],
            reasoning_content="上一轮推理内容",
        )
    ]

    payload = _convert_messages(messages, reasoning_key="reasoning_content")

    assert payload == [
        {
            "role": "assistant",
            "content": "正式回复",
            "reasoning_content": "上一轮推理内容",
        }
    ]


def test_convert_messages_uses_provider_reasoning_key_when_passing_back_reasoning() -> None:
    messages = [
        Message(
            role=RoleType.Assistant,
            parts=[TextMessagePart(text="正式回复")],
            reasoning_content="上一轮推理内容",
        )
    ]

    payload = _convert_messages(messages, reasoning_key="reasoning")

    assert payload == [
        {
            "role": "assistant",
            "content": "正式回复",
            "reasoning": "上一轮推理内容",
        }
    ]
