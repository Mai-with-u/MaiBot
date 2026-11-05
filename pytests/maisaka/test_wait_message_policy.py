from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.maisaka.builtin_tool.wait import handle_tool
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.scenes.group.scheduling import GroupTurnScheduler


@pytest.mark.parametrize("elapsed,draw,accepted", [(0,0,False),(50,0.079,True),(50,0.081,False),(100,0.239,True),(100,0.241,False)])
def test_group_wait_probability(monkeypatch, elapsed, draw, accepted):
    runtime = SimpleNamespace(_pending_wait_started_at=1000, _pending_wait_seconds=100, message_cache=[1])
    scheduler = GroupTurnScheduler(runtime)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.time.time", lambda: 1000+elapsed)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", lambda: draw)
    assert scheduler._resume_wait_on_message() is accepted
    assert scheduler._resume_wait_on_message() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [None, "wait", "stop"])
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


@pytest.mark.asyncio
async def test_wait_absolute_time_and_validation():
    runtime = SimpleNamespace(_enter_counted_wait_state=Mock(return_value=1))
    ctx = SimpleNamespace(runtime=runtime, engine=SimpleNamespace(active_logical_turn_id="turn"),
                          build_success_result=Mock())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
    invocation = SimpleNamespace(arguments={"until": deadline.isoformat()}, call_id="call", tool_name="wait")
    await handle_tool(ctx, invocation)
    assert 59 <= runtime._enter_counted_wait_state.call_args.kwargs["seconds"] <= 60
    invocation.arguments = {"seconds": 1, "until": deadline.isoformat()}
    with pytest.raises(ValueError):
        await handle_tool(ctx, invocation)
