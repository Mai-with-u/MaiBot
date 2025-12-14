from abc import ABC, abstractmethod
from typing import List, Dict, Any
from src.common.database import db


class MessageStorage(ABC):
    """消息存储接口"""

    @abstractmethod
    async def get_messages_after(self, chat_id: str, message: Dict[str, Any]) -> List[Dict[str, Any]]:
        """获取指定消息ID之后的所有消息

        Args:
            chat_id: 聊天ID
            message: 消息

        Returns:
            List[Dict[str, Any]]: 消息列表
        """
        pass

    @abstractmethod
    async def get_messages_before(self, chat_id: str, time_point: float, limit: int = 5) -> List[Dict[str, Any]]:
        """获取指定时间点之前的消息

        Args:
            chat_id: 聊天ID
            time_point: 时间戳
            limit: 最大消息数量

        Returns:
            List[Dict[str, Any]]: 消息列表
        """
        pass

    @abstractmethod
    async def has_new_messages(self, chat_id: str, after_time: float) -> bool:
        """检查是否有新消息

        Args:
            chat_id: 聊天ID
            after_time: 时间戳

        Returns:
            bool: 是否有新消息
        """
        pass


class MongoDBMessageStorage(MessageStorage):
    """MongoDB消息存储实现"""

    async def get_messages_after(self, chat_id: str, message_time: float) -> List[Dict[str, Any]]:
        query = {"chat_id": chat_id, "time": {"$gt": message_time}}
        # print(f"storage_check_message: {message_time}")

        return list(db.messages.find(query).sort("time", 1))

    async def get_messages_before(self, chat_id: str, time_point: float, limit: int = 5) -> List[Dict[str, Any]]:
        query = {"chat_id": chat_id, "time": {"$lt": time_point}}

        messages = list(db.messages.find(query).sort("time", -1).limit(limit))

        # 将消息按时间正序排列
        messages.reverse()
        return messages

    async def has_new_messages(self, chat_id: str, after_time: float) -> bool:
        query = {"chat_id": chat_id, "time": {"$gt": after_time}}

        return db.messages.find_one(query) is not None


# # 创建一个内存消息存储实现，用于测试
# class InMemoryMessageStorage(MessageStorage):
#     """内存消息存储实现，主要用于测试"""

#     def __init__(self):
#         self.messages: Dict[str, List[Dict[str, Any]]] = {}

#     async def get_messages_after(self, chat_id: str, message_id: Optional[str] = None) -> List[Dict[str, Any]]:
#         if chat_id not in self.messages:
#             return []

#         messages = self.messages[chat_id]
#         if not message_id:
#             return messages

#         # 找到message_id的索引
#         try:
#             index = next(i for i, m in enumerate(messages) if m["message_id"] == message_id)
#             return messages[index + 1:]
#         except StopIteration:
#             return []

#     async def get_messages_before(self, chat_id: str, time_point: float, limit: int = 5) -> List[Dict[str, Any]]:
#         if chat_id not in self.messages:
#             return []

#         messages = [
#             m for m in self.messages[chat_id]
#             if m["time"] < time_point
#         ]

#         return messages[-limit:]

#     async def has_new_messages(self, chat_id: str, after_time: float) -> bool:
#         if chat_id not in self.messages:
#             return False

#         return any(m["time"] > after_time for m in self.messages[chat_id])

#     # 测试辅助方法
#     def add_message(self, chat_id: str, message: Dict[str, Any]):
#         """添加测试消息"""
#         if chat_id not in self.messages:
#             self.messages[chat_id] = []
#         self.messages[chat_id].append(message)
#         self.messages[chat_id].sort(key=lambda m: m["time"])
