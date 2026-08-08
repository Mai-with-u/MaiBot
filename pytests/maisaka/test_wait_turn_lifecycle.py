from datetime import datetime

import asyncio
import time

import pytest

from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting


def _build_runtime_stub() -> MaisakaHeartFlowChatting:
    runtime = object.__new__(MaisakaHeartFlowChatting)
    runtime._agent_state = runtime._STATE_RUNNING
    runtime._pending_wait_tool_call_id = None
    runtime._pending_wait_logical_turn_id = None
    runtime._pending_wait_started_at = None
    runtime._pending_wait_seconds = None
    runtime._wait_timeout_task = None
    runtime._proactive_trigger_message = None
    runtime._proactive_logical_turn_id = None
    runtime._internal_turn_queue = asyncio.Queue()
    runtime._mark_message_turn_unscheduled = lambda: None
    runtime._cancel_deferred_message_turn_task = lambda: None
    return runtime


def test_wait_pause_resume_completes_original_logical_turn() -> None:
    runtime = _build_runtime_stub()
    runtime._enter_wait_state(
        seconds=None,
        tool_call_id="wait-call-1",
        logical_turn_id="turn-wait-1",
    )
    runtime._pending_wait_started_at = time.time() - 1

    assert runtime._get_pending_wait_tool_call_ids() == {"wait-call-1"}
    runtime._enter_running_state()
    engine = object.__new__(MaisakaReasoningEngine)
    engine._runtime = runtime
    completed = engine._build_wait_completed_message(has_new_messages=True)

    assert completed.tool_call_id == "wait-call-1"
    assert completed.logical_turn_id == "turn-wait-1"
    assert completed.timestamp <= datetime.now()
    assert runtime._get_pending_wait_tool_call_ids() == set()


def test_wait_requires_registered_call_and_turn() -> None:
    runtime = _build_runtime_stub()
    with pytest.raises(ValueError, match="tool_call_id 和 logical_turn_id"):
        runtime._enter_wait_state(tool_call_id="wait-call-1", logical_turn_id=None)
    with pytest.raises(RuntimeError, match="不存在可补齐"):
        runtime._consume_pending_wait_state()


def test_proactive_switch_trigger_carries_source_logical_turn() -> None:
    runtime = _build_runtime_stub()
    runtime._agent_state = runtime._STATE_WAIT
    trigger = object()

    runtime._queue_proactive_turn(trigger, logical_turn_id="turn-switch-1")  # type: ignore[arg-type]

    assert runtime._agent_state == runtime._STATE_RUNNING
    assert runtime._consume_proactive_trigger_message() is trigger
    assert runtime._consume_proactive_logical_turn_id() == "turn-switch-1"
    assert runtime._internal_turn_queue.get_nowait() == "proactive"
