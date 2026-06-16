import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from maim_message import (
    BaseMessageInfo as MaimBaseMessageInfo,
    GroupInfo as MaimGroupInfo,
    MessageBase,
    ReceiverInfo as MaimReceiverInfo,
    Seg,
    SenderInfo as MaimSenderInfo,
    UserInfo as MaimUserInfo,
)

from src.common.database.database_model import Messages
from src.common.data_models.message_component_data_model import MessageSequence
from src.common.utils.utils_message import MessageUtils

from . import BaseDatabaseDataModel


@dataclass
class UserInfo:
    user_id: str
    user_nickname: str
    user_cardname: Optional[str] = None


@dataclass
class GroupInfo:
    group_id: str
    group_name: str


@dataclass
class MessageInfo:
    user_info: UserInfo
    group_info: Optional[GroupInfo] = None
    additional_config: dict = field(default_factory=dict)


class MaiMessage(BaseDatabaseDataModel[Messages]):
    def __init__(self, message_id: str, timestamp: datetime, platform: str):
        self.message_id: str = message_id
        self.timestamp: datetime = timestamp
        self.initialized = False
        self.platform: str = platform

        self.message_info: MessageInfo
        self.is_mentioned: bool = False
        self.is_at: bool = False
        self.is_emoji: bool = False
        self.is_picture: bool = False
        self.is_command: bool = False
        self.is_notify: bool = False

        self.session_id: str
        self.reply_to: Optional[str] = None
        self.reply_frequency: Optional[float] = None

        self.processed_plain_text: Optional[str] = None
        self.raw_message: MessageSequence

    @classmethod
    def from_db_instance(cls, db_record: "Messages"):
        obj = cls(message_id=db_record.message_id, timestamp=db_record.timestamp, platform=db_record.platform)

        user_info = UserInfo(db_record.user_id, db_record.user_nickname, db_record.user_cardname)
        if db_record.group_id and db_record.group_name:
            group_info = GroupInfo(db_record.group_id, db_record.group_name)
        else:
            group_info = None

        obj.message_info = MessageInfo(
            user_info=user_info,
            group_info=group_info,
            additional_config=json.loads(db_record.additional_config) if db_record.additional_config else {},
        )
        obj.is_mentioned = db_record.is_mentioned
        obj.is_at = db_record.is_at
        obj.is_emoji = db_record.is_emoji
        obj.is_picture = db_record.is_picture
        obj.is_command = db_record.is_command
        obj.is_notify = db_record.is_notify
        obj.reply_to = db_record.reply_to
        obj.reply_frequency = getattr(db_record, "reply_frequency", None)
        obj.session_id = db_record.session_id
        obj.processed_plain_text = db_record.processed_plain_text
        obj.raw_message = MessageUtils.from_db_record_msg_to_MaiSeq(db_record.raw_content)
        return obj

    def to_db_instance(self) -> Messages:
        additional_config = (
            json.dumps(self.message_info.additional_config) if self.message_info.additional_config else None
        )
        return Messages(
            message_id=self.message_id,
            timestamp=self.timestamp,
            platform=self.platform,
            user_id=self.message_info.user_info.user_id,
            user_nickname=self.message_info.user_info.user_nickname,
            user_cardname=self.message_info.user_info.user_cardname,
            group_id=self.message_info.group_info.group_id if self.message_info.group_info else None,
            group_name=self.message_info.group_info.group_name if self.message_info.group_info else None,
            is_mentioned=self.is_mentioned,
            is_at=self.is_at,
            session_id=self.session_id,
            reply_to=self.reply_to,
            is_emoji=self.is_emoji,
            is_picture=self.is_picture,
            is_command=self.is_command,
            is_notify=self.is_notify,
            raw_content=MessageUtils.from_MaiSeq_to_db_record_msg(self.raw_message),
            processed_plain_text=self.processed_plain_text,
            additional_config=additional_config,
            reply_frequency=self.reply_frequency,
        )

    @classmethod
    def from_maim_message(cls, message: MessageBase):
        """从 maim_message.MessageBase 创建 MaiMessage。"""
        msg_info = message.message_info
        assert msg_info, "MessageBase 的 message_info 不能为空"

        platform = msg_info.platform
        assert isinstance(platform, str)

        msg_id = str(msg_info.message_id)
        timestamp = msg_info.time
        assert isinstance(msg_id, str)
        assert msg_id
        assert timestamp

        obj = cls(message_id=msg_id, timestamp=datetime.fromtimestamp(timestamp), platform=platform)
        obj.raw_message = MessageUtils.from_maim_message_segments_to_MaiSeq(message)

        usr_info = msg_info.user_info
        assert usr_info
        assert isinstance(usr_info.user_id, str)
        assert isinstance(usr_info.user_nickname, str)
        user_info = UserInfo(
            user_id=usr_info.user_id,
            user_nickname=usr_info.user_nickname,
            user_cardname=usr_info.user_cardname,
        )

        if msg_info.group_info:
            grp_info = msg_info.group_info
            assert isinstance(grp_info.group_id, str)
            assert isinstance(grp_info.group_name, str)
            group_info = GroupInfo(group_id=grp_info.group_id, group_name=grp_info.group_name)
        else:
            group_info = None

        add_cfg = msg_info.additional_config or {}
        obj.message_info = MessageInfo(user_info=user_info, group_info=group_info, additional_config=add_cfg)
        return obj

    async def to_maim_message(self) -> MessageBase:
        """将 MaiMessage 转换为 maim_message.MessageBase。"""
        sender_user_info = MaimUserInfo(
            user_id=self.message_info.user_info.user_id,
            user_nickname=self.message_info.user_info.user_nickname,
            user_cardname=self.message_info.user_info.user_cardname,
            platform=self.platform,
        )

        sender_group_info = None
        if self.message_info.group_info:
            sender_group_info = MaimGroupInfo(
                group_id=self.message_info.group_info.group_id,
                group_name=self.message_info.group_info.group_name,
                platform=self.platform,
            )

        sender_info = MaimSenderInfo(
            group_info=sender_group_info,
            user_info=sender_user_info,
        )

        receiver_group_info = sender_group_info
        receiver_user_info = None
        additional_config = self.message_info.additional_config or {}
        target_user_id = str(additional_config.get("platform_io_target_user_id") or "").strip()
        if receiver_group_info is None and target_user_id:
            receiver_user_info = MaimUserInfo(
                user_id=target_user_id,
                user_nickname=None,
                user_cardname=None,
                platform=self.platform,
            )

        receiver_info = None
        if receiver_group_info or receiver_user_info:
            receiver_info = MaimReceiverInfo(
                group_info=receiver_group_info,
                user_info=receiver_user_info,
            )

        # 私聊消息：适配器用 user_info.user_id 作为收件人 QQ
        # 发件人信息在 sender_info 中，收件人在 receiver_info 中
        effective_user_info = sender_user_info
        if receiver_group_info is None and receiver_user_info is not None:
            effective_user_info = receiver_user_info

        maim_msg_info = MaimBaseMessageInfo(
            platform=self.platform,
            message_id=self.message_id,
            time=self.timestamp.timestamp(),
            group_info=receiver_group_info,
            user_info=effective_user_info,
            additional_config=self.message_info.additional_config,
            sender_info=sender_info,
            receiver_info=receiver_info,
        )
        msg_segments = await MessageUtils.from_MaiSeq_to_maim_message_segments(self.raw_message)
        return MessageBase(message_info=maim_msg_info, message_segment=Seg(type="seglist", data=msg_segments))
