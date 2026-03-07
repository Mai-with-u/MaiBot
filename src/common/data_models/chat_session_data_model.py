from datetime import datetime
from typing import Optional

from src.common.database.database_model import ChatSession

from . import BaseDatabaseDataModel


class MaiChatSession(BaseDatabaseDataModel[ChatSession]):
    def __init__(
        self,
        session_id: str,
        platform: str,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        created_timestamp: Optional[datetime] = None,
        last_active_timestamp: Optional[datetime] = None,
    ):
        self.session_id: str = session_id
        self.platform: str = platform
        self.user_id: Optional[str] = user_id
        self.group_id: Optional[str] = group_id
        self.created_timestamp: datetime = created_timestamp or datetime.now()
        """会话创建时间，默认为当前时间"""
        self.last_active_timestamp: Optional[datetime] = last_active_timestamp

        # 验证字段
        assert self.platform, "Platform must be provided"
        assert self.user_id or self.group_id, "UserID 或 GroupID 必须提供其一"

        # 其他字段初始化
        self.is_group_session = bool(self.group_id)

    @classmethod
    def from_db_instance(cls, db_record: ChatSession):
        return cls(
            session_id=db_record.session_id,
            platform=db_record.platform,
            user_id=db_record.user_id,
            group_id=db_record.group_id,
            created_timestamp=db_record.created_timestamp,
            last_active_timestamp=db_record.last_active_timestamp,
        )

    def to_db_instance(self) -> ChatSession:
        return ChatSession(
            session_id=self.session_id,
            platform=self.platform,
            user_id=self.user_id,
            group_id=self.group_id,
            created_timestamp=self.created_timestamp,
            last_active_timestamp=self.last_active_timestamp,
        )
