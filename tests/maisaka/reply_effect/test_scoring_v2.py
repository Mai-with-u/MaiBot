from datetime import datetime

import pytest

from src.maisaka.reply_effect.judge import parse_judge_result
from src.maisaka.reply_effect.models import (
    FollowupMessageSnapshot,
    ReplyAssociation,
    ReplyEffectRecord,
    ReplyEffectScores,
    ReplyEffectStatus,
    ReplySnapshot,
    SessionSnapshot,
    UserSnapshot,
)
from src.maisaka.reply_effect.scoring import score_reply_effect


def build_record(effect_id: str = "effect-1") -> ReplyEffectRecord:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return ReplyEffectRecord(
        effect_id=effect_id,
        status=ReplyEffectStatus.PENDING,
        created_at=now,
        updated_at=now,
        session=SessionSnapshot("session", "session", "test", "group", "group", "", "测试群"),
        reply=ReplySnapshot("tool", "target", True, "bot 回复", ["bot 回复"], "reason", sent_message_ids=["bot-1"]),
        target_user=UserSnapshot("target-user", "目标用户", ""),
    )


def add_followup(
    record: ReplyEffectRecord,
    *,
    message_id: str,
    user_id: str,
    stance_target: str,
    stance: str,
    contribution: str,
    latency: float = 10.0,
) -> None:
    record.followup_messages.append(
        FollowupMessageSnapshot(
            message_id=message_id,
            timestamp=record.created_at,
            user_id=user_id,
            nickname=user_id,
            cardname="",
            visible_text="证据",
            plain_text="证据",
            latency_seconds=latency,
            is_target_user=user_id == "target-user",
            candidate_effect_ids=[record.effect_id],
            associations=[
                ReplyAssociation(
                    effect_id=record.effect_id,
                    attribution_type="semantic",
                    attribution_confidence=1.0,
                    stance_target=stance_target,
                    stance=stance,
                    contribution=contribution,
                    evaluator_confidence=1.0,
                )
            ],
        )
    )


def test_topic_negative_does_not_reduce_reception_and_advances_chat() -> None:
    record = build_record()
    add_followup(
        record,
        message_id="user-1",
        user_id="member-a",
        stance_target="topic_or_third_party",
        stance="rejection",
        contribution="advance",
    )

    scores = score_reply_effect(record)

    assert scores.reception_score == 50.0
    assert scores.conversation_score > 0


def test_factual_correction_reduces_reception_but_is_constructive() -> None:
    record = build_record()
    add_followup(
        record,
        message_id="user-1",
        user_id="member-a",
        stance_target="bot_content",
        stance="factual_correction",
        contribution="advance",
    )

    scores = score_reply_effect(record)

    assert scores.reception_score == 25.0
    assert scores.conversation_score > 0


def test_bot_attack_is_wrong_push() -> None:
    record = build_record()
    add_followup(
        record,
        message_id="user-1",
        user_id="member-a",
        stance_target="bot_persona",
        stance="bot_attack",
        contribution="wrong_push",
    )

    scores = score_reply_effect(record)

    assert scores.reception_score == 0.0
    assert scores.conversation_score == 0.0


def test_reception_averages_users_instead_of_message_count() -> None:
    record = build_record()
    for index in range(3):
        add_followup(
            record,
            message_id=f"positive-{index}",
            user_id="member-a",
            stance_target="bot_content",
            stance="appreciation",
            contribution="maintain",
        )
    add_followup(
        record,
        message_id="negative",
        user_id="member-b",
        stance_target="bot_content",
        stance="rejection",
        contribution="maintain",
    )

    scores = score_reply_effect(record)

    assert scores.reception_score == pytest.approx(55.0)


def test_relative_score_requires_thirty_comparable_records() -> None:
    record = build_record()
    history = []
    for index in range(29):
        item = build_record(f"history-{index}")
        item.status = ReplyEffectStatus.FINALIZED
        item.scores = ReplyEffectScores(10, 50, 10, 22, None, 1, 1, 1, 1)
        history.append(item)

    assert score_reply_effect(record, history).relative_score is None
    item = build_record("history-30")
    item.status = ReplyEffectStatus.FINALIZED
    item.scores = ReplyEffectScores(10, 50, 10, 22, None, 1, 1, 1, 1)
    history.append(item)
    assert score_reply_effect(record, history).relative_score is not None


def test_parser_rejects_missing_locked_quote() -> None:
    record = build_record()
    record.followup_messages.append(
        FollowupMessageSnapshot(
            message_id="user-1",
            timestamp=record.created_at,
            user_id="member-a",
            nickname="A",
            cardname="",
            visible_text="回复",
            plain_text="回复",
            latency_seconds=1,
            is_target_user=False,
            candidate_effect_ids=[record.effect_id],
            associations=[
                ReplyAssociation(
                    effect_id=record.effect_id,
                    attribution_type="explicit_quote",
                    attribution_confidence=1,
                    stance_target="bot_content",
                    stance="neutral",
                    contribution="maintain",
                )
            ],
        )
    )
    payload = {
        "strategy": {"primary": "answer", "secondary": [], "confidence": 1.0},
        "messages": [{"message_id": "user-1", "associations": []}],
    }

    with pytest.raises(ValueError, match="显式引用关联被遗漏"):
        parse_judge_result(payload, record, [record])
