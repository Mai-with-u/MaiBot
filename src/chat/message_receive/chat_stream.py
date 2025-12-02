import asyncio
import hashlib
import time
import copy
from typing import Dict, Optional, TYPE_CHECKING
from collections import OrderedDict
from rich.traceback import install
from maim_message import GroupInfo, UserInfo

from src.common.logger import get_logger
from src.common.database.database import db
from src.common.database.database_model import ChatStreams  # 新增导入

# 避免循环导入，使用TYPE_CHECKING进行类型提示
if TYPE_CHECKING:
    from .message import MessageRecv


install(extra_lines=3)


logger = get_logger("chat_stream")

# LRU + TTL 配置
MAX_LAST_MESSAGES_CACHE = 5000  # 最大缓存消息数
LAST_MESSAGE_TTL = 1800  # 消息缓存时间


class ChatMessageContext:
    """聊天消息上下文，存储消息的上下文信息"""

    def __init__(self, message: "MessageRecv"):
        self.message = message

    def get_template_name(self) -> Optional[str]:
        """获取模板名称"""
        if self.message.message_info.template_info and not self.message.message_info.template_info.template_default:
            return self.message.message_info.template_info.template_name  # type: ignore
        return None

    def get_last_message(self) -> "MessageRecv":
        """获取最后一条消息"""
        return self.message

    def check_types(self, types: list) -> bool:
        # sourcery skip: invert-any-all, use-any, use-next
        """检查消息类型"""
        if not self.message.message_info.format_info.accept_format:  # type: ignore
            return False
        for t in types:
            if t not in self.message.message_info.format_info.accept_format:  # type: ignore
                return False
        return True

    def get_priority_mode(self) -> str:
        """获取优先级模式"""
        return self.message.priority_mode

    def get_priority_info(self) -> Optional[dict]:
        """获取优先级信息"""
        if hasattr(self.message, "priority_info") and self.message.priority_info:
            return self.message.priority_info
        return None


class ChatStream:
    """聊天流对象，存储一个完整的聊天上下文"""

    def __init__(
        self,
        stream_id: str,
        platform: str,
        user_info: UserInfo,
        group_info: Optional[GroupInfo] = None,
        data: Optional[dict] = None,
    ):
        self.stream_id = stream_id
        self.platform = platform
        self.user_info = user_info
        self.group_info = group_info
        self.create_time = data.get("create_time", time.time()) if data else time.time()
        self.last_active_time = data.get("last_active_time", self.create_time) if data else self.create_time
        self.saved = False
        self.context: ChatMessageContext = None  # type: ignore # 用于存储该聊天的上下文信息

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "stream_id": self.stream_id,
            "platform": self.platform,
            "user_info": self.user_info.to_dict() if self.user_info else None,
            "group_info": self.group_info.to_dict() if self.group_info else None,
            "create_time": self.create_time,
            "last_active_time": self.last_active_time,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChatStream":
        """从字典创建实例"""
        user_info = UserInfo.from_dict(data.get("user_info", {})) if data.get("user_info") else None
        group_info = GroupInfo.from_dict(data.get("group_info", {})) if data.get("group_info") else None

        return cls(
            stream_id=data["stream_id"],
            platform=data["platform"],
            user_info=user_info,  # type: ignore
            group_info=group_info,
            data=data,
        )

    def update_active_time(self):
        """更新最后活跃时间"""
        self.last_active_time = time.time()
        self.saved = False

    def set_context(self, message: "MessageRecv"):
        """设置聊天消息上下文"""
        self.context = ChatMessageContext(message)


class ChatManager:
    """聊天管理器，管理所有聊天流"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.streams: Dict[str, ChatStream] = {}  # stream_id -> ChatStream
            self.last_messages: OrderedDict[str, "MessageRecv"] = OrderedDict()  # stream_id -> last_message
            self.last_message_timestamps: Dict[str, float] = {}  # stream_id -> timestamp
            try:
                db.connect(reuse_if_open=True)
                # 确保 ChatStreams 表存在
                db.create_tables([ChatStreams], safe=True)
            except Exception as e:
                logger.error(f"数据库连接或 ChatStreams 表创建失败: {e}")

            self._initialized = True
            # 在事件循环中启动初始化
            # asyncio.create_task(self._initialize())
            # # 启动自动保存任务
            # asyncio.create_task(self._auto_save_task())
            # # 启动 TTL 清理任务
            asyncio.create_task(self._cleanup_expired_messages())

    async def _initialize(self):
        """异步初始化"""
        try:
            await self.load_all_streams()
            logger.info(f"聊天管理器已启动，已加载 {len(self.streams)} 个聊天流")
        except Exception as e:
            logger.error(f"聊天管理器启动失败: {str(e)}")

    async def _auto_save_task(self):
        """定期自动保存所有聊天流"""
        while True:
            await asyncio.sleep(300)  # 每5分钟保存一次
            try:
                await self._save_all_streams()
                logger.info("聊天流自动保存完成")
            except Exception as e:
                logger.error(f"聊天流自动保存失败: {str(e)}")

    async def _cleanup_expired_messages(self):
        """定期清理过期的 last_messages"""
        while True:
            await asyncio.sleep(300)  # 每5分钟清理一次
            try:
                current_time = time.time()
                expired_keys = []
                
                for stream_id, timestamp in self.last_message_timestamps.items():
                    if current_time - timestamp > LAST_MESSAGE_TTL:
                        expired_keys.append(stream_id)
                
                for key in expired_keys:
                    self.last_messages.pop(key, None)
                    self.last_message_timestamps.pop(key, None)
                
                if expired_keys:
                    logger.info(f"清理了 {len(expired_keys)} 条过期的 last_messages")
            except Exception as e:
                logger.error(f"清理过期消息失败: {str(e)}")

    def register_message(self, message: "MessageRecv"):
        """注册消息到聊天流"""
        stream_id = self._generate_stream_id(
            message.message_info.platform,  # type: ignore
            message.message_info.user_info,
            message.message_info.group_info,
        )
        self.last_messages[stream_id] = message
        self.last_messages.move_to_end(stream_id)  
        self.last_message_timestamps[stream_id] = time.time()
        
        # LRU 
        if len(self.last_messages) > MAX_LAST_MESSAGES_CACHE:
            oldest_key, _ = self.last_messages.popitem(last=False)  
            self.last_message_timestamps.pop(oldest_key, None)
            logger.debug(f"LRU 淘汰: 移除最老的消息 {oldest_key}，当前缓存数: {len(self.last_messages)}")
        
        # logger.debug(f"注册消息到聊天流: {stream_id}")

    @staticmethod
    def _generate_stream_id(
        platform: str, user_info: Optional[UserInfo], group_info: Optional[GroupInfo] = None
    ) -> str:
        """生成聊天流唯一ID"""
        if not user_info and not group_info:
            raise ValueError("用户信息或群组信息必须提供")

        if group_info:
            # 组合关键信息
            components = [platform, str(group_info.group_id)]
        else:
            components = [platform, str(user_info.user_id), "private"]  # type: ignore

        # 使用MD5生成唯一ID
        key = "_".join(components)
        return hashlib.md5(key.encode()).hexdigest()

    def get_stream_id(self, platform: str, id: str, is_group: bool = True) -> str:
        """获取聊天流ID"""
        components = [platform, id] if is_group else [platform, id, "private"]
        key = "_".join(components)
        return hashlib.md5(key.encode()).hexdigest()

    async def get_or_create_stream(
        self, platform: str, user_info: UserInfo, group_info: Optional[GroupInfo] = None
    ) -> ChatStream:
        """获取或创建聊天流

        Args:
            platform: 平台标识
            user_info: 用户信息
            group_info: 群组信息（可选）

        Returns:
            ChatStream: 聊天流对象
        """
        # 生成stream_id
        try:
            stream_id = self._generate_stream_id(platform, user_info, group_info)

            # 检查内存中是否存在
            if stream_id in self.streams:
                stream = self.streams[stream_id]

                # 更新用户信息和群组信息
                stream.update_active_time()
                stream = copy.deepcopy(stream)  # 返回副本以避免外部修改影响缓存
                if user_info and user_info.platform and user_info.user_id:
                    stream.user_info = user_info
                if group_info:
                    stream.group_info = group_info
                from .message import MessageRecv  # 延迟导入，避免循环引用

                if stream_id in self.last_messages and isinstance(self.last_messages[stream_id], MessageRecv):
                    stream.set_context(self.last_messages[stream_id])
                else:
                    logger.error(f"聊天流 {stream_id} 不在最后消息列表中，可能是新创建的")
                return stream

            # 检查数据库中是否存在
            def _db_find_stream_sync(s_id: str):
                return ChatStreams.get_or_none(ChatStreams.stream_id == s_id)

            model_instance = await asyncio.to_thread(_db_find_stream_sync, stream_id)

            if model_instance:
                # 从 Peewee 模型转换回 ChatStream.from_dict 期望的格式
                user_info_data = {
                    "platform": model_instance.user_platform,
                    "user_id": model_instance.user_id,
                    "user_nickname": model_instance.user_nickname,
                    "user_cardname": model_instance.user_cardname or "",
                }
                group_info_data = None
                if model_instance.group_id:  # 假设 group_id 为空字符串表示没有群组信息
                    group_info_data = {
                        "platform": model_instance.group_platform,
                        "group_id": model_instance.group_id,
                        "group_name": model_instance.group_name,
                    }

                data_for_from_dict = {
                    "stream_id": model_instance.stream_id,
                    "platform": model_instance.platform,
                    "user_info": user_info_data,
                    "group_info": group_info_data,
                    "create_time": model_instance.create_time,
                    "last_active_time": model_instance.last_active_time,
                }
                stream = ChatStream.from_dict(data_for_from_dict)
                # 更新用户信息和群组信息
                stream.user_info = user_info
                if group_info:
                    stream.group_info = group_info
                stream.update_active_time()
            else:
                # 创建新的聊天流
                stream = ChatStream(
                    stream_id=stream_id,
                    platform=platform,
                    user_info=user_info,
                    group_info=group_info,
                )
        except Exception as e:
            logger.error(f"获取或创建聊天流失败: {e}", exc_info=True)
            raise e

        stream = copy.deepcopy(stream)
        from .message import MessageRecv  # 延迟导入，避免循环引用

        if stream_id in self.last_messages and isinstance(self.last_messages[stream_id], MessageRecv):
            stream.set_context(self.last_messages[stream_id])
        else:
            logger.error(f"聊天流 {stream_id} 不在最后消息列表中，可能是新创建的")
        # 保存到内存和数据库
        self.streams[stream_id] = stream
        await self._save_stream(stream)
        return stream

    def get_stream(self, stream_id: str) -> Optional[ChatStream]:
        """通过stream_id获取聊天流"""
        stream = self.streams.get(stream_id)
        if not stream:
            return None
        if stream_id in self.last_messages:
            stream.set_context(self.last_messages[stream_id])
        return stream

    def get_stream_by_info(
        self, platform: str, user_info: UserInfo, group_info: Optional[GroupInfo] = None
    ) -> Optional[ChatStream]:
        """通过信息获取聊天流"""
        stream_id = self._generate_stream_id(platform, user_info, group_info)
        return self.streams.get(stream_id)

    def get_stream_name(self, stream_id: str) -> Optional[str]:
        """根据 stream_id 获取聊天流名称"""
        stream = self.get_stream(stream_id)
        if not stream:
            return None

        if stream.group_info and stream.group_info.group_name:
            return stream.group_info.group_name
        elif stream.user_info and stream.user_info.user_nickname:
            return f"{stream.user_info.user_nickname}的私聊"
        else:
            return None

    @staticmethod
    async def _save_stream(stream: ChatStream):
        """保存聊天流到数据库"""
        if stream.saved:
            return
        stream_data_dict = stream.to_dict()

        def _db_save_stream_sync(s_data_dict: dict):
            user_info_d = s_data_dict.get("user_info")
            group_info_d = s_data_dict.get("group_info")

            fields_to_save = {
                "platform": s_data_dict["platform"],
                "create_time": s_data_dict["create_time"],
                "last_active_time": s_data_dict["last_active_time"],
                "user_platform": user_info_d["platform"] if user_info_d else "",
                "user_id": user_info_d["user_id"] if user_info_d else "",
                "user_nickname": user_info_d["user_nickname"] if user_info_d else "",
                "user_cardname": user_info_d.get("user_cardname", "") if user_info_d else None,
                "group_platform": group_info_d["platform"] if group_info_d else "",
                "group_id": group_info_d["group_id"] if group_info_d else "",
                "group_name": group_info_d["group_name"] if group_info_d else "",
            }

            ChatStreams.replace(stream_id=s_data_dict["stream_id"], **fields_to_save).execute()

        try:
            await asyncio.to_thread(_db_save_stream_sync, stream_data_dict)
            stream.saved = True
        except Exception as e:
            logger.error(f"保存聊天流 {stream.stream_id} 到数据库失败 (Peewee): {e}", exc_info=True)

    async def _save_all_streams(self):
        """保存所有聊天流"""
        for stream in self.streams.values():
            await self._save_stream(stream)

    async def load_all_streams(self):
        """从数据库加载所有聊天流"""
        logger.info("正在从数据库加载所有聊天流")

        def _db_load_all_streams_sync():
            loaded_streams_data = []
            for model_instance in ChatStreams.select():
                user_info_data = {
                    "platform": model_instance.user_platform,
                    "user_id": model_instance.user_id,
                    "user_nickname": model_instance.user_nickname,
                    "user_cardname": model_instance.user_cardname or "",
                }
                group_info_data = None
                if model_instance.group_id:
                    group_info_data = {
                        "platform": model_instance.group_platform,
                        "group_id": model_instance.group_id,
                        "group_name": model_instance.group_name,
                    }

                data_for_from_dict = {
                    "stream_id": model_instance.stream_id,
                    "platform": model_instance.platform,
                    "user_info": user_info_data,
                    "group_info": group_info_data,
                    "create_time": model_instance.create_time,
                    "last_active_time": model_instance.last_active_time,
                }
                loaded_streams_data.append(data_for_from_dict)
            return loaded_streams_data

        try:
            all_streams_data_list = await asyncio.to_thread(_db_load_all_streams_sync)
            self.streams.clear()
            for data in all_streams_data_list:
                stream = ChatStream.from_dict(data)
                stream.saved = True
                self.streams[stream.stream_id] = stream
                if stream.stream_id in self.last_messages:
                    stream.set_context(self.last_messages[stream.stream_id])
        except Exception as e:
            logger.error(f"从数据库加载所有聊天流失败 (Peewee): {e}", exc_info=True)


chat_manager = None


def get_chat_manager():
    global chat_manager
    if chat_manager is None:
        chat_manager = ChatManager()
    return chat_manager
