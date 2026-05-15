from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple

from .tool_option import ToolCall


class RoleType(str, Enum):
    """消息角色类型。"""

    System = "system"
    User = "user"
    Assistant = "assistant"
    Tool = "tool"


SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png", "webp", "gif"]
"""默认支持的图片格式列表。"""


@dataclass(slots=True)
class TextMessagePart:
    """文本消息片段。"""

    text: str

    def __post_init__(self) -> None:
        """执行文本片段的基础校验。

        Raises:
            ValueError: 当文本为空时抛出。
        """
        if self.text == "":
            raise ValueError("文本消息片段不能为空字符串")


@dataclass(slots=True)
class ImageMessagePart:
    """Base64 图片消息片段。"""

    image_format: str
    image_base64: str

    def __post_init__(self) -> None:
        """执行图片片段的基础校验。

        Raises:
            ValueError: 当图片格式或 Base64 数据无效时抛出。
        """
        if self.image_format.lower() not in SUPPORTED_IMAGE_FORMATS:
            raise ValueError("不受支持的图片格式")
        if not self.image_base64:
            raise ValueError("图片的 base64 编码不能为空")

    @property
    def normalized_image_format(self) -> str:
        """获取规范化后的图片格式。

        Returns:
            str: 规范化后的图片格式。`jpg` 会被统一为 `jpeg`。
        """
        image_format = self.image_format.lower()
        if image_format in {"jpg", "jpeg"}:
            return "jpeg"
        return image_format


MessagePart = TextMessagePart | ImageMessagePart


@dataclass(slots=True)
class Message:
    """统一消息模型。"""

    role: RoleType
    parts: List[MessagePart] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: List[ToolCall] | None = None
    reasoning_content: str | None = None

    def __post_init__(self) -> None:
        """执行消息对象的基础校验。

        Raises:
            ValueError: 当消息内容或工具调用信息不完整时抛出。
        """
        if not self.parts and not (self.role == RoleType.Assistant and self.tool_calls):
            raise ValueError("消息内容不能为空")
        if self.role == RoleType.Tool and not self.tool_call_id:
            raise ValueError("Tool 角色的工具调用 ID 不能为空")
        if self.tool_name and self.role != RoleType.Tool:
            raise ValueError("仅当角色为 Tool 时才能设置工具名称")

    @property
    def content(self) -> str | List[Tuple[str, str] | str]:
        """获取兼容旧逻辑的内容视图。

        Returns:
            str | List[Tuple[str, str] | str]: 当仅包含一个文本片段时返回字符串，
            否则返回混合列表，其中图片片段表示为 `(format, base64)` 元组。
        """
        if len(self.parts) == 1 and isinstance(self.parts[0], TextMessagePart):
            return self.parts[0].text
        content_items: List[Tuple[str, str] | str] = []
        for part in self.parts:
            if isinstance(part, TextMessagePart):
                content_items.append(part.text)
            else:
                content_items.append((part.image_format, part.image_base64))
        return content_items

    def get_text_content(self) -> str:
        """提取消息中的所有文本片段。

        Returns:
            str: 以原始顺序拼接后的文本内容。
        """
        return "".join(part.text for part in self.parts if isinstance(part, TextMessagePart))

    def __str__(self) -> str:
        """生成便于调试的字符串表示。

        Returns:
            str: 当前消息对象的可读摘要。
        """
        return (
            f"Role: {self.role}, Parts: {self.parts}, "
            f"Tool Call ID: {self.tool_call_id}, Tool Name: {self.tool_name}, "
            f"Tool Calls: {self.tool_calls}, Reasoning Content: {self.reasoning_content}"
        )


class MessageBuilder:
    """消息构建器。"""

    def __init__(self) -> None:
        """初始化构建器。"""
        self.__role: RoleType = RoleType.User
        self.__parts: List[MessagePart] = []
        self.__tool_call_id: str | None = None
        self.__tool_name: str | None = None
        self.__tool_calls: List[ToolCall] | None = None
        self.__reasoning_content: str | None = None

    def set_role(self, role: RoleType = RoleType.User) -> "MessageBuilder":
        """设置消息角色。

        Args:
            role: 目标角色，默认为 `user`。

        Returns:
            MessageBuilder: 当前构建器实例。
        """
        self.__role = role
        return self

    def add_text_part(self, text: str) -> "MessageBuilder":
        """追加文本片段。

        Args:
            text: 文本内容。

        Returns:
            MessageBuilder: 当前构建器实例。
        """
        self.__parts.append(TextMessagePart(text=text))
        return self

    def add_text_content(self, text: str) -> "MessageBuilder":
        """追加文本片段。

        Args:
            text: 文本内容。

        Returns:
            MessageBuilder: 当前构建器实例。
        """
        return self.add_text_part(text)

    def add_image_base64_part(
        self,
        image_format: str,
        image_base64: str,
        support_formats: List[str] = SUPPORTED_IMAGE_FORMATS,
    ) -> "MessageBuilder":
        """追加 Base64 图片片段。

        Args:
            image_format: 图片格式。
            image_base64: 图片的 Base64 编码。
            support_formats: 允许的图片格式列表。

        Returns:
            MessageBuilder: 当前构建器实例。

        Raises:
            ValueError: 当图片格式不被支持时抛出。
        """
        if image_format.lower() not in support_formats:
            raise ValueError("不受支持的图片格式")
        self.__parts.append(ImageMessagePart(image_format=image_format, image_base64=image_base64))
        return self

    def add_image_content(
        self,
        image_format: str,
        image_base64: str,
        support_formats: List[str] = SUPPORTED_IMAGE_FORMATS,
    ) -> "MessageBuilder":
        """追加 Base64 图片片段。

        Args:
            image_format: 图片格式。
            image_base64: 图片的 Base64 编码。
            support_formats: 允许的图片格式列表。

        Returns:
            MessageBuilder: 当前构建器实例。
        """
        return self.add_image_base64_part(
            image_format=image_format,
            image_base64=image_base64,
            support_formats=support_formats,
        )

    def set_tool_call_id(self, tool_call_id: str) -> "MessageBuilder":
        """设置工具结果消息引用的工具调用 ID。

        Args:
            tool_call_id: 工具调用 ID。

        Returns:
            MessageBuilder: 当前构建器实例。

        Raises:
            ValueError: 当当前角色不是 `tool` 或 ID 为空时抛出。
        """
        if self.__role != RoleType.Tool:
            raise ValueError("仅当角色为 Tool 时才能设置工具调用 ID")
        if not tool_call_id:
            raise ValueError("工具调用 ID 不能为空")
        self.__tool_call_id = tool_call_id
        return self

    def add_tool_call(self, tool_call_id: str) -> "MessageBuilder":
        """设置工具结果消息引用的工具调用 ID。

        Args:
            tool_call_id: 工具调用 ID。

        Returns:
            MessageBuilder: 当前构建器实例。
        """
        return self.set_tool_call_id(tool_call_id)

    def set_tool_name(self, tool_name: str) -> "MessageBuilder":
        """设置 Tool 消息对应的工具名称。"""
        if self.__role != RoleType.Tool:
            raise ValueError("仅当角色为 Tool 时才能设置工具名称")
        if not tool_name:
            raise ValueError("工具名称不能为空")
        self.__tool_name = tool_name
        return self

    def set_tool_calls(self, tool_calls: List[ToolCall]) -> "MessageBuilder":
        """设置助手消息中的工具调用列表。

        Args:
            tool_calls: 工具调用列表。

        Returns:
            MessageBuilder: 当前构建器实例。

        Raises:
            ValueError: 当当前角色不是 `assistant` 或列表为空时抛出。
        """
        if self.__role != RoleType.Assistant:
            raise ValueError("仅当角色为 Assistant 时才能设置工具调用列表")
        if not tool_calls:
            raise ValueError("工具调用列表不能为空")
        self.__tool_calls = list(tool_calls)
        return self

    def set_reasoning_content(self, reasoning_content: str | None) -> "MessageBuilder":
        """设置助手消息的推理内容。"""
        if self.__role != RoleType.Assistant:
            raise ValueError("仅当角色为 Assistant 时才能设置推理内容")
        normalized_reasoning = "" if reasoning_content is None else str(reasoning_content)
        self.__reasoning_content = normalized_reasoning or None
        return self

    def build(self) -> Message:
        """构建消息对象。

        Returns:
            Message: 构建完成的消息对象。
        """
        return Message(
            role=self.__role,
            parts=list(self.__parts),
            tool_call_id=self.__tool_call_id,
            tool_name=self.__tool_name,
            tool_calls=list(self.__tool_calls) if self.__tool_calls else None,
            reasoning_content=self.__reasoning_content,
        )
