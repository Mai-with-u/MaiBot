"""黑话（俚语）管理路由"""

from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Mapping, Optional, Set, Tuple, Type
import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String as SQLString, and_, case, cast, false, func, not_, or_, tuple_, update
from sqlalchemy.orm import aliased, load_only, with_expression
from sqlmodel import Session, col, select

from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.common.data_models.jargon_data_model import normalize_evidence_message_groups
from src.common.database.database import get_db_session
from src.common.database.database_model import ChatSession, Jargon, JargonCreatedBy, JargonEvidenceLink, JargonLearningRecord, Messages
from src.common.database.jargon_snapshots import inherited_source, materialize, read_jargons
from src.common.database.jargon_scope_transfers import stored_jargon_members, term_key
from src.common.database.sharing_models import SharingGroup, SharingGroupCapability
from src.common.logger import get_logger
from src.common.utils.utils_message import MessageUtils
from src.learners.learning_text import extract_learning_text
from src.services.sharing_group_service import jargon_group_ids, list_groups, resolved_members
from src.services.jargon_scope_service import attach_common_learning, copy_version, effective_scope_ids, inherit_evidence, scope_row

logger = get_logger("webui.jargon")

router = APIRouter(prefix="/jargon", tags=["Jargon"])
JARGON_SESSION_IDS_QUERY = Query(None, description="按共享组内任一聊天流ID筛选")


# ==================== 辅助函数 ====================


def parse_session_id_candidates(session_id_str: str) -> List[str]:
    """解析会话 ID 字段并提取候选 session_id。

    Args:
        session_id_str: JSON 格式或纯字符串格式的会话 ID。

    Returns:
        List[str]: 解析出的 session_id 列表。
    """
    if not session_id_str:
        return []

    try:
        # 尝试解析为 JSON
        parsed = json.loads(session_id_str)
        if isinstance(parsed, list):
            # 兼容旧格式: [["session_id", user_id], ...]
            return [str(item[0]) for item in parsed if isinstance(item, list) and len(item) >= 1]

        # 其他格式，返回原始字符串
        return [session_id_str]
    except (json.JSONDecodeError, TypeError):
        # 不是有效的 JSON，可能是直接的 session_id
        return [session_id_str]


def message_to_display_name(message: Messages) -> Optional[str]:
    """从消息记录中解析聊天显示名称。"""

    if message.group_id:
        return message.group_name or f"群聊{message.group_id}"
    private_name = message.user_cardname or message.user_nickname or (f"用户{message.user_id}" if message.user_id else None)
    return f"{private_name}的私聊" if private_name else None


def chat_session_to_display_name(chat_session: ChatSession) -> str:
    """从 ChatSession 记录中解析聊天显示名称。"""

    if chat_session.group_id:
        return chat_session.group_name or f"群聊{chat_session.group_id}"
    if chat_session.user_id:
        private_name = chat_session.user_cardname or chat_session.user_nickname or f"用户{chat_session.user_id}"
        return f"{private_name}的私聊"
    return chat_session.session_id[:20]


def get_latest_message_display_name(session_id: str, session: Session) -> Optional[str]:
    """读取指定聊天流的最新消息名称。"""

    message = session.exec(
        select(Messages).where(col(Messages.session_id) == session_id).order_by(col(Messages.timestamp).desc()).limit(1)
    ).first()
    return message_to_display_name(message) if message else None


def build_session_display_name_cache(session_ids: List[str], session: Session, include_message_fallback: bool = False) -> Dict[str, str]:
    """批量构建聊天流显示名称缓存，避免列表接口逐条查询。"""

    unique_session_ids = list(dict.fromkeys(session_id for session_id in session_ids if session_id))
    if not unique_session_ids:
        return {}

    display_name_by_session_id: Dict[str, str] = {}
    chat_sessions = session.exec(select(ChatSession).where(col(ChatSession.session_id).in_(unique_session_ids))).all()
    for chat_session in chat_sessions:
        display_name_by_session_id[chat_session.session_id] = chat_session_to_display_name(chat_session)

    missing_session_ids = [
        session_id for session_id in unique_session_ids if session_id not in display_name_by_session_id
    ]
    if include_message_fallback:
        for session_id in missing_session_ids:
            if display_name := get_latest_message_display_name(session_id, session):
                display_name_by_session_id[session_id] = display_name

    for session_id in unique_session_ids:
        display_name_by_session_id.setdefault(session_id, session_id[:20])

    return display_name_by_session_id


def get_display_name_for_session_id(session_id_str: str, session: Session) -> str:
    """获取聊天流的显示名称。

    Args:
        session_id_str: JSON 格式或纯字符串格式的会话 ID。
        session: 当前数据库会话。

    Returns:
        str: 聊天显示名称，无法查询时返回截断后的 session_id。
    """
    session_ids = parse_session_id_candidates(session_id_str)

    if not session_ids:
        return session_id_str[:20]

    primary_session_id = session_ids[0]
    message = session.exec(
        select(Messages)
        .where(col(Messages.session_id) == primary_session_id)
        .order_by(col(Messages.timestamp).desc())
        .limit(1)
    ).first()
    if message:
        if message.group_id:
            return message.group_name or f"群聊{message.group_id}"
        private_name = message.user_cardname or message.user_nickname or (
            f"用户{message.user_id}" if message.user_id else None
        )
        if private_name:
            return f"{private_name}的私聊"

    if not (
        chat_session := session.exec(select(ChatSession).where(col(ChatSession.session_id) == primary_session_id)).first()
    ):
        return primary_session_id[:20]

    if chat_session.group_id:
        return chat_session.group_name or f"群聊{chat_session.group_id}"
    if chat_session.user_id:
        private_name = chat_session.user_cardname or chat_session.user_nickname or f"用户{chat_session.user_id}"
        return f"{private_name}的私聊"

    return chat_session.session_id[:20]


# ==================== 请求/响应模型 ====================


class JargonResponse(BaseModel):
    """黑话信息响应"""

    id: int
    scope_kind: str = "chat"
    scope_id: str = ""
    revision: int = 0
    archived: bool = False
    content: str
    meaning: Optional[str] = None
    session_id: str
    session_ids: List[str] = Field(default_factory=list)
    chat_name: Optional[str] = None  # 解析后的聊天名称，用于前端显示
    chat_names: List[str] = Field(default_factory=list)
    count: int = 0
    is_jargon: Optional[bool] = None
    is_legacy_empty_meaning: bool = False
    is_complete: bool = False
    is_global: bool = False
    allow_update: bool = True
    created_by: JargonCreatedBy = JargonCreatedBy.AI
    created_timestamp: Any
    updated_timestamp: Any


class JargonListResponse(BaseModel):
    """黑话列表响应"""

    success: bool = True
    total: int
    page: int
    page_size: int
    data: List[Dict[str, Any]]


class JargonDetailResponse(BaseModel):
    """黑话详情响应"""

    success: bool = True
    data: JargonResponse


class JargonCreateRequest(BaseModel):
    """黑话创建请求"""

    group_id: Optional[str] = None

    content: str = Field(..., description="黑话内容")
    meaning: Optional[str] = Field(None, description="含义")
    session_id: Optional[str] = Field(None, description="聊天流ID")
    session_ids: Optional[List[str]] = Field(None, description="聊天流ID列表")
    is_global: bool = Field(False, description="是否为通用词条")


class JargonUpdateRequest(BaseModel):
    """黑话更新请求"""

    all_scope_revisions: Optional[Dict[int, int]] = None

    revision: Optional[int] = None
    content: Optional[str] = None
    meaning: Optional[str] = None
    session_id: Optional[str] = None
    session_ids: Optional[List[str]] = None
    is_global: Optional[bool] = None
    is_jargon: Optional[bool] = None
    allow_update: Optional[bool] = None
    created_by: Optional[JargonCreatedBy] = None


class JargonCreateResponse(BaseModel):
    """黑话创建响应"""

    success: bool = True
    message: str
    data: JargonResponse


class JargonUpdateResponse(BaseModel):
    """黑话更新响应"""

    success: bool = True
    message: str
    data: Optional[JargonResponse] = None


class JargonDeleteResponse(BaseModel):
    """黑话删除响应"""

    success: bool = True
    message: str
    deleted_count: int = 0


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""

    ids: List[int] = Field(..., description="要删除的黑话ID列表")


class JargonStatsResponse(BaseModel):
    """黑话统计响应"""

    success: bool = True
    data: Dict[str, Any]


class ChatInfoResponse(BaseModel):
    """聊天信息响应"""

    session_id: str
    chat_name: str
    platform: Optional[str] = None
    account_id: Optional[str] = None
    target_id: Optional[str] = None
    is_group: bool = False
    jargon_count: int = 0


class ChatListResponse(BaseModel):
    """聊天列表响应"""

    success: bool = True
    data: List[ChatInfoResponse]


class JargonGroupInfo(BaseModel):
    """黑话共享组及已解析的真实聊天流。"""

    id: str
    color: str
    index: int
    name: str
    session_ids: List[str]
    members: List[ChatInfoResponse]
    jargon_count: int = 0
    is_global: bool = False


class JargonGroupListResponse(BaseModel):
    success: bool = True
    data: List[JargonGroupInfo]


class JargonTargetInfo(BaseModel):
    """黑话导出中的聊天目标信息。"""

    platform: str
    id: str = Field(..., description="群聊使用 group_id，私聊使用 user_id")
    type: Literal["group", "private"]
    account_id: Optional[str] = None
    scope: Optional[str] = None
    count: int = 0


class JargonExportItem(BaseModel):
    """黑话导出条目，不包含本机数据库 ID 或 session_id。"""

    scope_kind: Optional[str] = None
    group_name: Optional[str] = None

    content: str
    meaning: str = ""
    count: int = 0
    is_jargon: Optional[bool] = None
    is_complete: bool = False
    is_global: bool = False
    allow_update: Optional[bool] = None
    created_by: JargonCreatedBy = JargonCreatedBy.MANUAL
    targets: Optional[List[JargonTargetInfo]] = None


class JargonExportRequest(BaseModel):
    """黑话导出请求。"""

    group_id: Optional[str] = None

    ids: Optional[List[int]] = None
    session_ids: Optional[List[str]] = None
    include_chat_info: bool = False


class JargonExportResponse(BaseModel):
    """黑话导出响应。"""

    success: bool = True
    version: int = 2
    type: str = "maibot.jargon.export"
    exported_at: str
    include_chat_info: bool = False
    count: int
    jargons: List[JargonExportItem]


class JargonConflictRecord(BaseModel):
    group_name: Optional[str] = None
    id: int
    meaning: str
    is_global: bool
    session_ids: List[str]


class JargonImportConflict(BaseModel):
    content: str
    records: List[JargonConflictRecord]


class JargonImportItem(JargonExportItem):
    excerpt: str = ""


class JargonImportRequest(BaseModel):
    """黑话导入请求。"""

    target_session_ids: List[str] = Field(default_factory=list, description="导入目标聊天流 ID 列表")
    is_global: bool = False
    jargons: List[JargonImportItem]
    conflict_strategy: Literal["skip", "overwrite"] = "skip"
    # 自动学习导入时，核对用户确认替换的记录和原释义。
    replacements: Optional[Dict[str, List[JargonConflictRecord]]] = None
    merges: Dict[str, List[JargonConflictRecord]] = Field(default_factory=dict)
    reviewed_skips: List[str] = Field(default_factory=list)


class JargonImportResponse(BaseModel):
    """黑话导入响应。"""

    success: bool = True
    message: str
    imported_count: int
    skipped_count: int = 0
    failed_count: int = 0


# ==================== 工具函数 ====================


def parse_session_id_dict(session_id_dict_str: Optional[str]) -> Dict[str, int]:
    """解析会话计数字典。

    Args:
        session_id_dict_str: 数据库中保存的会话计数字典 JSON 字符串。

    Returns:
        Dict[str, int]: 解析后的会话计数字典。
    """
    if not session_id_dict_str:
        return {}

    try:
        parsed = json.loads(session_id_dict_str)
    except (json.JSONDecodeError, TypeError):
        return {}

    if not isinstance(parsed, dict):
        return {}

    session_counts: Dict[str, int] = {}
    for session_id, count in parsed.items():
        if not isinstance(session_id, str):
            continue
        if isinstance(count, int):
            session_counts[session_id] = count
        else:
            try:
                session_counts[session_id] = int(count)
            except (TypeError, ValueError):
                session_counts[session_id] = 0
    return session_counts


def dump_session_id_dict(session_counts: Dict[str, int]) -> str:
    """序列化会话计数字典。

    Args:
        session_counts: 会话 ID 与出现次数的映射。

    Returns:
        str: 可写入数据库的 JSON 字符串。
    """
    return json.dumps(session_counts, ensure_ascii=False)


def get_primary_session_id(session_id_dict_str: Optional[str]) -> str:
    """从会话计数字典中选出主聊天 ID。

    Args:
        session_id_dict_str: 数据库中保存的会话计数字典 JSON 字符串。

    Returns:
        str: 出现次数最多的聊天 ID，没有记录时返回空字符串。
    """
    if not (session_counts := parse_session_id_dict(session_id_dict_str)):
        return ""

    return max(session_counts.items(), key=lambda item: item[1])[0]


def get_session_ids(session_id_dict_str: Optional[str]) -> List[str]:
    """从会话计数字典中按保存顺序取出全部聊天流 ID。"""

    return list(parse_session_id_dict(session_id_dict_str).keys())


def jargon_has_meaning_condition(model: Type[Jargon] = Jargon) -> Any:
    """有效黑话必须有非空含义。"""

    return func.length(func.trim(col(model.meaning))) > 0


def effective_jargon_condition(model: Type[Jargon] = Jargon) -> Any:
    """WebUI 侧确认黑话的统一判定条件。"""

    return and_(col(model.is_jargon).is_(True), jargon_has_meaning_condition(model))


def no_jargon_condition(model: Type[Jargon] = Jargon) -> Any:
    """WebUI 侧无黑话的统一判定条件，包含历史空含义黑话记录。"""

    return or_(col(model.is_jargon).is_(False), col(model.is_jargon).is_(None), not_(effective_jargon_condition(model)))


def pending_jargon_condition(model: Type[Jargon] = Jargon) -> Any:
    """待推断包括尚无结论的词条和缺少释义的历史黑话。"""
    return or_(
        col(model.is_jargon).is_(None),
        and_(col(model.is_jargon).is_(True), not_(jargon_has_meaning_condition(model))),
    )


def is_legacy_empty_meaning_jargon(is_jargon: Any, meaning: Any) -> bool:
    """识别历史遗留的“标记为黑话但没有含义”的记录。"""

    return bool(is_jargon) and not str(meaning or "").strip()


def visible_in_chats(chats: List[str], model: Type[Jargon] = Jargon) -> Any:
    matches = [col(model.id).in_(effective_scope_ids(chat, jargon_group_ids(chat))) for chat in dict.fromkeys(chats)]
    return or_(*matches) if matches else col(model.id).in_([])


def apply_jargon_list_filters(
    statement: Any,
    model: Type[Jargon] = Jargon,
    *,
    search: Optional[str],
    session_id: Optional[str],
    jargon_status: Optional[Literal["confirmed_jargon", "confirmed_not_jargon", "manual_jargon", "pending"]],
    is_jargon: Optional[bool],
    is_complete: Optional[bool],
    is_global: Optional[bool],
) -> Any:
    """向黑话列表查询追加筛选条件。"""

    statement = statement.where(col(model.archived).is_(False))
    normalized_search = search.strip() if search else ""
    if normalized_search:
        statement = statement.where(col(model.content).contains(normalized_search))

    if session_id:
        statement = statement.where(visible_in_chats(parse_session_id_candidates(session_id) or [session_id], model))

    if jargon_status == "confirmed_jargon":
        statement = statement.where(effective_jargon_condition(model))
    elif jargon_status == "confirmed_not_jargon":
        statement = statement.where(col(model.is_jargon).is_(False))
    elif jargon_status == "manual_jargon":
        statement = statement.where(col(model.allow_update).is_(False))
    elif jargon_status == "pending":
        statement = statement.where(pending_jargon_condition(model))
    elif is_jargon is not None:
        statement = statement.where(effective_jargon_condition(model) if is_jargon else no_jargon_condition(model))

    if is_complete is not None:
        statement = statement.where(col(model.is_complete) == is_complete)

    if is_global is not None:
        statement = statement.where(col(model.is_global) == is_global)

    return statement


def count_jargon_query(session: Session, statement: Any) -> int:
    """统计查询结果数量。"""

    return int(session.exec(select(func.count()).select_from(statement.subquery())).one() or 0)


def build_session_id_dict_for_session(session_id: str, count: int = 1) -> str:
    """为单个聊天 ID 构建会话计数字典。

    Args:
        session_id: 聊天流 ID。
        count: 该聊天 ID 的出现次数。

    Returns:
        str: 可写入数据库的会话计数字典 JSON 字符串。
    """
    return dump_session_id_dict({session_id: count})


def build_session_id_dict_for_sessions(session_ids: List[str], count: int = 1) -> str:
    """为多个聊天流 ID 构建会话计数字典。"""

    return dump_session_id_dict({session_id: count for session_id in session_ids})


def require_existing_session_id(session_id: Optional[str]) -> str:
    """校验资源归属的聊天流 ID 必须是真实存在的会话。"""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=400, detail="缺少聊天流 ID")
    if _chat_manager.get_existing_session_by_session_id(normalized_session_id) is None:
        raise HTTPException(status_code=400, detail=f"聊天流不存在: {normalized_session_id}")
    return normalized_session_id


def require_existing_session_ids(session_ids: Optional[List[str]]) -> List[str]:
    """校验多个资源归属聊天流 ID，并保留用户选择顺序去重。"""

    normalized_session_ids: List[str] = []
    seen_session_ids: Set[str] = set()
    for raw_session_id in session_ids or []:
        normalized_session_id = require_existing_session_id(raw_session_id)
        if normalized_session_id in seen_session_ids:
            continue
        seen_session_ids.add(normalized_session_id)
        normalized_session_ids.append(normalized_session_id)
    if not normalized_session_ids:
        raise HTTPException(status_code=400, detail="缺少聊天流 ID")
    return normalized_session_ids


def normalize_jargon_created_by(created_by: Any, jargon_id: Optional[int]) -> JargonCreatedBy:
    """兼容历史空值或异常值，并保持 WebUI 响应只输出合法创建来源。"""

    if created_by in (JargonCreatedBy.MANUAL, JargonCreatedBy.MANUAL.value):
        return JargonCreatedBy.MANUAL
    if created_by in (JargonCreatedBy.AI, JargonCreatedBy.AI.value, None, ""):
        return JargonCreatedBy.AI

    logger.warning(f"黑话记录存在未知创建来源，已按 AI 展示: id={jargon_id}, created_by={created_by!r}")
    return JargonCreatedBy.AI


def chat_session_to_export_target(chat_session: ChatSession, count: int = 0) -> Optional[JargonTargetInfo]:
    """将真实聊天流转换为可迁移的 platform/id/type 目标信息。"""

    if chat_session.group_id:
        return JargonTargetInfo(
            platform=chat_session.platform,
            id=chat_session.group_id,
            type="group",
            account_id=chat_session.account_id,
            scope=chat_session.scope,
            count=count,
        )
    if chat_session.user_id:
        return JargonTargetInfo(
            platform=chat_session.platform,
            id=chat_session.user_id,
            type="private",
            account_id=chat_session.account_id,
            scope=chat_session.scope,
            count=count,
        )
    return None


def jargon_to_export_item(
    jargon: Jargon,
    chat_session_by_session_id: Optional[Dict[str, ChatSession]] = None,
    group: Optional[Dict[str, Any]] = None,
) -> JargonExportItem:
    """将数据库黑话记录转换为导出条目。"""

    session_counts = parse_session_id_dict(jargon.session_id_dict)
    targets: Optional[List[JargonTargetInfo]] = None
    if chat_session_by_session_id is not None:
        targets = []
        owners = resolved_members(group) if group else [jargon.scope_id] if jargon.scope_kind == "chat" else []
        for session_id in sorted(owners):
            count = session_counts.get(session_id, 0)
            chat_session = chat_session_by_session_id.get(session_id)
            if not chat_session:
                continue
            target = chat_session_to_export_target(chat_session, count)
            if target is not None:
                targets.append(target)

    return JargonExportItem(
        scope_kind=jargon.scope_kind,
        group_name=group["name"] if group else None,
        content=jargon.content,
        meaning=jargon.meaning or "",
        count=jargon.count,
        is_jargon=jargon.is_jargon,
        is_complete=jargon.is_complete,
        is_global=jargon.is_global,
        allow_update=jargon.allow_update,
        created_by=normalize_jargon_created_by(jargon.created_by, jargon.id),
        targets=targets,
    )


def jargon_to_dict(
    jargon: Jargon,
    session: Session,
    chat_name_cache: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """将黑话模型转换为字典。

    Args:
        jargon: 数据库中的黑话记录。
        session: 当前数据库会话，用于查询聊天显示名称。

    Returns:
        Dict[str, Any]: WebUI 可直接序列化的黑话数据。
    """
    session_ids = get_session_ids(jargon.session_id_dict)
    session_id = session_ids[0] if session_ids else ""

    def resolve_chat_name(current_session_id: str) -> str:
        if chat_name_cache is not None and current_session_id in chat_name_cache:
            return chat_name_cache[current_session_id]
        return get_display_name_for_session_id(current_session_id, session)

    chat_names = [resolve_chat_name(current_session_id) for current_session_id in session_ids]
    chat_name = chat_names[0] if chat_names else None
    is_legacy_empty_meaning = is_legacy_empty_meaning_jargon(jargon.is_jargon, jargon.meaning)

    return {
        "id": jargon.id,
        "scope_kind": jargon.scope_kind, "scope_id": jargon.scope_id, "revision": jargon.revision, "archived": jargon.archived,
        "content": jargon.content,
        "meaning": jargon.meaning,
        "session_id": session_id,
        "session_ids": session_ids,
        "chat_name": chat_name,
        "chat_names": chat_names,
        "count": jargon.count,
        "is_jargon": None if is_legacy_empty_meaning else jargon.is_jargon,
        "is_legacy_empty_meaning": is_legacy_empty_meaning,
        "is_complete": jargon.is_complete,
        "is_global": jargon.is_global,
        "allow_update": jargon.allow_update,
        "created_by": normalize_jargon_created_by(jargon.created_by, jargon.id),
        "created_timestamp": jargon.created_timestamp,
        "updated_timestamp": jargon.updated_timestamp,
    }


def jargon_list_row_to_dict(
    row: Mapping[str, Any],
    session: Session,
    chat_name_cache: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """将列表查询的原始列映射转换为 WebUI 可序列化字典。"""

    session_ids = get_session_ids(str(row["session_id_dict"] or "{}"))
    session_id = session_ids[0] if session_ids else ""

    def resolve_chat_name(current_session_id: str) -> str:
        if chat_name_cache is not None and current_session_id in chat_name_cache:
            return chat_name_cache[current_session_id]
        return get_display_name_for_session_id(current_session_id, session)

    chat_names = [resolve_chat_name(current_session_id) for current_session_id in session_ids]
    chat_name = chat_names[0] if chat_names else None
    is_legacy_empty_meaning = is_legacy_empty_meaning_jargon(row["is_jargon"], row["meaning"])

    return {
        "id": row["id"],
        "scope_kind": row["scope_kind"], "scope_id": row["scope_id"], "revision": row["revision"], "archived": row["archived"],
        "content": row["content"],
        "meaning": row["meaning"],
        "session_id": session_id,
        "session_ids": session_ids,
        "chat_name": chat_name,
        "chat_names": chat_names,
        "count": row["count"],
        "is_jargon": None if is_legacy_empty_meaning else row["is_jargon"],
        "is_legacy_empty_meaning": is_legacy_empty_meaning,
        "is_complete": row["is_complete"],
        "is_global": row["is_global"],
        "allow_update": row["allow_update"],
        "created_by": normalize_jargon_created_by(row["created_by"], row["id"]),
        "created_timestamp": row["created_timestamp"] or "",
        "updated_timestamp": row["updated_timestamp"] or "",
    }


def build_jargon_compatible_select(model: Type[Jargon] = Jargon) -> Any:
    """构建黑话兼容读取列，避免历史异常值触发 ORM 类型转换错误。"""

    return select(
        col(model.id).label("id"), model.scope_kind, model.scope_id, model.revision, model.archived,
        col(model.content).label("content"),
        col(model.meaning).label("meaning"),
        col(model.session_id_dict).label("session_id_dict"),
        col(model.count).label("count"),
        col(model.is_jargon).label("is_jargon"),
        col(model.is_complete).label("is_complete"),
        col(model.is_global).label("is_global"),
        col(model.allow_update).label("allow_update"),
        col(model.created_by).label("created_by"),
        cast(col(model.created_timestamp), SQLString).label("created_timestamp"),
        cast(col(model.updated_timestamp), SQLString).label("updated_timestamp"),
    )


def build_jargon_list_select(model: Type[Jargon] = Jargon) -> Any:
    """构建黑话列表读取列，列表页不读取原始上下文大字段。"""

    return select(
        col(model.id).label("id"), model.scope_kind, model.scope_id, model.revision, model.archived,
        col(model.content).label("content"),
        col(model.meaning).label("meaning"),
        col(model.session_id_dict).label("session_id_dict"),
        col(model.count).label("count"),
        col(model.is_jargon).label("is_jargon"),
        col(model.is_complete).label("is_complete"),
        col(model.is_global).label("is_global"),
        col(model.allow_update).label("allow_update"),
        col(model.created_by).label("created_by"),
        cast(col(model.created_timestamp), SQLString).label("created_timestamp"),
        cast(col(model.updated_timestamp), SQLString).label("updated_timestamp"),
    )


# ==================== API 端点 ====================


class JargonScopeChange(BaseModel):
    action: Literal["move", "merge", "split"]
    kind: Literal["chat", "group", "global"]
    target: str = ""
    versions: Dict[int, int]
    inherit_id: int


def scope_names(scopes: List[Tuple[str, str, int]], session: Session) -> Dict[Tuple[str, str], str]:
    names = build_session_display_name_cache([scope_id for kind, scope_id, _ in scopes if kind == "chat"], session)
    groups = {group["id"]: group["name"] for group in list_groups()}
    return {(kind, scope_id): (
        names.get(scope_id, scope_id) if kind == "chat" else
        groups.get(scope_id, "已删除共享组") if kind == "group" else
        "通用词库" if kind == "global" else f"旧记录 #{version_id}"
    ) for kind, scope_id, version_id in scopes}


@router.get("/library/versions")
def jargon_versions(content: str, include_history: bool = True):
    with get_db_session(auto_commit=False) as session:
        model = read_jargons()
        statement = select(model).where(func.lower(func.trim(model.content)) == term_key(content))
        if not include_history:
            statement = statement.where(col(model.archived).is_(False))
        rows = list(session.exec(statement.order_by(model.archived, model.id)).all())
        names = scope_names([(row.scope_kind, row.scope_id, row.id) for row in rows], session)
        chats = build_session_display_name_cache([sid for row in rows for sid in get_session_ids(row.session_id_dict)], session)
        return [dict(jargon_to_dict(row, session, chats), scope_name=names[row.scope_kind, row.scope_id],
                     baseline_id=row.baseline_id, last_inference_count=row.last_inference_count) for row in rows]


@router.get("/library/evidence/{jargon_id}")
def jargon_evidence(jargon_id: int, offset: int = Query(0, ge=0)):
    with get_db_session(auto_commit=False) as session:
        stmt = select(JargonLearningRecord).join(JargonEvidenceLink, JargonEvidenceLink.record_id == JargonLearningRecord.id)
        inherited = inherited_source(session.connection(), jargon_id)
        rows = session.exec(stmt.where(JargonEvidenceLink.jargon_id == (inherited[0] if inherited else jargon_id))
                            .order_by(col(JargonLearningRecord.id).desc()).offset(offset).limit(20)).all()
        names = build_session_display_name_cache([r.session_id for r in rows if r.session_id], session)
        groups_by_record = {}
        for row in rows:
            if row.excerpt.strip() or not row.evidence:
                continue
            try:
                groups_by_record[row.id] = normalize_evidence_message_groups(json.loads(row.evidence))
            except json.JSONDecodeError:
                logger.warning(f"黑话学习记录 {row.id} 的消息引用无法解析")
        keys = sorted({(ref["platform"], ref["message_id"]) for groups in groups_by_record.values()
                       for group in groups for ref in group})
        messages: Dict[Tuple[str, str], List[Messages]] = {}
        for start in range(0, len(keys), 400):
            query = select(Messages).options(
                load_only(Messages.platform, Messages.message_id, Messages.session_id, Messages.user_cardname,
                          Messages.user_nickname, Messages.timestamp, Messages.processed_plain_text),
                with_expression(Messages.raw_content, case(
                    (func.nullif(Messages.processed_plain_text, "").is_(None), Messages.raw_content)
                )),
            ).where(tuple_(Messages.platform, Messages.message_id).in_(keys[start:start + 400]))
            for message in session.exec(query):
                messages.setdefault((message.platform, message.message_id), []).append(message)

        result = []
        for row in rows:
            passages = []
            unavailable = 0
            for group in groups_by_record.get(row.id, []):
                passage = []
                for ref in group:
                    matches = messages.get((ref["platform"], ref["message_id"]), [])
                    if row.session_id:
                        matches = [m for m in matches if m.session_id == row.session_id]
                    if len(matches) != 1:
                        unavailable += 1
                        continue
                    message = matches[0]
                    text = message.processed_plain_text or extract_learning_text(
                        MessageUtils.from_db_record_msg_to_MaiSeq(message.raw_content)
                    )
                    if text.strip():
                        passage.append({"sender": message.user_cardname or message.user_nickname,
                                        "time": message.timestamp, "text": text})
                    else:
                        unavailable += 1
                if passage:
                    passages.append(passage)
            result.append(dict(row.model_dump(), passages=passages, unavailable=unavailable,
                               source_name="导入文段" if row.scope_kind == "import"
                               else names.get(row.session_id, "旧记录（来源未确定）")))
        return result


def require_jargon_group(session: Session, group_id: str) -> None:
    group = session.get(SharingGroup, group_id)
    capability = session.get(SharingGroupCapability, (group_id, "jargon"))
    if not group or not group.enabled or not capability or not capability.enabled:
        raise HTTPException(409, "请选择启用黑话的共享组")


@router.post("/library/scope")
def change_jargon_scope(request: JargonScopeChange):
    if request.kind == "chat":
        require_existing_session_id(request.target)
    elif request.kind == "global" and request.target:
        raise HTTPException(400, "通用词条无需目标 ID")
    with get_db_session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if request.kind == "group":
            require_jargon_group(session, request.target)
        if request.kind == "chat" and stored_jargon_members(session.connection(), request.target):
            raise HTTPException(409, "该聊天已加入黑话共享组，请选择共享组作为目标作用域")
        materialize(session.connection(), list(request.versions))
        sources = list(session.exec(select(Jargon).where(col(Jargon.id).in_(request.versions.keys()), Jargon.scope_kind != "snapshot")).all())
        chosen = next((r for r in sources if r.id == request.inherit_id), None)
        if not chosen or len(sources) != len(request.versions) or any(r.revision != request.versions[r.id] for r in sources):
            raise HTTPException(409, "释义已变化，请刷新后重新选择")
        if any(row.scope_kind == "global" for row in sources):
            raise HTTPException(409, "通用词条不能移动、复制或参与作用域合并")
        if request.kind == "global" and (request.action != "move" or len(sources) != 1):
            raise HTTPException(400, "请选择一个普通词条转为通用词条")
        if any(term_key(r.content) != term_key(chosen.content) for r in sources):
            raise HTTPException(400, "只能合并同名词条")
        if any(r.archived for r in sources) and request.action != "split":
            raise HTTPException(409, "历史版本只能复制到新的作用域")
        target = scope_row(session, chosen.content, request.kind, request.target)
        if request.kind == "global" and target:
            raise HTTPException(409, "通用词库已有同名词条")
        if request.action != "merge" and len(sources) != 1:
            raise HTTPException(400, "请选择一个来源")
        if target and (request.action != "merge" or target.id not in request.versions):
            raise HTTPException(409, "目标已存在同名释义，请在合并中选择继承版本")
        if request.action != "split":
            for row in sources:
                row.archived = True
                row.revision += 1
            session.flush()
        result = copy_version(session, chosen, request.kind, request.target)
        if request.kind == "global":
            result.allow_update = True
            result.is_complete = False
        inherit_evidence(session, result, sources)
        return {"id": result.id}


def has_snapshot_members(session: Session) -> bool:
    """在同一读事务内选择实体表或包含继承版本的视图。"""
    db = session.connection()
    db.exec_driver_sql("BEGIN")
    return db.exec_driver_sql("SELECT 1 FROM jargon_snapshot_members WHERE chat IS NOT NULL LIMIT 1").first() is not None


@router.get("/list", response_model=JargonListResponse)
def get_jargon_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    session_id: Optional[str] = Query(None, description="按聊天流ID筛选"),
    session_ids: Optional[List[str]] = JARGON_SESSION_IDS_QUERY,
    jargon_status: Optional[Literal["confirmed_jargon", "confirmed_not_jargon", "manual_jargon", "pending"]] = Query(
        None,
        description="按黑话判定状态筛选",
    ),
    is_jargon: Optional[bool] = Query(None, description="按是否是黑话筛选"),
    is_complete: Optional[bool] = Query(None, description="按是否推断完成筛选"),
    is_global: Optional[bool] = Query(None, description="按是否为通用词条筛选"),
    grouped: bool = Query(False),
    compact: bool = False,
    group_id: Optional[str] = Query(None),
    sort_by: str = Query("time", description="黑话排序: time/time_asc/created/created_asc/count"),
) -> JargonListResponse:
    """获取黑话列表。

    Args:
        page: 页码，从 1 开始。
        page_size: 每页数量，范围为 1-100。
        search: 搜索关键词。
        session_id: 聊天流 ID 筛选条件。
        session_ids: 共享组聊天流 ID 列表，匹配其中任一聊天流。
        jargon_status: 黑话判定状态筛选条件。
        is_jargon: 是否为黑话的筛选条件。
        is_complete: 是否推断完成的筛选条件。
        is_global: 是否为通用词条的筛选条件。
        sort_by: 按活跃时间、创建时间或遇见次数排列。

    Returns:
        JargonListResponse: 分页后的黑话列表。
    """
    try:
        if sort_by not in {"time", "time_asc", "created", "created_asc", "count"}:
            raise HTTPException(status_code=400, detail=f"不支持的黑话排序: {sort_by}")

        chats = (parse_session_id_candidates(session_id) or [session_id]) if session_id else session_ids
        def filtered(model: Type[Jargon]) -> Any:
            eligible = apply_jargon_list_filters(
                build_jargon_list_select(model), model, search=search, session_id=session_id, jargon_status=jargon_status,
                is_jargon=is_jargon, is_complete=is_complete, is_global=is_global,
            )
            if session_ids is not None:
                eligible = eligible.where(visible_in_chats(session_ids, model))
            if group_id:
                eligible = eligible.where(model.scope_kind == "group", model.scope_id == group_id)
            return eligible

        with get_db_session() as session:
            inherited = has_snapshot_members(session)
            model = read_jargons(representative=grouped and not chats, chats=chats) if inherited else Jargon
            eligible = filtered(model)
            def sort_key(source: Type[Jargon]) -> Any:
                if sort_by == "count":
                    return col(source.count)
                return col(source.created_timestamp) if sort_by.startswith("created") else col(source.updated_timestamp)

            sort_column = sort_key(model)
            ascending = sort_by.endswith("_asc")

            def ordering(value: Any, row_id: Any) -> List[Any]:
                return [case((value.is_(None), 1), else_=0), value.asc() if ascending else value.desc(),
                        row_id.asc() if ascending else row_id.desc()]

            order = ordering(sort_column, col(model.id))
            count_statement = eligible
            statement = eligible.order_by(*order)
            if grouped:
                term = func.lower(func.trim(model.content))
                # 排序只需词、排序字段和 ID，不把每个聊天的完整释义写入临时排序表。
                ranked = eligible.with_only_columns(
                    model.content, model.id, sort_column.label("sort_value"),
                    func.row_number().over(partition_by=term, order_by=order).label("n"),
                ).subquery()
                count_statement = select(ranked).where(ranked.c.n == 1)
                statement = count_statement.add_columns(func.count().over().label("total")).order_by(
                    *ordering(ranked.c.sort_value, ranked.c.id))
            page_rows = session.execute(statement.offset((page - 1) * page_size).limit(page_size)).mappings().all()
            if grouped:
                total = page_rows[0]["total"] if page_rows else (0 if page == 1 else count_jargon_query(session, count_statement))
            else:
                total = count_jargon_query(session, count_statement)
            if grouped and page_rows:
                keys = [term_key(row["content"]) for row in page_rows]
                full = read_jargons(chats=chats) if inherited else Jargon
                # 先用词名索引提取当前页，再应用分类。否则 SQLite 会再次扫描该分类的所有记录。
                candidates = build_jargon_list_select(full).where(func.lower(func.trim(full.content)).in_(keys)).cte(
                    "page_versions").prefix_with("MATERIALIZED")
                full = aliased(Jargon, candidates, adapt_on_names=True)
                statement = filtered(full).order_by(*ordering(sort_key(full), full.id))
                variants = session.execute(statement).mappings().all()
                names = scope_names([(r["scope_kind"], r["scope_id"], r["id"]) for r in variants], session)
            else:
                variants = page_rows
            by_term: Dict[str, List[Mapping[str, Any]]] = {}
            if grouped:
                for row in variants:
                    by_term.setdefault(term_key(row["content"]), []).append(row)
            visible_rows = [rows[0] for rows in by_term.values()] if grouped and compact else variants
            session_ids_on_page = [sid for row in visible_rows for sid in get_session_ids(row["session_id_dict"])]
            cache = build_session_display_name_cache(session_ids_on_page, session, include_message_fallback=True)
            if grouped:
                data = []
                for page_row in page_rows:
                    matches = by_term[term_key(page_row["content"])]
                    versions = []
                    for row in matches[:1] if compact else matches:
                        version = jargon_list_row_to_dict(row, session, cache)
                        version["scope_name"] = names[row["scope_kind"], row["scope_id"]]
                        versions.append(version)
                    data.append({
                        **versions[0],
                        "scope_ids": [r["id"] for r in matches],
                        "scope_names": [names[r["scope_kind"], r["scope_id"]] for r in matches],
                        "meaning_count": len({r["meaning"] for r in matches if r["meaning"]}),
                        "scope_versions": versions if len(versions) > 1 else [],
                    })
            else:
                data = [jargon_list_row_to_dict(row, session, cache) for row in variants]

        return JargonListResponse(
            success=True,
            total=total,
            page=page,
            page_size=page_size,
            data=data,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取黑话列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话列表失败: {str(e)}") from e


@router.get("/chats", response_model=ChatListResponse)
def get_chat_list(include_empty: bool = Query(False, description="是否包含没有黑话记录的聊天流")) -> ChatListResponse:
    """获取可用于黑话新增、编辑和筛选的聊天流列表。

    Returns:
        ChatListResponse: 已知真实聊天流，以及旧黑话记录中保留的聊天流。
    """
    try:
        with get_db_session() as session:
            chat_sessions = session.exec(select(ChatSession)).all()
            db = session.connection()
            local_counts = dict(db.exec_driver_sql("""SELECT scope_id,count(*) FROM jargons
                WHERE archived=0 AND scope_kind='chat' GROUP BY scope_id""").all())
            terms: Dict[Tuple[str, str], Set[str]] = {}
            for kind, owner, key in session.exec(select(
                Jargon.scope_kind, Jargon.scope_id, func.lower(func.trim(Jargon.content))
            ).where(col(Jargon.archived) == false(), col(Jargon.scope_kind).in_(["global", "group"]))):
                terms.setdefault((kind, owner), set()).add(key)
            seen_session_ids = set(local_counts)
            inherited = session.connection().exec_driver_sql(
                "SELECT chat,snapshot FROM jargon_snapshot_members WHERE chat IS NOT NULL"
            ).all()
            seen_session_ids.update(chat for chat, _ in inherited)
            frozen_terms: Dict[str, Set[str]] = {}
            excluded: Dict[str, Set[str]] = {}
            for snap, key in session.connection().exec_driver_sql("""SELECT scope_id,lower(trim(content)) FROM jargons
                    WHERE scope_kind='snapshot' AND scope_id IN (SELECT snapshot FROM jargon_snapshot_members WHERE chat IS NOT NULL)"""):
                frozen_terms.setdefault(snap, set()).add(key)
            for chat, key in session.connection().exec_driver_sql("""SELECT m.chat,lower(trim(f.content))
                    FROM jargons fork JOIN jargon_snapshot_members m ON m.end_id=(
                        SELECT min(end_id) FROM jargon_snapshot_members WHERE end_id>=-fork.id)
                    JOIN jargons f ON f.id=-fork.id-m.shift AND f.scope_id=m.snapshot
                    WHERE fork.id<0 AND m.chat IS NOT NULL"""):
                excluded.setdefault(chat, set()).add(key)
            known_ids = seen_session_ids | {chat.session_id for chat in chat_sessions}
            shared_groups: Dict[str, List[str]] = {}
            for group in list_groups():
                group_terms = terms.get(("group", group["id"]))
                if group_terms and group["enabled"] and group["capabilities"].get("jargon"):
                    for sid in resolved_members(group):
                        shared_groups.setdefault(sid, []).append(group["id"])
            global_terms = terms.get(("global", ""), set())
            # 物理聊天词条直接按作用域索引计数；同一组或快照的共享词集只构建一次。
            snapshots = dict(inherited)
            shared_cache: Dict[Tuple[Tuple[str, ...], Optional[str]], Set[str]] = {}
            jargon_counts = {}
            for sid in known_ids:
                owners = tuple(shared_groups.get(sid, ()))
                snapshot = snapshots.get(sid)
                cache_key = owners, snapshot
                if cache_key not in shared_cache:
                    shared_cache[cache_key] = global_terms.union(
                        *(terms["group", owner] for owner in owners), frozen_terms.get(snapshot, ()))
                shared = shared_cache[cache_key]
                hidden = excluded.get(sid)
                if hidden:
                    shared = shared - (hidden - global_terms).difference(*(terms["group", owner] for owner in owners))
                local = local_counts.get(sid, 0)
                overlap = 0
                if local and shared:
                    if local < len(shared):
                        overlap = sum(key in shared for key in db.exec_driver_sql("""
                            SELECT lower(trim(content)) FROM jargons WHERE archived=0 AND scope_kind='chat' AND scope_id=?
                        """, (sid,)).scalars())
                    else:
                        overlap = db.exec_driver_sql("""SELECT count(*) FROM json_each(?) k WHERE EXISTS (
                            SELECT 1 FROM jargons WHERE archived=0 AND scope_kind='chat' AND scope_id=?
                            AND lower(trim(content))=k.value)""", (json.dumps(list(shared)), sid)).scalar_one()
                jargon_counts[sid] = local + len(shared) - overlap
            if not include_empty:
                chat_sessions = [chat for chat in chat_sessions if jargon_counts[chat.session_id]]
            chat_by_session_id: Dict[str, ChatInfoResponse] = {}
            display_name_cache = {
                chat_session.session_id: chat_session_to_display_name(chat_session) for chat_session in chat_sessions
            }
            orphan_session_ids = [
                session_id for session_id in seen_session_ids if session_id not in display_name_cache
            ]
            display_name_cache.update(
                build_session_display_name_cache(orphan_session_ids, session, include_message_fallback=True)
            )

            for chat_session in chat_sessions:
                chat_by_session_id[chat_session.session_id] = ChatInfoResponse(
                    session_id=chat_session.session_id,
                    chat_name=display_name_cache[chat_session.session_id],
                    platform=chat_session.platform,
                    account_id=chat_session.account_id,
                    target_id=chat_session.group_id or chat_session.user_id,
                    is_group=bool(chat_session.group_id),
                    jargon_count=jargon_counts.get(chat_session.session_id, 0),
                )

            # 兼容旧数据：黑话记录里可能还保留着已经不在 chat_sessions 表中的聊天流。
            for stored_session_id in seen_session_ids:
                if stored_session_id in chat_by_session_id:
                    continue
                chat_by_session_id[stored_session_id] = ChatInfoResponse(
                    session_id=stored_session_id,
                    chat_name=display_name_cache[stored_session_id],
                    platform=None,
                    account_id=None,
                    is_group=False,
                    jargon_count=jargon_counts[stored_session_id],
                )

            # 聚合最新消息时间，包含旧黑话保留但已不在会话表中的聊天。
            latest_message_times = dict(session.exec(
                select(Messages.session_id, func.max(Messages.timestamp))
                .where(col(Messages.session_id).in_(chat_by_session_id))
                .group_by(col(Messages.session_id))
            ).all())

        result = sorted(chat_by_session_id.values(), key=lambda item: (item.chat_name, item.session_id))
        result.sort(key=lambda item: latest_message_times.get(item.session_id, datetime.min), reverse=True)
        return ChatListResponse(success=True, data=result)

    except Exception as e:
        logger.error(f"获取聊天列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取聊天列表失败: {str(e)}") from e


@router.get("/groups", response_model=JargonGroupListResponse)
def get_jargon_groups() -> JargonGroupListResponse:
    """按黑话共享配置解析组成员，不生成新的会话 ID。"""
    enabled = [group for group in list_groups() if group["enabled"] and group["capabilities"].get("jargon")]
    if not enabled:
        return JargonGroupListResponse(data=[])
    chats = {chat.session_id: chat for chat in get_chat_list(include_empty=True).data}
    groups: List[JargonGroupInfo] = []
    for index, group in enumerate(enabled):
        session_ids = resolved_members(group)
        is_global = any(rule == {"platform": "*", "item_id": "*", "rule_type": "all"} for rule in group["rules"])
        resolved_ids = sorted(session_ids)
        groups.append(
            JargonGroupInfo(
                index=index,
                id=group["id"],
                color=group["color"],
                name=group["name"],
                session_ids=resolved_ids,
                members=[chats[session_id] for session_id in resolved_ids if session_id in chats],
                is_global=is_global,
            )
        )
    with get_db_session() as session:
        for g in groups:
            g.jargon_count = session.exec(select(func.count()).select_from(Jargon).where(
                col(Jargon.archived).is_(False), Jargon.scope_kind == "group", Jargon.scope_id == g.id)).one()
    return JargonGroupListResponse(data=groups)


@router.get("/stats/overview")
def get_jargon_overview() -> Dict[str, int]:
    """读取首屏和侧栏实际使用的词数。"""
    with get_db_session() as session:
        inherited = has_snapshot_members(session)
        model = read_jargons(representative=True) if inherited else Jargon
        distinct_terms = select(func.count(func.distinct(func.lower(func.trim(model.content))))).where(
            col(model.archived).is_(False))
        # 实体词库沿词名索引跳到下一个不同的词，避免对数百万份相同词名逐一去重。
        total = session.exec(distinct_terms).one() if inherited else session.connection().exec_driver_sql("""
            WITH RECURSIVE terms(term) AS (
                SELECT min(lower(trim(content))) FROM jargons
                UNION ALL SELECT (SELECT min(lower(trim(content))) FROM jargons WHERE lower(trim(content))>terms.term)
                FROM terms WHERE term IS NOT NULL
            ) SELECT count(*) FROM terms WHERE term IS NOT NULL AND EXISTS (
                SELECT 1 FROM jargons WHERE lower(trim(content))=terms.term AND archived=0)
        """).scalar_one()
        return {
            "total": total,
            "confirmed_jargon": session.exec(distinct_terms.where(effective_jargon_condition(model))).one(),
            "complete_count": session.exec(distinct_terms.where(col(model.is_complete).is_(True))).one(),
        }


@router.get("/stats/summary", response_model=JargonStatsResponse)
def get_jargon_stats(include_chat_distribution: bool = True) -> JargonStatsResponse:
    """获取黑话统计数据。

    Returns:
        JargonStatsResponse: 黑话总数、确认状态和聊天分布统计。
    """
    try:
        with get_db_session() as session:
            model = read_jargons(representative=True) if has_snapshot_members(session) else Jargon
            # 每个词在不同域可以有不同状态，各项统计分别判断是否至少命中一个域。
            conditions = {
                "confirmed_jargon": effective_jargon_condition(model),
                "confirmed_not_jargon": col(model.is_jargon).is_(False),
                "pending": pending_jargon_condition(model),
                "manual_jargon": col(model.allow_update).is_(False),
                "global_count": col(model.is_global).is_(True),
                "complete_count": col(model.is_complete).is_(True),
            }
            by_term = select(*(
                func.max(case((condition, 1), else_=0)).label(name) for name, condition in conditions.items()
            )).where(col(model.archived).is_(False)).group_by(func.lower(func.trim(model.content))).subquery()
            counts = dict(session.execute(select(
                func.count().label("total"),
                *(func.coalesce(func.sum(by_term.c[name]), 0).label(name) for name in conditions),
            ).select_from(by_term)).mappings().one())
            top_chats_counter: Dict[str, int] = {}
            # 相同快照按仍引用该词的聊天数计权，避免展开整个“词 × 聊天”关系。
            if include_chat_distribution:
                for session_id_dict, weight in session.connection().exec_driver_sql("""
                    WITH members AS (SELECT snapshot,count(*) n FROM jargon_snapshot_members
                                     WHERE chat IS NOT NULL GROUP BY snapshot),
                    taken AS (SELECT -fork.id-m.shift AS source, count(*) n FROM jargons fork
                              JOIN jargon_snapshot_members m ON m.end_id=(
                                  SELECT min(end_id) FROM jargon_snapshot_members WHERE end_id>=-fork.id)
                              WHERE fork.id<0 AND m.chat IS NOT NULL GROUP BY source)
                    SELECT session_id_dict,1 FROM jargons WHERE archived=0
                    UNION ALL SELECT f.session_id_dict,m.n-coalesce(t.n,0) FROM members m
                        JOIN jargons f ON f.scope_kind='snapshot' AND f.scope_id=m.snapshot
                        LEFT JOIN taken t ON t.source=f.id WHERE m.n>coalesce(t.n,0)"""):
                    for session_id in parse_session_id_dict(session_id_dict):
                        top_chats_counter[session_id] = top_chats_counter.get(session_id, 0) + weight

            top_chats_dict = dict(sorted(top_chats_counter.items(), key=lambda item: item[1], reverse=True)[:5])
            chat_count = len(top_chats_counter)

        return JargonStatsResponse(
            success=True,
            data={
                **counts,
                "chat_count": chat_count,
                "top_chats": top_chats_dict,
            },
        )

    except Exception as e:
        logger.error(f"获取黑话统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话统计失败: {str(e)}") from e


@router.post("/export", response_model=JargonExportResponse)
def export_jargons(request: JargonExportRequest) -> JargonExportResponse:
    """导出黑话记录，可选携带 platform/id/type 形式的聊天目标信息。"""

    try:
        model = read_jargons(chats=request.session_ids)
        statement = select(model).where(col(model.archived).is_(False)).order_by(col(model.count).desc(), col(model.id).desc())
        if request.group_id is not None:
            statement = statement.where(model.scope_kind == "group", model.scope_id == request.group_id)
        elif request.session_ids is not None:
            statement = statement.where(visible_in_chats(request.session_ids, model))
        if request.ids is not None:
            unique_ids = list(dict.fromkeys(request.ids))
            statement = statement.where(col(model.id).in_(unique_ids))

        with get_db_session() as session:
            jargons = session.exec(statement).all()
            if request.ids and len(jargons) != len(set(request.ids)):
                found_ids = {jargon.id for jargon in jargons}
                missing_ids = sorted(set(request.ids) - found_ids)
                raise HTTPException(status_code=400, detail=f"部分黑话不存在: {missing_ids}")

            groups = {g["id"]: g for g in list_groups()} if any(row.scope_kind == "group" for row in jargons) else {}
            chat_session_by_session_id: Optional[Dict[str, ChatSession]] = None
            if request.include_chat_info:
                session_ids: List[str] = []
                for jargon in jargons:
                    if jargon.scope_kind == "chat":
                        session_ids.append(jargon.scope_id)
                    elif jargon.scope_kind == "group" and jargon.scope_id in groups:
                        session_ids.extend(resolved_members(groups[jargon.scope_id]))
                unique_session_ids = list(dict.fromkeys(session_ids))
                chat_session_by_session_id = {}
                if unique_session_ids:
                    chat_sessions = session.exec(
                        select(ChatSession).where(col(ChatSession.session_id).in_(unique_session_ids))
                    ).all()
                    chat_session_by_session_id = {
                        chat_session.session_id: chat_session for chat_session in chat_sessions
                    }

            items = [jargon_to_export_item(jargon, chat_session_by_session_id, groups.get(jargon.scope_id)) for jargon in jargons]

        return JargonExportResponse(
            exported_at=datetime.now().isoformat(),
            include_chat_info=request.include_chat_info,
            count=len(items),
            jargons=items,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导出黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出黑话失败: {str(e)}") from e


def import_scopes(session: Session, chats: Set[str], is_global: bool) -> Set[Tuple[str, str]]:
    if is_global:
        return {("global", "")}
    owners = set()
    for chat in chats:
        groups = stored_jargon_members(session.connection(), chat)
        if len(groups) > 1:
            raise HTTPException(409, "聊天的黑话共享组重叠，请先解决冲突")
        owners.add(("group", groups[0][0]) if groups else ("chat", chat))
    return owners


def find_import_conflicts(session: Session, content: str, owners: Set[Tuple[str, str]]) -> List[Jargon]:
    model = read_jargons(chats=[owner for kind, owner in owners if kind == "chat"])
    stmt = select(model).where(col(model.archived).is_(False),
                                func.lower(func.trim(model.content)) == term_key(content))
    scope = tuple_(model.scope_kind, model.scope_id).in_(owners)
    return list(session.exec(stmt.where(scope).order_by(col(model.id))).all())


def conflict_record(session: Session, jargon: Jargon) -> JargonConflictRecord:
    return JargonConflictRecord(
        id=jargon.id, meaning=jargon.meaning, is_global=jargon.is_global,
        group_name=session.get(SharingGroup, jargon.scope_id).name if jargon.scope_kind == "group" else None,
        session_ids=[jargon.scope_id] if jargon.scope_kind == "chat" else [],
    )


def apply_import_meanings(records: List[Jargon], item: JargonExportItem, *, merge: bool = False) -> None:
    """原地更新已确认的同名记录，保留证据、遇见次数和已关联的聊天。"""
    for row in records:
        row.revision += 1
        incoming = item.meaning.strip()
        if merge:
            existing = row.meaning.rstrip()
            if incoming and incoming != existing.strip() and not existing.endswith("\n\n" + incoming):
                row.meaning = f"{existing}\n\n{incoming}" if existing else incoming
        else:
            row.meaning = incoming
        row.is_jargon = None if item.is_jargon and not row.meaning else item.is_jargon
        row.created_by = JargonCreatedBy.MANUAL
        if item.allow_update is not None:
            row.allow_update = item.allow_update
        elif row.scope_kind != "global":
            row.allow_update = False
        row.updated_timestamp = datetime.now()


def link_import_evidence(session: Session, item: JargonImportItem, rows: List[Jargon]) -> None:
    if not item.excerpt or not rows:
        return
    event = JargonLearningRecord(content=item.content, scope_kind="import", scope_id="", excerpt=item.excerpt,
                                 recorded_at=datetime.now())
    session.add(event)
    session.flush()
    for row in rows:
        session.add(JargonEvidenceLink(jargon_id=row.id, record_id=event.id))
    if all(row.scope_kind != "global" for row in rows):
        attach_common_learning(session, event)


def import_target_sessions(request: JargonImportRequest) -> List[str]:
    if request.is_global and request.target_session_ids:
        raise HTTPException(status_code=400, detail="导入通用词库时不能同时指定聊天")
    if not request.jargons:
        raise HTTPException(status_code=400, detail="导入文件中没有黑话")
    return [] if request.is_global else require_existing_session_ids(request.target_session_ids)


@router.post("/import/preview")
def preview_jargon_import(request: JargonImportRequest) -> List[JargonImportConflict]:
    """按导入范围返回同名记录，供用户比较释义。"""
    targets = set(import_target_sessions(request))
    result: List[JargonImportConflict] = []
    with get_db_session(auto_commit=False) as session:
        owners = import_scopes(session, targets, request.is_global)
        for content in dict.fromkeys(item.content.strip() for item in request.jargons):
            records = find_import_conflicts(session, content, owners)
            if records:
                result.append(JargonImportConflict(content=content, records=[conflict_record(session, row) for row in records]))
    return result


@router.post("/import", response_model=JargonImportResponse)
def import_jargons(request: JargonImportRequest) -> JargonImportResponse:
    """将黑话 JSON 导入到用户选择的真实聊天流或通用词库。"""

    try:
        target_session_ids = import_target_sessions(request)
        imported_count = 0
        skipped_count = 0
        failed_count = 0
        target_session_id_set = set(target_session_ids)

        with get_db_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            targets = import_scopes(session, target_session_id_set, request.is_global)
            reviewed = request.replacements is not None or bool(request.merges)
            replacements = request.replacements or {}
            if reviewed:
                # 在同一 SQLite 写事务中校验快照和提交，避免覆盖确认期间的新修改。
                titles = [term_key(item.content.strip()) for item in request.jargons]
                if len(set(titles)) != len(titles):
                    raise HTTPException(status_code=400, detail="所选候选包含同名词条，请修改后再导入")
            for item in request.jargons:
                content = item.content.strip()
                if not content:
                    failed_count += 1
                    continue

                meaning = item.meaning.strip()
                imported_rows = []
                overlapping_jargons = find_import_conflicts(session, content, targets)
                if reviewed:
                    if content in request.reviewed_skips:
                        skipped_count += 1
                        continue
                    expected = request.merges[content] if content in request.merges else replacements.get(content, [])
                    if expected != [conflict_record(session, row) for row in overlapping_jargons]:
                        raise HTTPException(status_code=409, detail=f"「{content}」的冲突记录已变化，请重新导入并确认")
                    if overlapping_jargons:
                        materialize(session.connection(), [row.id for row in overlapping_jargons])
                        apply_import_meanings(overlapping_jargons, item, merge=content in request.merges)
                        imported_rows.extend(overlapping_jargons)
                        remaining = targets - {(row.scope_kind, row.scope_id) for row in overlapping_jargons}
                        if request.is_global or not remaining:
                            link_import_evidence(session, item, imported_rows)
                            imported_count += 1
                            continue
                        overlapping_jargons = []
                    else:
                        remaining = targets

                if overlapping_jargons and request.conflict_strategy == "skip":
                    skipped_count += 1
                    continue

                owners = remaining if reviewed else targets
                if request.conflict_strategy == "overwrite" and overlapping_jargons:
                    materialize(session.connection(), [row.id for row in overlapping_jargons])
                    apply_import_meanings(overlapping_jargons, item)
                    imported_rows.extend(overlapping_jargons)
                    owners = owners - {(row.scope_kind, row.scope_id) for row in overlapping_jargons}
                    if request.is_global or not owners:
                        link_import_evidence(session, item, imported_rows)
                        imported_count += 1
                        continue

                session.flush()
                for kind, owner in sorted(owners):
                    row = Jargon(
                        content=content, meaning=meaning, scope_kind=kind,
                        scope_id=owner, session_id_dict=dump_session_id_dict({owner: max(item.count, 1)} if kind == "chat" else {}),
                        count=max(item.count, 0), is_jargon=None if item.is_jargon and not meaning else item.is_jargon,
                        allow_update=item.allow_update if item.allow_update is not None else request.is_global,
                        is_complete=item.is_complete and not request.is_global, is_global=request.is_global, created_by=JargonCreatedBy.MANUAL,
                    )
                    session.add(row)
                    imported_rows.append(row)
                session.flush()
                link_import_evidence(session, item, imported_rows)
                imported_count += 1

        logger.info(
            f"导入黑话完成: targets={target_session_ids}, imported={imported_count}, "
            f"skipped={skipped_count}, failed={failed_count}, strategy={request.conflict_strategy}"
        )
        return JargonImportResponse(
            message=f"导入完成：成功 {imported_count} 个，跳过 {skipped_count} 个，失败 {failed_count} 个",
            imported_count=imported_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入黑话失败: {str(e)}") from e


@router.get("/{jargon_id}", response_model=JargonDetailResponse)
def get_jargon_detail(jargon_id: int) -> JargonDetailResponse:
    """获取黑话详情。

    Args:
        jargon_id: 黑话记录 ID。

    Returns:
        JargonDetailResponse: 指定黑话记录的详细信息。
    """
    try:
        with get_db_session() as session:
            inherited = inherited_source(session.connection(), jargon_id)
            row = session.execute(
                build_jargon_compatible_select().where(col(Jargon.id) == (inherited[0] if inherited else jargon_id))
            ).mappings().first()
            if row is None or (row["scope_kind"] == "snapshot" and not inherited):
                raise HTTPException(status_code=404, detail="黑话不存在")
            if inherited:
                row = dict(row, id=jargon_id, scope_kind="chat", scope_id=inherited[1], archived=False, revision=0)
            data = JargonResponse(**jargon_list_row_to_dict(row, session))

        return JargonDetailResponse(success=True, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取黑话详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话详情失败: {str(e)}") from e


@router.post("/", response_model=JargonCreateResponse)
def create_jargon(request: JargonCreateRequest) -> JargonCreateResponse:
    """创建黑话。

    Args:
        request: 创建黑话所需的请求数据。

    Returns:
        JargonCreateResponse: 创建结果和新黑话数据。
    """
    try:
        content = request.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="黑话内容不能为空")
        meaning = (request.meaning or "").strip()

        raw_session_ids = request.session_ids if request.session_ids is not None else (
            [request.session_id] if request.session_id else []
        )
        # 通用词条不要求来源聊天；兼容已有调用方提供的真实来源聊天。
        if request.group_id:
            if raw_session_ids or request.is_global:
                raise HTTPException(400, "请选择一个归属范围")
        session_ids = [] if request.group_id else (
            require_existing_session_ids(raw_session_ids) if raw_session_ids or not request.is_global else []
        )
        with get_db_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if request.group_id:
                require_jargon_group(session, request.group_id)
                owners = {("group", request.group_id)}
            else:
                owners = import_scopes(session, set(session_ids), request.is_global)
            existing = find_import_conflicts(session, content, owners)
            if any(row.created_by == JargonCreatedBy.MANUAL or not row.allow_update for row in existing):
                raise HTTPException(status_code=400, detail="该范围中已有同名的手动或暂停更新词条")
            materialize(session.connection(), [row.id for row in existing])
            for row in existing:
                row.archived = True
                row.revision += 1
            session.flush()
            for kind, owner in sorted(owners):
                previous = next((row for row in existing if (row.scope_kind, row.scope_id) == (kind, owner)), None)
                if previous:
                    jargon = copy_version(session, previous, previous.scope_kind, owner)
                    jargon.meaning = meaning
                    jargon.created_by = JargonCreatedBy.MANUAL
                    jargon.allow_update = request.is_global
                    jargon.is_jargon = True if meaning else None
                    continue
                jargon = Jargon(content=content, meaning=meaning, scope_kind=kind,
                                scope_id=owner, allow_update=request.is_global, is_global=request.is_global, created_by=JargonCreatedBy.MANUAL,
                                session_id_dict=build_session_id_dict_for_sessions([owner] if kind == "chat" else []),
                                is_jargon=True if meaning else None)
                session.add(jargon)
                session.flush()
            data = JargonResponse(**jargon_to_dict(jargon, session))

        return JargonCreateResponse(success=True, message="创建成功", data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建黑话失败: {str(e)}") from e


def apply_jargon_edit(session: Session, jargon: Jargon, update_data: Dict[str, Any]) -> None:
    if {"session_ids", "session_id", "is_global"} & update_data.keys():
        ids = update_data.get("session_ids")
        if ids is None and update_data.get("session_id"):
            ids = [update_data["session_id"]]
        global_target = update_data.get("is_global", jargon.is_global)
        if global_target != jargon.is_global or (ids is not None and set(ids) != set(get_session_ids(jargon.session_id_dict))):
            raise HTTPException(status_code=409, detail="请使用管理作用域操作修改归属")
    jargon.revision += 1
    if "meaning" in update_data:
        jargon.meaning = update_data["meaning"] or ""
    if "is_global" in update_data and update_data["is_global"] is not None:
        jargon.is_global = update_data["is_global"]
    if "is_jargon" in update_data:
        jargon.is_jargon = update_data["is_jargon"]
    if "created_by" in update_data and update_data["created_by"] is not None:
        jargon.created_by = update_data["created_by"]
        if "allow_update" not in update_data:
            jargon.allow_update = jargon.created_by != JargonCreatedBy.MANUAL
    if update_data.get("allow_update") is not None:
        jargon.allow_update = update_data["allow_update"]
    if jargon.scope_kind == "global" and jargon.allow_update:
        jargon.is_complete = False
    jargon.updated_timestamp = datetime.now()
    session.add(jargon)


@router.patch("/{jargon_id}", response_model=JargonUpdateResponse)
def update_jargon(jargon_id: int, request: JargonUpdateRequest) -> JargonUpdateResponse:
    """增量更新黑话。

    Args:
        jargon_id: 黑话记录 ID。
        request: 只包含需要更新字段的请求数据。

    Returns:
        JargonUpdateResponse: 更新结果和更新后的黑话数据。
    """
    try:
        with get_db_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            materialize(session.connection(), [jargon_id])
            jargon = session.exec(select(Jargon).where(col(Jargon.id) == jargon_id)).first()
            if not jargon or jargon.archived:
                raise HTTPException(status_code=404, detail="黑话不存在")

            if request.revision is not None and request.revision != jargon.revision:
                raise HTTPException(409, "词条已更新，请重新打开后编辑")
            changes = request.model_dump(exclude_unset=True, exclude={"revision", "all_scope_revisions"})
            content = changes.pop("content", None)
            if content is not None and content != jargon.content:
                raise HTTPException(409, "词条名称无法修改，请新建词条")
            targets = [jargon]
            if request.all_scope_revisions is not None:
                if changes.keys() - {"meaning", "allow_update", "is_jargon"}:
                    raise HTTPException(400, "保存到全部仅支持修改含义、状态和更新开关")
                model = read_jargons()
                versions = dict(session.exec(select(model.id, model.revision).where(
                    col(model.archived).is_(False), func.lower(func.trim(model.content)) == term_key(jargon.content)
                )).all())
                if versions != request.all_scope_revisions:
                    raise HTTPException(409, "作用域或释义已变化，请重新打开词条后再保存")
                materialize(session.connection(), list(versions))
                targets = session.exec(select(Jargon).where(col(Jargon.id).in_(versions))).all()
            if changes:
                for target in targets:
                    apply_jargon_edit(session, target, changes)

            logger.info(f"更新黑话成功: id={jargon_id}")
            data = JargonResponse(**jargon_to_dict(jargon, session))

        return JargonUpdateResponse(success=True, message="更新成功", data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新黑话失败: {str(e)}") from e


@router.delete("/{jargon_id}", response_model=JargonDeleteResponse)
def delete_jargon(jargon_id: int) -> JargonDeleteResponse:
    """删除黑话。

    Args:
        jargon_id: 黑话记录 ID。

    Returns:
        JargonDeleteResponse: 删除结果。
    """
    try:
        with get_db_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            materialize(session.connection(), [jargon_id])
            jargon = session.exec(select(Jargon).where(col(Jargon.id) == jargon_id)).first()
            if not jargon or jargon.scope_kind == "snapshot":
                raise HTTPException(status_code=404, detail="黑话不存在")

            content = jargon.content
            jargon.archived = True
            jargon.revision += 1

            logger.info(f"删除黑话成功: id={jargon_id}, content={content}")

        return JargonDeleteResponse(success=True, message="删除成功", deleted_count=1)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除黑话失败: {str(e)}") from e


@router.post("/batch/delete", response_model=JargonDeleteResponse)
def batch_delete_jargons(request: BatchDeleteRequest) -> JargonDeleteResponse:
    """批量删除黑话。

    Args:
        request: 包含要删除黑话 ID 列表的请求。

    Returns:
        JargonDeleteResponse: 批量删除结果。
    """
    try:
        if not request.ids:
            raise HTTPException(status_code=400, detail="ID列表不能为空")

        with get_db_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            materialize(session.connection(), request.ids)
            result = session.exec(update(Jargon).where(col(Jargon.id).in_(request.ids), col(Jargon.archived).is_(False)).values(archived=True, revision=Jargon.revision + 1))
            deleted_count = result.rowcount or 0

            logger.info(f"批量删除黑话成功: 删除了 {deleted_count} 条记录")

        return JargonDeleteResponse(
            success=True,
            message=f"成功删除 {deleted_count} 条黑话",
            deleted_count=deleted_count,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量删除黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量删除黑话失败: {str(e)}") from e


@router.post("/batch/set-jargon", response_model=JargonUpdateResponse)
def batch_set_jargon_status(
    ids: Annotated[List[int], Query(description="黑话ID列表")],
    is_jargon: Annotated[bool, Query(description="是否是黑话")],
) -> JargonUpdateResponse:
    """批量设置黑话状态。

    Args:
        ids: 需要更新状态的黑话 ID 列表。
        is_jargon: 目标黑话状态。

    Returns:
        JargonUpdateResponse: 批量更新结果。
    """
    try:
        if not ids:
            raise HTTPException(status_code=400, detail="ID列表不能为空")

        with get_db_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            materialize(session.connection(), ids)
            jargons = session.exec(select(Jargon).where(col(Jargon.id).in_(ids), col(Jargon.archived).is_(False))).all()
            for jargon in jargons:
                jargon.revision += 1
                jargon.is_jargon = is_jargon
                jargon.updated_timestamp = datetime.now()
                session.add(jargon)
            updated_count = len(jargons)

            logger.info(f"批量更新黑话状态成功: 更新了 {updated_count} 条记录，is_jargon={is_jargon}")

        return JargonUpdateResponse(success=True, message=f"成功更新 {updated_count} 条黑话状态")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量更新黑话状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量更新黑话状态失败: {str(e)}") from e
