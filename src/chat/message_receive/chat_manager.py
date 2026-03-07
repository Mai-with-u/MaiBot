from datetime import datetime
from rich.traceback import install
from sqlmodel import select
from typing import Optional, TYPE_CHECKING, List, Dict

import asyncio

from src.common.logger import get_logger
from src.common.data_models.chat_session_data_model import MaiChatSession
from src.common.database.database_model import ChatSession
from src.common.database.database import get_db_session
from src.common.utils.utils_session import SessionUtils

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
        group_id: Optional[str] = None,
        created_timestamp: Optional[datetime] = None,
        last_active_timestamp: Optional[datetime] = None,
    ):
        self.context: Optional[SessionContext] = None
        self.accept_format: List[str] = []

        super().__init__(
            session_id=session_id,
            platform=platform,
            user_id=user_id,
            group_id=group_id,
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
            logger.info(f"已加载 {len(self.sessions)} 个会话记录到内存中")
        except Exception as e:
            logger.error(f"初始化聊天管理器出现错误: {e}")

    async def get_or_create_session(
        self, platform: str, user_id: str, group_id: Optional[str] = None
    ) -> BotChatSession:
        """获取会话，如果不存在则创建一个新会话；一个封装方法。

        Args:
            platform: 平台
            user_id: 用户ID
            group_id: 群ID（如果是群聊）
        Returns:
            return (BotChatSession) 会话对象
        Raises:
            Exception: 获取或创建会话时发生错误
        """
        session_id = SessionUtils.calculate_session_id(platform, user_id=user_id, group_id=group_id)
        if session := self.get_session_by_session_id(session_id):
            session.update_active_time()
            return session

        # 内存没有就找db
        try:
            with get_db_session() as db_session:
                statement = select(ChatSession).filter_by(session_id=session_id).limit(1)
                if result := db_session.exec(statement).first():
                    session = BotChatSession.from_db_instance(result)
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
        )
        self.sessions[new_session.session_id] = new_session
        if new_session.session_id in self.last_messages:
            new_session.set_context(self.last_messages[new_session.session_id])
        self._save_session(new_session)
        return new_session

    def register_message(self, message: "SessionMessage"):
        platform = message.platform
        if not platform:
            raise ValueError("消息缺少平台信息")
        user_id = message.message_info.user_info.user_id
        group_id = message.message_info.group_info.group_id if message.message_info.group_info else None
        session_id = SessionUtils.calculate_session_id(platform, user_id=user_id, group_id=group_id)
        message.session_id = session_id  # 确保消息的session_id正确设置
        self.last_messages[session_id] = message

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
            if session.context and session.context.message and session.context.message.message_info.group_info:
                return session.context.message.message_info.group_info.group_name
        elif session.context and session.context.message and session.context.message.message_info.user_info:
            nickname = session.context.message.message_info.user_info.user_nickname
            return f"{nickname}的私聊"
        return None

    def get_session_by_info(
        self, platform: str, user_id: Optional[str] = None, group_id: Optional[str] = None
    ) -> Optional[BotChatSession]:
        """根据平台、用户ID和群ID获取对应的会话

        Args:
            platform: 平台
            user_id: 用户ID
            group_id: 群ID（如果是群聊）
        Returns:
            return (Optional[BotChatSession]): 会话对象，如果不存在则返回None
        """
        session_id = SessionUtils.calculate_session_id(platform, user_id=user_id, group_id=group_id)
        return self.get_session_by_session_id(session_id)

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
                db_session.add(result)
            else:
                db_session.add(db_instance)


chat_manager = ChatManager()
