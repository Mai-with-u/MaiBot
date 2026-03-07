from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, List, Optional, Tuple, Union

from . import BaseDataModel


class ReplyContentType(Enum):
    TEXT = "text"
    IMAGE = "image"
    EMOJI = "emoji"
    COMMAND = "command"
    VOICE = "voice"
    HYBRID = "hybrid"
    FORWARD = "forward"

    def __str__(self) -> str:
        return self.value


@dataclass
class ReplyContent:
    content_type: ReplyContentType | str
    content: Any


@dataclass
class ForwardNode:
    user_id: Optional[str] = None
    user_nickname: Optional[str] = None
    content: Union[str, List[ReplyContent], None] = None

    @classmethod
    def construct_as_id_reference(cls, message_id: str) -> "ForwardNode":
        return cls(content=message_id)

    @classmethod
    def construct_as_created_node(
        cls,
        user_id: str,
        user_nickname: str,
        content: List[ReplyContent],
    ) -> "ForwardNode":
        return cls(user_id=user_id, user_nickname=user_nickname, content=content)


class ReplySetModel(BaseDataModel):
    def __init__(self) -> None:
        self.reply_data: List[ReplyContent] = []

    def __len__(self) -> int:
        return len(self.reply_data)

    def add_text_content(self, text: str) -> None:
        self.reply_data.append(ReplyContent(content_type=ReplyContentType.TEXT, content=text))

    def add_voice_content(self, voice_base64: str) -> None:
        self.reply_data.append(ReplyContent(content_type=ReplyContentType.VOICE, content=voice_base64))

    def add_hybrid_content_by_raw(self, message_tuple_list: Iterable[Tuple[ReplyContentType | str, str]]) -> None:
        hybrid_contents: List[ReplyContent] = []
        for content_type, content in message_tuple_list:
            hybrid_contents.append(
                ReplyContent(content_type=self._normalize_content_type(content_type), content=content)
            )
        self.reply_data.append(ReplyContent(content_type=ReplyContentType.HYBRID, content=hybrid_contents))

    def add_forward_content(self, forward_nodes: List[ForwardNode]) -> None:
        self.reply_data.append(ReplyContent(content_type=ReplyContentType.FORWARD, content=forward_nodes))

    @staticmethod
    def _normalize_content_type(content_type: ReplyContentType | str) -> ReplyContentType | str:
        if isinstance(content_type, ReplyContentType):
            return content_type
        if isinstance(content_type, str):
            for item in ReplyContentType:
                if item.value == content_type:
                    return item
        return content_type
