"""Maisaka 聊天回想消息。"""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from html import escape
from math import sqrt
from typing import Any, Sequence
from json_repair import repair_json
from pydantic import BaseModel
import json
import re


from src.common.data_models.message_component_data_model import DictComponent, MessageSequence
from src.common.data_models.embedding_service_data_models import EmbeddingResult
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
from src.maisaka.context.messages import (
    ComplexSessionMessage,
    LLMContextMessage,
    ReferenceMessage,
    ReferenceMessageType,
    build_llm_message_from_context,
)
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.visual.message_limiter import limit_latest_images_in_messages

MID_TERM_MEMORY_COMPONENT_TYPE = "mid_term_memory"
MID_TERM_MEMORY_SOURCE_KIND = "mid_term_memory"
MID_TERM_MEMORY_COMPLEX_TYPE = "mid_term_memory"
MID_TERM_MEMORY_USER_NAME = "聊天回想"
MID_TERM_MEMORY_REFERENCE_MARKER = "【聊天回想-内部参考】"
MAX_SUMMARY_INPUT_CHARS = 16000
MID_TERM_MEMORY_RECALL_CONTEXT_MESSAGE_LIMIT = 12
MID_TERM_MEMORY_RECALL_CONTEXT_TEXT_LIMIT = 2400
MID_TERM_MEMORY_RECALL_SUMMARY_TEXT_LIMIT = 1400
MID_TERM_MEMORY_DEFAULT_RECALL_THRESHOLD = 0.8

logger = get_logger("maisaka_mid_term_memory")


class MidTermMemorySummaryModel(BaseModel):
    """聊天记录压缩摘要。"""

    summary: str
    recall_cues: list[str] = []


@dataclass(slots=True)
class MidTermMemoryBuildResult:
    """聊天回想消息构建结果。"""

    message: ComplexSessionMessage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_name: str = ""


@dataclass(frozen=True, slots=True)
class MidTermMemoryRecallCandidate:
    """一条聊天回想匹配段的召回候选。"""

    message: ComplexSessionMessage
    payload: dict[str, Any]
    segment_text: str
    score: float


def is_mid_term_memory_message(message: LLMContextMessage) -> bool:
    """判断上下文消息是否为聊天回想消息。"""

    return (
        isinstance(message, ComplexSessionMessage)
        and message.source_kind == MID_TERM_MEMORY_SOURCE_KIND
        and message.complex_message_type == MID_TERM_MEMORY_COMPLEX_TYPE
    )


def is_mid_term_memory_reference_message(message: LLMContextMessage) -> bool:
    """判断上下文消息是否为聊天回想召回参考。"""

    return (
        isinstance(message, ReferenceMessage)
        and message.reference_type == ReferenceMessageType.MEMORY
        and message.content.startswith(MID_TERM_MEMORY_REFERENCE_MARKER)
    )


async def build_mid_term_memory_message(
    removed_messages: Sequence[LLMContextMessage],
    *,
    session_id: str,
    log_prefix: str = "",
) -> MidTermMemoryBuildResult | None:
    """将被裁切的聊天历史总结成一条聊天回想消息。"""

    summary_source_messages = _select_summary_source_messages(removed_messages)
    if not summary_source_messages:
        logger.debug(f"{log_prefix} 聊天回想跳过: 裁切消息中没有可摘要文本")
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
        logger.debug(f"{log_prefix} 聊天回想跳过: 摘要输入消息为空")
        return None

    # logger.info(
    #     f"{log_prefix} 聊天回想完整 Prompt Messages: "
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

    request_prompt_messages: list[Message] = []

    def message_factory(_client: Any, model_info: Any = None) -> list[Message]:
        nonlocal request_prompt_messages
        request_prompt_messages = _build_summary_prompt_messages(
            summary_source_messages,
            instruction_prompt=instruction_prompt,
            enable_visual_message=_should_enable_visual_summary(model_info),
        )
        return request_prompt_messages

    result = await llm_client.generate_response_with_messages(message_factory)
    _save_mid_term_memory_prompt_preview(
        request_prompt_messages,
        result=result,
        session_id=session_id,
        time_range=time_range,
        participants=participants,
        log_prefix=log_prefix,
    )
    summary_payload = _parse_summary_response(result.response)
    if summary_payload is None:
        logger.warning(f"{log_prefix} 聊天回想解析失败，已跳过本次插入: response={_truncate(result.response, 300)}")
        return None

    recall_cue_embeddings = await _build_recall_cue_embeddings(
        summary_payload.recall_cues,
        session_id=session_id,
    )
    message = build_mid_term_memory_complex_message(
        summary_payload,
        time_range=time_range,
        participants=participants,
        source_messages=summary_source_messages,
        recall_cue_embeddings=recall_cue_embeddings,
    )
    logger.info(
        f"{log_prefix} 聊天回想生成内容: "
        f"msg_id={message.message_id} "
        f"时间范围={time_range} "
        f"参与人物={'、'.join(participants) if participants else '未知'} "
        f"召回线索={len(recall_cue_embeddings)} 条\n"
        f"summary:\n{summary_payload.summary.strip()}"
    )
    return MidTermMemoryBuildResult(
        message=message,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        model_name=result.model_name or "",
    )


def _select_summary_source_messages(messages: Sequence[LLMContextMessage]) -> list[LLMContextMessage]:
    """筛选真正参与聊天回想生成的历史消息。"""

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
    recall_cue_embeddings: Sequence[dict[str, Any]] | None = None,
) -> ComplexSessionMessage:
    """基于摘要内容构造聊天回想上下文消息。"""

    timestamp = _resolve_summary_timestamp(source_messages)
    participants_text = "、".join(participants) if participants else "未知"
    message_id = _build_summary_message_id(
        timestamp=timestamp,
        time_range=time_range,
        participants=participants,
        summary=summary_payload.summary,
    )
    payload = {
        "type": MID_TERM_MEMORY_COMPONENT_TYPE,
        "data": {
            "time_range": time_range,
            "participants": list(participants),
            "summary": summary_payload.summary.strip(),
            "recall_cues": list(recall_cue_embeddings or []),
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
            f"summary: {summary_payload.summary.strip()}",
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
    """将新的聊天回想插入到上一条聊天回想之后，并维护最大保留数量。"""

    if max_summary_count <= 0:
        return [message for message in history if not is_mid_term_memory_message(message)]

    updated_history = list(history)
    insert_index = _find_last_mid_term_memory_index(updated_history)
    updated_history.insert(insert_index + 1, summary_message)
    _trim_mid_term_memory_messages(updated_history, max_summary_count=max_summary_count)
    return updated_history


def build_mid_term_memory_preview_text(payload: dict[str, Any]) -> str:
    """构造聊天回想在 Prompt 中未展开时可见的内容。"""

    time_range = str(payload.get("time_range") or "未知").strip()
    participants = _coerce_str_list(payload.get("participants"))
    summary = _resolve_payload_summary(payload) or "无"
    return "\n".join(
        [
            "[聊天回想]",
            f"时间范围: {time_range}",
            f"参与人物: {'、'.join(participants) if participants else '未知'}",
            f"summary: {summary}",
        ]
    )


def build_mid_term_memory_full_text(payload: dict[str, Any]) -> str:
    """构造聊天回想的完整内容。"""

    time_range = str(payload.get("time_range") or "未知").strip()
    participants = _coerce_str_list(payload.get("participants"))
    summary = _resolve_payload_summary(payload) or "无"
    recall_cues = _extract_recall_cue_texts(payload)
    recall_cue_lines = ["召回线索:"] + [f"- {cue}" for cue in recall_cues]
    return "\n".join(
        [
            "【聊天回想】",
            f"时间范围: {time_range}",
            f"参与人物: {'、'.join(participants) if participants else '未知'}",
            "",
            "summary:",
            summary,
            "",
            *(recall_cue_lines if recall_cues else []),
        ]
    ).strip()


async def build_mid_term_memory_reference_message(
    *,
    history: Sequence[LLMContextMessage],
    selected_history: Sequence[LLMContextMessage],
    session_id: str,
    log_prefix: str = "",
) -> ReferenceMessage | None:
    """基于当前 Planner 上下文召回最相关的一条聊天回想。"""

    if not bool(global_config.chat.mid_term_memory):
        return None

    query_text = _build_mid_term_memory_recall_query_text(selected_history)
    if not query_text:
        return None

    candidates = _collect_mid_term_memory_recall_candidates(history)
    recalled_keys, recalled_segments = _collect_recalled_mid_term_memory_reference_identities(selected_history)
    candidates = [
        candidate
        for candidate in candidates
        if not _is_mid_term_memory_candidate_already_recalled(
            candidate,
            recalled_keys=recalled_keys,
            recalled_segments=recalled_segments,
        )
    ]
    if not candidates:
        if recalled_keys or recalled_segments:
            logger.debug(f"{log_prefix} 当前上下文已包含全部匹配的聊天回想参考，跳过重复召回")
        return None

    from src.services.embedding_service import EmbeddingServiceClient

    embedding_client = EmbeddingServiceClient(
        task_name="embedding",
        request_type="maisaka.mid_term_memory_recall",
        session_id=session_id,
    )
    query_result = await embedding_client.embed_text(query_text, session_id=session_id)
    best_candidate = _select_best_recall_candidate(
        candidates,
        query_embedding=query_result.embedding,
        threshold=MID_TERM_MEMORY_DEFAULT_RECALL_THRESHOLD,
    )
    if best_candidate is None:
        logger.debug(f"{log_prefix} 聊天回想召回未命中阈值")
        return None

    logger.info(
        f"{log_prefix} 聊天回想召回命中: "
        f"msg_id={best_candidate.message.message_id} "
        f"score={best_candidate.score:.4f} "
        f"segment={_truncate(best_candidate.segment_text, 120)}"
    )
    return ReferenceMessage(
        content=_format_mid_term_memory_reference(best_candidate),
        timestamp=datetime.now(),
        reference_type=ReferenceMessageType.MEMORY,
        remaining_uses_value=None,
        display_prefix="[参考消息]",
    )


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


def _save_mid_term_memory_prompt_preview(
    request_prompt_messages: Sequence[Message],
    *,
    result: Any,
    session_id: str,
    time_range: str,
    participants: Sequence[str],
    log_prefix: str,
) -> None:
    """保存聊天回想生成 Prompt 到 Maisaka Prompt 预览目录。"""

    if not bool(getattr(global_config.debug, "show_maisaka_thinking", False)):
        return
    if not request_prompt_messages:
        return

    participants_text = "、".join(participants) if participants else "未知"
    selection_reason = (
        f"会话ID: {session_id or 'unknown'}\n"
        f"时间范围: {time_range}\n"
        f"参与人物: {participants_text}\n"
        f"构建消息数: {len(request_prompt_messages)}\n"
        f"请求模型: {str(getattr(result, 'model_name', '') or 'unknown')}\n"
        f"Token: prompt={int(getattr(result, 'prompt_tokens', 0) or 0)} "
        f"completion={int(getattr(result, 'completion_tokens', 0) or 0)} "
        f"total={int(getattr(result, 'total_tokens', 0) or 0)}"
    )
    try:
        PromptCLIVisualizer.build_prompt_preview_access(
            list(request_prompt_messages),
            category="mid_term_memory",
            chat_id=session_id or "unknown",
            request_kind="mid_term_memory",
            selection_reason=selection_reason,
            output_content=str(getattr(result, "response", "") or ""),
            output_title="聊天回想生成结果",
            metadata={
                "model_name": str(getattr(result, "model_name", "") or ""),
            },
        )
        logger.debug(f"{log_prefix} 聊天回想生成 Prompt 预览已保存")
    except Exception as exc:
        logger.debug(f"{log_prefix} 聊天回想生成 Prompt 预览保存失败，已跳过: {exc}")


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

    summary = _resolve_payload_summary(payload)
    recall_cues = _normalize_recall_cues(payload.get("recall_cues") or payload.get("match_segments"))
    if not summary:
        return None
    return MidTermMemorySummaryModel(
        summary=summary,
        recall_cues=recall_cues,
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


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized_values: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized_values.append(text)
    return normalized_values


async def _build_recall_cue_embeddings(
    recall_cues: Sequence[str],
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    normalized_cues = _normalize_recall_cues(list(recall_cues))
    if not normalized_cues:
        return []

    from src.services.embedding_service import EmbeddingServiceClient

    embedding_client = EmbeddingServiceClient(
        task_name="embedding",
        request_type="maisaka.mid_term_memory_recall_cue",
        session_id=session_id,
    )
    embedding_results = await embedding_client.embed_texts(
        normalized_cues,
        max_concurrent=2,
        session_id=session_id,
    )
    return [
        _build_recall_cue_payload(cue, embedding_result)
        for cue, embedding_result in zip(normalized_cues, embedding_results, strict=True)
    ]


def _build_recall_cue_payload(cue: str, embedding_result: EmbeddingResult) -> dict[str, Any]:
    return {
        "text": cue,
        "embedding": [float(value) for value in embedding_result.embedding],
        "model_name": embedding_result.model_name,
    }


def _normalize_recall_cues(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_cues = re.split(r"\n+", value)
    elif isinstance(value, list):
        raw_cues = value
    else:
        raw_cues = []

    cues: list[str] = []
    seen: set[str] = set()
    for raw_cue in raw_cues:
        cue = _normalize_recall_cue_text(raw_cue)
        if not cue or cue in seen:
            continue
        seen.add(cue)
        cues.append(cue)
    return cues[:5]


def _normalize_recall_cue_text(value: Any) -> str:
    if isinstance(value, dict):
        text = value.get("text") or value.get("query") or value.get("content") or ""
        associations = _coerce_str_list(value.get("associations"))
        reason = str(value.get("reason") or "").strip()
        parts = [str(text or "").strip()]
        if associations:
            parts.append(f"关联: {'、'.join(associations)}")
        if reason:
            parts.append(f"原因: {reason}")
        return "；".join(part for part in parts if part)
    return " ".join(str(value or "").split()).strip()


def _get_mid_term_memory_payload(message: LLMContextMessage) -> dict[str, Any]:
    if not is_mid_term_memory_message(message):
        return {}

    raw_message = getattr(message, "raw_message", None)
    for component in getattr(raw_message, "components", []) or []:
        if not isinstance(component, DictComponent) or not isinstance(component.data, dict):
            continue
        raw_type = str(component.data.get("type") or "").strip()
        if raw_type != MID_TERM_MEMORY_COMPONENT_TYPE:
            continue
        payload = component.data.get("data", {})
        return payload if isinstance(payload, dict) else {}
    return {}


def _resolve_payload_summary(payload: dict[str, Any]) -> str:
    return str(payload.get("summary") or payload.get("long_summary") or payload.get("brief") or "").strip()


def _extract_recall_cue_texts(payload: dict[str, Any]) -> list[str]:
    return _normalize_recall_cues(payload.get("recall_cues") or payload.get("match_segments"))


def _collect_mid_term_memory_recall_candidates(
    history: Sequence[LLMContextMessage],
) -> list[MidTermMemoryRecallCandidate]:
    candidates: list[MidTermMemoryRecallCandidate] = []
    for message in history:
        if not isinstance(message, ComplexSessionMessage) or not is_mid_term_memory_message(message):
            continue

        payload = _get_mid_term_memory_payload(message)
        for cue_payload in _iter_recall_cue_payloads(payload):
            if not isinstance(cue_payload, dict):
                continue
            cue_text = str(cue_payload.get("text") or "").strip()
            embedding = cue_payload.get("embedding")
            if not cue_text or not isinstance(embedding, list) or not embedding:
                continue
            candidates.append(
                MidTermMemoryRecallCandidate(
                    message=message,
                    payload=payload,
                    segment_text=cue_text,
                    score=0.0,
                )
            )
    return candidates


def _iter_recall_cue_payloads(payload: dict[str, Any]) -> list[Any]:
    recall_cues = payload.get("recall_cues")
    if isinstance(recall_cues, list) and recall_cues:
        return recall_cues
    match_segments = payload.get("match_segments")
    return match_segments if isinstance(match_segments, list) else []


def _collect_recalled_mid_term_memory_reference_identities(
    messages: Sequence[LLMContextMessage],
) -> tuple[set[tuple[str, str]], set[str]]:
    recalled_keys: set[tuple[str, str]] = set()
    recalled_segments: set[str] = set()
    for message in messages:
        if not is_mid_term_memory_reference_message(message):
            continue

        message_id = _extract_labeled_reference_value(message.content, "摘要ID")
        segment_text = _extract_labeled_reference_value(message.content, "匹配段")
        normalized_segment = _normalize_reference_identity_text(segment_text)
        if not normalized_segment:
            continue

        recalled_segments.add(normalized_segment)
        normalized_message_id = _normalize_reference_identity_text(message_id)
        if normalized_message_id:
            recalled_keys.add((normalized_message_id, normalized_segment))
    return recalled_keys, recalled_segments


def _is_mid_term_memory_candidate_already_recalled(
    candidate: MidTermMemoryRecallCandidate,
    *,
    recalled_keys: set[tuple[str, str]],
    recalled_segments: set[str],
) -> bool:
    normalized_segment = _normalize_reference_identity_text(candidate.segment_text)
    if not normalized_segment:
        return False

    message_id = _normalize_reference_identity_text(candidate.message.message_id)
    if message_id and (message_id, normalized_segment) in recalled_keys:
        return True
    return normalized_segment in recalled_segments


def _extract_labeled_reference_value(content: str, label: str) -> str:
    prefix = f"{label}:"
    for line in str(content or "").splitlines():
        normalized_line = line.strip()
        if normalized_line.startswith(prefix):
            return normalized_line.removeprefix(prefix).strip()
    return ""


def _normalize_reference_identity_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _select_best_recall_candidate(
    candidates: Sequence[MidTermMemoryRecallCandidate],
    *,
    query_embedding: Sequence[float],
    threshold: float,
) -> MidTermMemoryRecallCandidate | None:
    best_candidate: MidTermMemoryRecallCandidate | None = None
    for candidate in candidates:
        segment_embedding = _get_candidate_embedding(candidate.payload, candidate.segment_text)
        if not segment_embedding:
            continue
        score = _cosine_similarity(query_embedding, segment_embedding)
        scored_candidate = MidTermMemoryRecallCandidate(
            message=candidate.message,
            payload=candidate.payload,
            segment_text=candidate.segment_text,
            score=score,
        )
        if best_candidate is None or scored_candidate.score > best_candidate.score:
            best_candidate = scored_candidate

    if best_candidate is None or best_candidate.score <= threshold:
        return None
    return best_candidate


def _get_candidate_embedding(payload: dict[str, Any], segment_text: str) -> list[float]:
    for cue_payload in _iter_recall_cue_payloads(payload):
        if not isinstance(cue_payload, dict):
            continue
        if str(cue_payload.get("text") or "").strip() != segment_text:
            continue
        embedding = cue_payload.get("embedding")
        if not isinstance(embedding, list):
            return []
        return [float(value) for value in embedding]
    return []


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError(f"聊天回想召回 embedding 维度不一致: query={len(left)} segment={len(right)}")
    if not left:
        return 0.0

    dot_product = sum(
        float(left_value) * float(right_value) for left_value, right_value in zip(left, right, strict=True)
    )
    left_norm = sqrt(sum(float(value) * float(value) for value in left))
    right_norm = sqrt(sum(float(value) * float(value) for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def _build_mid_term_memory_recall_query_text(selected_history: Sequence[LLMContextMessage]) -> str:
    query_items: list[str] = []
    for message in selected_history:
        if is_mid_term_memory_message(message) or is_mid_term_memory_reference_message(message):
            continue
        if isinstance(message, ReferenceMessage):
            continue

        text = " ".join(str(message.processed_plain_text or "").split()).strip()
        if not text:
            continue
        if len(text) > 360:
            text = text[-360:].strip()
        query_items.append(text)

    query_text = "\n".join(query_items[-MID_TERM_MEMORY_RECALL_CONTEXT_MESSAGE_LIMIT:])
    if len(query_text) <= MID_TERM_MEMORY_RECALL_CONTEXT_TEXT_LIMIT:
        return query_text
    return query_text[-MID_TERM_MEMORY_RECALL_CONTEXT_TEXT_LIMIT:]


def _format_mid_term_memory_reference(candidate: MidTermMemoryRecallCandidate) -> str:
    payload = candidate.payload
    message_id = str(candidate.message.message_id or "").strip()
    time_range = str(payload.get("time_range") or "未知").strip()
    participants = _coerce_str_list(payload.get("participants"))
    summary = _resolve_payload_summary(payload) or "无"
    if len(summary) > MID_TERM_MEMORY_RECALL_SUMMARY_TEXT_LIMIT:
        summary = summary[:MID_TERM_MEMORY_RECALL_SUMMARY_TEXT_LIMIT].rstrip() + "..."

    return "\n".join(
        [
            MID_TERM_MEMORY_REFERENCE_MARKER,
            "以下是根据当前上下文匹配到的一条聊天回想，只作为内部参考；仅在自然相关时使用，不要生硬复述。",
            *([f"摘要ID: {message_id}"] if message_id else []),
            f"匹配分数: {candidate.score:.4f}",
            f"匹配段: {candidate.segment_text}",
            f"时间范围: {time_range}",
            f"参与人物: {'、'.join(participants) if participants else '未知'}",
            "",
            "summary:",
            summary,
        ]
    ).strip()


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
    summary: str,
) -> str:
    digest_source = "\n".join([time_range, "、".join(participants), summary])
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
