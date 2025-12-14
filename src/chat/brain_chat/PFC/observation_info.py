from typing import List, Optional, Dict, Any, Set
from maim_message import UserInfo
import time
from src.common.logger import get_module_logger
from .chat_observer import ChatObserver
from .chat_states import NotificationHandler, NotificationType, Notification
from src.plugins.utils.chat_message_builder import build_readable_messages
import traceback  # 导入 traceback 用于调试

logger = get_module_logger("observation_info")


class ObservationInfoHandler(NotificationHandler):
    """ObservationInfo的通知处理器"""

    def __init__(self, observation_info: "ObservationInfo", private_name: str):
        """初始化处理器

        Args:
            observation_info: 要更新的ObservationInfo实例
            private_name: 私聊对象的名称，用于日志记录
        """
        self.observation_info = observation_info
        # 将 private_name 存储在 handler 实例中
        self.private_name = private_name

    async def handle_notification(self, notification: Notification):  # 添加类型提示
        # 获取通知类型和数据
        notification_type = notification.type
        data = notification.data

        try:  # 添加错误处理块
            if notification_type == NotificationType.NEW_MESSAGE:
                # 处理新消息通知
                # logger.debug(f"[私聊][{self.private_name}]收到新消息通知data: {data}") # 可以在需要时取消注释
                message_id = data.get("message_id")
                processed_plain_text = data.get("processed_plain_text")
                detailed_plain_text = data.get("detailed_plain_text")
                user_info_dict = data.get("user_info")  # 先获取字典
                time_value = data.get("time")

                # 确保 user_info 是字典类型再创建 UserInfo 对象
                user_info = None
                if isinstance(user_info_dict, dict):
                    try:
                        user_info = UserInfo.from_dict(user_info_dict)
                    except Exception as e:
                        logger.error(
                            f"[私聊][{self.private_name}]从字典创建 UserInfo 时出错: {e}, 字典内容: {user_info_dict}"
                        )
                        # 可以选择在这里返回或记录错误，避免后续代码出错
                        return
                elif user_info_dict is not None:
                    logger.warning(
                        f"[私聊][{self.private_name}]收到的 user_info 不是预期的字典类型: {type(user_info_dict)}"
                    )
                    # 根据需要处理非字典情况，这里暂时返回
                    return

                message = {
                    "message_id": message_id,
                    "processed_plain_text": processed_plain_text,
                    "detailed_plain_text": detailed_plain_text,
                    "user_info": user_info_dict,  # 存储原始字典或 UserInfo 对象，取决于你的 update_from_message 如何处理
                    "time": time_value,
                }
                # 传递 UserInfo 对象（如果成功创建）或原始字典
                await self.observation_info.update_from_message(message, user_info)  # 修改：传递 user_info 对象

            elif notification_type == NotificationType.COLD_CHAT:
                # 处理冷场通知
                is_cold = data.get("is_cold", False)
                await self.observation_info.update_cold_chat_status(is_cold, time.time())  # 修改：改为 await 调用

            elif notification_type == NotificationType.ACTIVE_CHAT:
                # 处理活跃通知 (通常由 COLD_CHAT 的反向状态处理)
                is_active = data.get("is_active", False)
                self.observation_info.is_cold = not is_active

            elif notification_type == NotificationType.BOT_SPEAKING:
                # 处理机器人说话通知 (按需实现)
                self.observation_info.is_typing = False
                self.observation_info.last_bot_speak_time = time.time()

            elif notification_type == NotificationType.USER_SPEAKING:
                # 处理用户说话通知
                self.observation_info.is_typing = False
                self.observation_info.last_user_speak_time = time.time()

            elif notification_type == NotificationType.MESSAGE_DELETED:
                # 处理消息删除通知
                message_id = data.get("message_id")
                # 从 unprocessed_messages 中移除被删除的消息
                original_count = len(self.observation_info.unprocessed_messages)
                self.observation_info.unprocessed_messages = [
                    msg for msg in self.observation_info.unprocessed_messages if msg.get("message_id") != message_id
                ]
                if len(self.observation_info.unprocessed_messages) < original_count:
                    logger.info(f"[私聊][{self.private_name}]移除了未处理的消息 (ID: {message_id})")

            elif notification_type == NotificationType.USER_JOINED:
                # 处理用户加入通知 (如果适用私聊场景)
                user_id = data.get("user_id")
                if user_id:
                    self.observation_info.active_users.add(str(user_id))  # 确保是字符串

            elif notification_type == NotificationType.USER_LEFT:
                # 处理用户离开通知 (如果适用私聊场景)
                user_id = data.get("user_id")
                if user_id:
                    self.observation_info.active_users.discard(str(user_id))  # 确保是字符串

            elif notification_type == NotificationType.ERROR:
                # 处理错误通知
                error_msg = data.get("error", "未提供错误信息")
                logger.error(f"[私聊][{self.private_name}]收到错误通知: {error_msg}")

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]处理通知时发生错误: {e}")
            logger.error(traceback.format_exc())  # 打印详细堆栈信息


# @dataclass <-- 这个，不需要了（递黄瓜）
class ObservationInfo:
    """决策信息类，用于收集和管理来自chat_observer的通知信息 (手动实现 __init__)"""

    # 类型提示保留，可用于文档和静态分析
    private_name: str
    chat_history: List[Dict[str, Any]]
    chat_history_str: str
    unprocessed_messages: List[Dict[str, Any]]
    active_users: Set[str]
    last_bot_speak_time: Optional[float]
    last_user_speak_time: Optional[float]
    last_message_time: Optional[float]
    last_message_id: Optional[str]
    last_message_content: str
    last_message_sender: Optional[str]
    bot_id: Optional[str]
    chat_history_count: int
    new_messages_count: int
    cold_chat_start_time: Optional[float]
    cold_chat_duration: float
    is_typing: bool
    is_cold_chat: bool
    changed: bool
    chat_observer: Optional[ChatObserver]
    handler: Optional[ObservationInfoHandler]

    def __init__(self, private_name: str):
        """
        手动初始化 ObservationInfo 的所有实例变量。
        """

        # 接收的参数
        self.private_name: str = private_name

        # data_list
        self.chat_history: List[Dict[str, Any]] = []
        self.chat_history_str: str = ""
        self.unprocessed_messages: List[Dict[str, Any]] = []
        self.active_users: Set[str] = set()

        # data
        self.last_bot_speak_time: Optional[float] = None
        self.last_user_speak_time: Optional[float] = None
        self.last_message_time: Optional[float] = None
        self.last_message_id: Optional[str] = None
        self.last_message_content: str = ""
        self.last_message_sender: Optional[str] = None
        self.bot_id: Optional[str] = None
        self.chat_history_count: int = 0
        self.new_messages_count: int = 0
        self.cold_chat_start_time: Optional[float] = None
        self.cold_chat_duration: float = 0.0

        # state
        self.is_typing: bool = False
        self.is_cold_chat: bool = False
        self.changed: bool = False

        # 关联对象
        self.chat_observer: Optional[ChatObserver] = None

        self.handler: ObservationInfoHandler = ObservationInfoHandler(self, self.private_name)

    def bind_to_chat_observer(self, chat_observer: ChatObserver):
        """绑定到指定的chat_observer

        Args:
            chat_observer: 要绑定的 ChatObserver 实例
        """
        if self.chat_observer:
            logger.warning(f"[私聊][{self.private_name}]尝试重复绑定 ChatObserver")
            return

        self.chat_observer = chat_observer
        try:
            if not self.handler:  # 确保 handler 已经被创建
                logger.error(f"[私聊][{self.private_name}] 尝试绑定时 handler 未初始化！")
                self.chat_observer = None  # 重置，防止后续错误
                return

            # 注册关心的通知类型
            self.chat_observer.notification_manager.register_handler(
                target="observation_info", notification_type=NotificationType.NEW_MESSAGE, handler=self.handler
            )
            self.chat_observer.notification_manager.register_handler(
                target="observation_info", notification_type=NotificationType.COLD_CHAT, handler=self.handler
            )
            # 可以根据需要注册更多通知类型
            # self.chat_observer.notification_manager.register_handler(
            #     target="observation_info", notification_type=NotificationType.MESSAGE_DELETED, handler=self.handler
            # )
            logger.info(f"[私聊][{self.private_name}]成功绑定到 ChatObserver")
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]绑定到 ChatObserver 时出错: {e}")
            self.chat_observer = None  # 绑定失败，重置

    def unbind_from_chat_observer(self):
        """解除与chat_observer的绑定"""
        if (
            self.chat_observer and hasattr(self.chat_observer, "notification_manager") and self.handler
        ):  # 增加 handler 检查
            try:
                self.chat_observer.notification_manager.unregister_handler(
                    target="observation_info", notification_type=NotificationType.NEW_MESSAGE, handler=self.handler
                )
                self.chat_observer.notification_manager.unregister_handler(
                    target="observation_info", notification_type=NotificationType.COLD_CHAT, handler=self.handler
                )
                # 如果注册了其他类型，也要在这里注销
                # self.chat_observer.notification_manager.unregister_handler(
                #     target="observation_info", notification_type=NotificationType.MESSAGE_DELETED, handler=self.handler
                # )
                logger.info(f"[私聊][{self.private_name}]成功从 ChatObserver 解绑")
            except Exception as e:
                logger.error(f"[私聊][{self.private_name}]从 ChatObserver 解绑时出错: {e}")
            finally:  # 确保 chat_observer 被重置
                self.chat_observer = None
        else:
            logger.warning(f"[私聊][{self.private_name}]尝试解绑时 ChatObserver 不存在、无效或 handler 未设置")

    # 修改：update_from_message 接收 UserInfo 对象
    async def update_from_message(self, message: Dict[str, Any], user_info: Optional[UserInfo]):
        """从消息更新信息

        Args:
            message: 消息数据字典
            user_info: 解析后的 UserInfo 对象 (可能为 None)
        """
        message_time = message.get("time")
        message_id = message.get("message_id")
        processed_text = message.get("processed_plain_text", "")

        # 只有在新消息到达时才更新 last_message 相关信息
        if message_time and message_time > (self.last_message_time or 0):
            self.last_message_time = message_time
            self.last_message_id = message_id
            self.last_message_content = processed_text
            # 重置冷场计时器
            self.is_cold_chat = False
            self.cold_chat_start_time = None
            self.cold_chat_duration = 0.0

            if user_info:
                sender_id = str(user_info.user_id)  # 确保是字符串
                self.last_message_sender = sender_id
                # 更新发言时间
                if sender_id == self.bot_id:
                    self.last_bot_speak_time = message_time
                else:
                    self.last_user_speak_time = message_time
                    self.active_users.add(sender_id)  # 用户发言则认为其活跃
            else:
                logger.warning(
                    f"[私聊][{self.private_name}]处理消息更新时缺少有效的 UserInfo 对象, message_id: {message_id}"
                )
                self.last_message_sender = None  # 发送者未知

            # 将原始消息字典添加到未处理列表
            self.unprocessed_messages.append(message)
            self.new_messages_count = len(self.unprocessed_messages)  # 直接用列表长度

            # logger.debug(f"[私聊][{self.private_name}]消息更新: last_time={self.last_message_time}, new_count={self.new_messages_count}")
            self.update_changed()  # 标记状态已改变
        else:
            # 如果消息时间戳不是最新的，可能不需要处理，或者记录一个警告
            pass
            # logger.warning(f"[私聊][{self.private_name}]收到过时或无效时间戳的消息: ID={message_id}, time={message_time}")

    def update_changed(self):
        """标记状态已改变，并重置标记"""
        # logger.debug(f"[私聊][{self.private_name}]状态标记为已改变 (changed=True)")
        self.changed = True

    async def update_cold_chat_status(self, is_cold: bool, current_time: float):
        """更新冷场状态

        Args:
            is_cold: 是否处于冷场状态
            current_time: 当前时间戳
        """
        if is_cold != self.is_cold_chat:  # 仅在状态变化时更新
            self.is_cold_chat = is_cold
            if is_cold:
                # 进入冷场状态
                self.cold_chat_start_time = (
                    self.last_message_time or current_time
                )  # 从最后消息时间开始算，或从当前时间开始
                logger.info(f"[私聊][{self.private_name}]进入冷场状态，开始时间: {self.cold_chat_start_time}")
            else:
                # 结束冷场状态
                if self.cold_chat_start_time:
                    self.cold_chat_duration = current_time - self.cold_chat_start_time
                    logger.info(f"[私聊][{self.private_name}]结束冷场状态，持续时间: {self.cold_chat_duration:.2f} 秒")
                self.cold_chat_start_time = None  # 重置开始时间
            self.update_changed()  # 状态变化，标记改变

        # 即使状态没变，如果是冷场状态，也更新持续时间
        if self.is_cold_chat and self.cold_chat_start_time:
            self.cold_chat_duration = current_time - self.cold_chat_start_time

    def get_active_duration(self) -> float:
        """获取当前活跃时长 (距离最后一条消息的时间)

        Returns:
            float: 最后一条消息到现在的时长（秒）
        """
        if not self.last_message_time:
            return 0.0
        return time.time() - self.last_message_time

    def get_user_response_time(self) -> Optional[float]:
        """获取用户最后响应时间 (距离用户最后发言的时间)

        Returns:
            Optional[float]: 用户最后发言到现在的时长（秒），如果没有用户发言则返回None
        """
        if not self.last_user_speak_time:
            return None
        return time.time() - self.last_user_speak_time

    def get_bot_response_time(self) -> Optional[float]:
        """获取机器人最后响应时间 (距离机器人最后发言的时间)

        Returns:
            Optional[float]: 机器人最后发言到现在的时长（秒），如果没有机器人发言则返回None
        """
        if not self.last_bot_speak_time:
            return None
        return time.time() - self.last_bot_speak_time

    async def clear_unprocessed_messages(self):
        """将未处理消息移入历史记录，并更新相关状态"""
        if not self.unprocessed_messages:
            return  # 没有未处理消息，直接返回

        # logger.debug(f"[私聊][{self.private_name}]处理 {len(self.unprocessed_messages)} 条未处理消息...")
        # 将未处理消息添加到历史记录中 (确保历史记录有长度限制，避免无限增长)
        max_history_len = 100  # 示例：最多保留100条历史记录
        self.chat_history.extend(self.unprocessed_messages)
        if len(self.chat_history) > max_history_len:
            self.chat_history = self.chat_history[-max_history_len:]

        # 更新历史记录字符串 (只使用最近一部分生成，例如20条)
        history_slice_for_str = self.chat_history[-20:]
        try:
            self.chat_history_str = await build_readable_messages(
                history_slice_for_str,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,  # read_mark 可能需要根据逻辑调整
            )
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]构建聊天记录字符串时出错: {e}")
            self.chat_history_str = "[构建聊天记录出错]"  # 提供错误提示

        # 清空未处理消息列表和计数
        # cleared_count = len(self.unprocessed_messages)
        self.unprocessed_messages.clear()
        self.new_messages_count = 0
        # self.has_unread_messages = False # 这个状态可以通过 new_messages_count 判断

        self.chat_history_count = len(self.chat_history)  # 更新历史记录总数
        # logger.debug(f"[私聊][{self.private_name}]已处理 {cleared_count} 条消息，当前历史记录 {self.chat_history_count} 条。")

        self.update_changed()  # 状态改变
