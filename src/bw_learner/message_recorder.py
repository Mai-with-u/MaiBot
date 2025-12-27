import time
import asyncio
from typing import List, Any
from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp_with_chat_inclusive
from src.bw_learner.expression_learner import expression_learner_manager
from src.bw_learner.jargon_miner import miner_manager

logger = get_logger("bw_learner")


class MessageRecorder:
    """
    统一的消息记录器，负责管理时间窗口和消息提取，并将消息分发给 expression_learner 和 jargon_miner
    """

    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.chat_stream = get_chat_manager().get_stream(chat_id)
        self.chat_name = get_chat_manager().get_stream_name(chat_id) or chat_id

        # 维护每个chat的上次提取时间
        self.last_extraction_time: float = time.time()

        # 提取锁，防止并发执行
        self._extraction_lock = asyncio.Lock()

        # 获取 expression 和 jargon 的配置参数
        self._init_parameters()

        # 获取 expression_learner 和 jargon_miner 实例
        self.expression_learner = expression_learner_manager.get_expression_learner(chat_id)
        self.jargon_miner = miner_manager.get_miner(chat_id)

    def _init_parameters(self) -> None:
        """初始化提取参数"""
        # 获取 expression 配置
        _, self.enable_expression_learning, self.enable_jargon_learning = (
            global_config.expression.get_expression_config_for_chat(self.chat_id)
        )
        self.min_messages_for_extraction = 30
        self.min_extraction_interval = 60

        logger.debug(
            f"MessageRecorder 初始化: chat_id={self.chat_id}, "
            f"min_messages={self.min_messages_for_extraction}, "
            f"min_interval={self.min_extraction_interval}"
        )

    def should_trigger_extraction(self) -> bool:
        """
        检查是否应该触发消息提取

        Returns:
            bool: 是否应该触发提取
        """
        # 检查时间间隔
        time_diff = time.time() - self.last_extraction_time
        if time_diff < self.min_extraction_interval:
            return False

        # 检查消息数量
        recent_messages = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_extraction_time,
            timestamp_end=time.time(),
        )

        if not recent_messages or len(recent_messages) < self.min_messages_for_extraction:
            return False

        return True

    async def extract_and_distribute(self) -> None:
        """
        提取消息并分发给 expression_learner 和 jargon_miner
        """
        # 使用异步锁防止并发执行
        async with self._extraction_lock:
            # 在锁内检查，避免并发触发
            if not self.should_trigger_extraction():
                return

            # 检查 chat_stream 是否存在
            if not self.chat_stream:
                return

            # 记录本次提取的时间窗口，避免重复提取
            extraction_start_time = self.last_extraction_time
            extraction_end_time = time.time()

            # 立即更新提取时间，防止并发触发
            self.last_extraction_time = extraction_end_time

            try:
                # logger.info(f"在聊天流 {self.chat_name} 开始统一消息提取和分发")

                # 拉取提取窗口内的消息
                messages = get_raw_msg_by_timestamp_with_chat_inclusive(
                    chat_id=self.chat_id,
                    timestamp_start=extraction_start_time,
                    timestamp_end=extraction_end_time,
                )

                if not messages:
                    logger.debug(f"聊天流 {self.chat_name} 没有新消息，跳过提取")
                    return

                # 按时间排序，确保顺序一致
                messages = sorted(messages, key=lambda msg: msg.time or 0)

                logger.info(
                    f"聊天流 {self.chat_name} 提取到 {len(messages)} 条消息，"
                    f"时间窗口: {extraction_start_time:.2f} - {extraction_end_time:.2f}"
                )

                # 触发 expression_learner 和 jargon_miner 的处理
                if self.enable_expression_learning:
                    asyncio.create_task(
                        self._trigger_expression_learning(messages)
                    )

            except Exception as e:
                logger.error(f"为聊天流 {self.chat_name} 提取和分发消息失败: {e}")
                import traceback

                traceback.print_exc()
                # 即使失败也保持时间戳更新，避免频繁重试

    async def _trigger_expression_learning(
        self, messages: List[Any]
    ) -> None:
        """
        触发 expression 学习，使用指定的消息列表

        Args:
            timestamp_start: 开始时间戳
            timestamp_end: 结束时间戳
            messages: 消息列表
        """
        try:
            # 传递消息给 ExpressionLearner（必需参数）
            learnt_style = await self.expression_learner.learn_and_store(messages=messages)

            if learnt_style:
                logger.info(f"聊天流 {self.chat_name} 表达学习完成")
            else:
                logger.debug(f"聊天流 {self.chat_name} 表达学习未获得有效结果")
        except Exception as e:
            logger.error(f"为聊天流 {self.chat_name} 触发表达学习失败: {e}")
            import traceback

            traceback.print_exc()


class MessageRecorderManager:
    """MessageRecorder 管理器"""

    def __init__(self) -> None:
        self._recorders: dict[str, MessageRecorder] = {}

    def get_recorder(self, chat_id: str) -> MessageRecorder:
        """获取或创建指定 chat_id 的 MessageRecorder"""
        if chat_id not in self._recorders:
            self._recorders[chat_id] = MessageRecorder(chat_id)
        return self._recorders[chat_id]


# 全局管理器实例
recorder_manager = MessageRecorderManager()


async def extract_and_distribute_messages(chat_id: str) -> None:
    """
    统一的消息提取和分发入口函数

    Args:
        chat_id: 聊天流ID
    """
    recorder = recorder_manager.get_recorder(chat_id)
    await recorder.extract_and_distribute()
