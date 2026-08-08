from typing import Any

import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.llm_models.model_client.openai_client import _convert_messages
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.llm_models.payload_content.provider_state import ProviderState
from src.llm_models.payload_content.tool_option import ToolCall
from src.llm_models.request_snapshot import deserialize_messages_snapshot, serialize_messages_snapshot


class _FakeReplyModel:
    def __init__(self, result: LLMResponseResult) -> None:
        self.result = result
        self.messages: list[Message] = []
        self.options: LLMGenerationOptions | None = None

    async def generate_response_with_messages(
        self,
        message_factory: Any,
        options: LLMGenerationOptions,
    ) -> LLMResponseResult:
        self.messages = await message_factory(object(), None)
        self.options = options
        return self.result


def test_reasoning_only_assistant_message_round_trips_and_converts_to_openai() -> None:
    message = (
        MessageBuilder()
        .set_role(RoleType.Assistant)
        .set_reasoning_content("先完成内部推理")
        .build()
    )

    converted = _convert_messages([message])
    restored = deserialize_messages_snapshot(serialize_messages_snapshot([message]))

    assert converted == [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "先完成内部推理",
        }
    ]
    assert restored[0].content == []
    assert restored[0].reasoning_content == "先完成内部推理"


def test_reasoning_only_detection_excludes_tool_calls() -> None:
    reasoning_only = LLMResponseResult(response="", reasoning="内部推理")
    with_tool_call = LLMResponseResult(
        response="",
        reasoning="内部推理",
        tool_calls=[ToolCall(call_id="call_test", func_name="test", args={})],
    )

    assert BaseMaisakaReplyGenerator._is_reasoning_only_response(reasoning_only) is True
    assert BaseMaisakaReplyGenerator._is_reasoning_only_response(with_tool_call) is False


@pytest.mark.asyncio
async def test_reasoning_continuation_appends_only_assistant_and_reuses_model() -> None:
    provider_state = ProviderState(
        client_type="openai_responses",
        provider_name="test-provider",
        endpoint_fingerprint="endpoint",
        model_identifier="test-model",
        message_fingerprint="message",
        output_items=[{"type": "reasoning", "id": "reasoning_test"}],
    )
    first_result = LLMResponseResult(
        response="",
        reasoning="第一次仅返回推理",
        model_name="reply-model",
        provider_state=provider_state,
    )
    final_result = LLMResponseResult(
        response="最终可见正文",
        reasoning="第二次推理",
        model_name="reply-model",
    )
    fake_model = _FakeReplyModel(final_result)
    original_messages = [
        MessageBuilder().add_text_content("原始请求").build(),
        (
            MessageBuilder()
            .set_role(RoleType.Assistant)
            .set_tool_calls([ToolCall(call_id="call_context", func_name="lookup", args={"key": "value"})])
            .build()
        ),
        (
            MessageBuilder()
            .set_role(RoleType.Tool)
            .set_tool_call_id("call_context")
            .set_tool_name("lookup")
            .add_text_content("工具返回")
            .build()
        ),
    ]
    generator = object.__new__(BaseMaisakaReplyGenerator)

    result, continued_messages = await generator._continue_reasoning_only_response(
        active_model=fake_model,
        request_messages=original_messages,
        generation_result=first_result,
        active_model_name=None,
    )

    assert result is final_result
    assert len(continued_messages) == 4
    assert continued_messages[:3] == original_messages
    assert continued_messages[3].role == RoleType.Assistant
    assert continued_messages[3].content == []
    assert continued_messages[3].reasoning_content == "第一次仅返回推理"
    assert continued_messages[3].provider_state is provider_state
    assert all(message.role != RoleType.User for message in continued_messages[3:])
    assert fake_model.messages == continued_messages
    assert fake_model.options is not None
    assert fake_model.options.model_name == "reply-model"
