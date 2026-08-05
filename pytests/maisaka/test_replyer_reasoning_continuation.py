from dataclasses import replace
from typing import Any

import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.common.data_models.reply_generation_data_models import (
    ReplyGenerationResult,
    build_reply_monitor_detail,
)
from src.llm_models.model_client.base_client import GenerationAttempt, GenerationTrace
from src.llm_models.model_client.openai_client import _convert_messages
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItem,
    ContextItemBuilder,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    ProviderActivityItem,
    ProviderReplayFragment,
    ProviderScope,
    ReasoningItem,
    ReasoningRepresentation,
    RoleType,
)


class _FakeReplyModel:
    def __init__(self, result: LLMResponseResult) -> None:
        self.result = result
        self.items: list[ContextItem] = []
        self.options: LLMGenerationOptions | None = None

    async def generate_response_with_context(
        self,
        context_factory: Any,
        options: LLMGenerationOptions,
    ) -> LLMResponseResult:
        self.items = await context_factory(object(), None)
        self.options = options
        return self.result


def _build_reasoning_only_result() -> LLMResponseResult:
    logical_turn_id = "turn-reasoning"
    scope = ProviderScope(
        schema_version=1,
        client_type="openai_responses",
        provider_name="test-provider",
        endpoint_fingerprint="endpoint",
        model_identifier="test-model",
    )
    reasoning = ReasoningItem(
        meta=ContextItemMeta.create(
            logical_turn_id=logical_turn_id,
        ),
        summary_parts=("第一次仅返回推理",),
        representation=ReasoningRepresentation.SUMMARY,
        replay=ProviderReplayFragment.from_payload(
            scope,
            {"type": "reasoning", "id": "reasoning_test", "summary": []},
        ),
    )
    provider_activity = ProviderActivityItem(
        meta=ContextItemMeta.create(
            logical_turn_id=logical_turn_id,
        ),
        provider_type="web_search",
        call_id="search_test",
        status="completed",
        display_summary="检索完成",
        replay=ProviderReplayFragment.from_payload(
            scope,
            {"type": "web_search_call", "id": "search_test", "status": "completed"},
        ),
    )
    result = LLMResponseResult(
        output_items=(reasoning, provider_activity),
        generation_trace=GenerationTrace(
            provider="test-provider",
            endpoint="https://example.test",
            model="test-model",
            response_id="resp-reasoning",
            status="completed",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=0,
            output_item_ids=(reasoning.meta.item_id, provider_activity.meta.item_id),
        ),
        model_name="reply-model",
    )
    result.generation_attempts = (
        GenerationAttempt(
            attempt_id="reply-attempt-1",
            workflow_purpose="replyer",
            workflow_attempt=1,
            provider_attempt=1,
            model_attempt=1,
            status="succeeded",
            started_at="2026-08-05T00:00:00.000",
            duration_ms=1.0,
            provider="test-provider",
            endpoint="https://example.test",
            model="test-model",
            client_type="openai_responses",
            operation="response",
            wire_protocol="responses",
            request_items=(),
            tool_definitions=(),
            request_parameters={},
            output_items=result.output_items,
            trace=result.generation_trace,
        ),
    )
    return result


def test_chat_projection_never_sends_reasoning_item() -> None:
    result = _build_reasoning_only_result()

    converted = _convert_messages(list(result.output_items))

    assert converted == []


def test_chat_projection_folds_adjacent_output_items_without_reasoning_fields() -> None:
    logical_turn_id = "chat-turn"
    items = [
        ReasoningItem(
            meta=ContextItemMeta.create(
                logical_turn_id=logical_turn_id,
            ),
            text_parts=("不会发送的推理",),
            representation=ReasoningRepresentation.RAW_TEXT,
        ),
        AssistantMessageItem(
            meta=ContextItemMeta.create(
                logical_turn_id=logical_turn_id,
            ),
            parts=(ContextTextPart("第一段"),),
        ),
        AssistantMessageItem(
            meta=ContextItemMeta.create(
                logical_turn_id=logical_turn_id,
            ),
            parts=(ContextTextPart("第二段"),),
        ),
        FunctionCallItem(
            meta=ContextItemMeta.create(
                logical_turn_id=logical_turn_id,
            ),
            tool_call=ContextToolCall.create(call_id="call-1", func_name="lookup"),
        ),
    ]

    converted = _convert_messages(items)

    assert len(converted) == 1
    assert converted[0]["content"] == "第一段第二段"
    assert converted[0]["tool_calls"][0]["id"] == "call-1"
    assert "reasoning_content" not in converted[0]


def test_reasoning_only_detection_requires_completed_trace_and_no_body() -> None:
    reasoning_only = _build_reasoning_only_result()
    incomplete_reasoning = ReasoningItem(
        meta=ContextItemMeta.create(logical_turn_id="incomplete-turn"),
        text_parts=("尚未完成",),
        representation=ReasoningRepresentation.RAW_TEXT,
    )
    incomplete = LLMResponseResult(
        output_items=(incomplete_reasoning,),
        generation_trace=GenerationTrace(
            provider="test-provider",
            endpoint="https://example.test",
            model="test-model",
            response_id=None,
            status="incomplete",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=0,
            output_item_ids=(incomplete_reasoning.meta.item_id,),
        ),
    )

    assert BaseMaisakaReplyGenerator._is_reasoning_only_response(reasoning_only) is True
    assert BaseMaisakaReplyGenerator._is_reasoning_only_response(incomplete) is False


def test_reply_monitor_detail_preserves_item_output_and_generation_diagnostics() -> None:
    generation_result = _build_reasoning_only_result()
    reply_result = ReplyGenerationResult(
        output_items=[{"item_type": "ReasoningItem", "meta": {"item_id": "reasoning-1"}}],
        generation_attempts=BaseMaisakaReplyGenerator._serialize_generation_attempts(generation_result),
    )

    detail = build_reply_monitor_detail(reply_result)

    assert generation_result.generation_trace is not None
    assert detail["output_items"] == reply_result.output_items
    assert detail["generation_attempts"][0]["trace"]["output_item_ids"] == list(
        generation_result.generation_trace.output_item_ids
    )


@pytest.mark.asyncio
async def test_reasoning_continuation_appends_all_output_items_and_reuses_options() -> None:
    first_result = _build_reasoning_only_result()
    final_result = LLMResponseResult.from_portable_output(
        response="最终可见正文",
        reasoning="第二次推理",
        model_name="reply-model",
    )
    assert first_result.generation_trace is not None
    final_trace = replace(
        first_result.generation_trace,
        response_id="resp-final",
        output_item_ids=tuple(item.meta.item_id for item in final_result.output_items),
    )
    final_result.generation_trace = final_trace
    final_result.generation_attempts = (
        replace(
            first_result.generation_attempts[0],
            attempt_id="reply-attempt-2",
            provider_attempt=2,
            output_items=final_result.output_items,
            trace=final_trace,
        ),
    )
    fake_model = _FakeReplyModel(final_result)
    original_items = [
        ContextItemBuilder().add_text_content("原始请求").build(),
        ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("既有模型正文").build(),
        FunctionCallItem(
            meta=ContextItemMeta.create(logical_turn_id="context-turn"),
            tool_call=ContextToolCall.create(
                call_id="call-context",
                func_name="lookup",
                args={"key": "value"},
            ),
        ),
        FunctionCallOutputItem(
            meta=ContextItemMeta.create(logical_turn_id="context-turn"),
            call_id="call-context",
            output="工具返回",
            tool_name="lookup",
        ),
    ]
    options = LLMGenerationOptions(model_name="reply-model", temperature=0.4, max_tokens=321)
    generator = object.__new__(BaseMaisakaReplyGenerator)

    result, continued_items = await generator._continue_reasoning_only_response(
        active_model=fake_model,
        request_messages=original_items,
        generation_result=first_result,
        generation_options=options,
    )

    assert result is final_result
    assert continued_items[:4] == original_items
    assert continued_items[4:] == list(first_result.output_items)
    assert continued_items[4].replay is first_result.output_items[0].replay
    assert continued_items[5].replay is first_result.output_items[1].replay
    assert all(not isinstance(item, type(original_items[0])) for item in continued_items[4:])
    assert fake_model.items == continued_items
    assert fake_model.options is options
    assert [attempt.workflow_purpose for attempt in result.generation_attempts] == [
        "replyer.reasoning_initial",
        "replyer.reasoning_continuation",
    ]
    assert result.generation_attempts[0].output_items == first_result.output_items
    assert result.generation_attempts[1].output_items == final_result.output_items
