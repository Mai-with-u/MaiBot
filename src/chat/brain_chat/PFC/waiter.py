from src.common.logger import get_module_logger
from .chat_observer import ChatObserver
from .conversation_info import ConversationInfo

# from src.individuality.individuality import Individuality # 不再需要
from ...config.config import global_config
import time
import asyncio

logger = get_module_logger("waiter")

# --- 在这里设定你想要的超时时间（秒） ---
# 例如： 120 秒 = 2 分钟
DESIRED_TIMEOUT_SECONDS = 300


class Waiter:
    """等待处理类"""

    def __init__(self, stream_id: str, private_name: str):
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.name = global_config.BOT_NICKNAME
        self.private_name = private_name
        # self.wait_accumulated_time = 0 # 不再需要累加计时

    async def wait(self, conversation_info: ConversationInfo) -> bool:
        """等待用户新消息或超时"""
        wait_start_time = time.time()
        logger.info(f"[私聊][{self.private_name}]进入常规等待状态 (超时: {DESIRED_TIMEOUT_SECONDS} 秒)...")

        while True:
            # 检查是否有新消息
            if self.chat_observer.new_message_after(wait_start_time):
                logger.info(f"[私聊][{self.private_name}]等待结束，收到新消息")
                return False  # 返回 False 表示不是超时

            # 检查是否超时
            elapsed_time = time.time() - wait_start_time
            if elapsed_time > DESIRED_TIMEOUT_SECONDS:
                logger.info(f"[私聊][{self.private_name}]等待超过 {DESIRED_TIMEOUT_SECONDS} 秒...添加思考目标。")
                wait_goal = {
                    "goal": f"你等待了{elapsed_time / 60:.1f}分钟，注意可能在对方看来聊天已经结束，思考接下来要做什么",
                    "reasoning": "对方很久没有回复你的消息了",
                }
                conversation_info.goal_list.append(wait_goal)
                logger.info(f"[私聊][{self.private_name}]添加目标: {wait_goal}")
                return True  # 返回 True 表示超时

            await asyncio.sleep(5)  # 每 5 秒检查一次
            logger.debug(
                f"[私聊][{self.private_name}]等待中..."
            )  # 可以考虑把这个频繁日志注释掉，只在超时或收到消息时输出

    async def wait_listening(self, conversation_info: ConversationInfo) -> bool:
        """倾听用户发言或超时"""
        wait_start_time = time.time()
        logger.info(f"[私聊][{self.private_name}]进入倾听等待状态 (超时: {DESIRED_TIMEOUT_SECONDS} 秒)...")

        while True:
            # 检查是否有新消息
            if self.chat_observer.new_message_after(wait_start_time):
                logger.info(f"[私聊][{self.private_name}]倾听等待结束，收到新消息")
                return False  # 返回 False 表示不是超时

            # 检查是否超时
            elapsed_time = time.time() - wait_start_time
            if elapsed_time > DESIRED_TIMEOUT_SECONDS:
                logger.info(f"[私聊][{self.private_name}]倾听等待超过 {DESIRED_TIMEOUT_SECONDS} 秒...添加思考目标。")
                wait_goal = {
                    # 保持 goal 文本一致
                    "goal": f"你等待了{elapsed_time / 60:.1f}分钟，对方似乎话说一半突然消失了，可能忙去了？也可能忘记了回复？要问问吗？还是结束对话？或继续等待？思考接下来要做什么",
                    "reasoning": "对方话说一半消失了，很久没有回复",
                }
                conversation_info.goal_list.append(wait_goal)
                logger.info(f"[私聊][{self.private_name}]添加目标: {wait_goal}")
                return True  # 返回 True 表示超时

            await asyncio.sleep(5)  # 每 5 秒检查一次
            logger.debug(f"[私聊][{self.private_name}]倾听等待中...")  # 同上，可以考虑注释掉
