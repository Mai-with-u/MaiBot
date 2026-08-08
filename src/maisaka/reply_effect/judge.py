"""回复效果 LLM 窄维度评审。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Tuple

import json

from .models import FollowupMessageSnapshot, ReplyEffectRecord, RubricScoreItem, RubricScores
from .scoring import normalize_text_for_prompt

JudgeRunner = Callable[[str], Awaitable[str]]


async def judge_reply_effect(record: ReplyEffectRecord, judge_runner: JudgeRunner | None) -> Tuple[RubricScores, str]:
    """执行 LLM rubric judge，失败时返回中性分。"""

    if judge_runner is None:
        return RubricScores(), "未提供 LLM judge runner"

    prompt = build_judge_prompt(record)
    try:
        response_text = await judge_runner(prompt)
        payload = _loads_json_object(response_text)
        return parse_rubric_scores(payload), ""
    except Exception as exc:
        return RubricScores(), str(exc)


def build_judge_prompt(record: ReplyEffectRecord) -> str:
    """构建窄维度评分 prompt。"""

    followup_text = _format_followups(record.followup_messages)
    return (
        "你是 Maisaka 回复效果的窄维度评审器，只评估这一次 bot 回复的交互感知质量。\n"
        "不要评价总体满意度，不要给建议，只输出 JSON。\n\n"
        "评分范围：1 到 5，1=很差，3=中性，5=很好。\n"
        "uncanny_risk 的 1=完全不怪，5=非常过度拟人/越界/油腻。\n\n"
        f"bot 回复：\n{normalize_text_for_prompt(record.reply.reply_text, 1200)}\n\n"
        f"后续用户消息：\n{followup_text or '（暂无后续用户消息）'}\n\n"
        "请输出严格 JSON 对象，格式如下：\n"
        "{\n"
        '  "social_presence": {"score": 3, "reason": "...", "evidence_spans": ["..."], "confidence": 0.7},\n'
        '  "warmth": {"score": 3, "reason": "...", "evidence_spans": ["..."], "confidence": 0.7},\n'
        '  "competence": {"score": 3, "reason": "...", "evidence_spans": ["..."], "confidence": 0.7},\n'
        '  "appropriateness": {"score": 3, "reason": "...", "evidence_spans": ["..."], "confidence": 0.7},\n'
        '  "uncanny_risk": {"score": 3, "reason": "...", "evidence_spans": ["..."], "confidence": 0.7}\n'
        "}"
    )


def parse_rubric_scores(payload: Dict[str, Any]) -> RubricScores:
    """解析 LLM rubric JSON。"""

    return RubricScores(
        social_presence=_parse_item(payload.get("social_presence")),
        warmth=_parse_item(payload.get("warmth")),
        competence=_parse_item(payload.get("competence")),
        appropriateness=_parse_item(payload.get("appropriateness")),
        uncanny_risk=_parse_item(payload.get("uncanny_risk")),
        available=True,
    )


def _parse_item(raw_item: Any) -> RubricScoreItem:
    if not isinstance(raw_item, dict):
        raw_item = {}
    score = _coerce_float(raw_item.get("score"), 3.0)
    score = max(1.0, min(5.0, score))
    evidence_spans = raw_item.get("evidence_spans")
    if not isinstance(evidence_spans, list):
        evidence_spans = []
    return RubricScoreItem(
        score=score,
        normalized_score=round((score - 1.0) / 4.0, 4),
        reason=str(raw_item.get("reason") or "").strip(),
        evidence_spans=[str(item).strip() for item in evidence_spans if str(item).strip()],
        confidence=max(0.0, min(1.0, _coerce_float(raw_item.get("confidence"), 0.0))),
    )


def _loads_json_object(response_text: str) -> Dict[str, Any]:
    normalized_response = str(response_text or "").strip()
    if normalized_response.startswith("```"):
        normalized_response = normalized_response.strip("`")
        if normalized_response.lower().startswith("json"):
            normalized_response = normalized_response[4:].strip()
    try:
        parsed = json.loads(normalized_response)
    except json.JSONDecodeError:
        start = normalized_response.find("{")
        end = normalized_response.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(normalized_response[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM judge 未返回 JSON 对象")
    return parsed


def _format_followups(followups: List[FollowupMessageSnapshot]) -> str:
    lines: List[str] = []
    for index, followup in enumerate(followups[:5], start=1):
        marker = "目标用户" if followup.is_target_user else "其他用户"
        lines.append(
            f"{index}. [{marker}] {normalize_text_for_prompt(followup.visible_text or followup.plain_text, 500)}"
        )
    return "\n".join(lines)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
