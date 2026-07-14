"""工具执行后的通用后处理工具。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.common.logger import get_logger
from src.core.tooling import ToolExecutionResult, ToolInvocation
from src.maisaka.utils.tool_record_payload import normalize_tool_record_value
from src.services.memory_service import memory_service

logger = get_logger("maisaka_tool_post_execution")

_MEMORY_FEEDBACK_TASK_METADATA_KEY = "memory_feedback_task"


def with_memory_feedback_task(metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """为工具结果 metadata 标记需要创建记忆反馈任务。"""

    marked_metadata = dict(metadata or {})
    marked_metadata[_MEMORY_FEEDBACK_TASK_METADATA_KEY] = {"enabled": True}
    return marked_metadata


async def handle_tool_post_execution_effects(
    *,
    invocation: ToolInvocation,
    result: ToolExecutionResult,
    saved_record: Optional[dict[str, Any]],
    chat_stream: Any,
    log_prefix: str,
) -> None:
    """处理工具执行后的非落库副作用。"""

    memory_feedback_task = result.metadata.get(_MEMORY_FEEDBACK_TASK_METADATA_KEY)
    if isinstance(memory_feedback_task, dict) and bool(memory_feedback_task.get("enabled")):
        await _enqueue_memory_feedback_task(
            invocation=invocation,
            result=result,
            saved_record=saved_record,
            chat_stream=chat_stream,
            log_prefix=log_prefix,
        )


async def _enqueue_memory_feedback_task(
    *,
    invocation: ToolInvocation,
    result: ToolExecutionResult,
    saved_record: Optional[dict[str, Any]],
    chat_stream: Any,
    log_prefix: str,
) -> None:
    """为声明了反馈任务的记忆检索结果创建反馈纠错任务。"""

    if saved_record is None:
        return
    if chat_stream is None:
        return

    try:
        normalized_structured_content = normalize_tool_record_value(result.structured_content)
        enqueue_payload = await memory_service.enqueue_feedback_task(
            query_tool_id=str(saved_record.get("tool_id") or invocation.call_id or "").strip(),
            session_id=str(saved_record.get("session_id") or chat_stream.session_id or "").strip(),
            query_timestamp=saved_record.get("timestamp"),
            structured_content=normalized_structured_content if isinstance(normalized_structured_content, dict) else {},
        )
    except Exception:
        logger.exception(f"{log_prefix} 反馈纠错任务入队失败: tool_call_id={invocation.call_id}")
    else:
        if not bool(enqueue_payload.get("success")):
            logger.debug(
                f"{log_prefix} 反馈纠错任务未入队: "
                f"tool_call_id={invocation.call_id} reason={enqueue_payload.get('reason', '')}"
            )
