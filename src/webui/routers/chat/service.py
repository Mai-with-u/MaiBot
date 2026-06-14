"""WebUI 聊天运行时服务。"""

from dataclasses import dataclass
import base64
import binascii
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, cast

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

from .serializers import serialize_message_sequence

logger = get_logger("webui.chat")

WEBUI_CHAT_GROUP_ID = "webui_local_chat"
WEBUI_CHAT_PLATFORM = "webui"
VIRTUAL_GROUP_ID_PREFIX = "webui_virtual_group_"
WEBUI_USER_ID_PREFIX = "webui_user_"

AsyncMessageSender = Callable[[Dict[str, Any]], Awaitable[None]]


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


@dataclass
class ChatSessionConnection:
    """逻辑聊天会话连接信息。"""

    session_id: str
    connection_id: str
    client_session_id: str
    user_id: str
    user_name: str
    channel_key: str
    virtual_config: Optional[VirtualIdentityConfig]
    sender: AsyncMessageSender


class ChatHistoryManager:
    """聊天历史管理器。"""

    def __init__(self, max_messages: int = 200) -> None:
        """初始化聊天历史管理器。

        Args:
            max_messages: 内存中允许处理的最大消息数。
        """
        self.max_messages = max_messages

    def _message_to_dict(self, msg: SessionMessage, group_id: Optional[str] = None) -> Dict[str, Any]:
        """将内部消息对象转换为前端可消费的字典。

        Args:
            msg: 内部统一消息对象。
            group_id: 当前会话所属的群组标识。

        Returns:
            Dict[str, Any]: 面向 WebUI 的消息字典。
        """
        del group_id
        user_info = msg.message_info.user_info
        user_id = user_info.user_id or ""
        is_bot = is_bot_self(msg.platform, user_id)

        # 将存库中的 raw_message 序列化为前端可识别的富文本消息段，
        # 避免“刚刚收到的机器人回复是富文本，刷新后变成纯文本”的体验不一致。
        segments: List[Dict[str, Any]] = []
        try:
            raw_message = getattr(msg, "raw_message", None)
            if raw_message is not None and getattr(raw_message, "components", None):
                segments = serialize_message_sequence(raw_message)
        except Exception as exc:  # 仅记录警告，退化为纯文本
            logger.debug(f"序列化历史消息段失败，退化为纯文本: {exc}")
            segments = []

        is_rich = bool(segments) and not (
            len(segments) == 1 and segments[0].get("type") == "text"
        )

        return {
            "id": msg.message_id,
            "type": "bot" if is_bot else "user",
            "content": msg.processed_plain_text or "",
            "timestamp": msg.timestamp.timestamp(),
            "sender_name": user_info.user_nickname or (global_config.bot.nickname if is_bot else "未知用户"),
            "sender_id": "bot" if is_bot else user_id,
            "is_bot": is_bot,
            "message_type": "rich" if is_rich else "text",
            "segments": segments if is_rich else None,
        }

    def _enrich_reply_segments(
        self,
        segments: List[Dict[str, Any]],
        message_index: Dict[str, SessionMessage],
        session_id: Optional[str],
    ) -> None:
        """回填历史消息中 reply 段缺失的发送者/原内容字段。

        DB 中持久化的 ReplyComponent 通常只保留了 ``target_message_id``，
        ``target_message_content`` / ``target_message_sender_*`` 字段为空。
        这里基于当前会话已加载的消息列表（必要时回查数据库）进行补全。

        Args:
            segments: 单条历史消息的消息段列表，原地修改。
            message_index: 当前会话已加载消息的 ``message_id -> SessionMessage`` 索引。
            session_id: 当前会话 ID，用于按 ID 单查时缩小范围。
        """
        for segment in segments:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            target_message_id = data.get("target_message_id")
            if not target_message_id:
                continue

            has_content = bool(str(data.get("target_message_content") or "").strip())
            has_sender = any(
                str(data.get(key) or "").strip()
                for key in (
                    "target_message_sender_id",
                    "target_message_sender_nickname",
                    "target_message_sender_cardname",
                )
            )
            if has_content and has_sender:
                continue

            target_msg = message_index.get(str(target_message_id))
            if target_msg is None:
                # 退化为按 ID 单查（仅当不在当前窗口内时才付出 DB 代价）
                try:
                    from src.services.message_service import get_message_by_id

                    target_msg = get_message_by_id(str(target_message_id), session_id or None)
                except Exception as exc:
                    logger.debug(f"按 ID 回查 reply 目标消息失败: {exc}")
                    target_msg = None
            if target_msg is None:
                continue

            user_info = target_msg.message_info.user_info
            if not has_content:
                content_text = target_msg.processed_plain_text or ""
                data["target_message_content"] = content_text
            if not has_sender:
                data["target_message_sender_id"] = user_info.user_id or ""
                data["target_message_sender_nickname"] = user_info.user_nickname or ""
                data["target_message_sender_cardname"] = (
                    getattr(user_info, "user_cardname", "") or ""
                )

    def _resolve_session_id(
        self,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """根据会话标识解析内部聊天会话 ID。

        优先按虚拟群聊解析；否则按 WebUI 私聊解析。

        Args:
            group_id: 群组标识（虚拟群聊模式）。
            user_id: 用户标识（私聊模式）。

        Returns:
            Optional[str]: 内部聊天会话 ID；当 group_id 与 user_id 均未提供时返回 ``None``。
        """
        if group_id:
            return SessionUtils.calculate_session_id(WEBUI_CHAT_PLATFORM, group_id=group_id)
        if user_id:
            return SessionUtils.calculate_session_id(WEBUI_CHAT_PLATFORM, user_id=user_id)
        return None

    def get_history(
        self,
        limit: int = 50,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取指定会话的历史消息。

        Args:
            limit: 最大返回条数。
            group_id: 群组标识（虚拟群聊模式）。
            user_id: 用户标识（私聊模式）。

        Returns:
            List[Dict[str, Any]]: 历史消息列表。
        """
        session_id = self._resolve_session_id(group_id=group_id, user_id=user_id)
        if session_id is None:
            logger.debug("获取聊天历史时缺少 group_id 与 user_id，返回空列表")
            return []
        try:
            messages = find_messages(
                session_id=session_id,
                limit=limit,
                limit_mode="latest",
                filter_command=False,
            )
            # 构建 message_id -> SessionMessage 索引，用于回填历史中 reply 段的发送者/内容
            # （DB 中通常只存了 target_message_id，target_message_content/sender_* 缺失）。
            message_index: Dict[str, SessionMessage] = {}
            for m in messages:
                mid = getattr(m, "message_id", None)
                if mid:
                    message_index[str(mid)] = m

            result: List[Dict[str, Any]] = []
            for msg in messages:
                item = self._message_to_dict(msg, group_id)
                segments = item.get("segments")
                if segments:
                    self._enrich_reply_segments(segments, message_index, session_id)
                result.append(item)
            logger.debug(
                f"从数据库加载了 {len(result)} 条聊天记录 (group_id={group_id}, user_id={user_id})"
            )
            return result
        except Exception as exc:
            logger.error(f"从数据库加载聊天记录失败: {exc}")
            return []

    def clear_history(
        self,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """清空指定会话的历史消息。

        Args:
            group_id: 群组标识（虚拟群聊模式）。
            user_id: 用户标识（私聊模式）。

        Returns:
            int: 被删除的消息数量。
        """
        session_id = self._resolve_session_id(group_id=group_id, user_id=user_id)
        if session_id is None:
            return 0
        try:
            with get_db_session() as session:
                statement = delete(Messages).where(col(Messages.session_id) == session_id)
                result = session.exec(statement)
                deleted = result.rowcount or 0
            logger.info(
                f"已清空 {deleted} 条聊天记录 (group_id={group_id}, user_id={user_id})"
            )
            return deleted
        except Exception as exc:
            logger.error(f"清空聊天记录失败: {exc}")
            return 0


class ChatConnectionManager:
    """统一聊天逻辑会话管理器。"""

    def __init__(self) -> None:
        """初始化聊天逻辑会话管理器。"""
        self.active_connections: Dict[str, ChatSessionConnection] = {}
        self.client_sessions: Dict[Tuple[str, str], str] = {}
        self.connection_sessions: Dict[str, Set[str]] = {}
        self.group_sessions: Dict[str, Set[str]] = {}
        self.user_sessions: Dict[str, Set[str]] = {}

    def _bind_channel(self, session_id: str, channel_key: str) -> None:
        """为会话绑定逻辑频道索引。

        Args:
            session_id: 内部会话 ID。
            channel_key: 频道键（``group:<gid>`` 或 ``private:<uid>``）。
        """
        channel_session_ids = self.group_sessions.setdefault(channel_key, set())
        channel_session_ids.add(session_id)

    def _unbind_channel(self, session_id: str, channel_key: str) -> None:
        """移除会话与逻辑频道的索引关系。

        Args:
            session_id: 内部会话 ID。
            channel_key: 频道键。
        """
        channel_session_ids = self.group_sessions.get(channel_key)
        if channel_session_ids is None:
            return

        channel_session_ids.discard(session_id)
        if not channel_session_ids:
            del self.group_sessions[channel_key]

    async def connect(
        self,
        session_id: str,
        connection_id: str,
        client_session_id: str,
        user_id: str,
        user_name: str,
        virtual_config: Optional[VirtualIdentityConfig],
        sender: AsyncMessageSender,
    ) -> None:
        """注册一个新的逻辑聊天会话。

        Args:
            session_id: 内部逻辑会话 ID。
            connection_id: 物理 WebSocket 连接 ID。
            client_session_id: 前端标签页使用的会话 ID。
            user_id: 规范化后的用户 ID。
            user_name: 当前展示昵称。
            virtual_config: 当前虚拟身份配置。
            sender: 发送消息到前端的异步回调。
        """
        channel_key = compute_channel_key(virtual_config, user_id)
        existing_session_id = self.client_sessions.get((connection_id, client_session_id))
        if existing_session_id is not None and existing_session_id == session_id:
            # 同一物理连接 + 前端会话重复打开（常见于 React StrictMode 双挂载或客户端去抖失败），
            # 直接复用现有会话并仅刷新可变字段，避免反复断开/重连产生噪声日志。
            existing = self.active_connections.get(existing_session_id)
            if existing is not None:
                if existing.channel_key != channel_key:
                    self._unbind_channel(existing_session_id, existing.channel_key)
                    self._bind_channel(existing_session_id, channel_key)
                    existing.channel_key = channel_key
                existing.user_id = user_id
                existing.user_name = user_name
                existing.virtual_config = virtual_config
                existing.sender = sender
                logger.debug(
                    f"WebUI 聊天会话复用: session={session_id}, connection={connection_id}, "
                    f"client_session={client_session_id}, channel={channel_key}",
                )
                return
        if existing_session_id is not None:
            self.disconnect(existing_session_id)

        session_connection = ChatSessionConnection(
            session_id=session_id,
            connection_id=connection_id,
            client_session_id=client_session_id,
            user_id=user_id,
            user_name=user_name,
            channel_key=channel_key,
            virtual_config=virtual_config,
            sender=sender,
        )

        self.active_connections[session_id] = session_connection
        self.client_sessions[(connection_id, client_session_id)] = session_id
        self.connection_sessions.setdefault(connection_id, set()).add(session_id)
        self.user_sessions.setdefault(user_id, set()).add(session_id)
        self._bind_channel(session_id, channel_key)
        logger.info(
            f"WebUI 聊天会话已连接: session={session_id}, connection={connection_id}, "
            f"client_session={client_session_id}, user={user_id}, channel={channel_key}",
        )

    def disconnect(self, session_id: str) -> None:
        """断开一个逻辑聊天会话。

        Args:
            session_id: 内部逻辑会话 ID。
        """
        session_connection = self.active_connections.pop(session_id, None)
        if session_connection is None:
            return

        self.client_sessions.pop((session_connection.connection_id, session_connection.client_session_id), None)
        self._unbind_channel(session_id, session_connection.channel_key)

        connection_session_ids = self.connection_sessions.get(session_connection.connection_id)
        if connection_session_ids is not None:
            connection_session_ids.discard(session_id)
            if not connection_session_ids:
                del self.connection_sessions[session_connection.connection_id]

        user_session_ids = self.user_sessions.get(session_connection.user_id)
        if user_session_ids is not None:
            user_session_ids.discard(session_id)
            if not user_session_ids:
                del self.user_sessions[session_connection.user_id]

        logger.info(f"WebUI 聊天会话已断开: session={session_id}")

    def disconnect_connection(self, connection_id: str) -> None:
        """断开物理连接下的全部逻辑聊天会话。

        Args:
            connection_id: 物理 WebSocket 连接 ID。
        """
        session_ids = list(self.connection_sessions.get(connection_id, set()))
        for session_id in session_ids:
            self.disconnect(session_id)

    def get_session(self, session_id: str) -> Optional[ChatSessionConnection]:
        """获取逻辑聊天会话信息。

        Args:
            session_id: 内部逻辑会话 ID。

        Returns:
            Optional[ChatSessionConnection]: 会话存在时返回对应信息。
        """
        return self.active_connections.get(session_id)

    def get_session_id(self, connection_id: str, client_session_id: str) -> Optional[str]:
        """根据连接 ID 和前端会话 ID 查询内部会话 ID。

        Args:
            connection_id: 物理 WebSocket 连接 ID。
            client_session_id: 前端标签页使用的会话 ID。

        Returns:
            Optional[str]: 找到时返回内部会话 ID。
        """
        return self.client_sessions.get((connection_id, client_session_id))

    def update_session_context(
        self,
        session_id: str,
        user_name: str,
        virtual_config: Optional[VirtualIdentityConfig],
    ) -> None:
        """更新会话上下文信息。

        Args:
            session_id: 内部逻辑会话 ID。
            user_name: 最新昵称。
            virtual_config: 最新虚拟身份配置。
        """
        session_connection = self.active_connections.get(session_id)
        if session_connection is None:
            return

        next_channel_key = compute_channel_key(virtual_config, session_connection.user_id)
        if next_channel_key != session_connection.channel_key:
            self._unbind_channel(session_id, session_connection.channel_key)
            self._bind_channel(session_id, next_channel_key)
            session_connection.channel_key = next_channel_key

        session_connection.user_name = user_name
        session_connection.virtual_config = virtual_config

    async def send_message(self, session_id: str, message: Dict[str, Any]) -> None:
        """向指定逻辑会话发送消息。

        Args:
            session_id: 内部逻辑会话 ID。
            message: 待发送的消息内容。
        """
        session_connection = self.active_connections.get(session_id)
        if session_connection is None:
            return

        try:
            await session_connection.sender(message)
        except Exception as exc:
            logger.error(f"发送聊天消息失败: session={session_id}, error={exc}")

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """向全部逻辑聊天会话广播消息。

        Args:
            message: 待广播的消息内容。
        """
        for session_id in list(self.active_connections.keys()):
            await self.send_message(session_id, message)

    async def broadcast_to_channel(self, channel_key: str, message: Dict[str, Any]) -> None:
        """向指定逻辑频道下的全部会话广播消息。

        Args:
            channel_key: 频道键（``group:<gid>`` 或 ``private:<uid>``）。
            message: 待广播的消息内容。
        """
        for session_id in list(self.group_sessions.get(channel_key, set())):
            await self.send_message(session_id, message)

    async def broadcast_to_group(
        self,
        group_id: Optional[str],
        message: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
    ) -> None:
        """向指定群组或私聊会话广播消息。

        当 ``group_id`` 非空时按群聊广播；否则按 ``user_id`` 私聊广播。

        Args:
            group_id: 群组标识；为空时使用 ``user_id``。
            message: 待广播的消息内容。
            user_id: 私聊接收方用户 ID。
        """
        if group_id:
            channel_key = f"group:{group_id}"
        elif user_id:
            channel_key = f"private:{user_id}"
        else:
            return
        await self.broadcast_to_channel(channel_key, message)


chat_history = ChatHistoryManager()
chat_manager = ChatConnectionManager()


def is_virtual_mode_enabled(virtual_config: Optional[VirtualIdentityConfig]) -> bool:
    """判断当前是否启用了虚拟身份模式。

    Args:
        virtual_config: 虚拟身份配置。

    Returns:
        bool: 已启用时返回 ``True``。
    """
    return bool(virtual_config and virtual_config.enabled)


def compute_channel_key(virtual_config: Optional[VirtualIdentityConfig], user_id: str) -> str:
    """计算当前会话的逻辑频道键。

    虚拟身份启用时使用虚拟群聊 ID，否则使用当前 WebUI 用户 ID 作为私聊频道。

    Args:
        virtual_config: 虚拟身份配置。
        user_id: 当前 WebUI 用户 ID。

    Returns:
        str: 频道键，格式为 ``group:<gid>`` 或 ``private:<uid>``。
    """
    if is_virtual_mode_enabled(virtual_config):
        assert virtual_config is not None
        return f"group:{virtual_config.group_id}"
    return f"private:{user_id}"


def normalize_webui_user_id(user_id: Optional[str]) -> str:
    """标准化 WebUI 用户 ID。

    Args:
        user_id: 原始用户 ID。

    Returns:
        str: 带统一前缀的用户 ID。
    """
    if not user_id:
        return f"{WEBUI_USER_ID_PREFIX}{uuid.uuid4().hex[:16]}"
    if user_id.startswith(WEBUI_USER_ID_PREFIX):
        return user_id
    return f"{WEBUI_USER_ID_PREFIX}{user_id}"


def get_person_by_person_id(person_id: str) -> Optional[PersonInfo]:
    """根据人物 ID 查询人物信息。

    Args:
        person_id: 人物 ID。

    Returns:
        Optional[PersonInfo]: 查到时返回人物信息。
    """
    with get_db_session(auto_commit=False) as session:
        statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
        return session.exec(statement).first()


def build_virtual_identity_config(person: PersonInfo, group_id: str, group_name: str) -> VirtualIdentityConfig:
    """根据人物信息构建虚拟身份配置。

    Args:
        person: 人物信息对象。
        group_id: 逻辑群组 ID。
        group_name: 逻辑群组名称。

    Returns:
        VirtualIdentityConfig: 虚拟身份配置对象。
    """
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
    """根据初始参数解析虚拟身份配置。

    Args:
        platform: 平台名称。
        person_id: 人物 ID。
        group_name: 群组名称。
        group_id: 群组 ID。

    Returns:
        Optional[VirtualIdentityConfig]: 解析成功时返回虚拟身份配置。
    """
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
            f"虚拟身份模式已通过参数激活: {virtual_config.user_nickname} @ "
            f"{virtual_config.platform}, group_id={virtual_group_id}",
        )
        return virtual_config
    except Exception as exc:
        logger.warning(f"通过参数配置虚拟身份失败: {exc}")
        return None


def build_session_info_message(
    session_id: str,
    user_id: str,
    user_name: str,
    virtual_config: Optional[VirtualIdentityConfig],
) -> Dict[str, Any]:
    """构建会话信息消息。

    Args:
        session_id: 内部逻辑会话 ID。
        user_id: 规范化后的用户 ID。
        user_name: 当前昵称。
        virtual_config: 虚拟身份配置。

    Returns:
        Dict[str, Any]: 会话信息消息。
    """
    # bot_qq 用于前端通过 WebUI 头像缓存接口加载机器人头像（qq_account == 0 表示未配置，不推送）。
    bot_qq_account = int(getattr(global_config.bot, "qq_account", 0) or 0)
    session_info_data: Dict[str, Any] = {
        "type": "session_info",
        "session_id": session_id,
        "user_id": user_id,
        "user_name": user_name,
        "bot_name": global_config.bot.nickname,
    }
    if bot_qq_account > 0:
        session_info_data["bot_qq"] = str(bot_qq_account)

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
    """获取当前虚拟身份对应的历史群组 ID。

    Args:
        virtual_config: 虚拟身份配置。

    Returns:
        Optional[str]: 虚拟身份启用时返回对应群组 ID；否则返回 ``None`` 表示使用私聊。
    """
    if is_virtual_mode_enabled(virtual_config):
        assert virtual_config is not None
        return virtual_config.group_id
    return None


def get_current_group_id(virtual_config: Optional[VirtualIdentityConfig]) -> Optional[str]:
    """获取当前会话的有效群组 ID。

    Args:
        virtual_config: 虚拟身份配置。

    Returns:
        Optional[str]: 虚拟身份启用时返回对应群组 ID；否则返回 ``None``（默认私聊模式）。
    """
    return get_active_history_group_id(virtual_config)


def build_welcome_message(virtual_config: Optional[VirtualIdentityConfig]) -> str:
    """构建欢迎消息。

    Args:
        virtual_config: 虚拟身份配置。

    Returns:
        str: 欢迎消息文本。
    """
    if is_virtual_mode_enabled(virtual_config):
        assert virtual_config is not None
        return (
            f"已以 {virtual_config.user_nickname} 的身份连接到「{virtual_config.group_name}」，"
            f"开始与 {global_config.bot.nickname} 对话吧！"
        )
    return f"已连接到本地聊天室，可以开始与 {global_config.bot.nickname} 对话了！"


async def send_chat_error(session_id: str, content: str) -> None:
    """向指定会话发送错误消息。

    Args:
        session_id: 内部逻辑会话 ID。
        content: 错误消息内容。
    """
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
    include_welcome: bool = True,
) -> None:
    """向新会话发送初始化状态。

    Args:
        session_id: 内部逻辑会话 ID。
        user_id: 规范化后的用户 ID。
        user_name: 当前昵称。
        virtual_config: 虚拟身份配置。
        include_welcome: 是否发送欢迎消息。
    """
    await chat_manager.send_message(
        session_id,
        build_session_info_message(
            session_id=session_id,
            user_id=user_id,
            user_name=user_name,
            virtual_config=virtual_config,
        ),
    )

    history_group_id = get_active_history_group_id(virtual_config)
    history_user_id = None if history_group_id else user_id
    history = chat_history.get_history(
        50,
        group_id=history_group_id,
        user_id=history_user_id,
    )
    await chat_manager.send_message(
        session_id,
        {
            "type": "history",
            "messages": history,
            "group_id": get_current_group_id(virtual_config),
        },
    )

    if include_welcome:
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
) -> Tuple[str, str]:
    """解析当前发送者身份。

    Args:
        current_user_name: 当前昵称。
        normalized_user_id: 规范化后的用户 ID。
        virtual_config: 虚拟身份配置。

    Returns:
        Tuple[str, str]: ``(发送者昵称, 发送者用户 ID)``。
    """
    if is_virtual_mode_enabled(virtual_config):
        assert virtual_config is not None
        return virtual_config.user_nickname or current_user_name, virtual_config.user_id or normalized_user_id
    return current_user_name, normalized_user_id


def normalize_chat_images(raw_images: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_images, list):
        return []

    images: List[Dict[str, str]] = []
    for raw_image in raw_images[:8]:
        if not isinstance(raw_image, dict):
            continue

        mime_type = str(raw_image.get("mime_type") or raw_image.get("mimeType") or "image/png").strip()
        if not mime_type.startswith("image/"):
            continue

        raw_base64 = str(raw_image.get("base64") or "").strip()
        data_url = str(raw_image.get("data_url") or raw_image.get("dataUrl") or "").strip()
        if not raw_base64 and data_url.startswith("data:image/") and "," in data_url:
            raw_base64 = data_url.split(",", maxsplit=1)[1].strip()
        if not raw_base64:
            continue

        try:
            base64.b64decode(raw_base64, validate=True)
        except (binascii.Error, ValueError):
            logger.warning("WebUI chat image ignored: invalid base64 payload")
            continue

        images.append(
            {
                "name": str(raw_image.get("name") or "").strip(),
                "mime_type": mime_type,
                "base64": raw_base64,
            }
        )

    return images


def normalize_chat_files(raw_files: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_files, list):
        return []

    files: List[Dict[str, Any]] = []
    for raw_file in raw_files[:6]:
        if not isinstance(raw_file, dict):
            continue

        name = str(raw_file.get("name") or raw_file.get("file_name") or raw_file.get("filename") or "").strip()
        raw_base64 = str(raw_file.get("base64") or "").strip()
        if not name or not raw_base64:
            continue

        try:
            base64.b64decode(raw_base64, validate=True)
        except (binascii.Error, ValueError):
            logger.warning("WebUI chat file ignored: invalid base64 payload")
            continue

        files.append(
            {
                "name": name,
                "mime_type": str(raw_file.get("mime_type") or raw_file.get("mimeType") or "application/octet-stream").strip(),
                "base64": raw_base64,
                "size": int(raw_file.get("size") or 0),
            }
        )

    return files


def normalize_chat_voices(raw_voices: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_voices, list):
        return []

    voices: List[Dict[str, str]] = []
    for raw_voice in raw_voices[:4]:
        if not isinstance(raw_voice, dict):
            continue

        mime_type = str(raw_voice.get("mime_type") or raw_voice.get("mimeType") or "audio/mpeg").strip()
        if not mime_type.startswith("audio/"):
            continue

        raw_base64 = str(raw_voice.get("base64") or "").strip()
        data_url = str(raw_voice.get("data_url") or raw_voice.get("dataUrl") or "").strip()
        if not raw_base64 and data_url.startswith("data:audio/") and "," in data_url:
            raw_base64 = data_url.split(",", maxsplit=1)[1].strip()
        if not raw_base64:
            continue

        try:
            base64.b64decode(raw_base64, validate=True)
        except (binascii.Error, ValueError):
            logger.warning("WebUI chat voice ignored: invalid base64 payload")
            continue

        voices.append(
            {
                "name": str(raw_voice.get("name") or "").strip(),
                "mime_type": mime_type,
                "base64": raw_base64,
            }
        )

    return voices


def build_display_content(
    content: str,
    images: List[Dict[str, str]],
    emojis: Optional[List[Dict[str, str]]] = None,
    files: Optional[List[Dict[str, Any]]] = None,
    voices: Optional[List[Dict[str, str]]] = None,
) -> str:
    image_count = len(images)
    emoji_count = len(emojis or [])
    voice_count = len(voices or [])
    file_parts = [f"[文件] {file.get('name', '')}".strip() for file in files or []]
    image_text = "" if image_count == 0 else ("[图片]" if image_count == 1 else f"[图片 x{image_count}]")
    emoji_text = "" if emoji_count == 0 else ("[表情]" if emoji_count == 1 else f"[表情 x{emoji_count}]")
    voice_text = "" if voice_count == 0 else ("[语音]" if voice_count == 1 else f"[语音 x{voice_count}]")
    return "\n".join(part for part in (content, image_text, emoji_text, voice_text, *file_parts) if part).strip()


def create_message_data(
    content: str,
    user_id: str,
    user_name: str,
    message_id: Optional[str] = None,
    is_at_bot: bool = True,
    virtual_config: Optional[VirtualIdentityConfig] = None,
    images: Optional[List[Dict[str, str]]] = None,
    emojis: Optional[List[Dict[str, str]]] = None,
    files: Optional[List[Dict[str, Any]]] = None,
    voices: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """构建发送给聊天核心的消息数据。

    Args:
        content: 文本内容。
        user_id: 用户 ID。
        user_name: 用户昵称。
        message_id: 消息 ID。
        is_at_bot: 是否默认艾特机器人。
        virtual_config: 虚拟身份配置。

    Returns:
        Dict[str, Any]: 聊天核心可处理的消息数据。
    """
    if message_id is None:
        message_id = str(uuid.uuid4())

    if virtual_config and virtual_config.enabled:
        platform = virtual_config.platform or WEBUI_CHAT_PLATFORM
        group_id: Optional[str] = (
            virtual_config.group_id or f"{VIRTUAL_GROUP_ID_PREFIX}{uuid.uuid4().hex[:8]}"
        )
        group_name: Optional[str] = virtual_config.group_name or "WebUI虚拟群聊"
        actual_user_id = virtual_config.user_id or user_id
        actual_user_nickname = virtual_config.user_nickname or user_name
    else:
        platform = WEBUI_CHAT_PLATFORM
        group_id = None
        group_name = None
        actual_user_id = user_id
        actual_user_nickname = user_name

    message_info: Dict[str, Any] = {
        "platform": platform,
        "message_id": message_id,
        "time": time.time(),
        "user_info": {
            "user_id": actual_user_id,
            "user_nickname": actual_user_nickname,
            "user_cardname": actual_user_nickname,
            "platform": platform,
        },
        "additional_config": {
            "at_bot": is_at_bot,
        },
    }
    if group_id is not None:
        message_info["group_info"] = {
            "group_id": group_id,
            "group_name": group_name,
            "platform": platform,
        }

    normalized_images = images or []
    normalized_emojis = emojis or []
    normalized_files = files or []
    normalized_voices = voices or []
    message_segments: List[Dict[str, Any]] = []
    if content:
        message_segments.append(
            {
                "type": "text",
                "data": content,
            }
        )
    for image in normalized_images:
        message_segments.append(
            {
                "type": "image",
                "data": image["base64"],
            }
        )
    for emoji in normalized_emojis:
        message_segments.append(
            {
                "type": "emoji",
                "data": emoji["base64"],
            }
        )
    for voice in normalized_voices:
        message_segments.append(
            {
                "type": "voice",
                "data": voice["base64"],
            }
        )
    for file in normalized_files:
        message_segments.append(
            {
                "type": "dict",
                "data": {
                    "type": "file",
                    "data": {
                        "name": file.get("name", ""),
                        "mime_type": file.get("mime_type", ""),
                        "size": file.get("size", 0),
                        "base64": file.get("base64", ""),
                    },
                },
            }
        )

    display_content = build_display_content(content, normalized_images, normalized_emojis, normalized_files, normalized_voices)

    return {
        "message_info": message_info,
        "message_segment": {
            "type": "seglist",
            "data": message_segments,
        },
        "raw_message": display_content,
        "processed_plain_text": display_content,
    }


async def handle_chat_message(
    session_id: str,
    data: Dict[str, Any],
    current_user_name: str,
    normalized_user_id: str,
    current_virtual_config: Optional[VirtualIdentityConfig],
) -> str:
    """处理用户发送的聊天消息。

    Args:
        session_id: 内部逻辑会话 ID。
        data: 前端提交的消息数据。
        current_user_name: 当前昵称。
        normalized_user_id: 规范化后的用户 ID。
        current_virtual_config: 当前虚拟身份配置。

    Returns:
        str: 处理后的最新昵称。
    """
    content = str(data.get("content", "")).strip()
    images = normalize_chat_images(data.get("images"))
    emojis = normalize_chat_images(data.get("emojis"))
    files = normalize_chat_files(data.get("files"))
    voices = normalize_chat_voices(data.get("voices"))
    if not content and not images and not emojis and not files and not voices:
        return current_user_name
    display_content = build_display_content(content, images, emojis, files, voices)

    next_user_name = str(data.get("user_name", current_user_name))
    message_id = str(uuid.uuid4())
    timestamp = time.time()
    sender_name, sender_user_id = resolve_sender_identity(
        current_user_name=next_user_name,
        normalized_user_id=normalized_user_id,
        virtual_config=current_virtual_config,
    )
    target_group_id = get_current_group_id(current_virtual_config)

    await chat_manager.broadcast_to_group(
        target_group_id,
        {
            "type": "user_message",
            "content": display_content,
            "images": images,
            "emojis": emojis,
            "files": files,
            "voices": voices,
            "group_id": target_group_id,
            "message_id": message_id,
            "timestamp": timestamp,
            "sender": {
                "name": sender_name,
                "user_id": sender_user_id,
                "is_bot": False,
            },
            "virtual_mode": is_virtual_mode_enabled(current_virtual_config),
        },
        user_id=normalized_user_id,
    )

    message_data = create_message_data(
        content=content,
        user_id=normalized_user_id,
        user_name=next_user_name,
        message_id=message_id,
        is_at_bot=True,
        virtual_config=current_virtual_config,
        images=images,
        emojis=emojis,
        files=files,
        voices=voices,
    )

    try:
        await chat_manager.broadcast_to_group(
            target_group_id,
            {"type": "typing", "is_typing": True},
            user_id=normalized_user_id,
        )
        await chat_bot.message_process(message_data)
    except Exception as exc:
        logger.error(f"处理消息时出错: {exc}")
        await send_chat_error(session_id, f"处理消息时出错: {str(exc)}")
    finally:
        await chat_manager.broadcast_to_group(
            target_group_id,
            {"type": "typing", "is_typing": False},
            user_id=normalized_user_id,
        )

    return next_user_name


async def handle_chat_ping(session_id: str) -> None:
    """处理聊天心跳。

    Args:
        session_id: 内部逻辑会话 ID。
    """
    await chat_manager.send_message(session_id, {"type": "pong", "timestamp": time.time()})


async def handle_nickname_update(session_id: str, data: Dict[str, Any], current_user_name: str) -> str:
    """处理昵称更新请求。

    Args:
        session_id: 内部逻辑会话 ID。
        data: 前端提交的数据。
        current_user_name: 当前昵称。

    Returns:
        str: 更新后的昵称。
    """
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
    virtual_data: Dict[str, Any],
) -> Optional[VirtualIdentityConfig]:
    """启用虚拟身份模式。

    Args:
        session_id: 内部逻辑会话 ID。
        session_prefix: 会话前缀，用于生成默认群组 ID。
        virtual_data: 前端提交的虚拟身份配置。

    Returns:
        Optional[VirtualIdentityConfig]: 启用成功时返回新的虚拟身份配置。
    """
    if not virtual_data.get("platform") or not virtual_data.get("person_id"):
        await send_chat_error(session_id, "虚拟身份配置缺少必要字段: platform 和 person_id")
        return None

    person_id_value = str(virtual_data.get("person_id"))
    try:
        person = get_person_by_person_id(person_id_value)
        if person is None:
            await send_chat_error(session_id, f"找不到用户: {person_id_value}")
            return None

        custom_group_id = str(virtual_data.get("group_id") or "").strip()
        if custom_group_id:
            current_group_id = custom_group_id
            if not current_group_id.startswith(VIRTUAL_GROUP_ID_PREFIX):
                current_group_id = f"{VIRTUAL_GROUP_ID_PREFIX}{current_group_id}"
        else:
            current_group_id = f"{VIRTUAL_GROUP_ID_PREFIX}{session_prefix}"

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
    except Exception as exc:
        logger.error(f"设置虚拟身份失败: {exc}")
        await send_chat_error(session_id, f"设置虚拟身份失败: {str(exc)}")
        return None


async def disable_virtual_identity(session_id: str, normalized_user_id: str) -> None:
    """关闭虚拟身份模式。

    Args:
        session_id: 内部逻辑会话 ID。
        normalized_user_id: 规范化后的 WebUI 用户 ID，用于加载私聊历史。
    """
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
            "messages": chat_history.get_history(50, user_id=normalized_user_id),
            "group_id": None,
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
    data: Dict[str, Any],
    current_virtual_config: Optional[VirtualIdentityConfig],
    normalized_user_id: str,
) -> Optional[VirtualIdentityConfig]:
    """处理虚拟身份切换请求。

    Args:
        session_id: 内部逻辑会话 ID。
        session_id_prefix: 会话前缀。
        data: 前端提交的数据。
        current_virtual_config: 当前虚拟身份配置。
        normalized_user_id: 规范化后的 WebUI 用户 ID。

    Returns:
        Optional[VirtualIdentityConfig]: 更新后的虚拟身份配置。
    """
    virtual_data = cast(Dict[str, Any], data.get("config", {}))
    if virtual_data.get("enabled"):
        next_config = await enable_virtual_identity(session_id, session_id_prefix, virtual_data)
        return next_config if next_config is not None else current_virtual_config

    await disable_virtual_identity(session_id, normalized_user_id)
    return None


async def dispatch_chat_event(
    session_id: str,
    session_id_prefix: str,
    data: Dict[str, Any],
    current_user_name: str,
    normalized_user_id: str,
    current_virtual_config: Optional[VirtualIdentityConfig],
) -> Tuple[str, Optional[VirtualIdentityConfig]]:
    """分发聊天事件到对应的处理器。

    Args:
        session_id: 内部逻辑会话 ID。
        session_id_prefix: 会话前缀。
        data: 前端提交的数据。
        current_user_name: 当前昵称。
        normalized_user_id: 规范化后的用户 ID。
        current_virtual_config: 当前虚拟身份配置。

    Returns:
        Tuple[str, Optional[VirtualIdentityConfig]]: ``(最新昵称, 最新虚拟身份配置)``。
    """
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
            normalized_user_id=normalized_user_id,
        )
        return current_user_name, next_virtual_config

    return current_user_name, current_virtual_config
