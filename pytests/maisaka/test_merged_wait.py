"""统一等待工具的终止、定时续接和回复后控制。"""

from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, Mock

from jsonschema import ValidationError, validate

import pytest

from src.core.tooling import ToolExecutionResult
from src.maisaka.builtin_tool.reply import get_tool_spec as reply_spec
from src.maisaka.builtin_tool.reply import handle_tool as handle_reply
from src.maisaka.builtin_tool.wait import get_tool_spec as wait_spec
from src.maisaka.builtin_tool.wait import handle_tool as handle_wait
from src.maisaka.builtin_tool.wait import resolve_wait_seconds
from src.maisaka.focus.group_runtime import FocusGroupRuntime
from src.maisaka.reasoning_engine import CycleRuntimeState, MaisakaReasoningEngine


def make_context() -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(_enter_counted_wait_state=Mock(return_value=1)),
        engine=SimpleNamespace(active_logical_turn_id="turn", _end_planner_no_tool_cycle=Mock()),
        build_success_result=lambda name, content, metadata: ToolExecutionResult(
            tool_name=name, success=True, content=content, metadata=metadata,
        ),
        build_failure_result=lambda name, error: ToolExecutionResult(
            tool_name=name, success=False, error_message=error,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [None, 0, 60])
async def test_wait_without_time_ends_but_zero_schedules(seconds: Optional[int]) -> None:
    ctx = make_context()
    arguments = {} if seconds is None else {"seconds": seconds}
    result = await handle_wait(ctx, SimpleNamespace(arguments=arguments, tool_name="wait", call_id="call"))
    assert result.success and result.metadata["pause_execution"]
    if seconds is None:
        ctx.engine._end_planner_no_tool_cycle.assert_called_once()
        ctx.runtime._enter_counted_wait_state.assert_not_called()
    else:
        ctx.engine._end_planner_no_tool_cycle.assert_not_called()
        assert ctx.runtime._enter_counted_wait_state.call_args.kwargs["seconds"] == seconds


@pytest.mark.parametrize("arguments", [{"seconds": -1}, {"seconds": True}, {"seconds": None}])
def test_invalid_wait_time_is_rejected(arguments: Dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        resolve_wait_seconds(arguments)


def test_wait_schema_only_allows_optional_seconds() -> None:
    validate({}, wait_spec().parameters_schema)
    validate({"seconds": 0}, wait_spec().parameters_schema)
    with pytest.raises(ValidationError):
        validate({"seconds": 1, "until": "2026-11-13T20:00:00+08:00"}, wait_spec().parameters_schema)
    assert set(wait_spec().parameters_schema["properties"]) == {"seconds"}
    assert "wait_until" not in reply_spec().parameters_schema["properties"]
    with pytest.raises(ValidationError):
        validate({"until": "2026-11-13T20:00:00+08:00"}, wait_spec().parameters_schema)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments,expected_action,expected_seconds", [
    ({}, "wait", None),
    ({"after_reply": "wait"}, "wait", None),
    ({"after_reply": "wait", "wait_seconds": 0}, "wait", 0),
    ({"wait_seconds": 60}, "wait", 60),
    ({"after_reply": "continue"}, "continue", None),
])
async def test_reply_pause_defaults_and_time(
    monkeypatch: pytest.MonkeyPatch, arguments: Dict[str, Any], expected_action: str, expected_seconds: Optional[int],
) -> None:
    execute = AsyncMock(return_value=ToolExecutionResult(tool_name="reply", success=True))
    monkeypatch.setattr("src.maisaka.builtin_tool.reply.execute_reply", execute)
    result = await handle_reply(make_context(), SimpleNamespace(arguments=arguments, call_id="reply", tool_name="reply"))
    execute.assert_awaited_once()
    assert result.metadata["after_reply"] == expected_action
    assert result.metadata.get("wait_seconds") == expected_seconds


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"after_reply": "stop"},
    {"wait_seconds": 1, "wait_until": "2026-11-13T20:00:00+08:00"},
    {"after_reply": "continue", "wait_seconds": 0},
    {"wait_until": "invalid-date"},
])
async def test_invalid_reply_control_does_not_send(monkeypatch: pytest.MonkeyPatch, arguments: Dict[str, Any]) -> None:
    execute = AsyncMock()
    monkeypatch.setattr("src.maisaka.builtin_tool.reply.execute_reply", execute)
    result = await handle_reply(make_context(), SimpleNamespace(arguments=arguments, call_id="reply", tool_name="reply"))
    assert not result.success
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [None, 0, 60])
async def test_focus_wait_controls_group_without_pausing_member(seconds: Optional[int]) -> None:
    ctx = make_context()
    result = await handle_wait(ctx, SimpleNamespace(
        arguments={} if seconds is None else {"seconds": seconds}, call_id="call", tool_name="wait",
    ), SimpleNamespace(is_focus=True))
    ctx.runtime._enter_counted_wait_state.assert_not_called()
    ctx.engine._end_planner_no_tool_cycle.assert_not_called()
    group = object.__new__(FocusGroupRuntime)
    group._consecutive_waits = 0
    group._wait_until = None
    group._wait_session_id = None
    group._pending = []
    group._apply_wait_control(result, "session")
    assert result.metadata["pause_execution"]
    assert (group._wait_until is None) == (seconds is None)
    assert group._wait_session_id == (None if seconds is None else "session")


@pytest.mark.parametrize("seconds", [None, 0, 60])
def test_focus_reply_uses_same_pause_policy(seconds: Optional[int]) -> None:
    group = object.__new__(FocusGroupRuntime)
    group._consecutive_waits = 0
    group._wait_until = None
    group._wait_session_id = None
    group._pending = []
    result = ToolExecutionResult(
        tool_name="reply", success=True, metadata={"after_reply": "wait", "wait_seconds": seconds},
    )
    group._apply_reply_control(result, "session")
    assert result.metadata["pause_execution"]
    assert (group._wait_until is None) == (seconds is None)


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds,success", [(None, True), (0, True), (60, True), (None, False)])
async def test_ordinary_reply_pause_cancels_later_tools_and_keeps_failures_running(
    monkeypatch: pytest.MonkeyPatch, seconds: Optional[int], success: bool,
) -> None:
    monkeypatch.setattr("src.maisaka.reasoning_engine.global_config.capabilities.tool_result_condense", False)
    reply_result = ToolExecutionResult(
        tool_name="reply", success=success,
        metadata={"after_reply": "wait", "wait_seconds": seconds} if success else {},
    )
    invoke = AsyncMock(side_effect=[reply_result, ToolExecutionResult(tool_name="query_memory", success=True)])
    runtime = SimpleNamespace(
        _tool_registry=SimpleNamespace(list_tools=AsyncMock(return_value=[]), invoke=invoke),
        _update_stage_status=Mock(), _reset_consecutive_wait_count=Mock(),
        _enter_counted_wait_state=Mock(), _enter_stop_state=Mock(), _end_planner_continuation=Mock(),
        is_action_tool_currently_available=lambda _: True, log_prefix="test",
    )
    engine = MaisakaReasoningEngine(runtime)
    engine._turn_passive_replyer = False
    engine._active_logical_turn_id = "turn"
    engine._build_tool_execution_context = Mock()
    engine._build_tool_availability_context = Mock()
    engine._log_tool_call_source = Mock()
    engine._build_tool_invocation = lambda call, _: SimpleNamespace(tool_name=call.func_name)
    engine._record_tool_execution_effects = AsyncMock()
    engine._append_tool_execution_result = Mock()
    engine._append_tool_post_history_messages = Mock()
    engine._emit_planner_progress = AsyncMock()

    async def display(**kwargs: Any) -> None:
        kwargs["tool_monitor_results"].append({})

    engine._append_tool_display_results = display
    calls = [SimpleNamespace(func_name=name, call_id=name) for name in ("reply", "query_memory")]
    paused, reason, _, _ = await engine._handle_tool_calls(calls, "test", SimpleNamespace(), CycleRuntimeState())
    assert paused is success
    assert invoke.await_count == (1 if success else 2)
    if success and seconds is None:
        assert reason == "reply:wait:end"
        runtime._enter_stop_state.assert_called_once()
        runtime._enter_counted_wait_state.assert_not_called()
    elif success:
        assert reason == "reply:wait"
        runtime._enter_stop_state.assert_not_called()
        assert runtime._enter_counted_wait_state.call_args.kwargs["seconds"] == seconds
    else:
        runtime._enter_stop_state.assert_not_called()
        runtime._enter_counted_wait_state.assert_not_called()
