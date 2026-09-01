"""工具调用记录落库数据构造工具。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.config.config import global_config
from src.core.tooling import ToolExecutionResult, ToolInvocation, ToolSpec


def normalize_tool_record_value(value: Any) -> Any:
    """将工具记录值规范化为可 JSON 序列化的结构。"""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        normalized_dict: dict[str, Any] = {}
        for key, item in value.items():
            normalized_dict[str(key)] = normalize_tool_record_value(item)
        return normalized_dict
    if isinstance(value, (list, tuple, set)):
        return [normalize_tool_record_value(item) for item in value]
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if hasattr(value, "model_dump"):
        try:
            return normalize_tool_record_value(value.model_dump())
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        try:
            return normalize_tool_record_value(dict(value.__dict__))
        except Exception:
            return str(value)
    return str(value)


def _omit_tool_record_large_media(value: Any) -> Any:
    """移除工具记录落库数据中的内联媒体大字段。"""

    media_keys = {"base64", "data", "image_base64", "audio_base64", "emoji_base64"}
    if isinstance(value, dict):
        omitted: dict[str, Any] = {}
        content_type = str(value.get("content_type") or "").strip().lower()
        mime_type = str(value.get("mime_type") or "").strip()
        image_format = str(value.get("image_format") or value.get("format") or "").strip()
        for key, item in value.items():
            normalized_key = str(key)
            if normalized_key in media_keys and isinstance(item, str) and _is_large_inline_media_text(item):
                omitted[normalized_key] = _build_omitted_media_marker(
                    item,
                    content_type=content_type,
                    mime_type=mime_type,
                    image_format=image_format,
                )
                continue
            omitted[normalized_key] = _omit_tool_record_large_media(item)
        return omitted

    if isinstance(value, list):
        return [_omit_tool_record_large_media(item) for item in value]

    if isinstance(value, str) and _is_large_inline_media_text(value):
        return _build_omitted_media_marker(value)
    return value


def _is_large_inline_media_text(value: str) -> bool:
    if len(value) <= 1024:
        return False
    normalized_value = value.strip()
    if normalized_value.startswith("data:image/") or normalized_value.startswith("data:audio/"):
        return True
    if len(normalized_value) < 4096:
        return False
    base64_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\r\n")
    sample = normalized_value[:4096]
    return all(char in base64_chars for char in sample)


def _build_omitted_media_marker(
    value: str,
    *,
    content_type: str = "",
    mime_type: str = "",
    image_format: str = "",
) -> dict[str, Any]:
    approx_size = max(0, len(value) * 3 // 4)
    marker: dict[str, Any] = {
        "base64_omitted": True,
        "original_length": len(value),
        "approx_bytes": approx_size,
    }
    if content_type:
        marker["content_type"] = content_type
    if mime_type:
        marker["mime_type"] = mime_type
    if image_format:
        marker["image_format"] = image_format
    return marker


def _build_omitted_structured_content_marker(value: Any) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "structured_content_omitted": True,
        "reason": "debug.record_tool_structured_content=false",
        "value_type": type(value).__name__,
    }
    if isinstance(value, dict):
        marker["top_level_keys"] = [str(key) for key in value.keys()]
    elif isinstance(value, (list, tuple, set)):
        marker["item_count"] = len(value)
    return marker


def build_tool_record_structured_content(value: Any) -> Any:
    """构造工具记录中的 structured_content 字段。"""

    if value is None:
        return None
    normalized_value = normalize_tool_record_value(value)
    if not global_config.debug.record_tool_structured_content:
        return _build_omitted_structured_content_marker(normalized_value)
    return _omit_tool_record_large_media(normalized_value)


def build_tool_record_payload(
    invocation: ToolInvocation,
    result: ToolExecutionResult,
    tool_spec: Optional[ToolSpec],
) -> dict[str, Any]:
    """构造统一工具落库数据。"""

    payload: dict[str, Any] = {
        "call_id": invocation.call_id,
        "session_id": invocation.session_id,
        "stream_id": invocation.stream_id,
        "arguments": normalize_tool_record_value(invocation.arguments),
        "success": result.success,
        "content": result.content,
        "error_message": result.error_message,
        "history_content": result.get_history_content(),
        "structured_content": build_tool_record_structured_content(result.structured_content),
        "metadata": normalize_tool_record_value(result.metadata),
        "stop_after_execution": result.stop_after_execution,
    }
    if tool_spec is not None:
        payload["provider_name"] = tool_spec.provider_name
        payload["provider_type"] = tool_spec.provider_type
        payload["description"] = tool_spec.description
        payload["title"] = tool_spec.title
    return payload
