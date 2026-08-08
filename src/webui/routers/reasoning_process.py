"""推理过程日志浏览接口。"""

from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
import base64
import json
import os
import re
import shutil
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from src.common.data_models.llm_service_data_models import LLMServiceRequest
from src.common.database.database import get_db_session
from src.common.database.database_model import ChatSession, Messages
from src.config.config import config_manager
from src.llm_models.payload_content.context_item import CONTEXT_ITEM_SCHEMA_VERSION, ContextItem
from src.llm_models.payload_content.context_protocol import ContextProtocolMode, validate_context_items
from src.llm_models.request_snapshot import (
    deserialize_context_item_snapshot,
    serialize_generation_attempt,
    serialize_context_item_snapshot,
)
from src.services.llm_service import generate as generate_llm_response
from src.services.bot_account_service import get_all_bot_account_pairs
from src.services.service_task_resolver import get_available_models
from src.webui.dependencies import require_auth
from src.webui.routers.avatar import build_webui_avatar_url

router = APIRouter(prefix="/reasoning-process", tags=["reasoning-process"], dependencies=[Depends(require_auth)])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROMPT_LOG_ROOT = (PROJECT_ROOT / "logs" / "maisaka_prompt").resolve()
REPLAY_IMAGE_ROOTS = (
    (PROJECT_ROOT / "data" / "images").resolve(),
    (PROJECT_ROOT / "data" / "emoji").resolve(),
    (PROJECT_ROOT / "data" / "prompt_imgs").resolve(),
    (PROJECT_ROOT / "data" / "html_imgs").resolve(),
)
ALLOWED_SUFFIXES = {".txt", ".html", ".json"}
SESSION_CHAT_TYPES = ("group", "private")
ALL_GROUP_SESSIONS = "__all_group_chats__"
BEHAVIOR_REFERENCE_MARKER = "[行为表现参考]"
PROMPT_METADATA_MARKER = "[请求信息]"
PROMPT_SEPARATOR = "=" * 80
PROMPT_METADATA_SCRIPT_PATTERN = re.compile(
    r"<script[^>]*id=[\"']prompt-preview-metadata[\"'][^>]*>(?P<payload>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
MESSAGE_TAG_PATTERN = re.compile(r"<message\b(?P<attrs>[^>]*)>", re.IGNORECASE)
MESSAGE_TAG_ATTR_PATTERN = re.compile(r'([A-Za-z_][\w:-]*)\s*=\s*"([^"]*)"')


def _structured_prompt_content_to_text(content: Any) -> str:
    """将 Prompt JSON 的结构化 content 转为展示/检索用文本。"""

    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
                continue
            if isinstance(item, dict) and str(item.get("type") or "").lower() in {"image", "image_url", "input_image"}:
                image_format = str(item.get("image_format") or item.get("format") or "").strip() or "unknown"
                size_bytes = item.get("size_bytes")
                size_text = f" {size_bytes} B" if isinstance(size_bytes, int) else ""
                parts.append(f"[图片 image/{image_format}{size_text}]")
                continue
            try:
                parts.append(json.dumps(item, ensure_ascii=False, indent=2, default=str))
            except Exception:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    try:
        return json.dumps(content, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(content)


def _format_count_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return "无"
    return str(value)


def _format_jargon_learning_entry(entry: Any, index: int) -> str:
    if not isinstance(entry, dict):
        return f"{index}. {_structured_prompt_content_to_text(entry) or '空条目'}"

    content = str(entry.get("content") or "").strip() or "空词条"
    parts = [f"{index}. {content}"]
    source_id = str(entry.get("source_id") or "").strip()
    if source_id:
        parts.append(f"source_id={source_id}")
    reason = str(entry.get("reason") or "").strip()
    if reason:
        parts.append(f"原因: {reason}")

    raw_content = entry.get("raw_content")
    if isinstance(raw_content, list):
        parts.append(f"原始上下文 {len(raw_content)} 条")
    evidence_messages = entry.get("evidence_messages")
    if isinstance(evidence_messages, list):
        parts.append(f"证据消息 {len(evidence_messages)} 条")
    return "；".join(parts)


def _format_jargon_learning_entries(title: str, entries: Any) -> str:
    if not isinstance(entries, list) or not entries:
        return f"[{title}]\n无"

    lines = [f"[{title}]"]
    lines.extend(_format_jargon_learning_entry(entry, index) for index, entry in enumerate(entries, start=1))
    return "\n".join(lines)


def _is_jargon_learning_update_payload(payload: dict[str, Any]) -> bool:
    return str(payload.get("record_type") or "").strip() == "jargon_learning_update"


def _is_jargon_learning_update_preview_payload(payload: dict[str, Any]) -> bool:
    request = payload.get("request")
    return isinstance(request, dict) and request.get("kind") == "jargon_learning_update"


def _build_jargon_learning_update_preview_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """将黑话学习更新日志包装成 dashboard 可展示的 Prompt JSON 结构。"""

    session_name = str(payload.get("session_name") or payload.get("learning_session_id") or "").strip()
    status = str(payload.get("status") or "").strip() or "unknown"
    wrote_database = payload.get("wrote_database")
    summary_lines = [
        "这是黑话学习更新过程日志，不是可重放的 LLM prompt。",
        f"状态: {status}",
        f"会话: {session_name or '未知'}",
        f"创建时间: {_format_count_value(payload.get('created_at'))}",
        f"学习素材数: {_format_count_value(payload.get('source_item_count'))}",
        f"解析候选: {_format_count_value(payload.get('parsed_entry_count'))}",
        f"接受条目: {_format_count_value(payload.get('accepted_entry_count'))}",
        f"跳过条目: {_format_count_value(payload.get('skipped_entry_count'))}",
        f"新增: {_format_count_value(payload.get('saved'))}",
        f"更新: {_format_count_value(payload.get('updated'))}",
        f"写入数据库: {_format_count_value(wrote_database)}",
    ]
    details = "\n\n".join(
        [
            "\n".join(summary_lines),
            _format_jargon_learning_entries("解析候选", payload.get("parsed_entries")),
            _format_jargon_learning_entries("接受条目", payload.get("accepted_entries")),
            _format_jargon_learning_entries("跳过条目", payload.get("skipped_entries")),
        ]
    )
    preview_payload = {
        "schema_version": 6,
        "request": {
            "kind": "jargon_learning_update",
            "selection_reason": "\n".join(summary_lines),
        },
        "metadata": {},
        "presentation": {"output_title": "黑话学习更新日志"},
        "request_items": [],
        "output_items": [
            {
                "item_type": "ProviderOpaqueItem",
                "meta": {
                    "item_id": f"jargon-learning-update-{payload.get('learning_session_id') or 'unknown'}",
                    "logical_turn_id": None,
                    "timestamp": str(payload.get("created_at") or ""),
                },
                "display_summary": details,
                "provider_type": "maibot_jargon_learning",
            }
        ],
        "tool_definitions": [],
        "generation_attempts": [],
    }
    return preview_payload


def _normalize_prompt_json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if _is_jargon_learning_update_payload(payload):
        return _build_jargon_learning_update_preview_payload(payload)
    return payload


def _is_legacy_jargon_learning_update_file(file_path: Path) -> bool:
    try:
        content_head = file_path.read_text(encoding="utf-8", errors="replace")[:2048]
    except OSError:
        return False
    return '"record_type"' in content_head and '"jargon_learning_update"' in content_head


class ReasoningPromptStageInfo(BaseModel):
    """推理过程类型概要。"""

    name: str
    session_count: int = 0
    latest_modified_at: float = 0


class ReasoningPromptSessionInfo(BaseModel):
    """推理过程日志目录对应的聊天流信息。"""

    name: str
    platform: str = ""
    chat_type: str = ""
    target_id: str = ""
    resolved_session_id: str | None = None
    display_name: str = ""
    account_id: str | None = None
    matched_current_account: bool = False


class ReasoningPromptFile(BaseModel):
    """推理过程日志条目。"""

    stage: str
    session_id: str
    resolved_session_id: str | None = None
    session_display_name: str | None = None
    platform: str | None = None
    chat_type: str | None = None
    target_id: str | None = None
    stem: str
    timestamp: int | None = None
    text_path: str | None = None
    html_path: str | None = None
    json_path: str | None = None
    output_preview: str | None = None
    action_preview: str | None = None
    display_title: str | None = None
    related_json_paths: list[str] = Field(default_factory=list)
    has_behavior_choice_insert: bool = False
    model_name: str | None = None
    duration_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    size: int = 0
    modified_at: float = 0


class ReasoningPromptListResponse(BaseModel):
    """推理过程日志列表响应。"""

    items: list[ReasoningPromptFile]
    total: int
    page: int
    page_size: int
    stages: list[str] = Field(default_factory=list)
    stage_infos: list[ReasoningPromptStageInfo] = Field(default_factory=list)
    sessions: list[str] = Field(default_factory=list)
    session_infos: list[ReasoningPromptSessionInfo] = Field(default_factory=list)
    selected_session: str = ""


class ReasoningPromptStagesResponse(BaseModel):
    """推理过程类型概览响应。"""

    stages: list[str] = Field(default_factory=list)
    stage_infos: list[ReasoningPromptStageInfo] = Field(default_factory=list)


class ReasoningPromptClearStageResponse(BaseModel):
    """清空某类推理过程日志的响应。"""

    stage: str
    deleted_files: int = 0


class ReasoningPromptMessageAvatar(BaseModel):
    """推理过程消息头像信息。"""

    message_id: str
    platform: str
    user_id: str
    display_name: str = ""
    avatar_url: str | None = None


class ReasoningPromptContentResponse(BaseModel):
    """推理过程文本内容响应。"""

    path: str
    content: str
    size: int
    modified_at: float
    model_name: str | None = None
    duration_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    message_avatars: dict[str, ReasoningPromptMessageAvatar] = Field(default_factory=dict)


class ReasoningReplayRequest(BaseModel):
    """推理过程重放请求。"""

    source_path: str | None = Field(default=None, description="原始 prompt JSON 相对路径")
    stage: str = Field(default="", description="原始推理阶段")
    model_name: str = Field(..., min_length=1, description="要用于重放的模型名称")
    item_schema_version: int = Field(default=CONTEXT_ITEM_SCHEMA_VERSION)
    request_items: list[dict[str, Any]] = Field(default_factory=list)
    tool_definitions: list[dict[str, Any]] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)


class ReasoningReplayResponse(BaseModel):
    """推理过程重放响应。"""

    success: bool
    output_items: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: int = 6
    generation_attempts: list[dict[str, Any]] = Field(default_factory=list)
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    duration_ms: float = 0
    error: str | None = None


def _to_safe_relative_path(relative_path: str) -> Path:
    safe_path = Path(relative_path)
    if safe_path.is_absolute() or ".." in safe_path.parts:
        raise HTTPException(status_code=400, detail="路径不合法")
    return safe_path


def _resolve_prompt_log_path(relative_path: str, allowed_suffixes: set[str]) -> Path:
    safe_path = _to_safe_relative_path(relative_path)
    resolved_path = (PROMPT_LOG_ROOT / safe_path).resolve()

    try:
        resolved_path.relative_to(PROMPT_LOG_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="路径不合法") from exc

    if resolved_path.suffix.lower() not in allowed_suffixes:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    return resolved_path


def _relative_posix_path(path: Path) -> str:
    return path.relative_to(PROMPT_LOG_ROOT).as_posix()


def _is_safe_name(name: str) -> bool:
    path = Path(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and len(path.parts) == 1


def _list_stage_names() -> list[str]:
    if not PROMPT_LOG_ROOT.is_dir():
        return []

    return sorted(path.name for path in PROMPT_LOG_ROOT.iterdir() if path.is_dir() and _is_safe_name(path.name))


def _list_stage_infos() -> list[ReasoningPromptStageInfo]:
    if not PROMPT_LOG_ROOT.is_dir():
        return []

    stage_infos: list[ReasoningPromptStageInfo] = []
    for stage_dir in PROMPT_LOG_ROOT.iterdir():
        if not stage_dir.is_dir() or not _is_safe_name(stage_dir.name):
            continue

        session_dirs = [path for path in stage_dir.iterdir() if path.is_dir() and _is_safe_name(path.name)]
        latest_modified_at = 0.0
        for session_dir in session_dirs:
            try:
                latest_modified_at = max(latest_modified_at, session_dir.stat().st_mtime)
            except OSError:
                continue

        stage_infos.append(
            ReasoningPromptStageInfo(
                name=stage_dir.name,
                session_count=len(session_dirs),
                latest_modified_at=latest_modified_at,
            )
        )

    stage_infos.sort(key=lambda item: item.name)
    return stage_infos


def _resolve_stage_name(stage: str) -> str:
    normalized_stage = str(stage or "").strip()
    if not normalized_stage or normalized_stage == "all":
        return "planner"
    if not _is_safe_name(normalized_stage):
        raise HTTPException(status_code=400, detail="阶段名称不合法")
    return normalized_stage


def _list_session_names(stage: str) -> list[str]:
    stage_dir = PROMPT_LOG_ROOT / stage
    if not stage_dir.is_dir():
        return []

    session_dirs = [path for path in stage_dir.iterdir() if path.is_dir() and _is_safe_name(path.name)]
    session_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [path.name for path in session_dirs]


def _resolve_session_name(session: str, sessions: list[str]) -> str:
    normalized_session = str(session or "").strip()
    if normalized_session == ALL_GROUP_SESSIONS:
        return ALL_GROUP_SESSIONS
    if not normalized_session or normalized_session in {"all", "auto"}:
        return sessions[0] if sessions else ""
    if not _is_safe_name(normalized_session):
        raise HTTPException(status_code=400, detail="会话名称不合法")
    return normalized_session if normalized_session in sessions else ""


def _get_configured_platform_accounts() -> set[tuple[str, str]]:
    """读取当前实例全部有效的平台账号对。"""

    return get_all_bot_account_pairs()


def _parse_session_directory_name(name: str) -> tuple[str, str, str] | None:
    """解析 ``platform_type_id`` 形式的日志目录名。"""

    normalized_name = str(name or "").strip()
    for chat_type in SESSION_CHAT_TYPES:
        marker = f"_{chat_type}_"
        if marker not in normalized_name:
            continue

        platform, target_id = normalized_name.split(marker, 1)
        platform = platform.strip()
        target_id = target_id.strip()
        if platform and target_id:
            return platform, chat_type, target_id

    return None


def _get_chat_manager() -> Any:
    from src.chat.message_receive.chat_manager import chat_manager

    return chat_manager


def _session_sort_key(session: Any) -> float:
    timestamp = session.last_active_timestamp or session.created_timestamp
    return timestamp.timestamp() if timestamp else 0.0


def _select_current_account_session(
    sessions: list[Any],
    configured_accounts: set[tuple[str, str]],
) -> tuple[Any | None, bool]:
    configured_matches = [
        session
        for session in sessions
        if (
            str(session.platform or "").strip(),
            str(session.account_id or "").strip(),
        )
        in configured_accounts
    ]
    if configured_matches:
        return max(configured_matches, key=_session_sort_key), True

    legacy_sessions = [session for session in sessions if not str(session.account_id or "").strip()]
    if len(sessions) == 1:
        return sessions[0], False
    if len(legacy_sessions) == 1:
        return legacy_sessions[0], False
    return None, False


def _get_chat_name_from_latest_message(session_id: str, db_session: Session) -> str | None:
    statement = (
        select(Messages).where(col(Messages.session_id) == session_id).order_by(col(Messages.timestamp).desc()).limit(1)
    )
    message = db_session.exec(statement).first()
    if not message:
        return None
    if message.group_id:
        return message.group_name or f"群聊{message.group_id}"

    private_name = message.user_cardname or message.user_nickname or (f"用户{message.user_id}" if message.user_id else None)
    return f"{private_name}的私聊" if private_name else None


def _get_chat_name_from_session_record(chat_session: Any | ChatSession) -> str:
    if chat_session.group_id:
        return chat_session.group_name or f"群聊{chat_session.group_id}"
    if chat_session.user_id:
        private_name = chat_session.user_cardname or chat_session.user_nickname or f"用户{chat_session.user_id}"
        return f"{private_name}的私聊"
    return chat_session.session_id


def _get_chat_display_name(chat_session: Any, db_session: Session) -> str:
    chat_manager = _get_chat_manager()
    if name := chat_manager.get_session_name(chat_session.session_id):
        return name
    session_record_name = _get_chat_name_from_session_record(chat_session)
    if session_record_name != chat_session.session_id:
        return session_record_name
    if name := _get_chat_name_from_latest_message(chat_session.session_id, db_session):
        return name
    return session_record_name


def _get_session_target_key(session: Any) -> tuple[str, str, str] | None:
    platform = str(session.platform or "").strip()
    if not platform:
        return None

    group_id = str(session.group_id or "").strip()
    if group_id:
        return platform, "group", group_id

    user_id = str(session.user_id or "").strip()
    if user_id:
        return platform, "private", user_id

    return None


def _add_session_candidate(
    candidates_by_key: dict[tuple[str, str, str], list[Any]],
    seen_session_ids_by_key: dict[tuple[str, str, str], set[str]],
    session: Any,
) -> None:
    key = _get_session_target_key(session)
    if key is None or key not in candidates_by_key:
        return

    session_id = str(session.session_id or "").strip()
    if not session_id or session_id in seen_session_ids_by_key[key]:
        return

    seen_session_ids_by_key[key].add(session_id)
    candidates_by_key[key].append(session)


def _load_session_candidates_by_target(
    target_keys: set[tuple[str, str, str]],
    db_session: Session,
) -> dict[tuple[str, str, str], list[Any]]:
    """批量加载日志目录可能对应的真实聊天流候选。"""

    candidates_by_key: dict[tuple[str, str, str], list[Any]] = {key: [] for key in target_keys}
    seen_session_ids_by_key: dict[tuple[str, str, str], set[str]] = {key: set() for key in target_keys}
    if not target_keys:
        return candidates_by_key

    chat_manager = _get_chat_manager()
    for session in chat_manager.sessions.values():
        _add_session_candidate(candidates_by_key, seen_session_ids_by_key, session)

    platforms = sorted({platform for platform, _chat_type, _target_id in target_keys})
    if not platforms:
        return candidates_by_key

    statement = select(ChatSession).where(col(ChatSession.platform).in_(platforms))
    for db_instance in db_session.exec(statement).all():
        _add_session_candidate(candidates_by_key, seen_session_ids_by_key, db_instance)

    return candidates_by_key


def _fallback_session_display_name(name: str, parsed: tuple[str, str, str] | None) -> str:
    if parsed is None:
        return name

    platform, chat_type, target_id = parsed
    chat_type_label = "群聊" if chat_type == "group" else "私聊"
    return f"{platform} {chat_type_label} {target_id}"


def _parse_metadata_value(line: str) -> tuple[str, str] | None:
    """解析请求信息行中的键值对。"""

    normalized_line = line.strip()
    if not normalized_line:
        return None

    for separator in ("：", ":"):
        if separator not in normalized_line:
            continue
        key, value = normalized_line.split(separator, 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            return key, value
    return None


def _parse_duration_ms(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), 2)

    duration_text = str(value or "").strip()
    if not duration_text:
        return None

    match = re.search(r"-?\d+(?:\.\d+)?", duration_text)
    if not match:
        return None

    try:
        return round(float(match.group(0)), 2)
    except ValueError:
        return None


def _parse_token_count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, 0)

    token_text = str(value or "").strip().replace(",", "")
    match = re.search(r"\d+", token_text)
    return int(match.group(0)) if match else None


def _normalize_prompt_metadata(raw_metadata: dict[str, Any]) -> dict[str, object]:
    """归一化 prompt 预览元数据字段。"""

    metadata: dict[str, object] = {}
    model_name = str(raw_metadata.get("model_name") or raw_metadata.get("model") or "").strip()
    if model_name:
        metadata["model_name"] = model_name

    duration_ms = _parse_duration_ms(raw_metadata.get("duration_ms"))
    if duration_ms is not None:
        metadata["duration_ms"] = duration_ms

    for token_key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        token_count = raw_metadata.get(token_key)
        if isinstance(token_count, int) and not isinstance(token_count, bool):
            metadata[token_key] = max(token_count, 0)
    if "total_tokens" not in metadata and all(
        token_key in metadata for token_key in ("prompt_tokens", "completion_tokens")
    ):
        metadata["total_tokens"] = int(metadata["prompt_tokens"]) + int(metadata["completion_tokens"])

    return metadata


def _extract_prompt_metadata_from_text(content: str) -> dict[str, object]:
    """从 prompt 原始文本中提取请求模型与推理耗时。"""

    marker_index = content.find(PROMPT_METADATA_MARKER)
    if marker_index < 0:
        return {}

    metadata_text = content[marker_index + len(PROMPT_METADATA_MARKER) :]
    separator_index = metadata_text.find(PROMPT_SEPARATOR)
    if separator_index >= 0:
        metadata_text = metadata_text[:separator_index]

    raw_metadata: dict[str, Any] = {}
    for line in metadata_text.splitlines():
        parsed_value = _parse_metadata_value(line)
        if parsed_value is None:
            continue

        key, value = parsed_value
        if key in {"请求模型", "模型"}:
            raw_metadata["model_name"] = value
        elif key in {"推理耗时", "请求耗时", "耗时"}:
            raw_metadata["duration_ms"] = value
        elif key in {"输入 Token", "输入Token", "Prompt Token"}:
            raw_metadata["prompt_tokens"] = _parse_token_count(value)
        elif key in {"输出 Token", "输出Token", "Completion Token"}:
            raw_metadata["completion_tokens"] = _parse_token_count(value)
        elif key in {"总 Token", "总Token", "Token"}:
            raw_metadata["total_tokens"] = _parse_token_count(value)

    return _normalize_prompt_metadata(raw_metadata)


def _extract_prompt_metadata_from_html(content: str) -> dict[str, object]:
    """从 prompt HTML 预览中提取请求模型与推理耗时。"""

    match = PROMPT_METADATA_SCRIPT_PATTERN.search(content)
    if match:
        try:
            raw_metadata = json.loads(unescape(match.group("payload")).strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_metadata = {}
        if isinstance(raw_metadata, dict):
            metadata = _normalize_prompt_metadata(raw_metadata)
            if metadata:
                return metadata

    # 兼容未来或手写 HTML 中直接暴露出的文本。
    plain_text = unescape(re.sub(r"<[^>]+>", "\n", content))
    return _extract_prompt_metadata_from_text(plain_text)


def _load_prompt_json(file_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return _normalize_prompt_json_payload(payload) if isinstance(payload, dict) else {}


def _parse_message_tag_attrs(text: str) -> list[dict[str, str]]:
    attrs_list: list[dict[str, str]] = []
    for tag_match in MESSAGE_TAG_PATTERN.finditer(text):
        attrs: dict[str, str] = {}
        for attr_match in MESSAGE_TAG_ATTR_PATTERN.finditer(tag_match.group("attrs") or ""):
            key = attr_match.group(1)
            value = unescape(attr_match.group(2))
            if key and value:
                attrs[key] = value
        if attrs:
            attrs_list.append(attrs)
    return attrs_list


def _extract_message_ids_from_prompt_payload(payload: dict[str, Any]) -> list[str]:
    message_ids: list[str] = []
    seen: set[str] = set()

    def append_from_text(value: Any) -> None:
        if not isinstance(value, str) or "<message" not in value:
            return
        for attrs in _parse_message_tag_attrs(value):
            message_id = str(attrs.get("msg_id") or "").strip()
            if message_id and message_id not in seen:
                seen.add(message_id)
                message_ids.append(message_id)

    def append_from_value(value: Any) -> None:
        if isinstance(value, str):
            append_from_text(value)
            return
        if isinstance(value, list):
            for item in value:
                append_from_value(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                append_from_value(item)

    for item in payload.get("request_items") or []:
        append_from_value(item)
    for item in payload.get("output_items") or []:
        append_from_value(item)

    # v1-v4 旧记录只在读取边界扫描，不进入 Dashboard 的运行时模型。
    for message in payload.get("messages") or []:
        if isinstance(message, dict):
            append_from_text(_structured_prompt_content_to_text(message.get("content")))
    if isinstance(payload.get("output"), dict):
        append_from_text(_structured_prompt_content_to_text(payload["output"].get("content")))

    request = payload.get("request")
    if isinstance(request, dict):
        append_from_text(request.get("selection_reason"))

    return message_ids


def _resolve_content_session_info(relative_path: str) -> ReasoningPromptSessionInfo | None:
    try:
        safe_path = _to_safe_relative_path(relative_path)
    except HTTPException:
        return None

    parts = safe_path.parts
    if len(parts) < 3:
        return None

    stage_name, session_name = parts[0], parts[1]
    session_infos = _list_session_infos(stage_name, [session_name])
    return session_infos[0] if session_infos else None


def _message_avatar_from_db_record(message: Messages) -> ReasoningPromptMessageAvatar | None:
    message_id = str(message.message_id or "").strip()
    platform = str(message.platform or "").strip()
    user_id = str(message.user_id or "").strip()
    if not message_id or not platform or not user_id:
        return None

    display_name = str(message.user_cardname or message.user_nickname or "").strip()
    return ReasoningPromptMessageAvatar(
        message_id=message_id,
        platform=platform,
        user_id=user_id,
        display_name=display_name,
        avatar_url=build_webui_avatar_url(platform, user_id),
    )


def _load_message_avatar_map(
    *,
    message_ids: list[str],
    session_info: ReasoningPromptSessionInfo | None,
) -> dict[str, ReasoningPromptMessageAvatar]:
    if not message_ids:
        return {}

    session_ids = {
        value
        for value in {
            session_info.name if session_info else "",
            session_info.resolved_session_id if session_info else "",
        }
        if value
    }

    with get_db_session(auto_commit=False) as db_session:
        statement = select(Messages).where(col(Messages.message_id).in_(message_ids))
        if session_ids:
            statement = statement.where(col(Messages.session_id).in_(session_ids))
        rows = db_session.exec(statement).all()

    avatars: dict[str, ReasoningPromptMessageAvatar] = {}
    for row in rows:
        avatar = _message_avatar_from_db_record(row)
        if avatar is None or avatar.message_id in avatars:
            continue
        avatars[avatar.message_id] = avatar
    return avatars


def _load_prompt_message_avatar_map(relative_path: str, content: str) -> dict[str, ReasoningPromptMessageAvatar]:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    message_ids = _extract_message_ids_from_prompt_payload(payload)
    if not message_ids:
        return {}

    return _load_message_avatar_map(
        message_ids=message_ids,
        session_info=_resolve_content_session_info(relative_path),
    )


def _json_payload_has_behavior_reference(payload: dict[str, Any]) -> bool:
    serialized_payload = json.dumps(payload, ensure_ascii=False, default=str)
    return BEHAVIOR_REFERENCE_MARKER in serialized_payload


def _prompt_record_has_behavior_reference(
    *,
    stage_name: str,
    json_payload: dict[str, Any] | None,
    json_file_path: Path | None,
) -> bool:
    if stage_name != "planner":
        return False

    if json_payload is None and json_file_path is not None:
        json_payload = _load_prompt_json(json_file_path)

    return json_payload is not None and _json_payload_has_behavior_reference(json_payload)


def _extract_prompt_metadata_from_json_payload(payload: dict[str, Any]) -> dict[str, object]:
    raw_metadata = payload.get("metadata")
    metadata = _normalize_prompt_metadata(raw_metadata if isinstance(raw_metadata, dict) else {})
    if all(token_key in metadata for token_key in ("prompt_tokens", "completion_tokens", "total_tokens")):
        return metadata

    raw_attempts = payload.get("generation_attempts")
    attempts = raw_attempts if isinstance(raw_attempts, list) else []
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        trace = attempt.get("trace")
        if isinstance(trace, dict):
            trace_tokens = {
                token_key: trace.get(token_key)
                for token_key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            if any(isinstance(token_count, int) and token_count > 0 for token_count in trace_tokens.values()):
                for token_key, token_count in trace_tokens.items():
                    if token_key not in metadata and isinstance(token_count, int) and not isinstance(token_count, bool):
                        metadata[token_key] = max(token_count, 0)

        wire_response = attempt.get("wire_response")
        raw_usage = wire_response.get("usage") if isinstance(wire_response, dict) else None
        if isinstance(raw_usage, dict):
            usage_tokens = {
                "prompt_tokens": raw_usage.get("prompt_tokens", raw_usage.get("input_tokens")),
                "completion_tokens": raw_usage.get("completion_tokens", raw_usage.get("output_tokens")),
                "total_tokens": raw_usage.get("total_tokens"),
            }
            for token_key, token_count in usage_tokens.items():
                if token_key not in metadata and isinstance(token_count, int) and not isinstance(token_count, bool):
                    metadata[token_key] = max(token_count, 0)

        if all(token_key in metadata for token_key in ("prompt_tokens", "completion_tokens", "total_tokens")):
            break
    return _normalize_prompt_metadata(metadata)


def _extract_llm_error_display_title(payload: dict[str, Any]) -> str:
    """生成 LLM 异常记录在文件列表中的简短摘要。"""

    raw_metadata = payload.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    status = str(metadata.get("status") or "").strip()
    status_labels = {
        "failed": "请求失败",
        "final_failed": "最终失败",
        "retrying": "等待重试",
        "switching_model": "切换模型",
        "succeeded": "请求成功",
        "succeeded_after_retry": "重试后成功",
    }
    status_label = status_labels.get(status, status or "请求异常")

    raw_attempts = payload.get("generation_attempts")
    if not isinstance(raw_attempts, list):
        raw_attempts = payload.get("attempts")
    attempts = raw_attempts if isinstance(raw_attempts, list) else []
    error_message = ""
    for raw_attempt in reversed(attempts):
        if not isinstance(raw_attempt, dict):
            continue
        raw_error = raw_attempt.get("error")
        if not isinstance(raw_error, dict):
            continue
        error_message = str(raw_error.get("message") or "").strip()
        if error_message:
            break

    return f"{status_label} · {error_message}" if error_message else status_label


def _ensure_replay_model_exists(model_name: str) -> str:
    """确认重放模型存在并返回规范化模型名。"""

    normalized_model_name = model_name.strip()
    if not any(model.name == normalized_model_name for model in config_manager.get_model_config().models):
        raise HTTPException(status_code=404, detail=f"未找到模型: {normalized_model_name}")
    return normalized_model_name


def _resolve_replay_task_name(stage: str, model_name: str) -> str:
    """为重放调试选择一个已存在的任务配置名。"""

    available_tasks = get_available_models()
    normalized_stage = stage.strip()
    if normalized_stage in available_tasks:
        return normalized_stage

    for task_name, task_config in available_tasks.items():
        if model_name in list(task_config.model_list or []):
            return task_name

    if available_tasks:
        return next(iter(available_tasks.keys()))
    raise HTTPException(status_code=500, detail="没有可用的模型任务配置")


def _is_path_in_roots(file_path: Path, roots: tuple[Path, ...]) -> bool:
    resolved_path = file_path.resolve()
    for root in roots:
        try:
            resolved_path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _resolve_replay_image_path(raw_path: str) -> Path:
    """解析结构化 prompt 中的图片引用路径，只允许读取预览图片缓存目录。"""

    normalized_path = str(raw_path or "").strip()
    if not normalized_path:
        raise ValueError("图片引用缺少 image_path")

    candidate = Path(normalized_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved_path = candidate.resolve()
    if not _is_path_in_roots(resolved_path, REPLAY_IMAGE_ROOTS):
        raise ValueError(f"图片引用路径不在允许目录内: {normalized_path}")
    if not resolved_path.is_file():
        raise ValueError(f"图片引用文件不存在: {normalized_path}")
    return resolved_path


def _resolve_replay_image_uri(raw_uri: str) -> Path | None:
    """解析 file:// 图片引用；非 file URI 交给上游图片解析逻辑处理。"""

    normalized_uri = str(raw_uri or "").strip()
    if not normalized_uri.lower().startswith("file:"):
        return None

    parsed_uri = urlparse(normalized_uri)
    uri_path = unquote(parsed_uri.path or "")
    if os.name == "nt" and uri_path.startswith("/") and re.match(r"^/[A-Za-z]:/", uri_path):
        uri_path = uri_path[1:]
    return _resolve_replay_image_path(uri_path)


def _infer_image_format(file_path: Path, fallback_format: Any = None) -> str:
    raw_format = str(fallback_format or "").strip().lower().removeprefix(".")
    if raw_format:
        return "jpeg" if raw_format == "jpg" else raw_format

    suffix_format = file_path.suffix.lower().removeprefix(".")
    if suffix_format:
        return "jpeg" if suffix_format == "jpg" else suffix_format
    return "png"


def _read_replay_image_base64(file_path: Path) -> str:
    return base64.b64encode(file_path.read_bytes()).decode("utf-8")


def _rehydrate_replay_image_part(content_item: dict[str, Any]) -> dict[str, Any] | None:
    """把结构化 JSON 中省略 base64 的图片引用还原为 LLMService 可识别的图片片段。"""

    part_type = str(content_item.get("type", "text")).strip().lower()
    if part_type not in {"image", "image_url", "input_image"}:
        return None

    image_url = content_item.get("image_url")
    if isinstance(image_url, dict):
        image_url_value = image_url.get("url")
    else:
        image_url_value = image_url

    if isinstance(content_item.get("image_base64"), str):
        if isinstance(image_url_value, str) and not image_url_value.startswith("data:image/"):
            return {key: value for key, value in content_item.items() if key != "image_url"}
        return content_item

    if isinstance(image_url_value, str) and image_url_value.startswith("data:image/"):
        return content_item

    image_reference = content_item.get("image_reference")
    image_reference = image_reference if isinstance(image_reference, dict) else {}
    image_path = content_item.get("image_path") or image_reference.get("image_path")
    image_uri = image_url_value or content_item.get("image_uri") or image_reference.get("image_uri")

    resolved_path: Path | None = None
    if isinstance(image_path, str) and image_path.strip():
        resolved_path = _resolve_replay_image_path(image_path)
    elif isinstance(image_uri, str):
        resolved_path = _resolve_replay_image_uri(image_uri)

    if resolved_path is None:
        raise ValueError("图片片段缺少可用于重放的 image_path 或 file:// image_uri")

    return {
        key: value
        for key, value in content_item.items()
        if key not in {"image_url", "image_uri", "image_reference"}
    } | {
        "type": part_type,
        "image_format": _infer_image_format(
            resolved_path,
            content_item.get("image_format") or image_reference.get("image_format"),
        ),
        "image_base64": _read_replay_image_base64(resolved_path),
    }


def _rehydrate_replay_content(value: Any) -> Any:
    if isinstance(value, list):
        return [_rehydrate_replay_content(item) for item in value]
    if not isinstance(value, dict):
        return value

    image_part = _rehydrate_replay_image_part(value)
    if image_part is not None:
        return image_part

    return {key: _rehydrate_replay_content(item) for key, item in value.items()}


def _deserialize_replay_items(raw_items: list[dict[str, Any]]) -> list[ContextItem]:
    """还原 Dashboard 提交的规范 Item 快照，并校验关系与工具协议。"""

    items = [deserialize_context_item_snapshot(_rehydrate_replay_content(raw_item)) for raw_item in raw_items]
    validate_context_items(items, ContextProtocolMode.REQUEST_CONTEXT)
    return items


def _decode_json_string_match(value: str) -> str:
    try:
        decoded = json.loads(f'"{value}"')
    except (TypeError, ValueError, json.JSONDecodeError):
        return value
    return decoded if isinstance(decoded, str) else value


def _extract_prompt_metadata_from_json_head(file_path: Path, read_size: int = 8192) -> dict[str, object]:
    """从 JSON 文件头部轻量提取列表页所需元数据。"""

    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as file:
            content = file.read(read_size)
    except OSError:
        return {}

    raw_metadata: dict[str, Any] = {}
    model_match = re.search(r'"(?:model_name|model)"\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"', content)
    if model_match:
        raw_metadata["model_name"] = _decode_json_string_match(model_match.group("value"))

    duration_match = re.search(r'"duration_ms"\s*:\s*(?P<value>-?\d+(?:\.\d+)?)', content)
    if duration_match:
        raw_metadata["duration_ms"] = duration_match.group("value")

    for token_key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        token_match = re.search(rf'"{token_key}"\s*:\s*(?P<value>\d+)', content)
        if token_match:
            raw_metadata[token_key] = int(token_match.group("value"))

    return _normalize_prompt_metadata(raw_metadata)


def _extract_prompt_metadata(file_path: Path) -> dict[str, object]:
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    suffix = file_path.suffix.lower()
    if suffix == ".json":
        try:
            raw_payload = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return _extract_prompt_metadata_from_json_payload(
            _normalize_prompt_json_payload(raw_payload) if isinstance(raw_payload, dict) else {}
        )
    if suffix == ".html":
        return _extract_prompt_metadata_from_html(content)
    return _extract_prompt_metadata_from_text(content)


def _merge_prompt_metadata(record: dict[str, object], metadata: dict[str, object]) -> None:
    model_name = str(metadata.get("model_name") or "").strip()
    if model_name and not record.get("model_name"):
        record["model_name"] = model_name

    duration_ms = metadata.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and record.get("duration_ms") is None:
        record["duration_ms"] = float(duration_ms)

    for token_key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        token_count = metadata.get(token_key)
        if isinstance(token_count, int) and record.get(token_key) is None:
            record[token_key] = token_count


def _extract_output_block_from_content(content: str) -> str | None:
    """从新版 prompt 预览 txt 内容中提取原始输出结果区块。"""

    marker = "[输出结果]"
    marker_index = content.find(marker)
    if marker_index < 0:
        return None

    output_text = content[marker_index + len(marker) :]
    separator_index = output_text.find(PROMPT_SEPARATOR)
    if separator_index >= 0:
        output_text = output_text[:separator_index]

    output_text = output_text.strip()
    if not output_text:
        return None

    return output_text


def _extract_output_text_from_content(content: str) -> str | None:
    """从新版 prompt 预览 txt 内容中提取完整输出结果。"""

    output_text = _extract_output_block_from_content(content)
    if not output_text:
        return None

    normalized_output = " ".join(line.strip() for line in output_text.splitlines() if line.strip())
    if not normalized_output:
        return None

    return normalized_output


def _extract_output_text(file_path: Path) -> str | None:
    """从新版 prompt 预览 txt 中提取完整输出结果。"""

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    return _extract_output_text_from_content(content)


def _extract_output_text_from_json_payload(payload: dict[str, Any]) -> str | None:
    output_items = payload.get("output_items")
    if isinstance(output_items, list):
        text_parts: list[str] = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("item_type") or "")
            if item_type == "AssistantMessageItem":
                parts = item.get("parts")
                if isinstance(parts, list):
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "text" and isinstance(part.get("text"), str):
                            text_parts.append(part["text"])
                        elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                            text_parts.append(part["refusal"])
            elif item_type == "ProviderOpaqueItem" and isinstance(item.get("display_summary"), str):
                text_parts.append(item["display_summary"])
        normalized_item_text = " ".join(
            line.strip() for text in text_parts for line in text.splitlines() if line.strip()
        )
        if normalized_item_text:
            return normalized_item_text

    # v1-v4 旧记录只在读取边界解析。
    output = payload.get("output")
    if not isinstance(output, dict):
        return None

    content = output.get("content")
    if content in (None, "", []):
        return None
    if isinstance(content, str):
        return " ".join(line.strip() for line in content.splitlines() if line.strip()) or None
    if isinstance(content, dict):
        response_text = str(content.get("response") or "").strip()
        if response_text:
            return " ".join(line.strip() for line in response_text.splitlines() if line.strip()) or None
    derived_text = _structured_prompt_content_to_text(content)
    return " ".join(line.strip() for line in derived_text.splitlines() if line.strip()) or None


def _extract_output_preview_from_json(file_path: Path, max_chars: int = 160) -> str | None:
    return _extract_output_preview_from_json_payload(_load_prompt_json(file_path), max_chars=max_chars)


def _extract_output_preview_from_json_payload(payload: dict[str, Any], max_chars: int = 160) -> str | None:
    normalized_output = _extract_output_text_from_json_payload(payload)
    if not normalized_output:
        return None
    if len(normalized_output) <= max_chars:
        return normalized_output
    return f"{normalized_output[:max_chars].rstrip()}..."


def _extract_output_preview(file_path: Path, max_chars: int = 160) -> str | None:
    """从新版 prompt 预览 txt 中提取输出结果摘要。"""

    normalized_output = _extract_output_text(file_path)
    if not normalized_output:
        return None

    if len(normalized_output) <= max_chars:
        return normalized_output
    return f"{normalized_output[:max_chars].rstrip()}..."


def _extract_action_names_from_tool_calls(raw_tool_calls: Any) -> list[str]:
    """从结构化工具调用中提取动作名称。"""

    if isinstance(raw_tool_calls, dict):
        raw_tool_calls = [raw_tool_calls]
    if not isinstance(raw_tool_calls, list):
        return []

    action_names: list[str] = []
    for tool_call in raw_tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function_info = tool_call.get("function")
        action_name = ""
        if isinstance(function_info, dict):
            action_name = str(function_info.get("name") or "").strip()
        if not action_name:
            action_name = str(tool_call.get("name") or "").strip()
        if action_name:
            action_names.append(action_name)
    return action_names


def _extract_action_preview_from_json(file_path: Path, max_actions: int = 4) -> str | None:
    """从 prompt JSON 预览中提取动作摘要。"""

    return _extract_action_preview_from_json_payload(_load_prompt_json(file_path), max_actions=max_actions)


def _extract_action_preview_from_json_payload(payload: dict[str, Any], max_actions: int = 4) -> str | None:
    """从 prompt JSON payload 中提取动作摘要。"""

    if _is_jargon_learning_update_preview_payload(payload):
        return _extract_output_preview_from_json_payload(payload, max_chars=160)

    action_names: list[str] = []

    output_items = payload.get("output_items")
    if isinstance(output_items, list):
        for item in output_items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("item_type") or "")
            if item_type == "FunctionCallItem" and isinstance(item.get("tool_call"), dict):
                action_name = str(item["tool_call"].get("func_name") or "").strip()
            elif item_type == "ProviderActivityItem":
                action_name = str(item.get("provider_type") or item.get("action_type") or "").strip()
            else:
                action_name = ""
            if action_name:
                action_names.append(action_name)

    if not action_names:
        output = payload.get("output")
        if isinstance(output, dict):
            action_names = _extract_action_names_from_tool_calls(output.get("tool_calls"))

    if not action_names:
        return None

    shown_actions = action_names[:max_actions]
    preview = f"动作：{'、'.join(shown_actions)}"
    if len(action_names) > max_actions:
        preview = f"{preview} 等 {len(action_names)} 个"
    return preview


JARGON_UPDATE_STAGE_ORDER = {"with_context": 0, "content_only": 1, "compare": 2}


def _extract_jargon_learning_update_info(payload: dict[str, Any]) -> tuple[str, str]:
    request = payload.get("request")
    if not isinstance(request, dict):
        return "", ""

    selection_reason = str(request.get("selection_reason") or "")
    jargon_name = ""
    inference_stage = ""
    for raw_line in selection_reason.splitlines():
        line = raw_line.strip()
        if line.startswith("词条:"):
            jargon_name = line.split(":", 1)[1].strip()
            continue
        if line.startswith("推断阶段:"):
            inference_stage = line.split(":", 1)[1].strip()
            continue
    return jargon_name, inference_stage


def _group_jargon_learning_update_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """把同一黑话的一轮三次含义推断合并成一条 WebUI 列表记录。"""

    if not records:
        return records

    prepared_records: list[dict[str, object]] = []
    for record in records:
        if record.get("stage") != "jargon_learning_update" or not isinstance(record.get("json_path"), str):
            prepared_records.append(record)
            continue

        json_file_path = _resolve_record_file_path(record, "json_path", {".json"})
        if json_file_path is None:
            prepared_records.append(record)
            continue

        payload = _load_prompt_json(json_file_path)
        jargon_name, inference_stage = _extract_jargon_learning_update_info(payload)
        if not jargon_name:
            prepared_records.append(record)
            continue

        prepared_record = dict(record)
        prepared_record["display_title"] = jargon_name
        prepared_record["_jargon_name"] = jargon_name
        prepared_record["_inference_stage"] = inference_stage
        prepared_records.append(prepared_record)

    grouped_records: list[dict[str, object]] = []
    pending_groups: dict[tuple[str, str], list[dict[str, object]]] = {}

    def flush_group(group_key: tuple[str, str]) -> None:
        group = pending_groups.pop(group_key, [])
        if not group:
            return

        group.sort(
            key=lambda item: (
                JARGON_UPDATE_STAGE_ORDER.get(str(item.get("_inference_stage") or ""), 99),
                float(item.get("modified_at") or 0),
            )
        )
        base = dict(group[-1])
        related_json_paths = [str(item["json_path"]) for item in group if isinstance(item.get("json_path"), str)]
        base["related_json_paths"] = related_json_paths
        base["size"] = sum(int(item.get("size") or 0) for item in group)
        base["modified_at"] = max(float(item.get("modified_at") or 0) for item in group)
        timestamps = [int(item["timestamp"]) for item in group if isinstance(item.get("timestamp"), int)]
        if timestamps:
            base["timestamp"] = max(timestamps)
        base["stem"] = str(base.get("stem") or "")
        base["action_preview"] = str(base.get("display_title") or "")
        base.pop("_jargon_name", None)
        base.pop("_inference_stage", None)
        grouped_records.append(base)

    for record in sorted(prepared_records, key=lambda item: float(item.get("modified_at") or 0)):
        jargon_name = str(record.get("_jargon_name") or "")
        inference_stage = str(record.get("_inference_stage") or "")
        if not jargon_name:
            grouped_records.append(record)
            continue

        group_key = (str(record.get("session_id") or ""), jargon_name)
        group = pending_groups.get(group_key)
        if group:
            first_modified_at = float(group[0].get("modified_at") or 0)
            stage_names = {str(item.get("_inference_stage") or "") for item in group}
            if (
                abs(float(record.get("modified_at") or 0) - first_modified_at) > 120
                or (inference_stage and inference_stage in stage_names)
                or "compare" in stage_names
            ):
                flush_group(group_key)

        pending_groups.setdefault(group_key, []).append(record)

    for group_key in list(pending_groups):
        flush_group(group_key)

    grouped_records.sort(
        key=lambda item: (
            float(item["modified_at"]),
            int(item["timestamp"]) if isinstance(item.get("timestamp"), int) else 0,
        ),
        reverse=True,
    )
    return grouped_records


def _matches_prompt_file_search(item: ReasoningPromptFile, normalized_search: str) -> bool:
    """判断推理过程条目是否匹配搜索词。"""

    if (
        normalized_search in item.stage.casefold()
        or normalized_search in item.session_id.casefold()
        or normalized_search in (item.session_display_name or "").casefold()
        or normalized_search in (item.resolved_session_id or "").casefold()
        or normalized_search in (item.output_preview or "").casefold()
        or normalized_search in (item.action_preview or "").casefold()
        or normalized_search in (item.display_title or "").casefold()
        or normalized_search in (item.model_name or "").casefold()
        or normalized_search in (str(item.duration_ms) if item.duration_ms is not None else "")
        or normalized_search in (str(item.prompt_tokens) if item.prompt_tokens is not None else "")
        or normalized_search in (str(item.completion_tokens) if item.completion_tokens is not None else "")
        or normalized_search in (str(item.total_tokens) if item.total_tokens is not None else "")
        or normalized_search in item.stem.casefold()
    ):
        return True

    if item.stage != "replyer" or not (item.json_path or item.text_path):
        return False

    try:
        if item.json_path:
            file_path = _resolve_prompt_log_path(item.json_path, {".json"})
            output_text = _extract_output_text_from_json_payload(_load_prompt_json(file_path))
        else:
            file_path = _resolve_prompt_log_path(item.text_path or "", {".txt"})
            output_text = _extract_output_text(file_path)
    except HTTPException:
        return False

    return normalized_search in (output_text or "").casefold()


def _matches_prompt_file_action(item: ReasoningPromptFile, normalized_action: str) -> bool:
    """判断推理过程条目是否匹配动作过滤词。"""

    action_preview = str(item.action_preview or "").strip()
    if not action_preview:
        return False

    for prefix in ("动作：", "动作:"):
        if action_preview.startswith(prefix):
            action_preview = action_preview[len(prefix) :].strip()
            break

    return normalized_action in action_preview.casefold()


def _resolve_reasoning_session_info(
    name: str,
    *,
    configured_accounts: set[tuple[str, str]],
    candidates_by_key: dict[tuple[str, str, str], list[Any]],
    db_session: Session,
) -> ReasoningPromptSessionInfo:
    parsed = _parse_session_directory_name(name)
    if parsed is None:
        return ReasoningPromptSessionInfo(name=name, display_name=name)

    platform, chat_type, target_id = parsed
    matched_sessions = candidates_by_key.get((platform, chat_type, target_id), [])
    matched_session, matched_current_account = _select_current_account_session(matched_sessions, configured_accounts)

    if matched_session is None:
        return ReasoningPromptSessionInfo(
            name=name,
            platform=platform,
            chat_type=chat_type,
            target_id=target_id,
            display_name=_fallback_session_display_name(name, parsed),
        )

    return ReasoningPromptSessionInfo(
        name=name,
        platform=platform,
        chat_type=chat_type,
        target_id=target_id,
        resolved_session_id=matched_session.session_id,
        display_name=_get_chat_display_name(matched_session, db_session),
        account_id=matched_session.account_id,
        matched_current_account=matched_current_account,
    )


def _list_session_infos(stage: str, session_names: list[str] | None = None) -> list[ReasoningPromptSessionInfo]:
    if session_names is None:
        session_names = _list_session_names(stage)
    if not session_names:
        return []

    configured_accounts = _get_configured_platform_accounts()
    parsed_session_names = {
        name: parsed
        for name in session_names
        if (parsed := _parse_session_directory_name(name)) is not None
    }
    target_keys = set(parsed_session_names.values())
    with get_db_session(auto_commit=False) as db_session:
        candidates_by_key = _load_session_candidates_by_target(target_keys, db_session)
        return [
            _resolve_reasoning_session_info(
                name,
                configured_accounts=configured_accounts,
                candidates_by_key=candidates_by_key,
                db_session=db_session,
            )
            for name in session_names
        ]


def _is_group_session_info(session_name: str, session_info: ReasoningPromptSessionInfo | None) -> bool:
    if session_info is not None:
        return session_info.chat_type == "group"

    parsed = _parse_session_directory_name(session_name)
    return parsed is not None and parsed[1] == "group"


def _collect_prompt_file_records_for_session(
    stage: str,
    session: str,
    session_info_map: dict[str, ReasoningPromptSessionInfo],
) -> list[dict[str, object]]:
    session_dir = PROMPT_LOG_ROOT / stage / session
    if not session or not session_dir.is_dir():
        return []

    records: dict[tuple[str, str, str], dict[str, object]] = {}

    with os.scandir(session_dir) as entries:
        for entry in entries:
            if not entry.is_file():
                continue

            file_path = session_dir / entry.name
            file_suffix = file_path.suffix.lower()
            if file_suffix not in ALLOWED_SUFFIXES:
                continue

            try:
                stat = entry.stat()
            except OSError:
                continue

            try:
                relative_path = file_path.relative_to(PROMPT_LOG_ROOT)
            except ValueError:
                continue

            parts = relative_path.parts
            if len(parts) < 3:
                continue

            stage_name, session_id = parts[0], parts[1]
            if (
                stage_name == "jargon_learning_update"
                and file_suffix == ".json"
                and _is_legacy_jargon_learning_update_file(file_path)
            ):
                continue
            stem = file_path.stem
            key = (stage_name, session_id, stem)
            session_info = session_info_map.get(session_id)

            record = records.setdefault(
                key,
                {
                    "stage": stage_name,
                    "session_id": session_id,
                    "resolved_session_id": session_info.resolved_session_id if session_info else None,
                    "session_display_name": session_info.display_name if session_info else None,
                    "platform": session_info.platform if session_info else None,
                    "chat_type": session_info.chat_type if session_info else None,
                    "target_id": session_info.target_id if session_info else None,
                    "stem": stem,
                    "timestamp": int(stem) if stem.isdigit() else None,
                    "text_path": None,
                    "html_path": None,
                    "json_path": None,
                    "output_preview": None,
                    "action_preview": None,
                    "model_name": None,
                    "duration_ms": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "size": 0,
                    "modified_at": 0.0,
                },
            )
            record["size"] = int(record["size"]) + stat.st_size
            record["modified_at"] = max(float(record["modified_at"]), stat.st_mtime)

            if file_suffix == ".txt":
                record["text_path"] = _relative_posix_path(file_path)
            elif file_suffix == ".html":
                record["html_path"] = _relative_posix_path(file_path)
            elif file_suffix == ".json":
                record["json_path"] = _relative_posix_path(file_path)

    items = list(records.values())
    items.sort(
        key=lambda item: (
            float(item["modified_at"]),
            int(item["timestamp"]) if isinstance(item.get("timestamp"), int) else 0,
        ),
        reverse=True,
    )
    return _group_jargon_learning_update_records(items) if stage == "jargon_learning_update" else items


def _collect_prompt_file_records(
    stage: str,
    session: str,
    session_info_map: dict[str, ReasoningPromptSessionInfo],
) -> list[dict[str, object]]:
    if session != ALL_GROUP_SESSIONS:
        return _collect_prompt_file_records_for_session(stage, session, session_info_map)

    items: list[dict[str, object]] = []
    for session_name, session_info in session_info_map.items():
        if not _is_group_session_info(session_name, session_info):
            continue
        items.extend(_collect_prompt_file_records_for_session(stage, session_name, session_info_map))

    items.sort(
        key=lambda item: (
            float(item["modified_at"]),
            int(item["timestamp"]) if isinstance(item.get("timestamp"), int) else 0,
        ),
        reverse=True,
    )
    return items


def _resolve_record_file_path(record: dict[str, object], field_name: str, suffixes: set[str]) -> Path | None:
    relative_path = record.get(field_name)
    if not isinstance(relative_path, str) or not relative_path:
        return None

    try:
        return _resolve_prompt_log_path(relative_path, suffixes)
    except HTTPException:
        return None


def _hydrate_prompt_file_record(
    record: dict[str, object],
    *,
    include_previews: bool = True,
    include_action_preview: bool = False,
    include_output_preview: bool = False,
) -> ReasoningPromptFile:
    hydrated_record = dict(record)
    stage_name = str(hydrated_record["stage"])
    should_extract_action_preview = include_action_preview and stage_name in {"jargon_learning_update", "planner"}
    should_extract_output_preview = include_output_preview and stage_name == "replyer"
    should_extract_llm_error_preview = stage_name == "llm_error"
    json_payload: dict[str, Any] | None = None

    json_file_path = _resolve_record_file_path(hydrated_record, "json_path", {".json"})
    if json_file_path is not None:
        if (
            include_previews
            or should_extract_action_preview
            or should_extract_output_preview
            or should_extract_llm_error_preview
        ):
            json_payload = _load_prompt_json(json_file_path)
            _merge_prompt_metadata(hydrated_record, _extract_prompt_metadata_from_json_payload(json_payload))
            if should_extract_llm_error_preview:
                hydrated_record["display_title"] = _extract_llm_error_display_title(json_payload)
            elif stage_name == "replyer" and (include_previews or should_extract_output_preview):
                hydrated_record["output_preview"] = _extract_output_preview_from_json_payload(json_payload)
            elif stage_name == "planner" or _is_jargon_learning_update_preview_payload(json_payload):
                if not hydrated_record.get("display_title"):
                    hydrated_record["action_preview"] = _extract_action_preview_from_json_payload(json_payload)
        else:
            _merge_prompt_metadata(hydrated_record, _extract_prompt_metadata_from_json_head(json_file_path))

    metadata_missing = not hydrated_record.get("model_name") or hydrated_record.get("duration_ms") is None
    preview_missing = (
        (include_previews or should_extract_output_preview)
        and stage_name == "replyer"
        and not hydrated_record.get("output_preview")
    )

    text_file_path = _resolve_record_file_path(hydrated_record, "text_path", {".txt"})
    if text_file_path is not None and (metadata_missing or preview_missing):
        _merge_prompt_metadata(hydrated_record, _extract_prompt_metadata(text_file_path))
        if stage_name == "replyer" and not hydrated_record.get("output_preview"):
            hydrated_record["output_preview"] = _extract_output_preview(text_file_path)
        metadata_missing = not hydrated_record.get("model_name") or hydrated_record.get("duration_ms") is None

    html_file_path = _resolve_record_file_path(hydrated_record, "html_path", {".html"})
    hydrated_record["has_behavior_choice_insert"] = _prompt_record_has_behavior_reference(
        stage_name=stage_name,
        json_payload=json_payload,
        json_file_path=json_file_path,
    )
    if html_file_path is not None and metadata_missing:
        _merge_prompt_metadata(hydrated_record, _extract_prompt_metadata(html_file_path))

    return ReasoningPromptFile(**hydrated_record)


def _hydrate_prompt_file_records(
    records: list[dict[str, object]],
    *,
    include_previews: bool = True,
    include_action_preview: bool = False,
    include_output_preview: bool = False,
) -> list[ReasoningPromptFile]:
    return [
        _hydrate_prompt_file_record(
            record,
            include_previews=include_previews,
            include_action_preview=include_action_preview,
            include_output_preview=include_output_preview,
        )
        for record in records
    ]


def _deduplicate_replyer_prompt_records(items: list[ReasoningPromptFile]) -> list[ReasoningPromptFile]:
    """隐藏同一次 replyer 输出的重复预览记录。"""

    deduplicated_items: list[ReasoningPromptFile] = []
    seen_indexes: dict[tuple[str, str], list[int]] = {}

    for item in items:
        if item.stage != "replyer" or not item.output_preview:
            deduplicated_items.append(item)
            continue

        key = (item.session_id, item.output_preview.strip())
        existing_index = next(
            (
                index
                for index in seen_indexes.get(key, [])
                if _is_duplicate_replyer_record_time_close(deduplicated_items[index], item)
            ),
            None,
        )
        if existing_index is None:
            seen_indexes.setdefault(key, []).append(len(deduplicated_items))
            deduplicated_items.append(item)
            continue

        existing_item = deduplicated_items[existing_index]
        if _should_replace_duplicate_replyer_record(existing_item, item):
            deduplicated_items[existing_index] = item

    return deduplicated_items


def _is_duplicate_replyer_record_time_close(left: ReasoningPromptFile, right: ReasoningPromptFile) -> bool:
    left_time = left.timestamp
    right_time = right.timestamp
    if left_time is not None and right_time is not None:
        return abs(left_time - right_time) <= 10_000
    return abs(left.modified_at - right.modified_at) <= 10


def _should_replace_duplicate_replyer_record(existing_item: ReasoningPromptFile, candidate_item: ReasoningPromptFile) -> bool:
    if existing_item.model_name and not candidate_item.model_name:
        return False
    if candidate_item.model_name and not existing_item.model_name:
        return True
    if existing_item.duration_ms is not None and candidate_item.duration_ms is None:
        return False
    if candidate_item.duration_ms is not None and existing_item.duration_ms is None:
        return True
    return candidate_item.modified_at > existing_item.modified_at


@router.get("/stages", response_model=ReasoningPromptStagesResponse)
async def list_reasoning_prompt_stages():
    """只列出 logs/maisaka_prompt 下的推理过程类型概览。"""

    stage_infos = _list_stage_infos()
    return ReasoningPromptStagesResponse(
        stages=[item.name for item in stage_infos],
        stage_infos=stage_infos,
    )


@router.delete("/stages/{stage}", response_model=ReasoningPromptClearStageResponse)
async def clear_reasoning_prompt_stage(stage: str):
    """清空指定类型的推理过程日志。"""

    stage_name = _resolve_stage_name(stage)
    stage_dir = (PROMPT_LOG_ROOT / stage_name).resolve()
    if not stage_dir.is_relative_to(PROMPT_LOG_ROOT):
        raise HTTPException(status_code=400, detail="无效的推理过程类型")
    if not stage_dir.exists():
        return ReasoningPromptClearStageResponse(stage=stage_name, deleted_files=0)
    if not stage_dir.is_dir():
        raise HTTPException(status_code=400, detail="推理过程路径不是目录")

    deleted_files = sum(1 for path in stage_dir.rglob("*") if path.is_file())
    shutil.rmtree(stage_dir)
    return ReasoningPromptClearStageResponse(stage=stage_name, deleted_files=deleted_files)


@router.get("/files", response_model=ReasoningPromptListResponse)
async def list_reasoning_prompt_files(
    stage: str = Query("planner"),
    session: str = Query("auto"),
    action: str = Query(""),
    search: str = Query(""),
    target_stem: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
):
    """列出 logs/maisaka_prompt 下的推理过程日志。"""

    stage_infos = _list_stage_infos()
    stages = [item.name for item in stage_infos]
    selected_stage = _resolve_stage_name(stage)
    sessions = _list_session_names(selected_stage)
    selected_session = _resolve_session_name(session, sessions)
    normalized_action = action.strip().casefold()
    normalized_search = search.strip().casefold()
    normalized_target_stem = target_stem.strip()
    # 下拉菜单需要展示全部会话的真实名称，不能只解析当前选中项。
    session_infos = _list_session_infos(selected_stage, sessions)
    session_info_map = {item.name: item for item in session_infos}
    records = _collect_prompt_file_records(selected_stage, selected_session, session_info_map)

    if normalized_action or normalized_search:
        items = _hydrate_prompt_file_records(records, include_previews=True)
        if normalized_action:
            items = [item for item in items if _matches_prompt_file_action(item, normalized_action)]
        if normalized_search:
            items = [item for item in items if _matches_prompt_file_search(item, normalized_search)]
        if selected_stage == "replyer":
            items = _deduplicate_replyer_prompt_records(items)
        total = len(items)
        if normalized_target_stem:
            target_index = next((index for index, item in enumerate(items) if item.stem == normalized_target_stem), -1)
            if target_index >= 0:
                page = target_index // page_size + 1
        start = (page - 1) * page_size
        end = start + page_size
        items = items[start:end]
    elif selected_stage == "replyer":
        items = _hydrate_prompt_file_records(
            records,
            include_previews=False,
            include_output_preview=True,
        )
        items = _deduplicate_replyer_prompt_records(items)
        total = len(items)
        if normalized_target_stem:
            target_index = next((index for index, item in enumerate(items) if item.stem == normalized_target_stem), -1)
            if target_index >= 0:
                page = target_index // page_size + 1
        start = (page - 1) * page_size
        end = start + page_size
        items = items[start:end]
    else:
        total = len(records)
        if normalized_target_stem:
            target_index = next(
                (
                    index
                    for index, record in enumerate(records)
                    if str(record.get("stem") or "") == normalized_target_stem
                ),
                -1,
            )
            if target_index >= 0:
                page = target_index // page_size + 1
        start = (page - 1) * page_size
        end = start + page_size
        items = _hydrate_prompt_file_records(
            records[start:end],
            include_previews=False,
            include_action_preview=True,
            include_output_preview=True,
        )

    return ReasoningPromptListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        stages=stages,
        stage_infos=stage_infos,
        sessions=sessions,
        session_infos=session_infos,
        selected_session=selected_session,
    )


@router.get("/file", response_model=ReasoningPromptContentResponse)
async def get_reasoning_prompt_file(path: str = Query(...)):
    """读取推理过程 txt/json 日志内容。"""

    file_path = _resolve_prompt_log_path(path, {".txt", ".json"})
    stat = file_path.stat()
    content = file_path.read_text(encoding="utf-8", errors="replace")
    if file_path.suffix.lower() == ".json":
        try:
            raw_payload = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_payload = {}
        payload = _normalize_prompt_json_payload(raw_payload) if isinstance(raw_payload, dict) else {}
        metadata = _extract_prompt_metadata_from_json_payload(payload)
        if isinstance(raw_payload, dict) and _is_jargon_learning_update_payload(raw_payload):
            content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        message_avatars = _load_prompt_message_avatar_map(path, content)
    else:
        metadata = _extract_prompt_metadata(file_path)
        message_avatars = {}

    return ReasoningPromptContentResponse(
        path=_relative_posix_path(file_path),
        content=content,
        size=stat.st_size,
        modified_at=stat.st_mtime,
        model_name=metadata.get("model_name") if isinstance(metadata.get("model_name"), str) else None,
        duration_ms=metadata.get("duration_ms") if isinstance(metadata.get("duration_ms"), (int, float)) else None,
        prompt_tokens=metadata.get("prompt_tokens") if isinstance(metadata.get("prompt_tokens"), int) else None,
        completion_tokens=(
            metadata.get("completion_tokens") if isinstance(metadata.get("completion_tokens"), int) else None
        ),
        total_tokens=metadata.get("total_tokens") if isinstance(metadata.get("total_tokens"), int) else None,
        message_avatars=message_avatars,
    )


@router.post("/replay", response_model=ReasoningReplayResponse)
async def replay_reasoning_prompt(request: ReasoningReplayRequest):
    """使用可编辑 Context Items 重放一次推理过程请求。"""

    model_name = _ensure_replay_model_exists(request.model_name)
    if request.item_schema_version != CONTEXT_ITEM_SCHEMA_VERSION:
        raise HTTPException(status_code=400, detail="重放 Item schema 版本不匹配")
    if not request.request_items:
        raise HTTPException(status_code=400, detail="重放 Items 不能为空")

    tool_definitions = request.tool_definitions
    if tool_definitions is None and request.source_path:
        source_path = _resolve_prompt_log_path(request.source_path, {".json"})
        source_payload = _load_prompt_json(source_path)
        raw_tool_definitions = source_payload.get("tool_definitions")
        if isinstance(raw_tool_definitions, list):
            tool_definitions = [item for item in raw_tool_definitions if isinstance(item, dict)]
    if tool_definitions is None:
        tool_definitions = []

    try:
        replay_items = _deserialize_replay_items(request.request_items)
    except (TypeError, ValueError) as exc:
        return ReasoningReplayResponse(
            success=False,
            model_name=model_name,
            error=str(exc),
        )

    task_name = _resolve_replay_task_name(request.stage, model_name)
    started_at = time.perf_counter()
    service_result = await generate_llm_response(
        LLMServiceRequest(
            task_name=task_name,
            request_type=f"webui.reasoning_replay.{request.stage or 'unknown'}",
            context_factory=lambda _: replay_items,
            model_name=model_name,
            tool_options=tool_definitions,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
    )
    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    completion = service_result.completion
    return ReasoningReplayResponse(
        success=service_result.success,
        output_items=[serialize_context_item_snapshot(item) for item in completion.output_items],
        generation_attempts=[
            serialize_generation_attempt(attempt)
            for attempt in completion.generation_attempts
        ],
        model_name=completion.model_name or model_name,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        total_tokens=completion.total_tokens,
        prompt_cache_hit_tokens=completion.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=completion.prompt_cache_miss_tokens,
        duration_ms=duration_ms,
        error=service_result.error,
    )


@router.get("/html")
async def get_reasoning_prompt_html(path: str = Query(...)):
    """预览推理过程 html 日志内容。"""

    file_path = _resolve_prompt_log_path(path, {".html"})
    return FileResponse(
        file_path,
        media_type="text/html; charset=utf-8",
        headers={"X-Robots-Tag": "noindex, nofollow"},
    )
