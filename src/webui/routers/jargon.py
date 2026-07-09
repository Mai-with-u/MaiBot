"""黑话（俚语）管理路由"""

from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Mapping, Optional, Set
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, cast, func, not_, or_, String as SQLString
from sqlmodel import Session, col, delete, select

from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.common.database.database import get_db_session
from src.common.database.database_model import ChatSession, Jargon, JargonCreatedBy, Messages
from src.common.logger import get_logger
from src.webui.dependencies import require_auth

logger = get_logger("webui.jargon")

router = APIRouter(prefix="/jargon", tags=["Jargon"], dependencies=[Depends(require_auth)])


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
    content: str
    meaning: Optional[str] = None
    session_id: str
    session_ids: List[str] = Field(default_factory=list)
    chat_name: Optional[str] = None  # 解析后的聊天名称，用于前端显示
    chat_names: List[str] = Field(default_factory=list)
    count: int = 0
    is_jargon: bool = False
    is_legacy_empty_meaning: bool = False
    is_complete: bool = False
    is_global: bool = False
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

    content: str = Field(..., description="黑话内容")
    meaning: Optional[str] = Field(None, description="含义")
    session_id: Optional[str] = Field(None, description="聊天流ID")
    session_ids: Optional[List[str]] = Field(None, description="聊天流ID列表")
    is_global: bool = Field(False, description="是否为全局黑话")


class JargonUpdateRequest(BaseModel):
    """黑话更新请求"""

    content: Optional[str] = None
    meaning: Optional[str] = None
    session_id: Optional[str] = None
    session_ids: Optional[List[str]] = None
    is_global: Optional[bool] = None
    is_jargon: Optional[bool] = None
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
    is_group: bool = False


class ChatListResponse(BaseModel):
    """聊天列表响应"""

    success: bool = True
    data: List[ChatInfoResponse]


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

    content: str
    meaning: str = ""
    count: int = 0
    is_jargon: bool = False
    is_complete: bool = False
    is_global: bool = False
    created_by: JargonCreatedBy = JargonCreatedBy.MANUAL
    targets: Optional[List[JargonTargetInfo]] = None


class JargonExportRequest(BaseModel):
    """黑话导出请求。"""

    ids: Optional[List[int]] = None
    include_chat_info: bool = False


class JargonExportResponse(BaseModel):
    """黑话导出响应。"""

    success: bool = True
    version: int = 1
    type: str = "maibot.jargon.export"
    exported_at: str
    include_chat_info: bool = False
    count: int
    jargons: List[JargonExportItem]


class JargonImportRequest(BaseModel):
    """黑话导入请求。"""

    target_session_ids: List[str] = Field(..., description="导入目标聊天流 ID 列表")
    jargons: List[JargonExportItem]
    conflict_strategy: Literal["skip", "overwrite"] = "skip"


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


def has_session_id(session_id_dict_str: Optional[str], session_id: str) -> bool:
    """判断记录是否包含指定聊天 ID。

    Args:
        session_id_dict_str: 数据库中保存的会话计数字典 JSON 字符串。
        session_id: 需要检查的聊天流 ID。

    Returns:
        bool: 记录包含该聊天 ID 时返回 True。
    """
    return session_id in parse_session_id_dict(session_id_dict_str)


def build_session_id_dict_search_tokens(session_id: str) -> Set[str]:
    """构建 JSON key 的精确文本标记，用于 SQL 侧过滤 session_id_dict。"""

    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        return set()
    return {
        json.dumps(normalized_session_id),
        json.dumps(normalized_session_id, ensure_ascii=False),
    }


def build_session_id_dict_filter(session_ids: List[str]) -> Optional[Any]:
    """构建 session_id_dict 包含任一聊天流 ID 的数据库过滤条件。"""

    conditions = []
    seen_tokens: Set[str] = set()
    for session_id in session_ids:
        for token in build_session_id_dict_search_tokens(session_id):
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            conditions.append(func.instr(col(Jargon.session_id_dict), token) > 0)
    if not conditions:
        return None
    return or_(*conditions)


def jargon_has_meaning_condition() -> Any:
    """有效黑话必须有非空含义。"""

    return func.length(func.trim(col(Jargon.meaning))) > 0


def effective_jargon_condition() -> Any:
    """WebUI 侧确认黑话的统一判定条件。"""

    return and_(col(Jargon.is_jargon).is_(True), jargon_has_meaning_condition())


def no_jargon_condition() -> Any:
    """WebUI 侧无黑话的统一判定条件，包含历史空含义黑话记录。"""

    return or_(col(Jargon.is_jargon).is_(False), col(Jargon.is_jargon).is_(None), not_(effective_jargon_condition()))


def is_legacy_empty_meaning_jargon(is_jargon: Any, meaning: Any) -> bool:
    """识别历史遗留的“标记为黑话但没有含义”的记录。"""

    return bool(is_jargon) and not str(meaning or "").strip()


def apply_jargon_list_filters(
    statement: Any,
    *,
    search: Optional[str],
    session_id: Optional[str],
    jargon_status: Optional[Literal["confirmed_jargon", "confirmed_not_jargon", "manual_jargon", "pending"]],
    is_jargon: Optional[bool],
    is_complete: Optional[bool],
    is_global: Optional[bool],
) -> Any:
    """向黑话列表查询追加筛选条件。"""

    normalized_search = search.strip() if search else ""
    if normalized_search:
        statement = statement.where(col(Jargon.content).contains(normalized_search))

    if session_id:
        session_id_candidates = parse_session_id_candidates(session_id)
        session_ids = session_id_candidates or [session_id]
        session_id_filter = build_session_id_dict_filter(session_ids)
        if session_id_filter is not None:
            statement = statement.where(session_id_filter)

    if jargon_status == "confirmed_jargon":
        statement = statement.where(effective_jargon_condition())
    elif jargon_status == "confirmed_not_jargon":
        statement = statement.where(no_jargon_condition())
    elif jargon_status == "manual_jargon":
        statement = statement.where(col(Jargon.created_by) == JargonCreatedBy.MANUAL)
    elif jargon_status == "pending":
        statement = statement.where(no_jargon_condition())
    elif is_jargon is not None:
        statement = statement.where(effective_jargon_condition() if is_jargon else no_jargon_condition())

    if is_complete is not None:
        statement = statement.where(col(Jargon.is_complete) == is_complete)

    if is_global is not None:
        statement = statement.where(col(Jargon.is_global) == is_global)

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


def scopes_overlap(jargon: Jargon, target_session_ids: Set[str], target_is_global: bool) -> bool:
    """判断黑话记录作用域是否与目标手动记录重叠。"""

    if target_is_global or jargon.is_global:
        return True
    return bool(target_session_ids.intersection(parse_session_id_dict(jargon.session_id_dict)))


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
) -> JargonExportItem:
    """将数据库黑话记录转换为导出条目。"""

    session_counts = parse_session_id_dict(jargon.session_id_dict)
    targets: Optional[List[JargonTargetInfo]] = None
    if chat_session_by_session_id is not None:
        targets = []
        for session_id, count in session_counts.items():
            chat_session = chat_session_by_session_id.get(session_id)
            if not chat_session:
                continue
            target = chat_session_to_export_target(chat_session, count)
            if target is not None:
                targets.append(target)

    return JargonExportItem(
        content=jargon.content,
        meaning=jargon.meaning or "",
        count=jargon.count,
        is_jargon=bool(jargon.is_jargon),
        is_complete=jargon.is_complete,
        is_global=jargon.is_global,
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
        "content": jargon.content,
        "meaning": jargon.meaning,
        "session_id": session_id,
        "session_ids": session_ids,
        "chat_name": chat_name,
        "chat_names": chat_names,
        "count": jargon.count,
        "is_jargon": bool(jargon.is_jargon) and not is_legacy_empty_meaning,
        "is_legacy_empty_meaning": is_legacy_empty_meaning,
        "is_complete": jargon.is_complete,
        "is_global": jargon.is_global,
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
        "content": row["content"],
        "meaning": row["meaning"],
        "session_id": session_id,
        "session_ids": session_ids,
        "chat_name": chat_name,
        "chat_names": chat_names,
        "count": row["count"],
        "is_jargon": bool(row["is_jargon"]) and not is_legacy_empty_meaning,
        "is_legacy_empty_meaning": is_legacy_empty_meaning,
        "is_complete": row["is_complete"],
        "is_global": row["is_global"],
        "created_by": normalize_jargon_created_by(row["created_by"], row["id"]),
        "created_timestamp": row["created_timestamp"] or "",
        "updated_timestamp": row["updated_timestamp"] or "",
    }


def build_jargon_compatible_select() -> Any:
    """构建黑话兼容读取列，避免历史异常值触发 ORM 类型转换错误。"""

    return select(
        col(Jargon.id).label("id"),
        col(Jargon.content).label("content"),
        col(Jargon.meaning).label("meaning"),
        col(Jargon.session_id_dict).label("session_id_dict"),
        col(Jargon.count).label("count"),
        col(Jargon.is_jargon).label("is_jargon"),
        col(Jargon.is_complete).label("is_complete"),
        col(Jargon.is_global).label("is_global"),
        col(Jargon.created_by).label("created_by"),
        cast(col(Jargon.created_timestamp), SQLString).label("created_timestamp"),
        cast(col(Jargon.updated_timestamp), SQLString).label("updated_timestamp"),
    )


def build_jargon_list_select() -> Any:
    """构建黑话列表读取列，列表页不读取原始上下文大字段。"""

    return select(
        col(Jargon.id).label("id"),
        col(Jargon.content).label("content"),
        col(Jargon.meaning).label("meaning"),
        col(Jargon.session_id_dict).label("session_id_dict"),
        col(Jargon.count).label("count"),
        col(Jargon.is_jargon).label("is_jargon"),
        col(Jargon.is_complete).label("is_complete"),
        col(Jargon.is_global).label("is_global"),
        col(Jargon.created_by).label("created_by"),
        cast(col(Jargon.created_timestamp), SQLString).label("created_timestamp"),
        cast(col(Jargon.updated_timestamp), SQLString).label("updated_timestamp"),
    )


# ==================== API 端点 ====================


@router.get("/list", response_model=JargonListResponse)
async def get_jargon_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    session_id: Optional[str] = Query(None, description="按聊天流ID筛选"),
    jargon_status: Optional[Literal["confirmed_jargon", "confirmed_not_jargon", "manual_jargon", "pending"]] = Query(
        None,
        description="按黑话判定状态筛选",
    ),
    is_jargon: Optional[bool] = Query(None, description="按是否是黑话筛选"),
    is_complete: Optional[bool] = Query(None, description="按是否推断完成筛选"),
    is_global: Optional[bool] = Query(None, description="按是否全局筛选"),
) -> JargonListResponse:
    """获取黑话列表。

    Args:
        page: 页码，从 1 开始。
        page_size: 每页数量，范围为 1-100。
        search: 搜索关键词。
        session_id: 聊天流 ID 筛选条件。
        jargon_status: 黑话判定状态筛选条件。
        is_jargon: 是否为黑话的筛选条件。
        is_complete: 是否推断完成的筛选条件。
        is_global: 是否为全局黑话的筛选条件。

    Returns:
        JargonListResponse: 分页后的黑话列表。
    """
    try:
        statement = apply_jargon_list_filters(
            build_jargon_list_select(),
            search=search,
            session_id=session_id,
            jargon_status=jargon_status,
            is_jargon=is_jargon,
            is_complete=is_complete,
            is_global=is_global,
        ).order_by(col(Jargon.count).desc(), col(Jargon.id).desc())

        count_statement = apply_jargon_list_filters(
            select(Jargon.id),
            search=search,
            session_id=session_id,
            jargon_status=jargon_status,
            is_jargon=is_jargon,
            is_complete=is_complete,
            is_global=is_global,
        )

        with get_db_session() as session:
            total = count_jargon_query(session, count_statement)
            offset = (page - 1) * page_size
            page_rows = session.execute(statement.offset(offset).limit(page_size)).mappings().all()
            page_session_ids: List[str] = []
            for row in page_rows:
                page_session_ids.extend(get_session_ids(str(row["session_id_dict"] or "{}")))
            chat_name_cache = build_session_display_name_cache(page_session_ids, session, include_message_fallback=True)
            data = [jargon_list_row_to_dict(row, session, chat_name_cache) for row in page_rows]

        return JargonListResponse(
            success=True,
            total=total,
            page=page,
            page_size=page_size,
            data=data,
        )

    except Exception as e:
        logger.error(f"获取黑话列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话列表失败: {str(e)}") from e


@router.get("/chats", response_model=ChatListResponse)
async def get_chat_list(include_empty: bool = Query(False, description="是否包含没有黑话记录的聊天流")) -> ChatListResponse:
    """获取可用于黑话新增、编辑和筛选的聊天流列表。

    Returns:
        ChatListResponse: 已知真实聊天流，以及旧黑话记录中保留的聊天流。
    """
    try:
        with get_db_session() as session:
            seen_session_ids: Set[str] = set()
            jargon_statement = select(Jargon.session_id_dict)
            if not include_empty:
                jargon_statement = jargon_statement.where(col(Jargon.is_global).is_(False))
            for session_id_dict in session.exec(jargon_statement).all():
                seen_session_ids.update(parse_session_id_dict(session_id_dict).keys())

            chat_by_session_id: Dict[str, ChatInfoResponse] = {}

            chat_session_statement = select(ChatSession)
            if not include_empty and seen_session_ids:
                chat_session_statement = chat_session_statement.where(col(ChatSession.session_id).in_(seen_session_ids))
            elif not include_empty:
                chat_session_statement = chat_session_statement.where(col(ChatSession.session_id) == "")

            chat_sessions = session.exec(chat_session_statement).all()
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
                    is_group=bool(chat_session.group_id),
                )

            # 兼容旧数据：黑话记录里可能还保留着已经不在 chat_sessions 表中的聊天流。
            for stored_session_id in seen_session_ids:
                if stored_session_id in chat_by_session_id:
                    continue
                chat_by_session_id[stored_session_id] = ChatInfoResponse(
                    session_id=stored_session_id,
                    chat_name=display_name_cache[stored_session_id],
                    platform=None,
                    is_group=False,
                )

        result = list(chat_by_session_id.values())
        result.sort(key=lambda item: (item.chat_name, item.session_id))
        return ChatListResponse(success=True, data=result)

    except Exception as e:
        logger.error(f"获取聊天列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取聊天列表失败: {str(e)}") from e


@router.get("/stats/summary", response_model=JargonStatsResponse)
async def get_jargon_stats() -> JargonStatsResponse:
    """获取黑话统计数据。

    Returns:
        JargonStatsResponse: 黑话总数、确认状态和聊天分布统计。
    """
    try:
        with get_db_session() as session:
            total = session.exec(select(func.count()).select_from(Jargon)).one()
            confirmed_jargon = session.exec(
                select(func.count()).select_from(Jargon).where(effective_jargon_condition())
            ).one()
            confirmed_not_jargon = session.exec(
                select(func.count())
                .select_from(Jargon)
                .where(no_jargon_condition())
            ).one()
            pending = session.exec(
                select(func.count()).select_from(Jargon).where(col(Jargon.is_jargon).is_(None))
            ).one()
            manual_jargon = session.exec(
                select(func.count()).select_from(Jargon).where(col(Jargon.created_by) == JargonCreatedBy.MANUAL)
            ).one()
            global_count = session.exec(
                select(func.count()).select_from(Jargon).where(col(Jargon.is_global).is_(True))
            ).one()
            complete_count = session.exec(
                select(func.count()).select_from(Jargon).where(col(Jargon.is_complete).is_(True))
            ).one()

            top_chats_counter: Dict[str, int] = {}
            for session_id_dict in session.exec(select(Jargon.session_id_dict)).all():
                for session_id in parse_session_id_dict(session_id_dict):
                    top_chats_counter[session_id] = top_chats_counter.get(session_id, 0) + 1

            top_chats_dict = dict(sorted(top_chats_counter.items(), key=lambda item: item[1], reverse=True)[:5])
            chat_count = len(top_chats_counter)

        return JargonStatsResponse(
            success=True,
            data={
                "total": total,
                "confirmed_jargon": confirmed_jargon,
                "confirmed_not_jargon": confirmed_not_jargon,
                "pending": pending,
                "manual_jargon": manual_jargon,
                "global_count": global_count,
                "complete_count": complete_count,
                "chat_count": chat_count,
                "top_chats": top_chats_dict,
            },
        )

    except Exception as e:
        logger.error(f"获取黑话统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话统计失败: {str(e)}") from e


@router.post("/export", response_model=JargonExportResponse)
async def export_jargons(request: JargonExportRequest) -> JargonExportResponse:
    """导出黑话记录，可选携带 platform/id/type 形式的聊天目标信息。"""

    try:
        statement = select(Jargon).order_by(col(Jargon.count).desc(), col(Jargon.id).desc())
        if request.ids:
            unique_ids = list(dict.fromkeys(request.ids))
            statement = statement.where(col(Jargon.id).in_(unique_ids))

        with get_db_session() as session:
            jargons = session.exec(statement).all()
            if request.ids and len(jargons) != len(set(request.ids)):
                found_ids = {jargon.id for jargon in jargons}
                missing_ids = sorted(set(request.ids) - found_ids)
                raise HTTPException(status_code=400, detail=f"部分黑话不存在: {missing_ids}")

            chat_session_by_session_id: Optional[Dict[str, ChatSession]] = None
            if request.include_chat_info:
                session_ids: List[str] = []
                for jargon in jargons:
                    session_ids.extend(parse_session_id_dict(jargon.session_id_dict).keys())
                unique_session_ids = list(dict.fromkeys(session_ids))
                chat_session_by_session_id = {}
                if unique_session_ids:
                    chat_sessions = session.exec(
                        select(ChatSession).where(col(ChatSession.session_id).in_(unique_session_ids))
                    ).all()
                    chat_session_by_session_id = {
                        chat_session.session_id: chat_session for chat_session in chat_sessions
                    }

            items = [jargon_to_export_item(jargon, chat_session_by_session_id) for jargon in jargons]

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


@router.post("/import", response_model=JargonImportResponse)
async def import_jargons(request: JargonImportRequest) -> JargonImportResponse:
    """将黑话 JSON 导入到用户选择的一个或多个真实聊天流。"""

    try:
        target_session_ids = require_existing_session_ids(request.target_session_ids)
        if not request.jargons:
            raise HTTPException(status_code=400, detail="导入文件中没有黑话")

        imported_count = 0
        skipped_count = 0
        failed_count = 0
        target_session_id_set = set(target_session_ids)

        with get_db_session() as session:
            for item in request.jargons:
                content = item.content.strip()
                if not content:
                    failed_count += 1
                    continue

                meaning = item.meaning.strip()
                same_content_jargons = session.exec(select(Jargon).where(col(Jargon.content) == content)).all()
                overlapping_jargons = [
                    jargon
                    for jargon in same_content_jargons
                    if scopes_overlap(jargon, target_session_id_set, False)
                ]

                if overlapping_jargons and request.conflict_strategy == "skip":
                    skipped_count += 1
                    continue

                if request.conflict_strategy == "overwrite":
                    for existing_jargon in overlapping_jargons:
                        session.delete(existing_jargon)
                    if overlapping_jargons:
                        session.flush()

                session_counts = {
                    session_id: max(item.count, 1)
                    for session_id in target_session_ids
                }
                jargon = Jargon(
                    content=content,
                    meaning=meaning,
                    session_id_dict=dump_session_id_dict(session_counts),
                    count=max(item.count, 0),
                    is_jargon=item.is_jargon and bool(meaning),
                    is_complete=item.is_complete,
                    is_global=False,
                    created_by=JargonCreatedBy.MANUAL,
                    updated_timestamp=datetime.now(),
                )
                session.add(jargon)
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
async def get_jargon_detail(jargon_id: int) -> JargonDetailResponse:
    """获取黑话详情。

    Args:
        jargon_id: 黑话记录 ID。

    Returns:
        JargonDetailResponse: 指定黑话记录的详细信息。
    """
    try:
        with get_db_session() as session:
            row = session.execute(
                build_jargon_compatible_select().where(col(Jargon.id) == jargon_id)
            ).mappings().first()
            if row is None:
                raise HTTPException(status_code=404, detail="黑话不存在")
            data = JargonResponse(**jargon_list_row_to_dict(row, session))

        return JargonDetailResponse(success=True, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取黑话详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话详情失败: {str(e)}") from e


@router.post("/", response_model=JargonCreateResponse)
async def create_jargon(request: JargonCreateRequest) -> JargonCreateResponse:
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

        raw_session_ids = request.session_ids if request.session_ids is not None else [request.session_id]
        session_ids = require_existing_session_ids(raw_session_ids)
        target_session_ids = set(session_ids)
        with get_db_session() as session:
            same_content_jargons = session.exec(select(Jargon).where(col(Jargon.content) == content)).all()
            existing = next(
                (
                    jargon
                    for jargon in same_content_jargons
                    if normalize_jargon_created_by(jargon.created_by, jargon.id) == JargonCreatedBy.MANUAL
                    and scopes_overlap(jargon, target_session_ids, request.is_global)
                ),
                None,
            )
            if existing is not None:
                raise HTTPException(status_code=400, detail="该范围中已存在相同内容的手动黑话")

            replaced_ai_count = 0
            for existing_jargon in same_content_jargons:
                if normalize_jargon_created_by(existing_jargon.created_by, existing_jargon.id) != JargonCreatedBy.AI:
                    continue
                if not scopes_overlap(existing_jargon, target_session_ids, request.is_global):
                    continue
                session.delete(existing_jargon)
                replaced_ai_count += 1

            jargon = Jargon(
                content=content,
                meaning=meaning,
                session_id_dict=build_session_id_dict_for_sessions(session_ids),
                count=0,
                is_jargon=bool(meaning),
                is_complete=False,
                is_global=request.is_global,
                created_by=JargonCreatedBy.MANUAL,
            )
            session.add(jargon)
            session.flush()

            logger.info(f"创建手动黑话成功: id={jargon.id}, content={content}, replaced_ai_count={replaced_ai_count}")
            data = JargonResponse(**jargon_to_dict(jargon, session))

        return JargonCreateResponse(success=True, message="创建成功", data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建黑话失败: {str(e)}") from e


@router.patch("/{jargon_id}", response_model=JargonUpdateResponse)
async def update_jargon(jargon_id: int, request: JargonUpdateRequest) -> JargonUpdateResponse:
    """增量更新黑话。

    Args:
        jargon_id: 黑话记录 ID。
        request: 只包含需要更新字段的请求数据。

    Returns:
        JargonUpdateResponse: 更新结果和更新后的黑话数据。
    """
    try:
        with get_db_session() as session:
            jargon = session.exec(select(Jargon).where(col(Jargon.id) == jargon_id)).first()
            if not jargon:
                raise HTTPException(status_code=404, detail="黑话不存在")

            if update_data := request.model_dump(exclude_unset=True):
                if "session_ids" in update_data and update_data["session_ids"] is not None:
                    session_ids = require_existing_session_ids(update_data["session_ids"])
                    jargon.session_id_dict = build_session_id_dict_for_sessions(session_ids, max(jargon.count, 1))
                elif "session_id" in update_data and update_data["session_id"] is not None:
                    session_id = require_existing_session_id(update_data["session_id"])
                    jargon.session_id_dict = build_session_id_dict_for_session(session_id, max(jargon.count, 1))
                if "content" in update_data and update_data["content"] is not None:
                    content = update_data["content"].strip()
                    if not content:
                        raise HTTPException(status_code=400, detail="黑话内容不能为空")
                    jargon.content = content
                if "meaning" in update_data:
                    jargon.meaning = update_data["meaning"] or ""
                if "is_global" in update_data and update_data["is_global"] is not None:
                    jargon.is_global = update_data["is_global"]
                if "is_jargon" in update_data:
                    jargon.is_jargon = update_data["is_jargon"]
                if "created_by" in update_data and update_data["created_by"] is not None:
                    jargon.created_by = update_data["created_by"]
                jargon.updated_timestamp = datetime.now()
                session.add(jargon)

            logger.info(f"更新黑话成功: id={jargon_id}")
            data = JargonResponse(**jargon_to_dict(jargon, session))

        return JargonUpdateResponse(success=True, message="更新成功", data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新黑话失败: {str(e)}") from e


@router.delete("/{jargon_id}", response_model=JargonDeleteResponse)
async def delete_jargon(jargon_id: int) -> JargonDeleteResponse:
    """删除黑话。

    Args:
        jargon_id: 黑话记录 ID。

    Returns:
        JargonDeleteResponse: 删除结果。
    """
    try:
        with get_db_session() as session:
            jargon = session.exec(select(Jargon).where(col(Jargon.id) == jargon_id)).first()
            if not jargon:
                raise HTTPException(status_code=404, detail="黑话不存在")

            content = jargon.content
            session.delete(jargon)

            logger.info(f"删除黑话成功: id={jargon_id}, content={content}")

        return JargonDeleteResponse(success=True, message="删除成功", deleted_count=1)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除黑话失败: {str(e)}") from e


@router.post("/batch/delete", response_model=JargonDeleteResponse)
async def batch_delete_jargons(request: BatchDeleteRequest) -> JargonDeleteResponse:
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
            result = session.exec(delete(Jargon).where(col(Jargon.id).in_(request.ids)))
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
async def batch_set_jargon_status(
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
            jargons = session.exec(select(Jargon).where(col(Jargon.id).in_(ids))).all()
            for jargon in jargons:
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
