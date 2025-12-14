import json
from typing import Optional, Any, Dict
from dataclasses import dataclass, field

from . import BaseDataModel


@dataclass
class DatabaseUserInfo(BaseDataModel):
    platform: str = field(default_factory=str)
    user_id: str = field(default_factory=str)
    user_nickname: str = field(default_factory=str)
    user_cardname: Optional[str] = None

    # def __post_init__(self):
    #     assert isinstance(self.platform, str), "platform must be a string"
    #     assert isinstance(self.user_id, str), "user_id must be a string"
    #     assert isinstance(self.user_nickname, str), "user_nickname must be a string"
    #     assert isinstance(self.user_cardname, str) or self.user_cardname is None, (
    #         "user_cardname must be a string or None"
    #     )


@dataclass
class DatabaseGroupInfo(BaseDataModel):
    group_id: str = field(default_factory=str)
    group_name: str = field(default_factory=str)
    group_platform: Optional[str] = None

    # def __post_init__(self):
    #     assert isinstance(self.group_id, str), "group_id must be a string"
    #     assert isinstance(self.group_name, str), "group_name must be a string"
    #     assert isinstance(self.group_platform, str) or self.group_platform is None, (
    #         "group_platform must be a string or None"
    #     )


@dataclass
class DatabaseChatInfo(BaseDataModel):
    stream_id: str = field(default_factory=str)
    platform: str = field(default_factory=str)
    create_time: float = field(default_factory=float)
    last_active_time: float = field(default_factory=float)
    user_info: DatabaseUserInfo = field(default_factory=DatabaseUserInfo)
    group_info: Optional[DatabaseGroupInfo] = None

    # def __post_init__(self):
    #     assert isinstance(self.stream_id, str), "stream_id must be a string"
    #     assert isinstance(self.platform, str), "platform must be a string"
    #     assert isinstance(self.create_time, float), "create_time must be a float"
    #     assert isinstance(self.last_active_time, float), "last_active_time must be a float"
    #     assert isinstance(self.user_info, DatabaseUserInfo), "user_info must be a DatabaseUserInfo instance"
    #     assert isinstance(self.group_info, DatabaseGroupInfo) or self.group_info is None, (
    #         "group_info must be a DatabaseGroupInfo instance or None"
    #     )


@dataclass(init=False)
class DatabaseMessages(BaseDataModel):
    def __init__(
        self,
        message_id: str = "",
        time: float = 0.0,
        chat_id: str = "",
        reply_to: Optional[str] = None,
        interest_value: Optional[float] = None,
        key_words: Optional[str] = None,
        key_words_lite: Optional[str] = None,
        is_mentioned: Optional[bool] = None,
        is_at: Optional[bool] = None,
        reply_probability_boost: Optional[float] = None,
        processed_plain_text: Optional[str] = None,
        display_message: Optional[str] = None,
        priority_mode: Optional[str] = None,
        priority_info: Optional[str] = None,
        additional_config: Optional[str] = None,
        is_emoji: bool = False,
        is_picid: bool = False,
        is_command: bool = False,
        intercept_message_level: int = 0,
        is_notify: bool = False,
        selected_expressions: Optional[str] = None,
        user_id: str = "",
        user_nickname: str = "",
        user_cardname: Optional[str] = None,
        user_platform: str = "",
        chat_info_group_id: Optional[str] = None,
        chat_info_group_name: Optional[str] = None,
        chat_info_group_platform: Optional[str] = None,
        chat_info_user_id: str = "",
        chat_info_user_nickname: str = "",
        chat_info_user_cardname: Optional[str] = None,
        chat_info_user_platform: str = "",
        chat_info_stream_id: str = "",
        chat_info_platform: str = "",
        chat_info_create_time: float = 0.0,
        chat_info_last_active_time: float = 0.0,
        **kwargs: Any,
    ):
        self.message_id = message_id
        self.time = time
        self.chat_id = chat_id
        self.reply_to = reply_to
        self.interest_value = interest_value

        self.key_words = key_words
        self.key_words_lite = key_words_lite
        self.is_mentioned = is_mentioned

        self.is_at = is_at
        self.reply_probability_boost = reply_probability_boost

        self.processed_plain_text = processed_plain_text
        self.display_message = display_message

        self.priority_mode = priority_mode
        self.priority_info = priority_info

        self.additional_config = additional_config
        self.is_emoji = is_emoji
        self.is_picid = is_picid
        self.is_command = is_command
        self.intercept_message_level = intercept_message_level
        self.is_notify = is_notify

        self.selected_expressions = selected_expressions

        self.group_info: Optional[DatabaseGroupInfo] = None
        self.user_info = DatabaseUserInfo(
            user_id=user_id,
            user_nickname=user_nickname,
            user_cardname=user_cardname,
            platform=user_platform,
        )
        if chat_info_group_id and chat_info_group_name:
            self.group_info = DatabaseGroupInfo(
                group_id=chat_info_group_id,
                group_name=chat_info_group_name,
                group_platform=chat_info_group_platform,
            )

        self.chat_info = DatabaseChatInfo(
            stream_id=chat_info_stream_id,
            platform=chat_info_platform,
            create_time=chat_info_create_time,
            last_active_time=chat_info_last_active_time,
            user_info=DatabaseUserInfo(
                user_id=chat_info_user_id,
                user_nickname=chat_info_user_nickname,
                user_cardname=chat_info_user_cardname,
                platform=chat_info_user_platform,
            ),
            group_info=self.group_info,
        )

        if kwargs:
            for key, value in kwargs.items():
                setattr(self, key, value)

    # def __post_init__(self):
    #     assert isinstance(self.message_id, str), "message_id must be a string"
    #     assert isinstance(self.time, float), "time must be a float"
    #     assert isinstance(self.chat_id, str), "chat_id must be a string"
    #     assert isinstance(self.reply_to, str) or self.reply_to is None, "reply_to must be a string or None"
    #     assert isinstance(self.interest_value, float) or self.interest_value is None, (
    #         "interest_value must be a float or None"
    #     )
    def flatten(self) -> Dict[str, Any]:
        """
        将消息数据模型转换为字典格式，便于存储或传输
        """
        return {
            "message_id": self.message_id,
            "time": self.time,
            "chat_id": self.chat_id,
            "reply_to": self.reply_to,
            "interest_value": self.interest_value,
            "key_words": self.key_words,
            "key_words_lite": self.key_words_lite,
            "is_mentioned": self.is_mentioned,
            "is_at": self.is_at,
            "reply_probability_boost": self.reply_probability_boost,
            "processed_plain_text": self.processed_plain_text,
            "display_message": self.display_message,
            "priority_mode": self.priority_mode,
            "priority_info": self.priority_info,
            "additional_config": self.additional_config,
            "is_emoji": self.is_emoji,
            "is_picid": self.is_picid,
            "is_command": self.is_command,
            "intercept_message_level": self.intercept_message_level,
            "is_notify": self.is_notify,
            "selected_expressions": self.selected_expressions,
            "user_id": self.user_info.user_id,
            "user_nickname": self.user_info.user_nickname,
            "user_cardname": self.user_info.user_cardname,
            "user_platform": self.user_info.platform,
            "chat_info_group_id": self.group_info.group_id if self.group_info else None,
            "chat_info_group_name": self.group_info.group_name if self.group_info else None,
            "chat_info_group_platform": self.group_info.group_platform if self.group_info else None,
            "chat_info_stream_id": self.chat_info.stream_id,
            "chat_info_platform": self.chat_info.platform,
            "chat_info_create_time": self.chat_info.create_time,
            "chat_info_last_active_time": self.chat_info.last_active_time,
            "chat_info_user_platform": self.chat_info.user_info.platform,
            "chat_info_user_id": self.chat_info.user_info.user_id,
            "chat_info_user_nickname": self.chat_info.user_info.user_nickname,
            "chat_info_user_cardname": self.chat_info.user_info.user_cardname,
        }


@dataclass(init=False)
class DatabaseActionRecords(BaseDataModel):
    def __init__(
        self,
        action_id: str,
        time: float,
        action_name: str,
        action_data: str,
        action_done: bool,
        action_build_into_prompt: bool,
        action_prompt_display: str,
        chat_id: str,
        chat_info_stream_id: str,
        chat_info_platform: str,
        action_reasoning: str,
    ):
        self.action_id = action_id
        self.time = time
        self.action_name = action_name
        if isinstance(action_data, str):
            self.action_data = json.loads(action_data)
        else:
            raise ValueError("action_data must be a JSON string")
        self.action_done = action_done
        self.action_build_into_prompt = action_build_into_prompt
        self.action_prompt_display = action_prompt_display
        self.chat_id = chat_id
        self.chat_info_stream_id = chat_info_stream_id
        self.chat_info_platform = chat_info_platform
        self.action_reasoning = action_reasoning
