"""回复效果 v2 的上下文评审与严格解析。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Dict, Tuple

import json

from .models import (
    CONTRIBUTIONS,
    STANCES,
    STANCE_TARGETS,
    STRATEGIES,
    FollowupMessageSnapshot,
    ReplyAssociation,
    ReplyEffectRecord,
)
from .scoring import normalize_text_for_prompt

JudgeRunner = Callable[[str], Awaitable[str]]
MAX_CONTEXT_ITEMS = 20
MAX_PROMPT_CHARS = 12_000


async def judge_reply_effect(
    record: ReplyEffectRecord,
    candidate_records: Sequence[ReplyEffectRecord],
    judge_runner: JudgeRunner | None,
) -> Tuple[str, list[str], float, Dict[str, list[ReplyAssociation]]]:
    """执行一次严格的语义归因评审，失败后携带校验错误重试一次。"""

    if judge_runner is None:
        raise RuntimeError("未提供 LLM judge runner")
    prompt = build_judge_prompt(record, candidate_records)
    error = ""
    for attempt in range(2):
        active_prompt = prompt if not error else f"{prompt}\n\n上一次输出校验失败：{error}\n请修正后重新输出完整 JSON。"
        response_text = await judge_runner(active_prompt)
        try:
            payload = _loads_json_object(response_text)
            return parse_judge_result(payload, record, candidate_records)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            error = str(exc)
            if attempt == 1:
                raise ValueError(f"回复效果评审连续两次校验失败：{error}") from exc
    raise AssertionError("不可达的评审重试状态")


def build_judge_prompt(record: ReplyEffectRecord, candidate_records: Sequence[ReplyEffectRecord]) -> str:
    instruction = (
        "你是 MaiSaka 群聊回复效果评审器。不得使用关键词机械判断，只根据完整语境进行语义归因。\n"
        "每条后续消息可以关联零个、一个或多个候选 bot 回复；只能返回给出的 effect_id。\n"
        "显式引用关系已经由程序锁定，你不得删除，可在有充分证据时补充其他关联。\n"
        "评价必须拆成两个轴：评价目标 stance_target 与讨论贡献 contribution。\n"
        "对话题或第三方的负面喜好不等于反感 bot；指出 bot 的事实错误使用 factual_correction + advance；"
        "针对 bot 的厌烦或攻击使用 bot_attack + wrong_push。"
    )
    output_contract = (
        "策略 primary 只能是 answer/opinion/empathy/humor/question/topic_start/acknowledgement/other；"
        "secondary 也只能使用这些值。\n"
        "stance_target 只能是 topic_or_third_party/bot_content/bot_persona。\n"
        "stance 只能是 appreciation/playful/neutral/confusion/factual_correction/rejection/bot_attack。\n"
        "contribution 只能是 advance/maintain/acknowledge/close/unrelated/wrong_push。\n"
        "confidence 与 attribution_confidence 必须是 0 到 1。\n"
        "严格输出：\n"
        "{\n"
        '  "strategy": {"primary": "answer", "secondary": [], "confidence": 0.8},\n'
        '  "messages": [{"message_id": "...", "associations": [{"effect_id": "...", '
        '"attribution_confidence": 0.8, "stance_target": "bot_content", "stance": "neutral", '
        '"contribution": "advance", "reason": "...", "evidence_spans": ["..."], "confidence": 0.8}]}]\n'
        "}"
    )
    section_template = (
        "{instruction}\n\n"
        "评论前上下文：\n{context}\n\n"
        "候选 bot 评论：\n{candidates}\n\n"
        "后续用户消息：\n{followups}\n\n"
        "{output_contract}"
    )
    fixed_length = len(
        section_template.format(
            instruction=instruction,
            context="（无）",
            candidates="（无）",
            followups="（无）",
            output_contract=output_contract,
        )
    )
    content_budget = max(0, MAX_PROMPT_CHARS - fixed_length)
    context_budget = int(content_budget * 0.25)
    candidate_budget = int(content_budget * 0.35)
    followup_budget = content_budget - context_budget - candidate_budget
    prompt = section_template.format(
        instruction=instruction,
        context=_format_context(record.context_snapshot, record.reply.target_message_id, context_budget) or "（无）",
        candidates=_format_candidates(candidate_records, candidate_budget) or "（无）",
        followups=_format_followups(record.followup_messages, followup_budget) or "（无）",
        output_contract=output_contract,
    )
    return prompt


def parse_judge_result(
    payload: Dict[str, Any],
    record: ReplyEffectRecord,
    candidate_records: Sequence[ReplyEffectRecord],
) -> Tuple[str, list[str], float, Dict[str, list[ReplyAssociation]]]:
    candidate_ids = {item.effect_id for item in candidate_records}
    followup_ids = {item.message_id for item in record.followup_messages}
    locked_effects = {
        item.message_id: {
            association.effect_id
            for association in item.associations
            if association.attribution_type == "explicit_quote"
        }
        for item in record.followup_messages
    }
    strategy = _require_dict(payload.get("strategy"), "strategy")
    primary = _require_enum(strategy.get("primary"), STRATEGIES, "strategy.primary")
    secondary_raw = strategy.get("secondary", [])
    if not isinstance(secondary_raw, list):
        raise ValueError("strategy.secondary 必须是数组")
    secondary = [_require_enum(item, STRATEGIES, "strategy.secondary") for item in secondary_raw]
    strategy_confidence = _require_probability(strategy.get("confidence"), "strategy.confidence")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages 必须是数组")
    parsed: Dict[str, list[ReplyAssociation]] = {}
    seen_messages: set[str] = set()
    for raw_message in messages:
        message = _require_dict(raw_message, "messages[]")
        message_id = str(message.get("message_id") or "").strip()
        if message_id not in followup_ids or message_id in seen_messages:
            raise ValueError(f"未知或重复的后续消息 ID：{message_id}")
        seen_messages.add(message_id)
        raw_associations = message.get("associations")
        if not isinstance(raw_associations, list):
            raise ValueError(f"消息 {message_id} 的 associations 必须是数组")
        associations: list[ReplyAssociation] = []
        seen_effects: set[str] = set()
        for raw_association in raw_associations:
            item = _require_dict(raw_association, "associations[]")
            effect_id = str(item.get("effect_id") or "").strip()
            if effect_id not in candidate_ids or effect_id in seen_effects:
                raise ValueError(f"消息 {message_id} 包含未知或重复候选：{effect_id}")
            seen_effects.add(effect_id)
            evidence_spans = item.get("evidence_spans", [])
            if not isinstance(evidence_spans, list):
                raise ValueError("evidence_spans 必须是数组")
            associations.append(
                ReplyAssociation(
                    effect_id=effect_id,
                    attribution_type="semantic",
                    attribution_confidence=_require_probability(
                        item.get("attribution_confidence"),
                        "attribution_confidence",
                    ),
                    stance_target=_require_enum(item.get("stance_target"), STANCE_TARGETS, "stance_target"),
                    stance=_require_enum(item.get("stance"), STANCES, "stance"),
                    contribution=_require_enum(item.get("contribution"), CONTRIBUTIONS, "contribution"),
                    reason=str(item.get("reason") or "").strip(),
                    evidence_spans=[str(span).strip() for span in evidence_spans if str(span).strip()],
                    evaluator_confidence=_require_probability(item.get("confidence"), "confidence"),
                )
            )
        if not locked_effects[message_id].issubset(seen_effects):
            raise ValueError(f"消息 {message_id} 的显式引用关联被遗漏")
        parsed[message_id] = associations
    if seen_messages != followup_ids:
        raise ValueError("评审结果未覆盖全部后续消息")
    return primary, list(dict.fromkeys(secondary)), strategy_confidence, parsed


def _format_context(context_snapshot: list[dict[str, Any]], target_message_id: str, max_chars: int) -> str:
    selected = context_snapshot[-MAX_CONTEXT_ITEMS:]
    target_item = next(
        (item for item in context_snapshot if str(item.get("message_id") or "") == target_message_id),
        None,
    )
    if target_item is not None:
        selected = [target_item, *(item for item in selected if item is not target_item)]
        selected = selected[:MAX_CONTEXT_ITEMS]
    lines: list[str] = []
    used = 0
    for item in selected:
        line = (
            f"[{item.get('timestamp', '')}] role={item.get('role', '')} source={item.get('source', '')} "
            f"message_id={item.get('message_id', '')}: {normalize_text_for_prompt(str(item.get('text') or ''), 300)}"
        )
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _format_candidates(candidate_records: Sequence[ReplyEffectRecord], max_chars: int) -> str:
    if not candidate_records or max_chars <= 0:
        return ""
    line_prefixes = [f"- effect_id={item.effect_id}，bot回复=" for item in candidate_records]
    fixed_chars = sum(len(prefix) + 1 for prefix in line_prefixes)
    text_limit = max(1, (max_chars - fixed_chars) // len(candidate_records))
    lines: list[str] = []
    used = 0
    for prefix, item in zip(line_prefixes, candidate_records, strict=True):
        line = f"{prefix}{normalize_text_for_prompt(item.reply.reply_text, text_limit)}"
        remaining = max_chars - used
        if remaining <= 0:
            break
        lines.append(line[:remaining])
        used += len(lines[-1]) + 1
    return "\n".join(lines)


def _format_followups(followups: list[FollowupMessageSnapshot], max_chars: int) -> str:
    if not followups or max_chars <= 0:
        return ""
    line_prefixes: list[str] = []
    for item in followups:
        locked = [association.effect_id for association in item.associations if association.attribution_type == "explicit_quote"]
        line_prefixes.append(
            f"- message_id={item.message_id} user_id={item.user_id} latency={item.latency_seconds}s "
            f"reply_to={item.reply_to or '-'} quotes={item.quote_target_ids} locked={locked}: "
        )
    fixed_chars = sum(len(prefix) + 1 for prefix in line_prefixes)
    text_limit = max(1, (max_chars - fixed_chars) // len(followups))
    lines: list[str] = []
    used = 0
    for prefix, item in zip(line_prefixes, followups, strict=True):
        line = f"{prefix}{normalize_text_for_prompt(item.visible_text or item.plain_text, text_limit)}"
        remaining = max_chars - used
        if remaining <= 0:
            break
        lines.append(line[:remaining])
        used += len(lines[-1]) + 1
    return "\n".join(lines)


def _loads_json_object(response_text: str) -> Dict[str, Any]:
    normalized = str(response_text or "").strip()
    if normalized.startswith("```"):
        normalized = normalized.strip("`")
        if normalized.lower().startswith("json"):
            normalized = normalized[4:].strip()
    parsed = json.loads(normalized)
    if not isinstance(parsed, dict):
        raise ValueError("LLM judge 未返回 JSON 对象")
    return parsed


def _require_dict(value: Any, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    return value


def _require_enum(value: Any, choices: set[str], field_name: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in choices:
        raise ValueError(f"{field_name} 取值非法：{normalized}")
    return normalized


def _require_probability(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是数值")
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} 必须在 0 到 1 之间")
    return normalized
