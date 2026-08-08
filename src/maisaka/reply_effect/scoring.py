"""回复效果评分规则。"""

from __future__ import annotations

from typing import Iterable, List

import re

from .models import BehaviorSignals, FollowupMessageSnapshot, FrictionSignals, ReplyEffectScores, RubricScores

NEGATIVE_PATTERNS = (
    "你没懂",
    "没懂",
    "不是这个意思",
    "不是",
    "别这样",
    "好烦",
    "烦死",
    "算了",
    "离谱",
    "无语",
    "你在说什么",
    "听不懂",
    "看不懂",
    "错了",
    "不对",
)
REPAIR_PATTERNS = (
    "我是说",
    "我说的是",
    "重新说",
    "再说一遍",
    "不是问",
    "你理解错",
    "你搞错",
    "我问的是",
    "纠正",
)
POSITIVE_PATTERNS = (
    "谢谢",
    "感谢",
    "懂了",
    "明白了",
    "可以",
    "有用",
    "不错",
    "好耶",
    "太好了",
)


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """限制数值范围。"""

    return max(lower, min(upper, value))


def score_reply_effect(
    followups: List[FollowupMessageSnapshot],
    rubric_scores: RubricScores,
    *,
    target_user_id: str = "",
    judge_error: str = "",
) -> ReplyEffectScores:
    """计算一条回复的 ASI 分数。"""

    behavior_signals = build_behavior_signals(followups, target_user_id=target_user_id)
    friction_signals = build_friction_signals(followups, rubric_scores, target_user_id=target_user_id)
    behavior_score = calculate_behavior_score(behavior_signals)
    relational_score = calculate_relational_score(rubric_scores)
    friction_score = calculate_friction_score(friction_signals)
    asi = calculate_asi_score(behavior_score, relational_score, friction_score)
    return ReplyEffectScores(
        asi=asi,
        behavior_score=round(behavior_score, 4),
        relational_score=round(relational_score, 4),
        friction_score=round(friction_score, 4),
        behavior_signals=behavior_signals,
        rubric_scores=rubric_scores,
        friction_signals=friction_signals,
        judge_error=judge_error,
    )


def build_behavior_signals(
    followups: List[FollowupMessageSnapshot],
    *,
    target_user_id: str = "",
) -> BehaviorSignals:
    """从后续消息构造行为满意度信号。"""

    target_followups = [
        followup
        for followup in followups
        if target_user_id and followup.user_id == target_user_id
    ]
    evidence_followups = target_followups or followups
    evidence_source = (
        "target_user_feedback"
        if target_followups
        else "indirect_session_feedback"
        if followups
        else "no_followup"
    )
    if not evidence_followups:
        return BehaviorSignals(
            continue_2turns=0.0,
            next_user_sentiment=0.5,
            user_expansion=0.0,
            no_correction=1.0,
            no_abort=0.6,
            evidence_source=evidence_source,
        )

    combined_text = "\n".join(followup.plain_text for followup in evidence_followups)
    negative_count = count_matches(combined_text, NEGATIVE_PATTERNS)
    repair_count = count_matches(combined_text, REPAIR_PATTERNS)
    positive_count = count_matches(combined_text, POSITIVE_PATTERNS)
    average_length = sum(len(followup.plain_text.strip()) for followup in evidence_followups) / len(evidence_followups)

    return BehaviorSignals(
        continue_2turns=1.0 if len(evidence_followups) >= 2 else 0.5,
        next_user_sentiment=estimate_sentiment(positive_count, negative_count, repair_count),
        user_expansion=clamp((average_length - 8.0) / 42.0),
        no_correction=0.0 if repair_count > 0 else 1.0,
        no_abort=0.0 if negative_count >= 2 or "算了" in combined_text else 1.0,
        evidence_source=evidence_source,
    )


def build_friction_signals(
    followups: List[FollowupMessageSnapshot],
    rubric_scores: RubricScores,
    *,
    target_user_id: str = "",
) -> FrictionSignals:
    """从后续消息和 LLM judge 结果构造摩擦信号。"""

    evidence_messages: List[str] = []
    explicit_negative = 0.0
    repair_loop = 0.0
    for followup in followups:
        text = followup.plain_text
        source_weight = 1.0 if target_user_id and followup.user_id == target_user_id else 0.65
        if any(pattern in text for pattern in NEGATIVE_PATTERNS):
            explicit_negative = max(explicit_negative, source_weight)
            evidence_messages.append(followup.message_id)
        if any(pattern in text for pattern in REPAIR_PATTERNS):
            repair_loop = max(repair_loop, source_weight)
            evidence_messages.append(followup.message_id)

    uncanny_risk = rubric_scores.uncanny_risk.normalized_score if rubric_scores.available else 0.5
    return FrictionSignals(
        explicit_negative=round(clamp(explicit_negative), 4),
        repair_loop=round(clamp(repair_loop), 4),
        uncanny_risk=round(clamp(uncanny_risk), 4),
        evidence_messages=sorted(set(evidence_messages)),
    )


def calculate_behavior_score(signals: BehaviorSignals) -> float:
    """计算行为满意度分数。"""

    return clamp(
        0.30 * signals.continue_2turns
        + 0.25 * signals.next_user_sentiment
        + 0.20 * signals.user_expansion
        + 0.15 * signals.no_correction
        + 0.10 * signals.no_abort
    )


def calculate_relational_score(rubric_scores: RubricScores) -> float:
    """计算感知质量分数。"""

    if not rubric_scores.available:
        return 0.5
    return clamp(
        0.35 * rubric_scores.social_presence.normalized_score
        + 0.25 * rubric_scores.warmth.normalized_score
        + 0.25 * rubric_scores.competence.normalized_score
        + 0.15 * rubric_scores.appropriateness.normalized_score
    )


def calculate_friction_score(signals: FrictionSignals) -> float:
    """计算摩擦惩罚分数。"""

    return clamp(
        0.40 * signals.explicit_negative
        + 0.30 * signals.repair_loop
        + 0.30 * signals.uncanny_risk
    )


def calculate_asi_score(behavior_score: float, relational_score: float, friction_score: float) -> float:
    """计算 0-100 的 ASI 总分，摩擦分越高扣分越多。"""

    return round(
        clamp(
            0.45 * behavior_score
            + 0.35 * relational_score
            + 0.20 * (1.0 - friction_score)
        )
        * 100,
        2,
    )


def has_explicit_negative_feedback(
    followups: Iterable[FollowupMessageSnapshot],
    *,
    target_user_id: str = "",
    allow_indirect: bool = False,
) -> bool:
    """判断是否出现可提前结算的明确负反馈。"""

    for followup in followups:
        if target_user_id and followup.user_id != target_user_id and not allow_indirect:
            continue
        if any(pattern in followup.plain_text for pattern in NEGATIVE_PATTERNS):
            return True
    return False


def has_repair_loop(
    followups: Iterable[FollowupMessageSnapshot],
    *,
    target_user_id: str = "",
    allow_indirect: bool = False,
) -> bool:
    """判断是否出现修复循环。"""

    repair_count = 0
    for followup in followups:
        if target_user_id and followup.user_id != target_user_id and not allow_indirect:
            continue
        if any(pattern in followup.plain_text for pattern in REPAIR_PATTERNS):
            repair_count += 1
    return repair_count >= 1


def count_matches(text: str, patterns: Iterable[str]) -> int:
    """统计模式命中次数。"""

    return sum(1 for pattern in patterns if pattern and pattern in text)


def estimate_sentiment(positive_count: int, negative_count: int, repair_count: int) -> float:
    """用轻量规则估计后续消息情绪。"""

    raw_score = 0.5 + 0.2 * positive_count - 0.25 * negative_count - 0.15 * repair_count
    return round(clamp(raw_score), 4)


def normalize_text_for_prompt(text: str, limit: int = 800) -> str:
    """清理用于评分 prompt 的文本。"""

    normalized_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized_text) <= limit:
        return normalized_text
    return normalized_text[: limit - 1] + "…"
