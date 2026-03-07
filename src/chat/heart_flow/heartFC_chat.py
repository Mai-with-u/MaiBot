from rich.traceback import install
from typing import Optional, List, TYPE_CHECKING

import asyncio
import time
import traceback
import random

from src.common.logger import get_logger
from src.common.utils.utils_session import SessionUtils
from src.config.config import global_config
from src.chat.message_receive.chat_manager import chat_manager

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

install(extra_lines=5)

logger = get_logger("heartFC_chat")


class HeartFChatting:
    """
    管理一个连续的Focus Chat聊天会话
    用于在特定的聊天会话里面生成回复
    """

    def __init__(self, session_id: str):
        """
        初始化 HeartFChatting 实例

        Args:
            session_id: 聊天会话ID
        """
        # 基础属性
        self.session_id = session_id
        session_name = chat_manager.get_session_name(session_id) or session_id
        self.log_prefix = f"[{session_name}]"

        # 系统运行状态
        self._running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._cycle_counter: int = 0
        self._hfc_lock: asyncio.Lock = asyncio.Lock()  # 用于保护 _hfc_func 的并发访问
        # 聊天频率相关
        self._consecutive_no_reply_count = 0  # 跟踪连续 no_reply 次数，用于动态调整阈值
        self._talk_frequency_adjust: float = 1.0  # 发言频率修正值，默认为1.0，可以根据需要调整

        # HFC内消息缓存
        self.message_cache: List[SessionMessage] = []

        # Asyncio Event 用于控制循环的开始和结束
        self._cycle_event = asyncio.Event()

    async def start(self):
        """启动 HeartFChatting 的主循环"""
        # 先检查是否已经启动运行
        if self._running:
            logger.debug(f"{self.log_prefix} 已经在运行中，无需重复启动")
            return

        try:
            self._running = True
            self._cycle_event.clear()  # 确保事件初始状态为未设置

            self._loop_task = asyncio.create_task(self.main_loop())
            self._loop_task.add_done_callback(self._handle_loop_completion)

            logger.info(f"{self.log_prefix} HeartFChatting 启动完成")
        except Exception as e:
            logger.error(f"{self.log_prefix} 启动 HeartFChatting 失败: {e}", exc_info=True)
            self._running = False  # 确保状态正确
            self._cycle_event.set()  # 确保事件被设置，避免死锁
            self._loop_task = None  # 确保任务引用被清理
            raise

    async def stop(self):
        """停止 HeartFChatting 的主循环"""
        if not self._running:
            logger.debug(f"{self.log_prefix} HeartFChatting 已经停止，无需重复停止")
            return

        self._running = False
        self._cycle_event.set()  # 触发事件，通知循环结束

        if self._loop_task:
            self._loop_task.cancel()  # 取消主循环任务
            try:
                await self._loop_task  # 等待任务完成
            except asyncio.CancelledError:
                logger.info(f"{self.log_prefix} HeartFChatting 主循环已成功取消")
            except Exception as e:
                logger.error(f"{self.log_prefix} 停止 HeartFChatting 时发生错误: {e}", exc_info=True)
            finally:
                self._loop_task = None  # 确保任务引用被清理

        logger.info(f"{self.log_prefix} HeartFChatting 已停止")

    async def adjust_talk_frequency(self, new_value: float):
        """调整发言频率的调整值

        Args:
            new_value: 新的修正值，必须为非负数。值越大，修正发言频率越高；值越小，修正发言频率越低。
        """
        self._talk_frequency_adjust = max(0.0, new_value)

    async def register_message(self, message: "SessionMessage"):
        """注册一条消息到 HeartFChatting 的缓存中，并检测其是否产生提及，决定是否唤醒聊天

        Args:
            message: 待注册的消息对象
        """
        self.message_cache.append(message)
        # 先检查at必回复
        if global_config.chat.inevitable_at_reply and message.is_at:
            async with self._hfc_lock:  # 确保与主循环逻辑的互斥访问
                await self._judge_and_response(message)
            return  # 直接返回，避免同一条消息被主循环再次处理
        # 再检查提及必回复
        if global_config.chat.mentioned_bot_reply and message.is_mentioned:
            # 直接获取锁，确保一定一定触发回复逻辑，不受当前是否正在执行主循环的影响
            async with self._hfc_lock:  # 确保与主循环逻辑的互斥访问
                await self._judge_and_response(message)
            return

    async def main_loop(self):
        try:
            while self._running and not self._cycle_event.is_set():
                if not self._hfc_lock.locked():
                    async with self._hfc_lock:  # 确保主循环逻辑的互斥访问
                        await self._hfc_func()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} HeartFChatting: 主循环被取消，正在关闭")
        except Exception as e:
            logger.error(f"{self.log_prefix} 麦麦聊天意外错误: {e}，将于3s后尝试重新启动")
            await self.stop()  # 确保状态正确
            await asyncio.sleep(3)
            await self.start()  # 尝试重新启动

    async def _hfc_func(self, mentioned_message: Optional["SessionMessage"] = None):
        """心流聊天的主循环逻辑"""
        if self._consecutive_no_reply_count >= 5:
            threshold = 2
        elif self._consecutive_no_reply_count >= 3:
            threshold = 2 if random.random() < 0.5 else 1
        else:
            threshold = 1

        if len(self.message_cache) < threshold:
            await asyncio.sleep(0.2)
            return True

        talk_value_threshold = random.random() * self._get_talk_value(self.session_id) * self._talk_frequency_adjust
        if mentioned_message and global_config.chat.mentioned_bot_reply:
            await self._judge_and_response(mentioned_message)
        elif random.random() < talk_value_threshold:
            await self._judge_and_response()
        return True

    async def _judge_and_response(self, mentioned_message: Optional["SessionMessage"] = None):
        """判定和生成回复"""
        # TODO: 在expression和reflector重构完成后完成这里的逻辑

    def _handle_loop_completion(self, task: asyncio.Task):
        """当 _hfc_func 任务完成时执行的回调。"""
        try:
            if exception := task.exception():
                logger.error(f"{self.log_prefix} HeartFChatting: 脱离了聊天(异常): {exception}")
                logger.error(traceback.format_exc())  # Log full traceback for exceptions
            else:
                logger.info(f"{self.log_prefix} HeartFChatting: 脱离了聊天 (外部停止)")
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} HeartFChatting: 结束了聊天")

    def _get_talk_value(self, session_id: Optional[str]) -> float:
        result = global_config.chat.talk_value or 0.0
        if not global_config.chat.enable_talk_value_rules or not global_config.chat.talk_value_rules:
            return result
        local_time = time.localtime()
        now_min = local_time.tm_hour * 60 + local_time.tm_min

        # 优先匹配会话相关的规则
        if session_id:
            for rule in global_config.chat.talk_value_rules:
                if not rule.platform and not rule.item_id:
                    continue  # 一起留空表示全局
                if rule.rule_type == "group":
                    rule_session_id = SessionUtils.calculate_session_id(rule.platform, group_id=str(rule.item_id))
                else:
                    rule_session_id = SessionUtils.calculate_session_id(rule.platform, user_id=str(rule.item_id))
                if rule_session_id != session_id:
                    continue  # 不匹配的会话ID，跳过
                parsed_range = self._parse_range(rule.time)
                if not parsed_range:
                    continue  # 无法解析的时间范围，跳过
                start_min, end_min = parsed_range
                in_range: bool = False
                if start_min <= end_min:
                    in_range = start_min <= now_min <= end_min
                else:  # 跨天的时间范围
                    in_range = now_min >= start_min or now_min <= end_min
                if in_range:
                    return rule.value or 0.0  # 如果规则生效但没有设置值，返回0.0

        # 没有匹配到会话相关的规则，继续匹配全局规则
        for rule in global_config.chat.talk_value_rules:
            if rule.platform or rule.item_id:
                continue  # 只匹配全局规则
            parsed_range = self._parse_range(rule.time)
            if not parsed_range:
                continue  # 无法解析的时间范围，跳过
            start_min, end_min = parsed_range
            in_range: bool = False
            if start_min <= end_min:
                in_range = start_min <= now_min <= end_min
            else:  # 跨天的时间范围
                in_range = now_min >= start_min or now_min <= end_min
            if in_range:
                return rule.value or 0.0  # 如果规则生效但没有设置值，返回0.0
        return result  # 如果没有任何规则生效，返回默认值

    def _parse_range(self, range_str: str) -> Optional[tuple[int, int]]:
        """解析 "HH:MM-HH:MM" 到 (start_min, end_min)。"""
        try:
            start_str, end_str = [s.strip() for s in range_str.split("-")]
            sh, sm = [int(x) for x in start_str.split(":")]
            eh, em = [int(x) for x in end_str.split(":")]
            return sh * 60 + sm, eh * 60 + em
        except Exception:
            return None
