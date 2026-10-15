"""动态门控回归测试，仅隔离应用导入，不启动数据库或机器人。"""

from datetime import datetime
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace

import sys

import pytest


@pytest.fixture
def gates(monkeypatch):
    package_name = "_test_dynamic_turn_trigger"
    package = ModuleType(package_name)
    package.__path__ = [str(Path(__file__).resolve().parents[2] / "src/maisaka/turn_trigger")]
    monkeypatch.setitem(sys.modules, package_name, package)

    message_module = ModuleType("src.chat.message_receive.message")
    message_module.SessionMessage = SimpleNamespace
    monkeypatch.setitem(sys.modules, message_module.__name__, message_module)
    utils_module = ModuleType("src.chat.utils.utils")
    utils_module.is_bot_self = lambda platform, user_id: user_id == "bot"
    monkeypatch.setitem(sys.modules, utils_module.__name__, utils_module)
    try:
        yield import_module(f"{package_name}.gates")
    finally:
        for name in ("dynamic_gate", "reply_likelihood", "gates"):
            sys.modules.pop(f"{package_name}.{name}", None)


def message(message_id: str, timestamp: float, *, user_id: str = "user") -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        timestamp=datetime.fromtimestamp(timestamp),
        platform="test",
        message_info=SimpleNamespace(user_info=SimpleNamespace(user_id=user_id)),
        processed_plain_text="有问题吗？",
        is_at=False,
        is_mentioned=False,
    )


def make_gate(gates, history=()):
    runtime = SimpleNamespace(chat_stream=SimpleNamespace(is_group_session=True), _chat_history=list(history))
    return gates.DynamicReplyTurnGate(runtime)


def test_recent_presence_excludes_expired_pending_but_keeps_batch_size(gates):
    now = 10_000.0
    pending = [message(str(i), now - 1800) for i in range(120)]
    pending.extend([message("boundary", now - 300), message("recent", now - 1)])
    gate = make_gate(gates)

    snapshot = gate._build_likelihood_input(pending, now, pending_messages=pending)

    assert snapshot.recent_message_count == 2
    assert snapshot.pending_count == 122
    assert snapshot.has_question_mark


def test_round_and_release_share_recent_presence_and_self_ratio(gates):
    now = 10_000.0
    history = [
        SimpleNamespace(timestamp=datetime.fromtimestamp(now - age), source=source, count_in_context=True)
        for age, source in ((400, "user"), (20, "user"), (10, "guided_reply"))
    ]
    pending = [message(str(i), now - 30) for i in range(10)]
    pending.append(message("expired", now - 301))
    gate = make_gate(gates, history)

    round_snapshot = gate._build_likelihood_input(pending[-2:-1], now, pending_messages=pending)
    full_snapshot = gate._build_likelihood_input(pending, now, pending_messages=pending)

    assert round_snapshot.recent_message_count == full_snapshot.recent_message_count == 12
    assert round_snapshot.recent_self_ratio == full_snapshot.recent_self_ratio == 1 / 12
    assert round_snapshot.pending_count == 1
    assert full_snapshot.pending_count == 11


def test_hot_backlog_recovers_when_messages_leave_recent_window(gates, monkeypatch):
    now = 10_000.0
    monkeypatch.setattr(gates.time, "time", lambda: now)
    pending = [message(str(i), now) for i in range(120)]
    gate = make_gate(gates)

    assert not gate.evaluate(pending_messages=pending, frequency=0.8).should_trigger

    now += 301
    pending.append(message("new-after-quiet", now))
    assert gate.evaluate(pending_messages=pending, frequency=0.8).should_trigger


def test_virtual_round_scores_include_other_recent_pending_messages(gates, monkeypatch):
    now = 10_000.0
    monkeypatch.setattr(gates.time, "time", lambda: now)
    pending = [message(str(i), now) for i in range(120)]
    gate = make_gate(gates)
    assert not gate.evaluate(pending_messages=pending, frequency=0.8).should_trigger

    now += 41
    pending.extend([message("new", now), message("self", now, user_id="bot")])
    gate.evaluate(pending_messages=pending, frequency=0.8)

    external = pending[:-1]
    expected = gates.estimate_reply_probability(
        gate._build_likelihood_input(external[-1:], now, pending_messages=external)
    )
    assert gate._gate._proactive_scores[-1][1] == pytest.approx(expected)
    assert gate._build_likelihood_input(external, now, pending_messages=external).recent_message_count == 121
