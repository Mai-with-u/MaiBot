"""Maisaka 推理引擎测试。"""

from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, Mock

import pytest

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation
from src.llm_models.model_client.base_client import GenerationAttempt, GenerationTrace
from src.llm_models.payload_content.context_item import (
    ContextItemMeta,
    ProviderActivityItem,
)
from src.llm_models.payload_content.native_tool import NativeToolCallSummary
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.chat_loop_service import ChatResponse, MaisakaChatLoopService
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.mode_policy import is_idle_cycle_reason
from src.maisaka.monitor.events import _serialize_planner_block, _serialize_tool_results
from src.maisaka.reasoning_engine import STOP_AFTER_EXECUTION_PAUSE_REASON, MaisakaReasoningEngine


class _ToolRegistryStub:
    """按顺序返回预设工具结果。"""

    def __init__(self, results: list[ToolExecutionResult]) -> None:
        self._results = list(results)
        self.invoked_tool_names: list[str] = []

    async def list_tools(self, context: object) -> list[object]:
        del context
        return []

    async def invoke(self, invocation: ToolInvocation, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        self.invoked_tool_names.append(invocation.tool_name)
        return self._results.pop(0)


def _build_tool_engine(results: list[ToolExecutionResult]) -> tuple[MaisakaReasoningEngine, SimpleNamespace]:
    """构造仅用于工具批次测试的推理引擎。"""

    runtime = SimpleNamespace(
        _tool_registry=_ToolRegistryStub(results),
        session_id="session-test",
        chat_stream=SimpleNamespace(
            is_group_session=True,
            group_id="group-test",
            user_id="",
            platform="test",
        ),
        is_action_tool_currently_available=lambda tool_name: True,
        _update_stage_status=lambda *args, **kwargs: None,
        _reset_consecutive_wait_count=Mock(),
        _end_planner_continuation=Mock(),
        _enter_stop_state=Mock(),
        log_prefix="[test]",
    )
    engine = MaisakaReasoningEngine(runtime)
    engine._record_tool_execution_effects = AsyncMock()  # type: ignore[method-assign]
    engine._append_tool_execution_result = lambda *args, **kwargs: None  # type: ignore[method-assign]
    engine._append_tool_post_history_messages = lambda messages: None  # type: ignore[method-assign]
    return engine, runtime


@pytest.mark.asyncio
async def test_successful_stop_request_finishes_after_full_tool_batch() -> None:
    engine, runtime = _build_tool_engine(
        [
            ToolExecutionResult(
                tool_name="terminal_tool",
                success=True,
                content="已完成",
                stop_after_execution=True,
            ),
            ToolExecutionResult(
                tool_name="second_terminal_tool",
                success=True,
                content="第二个终止工具完成",
                stop_after_execution=True,
            ),
            ToolExecutionResult(tool_name="following_tool", success=True, content="后续工具完成"),
        ]
    )

    should_pause, pause_reason, _, monitor_results = await engine._handle_tool_calls(
        [
            ToolCall(call_id="call-1", func_name="terminal_tool"),
            ToolCall(call_id="call-2", func_name="second_terminal_tool"),
            ToolCall(call_id="call-3", func_name="following_tool"),
        ],
        "测试思考",
    )

    assert should_pause is True
    assert pause_reason == STOP_AFTER_EXECUTION_PAUSE_REASON
    assert runtime._tool_registry.invoked_tool_names == [
        "terminal_tool",
        "second_terminal_tool",
        "following_tool",
    ]
    assert [result["stop_after_execution"] for result in monitor_results] == [True, True, False]
    runtime._end_planner_continuation.assert_called_once_with()
    assert runtime._reset_consecutive_wait_count.call_args_list[-1].args == ("tool_stop_after_execution",)
    runtime._enter_stop_state.assert_called_once_with()

    cycle_end = engine._cycle_end_for_pause_tool(pause_reason)
    assert cycle_end.reason == "tool_stop_after_execution"
    assert is_idle_cycle_reason(cycle_end.reason) is True


@pytest.mark.asyncio
async def test_failed_stop_request_keeps_planner_running() -> None:
    engine, runtime = _build_tool_engine(
        [
            ToolExecutionResult(
                tool_name="terminal_tool",
                success=False,
                error_message="执行失败",
                stop_after_execution=True,
            ),
            ToolExecutionResult(tool_name="following_tool", success=True, content="后续工具完成"),
        ]
    )

    should_pause, pause_reason, _, _ = await engine._handle_tool_calls(
        [
            ToolCall(call_id="call-1", func_name="terminal_tool"),
            ToolCall(call_id="call-2", func_name="following_tool"),
        ],
        "测试思考",
    )

    assert should_pause is False
    assert pause_reason == ""
    assert runtime._tool_registry.invoked_tool_names == ["terminal_tool", "following_tool"]
    runtime._end_planner_continuation.assert_not_called()
    runtime._enter_stop_state.assert_not_called()


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
            }
        ]
    )

    assert tools[0]["stop_after_execution"] is True


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
