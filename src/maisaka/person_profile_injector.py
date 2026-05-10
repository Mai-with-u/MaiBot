"""Maisaka 人物画像自动注入服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import AtComponent, ReplyComponent
from src.common.logger import get_logger
from src.config.config import global_config
from src.person_info.person_info import resolve_person_id_for_memory
from src.services.memory_service import memory_service

logger = get_logger("maisaka_person_profile_injector")

PROFILE_QUERY_LIMIT = 4
PROFILE_TEXT_MAX_CHARS = 900


@dataclass(frozen=True)
class PersonProfileCandidate:
    """一次 planner 注入候选人物。"""

    person_id: str
    person_name: str = ""
    user_id: str = ""
    source: str = ""


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _candidate_name(*values: object) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _is_bot_user_id(user_id: str) -> bool:
    bot_user_id = _clean_text(getattr(global_config.bot, "qq_account", ""))
    return bool(user_id and bot_user_id and user_id == bot_user_id)


def _resolve_candidate(
    *,
    platform: str,
    user_id: str = "",
    person_name: str = "",
    source: str,
) -> PersonProfileCandidate | None:
    clean_platform = _clean_text(platform)
    clean_user_id = _clean_text(user_id)
    clean_person_name = _clean_text(person_name)
    if _is_bot_user_id(clean_user_id):
        return None

    try:
        if clean_user_id:
            person_id = resolve_person_id_for_memory(
                platform=clean_platform,
                user_id=clean_user_id,
            )
        elif clean_person_name:
            person_id = resolve_person_id_for_memory(person_name=clean_person_name)
        else:
            person_id = ""
    except Exception as exc:
        logger.debug(f"解析人物画像候选失败: source={source} user_id={clean_user_id!r} name={clean_person_name!r} err={exc}")
        return None

    if not person_id:
        return None
    return PersonProfileCandidate(
        person_id=person_id,
        person_name=clean_person_name,
        user_id=clean_user_id,
        source=source,
    )


def _sender_candidate(message: SessionMessage, source: str) -> PersonProfileCandidate | None:
    user_info = message.message_info.user_info
    return _resolve_candidate(
        platform=message.platform,
        user_id=user_info.user_id,
        person_name=_candidate_name(user_info.user_cardname, user_info.user_nickname, user_info.user_id),
        source=source,
    )


def _at_candidate(message: SessionMessage, component: AtComponent) -> PersonProfileCandidate | None:
    return _resolve_candidate(
        platform=message.platform,
        user_id=component.target_user_id,
        person_name=_candidate_name(
            component.target_user_cardname,
            component.target_user_nickname,
            component.target_user_id,
        ),
        source="at_user",
    )


def _reply_candidate(message: SessionMessage, component: ReplyComponent) -> PersonProfileCandidate | None:
    return _resolve_candidate(
        platform=message.platform,
        user_id=component.target_message_sender_id or "",
        person_name=_candidate_name(
            component.target_message_sender_cardname,
            component.target_message_sender_nickname,
            component.target_message_sender_id,
        ),
        source="reply_sender",
    )


def _messages_current_first(
    anchor_message: SessionMessage,
    pending_messages: Sequence[SessionMessage] | None,
) -> list[SessionMessage]:
    messages: list[SessionMessage] = [anchor_message]
    seen_keys = {_message_key(anchor_message)}
    for message in reversed(list(pending_messages or [])):
        key = _message_key(message)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        messages.append(message)
    return messages


def _message_key(message: SessionMessage) -> str:
    message_id = _clean_text(message.message_id)
    if message_id:
        return f"message:{message_id}"
    return f"object:{id(message)}"


def collect_person_profile_candidates(
    anchor_message: SessionMessage,
    pending_messages: Sequence[SessionMessage] | None = None,
    *,
    max_profiles: int = 3,
) -> list[PersonProfileCandidate]:
    """按当前对象优先顺序收集本轮可注入画像候选。"""

    limit = max(1, int(max_profiles or 1))
    if anchor_message.message_info.group_info is None:
        candidate = _sender_candidate(anchor_message, "private_current_user")
        return [candidate] if candidate is not None else []

    candidates: list[PersonProfileCandidate] = []
    seen_person_ids: set[str] = set()

    def add(candidate: PersonProfileCandidate | None) -> bool:
        if candidate is None or candidate.person_id in seen_person_ids:
            return len(candidates) >= limit
        seen_person_ids.add(candidate.person_id)
        candidates.append(candidate)
        return len(candidates) >= limit

    for message in _messages_current_first(anchor_message, pending_messages):
        if add(_sender_candidate(message, "recent_speaker")):
            break
        for component in message.raw_message.components:
            if isinstance(component, AtComponent):
                if add(_at_candidate(message, component)):
                    break
            elif isinstance(component, ReplyComponent) and component.target_message_sender_id:
                if add(_reply_candidate(message, component)):
                    break
        if len(candidates) >= limit:
            break

    return candidates[:limit]


def _extract_profile_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    return _clean_text(payload.get("profile_text") or payload.get("summary"))


def _profile_display_name(candidate: PersonProfileCandidate, payload: object) -> str:
    if isinstance(payload, dict):
        payload_name = _clean_text(payload.get("person_name"))
        if payload_name:
            return payload_name
    return _candidate_name(candidate.person_name, candidate.user_id, candidate.person_id)


def _truncate_profile_text(profile_text: str) -> str:
    normalized = profile_text.strip()
    if len(normalized) <= PROFILE_TEXT_MAX_CHARS:
        return normalized
    return normalized[:PROFILE_TEXT_MAX_CHARS].rstrip() + "..."


def _format_profile_reference_block(blocks: Sequence[str]) -> str:
    joined_blocks = "\n\n".join(blocks).strip()
    if not joined_blocks:
        return ""
    return (
        "【人物画像-内部参考】\n"
        "以下内容仅供内部推理，不要向用户逐字复述。\n\n"
        f"{joined_blocks}\n\n"
        "使用时把它当作对当前人物的背景理解；若与当前对话冲突，以当前对话为准。"
    )


async def build_person_profile_injection_messages(
    *,
    anchor_message: SessionMessage,
    pending_messages: Sequence[SessionMessage] | None = None,
) -> list[str]:
    """构造注入 planner 的一次性人物画像内部参考消息。"""

    integration_config = global_config.a_memorix.integration
    if not bool(getattr(integration_config, "enable_person_profile_injection", True)):
        return []

    try:
        max_profiles = int(getattr(integration_config, "person_profile_injection_max_profiles", 3) or 3)
    except (TypeError, ValueError):
        max_profiles = 3
    candidates = collect_person_profile_candidates(
        anchor_message,
        pending_messages,
        max_profiles=max(1, max_profiles),
    )
    if not candidates:
        return []

    blocks: list[str] = []
    for candidate in candidates:
        try:
            payload = await memory_service.profile_admin(
                action="query",
                person_id=candidate.person_id,
                limit=PROFILE_QUERY_LIMIT,
            )
        except Exception as exc:
            logger.debug(f"查询人物画像注入内容失败: person_id={candidate.person_id!r} err={exc}")
            continue

        if not isinstance(payload, dict) or not bool(payload.get("success")):
            error = payload.get("error") if isinstance(payload, dict) else "invalid_payload"
            logger.debug(f"人物画像注入跳过: person_id={candidate.person_id!r} error={error}")
            continue

        profile_text = _extract_profile_text(payload)
        if not profile_text:
            logger.debug(f"人物画像注入跳过空画像: person_id={candidate.person_id!r}")
            continue

        display_name = _profile_display_name(candidate, payload)
        blocks.append(
            f"- {display_name}（person_id: {candidate.person_id}，来源: {candidate.source}）\n"
            f"  {_truncate_profile_text(profile_text)}"
        )

    reference_block = _format_profile_reference_block(blocks)
    return [reference_block] if reference_block else []
