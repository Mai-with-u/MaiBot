import time
import asyncio
import traceback
from typing import Optional, Dict, Any, List
from src.common.logger import get_module_logger
from maim_message import UserInfo
from ...config.config import global_config
from .chat_states import NotificationManager, create_new_message_notification, create_cold_chat_notification
from .message_storage import MongoDBMessageStorage
from rich.traceback import install

install(extra_lines=3)

logger = get_module_logger("chat_observer")


class ChatObserver:
    """聊天状态观察器"""

    # 类级别的实例管理
    _instances: Dict[str, "ChatObserver"] = {}

    @classmethod
    def get_instance(cls, stream_id: str, private_name: str) -> "ChatObserver":
        """获取或创建观察器实例

        Args:
            stream_id: 聊天流ID
            private_name: 私聊名称

        Returns:
            ChatObserver: 观察器实例
        """
        if stream_id not in cls._instances:
            cls._instances[stream_id] = cls(stream_id, private_name)
        return cls._instances[stream_id]

    def __init__(self, stream_id: str, private_name: str):
        """初始化观察器

        Args:
            stream_id: 聊天流ID
        """
        self.last_check_time = None
        self.last_bot_speak_time = None
        self.last_user_speak_time = None
        if stream_id in self._instances:
            raise RuntimeError(f"ChatObserver for {stream_id} already exists. Use get_instance() instead.")

        self.stream_id = stream_id
        self.private_name = private_name
        self.message_storage = MongoDBMessageStorage()

        # self.last_user_speak_time: Optional[float] = None  # 对方上次发言时间
        # self.last_bot_speak_time: Optional[float] = None  # 机器人上次发言时间
        # self.last_check_time: float = time.time()  # 上次查看聊天记录时间
        self.last_message_read: Optional[Dict[str, Any]] = None  # 最后读取的消息ID
        self.last_message_time: float = time.time()

        self.waiting_start_time: float = time.time()  # 等待开始时间，初始化为当前时间

        # 运行状态
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._update_event = asyncio.Event()  # 触发更新的事件
        self._update_complete = asyncio.Event()  # 更新完成的事件

        # 通知管理器
        self.notification_manager = NotificationManager()

        # 冷场检查配置
        self.cold_chat_threshold: float = 60.0  # 60秒无消息判定为冷场
        self.last_cold_chat_check: float = time.time()
        self.is_cold_chat_state: bool = False

        self.update_event = asyncio.Event()
        self.update_interval = 2  # 更新间隔（秒）
        self.message_cache = []
        self.update_running = False

    async def check(self) -> bool:
        """检查距离上一次观察之后是否有了新消息

        Returns:
            bool: 是否有新消息
        """
        logger.debug(f"[私聊][{self.private_name}]检查距离上一次观察之后是否有了新消息: {self.last_check_time}")

        new_message_exists = await self.message_storage.has_new_messages(self.stream_id, self.last_check_time)

        if new_message_exists:
            logger.debug(f"[私聊][{self.private_name}]发现新消息")
            self.last_check_time = time.time()

        return new_message_exists

    async def _add_message_to_history(self, message: Dict[str, Any]):
        """添加消息到历史记录并发送通知

        Args:
            message: 消息数据
        """
        try:
            # 发送新消息通知
            notification = create_new_message_notification(
                sender="chat_observer", target="observation_info", message=message
            )
            # print(self.notification_manager)
            await self.notification_manager.send_notification(notification)
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]添加消息到历史记录时出错: {e}")
            print(traceback.format_exc())

        # 检查并更新冷场状态
        await self._check_cold_chat()

    async def _check_cold_chat(self):
        """检查是否处于冷场状态并发送通知"""
        current_time = time.time()

        # 每10秒检查一次冷场状态
        if current_time - self.last_cold_chat_check < 10:
            return

        self.last_cold_chat_check = current_time

        # 判断是否冷场
        is_cold = (
            True
            if self.last_message_time is None
            else (current_time - self.last_message_time) > self.cold_chat_threshold
        )

        # 如果冷场状态发生变化，发送通知
        if is_cold != self.is_cold_chat_state:
            self.is_cold_chat_state = is_cold
            notification = create_cold_chat_notification(sender="chat_observer", target="pfc", is_cold=is_cold)
            await self.notification_manager.send_notification(notification)

    def new_message_after(self, time_point: float) -> bool:
        """判断是否在指定时间点后有新消息

        Args:
            time_point: 时间戳

        Returns:
            bool: 是否有新消息
        """

        if self.last_message_time is None:
            logger.debug(f"[私聊][{self.private_name}]没有最后消息时间，返回 False")
            return False

        has_new = self.last_message_time > time_point
        logger.debug(
            f"[私聊][{self.private_name}]判断是否在指定时间点后有新消息: {self.last_message_time} > {time_point} = {has_new}"
        )
        return has_new

    def get_message_history(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取消息历史

        Args:
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 限制返回消息数量
            user_id: 指定用户ID

        Returns:
            List[Dict[str, Any]]: 消息列表
        """
        filtered_messages = self.message_history

        if start_time is not None:
            filtered_messages = [m for m in filtered_messages if m["time"] >= start_time]

        if end_time is not None:
            filtered_messages = [m for m in filtered_messages if m["time"] <= end_time]

        if user_id is not None:
            filtered_messages = [
                m for m in filtered_messages if UserInfo.from_dict(m.get("user_info", {})).user_id == user_id
            ]

        if limit is not None:
            filtered_messages = filtered_messages[-limit:]

        return filtered_messages

    async def _fetch_new_messages(self) -> List[Dict[str, Any]]:
        """获取新消息

        Returns:
            List[Dict[str, Any]]: 新消息列表
        """
        new_messages = await self.message_storage.get_messages_after(self.stream_id, self.last_message_time)

        if new_messages:
            self.last_message_read = new_messages[-1]
            self.last_message_time = new_messages[-1]["time"]

        # print(f"获取数据库中找到的新消息: {new_messages}")

        return new_messages

    async def _fetch_new_messages_before(self, time_point: float) -> List[Dict[str, Any]]:
        """获取指定时间点之前的消息

        Args:
            time_point: 时间戳

        Returns:
            List[Dict[str, Any]]: 最多5条消息
        """
        new_messages = await self.message_storage.get_messages_before(self.stream_id, time_point)

        if new_messages:
            self.last_message_read = new_messages[-1]["message_id"]

        logger.debug(f"[私聊][{self.private_name}]获取指定时间点111之前的消息: {new_messages}")

        return new_messages

    """主要观察循环"""

    async def _update_loop(self):
        """更新循环"""
        # try:
        #     start_time = time.time()
        #     messages = await self._fetch_new_messages_before(start_time)
        #     for message in messages:
        #         await self._add_message_to_history(message)
        #     logger.debug(f"[私聊][{self.private_name}]缓冲消息: {messages}")
        # except Exception as e:
        #     logger.error(f"[私聊][{self.private_name}]缓冲消息出错: {e}")

        while self._running:
            try:
                # 等待事件或超时（1秒）
                try:
                    # print("等待事件")
                    await asyncio.wait_for(self._update_event.wait(), timeout=1)

                except asyncio.TimeoutError:
                    # print("超时")
                    pass  # 超时后也执行一次检查

                self._update_event.clear()  # 重置触发事件
                self._update_complete.clear()  # 重置完成事件

                # 获取新消息
                new_messages = await self._fetch_new_messages()

                if new_messages:
                    # 处理新消息
                    for message in new_messages:
                        await self._add_message_to_history(message)

                # 设置完成事件
                self._update_complete.set()

            except Exception as e:
                logger.error(f"[私聊][{self.private_name}]更新循环出错: {e}")
                logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")
                self._update_complete.set()  # 即使出错也要设置完成事件

    def trigger_update(self):
        """触发一次立即更新"""
        self._update_event.set()

    async def wait_for_update(self, timeout: float = 5.0) -> bool:
        """等待更新完成

        Args:
            timeout: 超时时间（秒）

        Returns:
            bool: 是否成功完成更新（False表示超时）
        """
        try:
            await asyncio.wait_for(self._update_complete.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"[私聊][{self.private_name}]等待更新完成超时（{timeout}秒）")
            return False

    def start(self):
        """启动观察器"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.debug(f"[私聊][{self.private_name}]ChatObserver for {self.stream_id} started")

    def stop(self):
        """停止观察器"""
        self._running = False
        self._update_event.set()  # 设置事件以解除等待
        self._update_complete.set()  # 设置完成事件以解除等待
        if self._task:
            self._task.cancel()
        logger.debug(f"[私聊][{self.private_name}]ChatObserver for {self.stream_id} stopped")

    async def process_chat_history(self, messages: list):
        """处理聊天历史

        Args:
            messages: 消息列表
        """
        self.update_check_time()

        for msg in messages:
            try:
                user_info = UserInfo.from_dict(msg.get("user_info", {}))
                if user_info.user_id == global_config.BOT_QQ:
                    self.update_bot_speak_time(msg["time"])
                else:
                    self.update_user_speak_time(msg["time"])
            except Exception as e:
                logger.warning(f"[私聊][{self.private_name}]处理消息时间时出错: {e}")
                continue

    def update_check_time(self):
        """更新查看时间"""
        self.last_check_time = time.time()

    def update_bot_speak_time(self, speak_time: Optional[float] = None):
        """更新机器人说话时间"""
        self.last_bot_speak_time = speak_time or time.time()

    def update_user_speak_time(self, speak_time: Optional[float] = None):
        """更新用户说话时间"""
        self.last_user_speak_time = speak_time or time.time()

    def get_time_info(self) -> str:
        """获取时间信息文本"""
        current_time = time.time()
        time_info = ""

        if self.last_bot_speak_time:
            bot_speak_ago = current_time - self.last_bot_speak_time
            time_info += f"\n距离你上次发言已经过去了{int(bot_speak_ago)}秒"

        if self.last_user_speak_time:
            user_speak_ago = current_time - self.last_user_speak_time
            time_info += f"\n距离对方上次发言已经过去了{int(user_speak_ago)}秒"

        return time_info

    def get_cached_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取缓存的消息历史

        Args:
            limit: 获取的最大消息数量，默认50

        Returns:
            List[Dict[str, Any]]: 缓存的消息历史列表
        """
        return self.message_cache[-limit:]

    def get_last_message(self) -> Optional[Dict[str, Any]]:
        """获取最后一条消息

        Returns:
            Optional[Dict[str, Any]]: 最后一条消息，如果没有则返回None
        """
        if not self.message_cache:
            return None
        return self.message_cache[-1]

    def __str__(self):
        return f"ChatObserver for {self.stream_id}"
