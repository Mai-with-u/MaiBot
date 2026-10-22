"""Context Item 的纯数据快照，不加载模型客户端或请求日志。"""

from datetime import datetime
from typing import Any, Sequence

from .context_item import (
    AssistantMessageItem, ContextImagePart, ContextItem, ContextItemMeta, ContextRefusalPart, ContextTextPart,
    ContextToolCall, FunctionCallItem, FunctionCallOutputItem, ProviderActivityItem, ProviderOpaqueItem,
    ReasoningItem, ReasoningRepresentation, SystemMessageItem, UserMessageItem,
)


def _serialize_item_meta(meta: ContextItemMeta) -> dict[str, Any]:
    """序列化 Item 关系元数据。"""

    return {
        "item_id": meta.item_id,
        "logical_turn_id": meta.logical_turn_id,
        "timestamp": meta.timestamp.isoformat(),
    }


def _strip_private_provider_fields(value: Any) -> Any:
    """从持久化/Hook 投影中移除只能用于当前进程原生续接的字段。"""

    if isinstance(value, dict):
        return {
            str(key): _strip_private_provider_fields(item)
            for key, item in value.items()
            if str(key).strip().lower().replace("-", "_") not in {"encrypted_content", "thought_signature"}
        }
    if isinstance(value, list):
        return [_strip_private_provider_fields(item) for item in value]
    return value


def _deserialize_item_meta(raw_meta: Any) -> ContextItemMeta:
    """从快照恢复 Item 关系元数据。"""

    if not isinstance(raw_meta, dict):
        raise ValueError("快照中的 item.meta 必须是字典")
    item_id = raw_meta.get("item_id")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("快照中的 item.meta.item_id 必须是非空字符串")
    if "logical_turn_id" not in raw_meta:
        raise ValueError("快照中的 item.meta 缺少 logical_turn_id")
    logical_turn_id = raw_meta.get("logical_turn_id")
    if logical_turn_id is not None and (not isinstance(logical_turn_id, str) or not logical_turn_id.strip()):
        raise ValueError("快照中的 item.meta.logical_turn_id 必须是非空字符串或 null")
    raw_timestamp = raw_meta.get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        raise ValueError("快照中的 item.meta.timestamp 必须是非空字符串")
    try:
        timestamp = datetime.fromisoformat(raw_timestamp)
    except ValueError as exc:
        raise ValueError("快照中的 item.meta.timestamp 不是合法 ISO 时间") from exc
    return ContextItemMeta(
        item_id=item_id.strip(),
        logical_turn_id=logical_turn_id,
        timestamp=timestamp,
    )


def _deserialize_string_tuple(raw_value: Any, field_name: str) -> tuple[str, ...]:
    """严格恢复字符串数组字段。"""

    if not isinstance(raw_value, list) or any(not isinstance(item, str) for item in raw_value):
        raise ValueError(f"快照中的 {field_name} 必须是字符串列表")
    return tuple(raw_value)


def _serialize_content_parts(parts: Sequence[Any]) -> list[dict[str, Any]]:
    """序列化规范化内容片段。"""

    parts_payload: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, ContextTextPart):
            parts_payload.append({"type": "text", "text": part.text})
            continue
        if isinstance(part, ContextImagePart):
            parts_payload.append(
                {
                    "type": "image",
                    "image_base64": part.image_base64,
                    "image_format": part.image_format,
                }
            )
        if isinstance(part, ContextRefusalPart):
            parts_payload.append({"type": "refusal", "refusal": part.refusal})
    return parts_payload


def _deserialize_content_parts(raw_parts: Any) -> tuple[ContextTextPart | ContextImagePart | ContextRefusalPart, ...]:
    """从快照恢复规范化内容片段。"""

    if not isinstance(raw_parts, list):
        raise ValueError("快照中的 item.parts 必须是列表")
    parts: list[ContextTextPart | ContextImagePart | ContextRefusalPart] = []
    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            raise ValueError("快照中的 item part 必须是字典")
        part_type = str(raw_part.get("type", "")).strip().lower()
        if part_type == "text":
            text = raw_part.get("text")
            if not isinstance(text, str):
                raise ValueError("文本 part 缺少 text 字段")
            parts.append(ContextTextPart(text))
            continue
        if part_type == "image":
            image_format = raw_part.get("image_format")
            image_base64 = raw_part.get("image_base64")
            if not isinstance(image_format, str) or not isinstance(image_base64, str):
                raise ValueError("图片 part 缺少 image_format 或 image_base64")
            parts.append(ContextImagePart(image_format=image_format, image_base64=image_base64))
            continue
        if part_type == "refusal":
            refusal = raw_part.get("refusal")
            if not isinstance(refusal, str):
                raise ValueError("拒答 part 缺少 refusal 字段")
            parts.append(ContextRefusalPart(refusal))
            continue
        raise ValueError(f"不支持的快照 Item part 类型: {part_type}")
    return tuple(parts)


def serialize_context_item_snapshot(item: ContextItem) -> dict[str, Any]:
    """序列化单个 Context Item；replay fragment 明确不进入快照。"""

    payload: dict[str, Any] = {
        "item_type": item.__class__.__name__,
        "meta": _serialize_item_meta(item.meta),
    }
    if isinstance(item, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
        payload["parts"] = _serialize_content_parts(item.parts)
        if isinstance(item, AssistantMessageItem) and item.phase is not None:
            payload["phase"] = item.phase
    elif isinstance(item, ReasoningItem):
        payload.update(
            {
                "representation": item.representation.value,
                "summary_parts": list(item.summary_parts),
                "text_parts": list(item.text_parts),
            }
        )
    elif isinstance(item, FunctionCallItem):
        payload["tool_call"] = {
            "call_id": item.tool_call.call_id,
            "func_name": item.tool_call.func_name,
            "args": item.tool_call.materialize_args(),
            "extra_content": _strip_private_provider_fields(item.tool_call.materialize_extra_content()),
        }
    elif isinstance(item, FunctionCallOutputItem):
        payload.update(
            {
                "call_id": item.call_id,
                "output": item.output,
                "success": item.success,
                "tool_name": item.tool_name,
            }
        )
    elif isinstance(item, ProviderActivityItem):
        payload.update(
            {
                "action_type": item.action_type,
                "call_id": item.call_id,
                "details": list(item.details),
                "display_summary": item.display_summary,
                "provider_type": item.provider_type,
                "source_count": item.source_count,
                "status": item.status,
            }
        )
    elif isinstance(item, ProviderOpaqueItem):
        payload.update(
            {
                "display_summary": item.display_summary,
                "provider_type": item.provider_type,
            }
        )
    return payload


def deserialize_context_item_snapshot(raw_item: Any) -> ContextItem:
    """从快照恢复单个无 replay 的 Context Item。"""

    if not isinstance(raw_item, dict):
        raise ValueError("快照中的 Context Item 必须是字典")
    item_type = raw_item.get("item_type")
    if not isinstance(item_type, str) or not item_type:
        raise ValueError("快照中的 item_type 必须是非空字符串")
    meta = _deserialize_item_meta(raw_item.get("meta"))
    if item_type == "SystemMessageItem":
        return SystemMessageItem(meta=meta, parts=_deserialize_content_parts(raw_item.get("parts")))
    if item_type == "UserMessageItem":
        return UserMessageItem(meta=meta, parts=_deserialize_content_parts(raw_item.get("parts")))
    if item_type == "AssistantMessageItem":
        phase = raw_item.get("phase")
        if phase is not None and not isinstance(phase, str):
            raise ValueError("AssistantMessageItem phase 必须是字符串或 null")
        return AssistantMessageItem(
            meta=meta,
            parts=_deserialize_content_parts(raw_item.get("parts")),
            phase=phase,
        )
    if item_type == "ReasoningItem":
        return ReasoningItem(
            meta=meta,
            summary_parts=_deserialize_string_tuple(raw_item.get("summary_parts"), "summary_parts"),
            text_parts=_deserialize_string_tuple(raw_item.get("text_parts"), "text_parts"),
            representation=ReasoningRepresentation(raw_item.get("representation")),
        )
    if item_type == "FunctionCallItem":
        raw_tool_call = raw_item.get("tool_call")
        if not isinstance(raw_tool_call, dict):
            raise ValueError("FunctionCallItem 快照缺少 tool_call")
        call_id = raw_tool_call.get("call_id")
        func_name = raw_tool_call.get("func_name")
        args = raw_tool_call.get("args")
        extra_content = raw_tool_call.get("extra_content")
        if not isinstance(call_id, str) or not isinstance(func_name, str):
            raise ValueError("FunctionCallItem tool_call 缺少字符串 call_id 或 func_name")
        if not isinstance(args, dict):
            raise ValueError("FunctionCallItem tool_call.args 必须是字典")
        if extra_content is not None and not isinstance(extra_content, dict):
            raise ValueError("FunctionCallItem tool_call.extra_content 必须是字典或 null")
        return FunctionCallItem(
            meta=meta,
            tool_call=ContextToolCall.create(
                call_id=call_id,
                func_name=func_name,
                args=args,
                extra_content=extra_content,
            ),
        )
    if item_type == "FunctionCallOutputItem":
        call_id = raw_item.get("call_id")
        output = raw_item.get("output")
        tool_name = raw_item.get("tool_name")
        success = raw_item.get("success")
        if not isinstance(call_id, str) or not isinstance(output, str) or not isinstance(tool_name, str):
            raise ValueError("FunctionCallOutputItem call_id、output、tool_name 必须是字符串")
        if not isinstance(success, bool):
            raise ValueError("FunctionCallOutputItem success 必须是布尔值")
        return FunctionCallOutputItem(
            meta=meta,
            call_id=call_id,
            output=output,
            tool_name=tool_name,
            success=success,
        )
    if item_type == "ProviderActivityItem":
        provider_type = raw_item.get("provider_type")
        call_id = raw_item.get("call_id")
        status = raw_item.get("status")
        display_summary = raw_item.get("display_summary")
        action_type = raw_item.get("action_type")
        source_count = raw_item.get("source_count")
        if not all(isinstance(value, str) for value in (provider_type, call_id, status, display_summary, action_type)):
            raise ValueError("ProviderActivityItem 文本字段类型不合法")
        if not isinstance(source_count, int) or isinstance(source_count, bool):
            raise ValueError("ProviderActivityItem source_count 必须是整数")
        return ProviderActivityItem(
            meta=meta,
            provider_type=provider_type,
            call_id=call_id,
            status=status,
            display_summary=display_summary,
            action_type=action_type,
            details=_deserialize_string_tuple(raw_item.get("details"), "details"),
            source_count=source_count,
        )
    if item_type == "ProviderOpaqueItem":
        provider_type = raw_item.get("provider_type")
        display_summary = raw_item.get("display_summary")
        if not isinstance(provider_type, str) or not isinstance(display_summary, str):
            raise ValueError("ProviderOpaqueItem provider_type 和 display_summary 必须是字符串")
        return ProviderOpaqueItem(
            meta=meta,
            provider_type=provider_type,
            display_summary=display_summary,
        )
    raise ValueError(f"不支持的 Context Item 快照类型: {item_type}")


def serialize_context_items_snapshot(items: Sequence[ContextItem]) -> list[dict[str, Any]]:
    """序列化 Context Items。"""

    return [serialize_context_item_snapshot(item) for item in items]


def deserialize_context_items_snapshot(raw_items: Any) -> list[ContextItem]:
    """从快照恢复 Context Items。"""

    if not isinstance(raw_items, list):
        raise ValueError("快照中的 context_items 必须是列表")
    return [deserialize_context_item_snapshot(raw_item) for raw_item in raw_items]


