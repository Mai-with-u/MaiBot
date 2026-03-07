from typing import TYPE_CHECKING

import traceback

from src.chat.heart_flow.heartflow import heartflow

# from src.chat.utils.chat_message_builder import replace_user_references
from src.common.utils.utils_message import MessageUtils
from src.common.logger import get_logger
from src.person_info.person_info import Person

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

logger = get_logger("chat")


class HeartFCMessageReceiver:
    """心流处理器，负责处理接收到的消息并计算兴趣度"""

    def __init__(self):
        pass

    async def process_message(self, message: "SessionMessage"):
        """处理接收到的原始消息数据

        主要流程:
        1. 消息解析与初始化
        2. 消息缓冲处理
        3. 过滤检查
        4. 兴趣度计算
        5. 关系处理

        Args:
            message: SessionMessage对象，包含原始消息数据和相关信息
        """
        try:
            # 通知消息不处理
            if message.is_notify:
                logger.debug("通知消息，跳过处理")
                return

            # 1. 消息解析与初始化
            userinfo = message.message_info.user_info
            group_info = message.message_info.group_info
            if userinfo is None or message.platform is None:
                raise ValueError("message userinfo or platform is missing")
            if userinfo.user_id is None or userinfo.user_nickname is None:
                raise ValueError("message userinfo id or nickname is missing")
            user_id = userinfo.user_id
            nickname = userinfo.user_nickname

            # 2. 计算at信息 （现在转移给Adapter完成）
            # is_mentioned, is_at, reply_probability_boost = is_mentioned_bot_in_message(message)
            # # print(f"is_mentioned: {is_mentioned}, is_at: {is_at}, reply_probability_boost: {reply_probability_boost}")
            # message.is_mentioned = is_mentioned
            # message.is_at = is_at

            MessageUtils.store_message_to_db(message)  # 存储消息到数据库

            await heartflow.get_or_create_heartflow_chat(message.session_id)

            # 3. 日志记录
            mes_name = group_info.group_name if group_info else "私聊"

            # TODO: 修复引用格式替换
            # # 应用用户引用格式替换，将回复<aaa:bbb>和@<aaa:bbb>格式转换为可读格式
            # processed_plain_text = replace_user_references(
            #     processed_text, message.message_info.platform, replace_bot_name=True
            # )
            # # if not processed_plain_text:
            # # print(message)

            logger.info(f"[{mes_name}]{userinfo.user_nickname}:{message.processed_plain_text}")

            # 如果是群聊，获取群号和群昵称
            group_id = None
            group_nick_name = None
            if group_info:
                group_id = group_info.group_id
                group_nick_name = userinfo.user_cardname

            _ = Person.register_person(
                platform=message.platform,
                user_id=user_id,
                nickname=nickname,
                group_id=group_id,
                group_nick_name=group_nick_name,
            )

        except Exception as e:
            logger.error(f"消息处理失败: {e}")
            print(traceback.format_exc())
