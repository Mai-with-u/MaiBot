from enum import Enum, auto
from typing import Optional, Dict, Any, List, Set
from dataclasses import dataclass
from datetime import datetime
from abc import ABC, abstractmethod


class ChatState(Enum):
    """聊天状态枚举"""

    NORMAL = auto()  # 正常状态
    NEW_MESSAGE = auto()  # 有新消息
    COLD_CHAT = auto()  # 冷场状态
    ACTIVE_CHAT = auto()  # 活跃状态
    BOT_SPEAKING = auto()  # 机器人正在说话
    USER_SPEAKING = auto()  # 用户正在说话
    SILENT = auto()  # 沉默状态
    ERROR = auto()  # 错误状态


class NotificationType(Enum):
    """通知类型枚举"""

    NEW_MESSAGE = auto()  # 新消息通知
    COLD_CHAT = auto()  # 冷场通知
    ACTIVE_CHAT = auto()  # 活跃通知
    BOT_SPEAKING = auto()  # 机器人说话通知
    USER_SPEAKING = auto()  # 用户说话通知
    MESSAGE_DELETED = auto()  # 消息删除通知
    USER_JOINED = auto()  # 用户加入通知
    USER_LEFT = auto()  # 用户离开通知
    ERROR = auto()  # 错误通知


@dataclass
class ChatStateInfo:
    """聊天状态信息"""

    state: ChatState
    last_message_time: Optional[float] = None
    last_message_content: Optional[str] = None
    last_speaker: Optional[str] = None
    message_count: int = 0
    cold_duration: float = 0.0  # 冷场持续时间（秒）
    active_duration: float = 0.0  # 活跃持续时间（秒）


@dataclass
class Notification:
    """通知基类"""

    type: NotificationType
    timestamp: float
    sender: str  # 发送者标识
    target: str  # 接收者标识
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {"type": self.type.name, "timestamp": self.timestamp, "data": self.data}


@dataclass
class StateNotification(Notification):
    """持续状态通知"""

    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict["is_active"] = self.is_active
        return base_dict


class NotificationHandler(ABC):
    """通知处理器接口"""

    @abstractmethod
    async def handle_notification(self, notification: Notification):
        """处理通知"""
        pass


class NotificationManager:
    """通知管理器"""

    def __init__(self):
        # 按接收者和通知类型存储处理器
        self._handlers: Dict[str, Dict[NotificationType, List[NotificationHandler]]] = {}
        self._active_states: Set[NotificationType] = set()
        self._notification_history: List[Notification] = []

    def register_handler(self, target: str, notification_type: NotificationType, handler: NotificationHandler):
        """注册通知处理器

        Args:
            target: 接收者标识（例如："pfc"）
            notification_type: 要处理的通知类型
            handler: 处理器实例
        """
        if target not in self._handlers:
            self._handlers[target] = {}
        if notification_type not in self._handlers[target]:
            self._handlers[target][notification_type] = []
            # print(self._handlers[target][notification_type])
        self._handlers[target][notification_type].append(handler)
        # print(self._handlers[target][notification_type])

    def unregister_handler(self, target: str, notification_type: NotificationType, handler: NotificationHandler):
        """注销通知处理器

        Args:
            target: 接收者标识
            notification_type: 通知类型
            handler: 要注销的处理器实例
        """
        if target in self._handlers and notification_type in self._handlers[target]:
            handlers = self._handlers[target][notification_type]
            if handler in handlers:
                handlers.remove(handler)
                # 如果该类型的处理器列表为空，删除该类型
                if not handlers:
                    del self._handlers[target][notification_type]
                    # 如果该目标没有任何处理器，删除该目标
                    if not self._handlers[target]:
                        del self._handlers[target]

    async def send_notification(self, notification: Notification):
        """发送通知"""
        self._notification_history.append(notification)

        # 如果是状态通知，更新活跃状态
        if isinstance(notification, StateNotification):
            if notification.is_active:
                self._active_states.add(notification.type)
            else:
                self._active_states.discard(notification.type)

        # 调用目标接收者的处理器
        target = notification.target
        if target in self._handlers:
            handlers = self._handlers[target].get(notification.type, [])
            # print(handlers)
            for handler in handlers:
                # print(f"调用处理器: {handler}")
                await handler.handle_notification(notification)

    def get_active_states(self) -> Set[NotificationType]:
        """获取当前活跃的状态"""
        return self._active_states.copy()

    def is_state_active(self, state_type: NotificationType) -> bool:
        """检查特定状态是否活跃"""
        return state_type in self._active_states

    def get_notification_history(
        self, sender: Optional[str] = None, target: Optional[str] = None, limit: Optional[int] = None
    ) -> List[Notification]:
        """获取通知历史

        Args:
            sender: 过滤特定发送者的通知
            target: 过滤特定接收者的通知
            limit: 限制返回数量
        """
        history = self._notification_history

        if sender:
            history = [n for n in history if n.sender == sender]
        if target:
            history = [n for n in history if n.target == target]

        if limit is not None:
            history = history[-limit:]

        return history

    def __str__(self):
        str = ""
        for target, handlers in self._handlers.items():
            for notification_type, handler_list in handlers.items():
                str += f"NotificationManager for {target} {notification_type} {handler_list}"
        return str


# 一些常用的通知创建函数
def create_new_message_notification(sender: str, target: str, message: Dict[str, Any]) -> Notification:
    """创建新消息通知"""
    return Notification(
        type=NotificationType.NEW_MESSAGE,
        timestamp=datetime.now().timestamp(),
        sender=sender,
        target=target,
        data={
            "message_id": message.get("message_id"),
            "processed_plain_text": message.get("processed_plain_text"),
            "detailed_plain_text": message.get("detailed_plain_text"),
            "user_info": message.get("user_info"),
            "time": message.get("time"),
        },
    )


def create_cold_chat_notification(sender: str, target: str, is_cold: bool) -> StateNotification:
    """创建冷场状态通知"""
    return StateNotification(
        type=NotificationType.COLD_CHAT,
        timestamp=datetime.now().timestamp(),
        sender=sender,
        target=target,
        data={"is_cold": is_cold},
        is_active=is_cold,
    )


def create_active_chat_notification(sender: str, target: str, is_active: bool) -> StateNotification:
    """创建活跃状态通知"""
    return StateNotification(
        type=NotificationType.ACTIVE_CHAT,
        timestamp=datetime.now().timestamp(),
        sender=sender,
        target=target,
        data={"is_active": is_active},
        is_active=is_active,
    )


class ChatStateManager:
    """聊天状态管理器"""

    def __init__(self):
        self.current_state = ChatState.NORMAL
        self.state_info = ChatStateInfo(state=ChatState.NORMAL)
        self.state_history: list[ChatStateInfo] = []

    def update_state(self, new_state: ChatState, **kwargs):
        """更新聊天状态

        Args:
            new_state: 新的状态
            **kwargs: 其他状态信息
        """
        self.current_state = new_state
        self.state_info.state = new_state

        # 更新其他状态信息
        for key, value in kwargs.items():
            if hasattr(self.state_info, key):
                setattr(self.state_info, key, value)

        # 记录状态历史
        self.state_history.append(self.state_info)

    def get_current_state_info(self) -> ChatStateInfo:
        """获取当前状态信息"""
        return self.state_info

    def get_state_history(self) -> list[ChatStateInfo]:
        """获取状态历史"""
        return self.state_history

    def is_cold_chat(self, threshold: float = 60.0) -> bool:
        """判断是否处于冷场状态

        Args:
            threshold: 冷场阈值（秒）

        Returns:
            bool: 是否冷场
        """
        if not self.state_info.last_message_time:
            return True

        current_time = datetime.now().timestamp()
        return (current_time - self.state_info.last_message_time) > threshold

    def is_active_chat(self, threshold: float = 5.0) -> bool:
        """判断是否处于活跃状态

        Args:
            threshold: 活跃阈值（秒）

        Returns:
            bool: 是否活跃
        """
        if not self.state_info.last_message_time:
            return False

        current_time = datetime.now().timestamp()
        return (current_time - self.state_info.last_message_time) <= threshold
