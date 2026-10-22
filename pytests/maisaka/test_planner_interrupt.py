from types import SimpleNamespace
import asyncio

import pytest

from src.maisaka import runtime as runtime_module
from src.maisaka.runtime import MaisakaHeartFlowChatting, PlannerInterruptController


def _runtime(monkeypatch, *, steering: bool) -> MaisakaHeartFlowChatting:
    monkeypatch.setattr(
        runtime_module,
        "global_config",
        SimpleNamespace(chat=SimpleNamespace(reply_timing=SimpleNamespace(planner_message_steering=steering))),
    )
    instance = object.__new__(MaisakaHeartFlowChatting)
    instance._agent_state = MaisakaHeartFlowChatting._STATE_RUNNING
    instance._planner_interrupt = PlannerInterruptController()
    instance.log_prefix = "[test]"
    instance.message_cache = []
    return instance


@pytest.mark.parametrize("steering", [False, True])
def test_only_quick_reaction_mode_interrupts_planner(monkeypatch, steering: bool) -> None:
    runtime = _runtime(monkeypatch, steering=steering)
    interrupt_flag = asyncio.Event()
    runtime._planner_interrupt.bind(interrupt_flag)

    runtime._request_planner_interrupt_for_message(SimpleNamespace(message_id="m1"))

    assert interrupt_flag.is_set() is steering


def test_same_planner_request_is_interrupted_once() -> None:
    controller = PlannerInterruptController()
    assert controller.request() == "idle"

    interrupt_flag = asyncio.Event()
    controller.bind(interrupt_flag)
    assert controller.request() == "request"
    assert controller.request() == "duplicate"

    controller.unbind(interrupt_flag)
    assert controller.request() == "idle"
