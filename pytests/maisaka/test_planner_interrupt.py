from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest

from src.llm_models.exceptions import ReqAbortException
from src.maisaka import reasoning_engine, runtime as runtime_module
from src.maisaka.runtime import MaisakaHeartFlowChatting, PlannerInterruptController


def _runtime(monkeypatch, *, steering: bool, is_group: bool = True) -> MaisakaHeartFlowChatting:
    monkeypatch.setattr(
        runtime_module,
        "global_config",
        SimpleNamespace(chat=SimpleNamespace(reply_timing=SimpleNamespace(planner_message_steering=steering))),
    )
    instance = object.__new__(MaisakaHeartFlowChatting)
    instance.chat_stream = SimpleNamespace(is_group_session=is_group)
    instance._agent_state = MaisakaHeartFlowChatting._STATE_RUNNING
    instance._planner_interrupt = PlannerInterruptController()
    instance.log_prefix = "[test]"
    instance.message_cache = []
    return instance


@pytest.mark.parametrize("steering", [False, True])
@pytest.mark.parametrize("is_group", [False, True])
def test_private_always_interrupts_and_group_respects_quick_reaction_mode(
    monkeypatch, steering: bool, is_group: bool
) -> None:
    runtime = _runtime(monkeypatch, steering=steering, is_group=is_group)
    interrupt_flag = asyncio.Event()
    runtime._planner_interrupt.bind(interrupt_flag)

    runtime._request_planner_interrupt_for_message(SimpleNamespace(message_id="m1"))

    assert interrupt_flag.is_set() is (not is_group or steering)


@pytest.mark.asyncio
@pytest.mark.parametrize("steering", [False, True])
@pytest.mark.parametrize("is_group", [False, True])
async def test_pending_messages_interrupt_before_planner_starts(
    monkeypatch, steering: bool, is_group: bool
) -> None:
    runtime = _runtime(monkeypatch, steering=steering, is_group=is_group)
    monkeypatch.setattr(reasoning_engine, "global_config", runtime_module.global_config)
    runtime._has_pending_messages = lambda: True
    runtime._chat_history = []
    monkeypatch.setattr(MaisakaHeartFlowChatting, "_max_context_size", property(lambda self: 1000))
    runtime._chat_loop_service = SimpleNamespace(
        set_interrupt_flag=Mock(), chat_loop_step=AsyncMock(return_value="response")
    )
    engine = reasoning_engine.MaisakaReasoningEngine(runtime)

    if not is_group or steering:
        with pytest.raises(ReqAbortException):
            await engine._run_interruptible_planner()
        runtime._chat_loop_service.chat_loop_step.assert_not_awaited()
    else:
        assert await engine._run_interruptible_planner() == "response"

    assert runtime._planner_interrupt.request() == "idle"
    runtime._chat_loop_service.set_interrupt_flag.assert_called_with(None)


def test_same_planner_request_is_interrupted_once() -> None:
    controller = PlannerInterruptController()
    assert controller.request() == "idle"

    interrupt_flag = asyncio.Event()
    controller.bind(interrupt_flag)
    assert controller.request() == "request"
    assert controller.request() == "duplicate"

    controller.unbind(interrupt_flag)
    assert controller.request() == "idle"
