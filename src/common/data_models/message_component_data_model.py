from abc import ABC, abstractmethod
from copy import deepcopy
from maim_message import Seg, UserInfo, MessageBase, BaseMessageInfo
from pathlib import Path
from sqlmodel import select
from typing import Optional, List, Union, Dict, Any

import asyncio
import hashlib
import base64

from src.common.logger import get_logger

logger = get_logger("base_message_component_model")

class UnknownUser(str): ...

class BaseMessageComponentModel(ABC):
    @property
    @abstractmethod
    def format_name(self) -> str:
        """消息组件的格式名称，用于标识该组件的类型"""
        raise NotImplementedError

    @abstractmethod
    async def to_seg(self) -> Seg:
        """将消息组件转换为 maim_message.Seg 对象"""
        raise NotImplementedError

    def clone(self):
        return deepcopy(self)


class ByteComponent:
    def __init__(self, *, binary_hash: str, content: Optional[str] = None, binary_data: Optional[bytes] = None) -> None:
        self.content: str = content if content is not None else ""
        """处理后的内容"""
        self.binary_data: bytes = binary_data if binary_data is not None else b""
        """原始二进制数据"""
        self.binary_hash: str = hashlib.sha256(self.binary_data).hexdigest() if self.binary_data else binary_hash
        """二进制数据的 SHA256 哈希值，用于唯一标识该二进制数据"""


class TextComponent(BaseMessageComponentModel):
    """文本组件，包含一个文本消息的内容"""

    @property
    def format_name(self) -> str:
        return "text"

    def __init__(self, text: str):
        self.text = text
        assert isinstance(text, str), "TextComponent 的 text 必须是字符串类型"

    async def to_seg(self) -> Seg:
        return Seg(type="text", data=self.text)


class ImageComponent(BaseMessageComponentModel, ByteComponent):
    """图片组件，包含一个图片消息的二进制数据和一个唯一标识该图片消息的 hash 值"""

    @property
    def format_name(self) -> str:
        return "image"

    async def load_image_binary(self):
        if self.binary_data:
            return
        from src.common.database.database import get_db_session
        from src.common.database.database_model import Images, ImageType

        try:
            with get_db_session() as db:
                statement = select(Images).filter_by(image_hash=self.binary_hash, image_type=ImageType.IMAGE).limit(1)
                if image_record := db.exec(statement).first():
                    image_path = Path(image_record.full_path)
                else:
                    raise ValueError(f"无法通过 image_hash 加载图片二进制数据: {self.binary_hash}")
            self.binary_data = await asyncio.to_thread(image_path.read_bytes)
        except Exception as e:
            raise ValueError(f"通过 image_hash 加载图片二进制数据时发生错误: {e}") from e

    async def to_seg(self) -> Seg:
        if not self.binary_data:
            await self.load_image_binary()
        return Seg(type="image", data=base64.b64encode(self.binary_data).decode())


class EmojiComponent(BaseMessageComponentModel, ByteComponent):
    """表情组件，包含一个表情消息的二进制数据和一个唯一标识该表情消息的 hash 值"""

    @property
    def format_name(self) -> str:
        return "emoji"

    async def load_emoji_binary(self) -> None:
        """
        加载表情的二进制数据，如果 binary_data 为空，则通过 emoji_hash 从表情管理器加载

        Raises:
            ValueError: 如果 binary_data 为空且缺少 emoji_hash
            ValueError: 如果无法通过 emoji_hash 加载表情二进制数据
        """
        if self.binary_data:
            return
        from src.common.database.database import get_db_session
        from src.common.database.database_model import Images, ImageType

        try:
            with get_db_session() as db:
                statement = select(Images).filter_by(image_hash=self.binary_hash, image_type=ImageType.EMOJI).limit(1)
                if image_record := db.exec(statement).first():
                    image_path = Path(image_record.full_path)
                else:
                    raise ValueError(f"无法通过 emoji_hash 加载表情二进制数据: {self.binary_hash}")
            self.binary_data = await asyncio.to_thread(image_path.read_bytes)
        except Exception as e:
            raise ValueError(f"通过 emoji_hash 加载表情二进制数据时发生错误: {e}") from e

    async def to_seg(self) -> Seg:
        if not self.binary_data:
            await self.load_emoji_binary()
        return Seg(type="emoji", data=base64.b64encode(self.binary_data).decode())


class VoiceComponent(BaseMessageComponentModel, ByteComponent):
    """语音组件，包含一个语音消息的二进制数据和一个唯一标识该语音消息的 hash 值"""

    @property
    def format_name(self) -> str:
        return "voice"

    async def load_voice_binary(self) -> None:
        if not self.binary_data:
            from src.common.utils.utils_file import FileUtils

            try:
                file_path = FileUtils.get_file_path_by_hash(self.binary_hash)
                self.binary_data = await asyncio.to_thread(file_path.read_bytes)
            except Exception as e:
                raise ValueError(f"通过 voice_hash 加载语音二进制数据时发生错误: {e}") from e

    async def to_seg(self) -> Seg:
        if not self.binary_data:
            await self.load_voice_binary()
        return Seg(type="voice", data=base64.b64encode(self.binary_data).decode())


class AtComponent(BaseMessageComponentModel):
    """@组件，包含一个被@的用户的ID，用于表示该组件是一个@某人的消息片段"""

    @property
    def format_name(self) -> str:
        return "at"

    def __init__(
        self,
        target_user_id: str,
        target_user_nickname: Optional[str] = None,
        target_user_cardname: Optional[str] = None,
    ) -> None:
        self.target_user_id = target_user_id
        """目标用户ID"""
        self.target_user_nickname: Optional[str] = target_user_nickname
        """目标用户昵称"""
        self.target_user_cardname: Optional[str] = target_user_cardname
        """目标用户备注名"""
        assert isinstance(target_user_id, str), "AtComponent 的 target_user_id 必须是字符串类型"

    async def to_seg(self) -> Seg:
        return Seg(type="at", data=self.target_user_id)


class ReplyComponent(BaseMessageComponentModel):
    """回复组件，包含一个回复消息的 ID，用于表示该组件是对哪条消息的回复"""

    @property
    def format_name(self) -> str:
        return "reply"

    def __init__(
        self,
        target_message_id: str,
        target_message_content: Optional[str] = None,
        target_message_sender_id: Optional[str] = None,
        target_message_sender_nickname: Optional[str] = None,
        target_message_sender_cardname: Optional[str] = None,
    ) -> None:
        assert isinstance(target_message_id, str), "ReplyComponent 的 target_message_id 必须是字符串类型"
        self.target_message_id = target_message_id
        """目标消息ID"""
        self.target_message_content: Optional[str] = target_message_content
        """目标消息内容"""
        self.target_message_sender_id: Optional[str] = target_message_sender_id
        """目标消息发送者ID"""
        self.target_message_sender_nickname: Optional[str] = target_message_sender_nickname
        """目标消息发送者昵称"""
        self.target_message_sender_cardname: Optional[str] = target_message_sender_cardname
        """目标消息发送者群昵称"""

    async def to_seg(self) -> Seg:
        return Seg(type="reply", data=self.target_message_id)


class ForwardNodeComponent(BaseMessageComponentModel):
    """转发节点消息组件，包含一个转发节点的消息，所有组件按照消息顺序排列"""

    @property
    def format_name(self) -> str:
        return "forward_node"

    def __init__(self, forward_components: List["ForwardComponent"]):
        self.forward_components = forward_components
        """节点的消息组件列表，按照消息顺序排列"""
        assert isinstance(forward_components, list), "ForwardNodeComponent 的 forward_components 必须是列表类型"
        assert all(isinstance(comp, ForwardComponent) for comp in forward_components), (
            "ForwardNodeComponent 的 forward_components 列表中必须全部是 ForwardComponent 类型"
        )
        assert forward_components, "ForwardNodeComponent 的 forward_components 不能为空列表"

    async def to_seg(self) -> "Seg":
        resp: List[Dict[str, Any]] = []
        for comp in self.forward_components:
            data = await comp.to_seg()
            sender_info = UserInfo(None, comp.user_id, comp.user_nickname, comp.user_cardname)
            base_message_info = BaseMessageInfo(user_info=sender_info)
            base_message = MessageBase(base_message_info, data)
            resp.append(base_message.to_dict())
        return Seg(type="forward", data=resp)  # type: ignore


class DictComponent:
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        assert isinstance(data, dict), "DictComponent 的 data 必须是字典类型"


StandardMessageComponents = Union[
    TextComponent,
    ImageComponent,
    EmojiComponent,
    VoiceComponent,
    AtComponent,
    ReplyComponent,
    ForwardNodeComponent,
    DictComponent,
]


class ForwardComponent(BaseMessageComponentModel):
    """转发组件，包含一个转发消息中的一个节点的信息，包括发送者信息和该节点的消息内容"""

    @property
    def format_name(self) -> str:
        return "forward"

    def __init__(
        self,
        user_nickname: str | UnknownUser,
        message_id: str,
        content: List[StandardMessageComponents],
        user_id: Optional[str] = None,
        user_cardname: Optional[str] = None,
    ):
        self.user_nickname: str | UnknownUser = user_nickname
        """转发节点的发送者昵称"""
        self.message_id: str = message_id
        """转发节点的消息ID"""
        self.content: List[StandardMessageComponents] = content
        """消息内容"""
        self.user_id: Optional[str] = user_id
        """转发节点的发送者ID，可能为 None"""
        self.user_cardname: Optional[str] = user_cardname
        """转发节点的发送者群名片，可能为 None"""
        assert self.content, "ForwardComponent 的 content 不能为空"

    async def to_seg(self) -> "Seg":
        return Seg(
            type="seglist", data=[await comp.to_seg() for comp in self.content if not isinstance(comp, DictComponent)]
        )


class MessageSequence:
    """消息组件序列，包含一个消息中的所有组件，按照顺序排列"""

    def __init__(self, components: List[StandardMessageComponents]):
        """
        创建一个消息组件序列

        **消息组件序列不会对组件进行去重或校验。**

        因此同一消息中可以包含多个相同的组件（例如多个文本组件、多个图片组件等）。
        因此也可以包含多个`ReplyComponent`组件（例如回复多条消息）。
        如果需要对组件进行去重或校验，还请在使用时自行处理。
        """
        self.components: List[StandardMessageComponents] = components

    """链式调用的接口，方便在创建消息组件序列时逐步追加组件"""

    def text(self, text: str) -> "MessageSequence":
        """在消息组件序列末尾追加一个文本组件"""
        self.components.append(TextComponent(text))
        return self

    def image(self, binary_data: bytes, content: Optional[str] = None):
        """在消息组件序列末尾追加一个图片组件"""
        hash_str = hashlib.sha256(binary_data).hexdigest()
        self.components.append(ImageComponent(binary_hash=hash_str, content=content, binary_data=binary_data))
        return self

    def emoji(self, binary_data: bytes, content: Optional[str] = None):
        """在消息组件序列末尾追加一个表情组件"""
        hash_str = hashlib.sha256(binary_data).hexdigest()
        self.components.append(EmojiComponent(binary_hash=hash_str, content=content, binary_data=binary_data))
        return self

    def voice(self, binary_data: bytes, content: Optional[str] = None):
        """在消息组件序列末尾追加一个语音组件"""
        hash_str = hashlib.sha256(binary_data).hexdigest()
        self.components.append(VoiceComponent(binary_hash=hash_str, content=content, binary_data=binary_data))
        return self

    def at(self, target_user_id: str):
        """在消息组件序列末尾追加一个@组件"""
        self.components.append(AtComponent(target_user_id))
        return self

    def reply(self, target_message_id: str):
        """在消息组件序列末尾追加一个回复组件"""
        self.components.append(ReplyComponent(target_message_id=target_message_id))
        return self

    def to_dict(self) -> List[Dict[str, Any]]:
        """将消息序列转换为字典列表格式，便于存储或传输"""
        return [self._item_2_dict(comp) for comp in self.components]

    @classmethod
    def from_dict(cls, data: List[Dict[str, Any]]):
        """从字典列表格式创建消息序列实例"""
        components: List[StandardMessageComponents] = []
        components.extend(cls._dict_2_item(item) for item in data)
        return cls(components=components)

    def _item_2_dict(self, item: StandardMessageComponents) -> Dict[str, Any]:
        """内部方法：将单个消息组件转换为字典格式"""
        if isinstance(item, TextComponent):
            return {"type": "text", "data": item.text}
        elif isinstance(item, ImageComponent):
            if not item.content:
                raise RuntimeError("ImageComponent content 未初始化")
            return {"type": "image", "data": item.content, "hash": item.binary_hash}
        elif isinstance(item, EmojiComponent):
            if not item.content:
                raise RuntimeError("EmojiComponent content 未初始化")
            return {"type": "emoji", "data": item.content, "hash": item.binary_hash}
        elif isinstance(item, VoiceComponent):
            if not item.content:
                raise RuntimeError("VoiceComponent content 未初始化")
            return {"type": "voice", "data": item.content, "hash": item.binary_hash}
        elif isinstance(item, AtComponent):
            return {
                "type": "at",
                "data": {
                    "target_user_id": item.target_user_id,
                    "target_user_nickname": item.target_user_nickname,
                    "target_user_cardname": item.target_user_cardname,
                },
            }
        elif isinstance(item, ReplyComponent):
            return {"type": "reply", "data": item.target_message_id}
        elif isinstance(item, ForwardNodeComponent):
            return {
                "type": "forward",
                "data": [
                    {
                        "user_id": comp.user_id,
                        "user_nickname": comp.user_nickname,
                        "user_cardname": comp.user_cardname,
                        "message_id": comp.message_id,
                        "content": [self._item_2_dict(c) for c in comp.content],
                    }
                    for comp in item.forward_components
                ],
            }
        else:
            logger.warning(f"Unofficial component type: {type(item)}, defaulting to DictComponent")
            return {"type": "dict", "data": item.data}

    @classmethod
    def _dict_2_item(cls, item: Dict[str, Any]) -> StandardMessageComponents:
        """内部方法：将单个消息组件的字典格式转换回组件对象"""
        item_type = item.get("type")
        if item_type == "text":
            return TextComponent(text=item["data"])
        elif item_type == "image":
            return ImageComponent(binary_hash=item["hash"], content=item["data"])
        elif item_type == "emoji":
            return EmojiComponent(binary_hash=item["hash"], content=item["data"])
        elif item_type == "voice":
            return VoiceComponent(binary_hash=item["hash"], content=item["data"])
        elif item_type == "at":
            return AtComponent(
                target_user_id=item["data"]["target_user_id"],
                target_user_nickname=item["data"].get("target_user_nickname"),
                target_user_cardname=item["data"].get("target_user_cardname"),
            )
        elif item_type == "reply":
            return ReplyComponent(target_message_id=item["data"])
        elif item_type == "forward":
            forward_components = []
            for fc in item["data"]:
                content = [cls._dict_2_item(c) for c in fc["content"]]
                forward_component = ForwardComponent(
                    user_nickname=fc["user_nickname"],
                    user_id=fc.get("user_id"),
                    user_cardname=fc.get("user_cardname"),
                    message_id=fc.get("message_id"),
                    content=content,
                )
                forward_components.append(forward_component)
            return ForwardNodeComponent(forward_components=forward_components)
        else:
            logger.warning(f"Unofficial component type in dict: {item_type}, defaulting to DictComponent")
            return DictComponent(data=item.get("data") or {})
