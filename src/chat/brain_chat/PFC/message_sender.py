"""PFC 侧消息发送封装。"""

from typing import Optional

from rich.traceback import install

from src.chat.message_receive.chat_manager import BotChatSession
from src.common.data_models.mai_message_data_model import MaiMessage
from src.common.logger import get_logger
from src.services import send_service as send_api

install(extra_lines=3)

logger = get_logger("message_sender")


class DirectMessageSender:
    """直接消息发送器。"""

    def __init__(self, private_name: str) -> None:
        """初始化直接消息发送器。

        Args:
            private_name: 当前私聊实例的名称。
        """
        self.private_name = private_name

    async def send_message(
        self,
        chat_stream: BotChatSession,
        content: str,
        reply_to_message: Optional[MaiMessage] = None,
    ) -> None:
        """发送文本消息到聊天流。

        Args:
            chat_stream: 目标聊天会话。
            content: 待发送的文本内容。
            reply_to_message: 可选的引用回复锚点消息。

        Raises:
            RuntimeError: 当消息发送失败时抛出。
        """
        try:
            sent = await send_api.text_to_stream(
                text=content,
                stream_id=chat_stream.session_id,
                set_reply=reply_to_message is not None,
                reply_message=reply_to_message,
                storage_message=True,
            )

            if sent:
                logger.info(f"[私聊][{self.private_name}]PFC消息已发送: {content}")
                return

            logger.error(f"[私聊][{self.private_name}]PFC消息发送失败")
            raise RuntimeError("消息发送失败")
        except Exception as exc:
            logger.error(f"[私聊][{self.private_name}]PFC消息发送失败: {exc}")
            raise
