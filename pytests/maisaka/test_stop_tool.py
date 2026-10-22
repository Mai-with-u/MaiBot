"""默认 stop 工具的可用性与收尾行为。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.common.i18n import use_locale
from src.core.tooling import ToolRegistry
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.builtin_tool.provider import MaisakaBuiltinToolProvider
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.mode_policy import is_idle_cycle_reason
from src.maisaka.reasoning_engine import CycleRuntimeState, MaisakaReasoningEngine


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
@pytest.mark.parametrize("prompt_name", ["maisaka_chat", "maisaka_chat_focus"])
def test_stop_prompt_is_always_available(monkeypatch: pytest.MonkeyPatch, locale: str, prompt_name: str) -> None:
    service = MaisakaChatLoopService(is_group_chat=True)
    monkeypatch.setattr(service, "_get_chat_prompt_name", lambda: prompt_name)
    with use_locale(locale):
        prompt = service._build_chat_system_prompt()

    stop_lines = [line for line in prompt.splitlines() if line.startswith("- stop()")]
    assert len(stop_lines) == 1
    assert "{stop_rule}" not in prompt


@pytest.mark.asyncio
async def test_stop_ends_thinking_like_no_tools() -> None:
    registry = ToolRegistry()
    runtime = SimpleNamespace(
        _tool_registry=registry,
        _chat_history=[],
        session_id="test-stop",
        chat_stream=SimpleNamespace(is_group_session=True, group_id="group", user_id="", platform="test"),
        is_action_tool_currently_available=lambda name: True,
        expand_message_id_aliases=lambda arguments: arguments,
        _update_stage_status=Mock(),
        _reset_consecutive_wait_count=Mock(),
        _end_planner_continuation=Mock(),
        _enter_stop_state=Mock(),
        log_prefix="[test]",
    )
    engine = MaisakaReasoningEngine(runtime)
    registry.register_provider(MaisakaBuiltinToolProvider(engine.build_builtin_tool_handlers()))
    engine._record_tool_execution_effects = AsyncMock()
    engine._append_tool_execution_result = Mock()
    engine._emit_planner_progress = AsyncMock()

    assert await registry.has_tool("stop")
    spec = await registry.get_tool_spec("stop")
    assert spec is not None
    assert spec.parameters_schema == {"type": "object", "properties": {}, "required": []}

    should_pause, reason, _, results = await engine._handle_tool_calls(
        [ToolCall(call_id="stop-1", func_name="stop", args={})],
        "已经没有需要继续的操作。",
        SimpleNamespace(),
        CycleRuntimeState(),
    )

    assert should_pause
    assert reason == "stop"
    assert results[0]["success"]
    assert engine._cycle_end_for_pause_tool(reason).reason == "planner_no_tool_end"
    assert is_idle_cycle_reason(engine._cycle_end_for_pause_tool(reason).reason)
    runtime._end_planner_continuation.assert_called_once_with()
    runtime._enter_stop_state.assert_called_once_with()
    runtime._reset_consecutive_wait_count.assert_any_call("planner_no_tool_end")
    assert runtime._chat_history == []
    engine._append_tool_execution_result.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["决定：调用 reply 回复。", "没有新消息，保持安静。"])
async def test_no_tool_response_ends_without_retry(content: str) -> None:
    runtime = SimpleNamespace(
        _chat_history=[],
        _end_planner_continuation=Mock(),
        _reset_consecutive_wait_count=Mock(),
        _enter_stop_state=Mock(),
        log_prefix="[test]",
    )
    engine = MaisakaReasoningEngine(runtime)
    engine._emit_planner_progress = AsyncMock()
    state = CycleRuntimeState()

    should_end = await engine._handle_planner_response_actions(
        response=SimpleNamespace(content=content, raw_messages=[], tool_calls=None),
        cycle_detail=SimpleNamespace(),
        state=state,
        planner_extra_lines=state.planner_extra_lines,
    )

    assert should_end
    assert state.cycle_end.reason == "planner_no_tool_end"
    assert state.planner_extra_lines == ["状态：已结束本轮思考"]
    assert runtime._chat_history == []
    runtime._end_planner_continuation.assert_called_once_with()
    runtime._enter_stop_state.assert_called_once_with()
