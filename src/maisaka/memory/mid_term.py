"""Maisaka 聊天记录中期摘要消息。"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from html import escape
from typing import Any, Sequence

from json_repair import repair_json
from pydantic import BaseModel

from src.common.data_models.message_component_data_model import DictComponent, MessageSequence
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.llm_models.payload_content.message import (
    ImageMessagePart,
    Message,
    MessageBuilder,
    RoleType,
    TextMessagePart,
)
from src.maisaka.context.messages import ComplexSessionMessage, LLMContextMessage, build_llm_message_from_context
from src.maisaka.visual.message_limiter import limit_latest_images_in_messages

MID_TERM_MEMORY_COMPONENT_TYPE = "mid_term_memory"
MID_TERM_MEMORY_SOURCE_KIND = "mid_term_memory"
MID_TERM_MEMORY_COMPLEX_TYPE = "mid_term_memory"
MID_TERM_MEMORY_USER_NAME = "聊天记录摘要"
MAX_SUMMARY_INPUT_CHARS = 16000

logger = get_logger("maisaka_mid_term_memory")


class MidTermMemorySummaryModel(BaseModel):
    """聊天记录压缩摘要。"""

    long_summary: str
    brief: str
    keywords: list[str]


@dataclass(slots=True)
class MidTermMemoryBuildResult:
    """中期摘要消息构建结果。"""

    message: ComplexSessionMessage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_name: str = ""


def is_mid_term_memory_message(message: LLMContextMessage) -> bool:
    """判断上下文消息是否为中期摘要消息。"""

    return (
        isinstance(message, ComplexSessionMessage)
        and message.source_kind == MID_TERM_MEMORY_SOURCE_KIND
        and message.complex_message_type == MID_TERM_MEMORY_COMPLEX_TYPE
    )


async def build_mid_term_memory_message(
    removed_messages: Sequence[LLMContextMessage],
    *,
    session_id: str,
    log_prefix: str = "",
) -> MidTermMemoryBuildResult | None:
    """将被裁切的聊天历史总结成一条可展开的复杂消息。"""

    summary_source_messages = _select_summary_source_messages(removed_messages)
    if not summary_source_messages:
        logger.debug(f"{log_prefix} 中期聊天记录摘要跳过: 裁切消息中没有可摘要文本")
        return None

    time_range = _build_time_range(summary_source_messages)
    participants = _collect_participants(summary_source_messages)
    instruction_prompt = _build_summary_instruction_prompt(
        time_range=time_range,
        participants=participants,
    )
    text_prompt_messages = _build_summary_prompt_messages(
        summary_source_messages,
        instruction_prompt=instruction_prompt,
        enable_visual_message=False,
    )
    if len(text_prompt_messages) <= 1:
        logger.debug(f"{log_prefix} 中期聊天记录摘要跳过: 摘要输入消息为空")
        return None

    # logger.info(
    #     f"{log_prefix} 中期聊天记录概括完整 Prompt Messages: "
    #     f"裁切消息数={len(summary_source_messages)} "
    #     f"发送消息数={len(text_prompt_messages)} "
    #     f"时间范围={time_range} "
    #     f"参与人物={'、'.join(participants) if participants else '未知'} "
    #     f"prompt_chars={_count_prompt_message_chars(text_prompt_messages)}\n"
    #     f"{_render_summary_prompt_messages_for_log(text_prompt_messages)}"
    # )
    from src.services.llm_service import LLMServiceClient

    llm_client = LLMServiceClient(
        task_name="mid_memory",
        request_type="maisaka.mid_term_memory",
        session_id=session_id,
    )

    def message_factory(_client: Any, model_info: Any = None) -> list[Message]:
        return _build_summary_prompt_messages(
            summary_source_messages,
            instruction_prompt=instruction_prompt,
            enable_visual_message=_should_enable_visual_summary(model_info),
        )

    result = await llm_client.generate_response_with_messages(message_factory)
    summary_payload = _parse_summary_response(result.response)
    if summary_payload is None:
        logger.warning(
            f"{log_prefix} 中期聊天记录摘要解析失败，已跳过本次摘要插入: response={_truncate(result.response, 300)}"
        )
        return None

    message = build_mid_term_memory_complex_message(
        summary_payload,
        time_range=time_range,
        participants=participants,
        source_messages=summary_source_messages,
    )
    logger.info(
        f"{log_prefix} 中期聊天记录摘要生成内容: "
        f"msg_id={message.message_id} "
        f"时间范围={time_range} "
        f"参与人物={'、'.join(participants) if participants else '未知'} "
        f"关键词={'、'.join(summary_payload.keywords) if summary_payload.keywords else '无'}\n"
        f"brief:\n{summary_payload.brief.strip()}\n"
        f"long_summary:\n{summary_payload.long_summary.strip()}"
    )
    return MidTermMemoryBuildResult(
        message=message,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        model_name=result.model_name or "",
    )


def _select_summary_source_messages(messages: Sequence[LLMContextMessage]) -> list[LLMContextMessage]:
    """筛选真正参与中期摘要的历史消息。"""

    return [
        message
        for message in messages
        if message.role == "user"
        and not is_mid_term_memory_message(message)
        and str(message.processed_plain_text or "").strip()
    ]


def build_mid_term_memory_complex_message(
    summary_payload: MidTermMemorySummaryModel,
    *,
    time_range: str,
    participants: Sequence[str],
    source_messages: Sequence[LLMContextMessage],
) -> ComplexSessionMessage:
    """基于摘要内容构造复杂消息。"""

    timestamp = _resolve_summary_timestamp(source_messages)
    keywords = _normalize_keywords(summary_payload.keywords)
    participants_text = "、".join(participants) if participants else "未知"
    message_id = _build_summary_message_id(
        timestamp=timestamp,
        time_range=time_range,
        participants=participants,
        brief=summary_payload.brief,
        long_summary=summary_payload.long_summary,
    )
    payload = {
        "type": MID_TERM_MEMORY_COMPONENT_TYPE,
        "data": {
            "time_range": time_range,
            "participants": list(participants),
            "keywords": keywords,
            "brief": summary_payload.brief.strip(),
            "long_summary": summary_payload.long_summary.strip(),
        },
    }
    preview_text = build_mid_term_memory_preview_text(payload["data"])
    planner_prefix = _build_summary_planner_prefix(
        timestamp=timestamp,
        message_id=message_id,
    )
    visible_text = "\n".join(
        [
            f"[{MID_TERM_MEMORY_USER_NAME}]",
            f"时间范围: {time_range}",
            f"参与人物: {participants_text}",
            f"关键词: {'、'.join(keywords) if keywords else '无'}",
            f"brief: {summary_payload.brief.strip()}",
        ]
    )
    return ComplexSessionMessage(
        raw_message=MessageSequence([DictComponent(payload)]),
        visible_text=visible_text,
        timestamp=timestamp,
        message_id=message_id,
        source_kind=MID_TERM_MEMORY_SOURCE_KIND,
        prompt_text=f"{planner_prefix}{preview_text}",
        complex_message_type=MID_TERM_MEMORY_COMPLEX_TYPE,
    )


def insert_mid_term_memory_message(
    history: Sequence[LLMContextMessage],
    summary_message: ComplexSessionMessage,
    *,
    max_summary_count: int,
) -> list[LLMContextMessage]:
    """将新的中期摘要插入到上一条摘要之后，并维护最大保留数量。"""

    if max_summary_count <= 0:
        return [message for message in history if not is_mid_term_memory_message(message)]

    updated_history = list(history)
    insert_index = _find_last_mid_term_memory_index(updated_history)
    updated_history.insert(insert_index + 1, summary_message)
    _trim_mid_term_memory_messages(updated_history, max_summary_count=max_summary_count)
    return updated_history


def build_mid_term_memory_preview_text(payload: dict[str, Any]) -> str:
    """构造中期摘要在 Prompt 中未展开时可见的内容。"""

    time_range = str(payload.get("time_range") or "未知").strip()
    participants = _coerce_str_list(payload.get("participants"))
    keywords = _coerce_str_list(payload.get("keywords"))
    brief = str(payload.get("brief") or "").strip() or "无"
    return "\n".join(
        [
            "[消息类型]复杂消息",
            "聊天记录摘要",
            f"时间范围: {time_range}",
            f"参与人物: {'、'.join(participants) if participants else '未知'}",
            f"关键词: {'、'.join(keywords) if keywords else '无'}",
            f"brief: {brief}",
            "可以选择使用 view_complex_message 查看这段聊天记录的完整信息，获取关键信息和细节信息。",
        ]
    )


def build_mid_term_memory_full_text(payload: dict[str, Any]) -> str:
    """构造中期摘要通过复杂消息工具展开后的完整内容。"""

    time_range = str(payload.get("time_range") or "未知").strip()
    participants = _coerce_str_list(payload.get("participants"))
    keywords = _coerce_str_list(payload.get("keywords"))
    brief = str(payload.get("brief") or "").strip() or "无"
    long_summary = str(payload.get("long_summary") or "").strip() or brief
    return "\n".join(
        [
            "【聊天记录摘要】",
            f"时间范围: {time_range}",
            f"参与人物: {'、'.join(participants) if participants else '未知'}",
            f"关键词: {'、'.join(keywords) if keywords else '无'}",
            "",
            "brief:",
            brief,
            "",
            "long_summary:",
            long_summary,
        ]
    ).strip()


def _find_last_mid_term_memory_index(history: Sequence[LLMContextMessage]) -> int:
    last_index = -1
    for index, message in enumerate(history):
        if is_mid_term_memory_message(message):
            last_index = index
    return last_index


def _trim_mid_term_memory_messages(
    history: list[LLMContextMessage],
    *,
    max_summary_count: int,
) -> None:
    summary_indexes = [index for index, message in enumerate(history) if is_mid_term_memory_message(message)]
    excess_count = len(summary_indexes) - max_summary_count
    if excess_count <= 0:
        return

    for index in reversed(summary_indexes[:excess_count]):
        del history[index]


def _build_summary_instruction_prompt(
    *,
    time_range: str,
    participants: Sequence[str],
) -> str:
    participants_text = "、".join(participants) if participants else "未知"
    return load_prompt(
        "mid_term_memory_summary",
        time_range=time_range,
        participants_text=participants_text,
    )


def _build_summary_prompt_messages(
    source_messages: Sequence[LLMContextMessage],
    *,
    instruction_prompt: str,
    enable_visual_message: bool = False,
) -> list[Message]:
    prompt_messages = [MessageBuilder().set_role(RoleType.System).add_text_content(instruction_prompt).build()]
    total_source_chars = 0
    for source_message in source_messages:
        llm_message = build_llm_message_from_context(
            source_message,
            enable_visual_message=enable_visual_message,
        )
        if llm_message is None:
            continue

        message_text = llm_message.get_text_content().strip()
        if not message_text and not _message_has_visual_content(llm_message):
            continue

        remaining_chars = MAX_SUMMARY_INPUT_CHARS - total_source_chars
        if remaining_chars <= 0:
            break
        if len(message_text) > remaining_chars:
            llm_message = _truncate_message_text(llm_message, remaining_chars)
            prompt_messages.append(llm_message)
            break

        prompt_messages.append(llm_message)
        total_source_chars += len(message_text)

    if enable_visual_message:
        return limit_latest_images_in_messages(
            prompt_messages,
            max_image_num=global_config.visual.max_image_num,
        )
    return prompt_messages


def _count_prompt_message_chars(messages: Sequence[Message]) -> int:
    return sum(len(message.get_text_content()) for message in messages)


def _should_enable_visual_summary(model_info: Any) -> bool:
    return bool(getattr(model_info, "visual", False))


def _message_has_visual_content(message: Message) -> bool:
    return any(isinstance(part, ImageMessagePart) for part in message.parts)


def _truncate_message_text(message: Message, max_text_chars: int) -> Message:
    remaining_chars = max(0, int(max_text_chars))
    truncated_parts = []
    for part in message.parts:
        if isinstance(part, TextMessagePart):
            if remaining_chars <= 0:
                continue

            truncated_text = part.text[:remaining_chars]
            if truncated_text:
                truncated_parts.append(TextMessagePart(truncated_text))
                remaining_chars -= len(truncated_text)
            continue

        truncated_parts.append(part)

    if not truncated_parts:
        return (
            MessageBuilder()
            .set_role(message.role)
            .add_text_content(message.get_text_content()[:max_text_chars])
            .build()
        )
    return Message(
        role=message.role,
        parts=truncated_parts,
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        tool_calls=message.tool_calls,
    )


def _render_summary_prompt_messages_for_log(messages: Sequence[Message]) -> str:
    rendered_messages: list[str] = []
    for index, message in enumerate(messages, start=1):
        role = message.role.value if hasattr(message.role, "value") else str(message.role)
        rendered_messages.append(f"[{index}][{role}]\n{message.get_text_content()}")
    return "\n\n".join(rendered_messages).strip()


def _build_summary_planner_prefix(
    *,
    timestamp: datetime,
    message_id: str,
) -> str:
    return (
        f'<message msg_id="{escape(message_id, quote=True)}" '
        f'time="{escape(timestamp.strftime("%H:%M:%S"), quote=True)}">\n'
    )


def _build_time_range(messages: Sequence[LLMContextMessage]) -> str:
    timestamps = [message.timestamp for message in messages]
    if not timestamps:
        return "未知"

    start_time = min(timestamps)
    end_time = max(timestamps)
    return f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}"


def _collect_participants(messages: Sequence[LLMContextMessage]) -> list[str]:
    participants: list[str] = []
    seen: set[str] = set()
    for message in messages:
        participant = _resolve_participant_name(message)
        if not participant or participant in seen:
            continue
        seen.add(participant)
        participants.append(participant)
    return participants


def _resolve_participant_name(message: LLMContextMessage) -> str:
    original_message = getattr(message, "original_message", None)
    message_info = getattr(original_message, "message_info", None)
    user_info = getattr(message_info, "user_info", None)
    if user_info is not None:
        user_name = (
            getattr(user_info, "user_cardname", None)
            or getattr(user_info, "user_nickname", None)
            or getattr(user_info, "user_id", None)
        )
        if str(user_name or "").strip():
            return str(user_name).strip()

    if message.role == "assistant":
        return "麦麦"
    if isinstance(message, ComplexSessionMessage) and message.source_kind == "optimized_tool_history":
        return "历史工具调用"
    return str(message.source or "").strip()


def _parse_summary_response(response: str) -> MidTermMemorySummaryModel | None:
    payload = _load_json_payload(response)
    if not isinstance(payload, dict):
        return None

    long_summary = str(payload.get("long_summary") or "").strip()
    brief = str(payload.get("brief") or "").strip()
    keywords = _normalize_keywords(payload.get("keywords"))
    if not long_summary or not brief:
        return None
    return MidTermMemorySummaryModel(
        long_summary=long_summary,
        brief=brief,
        keywords=keywords,
    )


def _load_json_payload(response: str) -> Any:
    normalized_response = str(response or "").strip()
    if not normalized_response:
        return None

    candidates = [normalized_response]
    if fence_match := re.search(r"```(?:json)?\s*(.*?)\s*```", normalized_response, flags=re.S | re.I):
        candidates.append(fence_match.group(1).strip())

    object_start = normalized_response.find("{")
    object_end = normalized_response.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidates.append(normalized_response[object_start : object_end + 1])

    seen_candidates: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        parsed_payload = _parse_json_candidate(candidate)
        if isinstance(parsed_payload, dict):
            return parsed_payload

    return None


def _parse_json_candidate(candidate: str) -> Any:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    try:
        return repair_json(candidate, return_objects=True, logging=False)
    except Exception:
        return None


def _normalize_keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_keywords = re.split(r"[,，、\n]+", value)
    elif isinstance(value, list):
        raw_keywords = value
    else:
        raw_keywords = []

    keywords: list[str] = []
    seen: set[str] = set()
    for raw_keyword in raw_keywords:
        keyword = str(raw_keyword or "").strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
    return keywords[:8]


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized_values: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized_values.append(text)
    return normalized_values


def _resolve_summary_timestamp(messages: Sequence[LLMContextMessage]) -> datetime:
    timestamps = [message.timestamp for message in messages]
    if not timestamps:
        return datetime.now()
    return max(timestamps)


def _build_summary_message_id(
    *,
    timestamp: datetime,
    time_range: str,
    participants: Sequence[str],
    brief: str,
    long_summary: str,
) -> str:
    digest_source = "\n".join([time_range, "、".join(participants), brief, long_summary])
    digest = sha1(digest_source.encode("utf-8")).hexdigest()[:8]
    return f"mtm:{_to_base36(int(timestamp.timestamp() * 1000))}:{digest}"


def _to_base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    normalized_value = max(0, int(value))
    if normalized_value == 0:
        return "0"

    digits: list[str] = []
    while normalized_value:
        normalized_value, remainder = divmod(normalized_value, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))


def _truncate(text: str, max_length: int) -> str:
    normalized_text = str(text or "").strip()
    if len(normalized_text) <= max_length:
        return normalized_text
    return normalized_text[:max_length] + "..."
