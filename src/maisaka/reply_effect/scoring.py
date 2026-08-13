"""回复效果 v4 确定性评分规则。"""

from __future__ import annotations

from .models import FollowupMessageSnapshot, ReplyAssociation, ReplyEffectRecord, ReplyEffectScores

RECEPTION_CATEGORY_ORDER = (
    "appreciation",
    "playful",
    "neutral",
    "confusion",
    "factual_correction",
    "rejection",
    "bot_attack",
)
CONTRIBUTION_VALUES = {
    "advance": 1.0,
    "maintain": 0.6,
    "acknowledge": 0.3,
    "close": 0.1,
    "wrong_push": 0.0,
}


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _record_associations(record: ReplyEffectRecord) -> list[tuple[FollowupMessageSnapshot, ReplyAssociation]]:
    return [
        (followup, association)
        for followup in record.followup_messages
        for association in followup.associations
        if association.effect_id == record.effect_id
    ]


def _breadth_confidence_factor(user_count: int) -> float:
    """独立用户越多，证据越稳定；单个用户仍保留大部分可信度。"""

    return 0.75 + 0.25 * clamp(user_count / 3.0)


def _attribution_reliability(
    followup: FollowupMessageSnapshot,
    association: ReplyAssociation,
) -> float:
    """显式引用保持满额；语义关联按同消息的候选歧义数量折减。"""

    if association.attribution_type == "explicit_quote":
        return clamp(association.attribution_confidence)
    semantic_candidate_count = sum(
        item.attribution_type != "explicit_quote" for item in followup.associations
    )
    return clamp(association.attribution_confidence) / max(semantic_candidate_count, 1)


def _association_reliability(
    followup: FollowupMessageSnapshot,
    association: ReplyAssociation,
) -> float:
    """关联正确性与情绪或贡献轴判断必须同时成立。"""

    return _attribution_reliability(followup, association) * clamp(association.evaluator_confidence)


def calculate_response_score(record: ReplyEffectRecord) -> tuple[float, float]:
    edges = _record_associations(record)
    if not edges:
        return 0.0, 0.0
    confidences = [association.attribution_confidence for _, association in edges]
    presence = max(confidences)
    per_user: dict[str, float] = {}
    for followup, association in edges:
        per_user[followup.user_id] = max(
            per_user.get(followup.user_id, 0.0),
            _attribution_reliability(followup, association),
        )
    breadth = clamp(sum(per_user.values()) / 3.0)
    depth = clamp(sum(confidences) / 5.0)
    # 移除回应速度后，其余三项按原始 45:25:20 的比例归一化。
    score = 100.0 * (0.45 * presence + 0.25 * breadth + 0.20 * depth) / 0.90
    confidence = sum(per_user.values()) / len(per_user)
    confidence *= _breadth_confidence_factor(len(per_user))
    return round(score, 2), round(clamp(confidence), 4)


def classify_reception(record: ReplyEffectRecord) -> tuple[list[str], dict[str, int], float]:
    """保留 Bot 定向反馈的原始类别，不再压缩为含义模糊的连续分数。"""

    category_counts = {category: 0 for category in RECEPTION_CATEGORY_ORDER}
    per_user_confidence: dict[str, float] = {}
    for followup, association in _record_associations(record):
        if association.stance_target not in {"bot_content", "bot_persona"}:
            continue
        weight = _association_reliability(followup, association)
        if weight <= 0:
            continue
        category_counts[association.stance] += 1
        per_user_confidence[followup.user_id] = max(
            per_user_confidence.get(followup.user_id, 0.0),
            weight,
        )
    populated_counts = {category: count for category, count in category_counts.items() if count > 0}
    if not populated_counts:
        return [], {}, 0.0

    confidence = sum(per_user_confidence.values()) / len(per_user_confidence)
    confidence *= _breadth_confidence_factor(len(per_user_confidence))
    categories = [category for category in RECEPTION_CATEGORY_ORDER if category in populated_counts]
    return categories, populated_counts, round(clamp(confidence), 4)


def calculate_conversation_score(record: ReplyEffectRecord) -> tuple[float, float]:
    edges = _record_associations(record)
    if not edges:
        return 0.0, 0.0
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

    per_user_confidence: dict[str, float] = {}
    for followup, association in edges:
        per_user_confidence[followup.user_id] = max(
            per_user_confidence.get(followup.user_id, 0.0),
            _association_reliability(followup, association),
        )
    confidence = sum(per_user_confidence.values()) / len(per_user_confidence)
    confidence *= _breadth_confidence_factor(len(per_user_confidence))
    return round(100.0 * base * (1.0 - wrong_ratio), 2), round(clamp(confidence), 4)


def score_reply_effect(
    record: ReplyEffectRecord,
) -> ReplyEffectScores:
    associations = _record_associations(record)
    response, response_confidence = calculate_response_score(record)
    reception_categories, reception_counts, reception_confidence = classify_reception(record)
    conversation, conversation_confidence = calculate_conversation_score(record)

    weighted_confidences = [(response_confidence, 0.40), (conversation_confidence, 0.30)]
    if reception_categories:
        weighted_confidences.append((reception_confidence, 0.30))
    active_weight = sum(weight for _, weight in weighted_confidences)
    evidence_confidence = sum(value * weight for value, weight in weighted_confidences) / active_weight

    confidence: float | None = None
    if associations:
        confidence = 0.35 + 0.65 * evidence_confidence
    return ReplyEffectScores(
        response_score=response,
        reception_categories=reception_categories,
        reception_counts=reception_counts,
        conversation_score=conversation,
        confidence=round(clamp(confidence), 4) if confidence is not None else None,
        response_evidence_confidence=response_confidence,
        reception_evidence_confidence=reception_confidence,
        conversation_evidence_confidence=conversation_confidence,
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
