"""查看频率状态转换、批次去重及静默触发。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest

from src.maisaka.scenes.group.scheduling import GroupTurnScheduler
from src.maisaka.chat_loop_service import ChatResponse
from src.maisaka.reasoning_engine import CycleRuntimeState, MaisakaReasoningEngine, TurnStartContext
from src.maisaka.turn_trigger.view_frequency import (
    ViewFrequencyController,
    mean_message_interval,
    predict_message_interval,
)


@pytest.mark.parametrize("frequency,runs", [(0.3, 5), (0.5, 8), (0.7, 12), (1, 16)])
def test_decay_stops_at_budget_and_does_not_reset(frequency: float, runs: int) -> None:
    controller = ViewFrequencyController()
    assert controller.probability(frequency) == frequency
    controller.activate(frequency)
    for index in range(runs):
        assert controller.probability(frequency) == 1
        controller.activate(frequency)
        assert controller.record_planner_run() is (index == runs - 1)
    assert controller.phase == "recovery"
    assert controller.probability(frequency) == 0


def test_recovery_progresses_on_rejected_decisions_and_can_reactivate() -> None:
    controller = ViewFrequencyController()
    controller.activate(0.0625)
    assert controller.record_planner_run()
    for index in range(16):
        assert controller.probability(0.5) == pytest.approx(0.5 * index / 16)
        assert not controller.decide(0.5, 0.99)
    assert controller.phase == "idle"
    assert controller.probability(0.5) == 0.5
    assert controller.decide(0.5, 0.2)
    controller.activate(0.5)
    assert controller.internal_frequency == 0.5


def test_early_stop_retains_budget_and_zero_disables_sampling() -> None:
    controller = ViewFrequencyController()
    controller.activate(0.5)
    controller.record_planner_run()
    controller.activate(0.5)
    assert controller.internal_frequency == 7 / 16
    assert not controller.decide(0, 0)


def test_interval_uses_receive_order_and_two_context_means() -> None:
    assert mean_message_interval([20, 0, 10, 10]) == 10
    assert mean_message_interval([10, 10]) is None
    assert predict_message_interval(10, 30) == 20
    assert predict_message_interval(10, None) == 10
    assert predict_message_interval(None, None) is None


def make_scheduler() -> GroupTurnScheduler:
    runtime = SimpleNamespace(
        message_cache=[SimpleNamespace(message_id="one")],
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
async def test_silence_is_sampled_and_rejection_advances_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = make_scheduler()
    runtime = scheduler._runtime
    runtime._running = True
    runtime._last_message_received_at = 100.0
    runtime._focus_group = None
    runtime._agent_state = "stop"
    runtime._STATE_RUNNING = "running"
    runtime._STATE_WAIT = "wait"
    runtime._message_turn_scheduled = False
    runtime._is_reply_frequency_silent = lambda: False
    runtime._get_message_quiet_period_remaining_seconds = lambda: 0
    runtime._get_effective_reply_frequency = lambda: 0.5
    runtime._enqueue_message_turn = Mock()
    scheduler.view_frequency.activate(0.0625)
    scheduler.view_frequency.record_planner_run()
    sleeps = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.asyncio.sleep", sleep)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.time.time", lambda: 120.0)
    monkeypatch.setattr("src.maisaka.scenes.group.scheduling.random.random", lambda: 0.0)
    await scheduler._check_silence(10.0)
    assert len(sleeps) == 2  # 第一轮概率为 0；恢复后第二轮才允许放行。
    runtime._enqueue_message_turn.assert_called_once()
    assert scheduler.consume_silence_trigger()
    assert not scheduler.consume_silence_trigger()
    assert scheduler._silence_handled_at == 100.0


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
    runtime._enter_stop_state = Mock(side_effect=lambda: setattr(runtime, "_agent_state", "stop"))
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


@pytest.mark.asyncio
async def test_internal_loop_continues_after_frequency_zero_until_round_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    runtime = engine._runtime
    scheduler = runtime._message_turn_scheduler
    runtime._max_internal_rounds = 12
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
    assert engine._run_planner_request.await_count == 12
    assert scheduler.view_frequency.phase == "recovery"
    assert scheduler.view_frequency.internal_frequency == 0
    runtime._enter_stop_state.assert_called_once()


def test_continued_planner_runs_do_not_reset_recovery() -> None:
    controller = ViewFrequencyController()
    controller.activate(0.0625)
    assert controller.record_planner_run()
    assert not controller.decide(0.5, 0.9)
    assert controller.recovery_rounds == 1
    assert not controller.record_planner_run()
    assert controller.recovery_rounds == 1
    assert controller.internal_frequency == 0.5 / 16


@pytest.mark.asyncio
async def test_interrupted_planner_does_not_consume_frequency(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    controller = engine._runtime._message_turn_scheduler.view_frequency
    controller.activate(0.5)
    state = CycleRuntimeState(response=planner_response(), planner_interrupted=True)
    await engine._finalize_cycle(cycle_detail=SimpleNamespace(cycle_id=1), round_index=0, state=state)
    assert controller.internal_frequency == 0.5


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
