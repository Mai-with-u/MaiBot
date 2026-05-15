from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

import base64
import json
import re

from src.common.logger import get_logger
from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import AudioTranscriptionRequest, EmbeddingRequest, ResponseRequest
from src.llm_models.payload_content.message import ImageMessagePart, Message, MessageBuilder, RoleType, TextMessagePart
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolCall, ToolOption, normalize_tool_options

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LLM_REQUEST_LOG_DIR = PROJECT_ROOT / "logs" / "llm_request"
REPLAY_SCRIPT_RELATIVE_PATH = Path("scripts") / "replay_llm_request.py"
REPLAY_SCRIPT_PATH = PROJECT_ROOT / REPLAY_SCRIPT_RELATIVE_PATH
FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
SNAPSHOT_VERSION = 1
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
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_name:
        payload["tool_name"] = message.tool_name
    if message.tool_calls:
        payload["tool_calls"] = serialize_tool_calls_snapshot(message.tool_calls)
    if message.reasoning_content:
        payload["reasoning_content"] = message.reasoning_content
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

    raw_tool_calls = raw_message.get("tool_calls")
    tool_calls = deserialize_tool_calls_snapshot(raw_tool_calls)
    if role == RoleType.Assistant and tool_calls:
        builder.set_tool_calls(tool_calls)

    reasoning_content = raw_message.get("reasoning_content")
    if role == RoleType.Assistant and isinstance(reasoning_content, str) and reasoning_content.strip():
        builder.set_reasoning_content(reasoning_content)

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
        "default_headers": _json_friendly(dict(api_provider.default_headers)),
        "default_query": _json_friendly(dict(api_provider.default_query)),
        "model_list_endpoint": api_provider.model_list_endpoint,
        "name": api_provider.name,
        "organization": api_provider.organization,
        "project": api_provider.project,
        "retry_interval": api_provider.retry_interval,
        "timeout": api_provider.timeout,
    }


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
    snapshot_files = [file_path for file_path in LLM_REQUEST_LOG_DIR.glob("*.json") if file_path.is_file()]
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
) -> Path | None:
    """保存失败请求快照。"""
    try:
        LLM_REQUEST_LOG_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now()
        file_name = (
            f"{timestamp.strftime('%Y%m%d_%H%M%S_%f')}"
            f"_{_sanitize_filename_component(client_type)}"
            f"_{_sanitize_filename_component(internal_request.get('request_kind', 'request'))}"
            f"_{_sanitize_filename_component(model_info.name or model_info.model_identifier)}.json"
        )
        snapshot_path = (LLM_REQUEST_LOG_DIR / file_name).resolve()

        snapshot_payload: dict[str, Any] = {
            "api_provider": serialize_api_provider_snapshot(api_provider),
            "client_type": client_type,
            "created_at": timestamp.isoformat(timespec="seconds"),
            "error": {
                "message": str(error),
                "status_code": getattr(error, "status_code", None),
                "type": type(error).__name__,
            },
            "internal_request": internal_request,
            "model_info": serialize_model_info_snapshot(model_info),
            "operation": operation,
            "provider_request": _json_friendly(provider_request),
            "snapshot_version": SNAPSHOT_VERSION,
        }

        response_body = extract_error_response_body(error)
        if response_body is not None:
            snapshot_payload["error"]["response_body"] = response_body

        snapshot_payload["replay"] = {
            "command": build_replay_command(snapshot_path),
            "file_uri": snapshot_path.as_uri(),
            "script_path": str(REPLAY_SCRIPT_PATH),
        }

        snapshot_path.write_text(
            json.dumps(snapshot_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _trim_llm_request_snapshots()
        return snapshot_path
    except Exception:
        logger.exception("淇濆瓨 LLM 澶辫触璇锋眰蹇収鏃跺彂鐢熷紓甯?")
        return None


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
        snapshot_uri = getattr(candidate, "request_snapshot_uri", "")
        replay_command = getattr(candidate, "request_snapshot_replay_command", "")
        if not any([snapshot_path, snapshot_uri, replay_command]):
            continue

        lines: list[str] = []
        if snapshot_path:
            lines.append(f"请求快照路径: {snapshot_path}")
        if snapshot_uri:
            lines.append(f"请求快照链接: {snapshot_uri}")
        if replay_command:
            lines.append(f"重放命令: {replay_command}")
        if lines:
            return "\n  " + "\n  ".join(lines)

    return ""
