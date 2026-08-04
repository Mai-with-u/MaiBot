from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

import base64
import binascii
import hashlib
import json
import re

from src.common.logger import get_logger
from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import (
    AudioTranscriptionRequest,
    ClientRequest,
    EmbeddingRequest,
    RequestTraceContext,
    ResponseRequest,
)
from src.llm_models.payload_content.message import ImageMessagePart, Message, MessageBuilder, RoleType, TextMessagePart
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolCall, ToolOption, normalize_tool_options

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LLM_REQUEST_LOG_DIR = PROJECT_ROOT / "logs" / "maisaka_prompt" / "llm_error"
LLM_REQUEST_AUDIO_DIR = PROJECT_ROOT / "data" / "prompt_audio"
REPLAY_SCRIPT_RELATIVE_PATH = Path("scripts") / "replay_llm_request.py"
REPLAY_SCRIPT_PATH = PROJECT_ROOT / REPLAY_SCRIPT_RELATIVE_PATH
FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
SNAPSHOT_VERSION = 3
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


def serialize_message_snapshot(message: Message) -> dict[str, Any]:
    """将内部消息对象序列化为可回放的快照结构。"""
    parts_payload: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextMessagePart):
            parts_payload.append({"type": "text", "text": part.text})
            continue

        if isinstance(part, ImageMessagePart):
            parts_payload.append(
                {
                    "type": "image",
                    "image_base64": part.image_base64,
                    "image_format": part.image_format,
                }
            )

    payload: dict[str, Any] = {
        "parts": parts_payload,
        "role": message.role.value,
    }
    if message.reasoning_content:
        payload["reasoning_content"] = message.reasoning_content
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_name:
        payload["tool_name"] = message.tool_name
    if message.tool_calls:
        payload["tool_calls"] = serialize_tool_calls_snapshot(message.tool_calls)
    return payload


def deserialize_message_snapshot(raw_message: Any) -> Message:
    """从快照恢复内部消息对象。"""
    if not isinstance(raw_message, dict):
        raise ValueError("快照中的 message 必须是字典")

    raw_role = raw_message.get("role")
    if not isinstance(raw_role, str):
        raise ValueError("快照中的 message 缺少 role")

    role = RoleType(raw_role)
    builder = MessageBuilder().set_role(role)

    reasoning_content = raw_message.get("reasoning_content")
    if role == RoleType.Assistant and isinstance(reasoning_content, str) and reasoning_content:
        builder.set_reasoning_content(reasoning_content)

    raw_tool_calls = raw_message.get("tool_calls")
    tool_calls = deserialize_tool_calls_snapshot(raw_tool_calls)
    if role == RoleType.Assistant and tool_calls:
        builder.set_tool_calls(tool_calls)

    tool_call_id = raw_message.get("tool_call_id")
    if role == RoleType.Tool and isinstance(tool_call_id, str):
        builder.set_tool_call_id(tool_call_id)

    tool_name = raw_message.get("tool_name")
    if role == RoleType.Tool and isinstance(tool_name, str) and tool_name:
        builder.set_tool_name(tool_name)

    raw_parts = raw_message.get("parts", [])
    if not isinstance(raw_parts, list):
        raise ValueError("快照中的 message.parts 必须是列表")

    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            raise ValueError("快照中的 message part 必须是字典")

        part_type = str(raw_part.get("type", "")).strip().lower()
        if part_type == "text":
            text = raw_part.get("text")
            if not isinstance(text, str):
                raise ValueError("文本 part 缺少 text 字段")
            builder.add_text_content(text)
            continue

        if part_type == "image":
            image_format = raw_part.get("image_format")
            image_base64 = raw_part.get("image_base64")
            if not isinstance(image_format, str) or not isinstance(image_base64, str):
                raise ValueError("图片 part 缺少 image_format 或 image_base64")
            builder.add_image_content(image_format=image_format, image_base64=image_base64)
            continue

        raise ValueError(f"不支持的快照消息 part 类型: {part_type}")

    return builder.build()


def serialize_messages_snapshot(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """序列化消息列表。"""
    return [serialize_message_snapshot(message) for message in messages]


def deserialize_messages_snapshot(raw_messages: Any) -> list[Message]:
    """从快照恢复消息列表。"""
    if not isinstance(raw_messages, list):
        raise ValueError("快照中的 messages 必须是列表")
    return [deserialize_message_snapshot(raw_message) for raw_message in raw_messages]


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


def deserialize_structured_messages_snapshot(raw_messages: Any) -> list[Message]:
    """从推理过程统一消息结构恢复内部消息对象。"""

    if not isinstance(raw_messages, list):
        raise ValueError("快照中的 messages 必须是列表")

    messages: list[Message] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            raise ValueError("快照中的 message 必须是字典")
        role = RoleType(str(raw_message.get("role") or "user"))
        builder = MessageBuilder().set_role(role)
        reasoning_content = raw_message.get("reasoning_content")
        if role == RoleType.Assistant and isinstance(reasoning_content, str) and reasoning_content:
            builder.set_reasoning_content(reasoning_content)
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

        tool_calls = deserialize_tool_calls_snapshot(raw_message.get("tool_calls"))
        if role == RoleType.Assistant and tool_calls:
            builder.set_tool_calls(tool_calls)
        tool_call_id = raw_message.get("tool_call_id")
        if role == RoleType.Tool and isinstance(tool_call_id, str):
            builder.set_tool_call_id(tool_call_id)
        tool_name = raw_message.get("tool_name")
        if role == RoleType.Tool and isinstance(tool_name, str) and tool_name:
            builder.set_tool_name(tool_name)
        messages.append(builder.build())
    return messages


def read_structured_audio_base64(raw_messages: Any) -> str:
    """读取结构化消息中的音频引用并恢复 Base64。"""

    if not isinstance(raw_messages, list):
        return ""
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        content = raw_message.get("content")
        if not isinstance(content, list):
            continue
        for raw_part in content:
            if not isinstance(raw_part, dict) or str(raw_part.get("type") or "") != "audio":
                continue
            raw_path = str(raw_part.get("audio_path") or "")
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
        "extra_params": _json_friendly(dict(request.extra_params)),
        "max_tokens": request.max_tokens,
        "message_list": serialize_messages_snapshot(request.message_list),
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


def _build_structured_messages(internal_request: dict[str, Any]) -> list[dict[str, Any]]:
    """把内部请求快照转换成与推理过程日志一致的消息结构。"""

    from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer

    request_kind = str(internal_request.get("request_kind") or "")
    if request_kind == "embedding":
        embedding_input = str(internal_request.get("embedding_input") or "")
        return PromptCLIVisualizer.build_structured_message_payload(
            [{"role": "user", "content": embedding_input}],
            keep_base64=False,
        )
    if request_kind == "audio_transcription":
        audio_reference = _build_audio_reference(
            str(internal_request.get("audio_base64") or ""),
            str((internal_request.get("extra_params") or {}).get("audio_mime_type") or "audio/wav"),
        )
        return PromptCLIVisualizer.build_structured_message_payload(
            [{"role": "user", "content": [audio_reference]}],
            keep_base64=False,
        )

    messages: list[dict[str, Any]] = []
    raw_messages = internal_request.get("message_list")
    if not isinstance(raw_messages, list):
        return messages

    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        content: list[dict[str, Any]] = []
        for raw_part in raw_message.get("parts") or []:
            if not isinstance(raw_part, dict):
                continue
            part_type = str(raw_part.get("type") or "")
            if part_type == "text":
                content.append({"type": "text", "text": str(raw_part.get("text") or "")})
            elif part_type == "image":
                content.append(
                    {
                        "type": "image",
                        "image_base64": str(raw_part.get("image_base64") or ""),
                        "image_format": str(raw_part.get("image_format") or ""),
                    }
                )

        message_payload: dict[str, Any] = {
            "role": str(raw_message.get("role") or "unknown"),
            "content": content,
        }
        for key in ("tool_call_id", "tool_name", "tool_calls"):
            if raw_message.get(key) not in (None, "", []):
                message_payload[key] = raw_message[key]
        messages.append(message_payload)

    return PromptCLIVisualizer.build_structured_message_payload(messages, keep_base64=False)


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
        return "[见 messages]"
    if normalized_key in {"audio_base64", "image_base64", "base64"}:
        return "[见媒体引用]"

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

    excluded_keys = {"audio_base64", "embedding_input", "message_list", "model_info", "request_kind", "tool_options"}
    return {
        key: _sanitize_provider_request(value, key=key)
        for key, value in internal_request.items()
        if key not in excluded_keys
    }


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
        snapshot_path = (
            Path(active_trace_context.snapshot_path).resolve()
            if active_trace_context.snapshot_path
            else _build_snapshot_path(active_trace_context)
        )
        active_trace_context.snapshot_path = str(snapshot_path)

        if snapshot_path.is_file():
            snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
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
                "messages": _build_structured_messages(internal_request),
                "output": None,
                "tool_definitions": internal_request.get("tool_options") or [],
                "request_parameters": _build_request_parameters(internal_request),
                "model_info": serialize_model_info_snapshot(model_info),
                "api_provider": serialize_api_provider_snapshot(api_provider),
                "provider_request": _sanitize_provider_request(provider_request),
                "attempts": [],
                "replay": {
                    "command": build_replay_command(snapshot_path),
                    "file_uri": snapshot_path.as_uri(),
                    "script_path": str(REPLAY_SCRIPT_PATH),
                },
            }

        attempt_number = active_trace_context.attempt or len(snapshot_payload["attempts"]) + 1
        attempt_payload: dict[str, Any] = {
            "api_provider": serialize_api_provider_snapshot(api_provider),
            "attempt": attempt_number,
            "client_type": client_type,
            "error": {
                "message": str(error),
                "status_code": getattr(error, "status_code", None),
                "type": type(error).__name__,
            },
            "model_attempt": active_trace_context.model_attempt or 1,
            "model_info": serialize_model_info_snapshot(model_info),
            "model_name": model_info.name,
            "operation": operation,
            "provider_request": _sanitize_provider_request(provider_request),
            "provider_name": api_provider.name,
            "status": "failed",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

        response_body = extract_error_response_body(error)
        if response_body is not None:
            attempt_payload["error"]["response_body"] = _sanitize_provider_request(response_body)

        attempts = snapshot_payload["attempts"]
        existing_attempt = next(
            (
                item
                for item in attempts
                if item.get("attempt") == attempt_number and item.get("model_name") == model_info.name
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
    if snapshot_path is None or not snapshot_path.is_file():
        return
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for attempt in reversed(payload.get("attempts") or []):
        if attempt_number <= 0 or attempt.get("attempt") == attempt_number:
            attempt["status"] = status
            if retry_interval is not None:
                attempt["retry_interval"] = retry_interval
            break
    payload["metadata"]["status"] = status
    _write_snapshot(snapshot_path, payload)


def mark_request_succeeded(request: ClientRequest) -> None:
    """请求在至少一次失败后成功时，追加成功尝试并结束失败记录。"""

    trace_context = request.trace_context
    if trace_context is None or not trace_context.snapshot_path:
        return
    snapshot_path = Path(trace_context.snapshot_path).resolve()
    if not snapshot_path.is_file():
        return
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["attempts"].append(
        {
            "attempt": trace_context.attempt,
            "model_attempt": trace_context.model_attempt,
            "model_name": request.model_info.name,
            "provider_name": request.model_info.api_provider,
            "status": "succeeded",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
    )
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
    if snapshot_path is None or not snapshot_path.is_file():
        return
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for attempt in reversed(payload.get("attempts") or []):
        if attempt_number <= 0 or attempt.get("attempt") == attempt_number:
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
