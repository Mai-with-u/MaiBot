"""TurnPolicy.decide 的决策契约。

输入与 `MessageTurnScheduler.schedule_message_turn` 在确认存在待处理消息后
构造的 `TurnSnapshot` 一致。测试不构造、不调用真实 runtime。
"""

from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock

import pytest

from src.maisaka.turn_gates import TurnGateResult

turn_policy_module = pytest.importorskip("src.maisaka.turn_policy")
TurnPolicy = turn_policy_module.TurnPolicy
TurnSnapshot = turn_policy_module.TurnSnapshot


def _scheduler_snapshot(**overrides: object) -> object:
    """构造 MessageTurnScheduler 会交给 TurnPolicy.decide 的同一份快照。"""

    payload = {
        "pending_count": 2,
        "pending_messages": (),
        "effective_frequency": 0.5,
        "is_silent": False,
        "has_forced_trigger": False,
        "trigger_threshold": 4,
        "log_prefix": "[test]",
    }
    payload.update(overrides)
    return TurnSnapshot(**payload)


def _build_policy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    backoff_delay: Optional[float] = None,
    necessity_enabled: bool = False,
    necessity_trigger: bool = False,
    frequency_decision: str = "wait",
    frequency_delay: Optional[float] = None,
) -> tuple[object, Mock, Mock, Mock]:
    """用调度器同款快照依赖构造 TurnPolicy，门控与退避均替换为可观察桩。"""

    should_delay = Mock(return_value=backoff_delay)
    necessity_evaluate = Mock(
        return_value=TurnGateResult(
            decision="trigger" if necessity_trigger else "wait",
            detail="必要性门控",
            pressure_score=10,
        )
    )
    frequency_evaluate = Mock(
        return_value=TurnGateResult(
            decision=frequency_decision,  # type: ignore[arg-type]
            detail="频率门控",
            delay_seconds=frequency_delay,
        )
    )
    monkeypatch.setattr(
        turn_policy_module,
        "ReplyNecessityTurnGate",
        Mock(return_value=SimpleNamespace(evaluate=necessity_evaluate)),
    )
    monkeypatch.setattr(
        turn_policy_module,
        "FrequencyThresholdTurnGate",
        Mock(return_value=SimpleNamespace(evaluate=frequency_evaluate)),
    )
    monkeypatch.setattr(
        turn_policy_module,
        "is_reply_necessity_trigger_enabled",
        lambda: necessity_enabled,
    )
    runtime = SimpleNamespace(
        _idle_backoff=SimpleNamespace(should_delay=should_delay),
        log_prefix="[test]",
    )
    return TurnPolicy(runtime), should_delay, necessity_evaluate, frequency_evaluate


def test_静默频率会静默消费并优先于强制触发与门控(monkeypatch: pytest.MonkeyPatch) -> None:
    """频率为 0 时调度器仍会投递 turn 做静默消费，且不再看强制触发、退避或 XOR 门控。"""

    policy, should_delay, necessity_evaluate, frequency_evaluate = _build_policy(
        monkeypatch,
        backoff_delay=9.0,
        necessity_enabled=True,
        necessity_trigger=False,
        frequency_decision="trigger",
        frequency_delay=2.0,
    )

    decision = policy.decide(
        _scheduler_snapshot(
            is_silent=True,
            effective_frequency=0.0,
            has_forced_trigger=True,
        )
    )

    assert decision.action == "enqueue"
    should_delay.assert_not_called()
    necessity_evaluate.assert_not_called()
    frequency_evaluate.assert_not_called()


def test_强制触发会绕过空闲退避与频率必要性门控(monkeypatch: pytest.MonkeyPatch) -> None:
    """@/提及强制触发时调度器直接 enqueue，即使退避中或两个门控都不触发。"""

    policy, should_delay, necessity_evaluate, frequency_evaluate = _build_policy(
        monkeypatch,
        backoff_delay=4.0,
        necessity_enabled=False,
        necessity_trigger=False,
        frequency_decision="wait",
        frequency_delay=4.0,
    )

    decision = policy.decide(_scheduler_snapshot(has_forced_trigger=True))

    assert decision.action == "enqueue"
    should_delay.assert_not_called()
    necessity_evaluate.assert_not_called()
    frequency_evaluate.assert_not_called()


def test_空闲退避会延迟且不进入planner(monkeypatch: pytest.MonkeyPatch) -> None:
    """调度器在 silent/forced 之后检查 idle backoff；命中则 delay，不再评估 XOR 门控。"""

    policy, should_delay, necessity_evaluate, frequency_evaluate = _build_policy(
        monkeypatch,
        backoff_delay=2.5,
        necessity_enabled=True,
        necessity_trigger=True,
        frequency_decision="trigger",
        frequency_delay=3.0,
    )
    snapshot = _scheduler_snapshot()

    decision = policy.decide(snapshot)

    assert decision.action == "delay"
    assert decision.delay_seconds == 2.5
    should_delay.assert_called_once_with(snapshot.pending_count)
    necessity_evaluate.assert_not_called()
    frequency_evaluate.assert_not_called()


def test_必要性模式不会走频率门控(monkeypatch: pytest.MonkeyPatch) -> None:
    """reply_trigger_mode=reply_necessity 时只看必要性门，频率触发或 delay 都必须被忽略。"""

    wait_policy, _, wait_necessity, wait_frequency = _build_policy(
        monkeypatch,
        necessity_enabled=True,
        necessity_trigger=False,
        frequency_decision="trigger",
        frequency_delay=1.5,
    )
    ignored_frequency = wait_policy.decide(_scheduler_snapshot())

    trigger_policy, _, trigger_necessity, trigger_frequency = _build_policy(
        monkeypatch,
        necessity_enabled=True,
        necessity_trigger=True,
        frequency_decision="delay",
        frequency_delay=1.5,
    )
    necessity_trigger = trigger_policy.decide(_scheduler_snapshot())

    assert ignored_frequency.action == "wait"
    assert ignored_frequency.delay_seconds is None
    wait_necessity.assert_called_once_with(pending_messages=(), trigger_threshold=4)
    wait_frequency.assert_not_called()

    assert necessity_trigger.action == "enqueue"
    trigger_necessity.assert_called_once_with(pending_messages=(), trigger_threshold=4)
    trigger_frequency.assert_not_called()


def test_频率模式不会走必要性门控(monkeypatch: pytest.MonkeyPatch) -> None:
    """reply_trigger_mode=frequency 时只看频率门，必要性评分不得改写 enqueue/wait/delay。"""

    wait_policy, _, wait_necessity, wait_frequency = _build_policy(
        monkeypatch,
        necessity_enabled=False,
        necessity_trigger=True,
        frequency_decision="wait",
    )
    ignored_necessity = wait_policy.decide(_scheduler_snapshot())

    trigger_policy, _, trigger_necessity, trigger_frequency = _build_policy(
        monkeypatch,
        necessity_enabled=False,
        necessity_trigger=False,
        frequency_decision="trigger",
    )
    frequency_trigger = trigger_policy.decide(_scheduler_snapshot())

    delay_policy, _, delay_necessity, delay_frequency = _build_policy(
        monkeypatch,
        necessity_enabled=False,
        necessity_trigger=True,
        frequency_decision="delay",
        frequency_delay=1.5,
    )
    frequency_delay = delay_policy.decide(_scheduler_snapshot())

    assert ignored_necessity.action == "wait"
    wait_necessity.assert_not_called()
    wait_frequency.assert_called_once_with(pending_count=2, trigger_threshold=4)

    assert frequency_trigger.action == "enqueue"
    trigger_necessity.assert_not_called()
    trigger_frequency.assert_called_once_with(pending_count=2, trigger_threshold=4)

    assert frequency_delay.action == "delay"
    assert frequency_delay.delay_seconds == 1.5
    delay_necessity.assert_not_called()
    delay_frequency.assert_called_once_with(pending_count=2, trigger_threshold=4)
