import re
import traceback

from typing import TYPE_CHECKING

from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.storage import MessageStorage
from src.chat.heart_flow.heartflow import heartflow
from src.chat.utils.utils import is_mentioned_bot_in_message
from src.chat.utils.chat_message_builder import replace_user_references
from src.common.logger import get_logger
from src.person_info.person_info import Person
from src.common.database.database_model import Images

if TYPE_CHECKING:
    pass

logger = get_logger("chat")


class HeartFCMessageReceiver:
    """心流处理器，负责处理接收到的消息并计算兴趣度"""

    def __init__(self):
        """初始化心流处理器，创建消息存储实例"""
        self.storage = MessageStorage()

    async def process_message(self, message: MessageRecv) -> None:
        """处理接收到的原始消息数据

        主要流程:
        1. 消息解析与初始化
        2. 消息缓冲处理
        3. 过滤检查
        4. 兴趣度计算
        5. 关系处理

        Args:
            message_data: 原始消息字符串
        """
        try:
            # 通知消息不处理
            if message.is_notify:
                logger.debug("通知消息，跳过处理")
                return

            # 1. 消息解析与初始化
            userinfo = message.message_info.user_info
            chat = message.chat_stream

            # 2. 计算at信息
            is_mentioned, is_at, reply_probability_boost = is_mentioned_bot_in_message(message)
            # print(f"is_mentioned: {is_mentioned}, is_at: {is_at}, reply_probability_boost: {reply_probability_boost}")
            message.is_mentioned = is_mentioned
            message.is_at = is_at
            message.reply_probability_boost = reply_probability_boost

            await self.storage.store_message(message, chat)

            await heartflow.get_or_create_heartflow_chat(chat.stream_id)  # type: ignore

            # 3. 日志记录
            mes_name = chat.group_info.group_name if chat.group_info else "私聊"

            # 用这个pattern截取出id部分，picid是一个list，并替换成对应的图片描述
            picid_pattern = r"\[picid:([^\]]+)\]"
            picid_list = re.findall(picid_pattern, message.processed_plain_text)

            # 创建替换后的文本
            processed_text = message.processed_plain_text
            if picid_list:
                for picid in picid_list:
                    image = Images.get_or_none(Images.image_id == picid)
                    if image and image.description:
                        # 将[picid:xxxx]替换成图片描述
                        processed_text = processed_text.replace(f"[picid:{picid}]", f"[图片：{image.description}]")
                    else:
                        # 如果没有找到图片描述，则移除[picid:xxxx]标记
                        processed_text = processed_text.replace(f"[picid:{picid}]", "[图片：网络不好，图片无法加载]")

            # 应用用户引用格式替换，将回复<aaa:bbb>和@<aaa:bbb>格式转换为可读格式
            processed_plain_text = replace_user_references(
                processed_text,
                message.message_info.platform,  # type: ignore
                replace_bot_name=True,
            )
            # if not processed_plain_text:
            # print(message)

            logger.info(f"[{mes_name}]{userinfo.user_nickname}:{processed_plain_text}")  # type: ignore

            # 如果是群聊，获取群号和群昵称
            group_id = None
            group_nick_name = None
            if chat.group_info:
                group_id = chat.group_info.group_id  # type: ignore
                group_nick_name = userinfo.user_cardname  # type: ignore

            _ = Person.register_person(
                platform=message.message_info.platform,  # type: ignore
                user_id=message.message_info.user_info.user_id,  # type: ignore
                nickname=userinfo.user_nickname,  # type: ignore
                group_id=group_id,
                group_nick_name=group_nick_name,
            )

        except Exception as e:
            logger.error(f"消息处理失败: {e}")
            print(traceback.format_exc())
