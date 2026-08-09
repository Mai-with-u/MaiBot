from datetime import datetime

import pytest

from src.maisaka.reply_effect.judge import parse_judge_result
from src.maisaka.reply_effect.models import (
    FollowupMessageSnapshot,
    ReplyAssociation,
    ReplyEffectRecord,
    ReplyEffectStatus,
    ReplySnapshot,
    SessionSnapshot,
    UserSnapshot,
    reply_effect_record_from_dict,
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
    attribution_confidence: float = 1.0,
    evaluator_confidence: float = 1.0,
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
                    attribution_confidence=attribution_confidence,
                    stance_target=stance_target,
                    stance=stance,
                    contribution=contribution,
                    evaluator_confidence=evaluator_confidence,
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

    assert scores.reception_score is None
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


def test_missing_reception_is_removed_from_raw_score_weights() -> None:
    record = build_record()
    scores = score_reply_effect(record)

    assert scores.reception_score is None
    assert scores.raw_score == 0.0
    assert scores.confidence == pytest.approx(0.35)


def test_low_confidence_emotion_shrinks_toward_neutral() -> None:
    record = build_record()
    add_followup(
        record,
        message_id="positive",
        user_id="member-a",
        stance_target="bot_content",
        stance="appreciation",
        contribution="maintain",
        attribution_confidence=0.5,
        evaluator_confidence=0.8,
    )

    scores = score_reply_effect(record)

    assert scores.reception_score == 70.0
    assert scores.reception_evidence_confidence == pytest.approx(1 / 3, abs=0.0001)


def test_incomplete_observation_reduces_overall_confidence() -> None:
    record = build_record()
    add_followup(
        record,
        message_id="positive",
        user_id="member-a",
        stance_target="bot_content",
        stance="appreciation",
        contribution="maintain",
    )

    complete = score_reply_effect(record, observation_complete=True)
    incomplete = score_reply_effect(record, observation_complete=False)

    assert incomplete.confidence == pytest.approx(complete.confidence * 0.6, abs=0.0001)


def test_semantic_multi_candidate_association_reduces_confidence() -> None:
    record = build_record()
    add_followup(
        record,
        message_id="ambiguous",
        user_id="member-a",
        stance_target="bot_content",
        stance="appreciation",
        contribution="maintain",
    )
    record.followup_messages[0].associations.append(
        ReplyAssociation(
            effect_id="other-effect",
            attribution_type="semantic",
            attribution_confidence=1.0,
            stance_target="bot_content",
            stance="appreciation",
            contribution="maintain",
            evaluator_confidence=1.0,
        )
    )

    scores = score_reply_effect(record)

    assert scores.reception_score == 75.0
    assert scores.reception_evidence_confidence == pytest.approx(5 / 12, abs=0.0001)


def test_record_restores_evaluation_version() -> None:
    payload = build_record().to_json_dict()

    restored = reply_effect_record_from_dict(payload)

    assert restored.evaluation_version == 3


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


def test_parser_rejects_candidate_unavailable_when_followup_was_received() -> None:
    record = build_record("effect-current")
    future_record = build_record("effect-future")
    record.followup_messages.append(
        FollowupMessageSnapshot(
            message_id="user-1",
            timestamp=record.created_at,
            user_id="member-a",
            nickname="A",
            cardname="",
            visible_text="早于未来回复的用户消息",
            plain_text="早于未来回复的用户消息",
            latency_seconds=1,
            is_target_user=False,
            candidate_effect_ids=[record.effect_id],
        )
    )
    payload = {
        "strategy": {"primary": "answer", "secondary": [], "confidence": 1.0},
        "messages": [
            {
                "message_id": "user-1",
                "associations": [
                    {
                        "effect_id": future_record.effect_id,
                        "attribution_confidence": 1.0,
                        "stance_target": "bot_content",
                        "stance": "neutral",
                        "contribution": "maintain",
                        "reason": "错误关联到未来回复",
                        "evidence_spans": ["早于未来回复的用户消息"],
                        "confidence": 1.0,
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="当时尚不存在或已结束观察"):
        parse_judge_result(payload, record, [record, future_record])


def test_parser_accepts_empty_associations_for_unrelated_message() -> None:
    record = build_record()
    record.followup_messages.append(
        FollowupMessageSnapshot(
            message_id="user-1",
            timestamp=record.created_at,
            user_id="member-a",
            nickname="A",
            cardname="",
            visible_text="完全无关的消息",
            plain_text="完全无关的消息",
            latency_seconds=1,
            is_target_user=False,
            candidate_effect_ids=[record.effect_id],
        )
    )
    payload = {
        "strategy": {"primary": "answer", "secondary": [], "confidence": 1.0},
        "messages": [{"message_id": "user-1", "associations": []}],
    }

    _, _, _, associations = parse_judge_result(payload, record, [record])

    assert associations == {"user-1": []}


def test_parser_rejects_unrelated_as_an_association_label() -> None:
    record = build_record()
    record.followup_messages.append(
        FollowupMessageSnapshot(
            message_id="user-1",
            timestamp=record.created_at,
            user_id="member-a",
            nickname="A",
            cardname="",
            visible_text="完全无关的消息",
            plain_text="完全无关的消息",
            latency_seconds=1,
            is_target_user=False,
            candidate_effect_ids=[record.effect_id],
        )
    )
    payload = {
        "strategy": {"primary": "answer", "secondary": [], "confidence": 1.0},
        "messages": [
            {
                "message_id": "user-1",
                "associations": [
                    {
                        "effect_id": record.effect_id,
                        "attribution_confidence": 0.6,
                        "stance_target": "topic_or_third_party",
                        "stance": "neutral",
                        "contribution": "unrelated",
                        "reason": "无关",
                        "evidence_spans": ["完全无关的消息"],
                        "confidence": 0.6,
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="contribution 取值非法"):
        parse_judge_result(payload, record, [record])
