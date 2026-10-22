from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.maisaka.turn_trigger import scheduler as scheduler_module
from src.maisaka.turn_trigger.scheduler import MessageTurnScheduler


def _build_scheduler(
    monkeypatch,
    *,
    quiet_seconds: float = 0.0,
    backoff_seconds: float = 0.0,
    forced: bool = False,
    agent_state: str = "running",
    steering: bool = False,
) -> tuple[MessageTurnScheduler, SimpleNamespace]:
    monkeypatch.setattr(
        scheduler_module,
        "global_config",
        SimpleNamespace(chat=SimpleNamespace(reply_timing=SimpleNamespace(planner_message_steering=steering))),
    )
    runtime = SimpleNamespace(
        _agent_state=agent_state,
        _STATE_WAIT="wait",
        _message_turn_scheduled=False,
        chat_stream=SimpleNamespace(is_group_session=True),
        _get_pending_message_count=lambda: 1,
        _get_effective_reply_frequency=lambda: 0.4,
        _is_reply_frequency_silent=lambda: False,
        _has_forced_turn_trigger=lambda: forced,
        _idle_backoff=SimpleNamespace(get_delay_seconds=lambda pending_count: backoff_seconds),
        _get_message_quiet_period_remaining_seconds=lambda: quiet_seconds,
        _cancel_wait_timeout_task=Mock(),
        _update_stage_status=Mock(),
        _defer_message_turn_check=Mock(),
        _enqueue_message_turn=Mock(),
        message_cache=[],
        _last_processed_index=0,
        log_prefix="[test]",
    )
    scheduler = MessageTurnScheduler(runtime)
    scheduler._dynamic_reply_gate = Mock()
    scheduler._dynamic_reply_gate.evaluate.return_value = SimpleNamespace(decision="trigger", detail="")
    return scheduler, runtime


@pytest.mark.parametrize("forced", [False, True])
def test_every_path_waits_for_debounce_before_judging(monkeypatch, forced: bool) -> None:
    scheduler, runtime = _build_scheduler(monkeypatch, quiet_seconds=1.5, forced=forced)

    scheduler.schedule_message_turn()

    runtime._defer_message_turn_check.assert_called_once_with(1.5)
    scheduler._dynamic_reply_gate.evaluate.assert_not_called()
    scheduler._dynamic_reply_gate.record_forced_turn.assert_not_called()
    runtime._enqueue_message_turn.assert_not_called()


def test_dynamic_gate_evaluates_after_debounce_deadline(monkeypatch) -> None:
    scheduler, runtime = _build_scheduler(monkeypatch)

    scheduler.schedule_message_turn()

    runtime._defer_message_turn_check.assert_not_called()
    scheduler._dynamic_reply_gate.evaluate.assert_called_once()
    runtime._enqueue_message_turn.assert_called_once()


def test_forced_turn_bypasses_idle_backoff(monkeypatch) -> None:
    scheduler, runtime = _build_scheduler(monkeypatch, backoff_seconds=30.0, forced=True)

    scheduler.schedule_message_turn()

    scheduler._dynamic_reply_gate.record_forced_turn.assert_called_once()
    runtime._defer_message_turn_check.assert_not_called()
    runtime._enqueue_message_turn.assert_called_once()


def test_idle_backoff_defers_before_dynamic_gate(monkeypatch) -> None:
    scheduler, runtime = _build_scheduler(monkeypatch, backoff_seconds=30.0)

    scheduler.schedule_message_turn()

    runtime._defer_message_turn_check.assert_called_once_with(30.0)
    scheduler._dynamic_reply_gate.evaluate.assert_not_called()
    runtime._enqueue_message_turn.assert_not_called()


def test_steering_wait_resumes_only_after_debounce(monkeypatch) -> None:
    scheduler, runtime = _build_scheduler(monkeypatch, quiet_seconds=1.0, agent_state="wait", steering=True)

    scheduler.schedule_message_turn()

    runtime._cancel_wait_timeout_task.assert_not_called()
    runtime._defer_message_turn_check.assert_called_once_with(1.0)

    runtime._get_message_quiet_period_remaining_seconds = lambda: 0.0
    scheduler.schedule_message_turn()

    runtime._cancel_wait_timeout_task.assert_called_once()
    scheduler._dynamic_reply_gate.evaluate.assert_not_called()
    runtime._enqueue_message_turn.assert_called_once()
