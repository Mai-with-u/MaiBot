from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.debounce import MessageDebouncer
from src.core.tooling import ToolAvailabilityContext
from src.maisaka.builtin_tool import get_builtin_tools
from src.maisaka.builtin_tool.wait import handle_tool
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.scenes.group.scheduling import GroupTurnScheduler


@pytest.mark.parametrize("draw,accepted", [(0.1, True), (0.9, False)])
def test_group_wait_uses_view_frequency(monkeypatch, draw, accepted):
    messages = [SimpleNamespace(message_id="one")]
    runtime = SimpleNamespace(
        _get_pending_message_count=lambda: len(messages),
        _get_effective_reply_frequency=lambda: 0.5,
        _has_forced_turn_trigger=lambda: False,
        message_cache=messages,
        _last_processed_index=0,
        log_prefix="test",
        _fixed_message_debouncer=MessageDebouncer(),
    )
    scheduler = GroupTurnScheduler(runtime)
    monkeypatch.setattr(scheduler, "_arm_silence_check", Mock())
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.filter_external_messages", lambda messages, **kw: messages)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", lambda: draw)
    assert scheduler._resume_wait_on_message() is accepted
    assert not scheduler._resume_wait_on_message()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [None, "wait"])
async def test_stale_wait_decision_is_not_executed(tool):
    engine = object.__new__(MaisakaReasoningEngine)
    engine._turn_passive_replyer = False
    engine._active_logical_turn_id = "turn"
    runtime = SimpleNamespace(_has_pending_messages=lambda: True,
                             _wait_for_message_quiet_period=AsyncMock(), _chat_history=[])
    engine._runtime = runtime
    response = SimpleNamespace(tool_calls=[] if tool is None else [SimpleNamespace(func_name=tool)])
    state = SimpleNamespace()
    assert not await engine._handle_planner_response_actions(response=response, cycle_detail=None,
                                                            state=state, planner_extra_lines=[])
    runtime._wait_for_message_quiet_period.assert_awaited_once()
    assert state.cycle_end.reason == "tool_continue"
    assert runtime._chat_history


@pytest.mark.parametrize("is_group_chat", [False, True])
def test_wait_replaces_stop_in_ordinary_chat(is_group_chat: bool) -> None:
    context = ToolAvailabilityContext(is_group_chat=is_group_chat)
    names = {tool["name"] for tool in get_builtin_tools(context)}
    assert {"reply", "wait"} <= names
    assert "stop" not in names


@pytest.mark.asyncio
async def test_untimed_wait_ends_ordinary_planner_turn() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._turn_passive_replyer = False
    runtime = SimpleNamespace(
        _has_pending_messages=lambda: False,
        _end_planner_continuation=Mock(),
        _reset_consecutive_wait_count=Mock(),
        _enter_stop_state=Mock(),
        _chat_history=[],
    )
    engine._runtime = runtime
    engine._emit_planner_progress = AsyncMock()
    response = SimpleNamespace(tool_calls=[SimpleNamespace(func_name="wait", args={})])
    state = SimpleNamespace()

    assert await engine._handle_planner_response_actions(
        response=response, cycle_detail=None, state=state, planner_extra_lines=[]
    )
    runtime._enter_stop_state.assert_called_once_with()
    assert state.cycle_end == engine._cycle_end_for_pause_tool("wait:end")
    assert runtime._chat_history == []


@pytest.mark.asyncio
async def test_wait_seconds_and_validation():
    runtime = SimpleNamespace(_enter_counted_wait_state=Mock(return_value=1))
    ctx = SimpleNamespace(runtime=runtime, engine=SimpleNamespace(active_logical_turn_id="turn"),
                          build_success_result=Mock())
    invocation = SimpleNamespace(arguments={"seconds": 60}, call_id="call", tool_name="wait")
    await handle_tool(ctx, invocation)
    assert runtime._enter_counted_wait_state.call_args.kwargs["seconds"] == 60
    invocation.arguments = {"seconds": -1}
    with pytest.raises(ValueError):
        await handle_tool(ctx, invocation)
