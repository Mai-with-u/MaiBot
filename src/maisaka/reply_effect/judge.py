"""回复效果 v2 的上下文评审与严格解析。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Dict, Tuple

import json

from src.config.config import global_config

from .models import (
    CONTRIBUTIONS,
    EVALUATION_VERSION,
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
    bot_names = [global_config.bot.nickname.strip(), *global_config.bot.alias_names]
    normalized_bot_names = list(dict.fromkeys(name.strip() for name in bot_names if name.strip()))
    bot_identity = "、".join(normalized_bot_names) or "（未配置昵称）"
    instruction = (
        f"回复效果评估标准版本：v{EVALUATION_VERSION}。\n"
        f"Bot 身份名称：{bot_identity}。这些名称在对话中指向当前 Bot，而不是第三方；"
        "出现名称本身不自动证明关联，仍需结合评价内容和连续语境判断。\n"
        "请先判断每条后续用户消息是否确实承接、回应或评价了某条候选 Bot 回复；"
        "观察窗口中的消息不一定与 Bot 有关。\n"
        "允许候选只限定消息发生时可以选择的 Bot 回复范围，不代表这些回复与消息实际相关。\n"
        "没有明确的引用、回复、指代、语义承接或情绪反馈证据时，必须返回 associations: []；"
        "不要为了评价话题转移、无人回应或回复未吸引讨论而强行建立关联。\n"
        "候选 Bot 回复一定先于与其关联的后续用户消息，请严格按照发送时间判断先后关系。\n"
        "必须把同一用户的相邻消息作为连续表达判断：如果前一条消息已明确承接某个候选 Bot 回复，"
        "紧邻消息继续评价 Bot 的回答、能力、人格或近期表现，应继承该对话焦点；"
        "只有明确换题、引用第三方或出现新的指向对象时才终止继承。\n"
        "明确称呼 Bot 并评价其回答或表现属于 bot_content 或 bot_persona 情绪反馈，"
        "不能仅因没有显式引用而返回空关联。若评价明确覆盖多轮 Bot 表现，可以关联多个候选；"
        "不得只因存在多个候选就机械关联全部候选。\n"
        "每条后续消息可以关联零个、一个或多个候选 bot 回复；只能返回给出的 candidate_id。\n"
        "messages 必须逐条覆盖给出的全部后续消息，即使无关也要返回该 message_id 和空 associations。\n"
        "只对已经确认存在关联的消息评价两个轴：评价目标 stance_target 与讨论贡献 contribution。\n"
        "对话题或第三方的负面喜好不等于反感 bot；指出 bot 的事实错误使用 factual_correction + advance；"
        "针对 bot 的厌烦或攻击使用 bot_attack + wrong_push。"
    )
    output_contract = (
        "策略 primary 只能是 answer/opinion/empathy/humor/question/topic_start/acknowledgement/other；"
        "secondary 也只能使用这些值。\n"
        "stance_target 只能是 topic_or_third_party/bot_content/bot_persona。\n"
        "stance 只能是 appreciation/playful/neutral/confusion/factual_correction/rejection/bot_attack。\n"
        "contribution 只能是 advance/maintain/acknowledge/close/wrong_push；"
        "无关消息必须使用空 associations，不存在 unrelated 关联标签。\n"
        "confidence 与 attribution_confidence 必须是 0 到 1。\n"
        "association 中的 candidate_id 必须使用候选 Bot 回复前的短编号。\n"
        "严格输出；下面同时给出无关联与有关联消息的格式：\n"
        "{\n"
        '  "strategy": {"primary": "answer", "secondary": [], "confidence": 0.8},\n'
        '  "messages": [\n'
        '    {"message_id": "无关联消息ID", "associations": []},\n'
        '    {"message_id": "有关联消息ID", "associations": [{"candidate_id": "c1", '
        '"attribution_confidence": 0.8, "stance_target": "bot_content", "stance": "neutral", '
        '"contribution": "advance", "reason": "具体关联理由", '
        '"evidence_spans": ["来自后续用户消息的证据"], "confidence": 0.8}]}\n'
        "  ]\n"
        "}"
    )
    section_template = (
        "{instruction}\n\n"
        "Bot 回复前的聊天上下文（按发送顺序排列）：\n{context}\n\n"
        "待评估的 Bot 回复：\n{candidates}\n\n"
        "Bot 回复后的用户消息（按发送顺序排列）：\n{followups}\n\n"
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
    candidate_aliases = {
        candidate.effect_id: f"c{index}"
        for index, candidate in enumerate(candidate_records, start=1)
    }
    candidate_minimum = _candidate_required_chars(candidate_records)
    followup_minimum = _followup_required_chars(record.followup_messages, candidate_aliases)
    remaining_budget = max(0, content_budget - candidate_minimum - followup_minimum)
    context_budget = min(int(content_budget * 0.20), remaining_budget // 3)
    remaining_budget -= context_budget
    candidate_budget = candidate_minimum + int(remaining_budget * 0.40)
    followup_budget = followup_minimum + remaining_budget - int(remaining_budget * 0.40)
    prompt = section_template.format(
        instruction=instruction,
        context=_format_context(record.context_snapshot, record.reply.target_message_id, context_budget) or "（无）",
        candidates=_format_candidates(candidate_records, candidate_aliases, candidate_budget) or "（无）",
        followups=(
            _format_followups(record.followup_messages, candidate_aliases, followup_budget) or "（无）"
        ),
        output_contract=output_contract,
    )
    return prompt


def parse_judge_result(
    payload: Dict[str, Any],
    record: ReplyEffectRecord,
    candidate_records: Sequence[ReplyEffectRecord],
) -> Tuple[str, list[str], float, Dict[str, list[ReplyAssociation]]]:
    candidate_ids = {item.effect_id for item in candidate_records}
    effect_ids_by_alias = {
        f"c{index}": candidate.effect_id
        for index, candidate in enumerate(candidate_records, start=1)
    }
    followup_ids = {item.message_id for item in record.followup_messages}
    allowed_effects_by_message = {
        item.message_id: set(item.candidate_effect_ids) for item in record.followup_messages
    }
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
            candidate_id = str(item.get("candidate_id") or "").strip()
            effect_id = effect_ids_by_alias.get(candidate_id, "")
            if effect_id not in candidate_ids or effect_id in seen_effects:
                raise ValueError(f"消息 {message_id} 包含未知或重复候选：{candidate_id}")
            if effect_id not in allowed_effects_by_message[message_id]:
                raise ValueError(
                    f"消息 {message_id} 关联了当时尚不存在或已结束观察的 Bot 回复：{effect_id}"
                )
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
        missing_message_ids = sorted(followup_ids - seen_messages)
        raise ValueError(
            "评审结果未覆盖全部后续消息，缺少 message_id："
            + "、".join(missing_message_ids)
        )
    return primary, list(dict.fromkeys(secondary)), strategy_confidence, parsed


def _format_context(context_snapshot: list[dict[str, Any]], target_message_id: str, max_chars: int) -> str:
    # 评分只需要真实聊天内容，不传入工具结果、内部推理、记忆和黑话注入等运行时信息。
    conversation_items = [
        (index, item)
        for index, item in enumerate(context_snapshot)
        if str(item.get("source") or "") in {"user", "guided_reply"}
    ]
    selected = conversation_items[-MAX_CONTEXT_ITEMS:]
    target_item = next(
        (pair for pair in conversation_items if str(pair[1].get("message_id") or "") == target_message_id),
        None,
    )
    if target_item is not None and target_item not in selected:
        selected = [target_item, *selected[-(MAX_CONTEXT_ITEMS - 1) :]]
        selected.sort(key=lambda pair: pair[0])
    lines: list[str] = []
    used = 0
    for _, item in selected:
        source = str(item.get("source") or "")
        sender = item.get("sender")
        display_name = str(sender.get("display_name") or "用户") if isinstance(sender, dict) else "用户"
        speaker = "Bot" if source == "guided_reply" else display_name
        target_mark = "（触发当前 Bot 回复）" if str(item.get("message_id") or "") == target_message_id else ""
        line = (
            f"- [{item.get('timestamp', '')}] {speaker}{target_mark}: "
            f"{normalize_text_for_prompt(str(item.get('text') or ''), 300)}"
        )
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _candidate_prefixes(
    candidate_records: Sequence[ReplyEffectRecord],
    candidate_aliases: dict[str, str],
) -> list[str]:
    return [
        f"- candidate_id={candidate_aliases[item.effect_id]} time={item.created_at} Bot: "
        for item in candidate_records
    ]


def _candidate_required_chars(candidate_records: Sequence[ReplyEffectRecord]) -> int:
    aliases = {item.effect_id: f"c{index}" for index, item in enumerate(candidate_records, start=1)}
    return sum(len(prefix) + 1 for prefix in _candidate_prefixes(candidate_records, aliases))


def _format_candidates(
    candidate_records: Sequence[ReplyEffectRecord],
    candidate_aliases: dict[str, str],
    max_chars: int,
) -> str:
    if not candidate_records or max_chars <= 0:
        return ""
    line_prefixes = _candidate_prefixes(candidate_records, candidate_aliases)
    fixed_chars = sum(len(prefix) + 1 for prefix in line_prefixes)
    text_limit = max(0, (max_chars - fixed_chars) // len(candidate_records))
    lines: list[str] = []
    for prefix, item in zip(line_prefixes, candidate_records, strict=True):
        text = normalize_text_for_prompt(item.reply.reply_text, text_limit) if text_limit else ""
        lines.append(f"{prefix}{text}")
    return "\n".join(lines)


def _followup_prefixes(
    followups: list[FollowupMessageSnapshot],
    candidate_aliases: dict[str, str],
) -> list[str]:
    line_prefixes: list[str] = []
    for item in followups:
        display_name = item.cardname or item.nickname or "用户"
        allowed_candidates = [
            candidate_aliases[effect_id]
            for effect_id in item.candidate_effect_ids
            if effect_id in candidate_aliases
        ]
        metadata = [f"允许候选（不代表相关）={allowed_candidates}"]
        if item.reply_to:
            metadata.append(f"回复消息={item.reply_to}")
        if item.quote_target_ids:
            metadata.append(f"引用消息={item.quote_target_ids}")
        confirmed_effect_ids = [
            association.effect_id
            for association in item.associations
            if association.attribution_type == "explicit_quote"
        ]
        if confirmed_effect_ids:
            confirmed_candidates = [candidate_aliases[effect_id] for effect_id in confirmed_effect_ids]
            metadata.append(f"已确认关联={confirmed_candidates}")
        metadata_text = f" ({'，'.join(metadata)})"
        line_prefixes.append(f"- [{item.timestamp}] message_id={item.message_id} {display_name}{metadata_text}: ")
    return line_prefixes


def _followup_required_chars(
    followups: list[FollowupMessageSnapshot],
    candidate_aliases: dict[str, str],
) -> int:
    return sum(len(prefix) + 1 for prefix in _followup_prefixes(followups, candidate_aliases))


def _format_followups(
    followups: list[FollowupMessageSnapshot],
    candidate_aliases: dict[str, str],
    max_chars: int,
) -> str:
    if not followups or max_chars <= 0:
        return ""
    line_prefixes = _followup_prefixes(followups, candidate_aliases)
    fixed_chars = sum(len(prefix) + 1 for prefix in line_prefixes)
    text_limit = max(0, (max_chars - fixed_chars) // len(followups))
    lines: list[str] = []
    for prefix, item in zip(line_prefixes, followups, strict=True):
        text = (
            normalize_text_for_prompt(item.visible_text or item.plain_text, text_limit)
            if text_limit
            else ""
        )
        lines.append(f"{prefix}{text}")
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
