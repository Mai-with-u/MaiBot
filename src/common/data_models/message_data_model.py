from typing import Optional, TYPE_CHECKING, List, Tuple, Union, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from . import BaseDataModel

if TYPE_CHECKING:
    from .database_data_model import DatabaseMessages


@dataclass
class MessageAndActionModel(BaseDataModel):
    chat_id: str = field(default_factory=str)
    time: float = field(default_factory=float)
    user_id: str = field(default_factory=str)
    user_platform: str = field(default_factory=str)
    user_nickname: str = field(default_factory=str)
    user_cardname: Optional[str] = None
    processed_plain_text: Optional[str] = None
    display_message: Optional[str] = None
    chat_info_platform: str = field(default_factory=str)
    is_action_record: bool = field(default=False)
    action_name: Optional[str] = None
    is_command: bool = field(default=False)
    intercept_message_level: int = field(default=0)

    @classmethod
    def from_DatabaseMessages(cls, message: "DatabaseMessages"):
        return cls(
            chat_id=message.chat_id,
            time=message.time,
            user_id=message.user_info.user_id,
            user_platform=message.user_info.platform,
            user_nickname=message.user_info.user_nickname,
            user_cardname=message.user_info.user_cardname,
            processed_plain_text=message.processed_plain_text,
            display_message=message.display_message,
            chat_info_platform=message.chat_info.platform,
            is_command=message.is_command,
            intercept_message_level=getattr(message, "intercept_message_level", 0),
        )


class ReplyContentType(Enum):
    TEXT = "text"
    IMAGE = "image"
    EMOJI = "emoji"
    COMMAND = "command"
    VOICE = "voice"
    FORWARD = "forward"
    HYBRID = "hybrid"  # 混合类型，包含多种内容

    def __repr__(self) -> str:
        return self.value


@dataclass
class ForwardNode(BaseDataModel):
    user_id: Optional[str] = None
    user_nickname: Optional[str] = None
    content: Union[List["ReplyContent"], str] = field(default_factory=list)

    @classmethod
    def construct_as_id_reference(cls, message_id: str) -> "ForwardNode":
        return cls(user_id="", user_nickname="", content=message_id)

    @classmethod
    def construct_as_created_node(
        cls, user_id: str, user_nickname: str, content: List["ReplyContent"]
    ) -> "ForwardNode":
        return cls(user_id=user_id, user_nickname=user_nickname, content=content)


@dataclass
class ReplyContent(BaseDataModel):
    content_type: ReplyContentType | str
    content: Union[str, Dict, List[ForwardNode], List["ReplyContent"]]  # 支持嵌套的 ReplyContent

    @classmethod
    def construct_as_text(cls, text: str):
        return cls(content_type=ReplyContentType.TEXT, content=text)

    @classmethod
    def construct_as_image(cls, image_base64: str):
        return cls(content_type=ReplyContentType.IMAGE, content=image_base64)

    @classmethod
    def construct_as_voice(cls, voice_base64: str):
        return cls(content_type=ReplyContentType.VOICE, content=voice_base64)

    @classmethod
    def construct_as_emoji(cls, emoji_str: str):
        return cls(content_type=ReplyContentType.EMOJI, content=emoji_str)

    @classmethod
    def construct_as_command(cls, command_arg: Dict):
        return cls(content_type=ReplyContentType.COMMAND, content=command_arg)

    @classmethod
    def construct_as_hybrid(cls, hybrid_content: List[Tuple[ReplyContentType | str, str]]):
        hybrid_content_list: List[ReplyContent] = []
        for content_type, content in hybrid_content:
            assert content_type not in [
                ReplyContentType.HYBRID,
                ReplyContentType.FORWARD,
                ReplyContentType.VOICE,
                ReplyContentType.COMMAND,
            ], "混合内容的每个项不能是混合、转发、语音或命令类型"
            assert isinstance(content, str), "混合内容的每个项必须是字符串"
            hybrid_content_list.append(ReplyContent(content_type=content_type, content=content))
        return cls(content_type=ReplyContentType.HYBRID, content=hybrid_content_list)

    @classmethod
    def construct_as_forward(cls, forward_nodes: List[ForwardNode]):
        return cls(content_type=ReplyContentType.FORWARD, content=forward_nodes)

    def __post_init__(self):
        if isinstance(self.content_type, ReplyContentType):
            if self.content_type not in [ReplyContentType.HYBRID, ReplyContentType.FORWARD] and isinstance(
                self.content, List
            ):
                raise ValueError(
                    f"非混合类型/转发类型的内容不能是列表，content_type: {self.content_type}, content: {self.content}"
                )
            elif self.content_type in [ReplyContentType.HYBRID, ReplyContentType.FORWARD]:
                if not isinstance(self.content, List):
                    raise ValueError(
                        f"混合类型/转发类型的内容必须是列表，content_type: {self.content_type}, content: {self.content}"
                    )


@dataclass
class ReplySetModel(BaseDataModel):
    """
    回复集数据模型，用于多种回复类型的返回
    """

    reply_data: List[ReplyContent] = field(default_factory=list)

    def __len__(self):
        return len(self.reply_data)

    def add_text_content(self, text: str):
        """
        添加文本内容
        Args:
            text: 文本内容
        """
        self.reply_data.append(ReplyContent(content_type=ReplyContentType.TEXT, content=text))

    def add_image_content(self, image_base64: str):
        """
        添加图片内容，base64编码的图片数据
        Args:
            image_base64: base64编码的图片数据
        """
        self.reply_data.append(ReplyContent(content_type=ReplyContentType.IMAGE, content=image_base64))

    def add_voice_content(self, voice_base64: str):
        """
        添加语音内容，base64编码的音频数据
        Args:
            voice_base64: base64编码的音频数据
        """
        self.reply_data.append(ReplyContent(content_type=ReplyContentType.VOICE, content=voice_base64))

    def add_hybrid_content_by_raw(self, hybrid_content: List[Tuple[ReplyContentType | str, str]]):
        """
        添加混合型内容，可以包含text, image, emoji的任意组合
        Args:
            hybrid_content: 元组 (类型, 消息内容) 构成的列表，如[(ReplyContentType.TEXT, "Hello"), (ReplyContentType.IMAGE, "<base64")]
        """
        hybrid_content_list: List[ReplyContent] = []
        for content_type, content in hybrid_content:
            assert content_type not in [
                ReplyContentType.HYBRID,
                ReplyContentType.FORWARD,
                ReplyContentType.VOICE,
                ReplyContentType.COMMAND,
            ], "混合内容的每个项不能是混合、转发、语音或命令类型"
            assert isinstance(content, str), "混合内容的每个项必须是字符串"
            hybrid_content_list.append(ReplyContent(content_type=content_type, content=content))

        self.reply_data.append(ReplyContent(content_type=ReplyContentType.HYBRID, content=hybrid_content_list))

    def add_hybrid_content(self, hybrid_content: List[ReplyContent]):
        """
        添加混合型内容，使用已经构造好的 ReplyContent 列表
        Args:
            hybrid_content: ReplyContent 构成的列表，如[ReplyContent(ReplyContentType.TEXT, "Hello"), ReplyContent(ReplyContentType.IMAGE, "<base64")]
        """
        for content in hybrid_content:
            assert content.content_type not in [
                ReplyContentType.HYBRID,
                ReplyContentType.FORWARD,
                ReplyContentType.VOICE,
                ReplyContentType.COMMAND,
            ], "混合内容的每个项不能是混合、转发、语音或命令类型"
            assert isinstance(content.content, str), "混合内容的每个项必须是字符串"

        self.reply_data.append(ReplyContent(content_type=ReplyContentType.HYBRID, content=hybrid_content))

    def add_custom_content(self, content_type: str, content: Any):
        """
        添加自定义类型的内容"""
        self.reply_data.append(ReplyContent(content_type=content_type, content=content))

    def add_forward_content(self, forward_content: List[ForwardNode]):
        """添加转发内容，可以是字符串或ReplyContent，嵌套的转发内容需要自己构造放入"""
        self.reply_data.append(ReplyContent(content_type=ReplyContentType.FORWARD, content=forward_content))
