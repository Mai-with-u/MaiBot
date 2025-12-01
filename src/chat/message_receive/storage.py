import re
import json
import traceback
from typing import Union

from src.common.database.database_model import Messages, Images
from src.common.logger import get_logger
from .chat_stream import ChatStream
from .message import MessageSending, MessageRecv

logger = get_logger("message_storage")


class MessageStorage:
    @staticmethod
    def _serialize_keywords(keywords) -> str:
        """将关键词列表序列化为JSON字符串"""
        if isinstance(keywords, list):
            return json.dumps(keywords, ensure_ascii=False)
        return "[]"

    @staticmethod
    def _deserialize_keywords(keywords_str: str) -> list:
        """将JSON字符串反序列化为关键词列表"""
        if not keywords_str:
            return []
        try:
            return json.loads(keywords_str)
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    async def store_message(message: Union[MessageSending, MessageRecv], chat_stream: ChatStream) -> None:
        """存储消息到数据库"""
        try:
            # 通知消息不存储
            if isinstance(message, MessageRecv) and message.is_notify:
                logger.debug("通知消息，跳过存储")
                return

            pattern = r"<MainRule>.*?</MainRule>|<schedule>.*?</schedule>|<UserMessage>.*?</UserMessage>"

            # print(message)

            processed_plain_text = message.processed_plain_text

            # print(processed_plain_text)

            if processed_plain_text:
                processed_plain_text = MessageStorage.replace_image_descriptions(processed_plain_text)
                filtered_processed_plain_text = re.sub(pattern, "", processed_plain_text, flags=re.DOTALL)
            else:
                filtered_processed_plain_text = ""

            if isinstance(message, MessageSending):
                display_message = message.display_message
                if display_message:
                    filtered_display_message = re.sub(pattern, "", display_message, flags=re.DOTALL)
                else:
                    filtered_display_message = ""
                interest_value = 0
                is_mentioned = False
                is_at = False
                reply_probability_boost = 0.0
                reply_to = message.reply_to
                priority_mode = ""
                priority_info = {}
                is_emoji = False
                is_picid = False
                is_notify = False
                is_command = False
                key_words = ""
                key_words_lite = ""
                selected_expressions = message.selected_expressions
                is_no_read_command = False
            else:
                filtered_display_message = ""
                interest_value = message.interest_value
                is_mentioned = message.is_mentioned
                is_at = message.is_at
                reply_probability_boost = message.reply_probability_boost
                reply_to = ""
                priority_mode = message.priority_mode
                priority_info = message.priority_info
                is_emoji = message.is_emoji
                is_picid = message.is_picid
                is_notify = message.is_notify
                is_command = message.is_command
                is_no_read_command = getattr(message, "is_no_read_command", False)
                # 序列化关键词列表为JSON字符串
                key_words = MessageStorage._serialize_keywords(message.key_words)
                key_words_lite = MessageStorage._serialize_keywords(message.key_words_lite)
                selected_expressions = ""

            chat_info_dict = chat_stream.to_dict()
            user_info_dict = message.message_info.user_info.to_dict()  # type: ignore

            # message_id 现在是 TextField，直接使用字符串值
            msg_id = message.message_info.message_id

            # 安全地获取 group_info, 如果为 None 则视为空字典
            group_info_from_chat = chat_info_dict.get("group_info") or {}
            # 安全地获取 user_info, 如果为 None 则视为空字典 (以防万一)
            user_info_from_chat = chat_info_dict.get("user_info") or {}

            Messages.create(
                message_id=msg_id,
                time=float(message.message_info.time),  # type: ignore
                chat_id=chat_stream.stream_id,
                # Flattened chat_info
                reply_to=reply_to,
                is_mentioned=is_mentioned,
                is_at=is_at,
                reply_probability_boost=reply_probability_boost,
                chat_info_stream_id=chat_info_dict.get("stream_id"),
                chat_info_platform=chat_info_dict.get("platform"),
                chat_info_user_platform=user_info_from_chat.get("platform"),
                chat_info_user_id=user_info_from_chat.get("user_id"),
                chat_info_user_nickname=user_info_from_chat.get("user_nickname"),
                chat_info_user_cardname=user_info_from_chat.get("user_cardname"),
                chat_info_group_platform=group_info_from_chat.get("platform"),
                chat_info_group_id=group_info_from_chat.get("group_id"),
                chat_info_group_name=group_info_from_chat.get("group_name"),
                chat_info_create_time=float(chat_info_dict.get("create_time", 0.0)),
                chat_info_last_active_time=float(chat_info_dict.get("last_active_time", 0.0)),
                # Flattened user_info (message sender)
                user_platform=user_info_dict.get("platform"),
                user_id=user_info_dict.get("user_id"),
                user_nickname=user_info_dict.get("user_nickname"),
                user_cardname=user_info_dict.get("user_cardname"),
                # Text content
                processed_plain_text=filtered_processed_plain_text,
                display_message=filtered_display_message,
                interest_value=interest_value,
                priority_mode=priority_mode,
                priority_info=priority_info,
                is_emoji=is_emoji,
                is_picid=is_picid,
                is_notify=is_notify,
                is_command=is_command,
                is_no_read_command=is_no_read_command,
                key_words=key_words,
                key_words_lite=key_words_lite,
                selected_expressions=selected_expressions,
            )
        except Exception:
            logger.exception("存储消息失败")
            logger.error(f"消息：{message}")
            traceback.print_exc()

    # 如果需要其他存储相关的函数，可以在这里添加
    @staticmethod
    def update_message(mmc_message_id: str | None, qq_message_id: str | None) -> bool:
        """实时更新数据库的自身发送消息ID"""
        try:
            if not qq_message_id:
                logger.info("消息不存在message_id，无法更新")
                return False
            if matched_message := (
                Messages.select().where((Messages.message_id == mmc_message_id)).order_by(Messages.time.desc()).first()
            ):
                # 更新找到的消息记录
                Messages.update(message_id=qq_message_id).where(Messages.id == matched_message.id).execute()  # type: ignore
                logger.debug(f"更新消息ID成功: {matched_message.message_id} -> {qq_message_id}")
                return True
            else:
                logger.debug("未找到匹配的消息")
                return False

        except Exception as e:
            logger.error(f"更新消息ID失败: {e}")
            return False

    @staticmethod
    def replace_image_descriptions(text: str) -> str:
        """将[图片：描述]替换为[picid:image_id]"""
        # 先检查文本中是否有图片标记
        pattern = r"\[图片：([^\]]+)\]"
        matches = re.findall(pattern, text)

        if not matches:
            logger.debug("文本中没有图片标记，直接返回原文本")
            return text

        def replace_match(match):
            description = match.group(1).strip()
            try:
                image_record = (
                    Images.select().where(Images.description == description).order_by(Images.timestamp.desc()).first()
                )
                return f"[picid:{image_record.image_id}]" if image_record else match.group(0)
            except Exception:
                return match.group(0)

        return re.sub(r"\[图片：([^\]]+)\]", replace_match, text)
