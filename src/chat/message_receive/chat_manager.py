from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

import asyncio

from rich.traceback import install
from sqlmodel import select

from src.common.data_models.chat_session_data_model import MaiChatSession
from src.common.database.database import get_db_session
from src.common.database.database_model import ChatSession
from src.common.logger import get_logger
from src.common.utils.utils_session import SessionUtils
from src.platform_io.route_key_factory import RouteKeyFactory

if TYPE_CHECKING:
    from .message import SessionMessage

install(extra_lines=3)

logger = get_logger("chat_manager")


class SessionContext:
    """会话上下文"""

    def __init__(self, message: "SessionMessage"):
        self.message = message
        self.template_name: Optional[str] = None

    def update_template(self, template_name: str):
        """更新当前使用的回复模板"""
        self.template_name = template_name


class BotChatSession(MaiChatSession):
    def __init__(
        self,
        session_id: str,
        platform: str,
        user_id: Optional[str] = None,
        user_nickname: Optional[str] = None,
        user_cardname: Optional[str] = None,
        group_id: Optional[str] = None,
        group_name: Optional[str] = None,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
        created_timestamp: Optional[datetime] = None,
        last_active_timestamp: Optional[datetime] = None,
    ):
        self.context: Optional[SessionContext] = None
        self.accept_format: List[str] = []

        super().__init__(
            session_id=session_id,
            platform=platform,
            user_id=user_id,
            user_nickname=user_nickname,
            user_cardname=user_cardname,
            group_id=group_id,
            group_name=group_name,
            account_id=account_id,
            scope=scope,
            created_timestamp=created_timestamp,
            last_active_timestamp=last_active_timestamp,
        )

    def check_types(self, types: List[str]) -> bool:
        """检查消息是否符合可接受类型列表"""
        return all(t in self.accept_format for t in types)

    def update_active_time(self):
        """更新最后活跃时间"""
        self.last_active_timestamp = datetime.now()

    def set_context(self, message: "SessionMessage"):
        """设置会话上下文"""
        self.context = SessionContext(message=message)


class ChatManager:
    """聊天管理器，负责管理所有聊天会话"""

    def __init__(self) -> None:
        self.sessions: Dict[str, BotChatSession] = {}  # session_id -> BotChatSession
        self.last_messages: Dict[str, "SessionMessage"] = {}  # session_id -> SessionMessage

    async def initialize(self):
        """初始化聊天管理器"""
        try:
            await self.load_all_sessions_from_db()
            logger.debug(f"已加载 {len(self.sessions)} 个会话记录到内存中")
        except Exception as e:
            logger.error(f"初始化聊天管理器出现错误: {e}")

    async def get_or_create_session(
        self,
        platform: str,
        user_id: str,
        group_id: Optional[str] = None,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> BotChatSession:
        """获取会话，如果不存在则创建一个新会话；一个封装方法。

        Args:
            platform: 平台
            user_id: 用户ID
            group_id: 群ID（如果是群聊）
            account_id: 平台账号 ID
            scope: 路由作用域
        Returns:
            return (BotChatSession) 会话对象
        Raises:
            Exception: 获取或创建会话时发生错误
        """
        session_id = SessionUtils.calculate_session_id(
            platform,
            user_id=user_id,
            group_id=group_id,
            account_id=account_id,
            scope=scope,
        )
        if session := self.get_session_by_session_id(session_id):
            route_metadata_changed = self._apply_route_metadata(session, account_id=account_id, scope=scope)
            session.update_active_time()
            identity_changed = False
            if session_id in self.last_messages:
                identity_changed = self._update_session_identity(session, self.last_messages[session_id])
            if route_metadata_changed or identity_changed:
                self._save_session(session)
            return session

        # 内存没有就找db
        try:
            with get_db_session() as db_session:
                statement = select(ChatSession).filter_by(session_id=session_id).limit(1)
                if result := db_session.exec(statement).first():
                    session = BotChatSession.from_db_instance(result)
                    route_metadata_changed = self._apply_route_metadata(session, account_id=account_id, scope=scope)
                    identity_changed = False
                    if session.session_id in self.last_messages:
                        session.set_context(self.last_messages[session.session_id])
                        identity_changed = self._update_session_identity(session, self.last_messages[session.session_id])
                    if route_metadata_changed or identity_changed:
                        result.account_id = session.account_id
                        result.scope = session.scope
                        result.user_id = session.user_id
                        result.user_nickname = session.user_nickname
                        result.user_cardname = session.user_cardname
                        result.group_id = session.group_id
                        result.group_name = session.group_name
                        db_session.add(result)
                    self.sessions[session.session_id] = session
                    return session
        except Exception as e:
            logger.error(f"从数据库获取会话时发生错误: {e}")
            raise e

        # 都没有就创建新的
        new_session = BotChatSession(
            session_id=session_id,
            platform=platform,
            user_id=user_id,
            group_id=group_id,
            account_id=account_id,
            scope=scope,
        )
        self.sessions[new_session.session_id] = new_session
        if new_session.session_id in self.last_messages:
            new_session.set_context(self.last_messages[new_session.session_id])
            self._update_session_identity(new_session, self.last_messages[new_session.session_id])
        self._save_session(new_session)
        return new_session

    def register_message(self, message: "SessionMessage"):
        platform = message.platform
        if not platform:
            raise ValueError("消息缺少平台信息")
        user_id = message.message_info.user_info.user_id
        group_id = message.message_info.group_info.group_id if message.message_info.group_info else None
        account_id = None
        scope = None
        additional_config = message.message_info.additional_config
        if isinstance(additional_config, dict):
            account_id, scope = RouteKeyFactory.extract_components(additional_config)
        session_id = SessionUtils.calculate_session_id(
            platform,
            user_id=user_id,
            group_id=group_id,
            account_id=account_id,
            scope=scope,
        )
        message.session_id = session_id  # 确保消息的session_id正确设置
        self.last_messages[session_id] = message
        session = self.sessions.get(session_id)
        if session is not None and self._update_session_identity(session, message):
            self._save_session(session)

    @staticmethod
    def _normalize_identity_text(value: Optional[str]) -> Optional[str]:
        normalized_value = str(value or "").strip()
        return normalized_value or None

    def _update_session_identity(self, session: BotChatSession, message: "SessionMessage") -> bool:
        """用真实入站消息补齐聊天流展示身份，群聊不保存最近发言人的用户信息。"""

        changed = False
        group_info = message.message_info.group_info
        user_info = message.message_info.user_info
        if group_info is not None:
            group_name = self._normalize_identity_text(group_info.group_name)
            if group_name and session.group_name != group_name:
                session.group_name = group_name
                changed = True
            if session.user_id is not None:
                session.user_id = None
                changed = True
            if session.user_nickname is not None:
                session.user_nickname = None
                changed = True
            if session.user_cardname is not None:
                session.user_cardname = None
                changed = True
            return changed

        user_nickname = self._normalize_identity_text(user_info.user_nickname)
        user_cardname = self._normalize_identity_text(user_info.user_cardname)
        if user_nickname and session.user_nickname != user_nickname:
            session.user_nickname = user_nickname
            changed = True
        if user_cardname != session.user_cardname:
            session.user_cardname = user_cardname
            changed = True
        return changed

    async def load_all_sessions_from_db(self):
        """从数据库加载全部会话记录到内存中"""
        self.sessions.clear()
        try:
            await asyncio.to_thread(self._load_sessions_from_db)
        except Exception as e:
            logger.error(f"从数据库加载会话记录时发生错误: {e}")
            self.sessions.clear()
            raise e

    async def regularly_save_sessions(self, interval_seconds: int = 300):
        """定期将会话记录保存到数据库中

        Args:
            interval_seconds: 保存间隔时间，单位为秒，默认为300秒（5分钟）
        """
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await asyncio.to_thread(self.save_all_sessions)
            except Exception as e:
                logger.error(f"定期保存会话记录时发生错误: {e}")

    def save_all_sessions(self):
        """将内存中的全部会话记录保存到数据库"""
        try:
            for session in self.sessions.values():
                self._save_session(session)
            logger.info(f"共 {len(self.sessions)} 个会话已经保存到数据库中")
        except Exception as e:
            logger.error(f"保存会话记录到数据库时发生错误: {e}")
            raise e

    def get_session_name(self, session_id: str) -> Optional[str]:
        """根据会话ID获取会话名称

        Args:
            session_id: 会话ID
        Returns:
            Optional[str]: 会话名称，如果无法获取则返回None
        """
        session = self.sessions.get(session_id)
        if not session:
            return None
        if session.is_group_session:
            if session.group_name:
                return session.group_name
            if session.context and session.context.message and session.context.message.message_info.group_info:
                return session.context.message.message_info.group_info.group_name
        elif session.user_nickname:
            return f"{session.user_nickname}的私聊"
        elif session.context and session.context.message and session.context.message.message_info.user_info:
            nickname = session.context.message.message_info.user_info.user_nickname
            return f"{nickname}的私聊"
        return None

    def get_named_session_options(
        self,
        excluded_platforms: Optional[Set[str]] = None,
    ) -> Dict[str, str]:
        """返回可供交互选择的聊天名称与真实会话 ID 映射。

        同名聊天会附带平台和目标 ID；若平台目标仍相同，则继续附带会话 ID，
        确保每个展示名称只会指向一个真实聊天流。
        """

        normalized_excluded_platforms = {
            platform.strip()
            for platform in (excluded_platforms or set())
            if platform.strip()
        }
        named_sessions: List[Tuple[str, BotChatSession]] = []
        for session in self.sessions.values():
            if session.platform in normalized_excluded_platforms:
                continue
            session_name = self.get_session_name(session.session_id)
            if session_name:
                named_sessions.append((session_name, session))

        name_counts = Counter(session_name for session_name, _session in named_sessions)
        options: Dict[str, str] = {}
        for session_name, session in sorted(
            named_sessions,
            key=lambda item: (item[0], item[1].platform, item[1].session_id),
        ):
            label = session_name
            if name_counts[session_name] > 1:
                target_id = session.group_id if session.is_group_session else session.user_id
                label = f"{session_name} [{session.platform}:{target_id}]"
            if label in options:
                label = f"{label} [{session.session_id}]"
            options[label] = session.session_id

        return options

    def get_session_by_info(
        self,
        platform: str,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> Optional[BotChatSession]:
        """根据平台、用户ID和群ID获取对应的会话

        Args:
            platform: 平台
            user_id: 用户ID
            group_id: 群ID（如果是群聊）
            account_id: 平台账号 ID
            scope: 路由作用域
        Returns:
            return (Optional[BotChatSession]): 会话对象，如果不存在则返回None
        """
        session_id = SessionUtils.calculate_session_id(
            platform,
            user_id=user_id,
            group_id=group_id,
            account_id=account_id,
            scope=scope,
        )
        return self.get_session_by_session_id(session_id)

    def resolve_sessions_by_target(
        self,
        *,
        platform: str,
        target_id: str,
        chat_type: str,
    ) -> List[BotChatSession]:
        """按平台、目标 ID 与聊天类型解析已存在的真实聊天流。

        业务模块不应自行重新计算 session_id，因为真实会话 ID 可能包含
        account_id、scope 等路由元数据。该接口只返回已经注册或已入库的会话。
        """

        normalized_platform = str(platform or "").strip()
        normalized_target_id = str(target_id or "").strip()
        normalized_chat_type = str(chat_type or "").strip()
        if not normalized_platform or not normalized_target_id:
            return []

        if normalized_chat_type == "group":
            target_attr = "group_id"
        elif normalized_chat_type == "private":
            target_attr = "user_id"
        else:
            return []

        matched_sessions: Dict[str, BotChatSession] = {}
        for session in self.sessions.values():
            if self._session_matches_target(
                session,
                platform=normalized_platform,
                target_attr=target_attr,
                target_id=normalized_target_id,
            ):
                matched_sessions[session.session_id] = session

        try:
            with get_db_session() as db_session:
                statement = select(ChatSession).filter_by(platform=normalized_platform)
                for db_instance in db_session.exec(statement).all():
                    if str(getattr(db_instance, target_attr) or "").strip() != normalized_target_id:
                        continue
                    if db_instance.session_id in matched_sessions:
                        continue
                    session = BotChatSession.from_db_instance(db_instance)
                    self.sessions[session.session_id] = session
                    if session.session_id in self.last_messages:
                        session.set_context(self.last_messages[session.session_id])
                    matched_sessions[session.session_id] = session
        except Exception as e:
            logger.error(
                f"按目标解析聊天流失败: platform={normalized_platform} "
                f"target_id={normalized_target_id} chat_type={normalized_chat_type} error={e}"
            )

        return list(matched_sessions.values())

    def resolve_session_ids_by_target(
        self,
        *,
        platform: str,
        target_id: str,
        chat_type: str,
    ) -> set[str]:
        """按平台、目标 ID 与聊天类型解析已存在的真实聊天流 ID。"""

        return {
            session.session_id
            for session in self.resolve_sessions_by_target(
                platform=platform,
                target_id=target_id,
                chat_type=chat_type,
            )
        }

    @staticmethod
    def _session_matches_target(
        session: BotChatSession,
        *,
        platform: str,
        target_attr: str,
        target_id: str,
    ) -> bool:
        return (
            str(session.platform or "").strip() == platform
            and str(getattr(session, target_attr) or "").strip() == target_id
        )

    @staticmethod
    def _normalize_route_value(value: Optional[str]) -> Optional[str]:
        normalized_value = str(value or "").strip()
        return normalized_value or None

    @classmethod
    def _apply_route_metadata(
        cls,
        session: BotChatSession,
        *,
        account_id: Optional[str],
        scope: Optional[str],
    ) -> bool:
        changed = False
        normalized_account_id = cls._normalize_route_value(account_id)
        normalized_scope = cls._normalize_route_value(scope)

        if normalized_account_id and not cls._normalize_route_value(session.account_id):
            session.account_id = normalized_account_id
            changed = True
        if normalized_scope and not cls._normalize_route_value(session.scope):
            session.scope = normalized_scope
            changed = True
        return changed

    def get_session_by_session_id(self, session_id: str) -> Optional[BotChatSession]:
        """根据会话ID获取对应的会话

        Args:
            session_id: 会话ID
        Returns:
            Optional[BotChatSession]: 会话对象，如果不存在则返回None
        """
        session = self.sessions.get(session_id)
        if session and session_id in self.last_messages:
            session.set_context(self.last_messages[session_id])
        return session

    def get_existing_session_by_session_id(self, session_id: str) -> Optional[BotChatSession]:
        """根据会话 ID 获取已存在的真实会话，内存未命中时从数据库加载。"""

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return None

        if session := self.get_session_by_session_id(normalized_session_id):
            return session

        try:
            with get_db_session() as db_session:
                statement = select(ChatSession).filter_by(session_id=normalized_session_id).limit(1)
                db_instance = db_session.exec(statement).first()
                if db_instance is None:
                    return None
                session = BotChatSession.from_db_instance(db_instance)
                self.sessions[session.session_id] = session
                if session.session_id in self.last_messages:
                    session.set_context(self.last_messages[session.session_id])
                return session
        except Exception as e:
            logger.error(f"从数据库获取已有会话失败: session_id={normalized_session_id} error={e}")
            return None

    def _load_sessions_from_db(self):
        """从数据库加载单个会话记录"""
        with get_db_session() as session:
            statements = select(ChatSession)
            for model_instance in session.exec(statements).all():
                bot_chat_session = BotChatSession.from_db_instance(model_instance)
                self.sessions[bot_chat_session.session_id] = bot_chat_session
                if bot_chat_session.session_id in self.last_messages:
                    bot_chat_session.set_context(self.last_messages[bot_chat_session.session_id])

    def _save_session(self, session: BotChatSession):
        """将会话记录保存到数据库"""
        with get_db_session() as db_session:
            db_instance = session.to_db_instance()
            statement = select(ChatSession).filter_by(session_id=db_instance.session_id).limit(1)
            if result := db_session.exec(statement).first():
                result.created_timestamp = db_instance.created_timestamp
                result.last_active_timestamp = db_instance.last_active_timestamp
                result.user_id = db_instance.user_id
                result.user_nickname = db_instance.user_nickname
                result.user_cardname = db_instance.user_cardname
                result.group_id = db_instance.group_id
                result.group_name = db_instance.group_name
                result.account_id = db_instance.account_id
                result.scope = db_instance.scope
                db_session.add(result)
            else:
                db_session.add(db_instance)


chat_manager = ChatManager()
