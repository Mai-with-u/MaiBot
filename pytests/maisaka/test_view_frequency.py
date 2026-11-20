"""群聊连续思考、批次去重、定时与静默唤醒。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest

from src.maisaka.chat_loop_service import ChatResponse
from src.maisaka.reasoning_engine import CycleEnd, CycleRuntimeState, MaisakaReasoningEngine, TurnStartContext
from src.maisaka.runtime import MaisakaHeartFlowChatting
from src.maisaka.scenes.group.scheduling import GroupTurnScheduler
from src.maisaka.turn_trigger.gates import WaitTimeoutTrigger
from src.maisaka.turn_trigger.view_frequency import mean_message_interval, predict_message_interval


def test_interval_uses_receive_order_and_two_context_means() -> None:
    assert mean_message_interval([20, 0, 10, 10]) == 10
    assert mean_message_interval([10, 10]) is None
    assert predict_message_interval(10, 30) == 20
    assert predict_message_interval(10, None) == 10
    assert predict_message_interval(None, None) is None


def make_scheduler() -> GroupTurnScheduler:
    runtime = SimpleNamespace(
        message_cache=[SimpleNamespace(message_id="one", received_at=None)],
        _last_processed_index=0,
        log_prefix="test",
        _has_forced_turn_trigger=lambda: False,
        _fixed_message_debouncer=SimpleNamespace(consume=Mock()),
    )
    return GroupTurnScheduler(runtime)


def test_each_batch_is_sampled_once_and_new_batch_keeps_context(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = make_scheduler()
    runtime = scheduler._runtime
    monkeypatch.setattr(scheduler, "_arm_silence_check", Mock())
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.filter_external_messages", lambda messages, **kw: messages)
    draw = Mock(side_effect=[0.9, 0.1])
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", draw)
    assert scheduler._evaluate_scene(1, 0.5).decision == "wait"
    assert scheduler._evaluate_scene(1, 0.5).decision == "wait"
    draw.assert_called_once()
    runtime.message_cache.append(SimpleNamespace(message_id="two"))
    assert scheduler._evaluate_scene(2, 0.5).decision == "trigger"
    assert len(runtime.message_cache) == 2
    assert runtime._fixed_message_debouncer.consume.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_state", ["stop", "wait"])
async def test_silence_samples_once_until_next_message(monkeypatch: pytest.MonkeyPatch, agent_state: str) -> None:
    scheduler = make_scheduler()
    runtime = scheduler._runtime
    runtime._running = True
    runtime._last_message_received_at = 100.0
    runtime._focus_group = None
    runtime._agent_state = agent_state
    runtime._STATE_RUNNING = "running"
    runtime._STATE_WAIT = "wait"
    runtime._message_turn_scheduled = False
    runtime._is_reply_frequency_silent = lambda: False
    runtime._get_message_quiet_period_remaining_seconds = lambda: 0
    runtime._get_effective_reply_frequency = Mock(side_effect=[0.32, 0.6])
    runtime._enqueue_message_turn = Mock()
    runtime._pending_wait_started_at = 100.0
    runtime._cancel_wait_timeout_task = Mock()
    runtime._schedule_message_turn = Mock()
    monkeypatch.setattr(scheduler, "_context_interval", lambda: 10.0)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.time.time", lambda: 120.0)
    draw = Mock(return_value=0.5)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", draw)

    scheduler.observe_message()
    await scheduler._silence_task
    draw.assert_called_once()
    runtime._enqueue_message_turn.assert_not_called()
    runtime._cancel_wait_timeout_task.assert_not_called()
    assert not scheduler.consume_silence_trigger()
    scheduler.finish_planner_turn()
    assert scheduler._silence_task is None

    runtime._last_message_received_at = 110.0
    scheduler.observe_message()
    await scheduler._silence_task
    assert draw.call_count == 2
    runtime._enqueue_message_turn.assert_called_once()
    assert scheduler.consume_silence_trigger()
    assert not scheduler.consume_silence_trigger()
    scheduler.finish_planner_turn()
    assert scheduler._silence_task is None
    assert runtime._cancel_wait_timeout_task.call_count == int(agent_state == "wait")


@pytest.mark.asyncio
async def test_silence_task_is_cancelled_on_close() -> None:
    scheduler = make_scheduler()
    task = asyncio.create_task(asyncio.sleep(3600))
    scheduler._silence_task = task
    scheduler.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert scheduler._silence_task is None


def make_engine(monkeypatch: pytest.MonkeyPatch) -> MaisakaReasoningEngine:
    scheduler = make_scheduler()
    runtime = scheduler._runtime
    runtime._message_turn_scheduler = scheduler
    runtime._max_internal_rounds = 100
    runtime._max_context_size = 100
    runtime._chat_history = []
    runtime._last_processed_index = len(runtime.message_cache)
    runtime._get_effective_reply_frequency = lambda: 0.5
    runtime._is_focus_mode_active_for_current_chat = lambda: False
    runtime._chat_loop_service = SimpleNamespace(passive_replyer_override=None)
    runtime._running = True
    runtime._agent_state = "running"
    runtime._STATE_RUNNING = "running"
    runtime._STATE_WAIT = "wait"
    runtime.session_id = "test"
    runtime._monitor_run_id = "run"
    runtime._STATE_STOP = "stop"
    runtime._cancel_wait_timeout_task = Mock()
    runtime._reset_consecutive_wait_count = Mock()
    runtime._end_logical_turn = lambda: MaisakaHeartFlowChatting._end_logical_turn(runtime)
    runtime._enter_stop_state = Mock(side_effect=lambda: MaisakaHeartFlowChatting._enter_stop_state(runtime))
    runtime._prepare_tool_prompt_records = Mock(return_value={})
    runtime._render_context_usage_panel = Mock()
    engine = MaisakaReasoningEngine(runtime)
    engine._end_cycle = AsyncMock(return_value=SimpleNamespace(time_records={}))
    monkeypatch.setattr("src.maisaka.reasoning_engine.emit_planner_snapshot", AsyncMock())
    return engine


def planner_response() -> ChatResponse:
    return ChatResponse(
        output_items=(),
        request_messages=[],
        selected_history_count=0,
        tool_count=0,
        prompt_tokens=0,
        built_message_count=0,
        completion_tokens=0,
        total_tokens=0,
    )


@pytest.mark.parametrize("waiting", [False, True])
def test_message_after_thinking_uses_current_frequency(monkeypatch: pytest.MonkeyPatch, waiting: bool) -> None:
    runtime = make_engine(monkeypatch)._runtime
    scheduler = runtime._message_turn_scheduler
    runtime._get_effective_reply_frequency = lambda: 0.32
    runtime._get_pending_message_count = lambda: 1
    runtime._schedule_message_turn = scheduler.schedule_message_turn
    runtime._get_message_quiet_period_remaining_seconds = lambda: 0
    runtime._is_reply_frequency_silent = lambda: False
    runtime._message_turn_scheduled = False
    runtime._enqueue_message_turn = Mock()
    runtime._pending_wait_started_at = 10.0
    runtime._update_stage_status = Mock()
    runtime._last_processed_index = 0
    monkeypatch.setattr(scheduler, "_arm_silence_check", Mock())
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.filter_external_messages", lambda messages, **kw: messages)
    draw = Mock(side_effect=[0.5, 0.2])
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", draw)

    scheduler.start_planner_turn("first")
    scheduler.record_planner_run()
    scheduler.schedule_message_turn()
    draw.assert_not_called()

    if waiting:
        runtime._agent_state = runtime._STATE_WAIT
    else:
        runtime._enter_stop_state()
    scheduler.finish_planner_turn()
    draw.assert_called_once()
    runtime._enqueue_message_turn.assert_not_called()
    if waiting:
        runtime._cancel_wait_timeout_task.assert_not_called()

    runtime.message_cache.append(SimpleNamespace(message_id="two"))
    scheduler.schedule_message_turn()
    runtime._enqueue_message_turn.assert_called_once()
    if waiting:
        runtime._cancel_wait_timeout_task.assert_called_once()


@pytest.mark.parametrize("frequency,limit,debt", [(0.07, 8, 6), (0.499, 8, 0), (0.5, 16, 8), (1, 16, 0)])
@pytest.mark.asyncio
async def test_internal_loop_runs_to_frequency_limit(
    monkeypatch: pytest.MonkeyPatch, frequency: float, limit: int, debt: int,
) -> None:
    engine = make_engine(monkeypatch)
    runtime = engine._runtime
    scheduler = runtime._message_turn_scheduler
    runtime._get_effective_reply_frequency = lambda: frequency
    runtime._internal_turn_queue = asyncio.Queue()
    runtime._internal_turn_queue.put_nowait("message")
    runtime._consume_forced_turn_reason = lambda: None
    runtime._has_pending_messages = lambda: False
    runtime._start_planner_continuation = Mock()
    runtime._update_stage_status = Mock()
    engine._prepare_turn_start_context = AsyncMock(return_value=TurnStartContext([], object(), False, False, False))
    engine._start_cycle_round = AsyncMock(return_value=(SimpleNamespace(cycle_id=1, time_records={}), "round"))
    engine._refresh_visual_placeholders_for_cycle = AsyncMock()
    engine._post_process_chat_history_after_cycle = AsyncMock()
    engine._handle_planner_response_actions = AsyncMock(return_value=False)

    async def request(**kwargs) -> None:
        kwargs["state"].response = planner_response()

    engine._run_planner_request = AsyncMock(side_effect=request)
    monkeypatch.setattr(scheduler, "finish_planner_turn", lambda: setattr(runtime, "_running", False))
    await asyncio.wait_for(engine.run_loop(), timeout=2)
    assert engine._run_planner_request.await_count == limit
    assert scheduler._round_debt == debt
    runtime._enter_stop_state.assert_called_once()


def test_debt_carries_across_wait_and_frequency_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    runtime = engine._runtime
    scheduler = runtime._message_turn_scheduler
    runtime._get_effective_reply_frequency = lambda: 0.07
    assert scheduler.start_planner_turn("first") == 0
    for _ in range(4):
        scheduler.record_planner_run()
    runtime._get_effective_reply_frequency = lambda: 1.0
    assert scheduler.start_planner_turn("first") == 4
    assert runtime._max_internal_rounds == 8
    scheduler.record_planner_run()
    assert scheduler._round_debt == 3
    assert scheduler.start_planner_turn("next") == 0
    assert runtime._max_internal_rounds == 16
    runtime._last_processed_index = 0
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.filter_external_messages", lambda messages, **kw: messages)
    monkeypatch.setattr(scheduler, "_arm_silence_check", Mock())
    runtime._has_forced_turn_trigger = lambda: True
    assert scheduler._evaluate_scene(1, 1).should_trigger
    assert scheduler._round_debt == 3
    runtime._has_forced_turn_trigger = lambda: False
    for remaining in (2, 1, 0):
        runtime.message_cache.append(SimpleNamespace(message_id=str(remaining)))
        assert not scheduler._evaluate_scene(1, 1).should_trigger
        assert not scheduler._evaluate_scene(1, 1).should_trigger
        assert scheduler._round_debt == remaining
    assert scheduler.allows_timed_wakeup


@pytest.mark.parametrize(
    "frequency, draw, accepted", [(0.32, 0.31, True), (0.32, 0.32, False), (0, 0, False), (1, 0.99, True)]
)
@pytest.mark.asyncio
async def test_wait_timeout_samples_current_frequency_once(
    monkeypatch: pytest.MonkeyPatch, frequency: float, draw: float, accepted: bool,
) -> None:
    engine = make_engine(monkeypatch)
    runtime = engine._runtime
    runtime._internal_turn_queue = asyncio.Queue()
    runtime._has_pending_messages = lambda: False
    runtime._has_pending_wait_tool_call = lambda: runtime._pending_wait_tool_call_id is not None
    runtime._is_reply_frequency_silent = lambda: runtime._get_effective_reply_frequency() == 0
    runtime._enter_running_state = lambda: MaisakaHeartFlowChatting._enter_running_state(runtime)
    runtime._consume_pending_wait_state = lambda: MaisakaHeartFlowChatting._consume_pending_wait_state(runtime)
    runtime._mark_message_turn_unscheduled = Mock()
    runtime._cancel_deferred_message_turn_task = Mock()
    runtime._schedule_wait_timeout = lambda **kwargs: MaisakaHeartFlowChatting._schedule_wait_timeout(runtime, **kwargs)
    runtime._end_planner_continuation = Mock()
    runtime._update_stage_status = Mock()
    sample = Mock(return_value=draw)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", sample)

    MaisakaHeartFlowChatting._enter_wait_state(runtime, seconds=0, tool_call_id="timer", logical_turn_id="first")
    await runtime._wait_timeout_task
    timeout = runtime._internal_turn_queue.get_nowait()
    runtime._get_effective_reply_frequency = lambda: frequency
    assert (await engine._prepare_turn_start_context(WaitTimeoutTrigger("older"))).trigger_message is None
    sample.assert_not_called()
    context = await engine._prepare_turn_start_context(timeout)
    assert context.timeout_triggered is accepted
    assert context.trigger_message is (runtime.message_cache[-1] if accepted else None)
    assert runtime._agent_state == (runtime._STATE_RUNNING if accepted else runtime._STATE_STOP)
    assert runtime._internal_turn_queue.empty()
    assert not runtime._has_pending_wait_tool_call()
    assert runtime._chat_history[-1].tool_call_id == "timer"
    assert (await engine._prepare_turn_start_context(timeout)).trigger_message is None
    sample.assert_called_once()


@pytest.mark.asyncio
async def test_accepted_message_and_wait_timeout_share_wakeup(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    runtime = engine._runtime
    runtime._agent_state = runtime._STATE_WAIT
    runtime._pending_wait_started_at = 10.0
    runtime._message_turn_scheduler.accepted_wait_started_at = runtime._pending_wait_started_at
    runtime._pending_wait_tool_call_id = "timer"
    runtime._internal_turn_queue = asyncio.Queue()
    runtime._internal_turn_queue.put_nowait("message")
    runtime._is_reply_frequency_silent = lambda: False
    runtime._has_pending_messages = lambda: True
    runtime._has_pending_wait_tool_call = lambda: True
    runtime._wait_for_message_quiet_period = AsyncMock()
    runtime._mark_message_turn_unscheduled = Mock()
    runtime._collect_pending_messages = lambda: runtime.message_cache
    runtime._enter_running_state = Mock()
    runtime._update_stage_status = Mock()
    engine._build_wait_completed_message = Mock(return_value=SimpleNamespace(logical_turn_id="first"))
    engine._ingest_messages = AsyncMock()
    draw = Mock()
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", draw)

    context = await engine._prepare_turn_start_context(WaitTimeoutTrigger("timer"))
    assert context.trigger_message is runtime.message_cache[-1]
    engine._build_wait_completed_message.assert_called_once_with(has_new_messages=True)
    draw.assert_not_called()


@pytest.mark.asyncio
async def test_round_debt_blocks_silence_and_queued_wait_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    runtime = engine._runtime
    scheduler = runtime._message_turn_scheduler
    runtime._get_effective_reply_frequency = lambda: 0.0625
    runtime._has_pending_wait_tool_call = lambda: True
    runtime._pending_wait_tool_call_id = "waiting"
    runtime._schedule_message_turn = Mock()
    runtime._internal_turn_queue = asyncio.Queue()
    runtime._is_reply_frequency_silent = lambda: False
    runtime._agent_state = runtime._STATE_WAIT
    scheduler.start_planner_turn("waiting")
    scheduler.record_planner_run()
    scheduler.record_planner_run()
    scheduler.finish_planner_turn()
    assert scheduler._silence_task is None
    runtime._last_message_received_at = 1.0
    runtime._enqueue_message_turn = Mock()
    await scheduler._check_silence(0.01)
    runtime._enqueue_message_turn.assert_not_called()
    context = await engine._prepare_turn_start_context(WaitTimeoutTrigger("waiting"))
    assert context.trigger_message is None
    assert runtime._agent_state == runtime._STATE_WAIT
    assert scheduler._round_debt == 1
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.filter_external_messages", lambda messages, **kw: messages)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", lambda: 0.99)
    runtime._last_processed_index = 0
    assert not scheduler._evaluate_scene(1, 0.0625).should_trigger
    assert scheduler._round_debt == 1


@pytest.mark.asyncio
async def test_wait_on_final_group_round_closes_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    runtime = engine._runtime
    runtime.scene = SimpleNamespace(is_group=True)
    runtime._get_effective_reply_frequency = lambda: 0.07
    scheduler = runtime._message_turn_scheduler
    scheduler.start_planner_turn("waiting")
    for _ in range(7):
        scheduler.record_planner_run()
    runtime._has_pending_wait_tool_call = lambda: True
    runtime._has_pending_messages = lambda: False
    runtime._end_planner_continuation = Mock()
    engine._build_wait_completed_message = Mock()
    state = CycleRuntimeState(response=planner_response(), cycle_end=CycleEnd("tool_pause:wait", "wait"))
    result = await engine._finalize_cycle(cycle_detail=SimpleNamespace(cycle_id=1), round_index=7, state=state)
    assert result.reason == "max_rounds"
    assert scheduler._round_debt == 6
    assert runtime._chat_history == [engine._build_wait_completed_message.return_value]
    runtime._enter_stop_state.assert_called_once()




@pytest.mark.asyncio
async def test_interrupted_planner_does_not_consume_round(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    scheduler = engine._runtime._message_turn_scheduler
    scheduler.start_planner_turn("first")
    state = CycleRuntimeState(response=planner_response(), planner_interrupted=True)
    await engine._finalize_cycle(cycle_detail=SimpleNamespace(cycle_id=1), round_index=0, state=state)
    assert scheduler.start_planner_turn("first") == 0


@pytest.mark.asyncio
async def test_silence_reuses_context_without_pending_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    runtime = engine._runtime
    scheduler = runtime._message_turn_scheduler
    scheduler._silence_triggered = True
    runtime._agent_state = "stop"
    runtime._internal_turn_queue = asyncio.Queue()
    runtime._is_reply_frequency_silent = lambda: False
    runtime._has_pending_messages = lambda: False
    runtime._has_pending_wait_tool_call = lambda: False
    runtime._mark_message_turn_unscheduled = Mock()
    runtime._enter_running_state = Mock()
    engine._emit_flow_step = AsyncMock()
    original_cache = list(runtime.message_cache)
    context = await engine._prepare_turn_start_context("message")
    assert context.trigger_message is original_cache[-1]
    assert context.cached_messages == []
    assert runtime.message_cache == original_cache
    assert not scheduler._silence_triggered
    runtime._enter_running_state.assert_called_once()
