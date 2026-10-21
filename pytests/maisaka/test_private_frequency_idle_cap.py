"""Frequency-gate timing regressions without starting the application runtime."""

from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest


@pytest.fixture
def gates(monkeypatch):
    package_name = "_test_private_frequency_idle_cap"
    package = ModuleType(package_name)
    package.__path__ = [str(Path(__file__).resolve().parents[2] / "src/maisaka/turn_trigger")]
    monkeypatch.setitem(sys.modules, package_name, package)
    message_module = ModuleType("src.chat.message_receive.message")
    message_module.SessionMessage = SimpleNamespace
    monkeypatch.setitem(sys.modules, message_module.__name__, message_module)
    utils_module = ModuleType("src.chat.utils.utils")
    utils_module.is_bot_self = lambda _platform, _user_id: False
    monkeypatch.setitem(sys.modules, utils_module.__name__, utils_module)
    try:
        yield import_module(f"{package_name}.gates")
    finally:
        for name in ("dynamic_gate", "reply_likelihood", "gates"):
            sys.modules.pop(f"{package_name}.{name}", None)


def make_gate(gates, *, group=False, interval=768.0, received_at=10_000.0):
    return gates.FrequencyThresholdTurnGate(
        SimpleNamespace(
            chat_stream=SimpleNamespace(is_group_session=group),
            _last_external_message_received_at=received_at,
            _last_message_received_at=received_at,
            _get_recent_average_external_message_interval=lambda: interval,
        )
    )


@pytest.mark.parametrize("interval,expected", [(10.0, 10.0), (30.0, 30.0), (768.0, 30.0)])
def test_private_compensation_uses_bounded_interval(gates, monkeypatch, interval, expected):
    monkeypatch.setattr(gates.time, "time", lambda: 10_000.0)
    gate = make_gate(gates, interval=interval)
    initial = gate.evaluate(pending_count=1, trigger_threshold=2)
    assert initial.decision == "delay"
    assert initial.delay_seconds == pytest.approx(expected)
    monkeypatch.setattr(gates.time, "time", lambda: 10_000.0 + expected)
    assert gate.evaluate(pending_count=1, trigger_threshold=2).should_trigger


def test_group_keeps_historical_message_interval(gates, monkeypatch):
    monkeypatch.setattr(gates.time, "time", lambda: 10_000.0)
    result = make_gate(gates, group=True).evaluate(pending_count=1, trigger_threshold=2)
    assert result.delay_seconds == pytest.approx(768.0)


@pytest.mark.parametrize("pending,threshold,elapsed", [(1, 3, 0.0), (1, 3, 30.0), (2, 3, 0.0)])
def test_private_cap_preserves_pending_message_threshold(gates, monkeypatch, pending, threshold, elapsed):
    monkeypatch.setattr(gates.time, "time", lambda: 10_000.0 + elapsed)
    result = make_gate(gates).evaluate(pending_count=pending, trigger_threshold=threshold)
    assert result.decision == "delay"
    assert result.delay_seconds == pytest.approx((threshold - pending) * 30.0 - elapsed)


def test_private_gate_never_triggers_from_silence_alone(gates, monkeypatch):
    monkeypatch.setattr(gates.time, "time", lambda: 100_000.0)
    result = make_gate(gates).evaluate(pending_count=0, trigger_threshold=2)
    assert not result.should_trigger


@pytest.mark.parametrize("interval", [None, 0.0, -1.0])
def test_missing_or_invalid_interval_still_waits(gates, monkeypatch, interval):
    monkeypatch.setattr(gates.time, "time", lambda: 10_000.0)
    result = make_gate(gates, interval=interval).evaluate(pending_count=1, trigger_threshold=2)
    assert result.decision == "wait"


def test_sufficient_messages_trigger_without_waiting(gates, monkeypatch):
    monkeypatch.setattr(gates.time, "time", lambda: 10_000.0)
    assert make_gate(gates).evaluate(pending_count=2, trigger_threshold=2).should_trigger
