"""推理过程日志浏览接口。"""

from html import unescape
from pathlib import Path
from typing import Any
import json
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import ChatSession, Messages
from src.webui.dependencies import require_auth
from src.webui.routers.avatar import build_webui_avatar_url

router = APIRouter(prefix="/reasoning-process", tags=["reasoning-process"], dependencies=[Depends(require_auth)])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROMPT_LOG_ROOT = (PROJECT_ROOT / "logs" / "maisaka_prompt").resolve()
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
    has_behavior_choice_insert: bool = False
    model_name: str | None = None
    duration_ms: float | None = None
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
    message_avatars: dict[str, ReasoningPromptMessageAvatar] = Field(default_factory=dict)


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
    """读取当前配置中的平台账号对。"""

    from src.config.config import global_config

    pairs: set[tuple[str, str]] = set()
    base_platform = str(global_config.bot.platform or "").strip()
    base_account = str(global_config.bot.qq_account or "").strip()
    if base_platform and base_account:
        pairs.add((base_platform, base_account))

    for item in global_config.bot.platforms:
        platform, separator, account_id = str(item or "").partition(":")
        platform = platform.strip()
        account_id = account_id.strip()
        if separator and platform and account_id:
            pairs.add((platform, account_id))

    return pairs


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


def _normalize_prompt_metadata(raw_metadata: dict[str, Any]) -> dict[str, object]:
    """归一化 prompt 预览元数据字段。"""

    metadata: dict[str, object] = {}
    model_name = str(raw_metadata.get("model_name") or raw_metadata.get("model") or "").strip()
    if model_name:
        metadata["model_name"] = model_name

    duration_ms = _parse_duration_ms(raw_metadata.get("duration_ms"))
    if duration_ms is not None:
        metadata["duration_ms"] = duration_ms

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
    return payload if isinstance(payload, dict) else {}


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

    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        append_from_text(message.get("content_text"))
        content = message.get("content")
        if isinstance(content, str):
            append_from_text(content)

    output = payload.get("output")
    if isinstance(output, dict):
        append_from_text(output.get("content_text"))
        content = output.get("content")
        if isinstance(content, str):
            append_from_text(content)

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
    return _normalize_prompt_metadata(raw_metadata if isinstance(raw_metadata, dict) else {})


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
        return _extract_prompt_metadata_from_json_payload(raw_payload if isinstance(raw_payload, dict) else {})
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
    output = payload.get("output")
    if not isinstance(output, dict):
        return None

    output_text = str(output.get("content_text") or "").strip()
    if output_text:
        return " ".join(line.strip() for line in output_text.splitlines() if line.strip())

    content = output.get("content")
    if content in (None, "", []):
        return None
    if isinstance(content, str):
        return " ".join(line.strip() for line in content.splitlines() if line.strip()) or None
    return json.dumps(content, ensure_ascii=False, default=str)


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

    output = payload.get("output")
    action_names: list[str] = []

    if isinstance(output, dict):
        action_names = _extract_action_names_from_tool_calls(output.get("tool_calls"))

    if not action_names:
        return None

    shown_actions = action_names[:max_actions]
    preview = f"动作：{'、'.join(shown_actions)}"
    if len(action_names) > max_actions:
        preview = f"{preview} 等 {len(action_names)} 个"
    return preview


def _matches_prompt_file_search(item: ReasoningPromptFile, normalized_search: str) -> bool:
    """判断推理过程条目是否匹配搜索词。"""

    if (
        normalized_search in item.stage.casefold()
        or normalized_search in item.session_id.casefold()
        or normalized_search in (item.session_display_name or "").casefold()
        or normalized_search in (item.resolved_session_id or "").casefold()
        or normalized_search in (item.output_preview or "").casefold()
        or normalized_search in (item.action_preview or "").casefold()
        or normalized_search in (item.model_name or "").casefold()
        or normalized_search in (str(item.duration_ms) if item.duration_ms is not None else "")
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
    return items


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
    should_extract_action_preview = include_action_preview and stage_name in {"planner", "timing_gate"}
    should_extract_output_preview = include_output_preview and stage_name == "replyer"
    json_payload: dict[str, Any] | None = None

    json_file_path = _resolve_record_file_path(hydrated_record, "json_path", {".json"})
    if json_file_path is not None:
        if include_previews or should_extract_action_preview or should_extract_output_preview:
            json_payload = _load_prompt_json(json_file_path)
            _merge_prompt_metadata(hydrated_record, _extract_prompt_metadata_from_json_payload(json_payload))
            if stage_name == "replyer" and (include_previews or should_extract_output_preview):
                hydrated_record["output_preview"] = _extract_output_preview_from_json_payload(json_payload)
            elif stage_name in {"planner", "timing_gate"}:
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


@router.get("/stages", response_model=ReasoningPromptStagesResponse)
async def list_reasoning_prompt_stages():
    """只列出 logs/maisaka_prompt 下的推理过程类型概览。"""

    stage_infos = _list_stage_infos()
    return ReasoningPromptStagesResponse(
        stages=[item.name for item in stage_infos],
        stage_infos=stage_infos,
    )


@router.get("/files", response_model=ReasoningPromptListResponse)
async def list_reasoning_prompt_files(
    stage: str = Query("planner"),
    session: str = Query("auto"),
    action: str = Query(""),
    search: str = Query(""),
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
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        items = items[start:end]
    else:
        total = len(records)
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
    metadata = _extract_prompt_metadata(file_path)
    content = file_path.read_text(encoding="utf-8", errors="replace")
    message_avatars = _load_prompt_message_avatar_map(path, content) if file_path.suffix.lower() == ".json" else {}

    return ReasoningPromptContentResponse(
        path=_relative_posix_path(file_path),
        content=content,
        size=stat.st_size,
        modified_at=stat.st_mtime,
        model_name=metadata.get("model_name") if isinstance(metadata.get("model_name"), str) else None,
        duration_ms=metadata.get("duration_ms") if isinstance(metadata.get("duration_ms"), (int, float)) else None,
        message_avatars=message_avatars,
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
