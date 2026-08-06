"""回复效果 v2 确定性评分规则。"""

from __future__ import annotations

from collections import defaultdict
from math import exp
from statistics import median
from typing import Sequence

from .models import FollowupMessageSnapshot, ReplyAssociation, ReplyEffectRecord, ReplyEffectScores

STANCE_VALUES = {
    "appreciation": 1.0,
    "playful": 0.6,
    "neutral": 0.0,
    "confusion": -0.3,
    "factual_correction": -0.5,
    "rejection": -0.8,
    "bot_attack": -1.0,
}
CONTRIBUTION_VALUES = {
    "advance": 1.0,
    "maintain": 0.6,
    "acknowledge": 0.3,
    "close": 0.1,
    "unrelated": 0.0,
    "wrong_push": 0.0,
}
MIN_BASELINE_SIZE = 30


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _record_associations(record: ReplyEffectRecord) -> list[tuple[FollowupMessageSnapshot, ReplyAssociation]]:
    return [
        (followup, association)
        for followup in record.followup_messages
        for association in followup.associations
        if association.effect_id == record.effect_id
    ]


def calculate_response_score(record: ReplyEffectRecord) -> tuple[float, float]:
    edges = _record_associations(record)
    if not edges:
        return 0.0, 1.0
    confidences = [association.attribution_confidence for _, association in edges]
    presence = max(confidences)
    per_user: dict[str, float] = {}
    for followup, association in edges:
        per_user[followup.user_id] = max(per_user.get(followup.user_id, 0.0), association.attribution_confidence)
    breadth = clamp(sum(per_user.values()) / 3.0)
    depth = clamp(sum(confidences) / 5.0)
    speed = max(
        association.attribution_confidence * exp(-followup.latency_seconds / 300.0)
        for followup, association in edges
    )
    score = 100.0 * (0.45 * presence + 0.25 * breadth + 0.20 * depth + 0.10 * speed)
    return round(score, 2), round(sum(confidences) / len(confidences), 4)


def calculate_reception_score(record: ReplyEffectRecord) -> tuple[float, float]:
    per_user_values: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for followup, association in _record_associations(record):
        if association.stance_target not in {"bot_content", "bot_persona"}:
            continue
        weight = association.attribution_confidence * association.evaluator_confidence
        per_user_values[followup.user_id].append((STANCE_VALUES[association.stance], weight))
    if not per_user_values:
        return 50.0, 0.0
    user_scores: list[float] = []
    evidence_weights: list[float] = []
    for values in per_user_values.values():
        total_weight = sum(weight for _, weight in values)
        if total_weight <= 0:
            continue
        user_scores.append(sum(value * weight for value, weight in values) / total_weight)
        evidence_weights.append(clamp(total_weight / len(values)))
    if not user_scores:
        return 50.0, 0.0
    score = 50.0 + 50.0 * sum(user_scores) / len(user_scores)
    return round(score, 2), round(sum(evidence_weights) / len(evidence_weights), 4)


def calculate_conversation_score(record: ReplyEffectRecord) -> tuple[float, float]:
    edges = _record_associations(record)
    if not edges:
        return 0.0, 1.0
    constructive_mass = sum(
        association.attribution_confidence * CONTRIBUTION_VALUES[association.contribution]
        for _, association in edges
    )
    constructive_users = {
        followup.user_id
        for followup, association in edges
        if CONTRIBUTION_VALUES[association.contribution] > 0
    }
    relevant_ids = {followup.message_id for followup, _ in edges}
    cross_user_edges = sum(
        1
        for followup, association in edges
        if association.contribution not in {"unrelated", "wrong_push"}
        and bool(set(followup.quote_target_ids + ([followup.reply_to] if followup.reply_to else [])) & relevant_ids)
    )
    observed_minutes = max(
        max((followup.latency_seconds for followup, _ in edges), default=0.0) / 60.0,
        1.0 / 60.0,
    )
    post_rate = len({followup.message_id for followup, _ in edges}) / observed_minutes
    pre_rate = record.pre_activity_count / 2.0
    activity_lift = clamp((post_rate - pre_rate) / max(pre_rate, 1.0))
    base = (
        0.35 * clamp(constructive_mass / 4.0)
        + 0.25 * clamp(len(constructive_users) / 3.0)
        + 0.20 * clamp(cross_user_edges / 3.0)
        + 0.20 * activity_lift
    )
    total_mass = sum(association.attribution_confidence for _, association in edges)
    wrong_mass = sum(
        association.attribution_confidence
        for _, association in edges
        if association.contribution == "wrong_push"
    )
    wrong_ratio = clamp(wrong_mass / max(total_mass, 1.0))
    confidence = sum(association.evaluator_confidence for _, association in edges) / len(edges)
    return round(100.0 * base * (1.0 - wrong_ratio), 2), round(confidence, 4)


def midrank_percentile(value: float, values: Sequence[float]) -> float:
    lower = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    return 100.0 * (lower + 0.5 * equal) / len(values)


def _select_baseline(record: ReplyEffectRecord, history: Sequence[ReplyEffectRecord]) -> tuple[list[ReplyEffectRecord], str]:
    usable = [item for item in history if item.scores is not None and item.scorer_version == record.scorer_version]
    levels = (
        (
            "session_strategy_activity_quote",
            lambda item: item.session.session_id == record.session.session_id
            and item.reply.strategy_primary == record.reply.strategy_primary
            and item.pre_activity_bucket == record.pre_activity_bucket
            and item.reply.set_quote == record.reply.set_quote,
        ),
        (
            "session_activity",
            lambda item: item.session.session_id == record.session.session_id
            and item.pre_activity_bucket == record.pre_activity_bucket,
        ),
        ("session", lambda item: item.session.session_id == record.session.session_id),
        (
            "global_type_strategy_activity",
            lambda item: item.session.chat_type == record.session.chat_type
            and item.reply.strategy_primary == record.reply.strategy_primary
            and item.pre_activity_bucket == record.pre_activity_bucket,
        ),
        ("global_type", lambda item: item.session.chat_type == record.session.chat_type),
    )
    for name, predicate in levels:
        cohort = [item for item in usable if predicate(item)]
        if len(cohort) >= MIN_BASELINE_SIZE:
            return cohort, name
    return [], "insufficient"


def score_reply_effect(
    record: ReplyEffectRecord,
    history: Sequence[ReplyEffectRecord] = (),
    *,
    observation_complete: bool = True,
) -> ReplyEffectScores:
    response, response_confidence = calculate_response_score(record)
    reception, reception_confidence = calculate_reception_score(record)
    conversation, conversation_confidence = calculate_conversation_score(record)
    raw_score = 0.40 * response + 0.30 * reception + 0.30 * conversation
    cohort, baseline_level = _select_baseline(record, history)
    relative_score = None
    baseline_confidence = 0.0
    if cohort:
        response_percentile = midrank_percentile(response, [item.scores.response_score for item in cohort if item.scores])
        reception_percentile = midrank_percentile(reception, [item.scores.reception_score for item in cohort if item.scores])
        conversation_percentile = midrank_percentile(
            conversation,
            [item.scores.conversation_score for item in cohort if item.scores],
        )
        relative_score = round(
            0.40 * response_percentile + 0.30 * reception_percentile + 0.30 * conversation_percentile,
            2,
        )
        baseline_confidence = clamp(len(cohort) / 100.0)
    evidence_confidence = median([response_confidence, reception_confidence, conversation_confidence])
    confidence = 0.40 * (1.0 if observation_complete else 0.6) + 0.40 * evidence_confidence + 0.20 * baseline_confidence
    return ReplyEffectScores(
        response_score=response,
        reception_score=reception,
        conversation_score=conversation,
        raw_score=round(raw_score, 2),
        relative_score=relative_score,
        confidence=round(clamp(confidence), 4),
        response_evidence_confidence=response_confidence,
        reception_evidence_confidence=reception_confidence,
        conversation_evidence_confidence=conversation_confidence,
        baseline_sample_size=len(cohort),
        baseline_level=baseline_level,
    )


def normalize_text_for_prompt(text: str, limit: int = 800) -> str:
    normalized = " ".join(str(text or "").split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def activity_bucket(message_count: int) -> str:
    if message_count <= 1:
        return "low"
    if message_count <= 5:
        return "medium"
    return "high"
