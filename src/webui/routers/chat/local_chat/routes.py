"""本地聊天室路由 - WebUI 与麦麦直接对话。"""

from typing import Dict, Optional

from fastapi import APIRouter, Query
from sqlalchemy import case, func
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import PersonInfo
from src.common.logger import get_logger
from src.config.config import global_config
from src.webui.routers.chat.service import (
    WEBUI_CHAT_PLATFORM,
    chat_history,
    chat_manager,
    normalize_webui_user_id,
)

logger = get_logger("webui.chat")

# 路径前缀由父路由 /api/chat 统一提供
router = APIRouter(tags=["LocalChat"])


@router.get("/history")
async def get_chat_history(
    limit: int = Query(default=50, ge=1, le=200),
    user_id: Optional[str] = Query(default=None),
    group_id: Optional[str] = Query(default=None),
    platform: Optional[str] = Query(default=None),
) -> Dict[str, object]:
    """获取聊天历史记录。

    优先按 ``group_id`` 加载群聊历史，此时必须传入对应 ``platform``；
    未提供 ``group_id`` 时使用规范化后的 ``user_id`` 加载 WebUI 私聊历史。
    """
    if group_id:
        history = chat_history.get_history(limit, group_id=group_id, platform=platform)
    else:
        normalized_user_id = normalize_webui_user_id(user_id)
        history = chat_history.get_history(limit, user_id=normalized_user_id, platform=platform)
    return {"success": True, "messages": history, "total": len(history)}


@router.get("/platforms")
async def get_available_platforms() -> Dict[str, object]:
    """获取可用平台列表。"""
    try:
        with get_db_session() as session:
            statement = (
                select(PersonInfo.platform, func.count().label("count"))
                .group_by(PersonInfo.platform)
                .order_by(func.count().desc())
            )
            platforms = session.exec(statement).all()

        result = [{"platform": platform, "count": count} for platform, count in platforms if platform]
        return {"success": True, "platforms": result}
    except Exception as e:
        logger.error(f"获取平台列表失败: {e}")
        return {"success": False, "error": str(e), "platforms": []}


@router.get("/persons")
async def get_persons_by_platform(
    platform: str = Query(..., description="平台名称"),
    search: Optional[str] = Query(default=None, description="搜索关键词"),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, object]:
    """获取指定平台的用户列表。"""
    try:
        statement = select(PersonInfo).where(col(PersonInfo.platform) == platform)
        if search:
            statement = statement.where(
                (col(PersonInfo.person_name).contains(search))
                | (col(PersonInfo.user_nickname).contains(search))
                | (col(PersonInfo.user_id).contains(search))
            )

        statement = statement.order_by(
            case((col(PersonInfo.last_known_time).is_(None), 1), else_=0),
            col(PersonInfo.last_known_time).desc(),
        ).limit(limit)

        with get_db_session() as session:
            persons = session.exec(statement).all()
            result = [
                {
                    "person_id": person.person_id,
                    "user_id": person.user_id,
                    "person_name": person.person_name,
                    "nickname": person.user_nickname,
                    "is_known": person.is_known,
                    "platform": person.platform,
                    "display_name": person.person_name or person.user_nickname or person.user_id,
                }
                for person in persons
            ]
        return {"success": True, "persons": result, "total": len(result)}
    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        return {"success": False, "error": str(e), "persons": []}


@router.delete("/history")
async def clear_chat_history(
    user_id: Optional[str] = Query(default=None),
    group_id: Optional[str] = Query(default=None),
    platform: Optional[str] = Query(default=None),
) -> Dict[str, object]:
    """清空聊天历史记录。

    优先按 ``group_id`` 清理群聊历史，此时必须传入对应 ``platform``；
    未提供 ``group_id`` 时使用规范化后的 ``user_id`` 清理 WebUI 私聊历史。
    """
    if group_id:
        deleted = chat_history.clear_history(group_id=group_id, platform=platform)
    else:
        normalized_user_id = normalize_webui_user_id(user_id)
        deleted = chat_history.clear_history(user_id=normalized_user_id, platform=platform)
    return {"success": True, "message": f"已清空 {deleted} 条聊天记录"}


@router.get("/info")
async def get_chat_info() -> Dict[str, object]:
    """获取聊天室信息。"""
    return {
        "bot_name": global_config.bot.nickname,
        "platform": WEBUI_CHAT_PLATFORM,
        "active_sessions": len(chat_manager.active_connections),
    }
