"""Maisaka 推理引擎测试。"""

from types import SimpleNamespace
from typing import Optional

import pytest

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.llm_models.model_client.base_client import GenerationAttempt, GenerationTrace
from src.llm_models.payload_content.context_item import (
    ContextItemMeta,
    ProviderActivityItem,
)
from src.llm_models.payload_content.native_tool import NativeToolCallSummary
from src.maisaka.chat_loop_service import ChatResponse, MaisakaChatLoopService
from src.maisaka.context.usage import (
    SECTION_INSTANT_NOTICE,
    SECTION_MESSAGES,
    SECTION_SYSTEM_PROMPT,
    ContextSectionUsage,
)
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.monitor.events import (
    _serialize_planner_block,
    _serialize_request_block,
    _serialize_tool_results,
)
from src.maisaka.reasoning_engine import MaisakaReasoningEngine


def _build_chat_response(content: Optional[str], reasoning: str) -> ChatResponse:
    """构造仅包含 Planner 思考字段的响应。"""

    result = LLMResponseResult.from_portable_output(
        response=content or "",
        reasoning=reasoning,
    )
    return ChatResponse(
        output_items=result.output_items,
        request_messages=[],
        selected_history_count=0,
        tool_count=0,
        prompt_tokens=0,
        built_message_count=0,
        completion_tokens=0,
        total_tokens=0,
    )


@pytest.mark.parametrize(
    ("content", "reasoning", "expected"),
    [
        (" Planner 工具正文 ", " Provider 原生推理 ", "Planner 工具正文"),
        ("", " Provider 原生推理 ", ""),
        (None, " Provider 原生推理 ", ""),
        ("   ", "   ", ""),
    ],
)
def test_planner_content_does_not_fall_back_to_reasoning(
    content: Optional[str],
    reasoning: str,
    expected: str,
) -> None:
    response = _build_chat_response(content, reasoning)

    result = MaisakaReasoningEngine._get_planner_content(response)

    assert result == expected


def test_native_tool_summary_is_serialized_without_provider_state() -> None:
    summary = NativeToolCallSummary(
        tool_type="web_search",
        call_id="ws_test",
        status="completed",
        action_type="search",
        details=["查询：Responses API"],
        source_count=2,
    )

    block = _serialize_planner_block("完成", [], [summary], 10, 5, 15, 100.0)

    assert block is not None
    assert block["native_tool_calls"] == [
        {
            "tool_type": "web_search",
            "call_id": "ws_test",
            "status": "completed",
            "action_type": "search",
            "details": ["查询：Responses API"],
            "source_count": 2,
        }
    ]
    assert "provider_state" not in block


def test_tool_stop_request_is_serialized_for_monitor() -> None:
    tools = _serialize_tool_results(
        [
            {
                "tool_call_id": "call-test",
                "tool_name": "test_tool",
                "success": True,
                "stop_after_execution": True,
                "started_at": 100.25,
                "ended_at": 102.75,
            }
        ]
    )

    assert tools[0]["stop_after_execution"] is True
    assert tools[0]["started_at"] == 100.25
    assert tools[0]["ended_at"] == 102.75


def test_cache_tokens_and_context_sections_are_serialized_for_monitor() -> None:
    planner_block = _serialize_planner_block("完成", [], [], 100, 5, 105, 100.0, "", 80, 20)
    request_block = _serialize_request_block(
        [{"role": "system", "content": "系统提示词"}],
        1,
        2,
        [ContextSectionUsage("messages", 12, 1)],
    )

    assert planner_block is not None
    assert planner_block["prompt_cache_hit_tokens"] == 80
    assert planner_block["prompt_cache_miss_tokens"] == 20
    assert request_block is not None
    assert request_block["context_sections"] == [{"key": "messages", "chars": 12, "count": 1}]


@pytest.mark.asyncio
async def test_chat_loop_reports_context_sections_and_cache_tokens(monkeypatch) -> None:
    """chat_loop_step 应回报提示词分段用量与缓存命中 token。"""

    class FakeLLMClient:
        async def generate_response_with_context(self, context_factory, options) -> LLMResponseResult:
            del options
            context_factory(None)
            result = LLMResponseResult.from_portable_output(response="好的", model_name="test-model")
            result.prompt_tokens = 1000
            result.completion_tokens = 20
            result.total_tokens = 1020
            result.prompt_cache_hit_tokens = 800
            result.prompt_cache_miss_tokens = 200
            return result

    class PassthroughRuntimeManager:
        async def invoke_hook(self, hook_name: str, **kwargs: object) -> SimpleNamespace:
            del hook_name
            return SimpleNamespace(kwargs=kwargs)

    service = MaisakaChatLoopService(chat_system_prompt="测试系统提示词")
    monkeypatch.setattr(service, "_get_llm_chat_client", lambda request_kind: FakeLLMClient())
    monkeypatch.setattr(
        MaisakaChatLoopService,
        "_get_runtime_manager",
        staticmethod(lambda: PassthroughRuntimeManager()),
    )
    monkeypatch.setattr(
        PromptCLIVisualizer,
        "build_prompt_section_result",
        staticmethod(
            lambda *args, **kwargs: SimpleNamespace(
                panel=None,
                preview_access=SimpleNamespace(preview_web_uri=""),
            )
        ),
    )

    response = await service.chat_loop_step([], tool_definitions=[])

    sections = {section.key: section.chars for section in response.context_sections}
    # 本轮没有历史消息：系统提示词单独成段，末尾的当前时间与提醒归入即时提示
    assert sections[SECTION_SYSTEM_PROMPT] == len("测试系统提示词")
    assert sections[SECTION_INSTANT_NOTICE] > 0
    assert SECTION_MESSAGES not in sections
    assert response.prompt_cache_hit_tokens == 800
    assert response.prompt_cache_miss_tokens == 200


@pytest.mark.asyncio
async def test_chat_loop_keeps_reasoning_separate_from_content(monkeypatch) -> None:
    """Provider 仅返回 reasoning 时，不应将其回填为 Planner 正文。"""

    class FakeLLMClient:
        async def generate_response_with_context(self, context_factory, options) -> LLMResponseResult:
            del context_factory, options
            result = LLMResponseResult.from_portable_output(
                reasoning="Provider 原生推理",
                model_name="test-model",
            )
            logical_turn_id = result.output_items[0].meta.logical_turn_id
            assert logical_turn_id is not None
            result.output_items = (
                *result.output_items,
                ProviderActivityItem(
                    meta=ContextItemMeta.create(
                        logical_turn_id=logical_turn_id,
                    ),
                    provider_type="web_search",
                    call_id="ws_test",
                    status="completed",
                    action_type="search",
                    details=("查询：Responses API",),
                ),
            )
            result.generation_trace = GenerationTrace(
                provider="test-provider",
                endpoint="responses",
                model="test-model",
                response_id="resp_test",
                status="completed",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                prompt_cache_hit_tokens=0,
                prompt_cache_miss_tokens=0,
                output_item_ids=tuple(item.meta.item_id for item in result.output_items),
            )
            result.provider_response = {
                "id": "resp_test",
                "status": "completed",
                "output": [
                    {
                        "type": "reasoning",
                        "id": "rs_test",
                        "summary": [{"type": "summary_text", "text": "Provider 原生推理"}],
                    },
                    {
                        "type": "web_search_call",
                        "id": "ws_test",
                        "status": "completed",
                        "action": {"type": "search", "queries": ["Responses API"]},
                    },
                ],
            }
            result.generation_attempts = (
                GenerationAttempt(
                    attempt_id="planner-attempt-1",
                    workflow_purpose="planner",
                    workflow_attempt=1,
                    provider_attempt=1,
                    model_attempt=1,
                    status="succeeded",
                    started_at="2026-08-05T00:00:00.000",
                    duration_ms=1.0,
                    provider="test-provider",
                    endpoint="responses",
                    model="test-model",
                    client_type="openai_responses",
                    operation="response",
                    wire_protocol="responses",
                ),
            )
            return result

    class PassthroughRuntimeManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def invoke_hook(self, hook_name: str, **kwargs: object) -> SimpleNamespace:
            self.calls.append((hook_name, kwargs))
            return SimpleNamespace(kwargs=kwargs)

    runtime_manager = PassthroughRuntimeManager()
    service = MaisakaChatLoopService(chat_system_prompt="测试系统提示词")
    monkeypatch.setattr(service, "_get_llm_chat_client", lambda request_kind: FakeLLMClient())
    monkeypatch.setattr(
        MaisakaChatLoopService,
        "_get_runtime_manager",
        staticmethod(lambda: runtime_manager),
    )
    prompt_preview_kwargs: dict[str, object] = {}

    def build_prompt_section_result(*args: object, **kwargs: object) -> SimpleNamespace:
        del args
        prompt_preview_kwargs.update(kwargs)
        return SimpleNamespace(
            panel=None,
            preview_access=SimpleNamespace(preview_web_uri=""),
        )

    monkeypatch.setattr(
        PromptCLIVisualizer,
        "build_prompt_section_result",
        build_prompt_section_result,
    )

    response = await service.chat_loop_step([])

    after_response_kwargs = next(
        kwargs for hook_name, kwargs in runtime_manager.calls if hook_name == "maisaka.planner.after_response"
    )
    assert len(after_response_kwargs["output_items"]) == 2
    assert response.content is None
    assert all(message.content == "" for message in response.raw_messages)
    assert response.reasoning == "Provider 原生推理"
    assert response.native_tool_calls[0].call_id == "ws_test"
    assert all(not hasattr(message, "native_tool_calls") for message in response.raw_messages)
    preview_output_items = prompt_preview_kwargs["output_items"]
    assert isinstance(preview_output_items, tuple)
    assert len(preview_output_items) == 2
    assert preview_output_items[0].__class__.__name__ == "ReasoningItem"
    assert preview_output_items[1].__class__.__name__ == "ProviderActivityItem"
    generation_attempts = prompt_preview_kwargs["generation_attempts"]
    assert isinstance(generation_attempts, tuple)
    assert generation_attempts[0].attempt_id == "planner-attempt-1"
    assert not hasattr(generation_attempts[0], "wire_response")
