"""Jev 回复决策逻辑测试。"""

from types import SimpleNamespace

import pytest

from src.common.i18n import set_locale
from src.maisaka.jev import (
    IGNORE_ACTION,
    REPLY_ACTION,
    build_reply_questions,
    build_reply_state,
    parse_reply_answer,
)
from src.maisaka.jev.client import JevAnswer, JevEvaluationResult


@pytest.fixture(autouse=True)
def _reset_locale() -> None:
    set_locale("zh-CN")


def _build_session_message(*, text: str, sender: str, is_at: bool = False) -> SimpleNamespace:
    """构造供决策模块消费的最小消息替身。"""

    return SimpleNamespace(
        platform="test",
        is_at=is_at,
        processed_plain_text=text,
        message_info=SimpleNamespace(
            user_info=SimpleNamespace(
                user_id=sender,
                user_nickname=sender,
                user_cardname=sender,
            )
        ),
    )


def _build_history_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(processed_plain_text=text)


def _build_result(action: str, *, probability: float, confidence: float = 0.7) -> JevEvaluationResult:
    other = IGNORE_ACTION if action == REPLY_ACTION else REPLY_ACTION
    return JevEvaluationResult(
        model="jev-1.13.0",
        answers={
            "reply_action": JevAnswer(
                answer_type="choice",
                choice=action,
                probabilities={action: probability, other: 1 - probability},
                confidence=confidence,
            )
        },
        input_tokens=100,
        output_tokens=2,
    )


def test_build_reply_state_contains_pending_and_recent_messages() -> None:
    state = build_reply_state(
        is_group_chat=True,
        pending_messages=[_build_session_message(text="@麦麦 在吗", sender="小明", is_at=True)],
        chat_history=[_build_history_message("[小红]今天天气不错"), _build_history_message("[麦麦]确实")],
        context_message_count=1,
    )

    assert state["chat_type"] == "群聊"
    assert state["bot_name"] == global_bot_name()
    assert state["pending_messages"] == [{"sender": "小明", "text": "@麦麦 在吗", "at_bot": True}]
    # context_message_count=1 时只带最近一条历史
    assert state["recent_messages"] == [{"sender": "麦麦", "text": "确实"}]


def test_build_reply_state_can_disable_context() -> None:
    state = build_reply_state(
        is_group_chat=False,
        pending_messages=[_build_session_message(text="你好", sender="小红")],
        chat_history=[_build_history_message("[麦麦]在的")],
        context_message_count=0,
    )

    assert state["recent_messages"] == []
    assert state["chat_type"] == "私聊"


def test_build_reply_questions_uses_choice_with_two_criteria() -> None:
    questions = build_reply_questions(is_group_chat=True)

    question = questions["reply_action"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {REPLY_ACTION, IGNORE_ACTION}
    # 提示词中必须渲染出机器人与聊天类型
    bot_name = global_bot_name()
    assert bot_name in question["instructions"]
    assert "群聊" in question["instructions"]


def test_build_reply_questions_localizes_chat_type() -> None:
    set_locale("en-US")
    questions = build_reply_questions(is_group_chat=False)

    assert "private chat" in questions["reply_action"]["instructions"]


def test_parse_reply_answer_marks_reply_as_trigger() -> None:
    decision = parse_reply_answer(_build_result(REPLY_ACTION, probability=0.83), pending_count=1)

    assert decision.should_reply is True
    assert decision.action == REPLY_ACTION
    assert decision.probability == pytest.approx(0.83)
    assert "reply" in decision.detail


def test_parse_reply_answer_marks_ignore_as_wait() -> None:
    decision = parse_reply_answer(_build_result(IGNORE_ACTION, probability=0.71), pending_count=3)

    assert decision.should_reply is False
    assert decision.action == IGNORE_ACTION
    assert decision.probability == pytest.approx(0.71)


def test_parse_reply_answer_rejects_missing_answer() -> None:
    result = JevEvaluationResult(model="jev-1.13.0", answers={})

    with pytest.raises(ValueError, match="未返回回复决策答案"):
        parse_reply_answer(result, pending_count=1)


def test_parse_reply_answer_rejects_unexpected_action() -> None:
    result = JevEvaluationResult(
        model="jev-1.13.0",
        answers={"reply_action": JevAnswer(answer_type="choice", choice="wait")},
    )

    with pytest.raises(ValueError, match="不在约定"):
        parse_reply_answer(result, pending_count=1)


def test_parse_reply_answer_rejects_wrong_answer_type() -> None:
    result = JevEvaluationResult(
        model="jev-1.13.0",
        answers={"reply_action": JevAnswer(answer_type="noul", noul=0.9)},
    )

    with pytest.raises(ValueError, match="应为 choice"):
        parse_reply_answer(result, pending_count=1)


def global_bot_name() -> str:
    from src.config.config import global_config

    return global_config.bot.nickname.strip()
