from datetime import datetime, timedelta
from types import SimpleNamespace

from src.maisaka.turn_trigger.dynamic_gate import (
    FORCED_TURN_EXPECTED_REPLIES,
    MIN_WINDOW_SCORE_COUNT,
    REPLY_WINDOW_SECONDS,
    VIRTUAL_ROUND_SECONDS,
    DynamicReplyGate,
)
from src.maisaka.turn_trigger.gates import DynamicReplyTurnGate
from src.maisaka.turn_trigger.reply_likelihood import (
    ReplyLikelihoodInput,
    estimate_reply_probability,
    summarize_pending_texts,
)


ROUND_GAP = VIRTUAL_ROUND_SECONDS + 1.0
"""相邻两次记录的间隔，保证各自落在不同的虚拟轮次里。"""

EVALUATED_AT = 30 * ROUND_GAP
"""在所有记录之后、且仍在统计窗口内的评估时刻。"""


def _build_input(**overrides) -> ReplyLikelihoodInput:
    values = {
        "mention_bot": False,
        "at_other": False,
        "has_question_mark": False,
        "placeholder_only": False,
        "recent_self_ratio": 0.1,
        "recent_message_count": 10,
        "seconds_since_bot_message": 600.0,
        "pending_count": 2,
    }
    values.update(overrides)
    return ReplyLikelihoodInput(**values)


def test_reply_probability_follows_fitted_signal_directions() -> None:
    """各信号对概率的影响方向应与拟合结果一致。"""

    baseline = estimate_reply_probability(_build_input())

    assert estimate_reply_probability(_build_input(mention_bot=True)) > baseline
    assert estimate_reply_probability(_build_input(has_question_mark=True)) > baseline
    assert estimate_reply_probability(_build_input(recent_self_ratio=0.5)) > baseline
    assert estimate_reply_probability(_build_input(at_other=True)) < baseline
    assert estimate_reply_probability(_build_input(placeholder_only=True)) < baseline
    assert estimate_reply_probability(_build_input(recent_message_count=60)) < baseline
    assert 0.0 < baseline < 1.0


def test_summarize_pending_texts() -> None:
    assert summarize_pending_texts(["@小明 你看这个？"], has_at_bot=False) == (True, True, False)
    assert summarize_pending_texts(["@麦麦 在吗"], has_at_bot=True) == (False, False, False)
    assert summarize_pending_texts(["[表情包:开心]", " [图片：一只猫]"], has_at_bot=False) == (False, False, True)
    assert summarize_pending_texts(["", "  "], has_at_bot=False) == (False, False, False)


def test_full_frequency_always_triggers() -> None:
    gate = DynamicReplyGate()
    gate.record_proactive_demand(0.05, now=100.0)

    decision = gate.evaluate(0.05, frequency=1.0, now=100.0)

    assert decision.should_trigger is True
    assert decision.threshold == 0.0


def test_window_threshold_keeps_highest_scored_batches() -> None:
    """窗口数据足够时，只放行概率最高、累计达到保留额度的那部分批次。"""

    gate = DynamicReplyGate()
    scores = [0.1] * 10 + [0.5] * 10
    for index, score in enumerate(scores):
        gate.record_proactive_demand(score, now=index * ROUND_GAP)
    # 预计 6 次回复，频率 0.5 -> 目标 3 次；窗口内已回复 3 次，反馈系数为 1
    for _ in range(3):
        gate.record_reply(now=EVALUATED_AT)

    high = gate.evaluate(0.5, frequency=0.5, now=EVALUATED_AT)
    low = gate.evaluate(0.1, frequency=0.5, now=EVALUATED_AT)

    assert len(scores) >= MIN_WINDOW_SCORE_COUNT
    assert high.target_replies == 3.0
    assert high.threshold == 0.5
    assert high.should_trigger is True
    assert low.should_trigger is False


def test_replying_more_than_target_tightens_threshold() -> None:
    gate_on_target = DynamicReplyGate()
    gate_over_target = DynamicReplyGate()
    for gate in (gate_on_target, gate_over_target):
        for index in range(30):
            gate.record_proactive_demand(0.1 + 0.02 * index, now=index * ROUND_GAP)
    expected_total = sum(0.1 + 0.02 * index for index in range(30))
    for _ in range(round(expected_total * 0.5)):
        gate_on_target.record_reply(now=EVALUATED_AT)
    for _ in range(round(expected_total * 0.5) + 3):
        gate_over_target.record_reply(now=EVALUATED_AT)

    on_target = gate_on_target.evaluate(0.3, frequency=0.5, now=EVALUATED_AT)
    over_target = gate_over_target.evaluate(0.3, frequency=0.5, now=EVALUATED_AT)

    assert over_target.keep_ratio < on_target.keep_ratio
    assert over_target.threshold > on_target.threshold


def test_replying_less_than_target_does_not_loosen_threshold() -> None:
    """实际回复少于目标时不放宽：保留比例停在按预计数算出的前馈值。"""

    gate = DynamicReplyGate()
    for index in range(30):
        gate.record_proactive_demand(0.3, now=index * ROUND_GAP)

    decision = gate.evaluate(0.3, frequency=0.5, now=EVALUATED_AT)

    assert decision.actual_replies == 0
    assert abs(decision.keep_ratio - 0.5) < 1e-9


def test_forced_turns_consume_reply_budget() -> None:
    """@ 强制触发的回复计入额度：额度被占满后，主动回复全部拦下。"""

    gate = DynamicReplyGate()
    for index in range(25):
        gate.record_proactive_demand(0.2, now=index * ROUND_GAP)
    for index in range(10):
        gate.record_forced_turn(now=float(index))

    decision = gate.evaluate(0.9, frequency=0.3, now=EVALUATED_AT)

    expected_total = 25 * 0.2 + 10 * FORCED_TURN_EXPECTED_REPLIES
    assert abs(decision.expected_replies - expected_total) < 1e-9
    assert decision.target_replies < 10 * FORCED_TURN_EXPECTED_REPLIES
    assert decision.keep_ratio == 0.0
    assert decision.should_trigger is False


def test_window_expires_old_records() -> None:
    gate = DynamicReplyGate()
    gate.record_proactive_demand(0.4, now=0.0)
    gate.record_forced_turn(now=0.0)
    gate.record_reply(now=0.0)

    decision = gate.evaluate(0.4, frequency=0.5, now=REPLY_WINDOW_SECONDS + 1.0)

    assert decision.expected_replies == 0.0
    assert decision.actual_replies == 0


def test_messages_in_same_virtual_round_are_counted_once() -> None:
    """同一虚拟轮次内陆续到达的消息只计一次，概率以最新一批为准；轮次结束后另起一次。"""

    gate = DynamicReplyGate()
    gate.record_proactive_demand(0.2, now=0.0)
    gate.record_proactive_demand(0.35, now=10.0)
    assert [score for _, score in gate._proactive_scores] == [0.35]

    gate.record_proactive_demand(0.1, now=VIRTUAL_ROUND_SECONDS + 1.0)
    assert [score for _, score in gate._proactive_scores] == [0.35, 0.1]

    gate.close_round()
    gate.record_proactive_demand(0.4, now=VIRTUAL_ROUND_SECONDS + 2.0)
    assert [score for _, score in gate._proactive_scores] == [0.35, 0.1, 0.4]


def test_messages_are_counted_once() -> None:
    gate = DynamicReplyGate()

    assert gate.take_uncounted_message_ids(["m1", "m2", ""]) == ["m1", "m2"]
    assert gate.take_uncounted_message_ids(["m2", "m3"]) == ["m3"]
    assert gate.take_uncounted_message_ids(["m1", "m3"]) == []


def _build_runtime(*, is_group: bool, history: list) -> SimpleNamespace:
    return SimpleNamespace(chat_stream=SimpleNamespace(is_group_session=is_group), _chat_history=history)


def _build_message(message_id: str, text: str, *, is_mentioned: bool = False, is_at: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        platform="test-platform",
        message_info=SimpleNamespace(user_info=SimpleNamespace(user_id="external-user")),
        processed_plain_text=text,
        is_mentioned=is_mentioned,
        is_at=is_at,
    )


def test_turn_gate_counts_each_message_once_and_reads_history() -> None:
    """同一批积压消息重复评估时，预计回复数不重复累计；麦麦近期发言会抬高概率。"""

    now = datetime.now()
    history = [
        SimpleNamespace(count_in_context=True, timestamp=now - timedelta(seconds=40), source="user"),
        SimpleNamespace(count_in_context=True, timestamp=now - timedelta(seconds=20), source="guided_reply"),
    ]
    gate = DynamicReplyTurnGate(_build_runtime(is_group=True, history=history))
    first_message = _build_message("m1", "这个怎么配置？")

    second_message = _build_message("m2", "麦麦知道吗", is_mentioned=True)

    # 频率为 0 时全部拦下：同一虚拟轮次内陆续到达、反复评估的消息只计一次预计回复数
    first = gate.evaluate(pending_messages=[first_message], frequency=0.0)
    second = gate.evaluate(pending_messages=[first_message, second_message], frequency=0.0)
    third = gate.evaluate(pending_messages=[first_message, second_message], frequency=0.0)

    assert "动态门控" in first.detail
    assert first.should_trigger is False
    assert len(gate._gate._proactive_scores) == 1
    assert second.detail == third.detail

    # 放行后 Planner 实际运行，当前轮次结束；之后到达的消息计入新的轮次
    passing_gate = DynamicReplyTurnGate(_build_runtime(is_group=True, history=history))
    assert passing_gate.evaluate(pending_messages=[first_message], frequency=1.0).should_trigger is True
    passing_gate.evaluate(pending_messages=[second_message], frequency=1.0)
    assert len(passing_gate._gate._proactive_scores) == 2

    quiet_gate = DynamicReplyTurnGate(_build_runtime(is_group=True, history=[]))
    with_recent_bot_reply = gate._build_likelihood_input([first_message], now.timestamp())
    without_bot_reply = quiet_gate._build_likelihood_input([first_message], now.timestamp())
    assert with_recent_bot_reply.recent_self_ratio > without_bot_reply.recent_self_ratio
    assert with_recent_bot_reply.seconds_since_bot_message < without_bot_reply.seconds_since_bot_message
    assert with_recent_bot_reply.recent_message_count == 3


def test_turn_gate_passes_private_chat_and_ignores_empty_batches() -> None:
    private_gate = DynamicReplyTurnGate(_build_runtime(is_group=False, history=[]))
    group_gate = DynamicReplyTurnGate(_build_runtime(is_group=True, history=[]))

    assert private_gate.evaluate(pending_messages=[_build_message("m1", "在吗")], frequency=0.1).should_trigger is True
    assert group_gate.evaluate(pending_messages=[], frequency=1.0).should_trigger is False


def test_forced_turn_is_recorded_once_per_batch() -> None:
    gate = DynamicReplyTurnGate(_build_runtime(is_group=True, history=[]))
    pending = [_build_message("m1", "@麦麦 在吗", is_at=True)]

    gate.record_forced_turn(pending)
    gate.record_forced_turn(pending)

    assert len(gate._gate._forced_expectations) == 1
