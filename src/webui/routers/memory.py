from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import json
import shutil
import uuid

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import col, select
import tomlkit

from src.A_memorix.host_service import a_memorix_host_service
from src.A_memorix.runtime_registry import get_runtime_kernel
from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.common.database.database import get_db_session
from src.common.database.database_model import ChatSession, Messages, PersonInfo
from src.person_info.person_info import resolve_person_id_for_memory
from src.services.memory_service import MemorySearchResult, memory_service
from src.webui.dependencies import require_auth


router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(require_auth)])
compat_router = APIRouter(prefix="/api", tags=["memory-compat"], dependencies=[Depends(require_auth)])
STAGING_ROOT = Path(__file__).resolve().parents[3] / "data" / "memory_upload_staging"


class NodeRequest(BaseModel):
    name: str = Field(..., min_length=1)


class NodeRenameRequest(BaseModel):
    old_name: str = Field(..., min_length=1)
    new_name: str = Field(..., min_length=1)


class EdgeCreateRequest(BaseModel):
    subject: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    object: str = Field(..., min_length=1)
    confidence: float = Field(1.0, ge=0.0)


class EdgeDeleteRequest(BaseModel):
    hash: str = ""
    subject: str = ""
    object: str = ""


class EdgeWeightRequest(BaseModel):
    hash: str = ""
    subject: str = ""
    object: str = ""
    weight: float = Field(..., ge=0.0)


class SourceDeleteRequest(BaseModel):
    source: str = Field(..., min_length=1)


class SourceBatchDeleteRequest(BaseModel):
    sources: list[str] = Field(default_factory=list)


class EpisodeRebuildRequest(BaseModel):
    source: str = ""
    sources: list[str] = Field(default_factory=list)
    all: bool = False


class EpisodeProcessPendingRequest(BaseModel):
    limit: int = Field(20, ge=1, le=200)
    max_retry: int = Field(3, ge=1, le=20)


class ProfileOverrideRequest(BaseModel):
    person_id: str = Field(..., min_length=1)
    override_text: str = ""
    updated_by: str = ""
    source: str = "webui"


class ProfileEvidenceCorrectRequest(BaseModel):
    evidence_type: str = Field(..., min_length=1)
    hash: str = Field(..., min_length=1)
    requested_by: str = "webui"
    reason: str = "profile_evidence_correction"
    refresh: bool = True
    limit: int = Field(12, ge=1, le=100)


class ImportChatTarget(BaseModel):
    """记忆导入可选择的聊天流。"""

    chat_id: str
    chat_name: str
    platform: Optional[str] = None
    group_id: Optional[str] = None
    user_id: Optional[str] = None
    account_id: Optional[str] = None
    scope: Optional[str] = None
    is_group: bool = False
    last_active_at: Optional[float] = None


class ImportChatTargetsResponse(BaseModel):
    success: bool
    data: list[ImportChatTarget]


class MemoryTimelineChat(BaseModel):
    chat_id: str
    chat_name: str
    platform: Optional[str] = None
    group_id: Optional[str] = None
    user_id: Optional[str] = None
    is_group: bool = False


class MemoryTimelineRange(BaseModel):
    time_start: Optional[float] = None
    time_end: Optional[float] = None
    min_time: Optional[float] = None
    max_time: Optional[float] = None


class MemoryTimelineJumpTarget(BaseModel):
    tab: str
    params: dict[str, Any] = Field(default_factory=dict)


class MemoryTimelineEvent(BaseModel):
    event_id: str
    event_type: str
    category: str
    occurred_at: float
    chat_id: str
    chat_name: str
    title: str
    summary: str
    object_count: int = 1
    key_id: str = ""
    source: str = ""
    attribution: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    jump_target: MemoryTimelineJumpTarget


class MemoryTimelineResponse(BaseModel):
    success: bool
    chat: MemoryTimelineChat
    range: MemoryTimelineRange
    items: list[MemoryTimelineEvent]
    summary: dict[str, Any]


class MaintainRequest(BaseModel):
    target: str = Field(..., min_length=1)
    hours: Optional[float] = None


class AutoSaveRequest(BaseModel):
    enabled: bool


class VectorRebuildRequest(BaseModel):
    dry_run: bool = False
    batch_size: int = Field(32, ge=1, le=512)
    include_relations: Optional[bool] = None


class MemoryConfigUpdateRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class MemoryRawConfigUpdateRequest(BaseModel):
    config: str = ""


class TuningApplyProfileRequest(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)
    reason: str = "manual"
    validate_result: bool = Field(default=True, alias="validate")


class TuningApplyBestRequest(BaseModel):
    persist: bool = False
    validate_result: bool = Field(default=True, alias="validate")


class V5ActionRequest(BaseModel):
    target: str = Field(..., min_length=1)
    strength: Optional[float] = Field(default=None, ge=0.0)
    reason: str = ""
    updated_by: str = "webui"


class DeleteActionRequest(BaseModel):
    mode: str = Field(..., min_length=1)
    selector: dict[str, Any] | str = Field(default_factory=dict)
    reason: str = ""
    requested_by: str = "webui"


class DeleteRestoreRequest(BaseModel):
    operation_id: str = ""
    mode: str = ""
    selector: dict[str, Any] | str = Field(default_factory=dict)
    reason: str = ""
    requested_by: str = "webui"


class DeletePurgeRequest(BaseModel):
    grace_hours: Optional[float] = Field(default=None, ge=0.0)
    limit: int = Field(1000, ge=1, le=5000)


class MemoryCorrectionPreviewRequest(BaseModel):
    request_text: str = Field(..., min_length=1)
    scope: str = "person_profile"
    person_id: str = ""
    person_keyword: str = ""
    chat_id: str = ""
    limit: Optional[int] = Field(default=None, ge=1)
    requested_by: str = "webui"
    reason: str = ""


class MemoryCorrectionExecuteRequest(BaseModel):
    plan_id: str = Field(..., min_length=1)
    confirmed: bool = True
    requested_by: str = "webui"
    reason: str = ""


class MemoryCorrectionRollbackRequest(BaseModel):
    requested_by: str = "webui"
    reason: str = ""


FuzzyModifyPreviewRequest = MemoryCorrectionPreviewRequest
FuzzyModifyExecuteRequest = MemoryCorrectionExecuteRequest
FuzzyModifyRollbackRequest = MemoryCorrectionRollbackRequest


class FeedbackRollbackRequest(BaseModel):
    requested_by: str = "webui"
    reason: str = ""


def _build_import_guide_markdown(settings: dict[str, Any]) -> str:
    path_aliases_raw = settings.get("path_aliases")
    path_aliases = path_aliases_raw if isinstance(path_aliases_raw, dict) else {}
    alias_lines = [
        f"- `{name}` -> `{path}`"
        for name, path in sorted(path_aliases.items())
        if str(name).strip() and str(path).strip()
    ]
    if not alias_lines:
        alias_lines = ["- 当前未配置路径别名"]
    return "\n".join(
        [
            "# 长期记忆导入说明",
            "",
            "支持的导入方式：",
            "- 上传文件：适合零散文档、日志、聊天导出文本。",
            "- 粘贴文本：适合一次性导入少量整理好的内容。",
            "- Raw Scan：扫描白名单目录内的原始文本文件。",
            "- LPMM OpenIE / Convert：处理既有 LPMM 数据。",
            "- Temporal Backfill：补回已有数据中的时间信息。",
            "- MaiBot Migration：从宿主数据库迁移历史聊天记忆。",
            "",
            "当前路径别名：",
            *alias_lines,
            "",
            "执行建议：",
            "- 首次导入先小批量试跑，确认切分和抽取结果正常。",
            "- 大批量导入时优先关注任务状态、失败块与重试结果。",
            "- 若路径解析失败，请先检查路径别名与相对路径是否仍然有效。",
        ]
    )


def _unwrap_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    nested = raw.get("payload")
    if isinstance(nested, dict):
        return dict(nested)
    return dict(raw)


def _get_chat_name_from_latest_message(message: Optional[dict[str, Any]]) -> Optional[str]:
    if not message:
        return None
    group_id = str(message.get("group_id") or "").strip()
    if group_id:
        return str(message.get("group_name") or "").strip() or f"群聊{group_id}"
    user_id = str(message.get("user_id") or "").strip()
    private_name = str(
        message.get("user_cardname") or message.get("user_nickname") or (f"用户{user_id}" if user_id else "")
    ).strip()
    return f"{private_name}的私聊" if private_name else None


def _get_chat_name(chat_session: ChatSession, latest_messages: dict[str, dict[str, Any]]) -> str:
    chat_id = str(chat_session.session_id or "").strip()
    try:
        if name := _chat_manager.get_session_name(chat_id):
            return name
    except Exception:
        pass
    if name := _get_chat_name_from_latest_message(latest_messages.get(chat_id)):
        return name
    if chat_session.group_name:
        return chat_session.group_name
    if chat_session.group_id:
        return f"群聊{chat_session.group_id}"
    private_name = chat_session.user_cardname or chat_session.user_nickname or (
        f"用户{chat_session.user_id}" if chat_session.user_id else ""
    )
    return f"{private_name}的私聊" if private_name else chat_id


def _prefetch_latest_messages_by_session(db_session: Any, session_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not session_ids:
        return {}

    statement = (
        select(Messages)
        .where(col(Messages.session_id).in_(session_ids))
        .order_by(col(Messages.session_id).asc(), col(Messages.timestamp).desc())
    )
    latest: dict[str, dict[str, Any]] = {}
    for message in db_session.exec(statement).all():
        chat_id = str(message.session_id or "").strip()
        if chat_id and chat_id not in latest:
            latest[chat_id] = {
                "group_id": message.group_id,
                "group_name": message.group_name,
                "user_id": message.user_id,
                "user_cardname": message.user_cardname,
                "user_nickname": message.user_nickname,
            }
    return latest


def _validate_import_chat_id(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    chat_id = str(normalized.get("chat_id") or "").strip()
    if not chat_id:
        normalized.pop("chat_id", None)
        return normalized
    try:
        if _chat_manager.get_existing_session_by_session_id(chat_id) is not None:
            normalized["chat_id"] = chat_id
            return normalized
    except Exception:
        pass
    with get_db_session() as session:
        chat_session = session.exec(select(ChatSession).where(col(ChatSession.session_id) == chat_id)).first()
    if chat_session is None:
        raise HTTPException(status_code=400, detail=f"聊天流不存在: {chat_id}")
    normalized["chat_id"] = chat_id
    return normalized


def _find_real_chat_session(chat_id: str) -> Optional[ChatSession]:
    token = str(chat_id or "").strip()
    if not token:
        return None
    try:
        managed_session = _chat_manager.get_existing_session_by_session_id(token)
        if managed_session is not None:
            return managed_session
    except Exception:
        pass
    with get_db_session() as session:
        return session.exec(select(ChatSession).where(col(ChatSession.session_id) == token)).first()


def _normalize_chat_lookup_token(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _compact_chat_lookup_tokens(parts: list[Any]) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for part in parts:
        token = str(part or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _get_chat_session_lookup_tokens(
    chat_session: ChatSession,
    latest_messages: dict[str, dict[str, Any]],
) -> list[str]:
    chat_id = str(chat_session.session_id or "").strip()
    latest_message = latest_messages.get(chat_id) or {}
    group_id = str(chat_session.group_id or latest_message.get("group_id") or "").strip()
    user_id = str(chat_session.user_id or latest_message.get("user_id") or "").strip()
    group_name = str(chat_session.group_name or latest_message.get("group_name") or "").strip()
    private_name = str(
        chat_session.user_cardname
        or chat_session.user_nickname
        or latest_message.get("user_cardname")
        or latest_message.get("user_nickname")
        or ""
    ).strip()
    chat_name = _get_chat_name(chat_session, latest_messages)

    return _compact_chat_lookup_tokens(
        [
            chat_id,
            chat_name,
            chat_session.platform,
            chat_session.account_id,
            chat_session.scope,
            group_id,
            group_name,
            f"群聊{group_id}" if group_id else "",
            user_id,
            private_name,
            f"用户{user_id}" if user_id else "",
            f"{private_name}的私聊" if private_name else "",
        ]
    )


def _score_chat_session_lookup(query_token: str, tokens: list[str]) -> int:
    normalized_tokens = [_normalize_chat_lookup_token(token) for token in tokens]
    normalized_tokens = [token for token in normalized_tokens if token]
    if not query_token or not normalized_tokens:
        return 0
    if query_token in normalized_tokens:
        return 100
    if any(token.startswith(query_token) for token in normalized_tokens):
        return 85
    if any(len(token) >= 4 and query_token.startswith(token) for token in normalized_tokens):
        return 75
    if any(query_token in token for token in normalized_tokens):
        return 65
    if any(len(token) >= 4 and token in query_token for token in normalized_tokens):
        return 55
    return 0


def _format_chat_session_lookup_label(chat_session: ChatSession, latest_messages: dict[str, dict[str, Any]]) -> str:
    chat_id = str(chat_session.session_id or "").strip()
    chat_name = _get_chat_name(chat_session, latest_messages)
    group_id = str(chat_session.group_id or "").strip()
    user_id = str(chat_session.user_id or "").strip()
    identifier = group_id or user_id or chat_id
    return f"{chat_name}({identifier})" if identifier and identifier != chat_name else chat_name


def _resolve_memory_correction_chat_id(chat_id: str) -> str:
    raw_chat_id = str(chat_id or "").strip()
    if not raw_chat_id:
        return ""
    real_chat_session = _find_real_chat_session(raw_chat_id)
    if real_chat_session is not None:
        return str(real_chat_session.session_id or raw_chat_id).strip()

    query_token = _normalize_chat_lookup_token(raw_chat_id)
    if not query_token:
        return raw_chat_id

    with get_db_session() as session:
        rows = list(
            session.exec(
                select(ChatSession).order_by(
                    col(ChatSession.last_active_timestamp).desc(),
                    col(ChatSession.created_timestamp).desc(),
                )
            ).all()
        )
        session_ids = [str(chat_session.session_id or "").strip() for chat_session in rows]
        latest_messages = _prefetch_latest_messages_by_session(session, [item for item in session_ids if item])

    scored_rows: list[tuple[int, ChatSession]] = []
    for chat_session in rows:
        session_id = str(chat_session.session_id or "").strip()
        if not session_id:
            continue
        tokens = _get_chat_session_lookup_tokens(chat_session, latest_messages)
        score = _score_chat_session_lookup(query_token, tokens)
        if score > 0:
            scored_rows.append((score, chat_session))

    if not scored_rows:
        return raw_chat_id

    scored_rows.sort(key=lambda item: item[0], reverse=True)
    best_score = scored_rows[0][0]
    best_rows = [chat_session for score, chat_session in scored_rows if score == best_score]
    if len(best_rows) > 1:
        candidates = "、".join(_format_chat_session_lookup_label(item, latest_messages) for item in best_rows[:5])
        raise HTTPException(
            status_code=400,
            detail=f"聊天流匹配不唯一: {raw_chat_id}，请填写更完整的名称、群号、用户 ID 或 session_id。候选：{candidates}",
        )

    return str(best_rows[0].session_id or raw_chat_id).strip()


def _timeline_chat_from_session(chat_session: ChatSession) -> MemoryTimelineChat:
    chat_id = str(chat_session.session_id or "").strip()
    latest_messages: dict[str, dict[str, Any]] = {}
    try:
        with get_db_session() as session:
            latest_messages = _prefetch_latest_messages_by_session(session, [chat_id])
    except Exception:
        latest_messages = {}
    return MemoryTimelineChat(
        chat_id=chat_id,
        chat_name=_get_chat_name(chat_session, latest_messages),
        platform=getattr(chat_session, "platform", None),
        group_id=getattr(chat_session, "group_id", None),
        user_id=getattr(chat_session, "user_id", None),
        is_group=bool(getattr(chat_session, "group_id", None)),
    )


def _timeline_sources_for_chat(chat_id: str) -> set[str]:
    token = str(chat_id or "").strip()
    if not token:
        return set()
    return {
        f"chat_summary:{token}",
        f"maibot.chat_history:{token}",
    }


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _decode_metadata_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, bytes):
        try:
            decoded = json.loads(raw.decode("utf-8"))
            return dict(decoded) if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
            return dict(decoded) if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    return {}


def _decode_json_payload(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return fallback
    return fallback


def _extend_metadata_chat_tokens(tokens: set[str], value: Any) -> None:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _extend_metadata_chat_tokens(tokens, item)
        return

    token = str(value or "").strip()
    if token:
        tokens.add(token)


def _metadata_chat_tokens(metadata: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("chat_id", "session_id", "stream_id", "chat_ids", "session_ids", "stream_ids"):
        _extend_metadata_chat_tokens(tokens, metadata.get(key))
    return tokens


def _metadata_matches_chat(metadata: dict[str, Any], chat_id: str) -> bool:
    token = str(chat_id or "").strip()
    if not token:
        return False
    if token in _metadata_chat_tokens(metadata):
        return True
    nested_candidates = [
        metadata.get("chat"),
        metadata.get("chat_target"),
        metadata.get("source_context"),
        metadata.get("import_context"),
    ]
    for candidate in nested_candidates:
        if isinstance(candidate, dict) and token in _metadata_chat_tokens(candidate):
            return True
    return False


def _source_matches_chat(source: Any, chat_id: str) -> bool:
    token = str(source or "").strip()
    return bool(token and token in _timeline_sources_for_chat(chat_id))


def _paragraph_matches_chat(row: dict[str, Any], chat_id: str) -> tuple[bool, str]:
    metadata = _decode_metadata_payload(row.get("metadata"))
    if _metadata_matches_chat(metadata, chat_id):
        return True, "metadata.chat_id"
    if _source_matches_chat(row.get("source"), chat_id):
        return True, "source"
    return False, ""


def _event_in_range(occurred_at: float, time_start: Optional[float], time_end: Optional[float]) -> bool:
    if time_start is not None and occurred_at < time_start:
        return False
    if time_end is not None and occurred_at > time_end:
        return False
    return True


def _types_match(event: MemoryTimelineEvent, accepted_types: set[str]) -> bool:
    if not accepted_types:
        return True
    return event.event_type in accepted_types or event.category in accepted_types


def _timeline_event(
    *,
    event_type: str,
    category: str,
    occurred_at: float,
    chat: MemoryTimelineChat,
    title: str,
    summary: str,
    jump_target: dict[str, Any],
    object_count: int = 1,
    key_id: str = "",
    source: str = "",
    attribution: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> MemoryTimelineEvent:
    safe_key = key_id or source or title
    event_id = f"{event_type}:{safe_key}:{occurred_at:.3f}"
    return MemoryTimelineEvent(
        event_id=event_id,
        event_type=event_type,
        category=category,
        occurred_at=occurred_at,
        chat_id=chat.chat_id,
        chat_name=chat.chat_name,
        title=title,
        summary=summary,
        object_count=max(1, int(object_count or 1)),
        key_id=str(key_id or ""),
        source=str(source or ""),
        attribution=str(attribution or ""),
        metadata=metadata or {},
        jump_target=MemoryTimelineJumpTarget(
            tab=str(jump_target.get("tab") or "timeline"),
            params=dict(jump_target.get("params") or {}),
        ),
    )


def _paragraph_jump_target(paragraph_hash: str) -> dict[str, Any]:
    token = str(paragraph_hash or "").strip()
    return {"tab": "graph", "params": {"paragraph_hash": token}}


def _delete_jump_target_for_paragraph(paragraph_hash: str, source: str = "") -> dict[str, Any]:
    token = str(paragraph_hash or "").strip()
    if token:
        rows = _query_memory_rows(
            """
            SELECT operation_id
            FROM delete_operation_items
            WHERE item_hash = ?
               OR item_key = ?
               OR payload_json LIKE ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (token, token, f"%{token}%"),
        )
        operation_id = str((rows[0] if rows else {}).get("operation_id") or "").strip()
        if operation_id:
            return {"tab": "delete", "params": {"operation_id": operation_id}}

    params = {"paragraph_hash": token}
    clean_source = str(source or "").strip()
    if clean_source:
        params["source"] = clean_source
    return {"tab": "delete", "params": params}


def _get_memory_metadata_store() -> Any:
    kernel = get_runtime_kernel()
    return getattr(kernel, "metadata_store", None) if kernel is not None else None


def _query_memory_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    metadata_store = _get_memory_metadata_store()
    if metadata_store is None or not hasattr(metadata_store, "query"):
        return []
    try:
        return list(metadata_store.query(sql, params))
    except Exception:
        return []


def _timeline_query_limit(limit: int, multiplier: int, minimum: int) -> Optional[int]:
    if limit <= 0:
        return None
    return max(limit * multiplier, minimum)


def _append_limit(sql: str, limit: Optional[int]) -> str:
    if limit is None:
        return sql
    return f"{sql}\n        LIMIT ?"


def _timeline_paragraph_events(
    *,
    chat: MemoryTimelineChat,
    time_start: Optional[float],
    time_end: Optional[float],
    accepted_types: set[str],
    limit: int,
) -> list[MemoryTimelineEvent]:
    query_limit = _timeline_query_limit(limit, 5, 200)
    rows = _query_memory_rows(
        _append_limit(
            """
        SELECT hash, content, created_at, updated_at, metadata, source, is_deleted, deleted_at
        FROM paragraphs
        ORDER BY COALESCE(updated_at, created_at, 0) DESC
        """,
            query_limit,
        ),
        (query_limit,) if query_limit is not None else (),
    )
    events: list[MemoryTimelineEvent] = []
    for row in rows:
        matched, attribution = _paragraph_matches_chat(row, chat.chat_id)
        if not matched:
            continue
        paragraph_hash = str(row.get("hash") or "").strip()
        source = str(row.get("source") or "").strip()
        content = str(row.get("content") or "").strip()
        preview = content[:80] + ("..." if len(content) > 80 else "")
        created_at = _safe_float(row.get("created_at"))
        updated_at = _safe_float(row.get("updated_at"))
        deleted_at = _safe_float(row.get("deleted_at"))
        is_deleted = bool(int(row.get("is_deleted") or 0))
        paragraph_jump_target = (
            _delete_jump_target_for_paragraph(paragraph_hash, source)
            if is_deleted
            else _paragraph_jump_target(paragraph_hash)
        )
        if created_at is not None and _event_in_range(created_at, time_start, time_end):
            events.append(
                _timeline_event(
                    event_type="paragraph_created",
                    category="paragraph",
                    occurred_at=created_at,
                    chat=chat,
                    title="段落新增",
                    summary=preview or "新增长期记忆段落",
                    key_id=paragraph_hash,
                    source=source,
                    attribution=attribution,
                    metadata={"paragraph_hash": paragraph_hash},
                    jump_target=paragraph_jump_target,
                )
            )
        if (
            updated_at is not None
            and created_at is not None
            and abs(updated_at - created_at) > 1.0
            and _event_in_range(updated_at, time_start, time_end)
        ):
            events.append(
                _timeline_event(
                    event_type="paragraph_updated",
                    category="paragraph",
                    occurred_at=updated_at,
                    chat=chat,
                    title="段落更新",
                    summary=preview or "长期记忆段落内容或元数据更新",
                    key_id=paragraph_hash,
                    source=source,
                    attribution=attribution,
                    metadata={"paragraph_hash": paragraph_hash},
                    jump_target=paragraph_jump_target,
                )
            )
        if is_deleted and deleted_at is not None and _event_in_range(deleted_at, time_start, time_end):
            events.append(
                _timeline_event(
                    event_type="paragraph_deleted",
                    category="paragraph",
                    occurred_at=deleted_at,
                    chat=chat,
                    title="段落被标记删除",
                    summary=preview or "长期记忆段落进入删除状态",
                    key_id=paragraph_hash,
                    source=source,
                    attribution=attribution,
                    metadata={"paragraph_hash": paragraph_hash},
                    jump_target=_delete_jump_target_for_paragraph(paragraph_hash, source),
                )
            )
    return [event for event in events if _types_match(event, accepted_types)]


def _timeline_episode_events(
    *,
    chat: MemoryTimelineChat,
    time_start: Optional[float],
    time_end: Optional[float],
    accepted_types: set[str],
    limit: int,
) -> list[MemoryTimelineEvent]:
    sources = sorted(_timeline_sources_for_chat(chat.chat_id))
    if not sources:
        return []
    placeholders = ",".join("?" for _ in sources)
    query_limit = _timeline_query_limit(limit, 3, 100)
    rows = _query_memory_rows(
        _append_limit(
            f"""
        SELECT episode_id, source, title, summary, paragraph_count, created_at, updated_at, event_time_start, event_time_end
        FROM episodes
        WHERE source IN ({placeholders})
        ORDER BY COALESCE(updated_at, created_at, event_time_start, 0) DESC
        """,
            query_limit,
        ),
        (*sources, *((query_limit,) if query_limit is not None else ())),
    )
    events: list[MemoryTimelineEvent] = []
    for row in rows:
        episode_id = str(row.get("episode_id") or "").strip()
        source = str(row.get("source") or "").strip()
        created_at = _safe_float(row.get("created_at"))
        updated_at = _safe_float(row.get("updated_at"))
        summary = str(row.get("summary") or row.get("title") or "Episode 已生成").strip()
        title = str(row.get("title") or "Episode").strip()
        paragraph_count = int(row.get("paragraph_count") or 1)
        if created_at is not None and _event_in_range(created_at, time_start, time_end):
            events.append(
                _timeline_event(
                    event_type="episode_created",
                    category="episode",
                    occurred_at=created_at,
                    chat=chat,
                    title=f"Episode 新增：{title}",
                    summary=summary,
                    object_count=paragraph_count,
                    key_id=episode_id,
                    source=source,
                    attribution="source",
                    metadata={"episode_id": episode_id},
                    jump_target={"tab": "episodes", "params": {"episode_id": episode_id, "source": source}},
                )
            )
        if (
            updated_at is not None
            and created_at is not None
            and abs(updated_at - created_at) > 1.0
            and _event_in_range(updated_at, time_start, time_end)
        ):
            events.append(
                _timeline_event(
                    event_type="episode_updated",
                    category="episode",
                    occurred_at=updated_at,
                    chat=chat,
                    title=f"Episode 更新：{title}",
                    summary=summary,
                    object_count=paragraph_count,
                    key_id=episode_id,
                    source=source,
                    attribution="source",
                    metadata={"episode_id": episode_id},
                    jump_target={"tab": "episodes", "params": {"episode_id": episode_id, "source": source}},
                )
            )
    return [event for event in events if _types_match(event, accepted_types)]


def _feedback_person_ids(task: dict[str, Any]) -> list[str]:
    candidates: list[Any] = []
    for key in ("decision_payload", "rollback_plan", "rollback_result", "query_snapshot"):
        value = task.get(key)
        if isinstance(value, dict):
            candidates.extend(value.get("person_ids") or [])
            candidates.extend(value.get("profile_person_ids") or [])
            profile_payload = value.get("profile")
            if isinstance(profile_payload, dict):
                candidates.append(profile_payload.get("person_id"))
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = str(candidate or "").strip()
        if token and token not in seen:
            seen.add(token)
            normalized.append(token)
    return normalized


def _timeline_feedback_events(
    *,
    chat: MemoryTimelineChat,
    time_start: Optional[float],
    time_end: Optional[float],
    accepted_types: set[str],
    limit: int,
) -> list[MemoryTimelineEvent]:
    query_limit = _timeline_query_limit(limit, 3, 100)
    rows = _query_memory_rows(
        _append_limit(
            """
        SELECT *
        FROM memory_feedback_tasks
        WHERE session_id = ?
        ORDER BY COALESCE(updated_at, query_timestamp, created_at, 0) DESC
        """,
            query_limit,
        ),
        (chat.chat_id, *((query_limit,) if query_limit is not None else ())),
    )
    events: list[MemoryTimelineEvent] = []
    for row in rows:
        task = dict(row)
        task["query_snapshot"] = _decode_json_payload(task.get("query_snapshot_json"), {})
        task["decision_payload"] = _decode_json_payload(task.get("decision_json"), {})
        task["rollback_plan"] = _decode_json_payload(task.get("rollback_plan_json"), {})
        task["rollback_result"] = _decode_json_payload(task.get("rollback_result_json"), {})
        task_id = str(task.get("id") or "").strip()
        query_tool_id = str(task.get("query_tool_id") or "").strip()
        status = str(task.get("status") or "").strip()
        updated_at = _first_float(task.get("updated_at"), task.get("query_timestamp"), task.get("created_at"))
        if updated_at is not None and _event_in_range(updated_at, time_start, time_end):
            events.append(
                _timeline_event(
                    event_type="feedback_correction_applied",
                    category="feedback",
                    occurred_at=updated_at,
                    chat=chat,
                    title="反馈纠错处理",
                    summary=f"纠错任务状态：{status or '未知'}",
                    object_count=1,
                    key_id=task_id,
                    source=query_tool_id,
                    attribution="feedback.session_id",
                    metadata={"task_id": task_id, "query_tool_id": query_tool_id, "status": status},
                    jump_target={"tab": "feedback", "params": {"task_id": task_id}},
                )
            )
        rolled_back_at = _safe_float(task.get("rolled_back_at"))
        if rolled_back_at is not None and _event_in_range(rolled_back_at, time_start, time_end):
            events.append(
                _timeline_event(
                    event_type="feedback_correction_rollback",
                    category="feedback",
                    occurred_at=rolled_back_at,
                    chat=chat,
                    title="反馈纠错回滚",
                    summary=str(task.get("rollback_reason") or "纠错任务已回滚"),
                    object_count=1,
                    key_id=task_id,
                    source=query_tool_id,
                    attribution="feedback.session_id",
                    metadata={"task_id": task_id, "query_tool_id": query_tool_id},
                    jump_target={"tab": "feedback", "params": {"task_id": task_id}},
                )
            )
        for person_id in _feedback_person_ids(task):
            if updated_at is not None and _event_in_range(updated_at, time_start, time_end):
                events.append(
                    _timeline_event(
                        event_type="profile_updated",
                        category="profile",
                        occurred_at=updated_at,
                        chat=chat,
                        title="相关画像变更",
                        summary="画像操作由该聊天流的反馈纠错记录关联触发",
                        object_count=1,
                        key_id=person_id,
                        source=query_tool_id,
                        attribution="feedback.session_id",
                        metadata={"person_id": person_id, "task_id": task_id},
                        jump_target={"tab": "profiles", "params": {"person_id": person_id}},
                    )
                )
    return [event for event in events if _types_match(event, accepted_types)]


def _operation_payload_matches_chat(value: Any, chat_id: str) -> bool:
    if isinstance(value, dict):
        if _metadata_matches_chat(value, chat_id):
            return True
        source = value.get("source") or value.get("item_key")
        if _source_matches_chat(source, chat_id):
            return True
        paragraph_hash = str(value.get("paragraph_hash") or value.get("item_hash") or value.get("hash") or "").strip()
        if paragraph_hash:
            rows = _query_memory_rows(
                "SELECT hash, metadata, source FROM paragraphs WHERE hash = ? LIMIT 1",
                (paragraph_hash,),
            )
            if rows and _paragraph_matches_chat(rows[0], chat_id)[0]:
                return True
        return any(_operation_payload_matches_chat(item, chat_id) for item in value.values())
    if isinstance(value, list):
        return any(_operation_payload_matches_chat(item, chat_id) for item in value)
    if isinstance(value, str):
        return _source_matches_chat(value, chat_id)
    return False


def _timeline_delete_events(
    *,
    chat: MemoryTimelineChat,
    time_start: Optional[float],
    time_end: Optional[float],
    accepted_types: set[str],
    limit: int,
) -> list[MemoryTimelineEvent]:
    query_limit = _timeline_query_limit(limit, 4, 200)
    rows = _query_memory_rows(
        _append_limit(
            """
        SELECT operation_id, mode, selector, reason, requested_by, status, created_at, restored_at, summary_json
        FROM delete_operations
        ORDER BY COALESCE(restored_at, created_at, 0) DESC
        """,
            query_limit,
        ),
        (query_limit,) if query_limit is not None else (),
    )
    operation_ids = [str(row.get("operation_id") or "").strip() for row in rows]
    operation_ids = [operation_id for operation_id in operation_ids if operation_id]
    items_by_operation: dict[str, list[dict[str, Any]]] = {operation_id: [] for operation_id in operation_ids}
    if operation_ids:
        placeholders = ",".join("?" for _ in operation_ids)
        item_rows = _query_memory_rows(
            f"""
            SELECT operation_id, item_type, item_hash, item_key, payload_json, created_at
            FROM delete_operation_items
            WHERE operation_id IN ({placeholders})
            ORDER BY operation_id ASC, id ASC
            """,
            tuple(operation_ids),
        )
        for item in item_rows:
            operation_id = str(item.get("operation_id") or "").strip()
            if operation_id in items_by_operation:
                items_by_operation[operation_id].append(dict(item))

    events: list[MemoryTimelineEvent] = []
    for row in rows:
        operation_id = str(row.get("operation_id") or "").strip()
        if not operation_id:
            continue
        decoded_items = [
            {
                **dict(item),
                "payload": _decode_json_payload(item.get("payload_json"), {}),
            }
            for item in items_by_operation.get(operation_id, [])
        ]
        summary_payload = _decode_json_payload(row.get("summary_json"), {})
        selector_payload = _decode_json_payload(row.get("selector"), row.get("selector"))
        if not any(
            _operation_payload_matches_chat(candidate, chat.chat_id)
            for candidate in (summary_payload, selector_payload, decoded_items)
        ):
            continue
        item_count = max(1, len(decoded_items))
        created_at = _safe_float(row.get("created_at"))
        restored_at = _safe_float(row.get("restored_at"))
        mode = str(row.get("mode") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if created_at is not None and _event_in_range(created_at, time_start, time_end):
            events.append(
                _timeline_event(
                    event_type="delete_executed",
                    category="delete",
                    occurred_at=created_at,
                    chat=chat,
                    title="删除操作执行",
                    summary=reason or f"删除模式：{mode or '未知'}",
                    object_count=item_count,
                    key_id=operation_id,
                    source=mode,
                    attribution="delete_operation.items",
                    metadata={"operation_id": operation_id, "mode": mode},
                    jump_target={"tab": "delete", "params": {"operation_id": operation_id}},
                )
            )
        if restored_at is not None and _event_in_range(restored_at, time_start, time_end):
            events.append(
                _timeline_event(
                    event_type="delete_restored",
                    category="delete",
                    occurred_at=restored_at,
                    chat=chat,
                    title="删除操作恢复",
                    summary=f"已恢复删除操作：{operation_id}",
                    object_count=item_count,
                    key_id=operation_id,
                    source=mode,
                    attribution="delete_operation.items",
                    metadata={"operation_id": operation_id, "mode": mode},
                    jump_target={"tab": "delete", "params": {"operation_id": operation_id}},
                )
            )
    return [event for event in events if _types_match(event, accepted_types)]


def _timeline_profile_events(
    *,
    chat: MemoryTimelineChat,
    time_start: Optional[float],
    time_end: Optional[float],
    accepted_types: set[str],
    limit: int,
) -> list[MemoryTimelineEvent]:
    query_limit = _timeline_query_limit(limit, 3, 100)
    rows = _query_memory_rows(
        _append_limit(
            """
        SELECT DISTINCT pps.person_id, pps.profile_version, pps.updated_at, pps.source_note
        FROM person_profile_snapshots pps
        JOIN paragraph_entities pe ON pe.entity_hash = pps.person_id OR pe.entity_hash IN (
            SELECT hash FROM entities WHERE name = pps.person_id
        )
        JOIN paragraphs p ON p.hash = pe.paragraph_hash
        ORDER BY pps.updated_at DESC
        """,
            query_limit,
        ),
        (query_limit,) if query_limit is not None else (),
    )
    person_ids = [str(row.get("person_id") or "").strip() for row in rows]
    person_ids = [person_id for person_id in person_ids if person_id]
    paragraphs_by_person: dict[str, list[dict[str, Any]]] = {person_id: [] for person_id in person_ids}
    if person_ids:
        placeholders = ",".join("?" for _ in person_ids)
        paragraph_rows = _query_memory_rows(
            f"""
            SELECT pe.entity_hash, e.name AS entity_name, p.hash, p.metadata, p.source
            FROM paragraph_entities pe
            LEFT JOIN entities e ON e.hash = pe.entity_hash
            JOIN paragraphs p ON p.hash = pe.paragraph_hash
            WHERE pe.entity_hash IN ({placeholders}) OR e.name IN ({placeholders})
            """,
            (*person_ids, *person_ids),
        )
        person_id_set = set(person_ids)
        for paragraph in paragraph_rows:
            entity_hash = str(paragraph.get("entity_hash") or "").strip()
            entity_name = str(paragraph.get("entity_name") or "").strip()
            for candidate in (entity_hash, entity_name):
                if candidate in person_id_set:
                    paragraphs_by_person[candidate].append(dict(paragraph))

    events: list[MemoryTimelineEvent] = []
    for row in rows:
        person_id = str(row.get("person_id") or "").strip()
        paragraph_rows = paragraphs_by_person.get(person_id, [])
        if not any(_paragraph_matches_chat(paragraph, chat.chat_id)[0] for paragraph in paragraph_rows):
            continue
        updated_at = _safe_float(row.get("updated_at"))
        if updated_at is None or not _event_in_range(updated_at, time_start, time_end):
            continue
        events.append(
            _timeline_event(
                event_type="profile_updated",
                category="profile",
                occurred_at=updated_at,
                chat=chat,
                title="相关画像变更",
                summary="人物画像证据包含该聊天流的长期记忆段落",
                object_count=max(1, len(paragraph_rows)),
                key_id=person_id,
                source=str(row.get("source_note") or ""),
                attribution="profile.evidence_paragraph",
                metadata={"person_id": person_id, "profile_version": row.get("profile_version")},
                jump_target={"tab": "profiles", "params": {"person_id": person_id}},
            )
        )
    override_limit = _timeline_query_limit(limit, 1, 100)
    override_rows = _query_memory_rows(
        _append_limit(
            """
        SELECT person_id, updated_at, updated_by, source
        FROM person_profile_overrides
        ORDER BY updated_at DESC
        """,
            override_limit,
        ),
        (override_limit,) if override_limit is not None else (),
    )
    for row in override_rows:
        source = str(row.get("source") or "").strip()
        person_id = str(row.get("person_id") or "").strip()
        updated_at = _safe_float(row.get("updated_at"))
        if updated_at is None or not _event_in_range(updated_at, time_start, time_end):
            continue
        if not _source_matches_chat(source, chat.chat_id) and chat.chat_id not in source:
            continue
        events.append(
            _timeline_event(
                event_type="profile_override_set",
                category="profile",
                occurred_at=updated_at,
                chat=chat,
                title="画像覆写设置",
                summary="人物画像手动覆写与该聊天流来源相关",
                key_id=person_id,
                source=source,
                attribution="profile.override.source",
                metadata={"person_id": person_id},
                jump_target={"tab": "profiles", "params": {"person_id": person_id}},
            )
        )
    return [event for event in events if _types_match(event, accepted_types)]


def _timeline_maintenance_events(
    *,
    chat: MemoryTimelineChat,
    time_start: Optional[float],
    time_end: Optional[float],
    accepted_types: set[str],
    limit: int,
) -> list[MemoryTimelineEvent]:
    query_limit = _timeline_query_limit(limit, 4, 200)
    rows = _query_memory_rows(
        _append_limit(
            """
        SELECT r.hash, r.subject, r.predicate, r.object, r.source_paragraph, r.last_reinforced,
               r.inactive_since, r.protected_until, r.metadata, p.source, p.metadata AS paragraph_metadata
        FROM relations r
        LEFT JOIN paragraphs p ON p.hash = r.source_paragraph
        ORDER BY COALESCE(r.last_reinforced, r.inactive_since, r.protected_until, r.created_at, 0) DESC
        """,
            query_limit,
        ),
        (query_limit,) if query_limit is not None else (),
    )
    events: list[MemoryTimelineEvent] = []
    for row in rows:
        paragraph_row = {"metadata": row.get("paragraph_metadata"), "source": row.get("source")}
        relation_hash = str(row.get("hash") or "").strip()
        matched, attribution = _paragraph_matches_chat(paragraph_row, chat.chat_id)
        if not matched:
            continue
        relation_text = " ".join(str(row.get(key) or "").strip() for key in ("subject", "predicate", "object")).strip()
        source = str(row.get("source") or "").strip()
        for event_type, timestamp_key, title in (
            ("relation_reinforced", "last_reinforced", "关系强化"),
            ("relation_frozen", "inactive_since", "关系冻结"),
            ("relation_protected", "protected_until", "关系保护"),
        ):
            occurred_at = _safe_float(row.get(timestamp_key))
            if occurred_at is None or not _event_in_range(occurred_at, time_start, time_end):
                continue
            events.append(
                _timeline_event(
                    event_type=event_type,
                    category="maintenance",
                    occurred_at=occurred_at,
                    chat=chat,
                    title=title,
                    summary=relation_text or "维护操作影响了该聊天流证据关系",
                    key_id=relation_hash,
                    source=source,
                    attribution=attribution,
                    metadata={"relation_hash": relation_hash, "source_paragraph": row.get("source_paragraph")},
                    jump_target={"tab": "maintenance", "params": {"target": relation_hash or relation_text}},
                )
            )
    return [event for event in events if _types_match(event, accepted_types)]


def _dedupe_timeline_events(events: list[MemoryTimelineEvent]) -> list[MemoryTimelineEvent]:
    seen: set[str] = set()
    deduped: list[MemoryTimelineEvent] = []
    for event in events:
        key = event.event_id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


async def _memory_timeline(
    *,
    chat_id: str,
    time_start: Optional[float],
    time_end: Optional[float],
    types: str,
    limit: int,
) -> MemoryTimelineResponse:
    clean_chat_id = str(chat_id or "").strip()
    if not clean_chat_id:
        raise HTTPException(status_code=400, detail="chat_id 不能为空")
    chat_session = _find_real_chat_session(clean_chat_id)
    if chat_session is None:
        raise HTTPException(status_code=400, detail=f"聊天流不存在: {clean_chat_id}")
    if time_start is not None and time_end is not None and time_start > time_end:
        raise HTTPException(status_code=400, detail="time_start 不能晚于 time_end")

    chat = _timeline_chat_from_session(chat_session)
    safe_limit = max(1, min(500, int(limit or 100)))
    accepted_types = {
        token.strip()
        for token in str(types or "").split(",")
        if token.strip() and token.strip() != "all"
    }
    collectors = (
        _timeline_paragraph_events,
        _timeline_episode_events,
        _timeline_feedback_events,
        _timeline_delete_events,
        _timeline_profile_events,
        _timeline_maintenance_events,
    )
    bound_events: list[MemoryTimelineEvent] = []
    for collector in collectors:
        bound_events.extend(
            collector(
                chat=chat,
                time_start=None,
                time_end=None,
                accepted_types=set(),
                limit=0,
            )
        )
    bound_events = _dedupe_timeline_events(bound_events)
    bound_times = [event.occurred_at for event in bound_events if event.occurred_at is not None]
    min_time = min(bound_times) if bound_times else None
    max_time = max(bound_times) if bound_times else None

    events: list[MemoryTimelineEvent] = []
    for collector in collectors:
        events.extend(
            collector(
                chat=chat,
                time_start=time_start,
                time_end=time_end,
                accepted_types=accepted_types,
                limit=safe_limit,
            )
        )

    events = _dedupe_timeline_events(events)
    events.sort(key=lambda item: item.occurred_at, reverse=True)
    items = events[:safe_limit]
    by_type: dict[str, int] = {}
    for event in items:
        by_type[event.category] = by_type.get(event.category, 0) + 1
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1

    if min_time is None or max_time is None:
        now = datetime.now(tz=timezone.utc)
        fallback_start = (now - timedelta(days=7)).timestamp()
        fallback_end = now.timestamp()
        min_time = min_time or fallback_start
        max_time = max_time or fallback_end

    return MemoryTimelineResponse(
        success=True,
        chat=chat,
        range=MemoryTimelineRange(
            time_start=time_start,
            time_end=time_end,
            min_time=min_time,
            max_time=max_time,
        ),
        items=items,
        summary={
            "total": len(items),
            "by_type": by_type,
        },
    )


async def _import_chat_targets() -> ImportChatTargetsResponse:
    try:
        with get_db_session() as session:
            rows = list(
                session.exec(
                    select(ChatSession).order_by(
                        col(ChatSession.last_active_timestamp).desc(),
                        col(ChatSession.created_timestamp).desc(),
                    )
                ).all()
            )
            session_ids = [str(chat_session.session_id or "").strip() for chat_session in rows]
            latest_messages = _prefetch_latest_messages_by_session(session, [item for item in session_ids if item])
            targets = [
                ImportChatTarget(
                    chat_id=chat_session.session_id,
                    chat_name=_get_chat_name(chat_session, latest_messages),
                    platform=chat_session.platform,
                    group_id=chat_session.group_id,
                    user_id=chat_session.user_id,
                    account_id=chat_session.account_id,
                    scope=chat_session.scope,
                    is_group=bool(chat_session.group_id),
                    last_active_at=chat_session.last_active_timestamp.timestamp()
                    if chat_session.last_active_timestamp
                    else None,
                )
                for chat_session in rows
                if str(chat_session.session_id or "").strip()
            ]
        return ImportChatTargetsResponse(success=True, data=targets)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取导入聊天流失败: {exc}") from exc


async def _graph_get(limit: int) -> dict:
    return await memory_service.graph_admin(action="get_graph", limit=limit)


async def _graph_search(query: str, limit: int) -> dict:
    return await memory_service.graph_admin(action="search", query=query, limit=limit)


async def _graph_get_node_detail(
    node_id: str,
    *,
    relation_limit: int,
    paragraph_limit: int,
    evidence_node_limit: int,
) -> dict:
    payload = await memory_service.graph_admin(
        action="node_detail",
        node_id=node_id,
        relation_limit=relation_limit,
        paragraph_limit=paragraph_limit,
        evidence_node_limit=evidence_node_limit,
    )
    if not bool(payload.get("success", False)):
        raise HTTPException(status_code=404, detail=str(payload.get("error", "未找到节点详情")))
    return payload


async def _graph_get_edge_detail(
    source: str,
    target: str,
    *,
    paragraph_limit: int,
    evidence_node_limit: int,
) -> dict:
    payload = await memory_service.graph_admin(
        action="edge_detail",
        source=source,
        target=target,
        paragraph_limit=paragraph_limit,
        evidence_node_limit=evidence_node_limit,
    )
    if not bool(payload.get("success", False)):
        raise HTTPException(status_code=404, detail=str(payload.get("error", "未找到边详情")))
    return payload


def _trim_memory_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _format_memory_relation(subject: Any, predicate: Any, obj: Any) -> str:
    return " ".join(str(item or "").strip() for item in (subject, predicate, obj) if str(item or "").strip())


def _format_graph_paragraph(row: dict[str, Any], entities: list[str], relations: list[dict[str, Any]]) -> dict[str, Any]:
    content = str(row.get("content") or "").strip()
    return {
        "hash": str(row.get("hash") or "").strip(),
        "content": content,
        "preview": _trim_memory_text(content),
        "source": str(row.get("source") or "").strip(),
        "created_at": _safe_float(row.get("created_at")),
        "updated_at": _safe_float(row.get("updated_at")),
        "entity_count": len(entities),
        "relation_count": len(relations),
        "entities": entities,
        "relations": [_format_memory_relation(item.get("subject"), item.get("predicate"), item.get("object")) for item in relations],
    }


async def _graph_get_paragraph_detail(paragraph_hash: str, evidence_node_limit: int) -> dict:
    token = str(paragraph_hash or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="paragraph_hash 不能为空")
    rows = _query_memory_rows(
        """
        SELECT hash, content, source, created_at, updated_at, metadata, is_deleted, deleted_at
        FROM paragraphs
        WHERE hash = ?
        LIMIT 1
        """,
        (token,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"未找到段落: {token}")

    paragraph_row = dict(rows[0])
    if bool(int(paragraph_row.get("is_deleted") or 0)) or paragraph_row.get("deleted_at") is not None:
        raise HTTPException(status_code=404, detail=f"段落已删除: {token}")

    entity_rows = [
        dict(row)
        for row in _query_memory_rows(
            """
            SELECT e.hash, e.name, pe.mention_count
            FROM paragraph_entities pe
            LEFT JOIN entities e ON e.hash = pe.entity_hash
            WHERE pe.paragraph_hash = ?
            ORDER BY COALESCE(pe.mention_count, 1) DESC, e.name ASC
            """,
            (token,),
        )
    ]
    relation_rows = [
        dict(row)
        for row in _query_memory_rows(
            """
            SELECT r.hash, r.subject, r.predicate, r.object, r.confidence
            FROM paragraph_relations pr
            JOIN relations r ON r.hash = pr.relation_hash
            WHERE pr.paragraph_hash = ?
              AND (r.is_inactive IS NULL OR r.is_inactive = 0)
            ORDER BY r.confidence DESC, r.created_at DESC
            """,
            (token,),
        )
    ]
    entities = [str(row.get("name") or row.get("hash") or "").strip() for row in entity_rows]
    entities = [name for name in entities if name]
    paragraph = _format_graph_paragraph(paragraph_row, entities, relation_rows)

    nodes: list[dict[str, Any]] = [
        {
            "id": f"paragraph:{token}",
            "type": "paragraph",
            "content": str(paragraph_row.get("content") or ""),
            "metadata": {
                "hash": token,
                "source": paragraph.get("source"),
                "updated_at": paragraph.get("updated_at"),
                "entity_count": len(entities),
                "relation_count": len(relation_rows),
                "preview": paragraph.get("preview"),
            },
        }
    ]
    edges: list[dict[str, Any]] = []
    node_ids = {f"paragraph:{token}"}

    for row in entity_rows:
        entity_name = str(row.get("name") or row.get("hash") or "").strip()
        if not entity_name:
            continue
        node_id = f"entity:{entity_name}"
        if node_id not in node_ids:
            node_ids.add(node_id)
            nodes.append({"id": node_id, "type": "entity", "content": entity_name, "metadata": {"entity_name": entity_name}})
        mention_count = int(row.get("mention_count") or 1)
        edges.append(
            {
                "source": f"paragraph:{token}",
                "target": node_id,
                "kind": "mentions",
                "label": f"提及 ×{mention_count}" if mention_count > 1 else "提及",
                "weight": float(max(1, mention_count)),
            }
        )

    for row in relation_rows:
        relation_hash = str(row.get("hash") or "").strip()
        if not relation_hash:
            continue
        relation_node_id = f"relation:{relation_hash}"
        relation_text = _format_memory_relation(row.get("subject"), row.get("predicate"), row.get("object"))
        if relation_node_id not in node_ids:
            node_ids.add(relation_node_id)
            nodes.append(
                {
                    "id": relation_node_id,
                    "type": "relation",
                    "content": relation_text,
                    "metadata": {
                        "hash": relation_hash,
                        "subject": str(row.get("subject") or "").strip(),
                        "predicate": str(row.get("predicate") or "").strip(),
                        "object": str(row.get("object") or "").strip(),
                        "confidence": float(row.get("confidence") or 0.0),
                        "paragraph_count": 1,
                        "paragraph_hashes": [token],
                        "text": relation_text,
                    },
                }
            )
        edges.append({"source": f"paragraph:{token}", "target": relation_node_id, "kind": "supports", "label": "支撑", "weight": 1.0})

    if len(nodes) > evidence_node_limit:
        kept_ids = {node["id"] for node in nodes[:evidence_node_limit]}
        nodes = [node for node in nodes if node["id"] in kept_ids]
        edges = [edge for edge in edges if edge["source"] in kept_ids and edge["target"] in kept_ids]

    return {
        "success": True,
        "paragraph": paragraph,
        "evidence_graph": {
            "nodes": nodes,
            "edges": edges,
            "focus_entities": entities,
        },
    }


async def _graph_create_node(payload: NodeRequest) -> dict:
    return await memory_service.graph_admin(action="create_node", name=payload.name)


async def _graph_delete_node(payload: NodeRequest) -> dict:
    return await memory_service.graph_admin(action="delete_node", name=payload.name)


async def _graph_rename_node(payload: NodeRenameRequest) -> dict:
    return await memory_service.graph_admin(action="rename_node", old_name=payload.old_name, new_name=payload.new_name)


async def _graph_create_edge(payload: EdgeCreateRequest) -> dict:
    return await memory_service.graph_admin(
        action="create_edge",
        subject=payload.subject,
        predicate=payload.predicate,
        object=payload.object,
        confidence=payload.confidence,
    )


async def _graph_delete_edge(payload: EdgeDeleteRequest) -> dict:
    return await memory_service.graph_admin(
        action="delete_edge",
        hash=payload.hash,
        subject=payload.subject,
        object=payload.object,
    )


async def _graph_update_edge_weight(payload: EdgeWeightRequest) -> dict:
    return await memory_service.graph_admin(
        action="update_edge_weight",
        hash=payload.hash,
        subject=payload.subject,
        object=payload.object,
        weight=payload.weight,
    )


async def _source_list() -> dict:
    return await memory_service.source_admin(action="list")


async def _source_delete(payload: SourceDeleteRequest) -> dict:
    return await memory_service.source_admin(action="delete", source=payload.source)


async def _source_batch_delete(payload: SourceBatchDeleteRequest) -> dict:
    return await memory_service.source_admin(action="batch_delete", sources=payload.sources)


async def _query_aggregate(
    query: str,
    *,
    limit: int,
    chat_id: str,
    person_id: str,
    time_start: float | None,
    time_end: float | None,
) -> dict:
    result: MemorySearchResult = await memory_service.search(
        query,
        limit=limit,
        mode="aggregate",
        chat_id=chat_id,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
        respect_filter=False,
    )
    return {"success": True, **result.to_dict()}


async def _episode_list(
    *,
    query: str,
    limit: int,
    source: str,
    person_id: str,
    platform: str,
    user_id: str,
    time_start: float | None,
    time_end: float | None,
) -> dict:
    clean_person_id = str(person_id or "").strip()
    if not clean_person_id and str(platform or "").strip() and str(user_id or "").strip():
        clean_person_id = resolve_person_id_for_memory(
            platform=str(platform or "").strip(),
            user_id=str(user_id or "").strip(),
            strict_known=False,
        )

    payload = await memory_service.episode_admin(
        action="list",
        query=query,
        limit=limit,
        source=source,
        person_id=clean_person_id,
        time_start=time_start,
        time_end=time_end,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload

    items = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            items.append(item)
            continue
        items.append(_enrich_episode_person_name(item))

    payload = dict(payload)
    payload["items"] = items
    return payload


async def _episode_get(episode_id: str) -> dict:
    payload = await memory_service.episode_admin(action="get", episode_id=episode_id)
    if isinstance(payload, dict) and isinstance(payload.get("episode"), dict):
        payload = dict(payload)
        payload["episode"] = _enrich_episode_person_name(payload["episode"])
    return payload


async def _episode_rebuild(payload: EpisodeRebuildRequest) -> dict:
    return await memory_service.episode_admin(
        action="rebuild",
        source=payload.source,
        sources=payload.sources,
        all=payload.all,
    )


async def _episode_status(limit: int) -> dict:
    return await memory_service.episode_admin(action="status", limit=limit)


async def _episode_process_pending(payload: EpisodeProcessPendingRequest) -> dict:
    return await memory_service.episode_admin(
        action="process_pending",
        limit=payload.limit,
        max_retry=payload.max_retry,
    )


async def _profile_query(
    *,
    person_id: str,
    person_keyword: str,
    platform: str,
    user_id: str,
    limit: int,
    force_refresh: bool,
) -> dict:
    clean_person_id = str(person_id or "").strip()
    if not clean_person_id and str(platform or "").strip() and str(user_id or "").strip():
        clean_person_id = resolve_person_id_for_memory(
            platform=str(platform or "").strip(),
            user_id=str(user_id or "").strip(),
            strict_known=False,
        )
    return await memory_service.profile_admin(
        action="query",
        person_id=clean_person_id,
        person_keyword=person_keyword,
        limit=limit,
        force_refresh=force_refresh,
    )


def _get_person_name_for_person_id(person_id: str) -> str:
    clean_person_id = str(person_id or "").strip()
    if not clean_person_id:
        return ""
    try:
        with get_db_session(auto_commit=False) as session:
            statement = select(PersonInfo.person_name).where(col(PersonInfo.person_id) == clean_person_id).limit(1)
            person_name = session.exec(statement).first()
            return str(person_name or "").strip()
    except Exception:
        return ""


def _enrich_episode_person_name(item: dict) -> dict:
    enriched = dict(item)
    item_person_id = str(enriched.get("person_id", "") or "").strip()

    participants = enriched.get("participants")
    if not item_person_id and isinstance(participants, list):
        for participant in participants:
            if isinstance(participant, dict):
                candidate = str(participant.get("person_id", "") or participant.get("id", "") or "").strip()
            else:
                candidate = str(participant or "").strip()
            if candidate:
                item_person_id = candidate
                break

    enriched["person_id"] = item_person_id
    enriched["person_name"] = _get_person_name_for_person_id(item_person_id)
    return enriched


async def _profile_list(limit: int) -> dict:
    payload = await memory_service.profile_admin(action="list", limit=limit)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload

    items = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            items.append(item)
            continue
        enriched = dict(item)
        person_id = str(enriched.get("person_id", "") or "").strip()
        enriched["person_name"] = _get_person_name_for_person_id(person_id)
        items.append(enriched)

    payload = dict(payload)
    payload["items"] = items
    return payload


async def _profile_search(
    *,
    person_id: str,
    person_keyword: str,
    platform: str,
    user_id: str,
    limit: int,
) -> dict:
    clean_person_id = str(person_id or "").strip()
    if not clean_person_id and str(platform or "").strip() and str(user_id or "").strip():
        clean_person_id = resolve_person_id_for_memory(
            platform=str(platform or "").strip(),
            user_id=str(user_id or "").strip(),
            strict_known=False,
        )

    payload = await _profile_list(max(limit, 200))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload

    keyword = str(person_keyword or "").strip().lower()

    def _matches(item: dict) -> bool:
        if clean_person_id and str(item.get("person_id", "") or "").strip() != clean_person_id:
            return False
        if not keyword:
            return True

        override = item.get("manual_override")
        override_text = ""
        if isinstance(override, dict):
            override_text = str(override.get("override_text", "") or override.get("text", "") or "")
        elif isinstance(override, str):
            override_text = override

        haystack = "\n".join(
            [
                str(item.get("person_id", "") or ""),
                str(item.get("person_name", "") or ""),
                str(item.get("profile_text", "") or ""),
                str(item.get("source_note", "") or ""),
                override_text,
            ]
        ).lower()
        return keyword in haystack

    items = [item for item in payload["items"] if isinstance(item, dict) and _matches(item)]
    items = items[:limit]
    return {
        "success": True,
        "items": items,
        "count": len(items),
        "query": {
            "person_id": clean_person_id,
            "person_keyword": person_keyword,
            "platform": platform,
            "user_id": user_id,
        },
    }


async def _profile_set_override(payload: ProfileOverrideRequest) -> dict:
    return await memory_service.profile_admin(
        action="set_override",
        person_id=payload.person_id,
        override_text=payload.override_text,
        updated_by=payload.updated_by,
        source=payload.source,
    )


async def _profile_delete_override(person_id: str) -> dict:
    return await memory_service.profile_admin(action="delete_override", person_id=person_id)


async def _profile_evidence(person_id: str, limit: int, force_refresh: bool) -> dict:
    return await memory_service.profile_admin(
        action="evidence",
        person_id=person_id,
        limit=limit,
        force_refresh=force_refresh,
    )


async def _profile_correct_evidence(person_id: str, payload: ProfileEvidenceCorrectRequest) -> dict:
    return await memory_service.profile_admin(
        action="correct_evidence",
        person_id=person_id,
        evidence_type=payload.evidence_type,
        hash=payload.hash,
        requested_by=payload.requested_by,
        reason=payload.reason,
        refresh=payload.refresh,
        limit=payload.limit,
    )


async def _feedback_list(limit: int, status: str, rollback_status: str, query: str) -> dict:
    statuses = [item.strip() for item in str(status or "").split(",") if item.strip()]
    rollback_statuses = [item.strip() for item in str(rollback_status or "").split(",") if item.strip()]
    return await memory_service.feedback_admin(
        action="list",
        limit=limit,
        statuses=statuses,
        rollback_statuses=rollback_statuses,
        query=query,
    )


async def _feedback_get(task_id: int) -> dict:
    return await memory_service.feedback_admin(action="get", task_id=task_id)


async def _feedback_rollback(task_id: int, payload: FeedbackRollbackRequest) -> dict:
    return await memory_service.feedback_admin(
        action="rollback",
        task_id=task_id,
        requested_by=payload.requested_by,
        reason=payload.reason,
    )


async def _runtime_save() -> dict:
    return await memory_service.runtime_admin(action="save")


async def _runtime_config() -> dict:
    payload = await memory_service.runtime_admin(action="get_config")
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    integration = config.get("integration") if isinstance(config.get("integration"), dict) else {}
    candidate_limit = integration.get("fuzzy_modify_candidate_limit")
    if candidate_limit is not None:
        payload["fuzzy_modify_candidate_limit"] = candidate_limit
    return payload


async def _runtime_self_check(refresh: bool) -> dict:
    return await memory_service.runtime_admin(action="refresh_self_check" if refresh else "self_check")


async def _runtime_auto_save(enabled: bool | None = None) -> dict:
    if enabled is None:
        config = await memory_service.runtime_admin(action="get_config")
        return {"success": bool(config.get("success", False)), "auto_save": bool(config.get("auto_save", False))}
    return await memory_service.runtime_admin(action="set_auto_save", enabled=enabled)


async def _runtime_rebuild_vectors(payload: VectorRebuildRequest) -> dict:
    return await memory_service.runtime_admin(
        action="rebuild_all_vectors",
        timeout_ms=600000,
        dry_run=payload.dry_run,
        batch_size=payload.batch_size,
        include_relations=payload.include_relations,
    )


async def _memory_config_schema() -> dict:
    return {
        "success": True,
        "schema": a_memorix_host_service.get_config_schema(),
        "path": str(a_memorix_host_service.get_config_path()),
    }


async def _memory_config_get() -> dict:
    return {
        "success": True,
        "config": a_memorix_host_service.get_config(),
        "path": str(a_memorix_host_service.get_config_path()),
    }


async def _memory_config_get_raw() -> dict:
    raw_payload = a_memorix_host_service.get_raw_config_with_meta()
    return {
        "success": True,
        "config": str(raw_payload.get("config", "") or ""),
        "exists": bool(raw_payload.get("exists", False)),
        "using_default": bool(raw_payload.get("using_default", False)),
        "path": str(a_memorix_host_service.get_config_path()),
    }


async def _memory_config_update(payload: MemoryConfigUpdateRequest) -> dict:
    return await a_memorix_host_service.update_config(payload.config)


async def _memory_config_update_raw(payload: MemoryRawConfigUpdateRequest) -> dict:
    try:
        tomlkit.loads(payload.config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"TOML 格式错误: {exc}") from exc
    return await a_memorix_host_service.update_raw_config(payload.config)


async def _maintenance_recycle_bin(limit: int) -> dict:
    return await memory_service.get_recycle_bin(limit=limit)


async def _maintenance_restore(payload: MaintainRequest) -> dict:
    return (await memory_service.restore_memory(target=payload.target)).to_dict()


async def _maintenance_reinforce(payload: MaintainRequest) -> dict:
    return (await memory_service.reinforce_memory(target=payload.target)).to_dict()


async def _maintenance_freeze(payload: MaintainRequest) -> dict:
    return (await memory_service.freeze_memory(target=payload.target)).to_dict()


async def _maintenance_protect(payload: MaintainRequest) -> dict:
    return (await memory_service.protect_memory(target=payload.target, hours=payload.hours)).to_dict()


async def _v5_status(target: str, limit: int) -> dict:
    return await memory_service.v5_admin(action="status", target=target, limit=limit)


async def _v5_recycle_bin(limit: int) -> dict:
    return await memory_service.v5_admin(action="recycle_bin", limit=limit)


async def _v5_action(action: str, payload: V5ActionRequest) -> dict:
    kwargs: dict[str, Any] = {
        "target": payload.target,
        "reason": payload.reason,
        "updated_by": payload.updated_by,
    }
    if payload.strength is not None:
        kwargs["strength"] = payload.strength
    return await memory_service.v5_admin(action=action, **kwargs)


async def _delete_preview(payload: DeleteActionRequest) -> dict:
    return await memory_service.delete_admin(action="preview", mode=payload.mode, selector=payload.selector)


async def _delete_execute(payload: DeleteActionRequest) -> dict:
    return await memory_service.delete_admin(
        action="execute",
        mode=payload.mode,
        selector=payload.selector,
        reason=payload.reason,
        requested_by=payload.requested_by,
    )


async def _delete_restore(payload: DeleteRestoreRequest) -> dict:
    return await memory_service.delete_admin(
        action="restore",
        mode=payload.mode,
        selector=payload.selector,
        operation_id=payload.operation_id,
        reason=payload.reason,
        requested_by=payload.requested_by,
    )


async def _delete_list(limit: int, mode: str) -> dict:
    return await memory_service.delete_admin(action="list_operations", limit=limit, mode=mode)


async def _delete_get(operation_id: str) -> dict:
    return await memory_service.delete_admin(action="get_operation", operation_id=operation_id)


async def _delete_purge(payload: DeletePurgeRequest) -> dict:
    return await memory_service.delete_admin(
        action="purge",
        grace_hours=payload.grace_hours,
        limit=payload.limit,
    )


async def _memory_correction_preview(payload: MemoryCorrectionPreviewRequest) -> dict:
    resolved_chat_id = _resolve_memory_correction_chat_id(payload.chat_id)
    return await memory_service.memory_correction_admin(
        action="preview",
        request_text=payload.request_text,
        scope=payload.scope,
        person_id=payload.person_id,
        person_keyword=payload.person_keyword,
        chat_id=resolved_chat_id,
        limit=payload.limit,
        requested_by=payload.requested_by,
        reason=payload.reason,
    )


async def _memory_correction_execute(payload: MemoryCorrectionExecuteRequest) -> dict:
    return await memory_service.memory_correction_admin(
        action="execute",
        plan_id=payload.plan_id,
        confirmed=payload.confirmed,
        requested_by=payload.requested_by,
        reason=payload.reason,
    )


async def _memory_correction_rollback(plan_id: str, payload: MemoryCorrectionRollbackRequest) -> dict:
    return await memory_service.memory_correction_admin(
        action="rollback",
        plan_id=plan_id,
        requested_by=payload.requested_by,
        reason=payload.reason,
    )


async def _fuzzy_modify_preview(payload: FuzzyModifyPreviewRequest) -> dict:
    return await _memory_correction_preview(payload)


async def _fuzzy_modify_execute(payload: FuzzyModifyExecuteRequest) -> dict:
    return await _memory_correction_execute(payload)


async def _fuzzy_modify_rollback(plan_id: str, payload: FuzzyModifyRollbackRequest) -> dict:
    return await _memory_correction_rollback(plan_id, payload)


async def _import_settings() -> dict:
    return await memory_service.import_admin(action="get_settings")


async def _import_path_aliases() -> dict:
    return await memory_service.import_admin(action="get_path_aliases")


async def _import_guide() -> dict:
    payload = await memory_service.import_admin(action="get_guide")
    if not isinstance(payload, dict):
        payload = {"success": False, "error": "invalid_payload"}
    if isinstance(payload.get("content"), str):
        return payload

    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else None
    if settings is None:
        settings_payload = await memory_service.import_admin(action="get_settings")
        settings = settings_payload.get("settings") if isinstance(settings_payload.get("settings"), dict) else {}

    return {
        "success": True,
        "source": "local",
        "path": "generated://memory_import_guide",
        "content": _build_import_guide_markdown(settings or {}),
        "settings": settings or {},
    }


async def _import_resolve_path(payload: dict[str, Any]) -> dict:
    return await memory_service.import_admin(action="resolve_path", **_unwrap_payload(payload))


async def _import_create(action: str, payload: dict[str, Any]) -> dict:
    return await memory_service.import_admin(action=action, **_validate_import_chat_id(_unwrap_payload(payload)))


async def _import_list(limit: int) -> dict:
    listing = await memory_service.import_admin(action="list", limit=limit)
    if not isinstance(listing, dict):
        listing = {"success": False, "items": []}
    settings_payload = await memory_service.import_admin(action="get_settings")
    settings = settings_payload.get("settings") if isinstance(settings_payload.get("settings"), dict) else {}
    listing.setdefault("success", True)
    listing.setdefault("items", [])
    listing["settings"] = settings
    return listing


async def _import_get(task_id: str, include_chunks: bool) -> dict:
    return await memory_service.import_admin(action="get", task_id=task_id, include_chunks=include_chunks)


async def _import_chunks(task_id: str, file_id: str, offset: int, limit: int) -> dict:
    return await memory_service.import_admin(
        action="get_chunks",
        task_id=task_id,
        file_id=file_id,
        offset=offset,
        limit=limit,
    )


async def _import_cancel(task_id: str) -> dict:
    return await memory_service.import_admin(action="cancel", task_id=task_id)


async def _import_retry(task_id: str, payload: dict[str, Any]) -> dict:
    raw = _unwrap_payload(payload)
    overrides = raw.get("overrides") if isinstance(raw.get("overrides"), dict) else raw
    return await memory_service.import_admin(action="retry_failed", task_id=task_id, overrides=overrides)


async def _tuning_settings() -> dict:
    return await memory_service.tuning_admin(action="get_settings")


async def _tuning_profile() -> dict:
    profile = await memory_service.tuning_admin(action="get_profile")
    if not isinstance(profile, dict):
        profile = {"success": False, "profile": {}}
    if not isinstance(profile.get("settings"), dict):
        settings = await memory_service.tuning_admin(action="get_settings")
        profile["settings"] = settings.get("settings") if isinstance(settings.get("settings"), dict) else {}
    return profile


async def _tuning_apply_profile(payload: TuningApplyProfileRequest) -> dict:
    return await memory_service.tuning_admin(
        action="apply_profile",
        profile=payload.profile,
        reason=payload.reason,
        validate=payload.validate_result,
    )


async def _tuning_rollback_profile() -> dict:
    return await memory_service.tuning_admin(action="rollback_profile")


async def _tuning_export_profile() -> dict:
    return await memory_service.tuning_admin(action="export_profile")


async def _tuning_create_task(payload: dict[str, Any]) -> dict:
    return await memory_service.tuning_admin(action="create_task", payload=_unwrap_payload(payload))


async def _tuning_list_tasks(limit: int) -> dict:
    return await memory_service.tuning_admin(action="list_tasks", limit=limit)


async def _tuning_get_task(task_id: str, include_rounds: bool) -> dict:
    return await memory_service.tuning_admin(action="get_task", task_id=task_id, include_rounds=include_rounds)


async def _tuning_get_rounds(task_id: str, offset: int, limit: int) -> dict:
    return await memory_service.tuning_admin(action="get_rounds", task_id=task_id, offset=offset, limit=limit)


async def _tuning_cancel(task_id: str) -> dict:
    return await memory_service.tuning_admin(action="cancel", task_id=task_id)


async def _tuning_apply_best(task_id: str, payload: TuningApplyBestRequest | None = None) -> dict:
    body = payload or TuningApplyBestRequest()
    result = await memory_service.tuning_admin(
        action="apply_best",
        task_id=task_id,
        validate=body.validate_result,
    )
    if not isinstance(result, dict):
        return {"success": False, "error": "invalid_payload", "persisted": False}
    result.setdefault("persisted", False)
    if not bool(result.get("success", False)) or not body.persist:
        return result

    runtime_payload = await memory_service.runtime_admin(action="get_config")
    runtime_config = runtime_payload.get("config") if isinstance(runtime_payload, dict) else None
    if not isinstance(runtime_config, dict):
        result["persisted"] = False
        result["persist_error"] = "runtime_config_unavailable"
        return result

    try:
        persist_payload = await a_memorix_host_service.update_config(runtime_config)
    except Exception as exc:
        result["persisted"] = False
        result["persist_error"] = f"persist_failed: {exc}"
        return result
    result["persisted"] = bool(isinstance(persist_payload, dict) and persist_payload.get("success", False))
    result["persist_result"] = persist_payload
    return result


async def _tuning_report(task_id: str, fmt: str) -> dict:
    payload_raw = await memory_service.tuning_admin(action="get_report", task_id=task_id, format=fmt)
    payload = payload_raw if isinstance(payload_raw, dict) else {}
    report_raw = payload.get("report")
    report = report_raw if isinstance(report_raw, dict) else {}
    return {
        "success": bool(payload.get("success", False)),
        "format": report.get("format", fmt),
        "content": report.get("content", ""),
        "path": report.get("path", ""),
        "error": payload.get("error", ""),
    }


async def _stage_upload_files(files: list[UploadFile]) -> tuple[Path, list[dict[str, Any]]]:
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    staging_dir = STAGING_ROOT / uuid.uuid4().hex
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_files: list[dict[str, Any]] = []
    for index, upload in enumerate(files):
        filename = Path(upload.filename or f"upload_{index}.txt").name
        target = staging_dir / f"{index:03d}_{filename}"
        content = await upload.read()
        target.write_bytes(content)
        staged_files.append(
            {
                "filename": filename,
                "staged_path": str(target.resolve()),
                "size": len(content),
            }
        )
    return staging_dir, staged_files


@router.get("/graph")
async def get_memory_graph(limit: int = Query(200, ge=1, le=5000)):
    return await _graph_get(limit)


@router.get("/graph/search")
async def search_memory_graph(
    query: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
):
    return await _graph_search(query, limit)


@router.get("/graph/node-detail")
async def get_memory_graph_node_detail(
    node_id: str = Query(..., min_length=1),
    relation_limit: int = Query(20, ge=1, le=100),
    paragraph_limit: int = Query(20, ge=1, le=100),
    evidence_node_limit: int = Query(80, ge=12, le=200),
):
    return await _graph_get_node_detail(
        node_id,
        relation_limit=relation_limit,
        paragraph_limit=paragraph_limit,
        evidence_node_limit=evidence_node_limit,
    )


@router.get("/graph/edge-detail")
async def get_memory_graph_edge_detail(
    source: str = Query(..., min_length=1),
    target: str = Query(..., min_length=1),
    paragraph_limit: int = Query(20, ge=1, le=100),
    evidence_node_limit: int = Query(80, ge=12, le=200),
):
    return await _graph_get_edge_detail(
        source,
        target,
        paragraph_limit=paragraph_limit,
        evidence_node_limit=evidence_node_limit,
    )


@router.get("/graph/paragraph-detail")
async def get_memory_graph_paragraph_detail(
    paragraph_hash: str = Query(..., min_length=1),
    evidence_node_limit: int = Query(80, ge=12, le=200),
):
    return await _graph_get_paragraph_detail(paragraph_hash, evidence_node_limit)


@router.post("/graph/node")
async def create_memory_node(payload: NodeRequest):
    return await _graph_create_node(payload)


@router.delete("/graph/node")
async def delete_memory_node(payload: NodeRequest):
    return await _graph_delete_node(payload)


@router.post("/graph/node/rename")
async def rename_memory_node(payload: NodeRenameRequest):
    return await _graph_rename_node(payload)


@router.post("/graph/edge")
async def create_memory_edge(payload: EdgeCreateRequest):
    return await _graph_create_edge(payload)


@router.delete("/graph/edge")
async def delete_memory_edge(payload: EdgeDeleteRequest):
    return await _graph_delete_edge(payload)


@router.post("/graph/edge/weight")
async def update_memory_edge_weight(payload: EdgeWeightRequest):
    return await _graph_update_edge_weight(payload)


@router.get("/sources")
async def list_memory_sources():
    return await _source_list()


@router.post("/sources/delete")
async def delete_memory_source(payload: SourceDeleteRequest):
    return await _source_delete(payload)


@router.post("/sources/batch-delete")
async def batch_delete_memory_sources(payload: SourceBatchDeleteRequest):
    return await _source_batch_delete(payload)


@router.get("/query/aggregate")
async def query_memory_aggregate(
    query: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    chat_id: str = Query(""),
    person_id: str = Query(""),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
):
    return await _query_aggregate(
        query,
        limit=limit,
        chat_id=chat_id,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
    )


@router.get("/timeline", response_model=MemoryTimelineResponse)
async def get_memory_timeline(
    chat_id: str = Query(..., min_length=1),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
    types: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
):
    return await _memory_timeline(
        chat_id=chat_id,
        time_start=time_start,
        time_end=time_end,
        types=types,
        limit=limit,
    )


@router.get("/episodes")
async def list_memory_episodes(
    query: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    source: str = Query(""),
    person_id: str = Query(""),
    platform: str = Query(""),
    user_id: str = Query(""),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
):
    return await _episode_list(
        query=query,
        limit=limit,
        source=source,
        person_id=person_id,
        platform=platform,
        user_id=user_id,
        time_start=time_start,
        time_end=time_end,
    )


@router.get("/episodes/status")
async def get_memory_episode_status(limit: int = Query(20, ge=1, le=200)):
    return await _episode_status(limit)


@router.get("/episodes/{episode_id}")
async def get_memory_episode(episode_id: str):
    return await _episode_get(episode_id)


@router.post("/episodes/rebuild")
async def rebuild_memory_episodes(payload: EpisodeRebuildRequest):
    return await _episode_rebuild(payload)


@router.post("/episodes/process-pending")
async def process_memory_episode_pending(payload: EpisodeProcessPendingRequest):
    return await _episode_process_pending(payload)


@router.get("/profiles/query")
async def query_memory_profile(
    person_id: str = Query(""),
    person_keyword: str = Query(""),
    platform: str = Query(""),
    user_id: str = Query(""),
    limit: int = Query(12, ge=1, le=100),
    force_refresh: bool = Query(False),
):
    return await _profile_query(
        person_id=person_id,
        person_keyword=person_keyword,
        platform=platform,
        user_id=user_id,
        limit=limit,
        force_refresh=force_refresh,
    )


@router.get("/profiles")
async def list_memory_profiles(limit: int = Query(50, ge=1, le=200)):
    return await _profile_list(limit)


@router.get("/profiles/search")
async def search_memory_profiles(
    person_id: str = Query(""),
    person_keyword: str = Query(""),
    platform: str = Query(""),
    user_id: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    return await _profile_search(
        person_id=person_id,
        person_keyword=person_keyword,
        platform=platform,
        user_id=user_id,
        limit=limit,
    )


@router.post("/profiles/override")
async def set_memory_profile_override(payload: ProfileOverrideRequest):
    return await _profile_set_override(payload)


@router.delete("/profiles/override/{person_id}")
async def delete_memory_profile_override(person_id: str):
    return await _profile_delete_override(person_id)


@router.get("/profiles/{person_id}/evidence")
async def get_memory_profile_evidence(
    person_id: str,
    limit: int = Query(12, ge=1, le=100),
    force_refresh: bool = Query(False),
):
    return await _profile_evidence(person_id, limit, force_refresh)


@router.post("/profiles/{person_id}/evidence/correct")
async def correct_memory_profile_evidence(person_id: str, payload: ProfileEvidenceCorrectRequest):
    return await _profile_correct_evidence(person_id, payload)


@router.get("/feedback-corrections")
async def list_memory_feedback_corrections(
    limit: int = Query(50, ge=1, le=200),
    status: str = Query(""),
    rollback_status: str = Query(""),
    query: str = Query(""),
):
    return await _feedback_list(limit, status, rollback_status, query)


@router.get("/feedback-corrections/{task_id}")
async def get_memory_feedback_correction(task_id: int):
    return await _feedback_get(task_id)


@router.post("/feedback-corrections/{task_id}/rollback")
async def rollback_memory_feedback_correction(task_id: int, payload: FeedbackRollbackRequest):
    return await _feedback_rollback(task_id, payload)


@router.post("/runtime/save")
async def save_memory_runtime():
    return await _runtime_save()


@router.get("/config/schema")
async def get_memory_config_schema():
    return await _memory_config_schema()


@router.get("/config")
async def get_memory_config():
    return await _memory_config_get()


@router.put("/config")
async def update_memory_config(payload: MemoryConfigUpdateRequest):
    return await _memory_config_update(payload)


@router.get("/config/raw")
async def get_memory_config_raw():
    return await _memory_config_get_raw()


@router.put("/config/raw")
async def update_memory_config_raw(payload: MemoryRawConfigUpdateRequest):
    return await _memory_config_update_raw(payload)


@router.get("/runtime/config")
async def get_memory_runtime_config():
    return await _runtime_config()


@router.get("/runtime/self-check")
async def get_memory_runtime_self_check():
    return await _runtime_self_check(False)


@router.post("/runtime/self-check/refresh")
async def refresh_memory_runtime_self_check():
    return await _runtime_self_check(True)


@router.get("/runtime/auto-save")
async def get_memory_runtime_auto_save():
    return await _runtime_auto_save(None)


@router.post("/runtime/auto-save")
async def set_memory_runtime_auto_save(payload: AutoSaveRequest):
    return await _runtime_auto_save(payload.enabled)


@router.post("/runtime/vectors/rebuild")
async def rebuild_memory_runtime_vectors(payload: VectorRebuildRequest):
    return await _runtime_rebuild_vectors(payload)


@router.get("/maintenance/recycle-bin")
async def get_memory_recycle_bin(limit: int = Query(50, ge=1, le=200)):
    return await _maintenance_recycle_bin(limit)


@router.post("/maintenance/restore")
async def restore_memory_relation(payload: MaintainRequest):
    return await _maintenance_restore(payload)


@router.post("/maintenance/reinforce")
async def reinforce_memory_relation(payload: MaintainRequest):
    return await _maintenance_reinforce(payload)


@router.post("/maintenance/freeze")
async def freeze_memory_relation(payload: MaintainRequest):
    return await _maintenance_freeze(payload)


@router.post("/maintenance/protect")
async def protect_memory_relation(payload: MaintainRequest):
    return await _maintenance_protect(payload)


@router.get("/v5/status")
async def get_memory_v5_status(
    target: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    return await _v5_status(target, limit)


@router.get("/v5/recycle-bin")
async def get_memory_v5_recycle_bin(limit: int = Query(50, ge=1, le=200)):
    return await _v5_recycle_bin(limit)


@router.post("/v5/reinforce")
async def reinforce_memory_v5(payload: V5ActionRequest):
    return await _v5_action("reinforce", payload)


@router.post("/v5/weaken")
async def weaken_memory_v5(payload: V5ActionRequest):
    return await _v5_action("weaken", payload)


@router.post("/v5/remember-forever")
async def remember_forever_memory_v5(payload: V5ActionRequest):
    return await _v5_action("remember_forever", payload)


@router.post("/v5/forget")
async def forget_memory_v5(payload: V5ActionRequest):
    return await _v5_action("forget", payload)


@router.post("/v5/restore")
async def restore_memory_v5(payload: V5ActionRequest):
    return await _v5_action("restore", payload)


@router.post("/delete/preview")
async def preview_memory_delete(payload: DeleteActionRequest):
    return await _delete_preview(payload)


@router.post("/delete/execute")
async def execute_memory_delete(payload: DeleteActionRequest):
    return await _delete_execute(payload)


@router.post("/delete/restore")
async def restore_memory_delete(payload: DeleteRestoreRequest):
    return await _delete_restore(payload)


@router.get("/delete/operations")
async def list_memory_delete_operations(
    limit: int = Query(50, ge=1, le=200),
    mode: str = Query(""),
):
    return await _delete_list(limit, mode)


@router.get("/delete/operations/{operation_id}")
async def get_memory_delete_operation(operation_id: str):
    return await _delete_get(operation_id)


@router.post("/delete/purge")
async def purge_memory_delete(payload: DeletePurgeRequest):
    return await _delete_purge(payload)


@router.post("/fuzzy-modify/preview")
async def preview_memory_fuzzy_modify(payload: FuzzyModifyPreviewRequest):
    return await _fuzzy_modify_preview(payload)


@router.post("/corrections/preview")
async def preview_memory_correction(payload: MemoryCorrectionPreviewRequest):
    return await _memory_correction_preview(payload)


@router.post("/fuzzy-modify/execute")
async def execute_memory_fuzzy_modify(payload: FuzzyModifyExecuteRequest):
    return await _fuzzy_modify_execute(payload)


@router.post("/corrections/execute")
async def execute_memory_correction(payload: MemoryCorrectionExecuteRequest):
    return await _memory_correction_execute(payload)


@router.get("/fuzzy-modify/plans")
async def list_memory_fuzzy_modify_plans(
    limit: int = Query(50, ge=1, le=200),
    status: str = Query(""),
    scope: str = Query(""),
):
    return await memory_service.memory_correction_admin(
        action="list",
        limit=limit,
        status=status,
        scope=scope,
    )


@router.get("/corrections/plans")
async def list_memory_correction_plans(
    limit: int = Query(50, ge=1, le=200),
    status: str = Query(""),
    scope: str = Query(""),
):
    return await memory_service.memory_correction_admin(
        action="list",
        limit=limit,
        status=status,
        scope=scope,
    )


@router.get("/fuzzy-modify/plans/{plan_id}")
async def get_memory_fuzzy_modify_plan(plan_id: str):
    return await memory_service.memory_correction_admin(action="get", plan_id=plan_id)


@router.get("/corrections/plans/{plan_id}")
async def get_memory_correction_plan(plan_id: str):
    return await memory_service.memory_correction_admin(action="get", plan_id=plan_id)


@router.post("/fuzzy-modify/plans/{plan_id}/rollback")
async def rollback_memory_fuzzy_modify_plan(plan_id: str, payload: FuzzyModifyRollbackRequest):
    return await _fuzzy_modify_rollback(plan_id, payload)


@router.post("/corrections/plans/{plan_id}/rollback")
async def rollback_memory_correction_plan(plan_id: str, payload: MemoryCorrectionRollbackRequest):
    return await _memory_correction_rollback(plan_id, payload)


@router.get("/import/settings")
async def get_memory_import_settings():
    return await _import_settings()


@router.get("/import/path-aliases")
async def get_memory_import_path_aliases():
    return await _import_path_aliases()


@router.get("/import/chat-targets", response_model=ImportChatTargetsResponse)
async def get_memory_import_chat_targets():
    return await _import_chat_targets()


@router.get("/import/guide")
async def get_memory_import_guide():
    return await _import_guide()


@router.post("/import/resolve-path")
async def resolve_memory_import_path(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_resolve_path(payload)


@router.post("/import/upload")
async def create_memory_import_upload(
    files: list[UploadFile] = File(...),
    payload_json: str = Form("{}"),
):
    staging_dir, staged_files = await _stage_upload_files(files)
    try:
        try:
            payload = json.loads(payload_json or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["staged_files"] = staged_files
        return await _import_create("create_upload", payload)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


@router.post("/import/paste")
async def create_memory_import_paste(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_paste", payload)


@router.post("/import/raw-scan")
async def create_memory_import_raw_scan(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_raw_scan", payload)


@router.post("/import/lpmm-openie")
async def create_memory_import_lpmm_openie(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_openie", payload)


@router.post("/import/lpmm-convert")
async def create_memory_import_lpmm_convert(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_convert", payload)


@router.post("/import/temporal-backfill")
async def create_memory_import_temporal_backfill(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_temporal_backfill", payload)


@router.post("/import/maibot-migration")
async def create_memory_import_maibot_migration(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_maibot_migration", payload)


@router.get("/import/tasks")
async def list_memory_import_tasks(limit: int = Query(50, ge=1, le=200)):
    return await _import_list(limit)


@router.get("/import/tasks/{task_id}")
async def get_memory_import_task(task_id: str, include_chunks: bool = Query(False)):
    return await _import_get(task_id, include_chunks)


@router.get("/import/tasks/{task_id}/chunks/{file_id}")
async def get_memory_import_chunks(
    task_id: str,
    file_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _import_chunks(task_id, file_id, offset, limit)


@router.post("/import/tasks/{task_id}/cancel")
async def cancel_memory_import_task(task_id: str):
    return await _import_cancel(task_id)


@router.post("/import/tasks/{task_id}/retry")
async def retry_memory_import_task(task_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_retry(task_id, payload)


@router.get("/retrieval_tuning/settings")
async def get_memory_tuning_settings():
    return await _tuning_settings()


@router.get("/retrieval_tuning/profile")
async def get_memory_tuning_profile():
    return await _tuning_profile()


@router.post("/retrieval_tuning/profile/apply")
async def apply_memory_tuning_profile(payload: TuningApplyProfileRequest):
    return await _tuning_apply_profile(payload)


@router.post("/retrieval_tuning/profile/rollback")
async def rollback_memory_tuning_profile():
    return await _tuning_rollback_profile()


@router.get("/retrieval_tuning/profile/export")
async def export_memory_tuning_profile():
    return await _tuning_export_profile()


@router.post("/retrieval_tuning/tasks")
async def create_memory_tuning_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _tuning_create_task(payload)


@router.get("/retrieval_tuning/tasks")
async def list_memory_tuning_tasks(limit: int = Query(50, ge=1, le=200)):
    return await _tuning_list_tasks(limit)


@router.get("/retrieval_tuning/tasks/{task_id}")
async def get_memory_tuning_task(task_id: str, include_rounds: bool = Query(False)):
    return await _tuning_get_task(task_id, include_rounds)


@router.get("/retrieval_tuning/tasks/{task_id}/rounds")
async def get_memory_tuning_rounds(
    task_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _tuning_get_rounds(task_id, offset, limit)


@router.post("/retrieval_tuning/tasks/{task_id}/cancel")
async def cancel_memory_tuning_task(task_id: str):
    return await _tuning_cancel(task_id)


@router.post("/retrieval_tuning/tasks/{task_id}/apply-best")
async def apply_best_memory_tuning_profile(
    task_id: str,
    payload: TuningApplyBestRequest = Body(default_factory=TuningApplyBestRequest),
):
    return await _tuning_apply_best(task_id, payload)


@router.get("/retrieval_tuning/tasks/{task_id}/report")
async def get_memory_tuning_report(task_id: str, format: str = Query("md")):
    return await _tuning_report(task_id, format)


@compat_router.get("/graph")
async def compat_get_graph(limit: int = Query(200, ge=1, le=5000)):
    return await _graph_get(limit)


@compat_router.post("/node")
async def compat_create_node(payload: NodeRequest):
    return await _graph_create_node(payload)


@compat_router.delete("/node")
async def compat_delete_node(payload: NodeRequest):
    return await _graph_delete_node(payload)


@compat_router.post("/node/rename")
async def compat_rename_node(payload: NodeRenameRequest):
    return await _graph_rename_node(payload)


@compat_router.post("/edge")
async def compat_create_edge(payload: EdgeCreateRequest):
    return await _graph_create_edge(payload)


@compat_router.delete("/edge")
async def compat_delete_edge(payload: EdgeDeleteRequest):
    return await _graph_delete_edge(payload)


@compat_router.post("/edge/weight")
async def compat_update_edge_weight(payload: EdgeWeightRequest):
    return await _graph_update_edge_weight(payload)


@compat_router.get("/source/list")
async def compat_list_sources():
    return await _source_list()


@compat_router.post("/source/delete")
async def compat_delete_source(payload: SourceDeleteRequest):
    return await _source_delete(payload)


@compat_router.post("/source/batch_delete")
async def compat_batch_delete_sources(payload: SourceBatchDeleteRequest):
    return await _source_batch_delete(payload)


@compat_router.get("/query/aggregate")
async def compat_query_aggregate(
    query: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    chat_id: str = Query(""),
    person_id: str = Query(""),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
):
    return await _query_aggregate(
        query,
        limit=limit,
        chat_id=chat_id,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
    )


@compat_router.get("/timeline", response_model=MemoryTimelineResponse)
async def compat_get_memory_timeline(
    chat_id: str = Query(..., min_length=1),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
    types: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
):
    return await _memory_timeline(
        chat_id=chat_id,
        time_start=time_start,
        time_end=time_end,
        types=types,
        limit=limit,
    )


@compat_router.get("/episodes")
async def compat_list_episodes(
    query: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    source: str = Query(""),
    person_id: str = Query(""),
    platform: str = Query(""),
    user_id: str = Query(""),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
):
    return await _episode_list(
        query=query,
        limit=limit,
        source=source,
        person_id=person_id,
        platform=platform,
        user_id=user_id,
        time_start=time_start,
        time_end=time_end,
    )


@compat_router.get("/episodes/status")
async def compat_episode_status(limit: int = Query(20, ge=1, le=200)):
    return await _episode_status(limit)


@compat_router.get("/episodes/{episode_id}")
async def compat_get_episode(episode_id: str):
    return await _episode_get(episode_id)


@compat_router.post("/episodes/rebuild")
async def compat_rebuild_episodes(payload: EpisodeRebuildRequest):
    return await _episode_rebuild(payload)


@compat_router.post("/episodes/process_pending")
async def compat_process_episode_pending(payload: EpisodeProcessPendingRequest):
    return await _episode_process_pending(payload)


@compat_router.get("/person_profile/query")
async def compat_profile_query(
    person_id: str = Query(""),
    person_keyword: str = Query(""),
    platform: str = Query(""),
    user_id: str = Query(""),
    limit: int = Query(12, ge=1, le=100),
    force_refresh: bool = Query(False),
):
    return await _profile_query(
        person_id=person_id,
        person_keyword=person_keyword,
        platform=platform,
        user_id=user_id,
        limit=limit,
        force_refresh=force_refresh,
    )


@compat_router.get("/person_profile/list")
async def compat_profile_list(limit: int = Query(50, ge=1, le=200)):
    return await _profile_list(limit)


@compat_router.get("/person_profile/search")
async def compat_profile_search(
    person_id: str = Query(""),
    person_keyword: str = Query(""),
    platform: str = Query(""),
    user_id: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    return await _profile_search(
        person_id=person_id,
        person_keyword=person_keyword,
        platform=platform,
        user_id=user_id,
        limit=limit,
    )


@compat_router.post("/person_profile/override")
async def compat_set_profile_override(payload: ProfileOverrideRequest):
    return await _profile_set_override(payload)


@compat_router.delete("/person_profile/override/{person_id}")
async def compat_delete_profile_override(person_id: str):
    return await _profile_delete_override(person_id)


@compat_router.post("/save")
async def compat_runtime_save():
    return await _runtime_save()


@compat_router.get("/config")
async def compat_runtime_config():
    return await _runtime_config()


@compat_router.get("/runtime/self_check")
async def compat_runtime_self_check():
    return await _runtime_self_check(False)


@compat_router.post("/runtime/self_check/refresh")
async def compat_refresh_runtime_self_check():
    return await _runtime_self_check(True)


@compat_router.get("/config/auto_save")
async def compat_runtime_auto_save():
    return await _runtime_auto_save(None)


@compat_router.post("/config/auto_save")
async def compat_set_runtime_auto_save(payload: AutoSaveRequest):
    return await _runtime_auto_save(payload.enabled)


@compat_router.post("/runtime/vectors/rebuild")
async def compat_rebuild_runtime_vectors(payload: VectorRebuildRequest):
    return await _runtime_rebuild_vectors(payload)


@compat_router.get("/memory/recycle_bin")
async def compat_get_recycle_bin(limit: int = Query(50, ge=1, le=200)):
    return await _maintenance_recycle_bin(limit)


@compat_router.post("/memory/restore")
async def compat_restore_memory(payload: MaintainRequest):
    return await _maintenance_restore(payload)


@compat_router.post("/memory/reinforce")
async def compat_reinforce_memory(payload: MaintainRequest):
    return await _maintenance_reinforce(payload)


@compat_router.post("/memory/freeze")
async def compat_freeze_memory(payload: MaintainRequest):
    return await _maintenance_freeze(payload)


@compat_router.post("/memory/protect")
async def compat_protect_memory(payload: MaintainRequest):
    return await _maintenance_protect(payload)


@compat_router.get("/import/settings")
async def compat_import_settings():
    return await _import_settings()


@compat_router.get("/import/path_aliases")
async def compat_import_path_aliases():
    return await _import_path_aliases()


@compat_router.get("/import/guide")
async def compat_import_guide():
    return await _import_guide()


@compat_router.post("/import/resolve_path")
async def compat_import_resolve_path(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_resolve_path(payload)


@compat_router.post("/import/upload")
async def compat_import_upload(
    files: list[UploadFile] = File(...),
    payload_json: str = Form("{}"),
):
    return await create_memory_import_upload(files=files, payload_json=payload_json)


@compat_router.post("/import/tasks/upload")
async def compat_import_upload_task(
    files: list[UploadFile] = File(...),
    payload_json: str = Form("{}"),
):
    return await create_memory_import_upload(files=files, payload_json=payload_json)


@compat_router.post("/import/paste")
async def compat_import_paste(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_paste", payload)


@compat_router.post("/import/tasks/paste")
async def compat_import_paste_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_paste", payload)


@compat_router.post("/import/raw_scan")
async def compat_import_raw_scan(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_raw_scan", payload)


@compat_router.post("/import/tasks/raw_scan")
async def compat_import_raw_scan_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_raw_scan", payload)


@compat_router.post("/import/lpmm_openie")
async def compat_import_lpmm_openie(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_openie", payload)


@compat_router.post("/import/tasks/lpmm_openie")
async def compat_import_lpmm_openie_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_openie", payload)


@compat_router.post("/import/lpmm_convert")
async def compat_import_lpmm_convert(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_convert", payload)


@compat_router.post("/import/tasks/lpmm_convert")
async def compat_import_lpmm_convert_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_convert", payload)


@compat_router.post("/import/temporal_backfill")
async def compat_import_temporal_backfill(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_temporal_backfill", payload)


@compat_router.post("/import/tasks/temporal_backfill")
async def compat_import_temporal_backfill_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_temporal_backfill", payload)


@compat_router.post("/import/maibot_migration")
async def compat_import_maibot_migration(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_maibot_migration", payload)


@compat_router.post("/import/tasks/maibot_migration")
async def compat_import_maibot_migration_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_maibot_migration", payload)


@compat_router.get("/import/tasks")
async def compat_import_list(limit: int = Query(50, ge=1, le=200)):
    return await _import_list(limit)


@compat_router.get("/import/tasks/{task_id}")
async def compat_import_get(task_id: str, include_chunks: bool = Query(False)):
    return await _import_get(task_id, include_chunks)


@compat_router.get("/import/tasks/{task_id}/chunks/{file_id}")
async def compat_import_chunks(
    task_id: str,
    file_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _import_chunks(task_id, file_id, offset, limit)


@compat_router.get("/import/tasks/{task_id}/files/{file_id}/chunks")
async def compat_import_file_chunks(
    task_id: str,
    file_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _import_chunks(task_id, file_id, offset, limit)


@compat_router.post("/import/tasks/{task_id}/cancel")
async def compat_import_cancel(task_id: str):
    return await _import_cancel(task_id)


@compat_router.post("/import/tasks/{task_id}/retry")
async def compat_import_retry(task_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_retry(task_id, payload)


@compat_router.post("/import/tasks/{task_id}/retry_failed")
async def compat_import_retry_failed(task_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_retry(task_id, payload)


@compat_router.get("/retrieval_tuning/settings")
async def compat_tuning_settings():
    return await _tuning_settings()


@compat_router.get("/retrieval_tuning/profile")
async def compat_tuning_profile():
    return await _tuning_profile()


@compat_router.post("/retrieval_tuning/profile/apply")
async def compat_apply_tuning_profile(payload: TuningApplyProfileRequest):
    return await _tuning_apply_profile(payload)


@compat_router.post("/retrieval_tuning/profile/rollback")
async def compat_rollback_tuning_profile():
    return await _tuning_rollback_profile()


@compat_router.get("/retrieval_tuning/profile/export")
async def compat_export_tuning_profile():
    return await _tuning_export_profile()


@compat_router.get("/retrieval_tuning/profile/export_toml")
async def compat_export_tuning_profile_toml():
    return await _tuning_export_profile()


@compat_router.post("/retrieval_tuning/tasks")
async def compat_create_tuning_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _tuning_create_task(payload)


@compat_router.get("/retrieval_tuning/tasks")
async def compat_list_tuning_tasks(limit: int = Query(50, ge=1, le=200)):
    return await _tuning_list_tasks(limit)


@compat_router.get("/retrieval_tuning/tasks/{task_id}")
async def compat_get_tuning_task(task_id: str, include_rounds: bool = Query(False)):
    return await _tuning_get_task(task_id, include_rounds)


@compat_router.get("/retrieval_tuning/tasks/{task_id}/rounds")
async def compat_get_tuning_rounds(
    task_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _tuning_get_rounds(task_id, offset, limit)


@compat_router.post("/retrieval_tuning/tasks/{task_id}/cancel")
async def compat_cancel_tuning_task(task_id: str):
    return await _tuning_cancel(task_id)


@compat_router.post("/retrieval_tuning/tasks/{task_id}/apply_best")
async def compat_apply_best_tuning_profile(
    task_id: str,
    payload: TuningApplyBestRequest = Body(default_factory=TuningApplyBestRequest),
):
    return await _tuning_apply_best(task_id, payload)


@compat_router.post("/retrieval_tuning/tasks/{task_id}/apply-best")
async def compat_apply_best_tuning_profile_kebab(
    task_id: str,
    payload: TuningApplyBestRequest = Body(default_factory=TuningApplyBestRequest),
):
    return await _tuning_apply_best(task_id, payload)


@compat_router.get("/retrieval_tuning/tasks/{task_id}/report")
async def compat_get_tuning_report(task_id: str, format: str = Query("md")):
    return await _tuning_report(task_id, format)
