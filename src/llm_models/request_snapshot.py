from dataclasses import replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

import base64
import binascii
import hashlib
import json
import re
import time
import uuid

from src.common.logger import get_logger
from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import (
    APIResponse,
    AudioTranscriptionRequest,
    ClientRequest,
    EmbeddingRequest,
    GenerationAttempt,
    GenerationTrace,
    RequestTraceContext,
    ResponseRequest,
)
from src.llm_models.generation_diagnostics import sanitize_diagnostic_url, sanitize_generation_diagnostic
from src.llm_models.payload_content.context_item import (
    CONTEXT_ITEM_SCHEMA_VERSION,
    AssistantMessageItem,
    ContextImagePart,
    ContextItem,
    ContextItemBuilder,
    ContextItemMeta,
    ContextRefusalPart,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    ProviderActivityItem,
    ProviderOpaqueItem,
    ReasoningItem,
    ReasoningRepresentation,
    RoleType,
    SystemMessageItem,
    UserMessageItem,
)
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolCall, ToolOption, normalize_tool_options

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LLM_REQUEST_LOG_DIR = PROJECT_ROOT / "logs" / "maisaka_prompt" / "llm_error"
LLM_REQUEST_AUDIO_DIR = PROJECT_ROOT / "data" / "prompt_audio"
REPLAY_SCRIPT_RELATIVE_PATH = Path("scripts") / "replay_llm_request.py"
REPLAY_SCRIPT_PATH = PROJECT_ROOT / REPLAY_SCRIPT_RELATIVE_PATH
FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
SNAPSHOT_VERSION = 6
DEFAULT_LLM_REQUEST_SNAPSHOT_LIMIT = 128

logger = get_logger("llm_request_snapshot")


def _json_friendly(value: Any) -> Any:
    """将任意对象尽量转换为可写入 JSON 的结构。"""
    if value is None or isinstance(value, (bool, float, int, str)):
        return value

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")

    if isinstance(value, Mapping):
        return {str(key): _json_friendly(item) for key, item in value.items()}

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_json_friendly(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_friendly(model_dump(mode="json", exclude_none=True))
        except TypeError:
            return _json_friendly(model_dump(exclude_none=True))

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_friendly(to_dict())

    return str(value)


def extract_error_response_body(error: Exception) -> Any | None:
    """尽量从异常对象中提取上游返回体，便于排查模型请求失败。"""
    candidate_errors = [error, getattr(error, "__cause__", None)]

    for candidate in candidate_errors:
        if candidate is None:
            continue

        response = getattr(candidate, "response", None)
        if response is not None:
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    return _json_friendly(response_json())
                except Exception:
                    pass

            response_text = getattr(response, "text", None)
            if response_text not in (None, ""):
                return str(response_text)

            response_content = getattr(response, "content", None)
            if response_content not in (None, b"", ""):
                return _json_friendly(response_content)

        response_body = getattr(candidate, "body", None)
        if response_body not in (None, "", b""):
            return _json_friendly(response_body)

        ext_info = getattr(candidate, "ext_info", None)
        if ext_info is not None:
            return _json_friendly(ext_info)

    return None


def _sanitize_filename_component(value: str) -> str:
    """将任意字符串转换为适合文件名使用的片段。"""
    normalized_value = FILENAME_SAFE_PATTERN.sub("-", value.strip())
    normalized_value = normalized_value.strip("-._")
    return normalized_value or "unknown"


def _serialize_tool_call(tool_call: ToolCall) -> dict[str, Any]:
    """序列化单个工具调用。"""
    payload = {
        "id": tool_call.call_id,
        "function": {
            "name": tool_call.func_name,
            "arguments": _json_friendly(tool_call.args or {}),
        },
    }
    if tool_call.extra_content:
        payload["extra_content"] = _json_friendly(tool_call.extra_content)
    return payload


def serialize_tool_calls_snapshot(tool_calls: Sequence[ToolCall] | None) -> list[dict[str, Any]]:
    """序列化工具调用列表。"""
    if not tool_calls:
        return []
    return [_serialize_tool_call(tool_call) for tool_call in tool_calls]


def deserialize_tool_calls_snapshot(raw_tool_calls: Any) -> list[ToolCall]:
    """从快照恢复工具调用列表。"""
    if raw_tool_calls in (None, []):
        return []
    if not isinstance(raw_tool_calls, list):
        raise ValueError("快照中的 tool_calls 必须是列表")

    normalized_tool_calls: list[ToolCall] = []
    for raw_tool_call in raw_tool_calls:
        if not isinstance(raw_tool_call, dict):
            raise ValueError("快照中的 tool_call 项必须是字典")

        function_info = raw_tool_call.get("function", {})
        if isinstance(function_info, dict):
            function_name = function_info.get("name")
            function_arguments = function_info.get("arguments")
        else:
            function_name = raw_tool_call.get("name")
            function_arguments = raw_tool_call.get("arguments")

        call_id = raw_tool_call.get("id") or raw_tool_call.get("call_id")
        if not isinstance(call_id, str) or not isinstance(function_name, str):
            raise ValueError("快照中的 tool_call 缺少 id 或 function.name")

        extra_content = raw_tool_call.get("extra_content")
        normalized_tool_calls.append(
            ToolCall(
                call_id=call_id,
                func_name=function_name,
                args=function_arguments if isinstance(function_arguments, dict) else {},
                extra_content=extra_content if isinstance(extra_content, dict) else None,
            )
        )
    return normalized_tool_calls


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


def deserialize_persisted_context_items_snapshot(raw_items: Any) -> list[ContextItem]:
    """从省略内联媒体的结构化记录恢复规范 Context Items。"""

    if not isinstance(raw_items, list):
        raise ValueError("结构化记录中的 request_items 必须是列表")
    restored_items: list[ContextItem] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("结构化记录中的 Context Item 必须是字典")
        restored_item = _json_friendly(raw_item)
        raw_parts = restored_item.get("parts")
        if isinstance(raw_parts, list):
            restored_parts: list[Any] = []
            for raw_part in raw_parts:
                if not isinstance(raw_part, dict):
                    restored_parts.append(raw_part)
                    continue
                image_part = _extract_structured_image_part(raw_part)
                if image_part is None:
                    restored_parts.append(raw_part)
                    continue
                image_format, image_base64 = image_part
                restored_parts.append(
                    {
                        "type": "image",
                        "image_format": image_format,
                        "image_base64": image_base64,
                    }
                )
            restored_item["parts"] = restored_parts
        restored_items.append(deserialize_context_item_snapshot(restored_item))
    return restored_items


def _resolve_snapshot_media_path(raw_path: str) -> Path:
    """解析结构化日志中的项目内媒体路径。"""

    candidate = Path(raw_path)
    resolved_path = candidate.resolve() if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()
    try:
        resolved_path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError(f"媒体引用不在项目目录内: {raw_path}") from exc
    if not resolved_path.is_file():
        raise ValueError(f"媒体引用文件不存在: {raw_path}")
    return resolved_path


def _extract_structured_image_part(raw_part: dict[str, Any]) -> tuple[str, str] | None:
    part_type = str(raw_part.get("type") or "").strip().lower()
    if part_type not in {"image", "image_url", "input_image"}:
        return None
    image_reference = raw_part.get("image_reference")
    reference = image_reference if isinstance(image_reference, dict) else raw_part
    raw_path = str(reference.get("image_path") or raw_part.get("image_path") or "")
    if not raw_path:
        return None
    image_format = str(raw_part.get("image_format") or reference.get("image_format") or "png")
    image_base64 = base64.b64encode(_resolve_snapshot_media_path(raw_path).read_bytes()).decode("ascii")
    return image_format, image_base64


def deserialize_structured_context_items_snapshot(raw_messages: Any) -> list[ContextItem]:
    """从旧推理过程 Chat 投影恢复 Context Items。"""

    if not isinstance(raw_messages, list):
        raise ValueError("快照中的 messages 必须是列表")

    items: list[ContextItem] = []
    logical_turn_by_call_id: dict[str, str] = {}
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            raise ValueError("快照中的 message 必须是字典")
        role = RoleType(str(raw_message.get("role") or "user"))
        builder = ContextItemBuilder().set_role(role)
        content = raw_message.get("content")
        content_parts = content if isinstance(content, list) else [{"type": "text", "text": str(content or "")}]
        for raw_part in content_parts:
            if isinstance(raw_part, str):
                builder.add_text_content(raw_part)
                continue
            if not isinstance(raw_part, dict):
                continue
            image_part = _extract_structured_image_part(raw_part)
            if image_part is not None:
                image_format, image_base64 = image_part
                builder.add_image_content(image_format=image_format, image_base64=image_base64)
                continue
            if str(raw_part.get("type") or "") == "text":
                builder.add_text_content(str(raw_part.get("text") or ""))

        tool_call_id = raw_message.get("tool_call_id")
        if role == RoleType.Tool and isinstance(tool_call_id, str):
            builder.set_tool_call_id(tool_call_id)
            builder.set_meta(
                ContextItemMeta.create(logical_turn_id=logical_turn_by_call_id.get(tool_call_id))
            )
        tool_name = raw_message.get("tool_name")
        if role == RoleType.Tool and isinstance(tool_name, str) and tool_name:
            builder.set_tool_name(tool_name)
        if role != RoleType.Assistant:
            items.append(builder.build())
            continue

        logical_turn_id = uuid.uuid4().hex
        assistant_group_items: list[ContextItem] = []
        reasoning_content = raw_message.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            assistant_group_items.append(
                ReasoningItem(
                    meta=ContextItemMeta.create(logical_turn_id=logical_turn_id),
                    text_parts=(reasoning_content,),
                    representation=ReasoningRepresentation.RAW_TEXT,
                )
            )
        if content not in (None, "", []):
            assistant_group_items.append(builder.build())
        for tool_call in deserialize_tool_calls_snapshot(raw_message.get("tool_calls")):
            assistant_group_items.append(
                FunctionCallItem(
                    meta=ContextItemMeta.create(logical_turn_id=logical_turn_id),
                    tool_call=ContextToolCall.create(
                        call_id=tool_call.call_id,
                        func_name=tool_call.func_name,
                        args=tool_call.args,
                        extra_content=tool_call.extra_content,
                    ),
                )
            )
        for item in assistant_group_items:
            bound_item = replace(item, meta=replace(item.meta, logical_turn_id=logical_turn_id))
            items.append(bound_item)
            if isinstance(bound_item, FunctionCallItem):
                logical_turn_by_call_id[bound_item.tool_call.call_id] = logical_turn_id
    return items


def read_structured_audio_base64(raw_items: Any) -> str:
    """读取旧消息或 v5 Item 中的音频引用并恢复 Base64。"""

    def find_audio_path(value: Any) -> str:
        if isinstance(value, list):
            for item in value:
                if raw_path := find_audio_path(item):
                    return raw_path
            return ""
        if not isinstance(value, dict):
            return ""
        if str(value.get("type") or "") == "audio":
            return str(value.get("audio_path") or "")
        for item in value.values():
            if raw_path := find_audio_path(item):
                return raw_path
        return ""

    raw_path = find_audio_path(raw_items)
    if raw_path:
        return base64.b64encode(_resolve_snapshot_media_path(raw_path).read_bytes()).decode("ascii")
    return ""


def serialize_model_info_snapshot(model_info: ModelInfo) -> dict[str, Any]:
    """序列化模型信息。"""
    return {
        "api_provider": model_info.api_provider,
        "extra_params": _json_friendly(dict(model_info.extra_params)),
        "force_stream_mode": model_info.force_stream_mode,
        "max_tokens": model_info.max_tokens,
        "model_identifier": model_info.model_identifier,
        "name": model_info.name,
        "temperature": model_info.temperature,
        "visual": model_info.visual,
    }


def deserialize_model_info_snapshot(raw_model_info: Any) -> ModelInfo:
    """从快照恢复模型信息。"""
    if not isinstance(raw_model_info, dict):
        raise ValueError("快照中的 model_info 必须是字典")

    return ModelInfo(
        api_provider=str(raw_model_info.get("api_provider") or ""),
        extra_params=dict(raw_model_info.get("extra_params") or {}),
        force_stream_mode=bool(raw_model_info.get("force_stream_mode", False)),
        max_tokens=raw_model_info.get("max_tokens"),
        model_identifier=str(raw_model_info.get("model_identifier") or ""),
        name=str(raw_model_info.get("name") or ""),
        temperature=raw_model_info.get("temperature"),
        visual=bool(raw_model_info.get("visual", False)),
    )


def serialize_response_format_snapshot(response_format: RespFormat | None) -> dict[str, Any] | None:
    """序列化响应格式定义。"""
    if response_format is None:
        return None
    return response_format.to_dict()


def deserialize_response_format_snapshot(raw_response_format: Any) -> RespFormat | None:
    """从快照恢复响应格式定义。"""
    if raw_response_format is None:
        return None
    if not isinstance(raw_response_format, dict):
        raise ValueError("快照中的 response_format 必须是字典")

    raw_format_type = raw_response_format.get("format_type")
    if not isinstance(raw_format_type, str):
        raise ValueError("快照中的 response_format 缺少 format_type")

    format_type = RespFormatType(raw_format_type)
    raw_schema = raw_response_format.get("schema")
    schema = raw_schema if isinstance(raw_schema, dict) else None
    return RespFormat(format_type=format_type, schema=schema)


def serialize_tool_options_snapshot(tool_options: Sequence[ToolOption] | None) -> list[dict[str, Any]]:
    """序列化工具定义列表。"""
    if not tool_options:
        return []
    return [tool_option.to_openai_function_schema() for tool_option in tool_options]


def deserialize_tool_options_snapshot(raw_tool_options: Any) -> list[ToolOption] | None:
    """从快照恢复工具定义列表。"""
    if raw_tool_options in (None, []):
        return None
    if not isinstance(raw_tool_options, list):
        raise ValueError("快照中的 tool_options 必须是列表")
    return normalize_tool_options(raw_tool_options)


def serialize_response_request_snapshot(request: ResponseRequest) -> dict[str, Any]:
    """序列化文本/多模态请求。"""
    return {
        "item_schema_version": CONTEXT_ITEM_SCHEMA_VERSION,
        "extra_params": _json_friendly(dict(request.extra_params)),
        "max_tokens": request.max_tokens,
        "context_items": serialize_context_items_snapshot(request.context_items),
        "logical_turn_id": request.logical_turn_id,
        "model_info": serialize_model_info_snapshot(request.model_info),
        "request_kind": "response",
        "response_format": serialize_response_format_snapshot(request.response_format),
        "temperature": request.temperature,
        "tool_options": serialize_tool_options_snapshot(request.tool_options),
    }


def serialize_embedding_request_snapshot(request: EmbeddingRequest) -> dict[str, Any]:
    """序列化嵌入请求。"""
    return {
        "embedding_input": request.embedding_input,
        "extra_params": _json_friendly(dict(request.extra_params)),
        "model_info": serialize_model_info_snapshot(request.model_info),
        "request_kind": "embedding",
    }


def serialize_audio_request_snapshot(request: AudioTranscriptionRequest) -> dict[str, Any]:
    """序列化音频转写请求。"""
    return {
        "audio_base64": request.audio_base64,
        "extra_params": _json_friendly(dict(request.extra_params)),
        "max_tokens": request.max_tokens,
        "model_info": serialize_model_info_snapshot(request.model_info),
        "request_kind": "audio_transcription",
    }


def serialize_api_provider_snapshot(api_provider: APIProvider) -> dict[str, Any]:
    """序列化 API Provider 配置，排除敏感认证信息。"""
    return {
        "auth_header_name": api_provider.auth_header_name,
        "auth_header_prefix": api_provider.auth_header_prefix,
        "auth_query_name": api_provider.auth_query_name,
        "auth_type": api_provider.auth_type,
        "base_url": api_provider.base_url,
        "client_type": api_provider.client_type,
        "default_headers": _sanitize_provider_request(dict(api_provider.default_headers)),
        "default_query": _sanitize_provider_request(dict(api_provider.default_query)),
        "model_list_endpoint": api_provider.model_list_endpoint,
        "name": api_provider.name,
        "organization": api_provider.organization,
        "project": api_provider.project,
        "retry_interval": api_provider.retry_interval,
        "timeout": api_provider.timeout,
    }


def serialize_client_request_snapshot(request: ClientRequest) -> dict[str, Any]:
    """按统一客户端请求类型生成可重放快照。"""

    if isinstance(request, ResponseRequest):
        return serialize_response_request_snapshot(request)
    if isinstance(request, EmbeddingRequest):
        return serialize_embedding_request_snapshot(request)
    return serialize_audio_request_snapshot(request)


def _build_structured_items(internal_request: dict[str, Any]) -> list[dict[str, Any]]:
    """把内部请求快照转换成 v5 Item-first 结构化记录。"""

    from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer

    request_kind = str(internal_request.get("request_kind") or "")
    if request_kind == "embedding":
        embedding_input = str(internal_request.get("embedding_input") or "")
        return PromptCLIVisualizer.build_structured_context_item_payload(
            [ContextItemBuilder().add_text_content(embedding_input).build()],
            keep_base64=False,
        )
    if request_kind == "audio_transcription":
        audio_reference = _build_audio_reference(
            str(internal_request.get("audio_base64") or ""),
            str((internal_request.get("extra_params") or {}).get("audio_mime_type") or "audio/wav"),
        )
        audio_item = serialize_context_item_snapshot(
            ProviderOpaqueItem(
                meta=ContextItemMeta.create(),
                provider_type="audio_transcription",
                display_summary="音频转写输入",
            )
        )
        audio_item["audio_reference"] = audio_reference
        return [audio_item]

    raw_items = internal_request.get("context_items")
    if not isinstance(raw_items, list):
        return []
    return [
        PromptCLIVisualizer.sanitize_structured_context_item_snapshot(raw_item, keep_base64=False)
        for raw_item in raw_items
        if isinstance(raw_item, dict)
    ]


def _build_audio_reference(audio_base64: str, mime_type: str) -> dict[str, Any]:
    """把音频外置到 data/prompt_audio，并返回可重放引用。"""

    normalized_mime_type = mime_type.strip().lower() or "audio/wav"
    audio_format = normalized_mime_type.partition("/")[2].split(";", maxsplit=1)[0] or "bin"
    payload: dict[str, Any] = {
        "type": "audio",
        "audio_format": audio_format,
        "mime_type": normalized_mime_type,
        "base64_omitted": True,
    }
    try:
        audio_bytes = base64.b64decode(audio_base64, validate=True)
    except (ValueError, binascii.Error):
        payload.update({"audio_available": False, "size_bytes": 0})
        return payload

    digest = hashlib.sha256(audio_bytes).hexdigest()
    LLM_REQUEST_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = LLM_REQUEST_AUDIO_DIR / f"{digest}.{_sanitize_filename_component(audio_format)}"
    if not audio_path.exists():
        audio_path.write_bytes(audio_bytes)
    payload.update(
        {
            "audio_available": True,
            "audio_hash": digest,
            "audio_path": _build_display_path(audio_path),
            "audio_uri": audio_path.resolve().as_uri(),
            "size_bytes": len(audio_bytes),
        }
    )
    return payload


def _sanitize_provider_request(value: Any, *, key: str = "") -> Any:
    """移除 Provider 请求中的重复正文、内联媒体和敏感认证字段。"""

    normalized_key = key.strip().lower()
    credential_key = normalized_key.replace("-", "_")
    if credential_key in {
        "api_key",
        "apikey",
        "authorization",
        "auth_token",
        "client_secret",
        "credential",
        "credentials",
        "proxy_authorization",
        "x_api_key",
    } or credential_key.endswith(("_api_key", "_access_token", "_auth_token", "_secret")):
        return "[已脱敏]"
    if normalized_key in {"messages", "contents"}:
        return "[见 request_items]"
    if normalized_key in {"audio_base64", "image_base64", "base64"}:
        return "[见媒体引用]"
    if normalized_key.replace("-", "_") in {"encrypted_content", "thought_signature"}:
        return "[仅在内存 replay fragment 中保留]"

    friendly_value = _json_friendly(value)
    if isinstance(friendly_value, dict):
        return {
            str(item_key): _sanitize_provider_request(item, key=str(item_key))
            for item_key, item in friendly_value.items()
        }
    if isinstance(friendly_value, list):
        return [_sanitize_provider_request(item) for item in friendly_value]
    if isinstance(friendly_value, str) and friendly_value.startswith(("data:image/", "data:audio/")):
        return "[见媒体引用]"
    return friendly_value


def _build_request_parameters(internal_request: dict[str, Any]) -> dict[str, Any]:
    """保留重放所需参数，同时排除已经提升为公共字段的正文和媒体。"""

    excluded_keys = {
        "audio_base64",
        "context_items",
        "embedding_input",
        "model_info",
        "request_kind",
        "tool_options",
    }
    return {
        key: _sanitize_provider_request(value, key=key)
        for key, value in internal_request.items()
        if key not in excluded_keys
    }


def serialize_generation_trace(trace: GenerationTrace | None) -> dict[str, Any] | None:
    """序列化稳定的 Provider 成功响应索引。"""

    if trace is None:
        return None
    return {
        "provider": trace.provider,
        "endpoint": sanitize_diagnostic_url(trace.endpoint),
        "model": trace.model,
        "response_id": trace.response_id,
        "status": trace.status,
        "prompt_tokens": trace.prompt_tokens,
        "completion_tokens": trace.completion_tokens,
        "total_tokens": trace.total_tokens,
        "prompt_cache_hit_tokens": trace.prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": trace.prompt_cache_miss_tokens,
        "output_item_ids": list(trace.output_item_ids),
    }


def _serialize_generation_attempt_items(items: Sequence[ContextItem]) -> list[dict[str, Any]]:
    """序列化 Attempt Items，并将内联媒体外置为可重放引用。"""

    from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer

    return [
        PromptCLIVisualizer.sanitize_structured_context_item_snapshot(
            serialize_context_item_snapshot(item),
            keep_base64=False,
        )
        for item in items
    ]


def serialize_generation_attempt(attempt: GenerationAttempt) -> dict[str, Any]:
    """把运行时 Attempt DTO 转换为 schema v6 JSON。"""

    payload: dict[str, Any] = {
        "attempt_id": attempt.attempt_id,
        "workflow_purpose": attempt.workflow_purpose,
        "workflow_attempt": attempt.workflow_attempt,
        "provider_attempt": attempt.provider_attempt,
        "model_attempt": attempt.model_attempt,
        "status": attempt.status,
        "started_at": attempt.started_at,
        "duration_ms": attempt.duration_ms,
        "provider": attempt.provider,
        "endpoint": sanitize_diagnostic_url(attempt.endpoint),
        "model": attempt.model,
        "client_type": attempt.client_type,
        "operation": attempt.operation,
        "wire_protocol": attempt.wire_protocol,
        "request_items": _serialize_generation_attempt_items(attempt.request_items),
        "tool_definitions": sanitize_generation_diagnostic(attempt.tool_definitions),
        "request_parameters": sanitize_generation_diagnostic(attempt.request_parameters),
        "wire_request": sanitize_generation_diagnostic(attempt.wire_request),
        "wire_response": sanitize_generation_diagnostic(attempt.wire_response),
        "output_items": _serialize_generation_attempt_items(attempt.output_items),
        "trace": serialize_generation_trace(attempt.trace),
    }
    if attempt.error is not None:
        payload["error"] = sanitize_generation_diagnostic(attempt.error)
    return payload


def record_failed_generation_attempt(
    *,
    api_provider: APIProvider,
    client_type: str,
    error: Exception,
    internal_request: dict[str, Any],
    model_info: ModelInfo,
    operation: str,
    provider_request: dict[str, Any],
    trace_context: RequestTraceContext,
) -> GenerationAttempt:
    """把一次实际失败调用追加到内存诊断链。"""

    attempt_number = trace_context.attempt or len(trace_context.generation_attempts) + 1
    started_timestamp = trace_context.current_attempt_started_at or time.time()
    raw_context_items = internal_request.get("context_items")
    request_items = (
        tuple(deserialize_context_items_snapshot(raw_context_items))
        if isinstance(raw_context_items, list)
        else ()
    )
    raw_tool_definitions = internal_request.get("tool_options")
    tool_definitions = tuple(
        dict(item)
        for item in raw_tool_definitions
        if isinstance(item, dict)
    ) if isinstance(raw_tool_definitions, list) else ()
    response_body = extract_error_response_body(error)
    attempt = GenerationAttempt(
        attempt_id=f"{trace_context.request_id}:{attempt_number}",
        workflow_purpose=trace_context.request_type or trace_context.task_name,
        workflow_attempt=1,
        provider_attempt=attempt_number,
        model_attempt=trace_context.model_attempt or 1,
        status="failed",
        started_at=datetime.fromtimestamp(started_timestamp).isoformat(timespec="milliseconds"),
        duration_ms=round(max(0.0, time.time() - started_timestamp) * 1000, 2),
        provider=api_provider.name,
        endpoint=sanitize_diagnostic_url(api_provider.base_url),
        model=model_info.model_identifier,
        client_type=client_type,
        operation=operation,
        wire_protocol=client_type,
        request_items=request_items,
        tool_definitions=tool_definitions,
        request_parameters=_build_request_parameters(internal_request),
        wire_request=sanitize_generation_diagnostic(provider_request),
        wire_response=sanitize_generation_diagnostic(response_body),
        error={
            "message": str(error),
            "status_code": getattr(error, "status_code", None),
            "type": type(error).__name__,
        },
    )
    for index, existing_attempt in enumerate(trace_context.generation_attempts):
        if existing_attempt.provider_attempt == attempt_number:
            trace_context.generation_attempts[index] = attempt
            break
    else:
        trace_context.generation_attempts.append(attempt)
    return attempt


def _build_snapshot_path(trace_context: RequestTraceContext) -> Path:
    if trace_context.session_id:
        from src.maisaka.display.preview_path_utils import build_preview_chat_dir_name

        session_dir_name = build_preview_chat_dir_name(trace_context.session_id)
    else:
        session_dir_name = "system"
    session_dir = LLM_REQUEST_LOG_DIR / session_dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.fromtimestamp(trace_context.started_at)
    file_name = f"{timestamp.strftime('%Y%m%d_%H%M%S_%f')}_{trace_context.request_id}.json"
    return (session_dir / file_name).resolve()


def _build_display_path(file_path: Path) -> str:
    resolved_path = file_path.resolve()
    try:
        return resolved_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved_path.as_posix()


def _write_snapshot(snapshot_path: Path, payload: dict[str, Any]) -> None:
    """原子更新单个逻辑请求的失败记录。"""

    payload["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temporary_path = snapshot_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary_path.replace(snapshot_path)


def build_replay_command(snapshot_path: Path) -> str:
    """构建回放当前快照的命令。"""
    return f'uv run python {REPLAY_SCRIPT_RELATIVE_PATH.as_posix()} "{snapshot_path.resolve()}"'


def _get_llm_request_snapshot_limit() -> int:
    try:
        from src.config.config import global_config

        return max(1, int(global_config.log.llm_request_snapshot_limit or DEFAULT_LLM_REQUEST_SNAPSHOT_LIMIT))
    except Exception:
        return DEFAULT_LLM_REQUEST_SNAPSHOT_LIMIT


def _trim_llm_request_snapshots() -> None:
    limit = _get_llm_request_snapshot_limit()
    snapshot_files = [file_path for file_path in LLM_REQUEST_LOG_DIR.rglob("*.json") if file_path.is_file()]
    if len(snapshot_files) <= limit:
        return

    sorted_files = sorted(snapshot_files, key=lambda file_path: file_path.stat().st_mtime)
    for old_file in sorted_files[: len(snapshot_files) - limit]:
        try:
            old_file.unlink()
        except FileNotFoundError:
            continue


def save_failed_request_snapshot(
    *,
    api_provider: APIProvider,
    client_type: str,
    error: Exception,
    internal_request: dict[str, Any],
    model_info: ModelInfo,
    operation: str,
    provider_request: dict[str, Any],
    trace_context: RequestTraceContext | None = None,
) -> Path | None:
    """保存或追加一次逻辑请求的失败尝试。"""
    try:
        active_trace_context = trace_context or RequestTraceContext()
        generation_attempt = record_failed_generation_attempt(
            api_provider=api_provider,
            client_type=client_type,
            error=error,
            internal_request=internal_request,
            model_info=model_info,
            operation=operation,
            provider_request=provider_request,
            trace_context=active_trace_context,
        )
        error.generation_trace_context = active_trace_context
        error.request_snapshot_attempt = generation_attempt.provider_attempt
        snapshot_path = (
            Path(active_trace_context.snapshot_path).resolve()
            if active_trace_context.snapshot_path
            else _build_snapshot_path(active_trace_context)
        )
        active_trace_context.snapshot_path = str(snapshot_path)

        if snapshot_path.is_file():
            snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if "request_items" not in snapshot_payload:
                snapshot_payload["schema_version"] = SNAPSHOT_VERSION
                snapshot_payload["presentation"] = {"output_title": "输出 Items"}
                snapshot_payload["request_items"] = _build_structured_items(internal_request)
                snapshot_payload["output_items"] = []
                snapshot_payload.pop("messages", None)
                snapshot_payload.pop("output", None)
        else:
            request_kind = str(internal_request.get("request_kind") or "request")
            created_at = datetime.fromtimestamp(active_trace_context.started_at).isoformat(timespec="seconds")
            snapshot_payload = {
                "schema_version": SNAPSHOT_VERSION,
                "request": {
                    "kind": request_kind,
                    "operation": operation,
                    "request_type": active_trace_context.request_type,
                    "task_name": active_trace_context.task_name,
                },
                "metadata": {
                    "client_type": client_type,
                    "created_at": created_at,
                    "model_name": model_info.name,
                    "provider_name": api_provider.name,
                    "request_id": active_trace_context.request_id,
                    "session_id": active_trace_context.session_id,
                    "status": "retrying",
                    "updated_at": created_at,
                },
                "presentation": {"output_title": "输出 Items"},
                "request_items": _build_structured_items(internal_request),
                "output_items": [],
                "tool_definitions": internal_request.get("tool_options") or [],
                "request_parameters": _build_request_parameters(internal_request),
                "model_info": serialize_model_info_snapshot(model_info),
                "api_provider": serialize_api_provider_snapshot(api_provider),
                "generation_attempts": [],
                "replay": {
                    "command": build_replay_command(snapshot_path),
                    "file_uri": snapshot_path.as_uri(),
                    "script_path": str(REPLAY_SCRIPT_PATH),
                },
            }

        attempt_number = generation_attempt.provider_attempt
        attempt_payload = serialize_generation_attempt(generation_attempt)
        attempts = snapshot_payload.setdefault("generation_attempts", [])
        snapshot_payload.pop("attempts", None)
        snapshot_payload.pop("provider_request", None)
        existing_attempt = next(
            (
                item
                for item in attempts
                if item.get("provider_attempt") == attempt_number and item.get("model") == model_info.model_identifier
            ),
            None,
        )
        if existing_attempt is None:
            attempts.append(attempt_payload)
        else:
            existing_attempt.update(attempt_payload)
        snapshot_payload["metadata"].update(
            {
                "client_type": client_type,
                "model_name": model_info.name,
                "provider_name": api_provider.name,
                "status": "retrying",
            }
        )
        _write_snapshot(snapshot_path, snapshot_payload)
        _trim_llm_request_snapshots()
        return snapshot_path
    except Exception:
        logger.exception("保存 LLM 失败请求快照时发生异常")
        return None


def _resolve_snapshot_from_exception(exception: Exception) -> tuple[Path | None, int]:
    for candidate in (exception, getattr(exception, "__cause__", None)):
        if candidate is None:
            continue
        snapshot_path = str(getattr(candidate, "request_snapshot_path", "") or "")
        if snapshot_path:
            return Path(snapshot_path).resolve(), int(getattr(candidate, "request_snapshot_attempt", 0) or 0)
    return None, 0


def update_failed_request_attempt(
    exception: Exception,
    *,
    status: str,
    retry_interval: float | None = None,
) -> None:
    """更新异常对应尝试的后续状态。"""

    snapshot_path, attempt_number = _resolve_snapshot_from_exception(exception)
    trace_context = getattr(exception, "generation_trace_context", None)
    if isinstance(trace_context, RequestTraceContext):
        trace_context.replace_attempt_status(attempt_number, status)
    if snapshot_path is None or not snapshot_path.is_file():
        return
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for attempt in reversed(payload.get("generation_attempts") or []):
        if attempt_number <= 0 or attempt.get("provider_attempt") == attempt_number:
            attempt["status"] = status
            if retry_interval is not None:
                attempt["retry_interval"] = retry_interval
            break
    payload["metadata"]["status"] = status
    _write_snapshot(snapshot_path, payload)


def mark_request_succeeded(request: ClientRequest, response: APIResponse) -> None:
    """请求在至少一次失败后成功时，追加成功尝试并结束失败记录。"""

    trace_context = request.trace_context
    if trace_context is None or not trace_context.snapshot_path:
        return
    snapshot_path = Path(trace_context.snapshot_path).resolve()
    if not snapshot_path.is_file():
        return
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["output_items"] = serialize_context_items_snapshot(response.output_items)
    if isinstance(request, ResponseRequest):
        payload["request_items"] = serialize_context_items_snapshot(request.context_items)
    if trace_context.generation_attempts:
        success_attempt = trace_context.generation_attempts[-1]
        serialized_attempt = serialize_generation_attempt(success_attempt)
        generation_attempts = payload.setdefault("generation_attempts", [])
        if not any(item.get("attempt_id") == success_attempt.attempt_id for item in generation_attempts):
            generation_attempts.append(serialized_attempt)
    payload["metadata"].update(
        {
            "model_name": request.model_info.name,
            "provider_name": request.model_info.api_provider,
            "status": "succeeded_after_retry",
        }
    )
    _write_snapshot(snapshot_path, payload)


def mark_request_final_failure(exception: Exception) -> None:
    """把一次逻辑请求标记为最终失败。"""

    snapshot_path, attempt_number = _resolve_snapshot_from_exception(exception)
    trace_context = getattr(exception, "generation_trace_context", None)
    if isinstance(trace_context, RequestTraceContext):
        trace_context.replace_attempt_status(attempt_number, "final_failed")
    if snapshot_path is None or not snapshot_path.is_file():
        return
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for attempt in reversed(payload.get("generation_attempts") or []):
        if attempt_number <= 0 or attempt.get("provider_attempt") == attempt_number:
            attempt["status"] = "final_failed"
            break
    payload["metadata"]["status"] = "final_failed"
    _write_snapshot(snapshot_path, payload)


def attach_request_snapshot(exception: Exception, snapshot_path: Path | None) -> None:
    """将请求快照信息挂载到异常对象上。"""
    if snapshot_path is None:
        return

    exception.request_snapshot_path = str(snapshot_path.resolve())
    exception.request_snapshot_uri = snapshot_path.resolve().as_uri()
    exception.request_snapshot_replay_command = build_replay_command(snapshot_path)


def has_request_snapshot(exception: Exception) -> bool:
    """鍒ゆ柇寮傚父鏄惁宸插叧鑱斾簡璇锋眰蹇収銆?"""
    for candidate in (exception, getattr(exception, "__cause__", None)):
        if candidate is None:
            continue
        if getattr(candidate, "request_snapshot_path", ""):
            return True
    return False


def format_request_snapshot_log_info(exception: Exception) -> str:
    """将异常上的快照信息格式化为日志片段。"""
    for candidate in (exception, getattr(exception, "__cause__", None)):
        if candidate is None:
            continue

        snapshot_path = getattr(candidate, "request_snapshot_path", "")
        replay_command = getattr(candidate, "request_snapshot_replay_command", "")
        if not any([snapshot_path, replay_command]):
            continue

        lines: list[str] = []
        if snapshot_path:
            lines.append(f"调用完整信息（如果需要求助，请发送该文本）: {snapshot_path}")
        if replay_command:
            lines.append(f"使用以下命令重新请求: {replay_command}")
        if lines:
            return "\n  " + "\n  ".join(lines)

    return ""
