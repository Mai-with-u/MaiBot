"""MaiSaka 实时监控事件广播模块。

通过统一 WebSocket 将 MaiSaka 推理引擎各阶段状态实时推送给前端监控界面。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import asyncio
import json
import time

from src.common.logger import get_logger
from src.maisaka.display.display_utils import format_tool_call_for_display

logger = get_logger("maisaka_monitor")

MONITOR_DOMAIN = "maisaka_monitor"
MONITOR_TOPIC = "main"
NON_PERSISTED_EVENTS = {"stage.status", "stage.removed", "stage.snapshot"}


def _normalize_payload_value(value: Any) -> Any:
    """将事件载荷中的任意值规范化为可序列化结构。"""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        normalized_dict: Dict[str, Any] = {}
        for key, item in value.items():
            normalized_dict[str(key)] = _normalize_payload_value(item)
        return normalized_dict
    if isinstance(value, (list, tuple, set)):
        return [_normalize_payload_value(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _normalize_payload_value(value.model_dump())
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        try:
            return _normalize_payload_value(dict(value.__dict__))
        except Exception:
            return str(value)
    return str(value)


def _extract_text_content(content: Any) -> Optional[str]:
    """从消息内容中提取纯文本表示。"""

    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block_type == "image_url":
                    text_parts.append("[图片，识别中.....]")
                else:
                    text_parts.append(f"[{block_type}]")
            elif isinstance(block, str):
                text_parts.append(block)
        return "\n".join(text_parts) if text_parts else None
    return str(content)


def _normalize_tool_call_arguments(arguments: Any) -> tuple[Any, Optional[str]]:
    """标准化工具调用参数，兼容 JSON 字符串和对象。"""

    if isinstance(arguments, str):
        raw_arguments = arguments
        try:
            parsed_arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return {}, raw_arguments
        return _normalize_payload_value(parsed_arguments), raw_arguments
    return _normalize_payload_value(arguments or {}), None


def _serialize_single_tool_call(tool_call: Any) -> Dict[str, Any]:
    """将不同来源的 tool_call 标准化为前端可直接展示的结构。"""

    display_payload = format_tool_call_for_display(tool_call)
    if isinstance(tool_call, dict):
        function_info = tool_call.get("function")
        if isinstance(function_info, dict):
            raw_arguments = function_info.get("arguments", tool_call.get("arguments", tool_call.get("args", {})))
            name = function_info.get("name", tool_call.get("name", tool_call.get("func_name", "unknown")))
        else:
            raw_arguments = tool_call.get("arguments", tool_call.get("args", {}))
            name = tool_call.get("name", tool_call.get("func_name", "unknown"))

        arguments, arguments_raw = _normalize_tool_call_arguments(raw_arguments)
        serialized: Dict[str, Any] = {
            "id": str(tool_call.get("id", tool_call.get("call_id", ""))),
            "name": str(name or "unknown"),
            "arguments": arguments,
        }
        if arguments_raw is not None:
            serialized["arguments_raw"] = arguments_raw
        for key in ("source", "source_label", "extra_content"):
            if display_payload.get(key):
                serialized[key] = display_payload[key]
        return serialized

    raw_arguments = getattr(tool_call, "args", None)
    if raw_arguments is None:
        raw_arguments = getattr(tool_call, "arguments", None)
    arguments, arguments_raw = _normalize_tool_call_arguments(raw_arguments)
    serialized = {
        "id": str(getattr(tool_call, "id", None) or getattr(tool_call, "call_id", "")),
        "name": str(getattr(tool_call, "func_name", None) or getattr(tool_call, "name", "unknown")),
        "arguments": arguments,
    }
    if arguments_raw is not None:
        serialized["arguments_raw"] = arguments_raw
    for key in ("source", "source_label", "extra_content"):
        if display_payload.get(key):
            serialized[key] = display_payload[key]
    return serialized


def _serialize_tool_calls_from_objects(tool_calls: List[Any]) -> List[Dict[str, Any]]:
    """将工具调用对象列表序列化为字典列表。"""

    return [_serialize_single_tool_call(tool_call) for tool_call in tool_calls]


def _serialize_tool_calls_from_dicts(tool_calls: List[Any]) -> List[Dict[str, Any]]:
    """将工具调用字典列表标准化为可传输格式。"""

    return [_serialize_single_tool_call(tool_call) for tool_call in tool_calls]


def _serialize_message(message: Any) -> Dict[str, Any]:
    """将单条消息序列化为可通过 WebSocket 传输的字典。"""

    if isinstance(message, dict):
        serialized: Dict[str, Any] = {
            "role": str(message.get("role", "unknown")),
            "content": _extract_text_content(message.get("content")),
        }
        if message.get("tool_call_id"):
            serialized["tool_call_id"] = str(message["tool_call_id"])
        if message.get("tool_calls"):
            serialized["tool_calls"] = _serialize_tool_calls_from_dicts(message["tool_calls"])
        return serialized

    raw_role = getattr(message, "role", "unknown")
    role_str = raw_role.value if hasattr(raw_role, "value") else str(raw_role)

    serialized = {
        "role": role_str,
        "content": _extract_text_content(getattr(message, "content", None)),
    }
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        serialized["tool_call_id"] = str(tool_call_id)

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        serialized["tool_calls"] = _serialize_tool_calls_from_objects(tool_calls)

    return serialized


def _serialize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    """批量序列化消息列表。"""

    return [_serialize_message(message) for message in messages]


def _enrich_session_identity(data: Dict[str, Any]) -> Dict[str, Any]:
    """为监控事件补充会话展示所需的群/用户标识。"""

    session_id = data.get("session_id")
    if not session_id:
        return data

    try:
        from src.chat.message_receive.chat_manager import chat_manager

        chat_stream = chat_manager.get_session_by_session_id(str(session_id))
    except Exception:
        return data

    if chat_stream is None:
        return data

    session_name = chat_manager.get_session_name(str(session_id))
    if session_name:
        data.setdefault("session_name", session_name)
    data.setdefault("is_group_chat", chat_stream.is_group_session)
    data.setdefault("group_id", chat_stream.group_id)
    data.setdefault("user_id", chat_stream.user_id)
    data.setdefault("platform", chat_stream.platform)
    return data


def _serialize_tool_results(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """标准化最终 planner 卡中的工具结果列表。"""

    serialized_tools: List[Dict[str, Any]] = []
    for tool in tools:
        serialized_tool = {
            "tool_call_id": str(tool.get("tool_call_id", "")),
            "tool_name": str(tool.get("tool_name", "")),
            "tool_args": _normalize_payload_value(tool.get("tool_args", {})),
            "tool_call_source": str(tool.get("tool_call_source", "")),
            "tool_call_source_label": str(tool.get("tool_call_source_label", "")),
            "success": bool(tool.get("success", False)),
            "duration_ms": float(tool.get("duration_ms", 0.0) or 0.0),
            "summary": str(tool.get("summary", "")),
        }
        detail = tool.get("detail")
        prompt_html_uri = str(tool.get("prompt_html_uri") or "").strip()
        if not prompt_html_uri and isinstance(detail, dict):
            prompt_html_uri = str(detail.get("prompt_html_uri") or "").strip()
        if prompt_html_uri:
            serialized_tool["prompt_html_uri"] = prompt_html_uri
        if detail is not None:
            serialized_tool["detail"] = _normalize_payload_value(detail)
        serialized_tools.append(serialized_tool)
    return serialized_tools


def _serialize_request_block(
    messages: Optional[List[Any]],
    selected_history_count: Optional[int],
    tool_count: Optional[int],
) -> Optional[Dict[str, Any]]:
    """标准化请求区块。"""

    if messages is None and selected_history_count is None and tool_count is None:
        return None

    return {
        "messages": _serialize_messages(list(messages or [])),
        "selected_history_count": int(selected_history_count or 0),
        "tool_count": int(tool_count or 0),
    }


def _serialize_planner_block(
    content: Optional[str],
    tool_calls: Optional[List[Any]],
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    total_tokens: Optional[int],
    duration_ms: Optional[float],
    prompt_html_uri: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """标准化 planner 结果区块。"""

    if (
        content is None
        and tool_calls is None
        and prompt_tokens is None
        and completion_tokens is None
        and total_tokens is None
        and duration_ms is None
        and prompt_html_uri is None
    ):
        return None

    return {
        "content": content,
        "tool_calls": _serialize_tool_calls_from_objects(list(tool_calls or [])),
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(total_tokens or 0),
        "duration_ms": float(duration_ms or 0.0),
        "prompt_html_uri": str(prompt_html_uri or ""),
    }


async def _broadcast(event: str, data: Dict[str, Any]) -> None:
    """通过统一 WebSocket 管理器向监控主题广播事件。"""

    try:
        from src.webui.routers.websocket.manager import websocket_manager

        data = _enrich_session_identity(data)
        broadcast_data = data
        if event not in NON_PERSISTED_EVENTS:
            from src.maisaka.monitor.event_store import record_monitor_event

            broadcast_data = await asyncio.to_thread(record_monitor_event, event, data)
        await websocket_manager.broadcast_to_topic(
            domain=MONITOR_DOMAIN,
            topic=MONITOR_TOPIC,
            event=event,
            data=broadcast_data,
        )
    except Exception as exc:
        logger.warning(f"MaiSaka 监控事件广播失败: {exc}", exc_info=True)


async def emit_session_start(
    session_id: str,
    session_name: str,
    *,
    is_group_chat: bool,
    group_id: Optional[str],
    user_id: Optional[str],
    platform: str,
) -> None:
    """广播会话开始事件。"""

    await _broadcast("session.start", {
        "session_id": session_id,
        "session_name": session_name,
        "is_group_chat": is_group_chat,
        "group_id": group_id,
        "user_id": user_id,
        "platform": platform,
        "timestamp": time.time(),
    })


async def emit_stage_status(
    *,
    session_id: str,
    session_name: str,
    stage: str,
    detail: str = "",
    round_text: str = "",
    agent_state: str = "",
    stage_started_at: float,
    updated_at: float,
    timestamp: float,
) -> None:
    """广播单个聊天流的当前阶段状态。"""

    await _broadcast("stage.status", {
        "session_id": session_id,
        "session_name": session_name,
        "stage": stage,
        "detail": detail,
        "round_text": round_text,
        "agent_state": agent_state,
        "stage_started_at": stage_started_at,
        "updated_at": updated_at,
        "timestamp": timestamp,
    })


async def emit_stage_removed(
    *,
    session_id: str,
    session_name: str = "",
) -> None:
    """广播聊天流阶段状态移除事件。"""

    await _broadcast("stage.removed", {
        "session_id": session_id,
        "session_name": session_name,
        "timestamp": time.time(),
    })


async def emit_llm_retry(
    *,
    session_id: str,
    task_name: str,
    request_type: str,
    model_name: str,
    attempt: int,
    max_attempts: int,
    reason: str,
    retry_interval: float,
) -> None:
    """广播模型请求失败后的重试进度。"""

    await _broadcast("llm.retry", {
        "session_id": session_id,
        "task_name": task_name,
        "request_type": request_type,
        "model_name": model_name,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "reason": reason,
        "retry_interval": retry_interval,
        "timestamp": time.time(),
    })


async def emit_llm_error(
    *,
    session_id: str,
    task_name: str,
    request_type: str,
    model_name: str,
    message: str,
) -> None:
    """广播模型请求最终失败。"""

    await _broadcast("llm.error", {
        "session_id": session_id,
        "task_name": task_name,
        "request_type": request_type,
        "model_name": model_name,
        "message": message,
        "timestamp": time.time(),
    })


async def emit_message_ingested(
    session_id: str,
    speaker_name: str,
    content: str,
    message_id: str,
    timestamp: float,
    *,
    platform: str = "",
    user_id: str = "",
    group_id: str = "",
    reply_to: Optional[Dict[str, Any]] = None,
    media: Optional[list[Dict[str, Any]]] = None,
) -> None:
    """广播新消息注入事件。"""

    await _broadcast("message.ingested", {
        "session_id": session_id,
        "speaker_name": speaker_name,
        "content": content,
        "message_id": message_id,
        "platform": platform,
        "user_id": user_id,
        "group_id": group_id,
        "reply_to": _normalize_payload_value(reply_to) if reply_to else None,
        "media": _normalize_payload_value(media) if media else [],
        "timestamp": timestamp,
    })


async def emit_message_sent(
    session_id: str,
    speaker_name: str,
    content: str,
    message_id: str,
    timestamp: float,
    source_kind: str = "",
    *,
    platform: str = "",
    user_id: str = "",
    group_id: str = "",
    reply_to: Optional[Dict[str, Any]] = None,
    media: Optional[list[Dict[str, Any]]] = None,
) -> None:
    """广播 MaiSaka 自己发送的消息事件。"""

    await _broadcast("message.sent", {
        "session_id": session_id,
        "speaker_name": speaker_name,
        "content": content,
        "message_id": message_id,
        "source_kind": source_kind,
        "platform": platform,
        "user_id": user_id,
        "group_id": group_id,
        "reply_to": _normalize_payload_value(reply_to) if reply_to else None,
        "media": _normalize_payload_value(media) if media else [],
        "timestamp": timestamp,
    })


async def emit_message_updated(
    session_id: str,
    speaker_name: str,
    content: str,
    message_id: str,
    timestamp: float,
    source_kind: str = "",
    *,
    platform: str = "",
    user_id: str = "",
    group_id: str = "",
    reply_to: Optional[Dict[str, Any]] = None,
    media: Optional[list[Dict[str, Any]]] = None,
) -> None:
    """广播已有消息内容更新事件。"""

    await _broadcast("message.updated", {
        "session_id": session_id,
        "speaker_name": speaker_name,
        "content": content,
        "message_id": message_id,
        "source_kind": source_kind,
        "platform": platform,
        "user_id": user_id,
        "group_id": group_id,
        "reply_to": _normalize_payload_value(reply_to) if reply_to else None,
        "media": _normalize_payload_value(media) if media else [],
        "timestamp": timestamp,
    })


async def emit_planner_finalized(
    *,
    session_id: str,
    cycle_id: int,
    planner_request_messages: Optional[List[Any]],
    planner_selected_history_count: Optional[int],
    planner_tool_count: Optional[int],
    planner_content: Optional[str],
    planner_tool_calls: Optional[List[Any]],
    planner_prompt_tokens: Optional[int],
    planner_completion_tokens: Optional[int],
    planner_total_tokens: Optional[int],
    planner_duration_ms: Optional[float],
    planner_prompt_html_uri: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    time_records: Optional[Dict[str, float]] = None,
    agent_state: str = "",
    planner_interrupted: bool = False,
    end_reason: str = "",
    end_detail: str = "",
) -> None:
    """广播一轮 planner 结束后的最终聚合事件。"""

    await _broadcast("planner.finalized", {
        "session_id": session_id,
        "cycle_id": cycle_id,
        "timestamp": time.time(),
        "request": _serialize_request_block(
            planner_request_messages,
            planner_selected_history_count,
            planner_tool_count,
        ),
        "planner": _serialize_planner_block(
            planner_content,
            planner_tool_calls,
            planner_prompt_tokens,
            planner_completion_tokens,
            planner_total_tokens,
            planner_duration_ms,
            planner_prompt_html_uri,
        ),
        "tools": _serialize_tool_results(list(tools or [])),
        "interrupted": planner_interrupted,
        "final_state": {
            "time_records": _normalize_payload_value(time_records or {}),
            "agent_state": agent_state,
            "end_reason": end_reason,
            "end_detail": end_detail,
        },
    })
