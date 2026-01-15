from pydantic import BaseModel
from typing import Optional


class VirtualIdentityConfig(BaseModel):
    """虚拟身份配置"""

    enabled: bool = False
    platform: Optional[str] = None
    person_id: Optional[str] = None
    user_id: Optional[str] = None
    user_nickname: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None


class ChatHistoryMessage(BaseModel):
    """聊天历史消息"""

    id: str
    type: str  # 'user' | 'bot' | 'system'
    content: str
    timestamp: float
    sender_name: str
    sender_id: Optional[str] = None
    is_bot: bool = False
