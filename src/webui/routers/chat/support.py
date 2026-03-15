"""WebUI 聊天路由支持逻辑。"""

from typing import Any, Optional, cast

import time
import uuid

from fastapi import WebSocket
from pydantic import BaseModel
from sqlmodel import col, delete, select

from src.chat.message_receive.bot import chat_bot
from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import is_bot_self
from src.common.database.database import get_db_session
from src.common.database.database_model import Messages, PersonInfo
from src.common.logger import get_logger
from src.common.message_repository import find_messages
from src.common.utils.utils_session import SessionUtils
from src.config.config import global_config
from src.webui.core import get_token_manager
from src.webui.routers.websocket.auth import verify_ws_token

logger = get_logger("webui.chat")

WEBUI_CHAT_GROUP_ID = "webui_local_chat"
WEBUI_CHAT_PLATFORM = "webui"
VIRTUAL_GROUP_ID_PREFIX = "webui_virtual_group_"
WEBUI_USER_ID_PREFIX = "webui_user_"


class VirtualIdentityConfig(BaseModel):
    """虚拟身份配置。"""

    enabled: bool = False
    platform: Optional[str] = None
    person_id: Optional[str] = None
    user_id: Optional[str] = None
    user_nickname: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None


class ChatHistoryMessage(BaseModel):
    """聊天历史消息。"""

    id: str
    type: str
    content: str
    timestamp: float
    sender_name: str
    sender_id: Optional[str] = None
    is_bot: bool = False


class ChatHistoryManager:
    """聊天历史管理器。"""

    def __init__(self, max_messages: int = 200) -> None:
        self.max_messages = max_messages

    def _message_to_dict(self, msg: SessionMessage, group_id: Optional[str] = None) -> dict[str, Any]:
        user_info = msg.message_info.user_info
        user_id = user_info.user_id or ""
        is_bot = is_bot_self(msg.platform, user_id)

        return {
            "id": msg.message_id,
            "type": "bot" if is_bot else "user",
            "content": msg.processed_plain_text or msg.display_message or "",
            "timestamp": msg.timestamp.timestamp(),
            "sender_name": user_info.user_nickname or (global_config.bot.nickname if is_bot else "未知用户"),
            "sender_id": "bot" if is_bot else user_id,
            "is_bot": is_bot,
        }

    def _resolve_session_id(self, group_id: Optional[str]) -> str:
        target_group_id = group_id or WEBUI_CHAT_GROUP_ID
        return SessionUtils.calculate_session_id(WEBUI_CHAT_PLATFORM, group_id=target_group_id)

    def get_history(self, limit: int = 50, group_id: Optional[str] = None) -> list[dict[str, Any]]:
        target_group_id = group_id or WEBUI_CHAT_GROUP_ID
        session_id = self._resolve_session_id(target_group_id)
        try:
            messages = find_messages(
                session_id=session_id,
                limit=limit,
                limit_mode="latest",
                filter_command=False,
            )
            result = [self._message_to_dict(msg, target_group_id) for msg in messages]
            logger.debug(f"从数据库加载了 {len(result)} 条聊天记录 (group_id={target_group_id})")
            return result
        except Exception as e:
            logger.error(f"从数据库加载聊天记录失败: {e}")
            return []

    def clear_history(self, group_id: Optional[str] = None) -> int:
        target_group_id = group_id or WEBUI_CHAT_GROUP_ID
        session_id = self._resolve_session_id(target_group_id)
        try:
            with get_db_session() as session:
                statement = delete(Messages).where(col(Messages.session_id) == session_id)
                result = session.exec(statement)
                deleted = result.rowcount or 0
            logger.info(f"已清空 {deleted} 条聊天记录 (group_id={target_group_id})")
            return deleted
        except Exception as e:
            logger.error(f"清空聊天记录失败: {e}")
            return 0


class ChatConnectionManager:
    """聊天连接管理器。"""

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        self.user_sessions: dict[str, str] = {}

    async def connect(self, websocket: WebSocket, session_id: str, user_id: str) -> None:
        await websocket.accept()
        self.active_connections[session_id] = websocket
        self.user_sessions[user_id] = session_id
        logger.info(f"WebUI 聊天会话已连接: session={session_id}, user={user_id}")

    def disconnect(self, session_id: str, user_id: str) -> None:
        if session_id in self.active_connections:
            del self.active_connections[session_id]
        if user_id in self.user_sessions and self.user_sessions[user_id] == session_id:
            del self.user_sessions[user_id]
        logger.info(f"WebUI 聊天会话已断开: session={session_id}")

    async def send_message(self, session_id: str, message: dict[str, Any]) -> None:
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].send_json(message)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")

    async def broadcast(self, message: dict[str, Any]) -> None:
        for session_id in list(self.active_connections.keys()):
            await self.send_message(session_id, message)


chat_history = ChatHistoryManager()
chat_manager = ChatConnectionManager()


def is_virtual_mode_enabled(virtual_config: Optional[VirtualIdentityConfig]) -> bool:
    return bool(virtual_config and virtual_config.enabled)


async def authenticate_chat_websocket(websocket: WebSocket, token: Optional[str]) -> bool:
    if token and verify_ws_token(token):
        logger.debug("聊天 WebSocket 使用临时 token 认证成功")
        return True

    if cookie_token := websocket.cookies.get("maibot_session"):
        token_manager = get_token_manager()
        if token_manager.verify_token(cookie_token):
            logger.debug("聊天 WebSocket 使用 Cookie 认证成功")
            return True

    return False


def normalize_webui_user_id(user_id: Optional[str]) -> str:
    if not user_id:
        return f"{WEBUI_USER_ID_PREFIX}{uuid.uuid4().hex[:16]}"
    if user_id.startswith(WEBUI_USER_ID_PREFIX):
        return user_id
    return f"{WEBUI_USER_ID_PREFIX}{user_id}"


def get_person_by_person_id(person_id: str) -> Optional[PersonInfo]:
    with get_db_session() as session:
        statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
        return session.exec(statement).first()


def build_virtual_identity_config(person: PersonInfo, group_id: str, group_name: str) -> VirtualIdentityConfig:
    return VirtualIdentityConfig(
        enabled=True,
        platform=person.platform,
        person_id=person.person_id,
        user_id=person.user_id,
        user_nickname=person.person_name or person.user_nickname or person.user_id,
        group_id=group_id,
        group_name=group_name,
    )


def resolve_initial_virtual_identity(
    platform: Optional[str],
    person_id: Optional[str],
    group_name: Optional[str],
    group_id: Optional[str],
) -> Optional[VirtualIdentityConfig]:
    if not (platform and person_id):
        return None

    try:
        person = get_person_by_person_id(person_id)
        if person is None:
            return None

        virtual_group_id = group_id or f"{VIRTUAL_GROUP_ID_PREFIX}{platform}_{person.user_id}"
        virtual_config = build_virtual_identity_config(
            person=person,
            group_id=virtual_group_id,
            group_name=group_name or "WebUI虚拟群聊",
        )
        logger.info(
            f"虚拟身份模式已通过 URL 参数激活: {virtual_config.user_nickname} @ {virtual_config.platform}, group_id={virtual_group_id}"
        )
        return virtual_config
    except Exception as e:
        logger.warning(f"通过 URL 参数配置虚拟身份失败: {e}")
        return None


def build_session_info_message(
    session_id: str,
    user_id: str,
    user_name: str,
    virtual_config: Optional[VirtualIdentityConfig],
) -> dict[str, Any]:
    session_info_data: dict[str, Any] = {
        "type": "session_info",
        "session_id": session_id,
        "user_id": user_id,
        "user_name": user_name,
        "bot_name": global_config.bot.nickname,
    }

    if is_virtual_mode_enabled(virtual_config):
        assert virtual_config is not None
        session_info_data["virtual_mode"] = True
        session_info_data["group_id"] = virtual_config.group_id
        session_info_data["virtual_identity"] = {
            "platform": virtual_config.platform,
            "user_id": virtual_config.user_id,
            "user_nickname": virtual_config.user_nickname,
            "group_name": virtual_config.group_name,
        }

    return session_info_data


def get_active_history_group_id(virtual_config: Optional[VirtualIdentityConfig]) -> Optional[str]:
    if is_virtual_mode_enabled(virtual_config):
        assert virtual_config is not None
        return virtual_config.group_id
    return None


def build_welcome_message(virtual_config: Optional[VirtualIdentityConfig]) -> str:
    if is_virtual_mode_enabled(virtual_config):
        assert virtual_config is not None
        return (
            f"已以 {virtual_config.user_nickname} 的身份连接到「{virtual_config.group_name}」，"
            f"开始与 {global_config.bot.nickname} 对话吧！"
        )
    return f"已连接到本地聊天室，可以开始与 {global_config.bot.nickname} 对话了！"


async def send_chat_error(session_id: str, content: str) -> None:
    await chat_manager.send_message(
        session_id,
        {
            "type": "error",
            "content": content,
            "timestamp": time.time(),
        },
    )


async def send_initial_chat_state(
    session_id: str,
    user_id: str,
    user_name: str,
    virtual_config: Optional[VirtualIdentityConfig],
) -> None:
    await chat_manager.send_message(
        session_id,
        build_session_info_message(
            session_id=session_id,
            user_id=user_id,
            user_name=user_name,
            virtual_config=virtual_config,
        ),
    )

    if history := chat_history.get_history(50, get_active_history_group_id(virtual_config)):
        await chat_manager.send_message(
            session_id,
            {
                "type": "history",
                "messages": history,
            },
        )

    await chat_manager.send_message(
        session_id,
        {
            "type": "system",
            "content": build_welcome_message(virtual_config),
            "timestamp": time.time(),
        },
    )


def resolve_sender_identity(
    current_user_name: str,
    normalized_user_id: str,
    virtual_config: Optional[VirtualIdentityConfig],
) -> tuple[str, str]:
    if is_virtual_mode_enabled(virtual_config):
        assert virtual_config is not None
        return virtual_config.user_nickname or current_user_name, virtual_config.user_id or normalized_user_id
    return current_user_name, normalized_user_id


def create_message_data(
    content: str,
    user_id: str,
    user_name: str,
    message_id: Optional[str] = None,
    is_at_bot: bool = True,
    virtual_config: Optional[VirtualIdentityConfig] = None,
) -> dict[str, Any]:
    if message_id is None:
        message_id = str(uuid.uuid4())

    if virtual_config and virtual_config.enabled:
        platform = virtual_config.platform or WEBUI_CHAT_PLATFORM
        group_id = virtual_config.group_id or f"{VIRTUAL_GROUP_ID_PREFIX}{uuid.uuid4().hex[:8]}"
        group_name = virtual_config.group_name or "WebUI虚拟群聊"
        actual_user_id = virtual_config.user_id or user_id
        actual_user_name = virtual_config.user_nickname or user_name
    else:
        platform = WEBUI_CHAT_PLATFORM
        group_id = WEBUI_CHAT_GROUP_ID
        group_name = "WebUI本地聊天室"
        actual_user_id = user_id
        actual_user_name = user_name

    return {
        "message_info": {
            "platform": platform,
            "message_id": message_id,
            "time": time.time(),
            "group_info": {
                "group_id": group_id,
                "group_name": group_name,
                "platform": platform,
            },
            "user_info": {
                "user_id": actual_user_id,
                "user_nickname": actual_user_name,
                "user_cardname": actual_user_name,
                "platform": platform,
            },
            "additional_config": {
                "at_bot": is_at_bot,
            },
        },
        "message_segment": {
            "type": "seglist",
            "data": [
                {
                    "type": "text",
                    "data": content,
                },
                {
                    "type": "mention_bot",
                    "data": "1.0",
                },
            ],
        },
        "raw_message": content,
        "processed_plain_text": content,
    }


async def handle_chat_message(
    session_id: str,
    data: dict[str, Any],
    current_user_name: str,
    normalized_user_id: str,
    current_virtual_config: Optional[VirtualIdentityConfig],
) -> str:
    content = str(data.get("content", "")).strip()
    if not content:
        return current_user_name

    next_user_name = str(data.get("user_name", current_user_name))
    message_id = str(uuid.uuid4())
    timestamp = time.time()
    sender_name, sender_user_id = resolve_sender_identity(
        current_user_name=next_user_name,
        normalized_user_id=normalized_user_id,
        virtual_config=current_virtual_config,
    )

    await chat_manager.broadcast(
        {
            "type": "user_message",
            "content": content,
            "message_id": message_id,
            "timestamp": timestamp,
            "sender": {
                "name": sender_name,
                "user_id": sender_user_id,
                "is_bot": False,
            },
            "virtual_mode": is_virtual_mode_enabled(current_virtual_config),
        }
    )

    message_data = create_message_data(
        content=content,
        user_id=normalized_user_id,
        user_name=next_user_name,
        message_id=message_id,
        is_at_bot=True,
        virtual_config=current_virtual_config,
    )

    try:
        await chat_manager.broadcast({"type": "typing", "is_typing": True})
        await chat_bot.message_process(message_data)
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        await send_chat_error(session_id, f"处理消息时出错: {str(e)}")
    finally:
        await chat_manager.broadcast({"type": "typing", "is_typing": False})

    return next_user_name


async def handle_chat_ping(session_id: str) -> None:
    await chat_manager.send_message(session_id, {"type": "pong", "timestamp": time.time()})


async def handle_nickname_update(session_id: str, data: dict[str, Any], current_user_name: str) -> str:
    new_name = str(data.get("user_name", "")).strip()
    if not new_name:
        return current_user_name

    await chat_manager.send_message(
        session_id,
        {
            "type": "nickname_updated",
            "user_name": new_name,
            "timestamp": time.time(),
        },
    )
    return new_name


async def enable_virtual_identity(
    session_id: str,
    session_prefix: str,
    virtual_data: dict[str, Any],
) -> Optional[VirtualIdentityConfig]:
    if not virtual_data.get("platform") or not virtual_data.get("person_id"):
        await send_chat_error(session_id, "虚拟身份配置缺少必要字段: platform 和 person_id")
        return None

    person_id_value = str(virtual_data.get("person_id"))
    try:
        person = get_person_by_person_id(person_id_value)
        if not person:
            await send_chat_error(session_id, f"找不到用户: {person_id_value}")
            return None

        custom_group_id = virtual_data.get("group_id")
        current_group_id = (
            f"{VIRTUAL_GROUP_ID_PREFIX}{custom_group_id}"
            if custom_group_id
            else f"{VIRTUAL_GROUP_ID_PREFIX}{session_prefix}"
        )
        current_virtual_config = build_virtual_identity_config(
            person=person,
            group_id=current_group_id,
            group_name=str(virtual_data.get("group_name", "WebUI虚拟群聊")),
        )

        await chat_manager.send_message(
            session_id,
            {
                "type": "virtual_identity_set",
                "config": {
                    "enabled": True,
                    "platform": current_virtual_config.platform,
                    "user_id": current_virtual_config.user_id,
                    "user_nickname": current_virtual_config.user_nickname,
                    "group_id": current_virtual_config.group_id,
                    "group_name": current_virtual_config.group_name,
                },
                "timestamp": time.time(),
            },
        )
        await chat_manager.send_message(
            session_id,
            {
                "type": "history",
                "messages": chat_history.get_history(50, current_virtual_config.group_id),
                "group_id": current_virtual_config.group_id,
            },
        )
        await chat_manager.send_message(
            session_id,
            {
                "type": "system",
                "content": (
                    f"已切换到虚拟身份模式：以 {current_virtual_config.user_nickname} 的身份在"
                    f"「{current_virtual_config.group_name}」与 {global_config.bot.nickname} 对话"
                ),
                "timestamp": time.time(),
            },
        )
        return current_virtual_config
    except Exception as e:
        logger.error(f"设置虚拟身份失败: {e}")
        await send_chat_error(session_id, f"设置虚拟身份失败: {str(e)}")
        return None


async def disable_virtual_identity(session_id: str) -> None:
    await chat_manager.send_message(
        session_id,
        {
            "type": "virtual_identity_set",
            "config": {"enabled": False},
            "timestamp": time.time(),
        },
    )
    await chat_manager.send_message(
        session_id,
        {
            "type": "history",
            "messages": chat_history.get_history(50, WEBUI_CHAT_GROUP_ID),
            "group_id": WEBUI_CHAT_GROUP_ID,
        },
    )
    await chat_manager.send_message(
        session_id,
        {
            "type": "system",
            "content": "已切换回 WebUI 独立用户模式",
            "timestamp": time.time(),
        },
    )


async def handle_virtual_identity_update(
    session_id: str,
    session_id_prefix: str,
    data: dict[str, Any],
    current_virtual_config: Optional[VirtualIdentityConfig],
) -> Optional[VirtualIdentityConfig]:
    virtual_data = cast(dict[str, Any], data.get("config", {}))
    if virtual_data.get("enabled"):
        next_config = await enable_virtual_identity(session_id, session_id_prefix, virtual_data)
        return next_config if next_config is not None else current_virtual_config

    await disable_virtual_identity(session_id)
    return None


async def dispatch_chat_event(
    session_id: str,
    session_id_prefix: str,
    data: dict[str, Any],
    current_user_name: str,
    normalized_user_id: str,
    current_virtual_config: Optional[VirtualIdentityConfig],
) -> tuple[str, Optional[VirtualIdentityConfig]]:
    event_type = data.get("type")
    if event_type == "message":
        next_user_name = await handle_chat_message(
            session_id=session_id,
            data=data,
            current_user_name=current_user_name,
            normalized_user_id=normalized_user_id,
            current_virtual_config=current_virtual_config,
        )
        return next_user_name, current_virtual_config

    if event_type == "ping":
        await handle_chat_ping(session_id)
        return current_user_name, current_virtual_config

    if event_type == "update_nickname":
        next_user_name = await handle_nickname_update(session_id, data, current_user_name)
        return next_user_name, current_virtual_config

    if event_type == "set_virtual_identity":
        next_virtual_config = await handle_virtual_identity_update(
            session_id=session_id,
            session_id_prefix=session_id_prefix,
            data=data,
            current_virtual_config=current_virtual_config,
        )
        return current_user_name, next_virtual_config

    return current_user_name, current_virtual_config
