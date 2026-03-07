import time
from typing import Optional
from src.common.logger import get_logger
from maim_message import Seg

from src.common.data_models.mai_message_data_model import MaiMessage, UserInfo
from src.chat.message_receive.chat_manager import BotChatSession
from src.chat.message_receive.message import MessageSending
from src.chat.message_receive.uni_message_sender import UniversalMessageSender
from src.config.config import global_config
from rich.traceback import install

install(extra_lines=3)


logger = get_logger("message_sender")


class DirectMessageSender:
    """直接消息发送器"""

    def __init__(self, private_name: str):
        self.private_name = private_name

    async def send_message(
        self,
        chat_stream: BotChatSession,
        content: str,
        reply_to_message: Optional[MaiMessage] = None,
    ) -> None:
        """发送消息到聊天流

        Args:
            chat_stream: 聊天会话
            content: 消息内容
            reply_to_message: 要回复的消息（可选）
        """
        try:
            # 创建消息内容
            segments = Seg(type="seglist", data=[Seg(type="text", data=content)])

            # 获取麦麦的信息
            bot_user_info = UserInfo(
                user_id=global_config.bot.qq_account,
                user_nickname=global_config.bot.nickname,
            )

            # 用当前时间作为message_id，和之前那套sender一样
            message_id = f"dm{round(time.time(), 2)}"

            # 构建发送者信息（私聊时为接收者）
            sender_info = None
            if reply_to_message and reply_to_message.message_info and reply_to_message.message_info.user_info:
                sender_info = reply_to_message.message_info.user_info

            # 构建消息对象
            message = MessageSending(
                message_id=message_id,
                session=chat_stream,
                bot_user_info=bot_user_info,
                sender_info=sender_info,
                message_segment=segments,
                reply=reply_to_message,
                is_head=True,
                is_emoji=False,
                thinking_start_time=time.time(),
            )

            # 发送消息
            message_sender = UniversalMessageSender()
            sent = await message_sender.send_message(message, typing=False, set_reply=False, storage_message=True)

            if sent:
                logger.info(f"[私聊][{self.private_name}]PFC消息已发送: {content}")
            else:
                logger.error(f"[私聊][{self.private_name}]PFC消息发送失败")
                raise RuntimeError("消息发送失败")

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]PFC消息发送失败: {str(e)}")
            raise
