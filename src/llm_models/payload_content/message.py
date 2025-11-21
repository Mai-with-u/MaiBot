from enum import Enum
from typing import List, Optional

from .tool_option import ToolCall


# 设计这系列类的目的是为未来可能的扩展做准备


class RoleType(Enum):
    System = "system"
    User = "user"
    Assistant = "assistant"
    Tool = "tool"


SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png", "webp", "gif"]  # openai支持的图片格式


class Message:
    def __init__(
        self,
        role: RoleType,
        content: str | list[tuple[str, str] | str],
        tool_call_id: str | None = None,
        tool_calls: Optional[List[ToolCall]] = None,
    ):
        """
        初始化消息对象
        （不应直接修改Message类，而应使用MessageBuilder类来构建对象）
        """
        self.role: RoleType = role
        self.content: str | list[tuple[str, str] | str] = content
        self.tool_call_id: str | None = tool_call_id
        self.tool_calls: Optional[List[ToolCall]] = tool_calls

    def __str__(self) -> str:
        return (
            f"Role: {self.role}, Content: {self.content}, "
            f"Tool Call ID: {self.tool_call_id}, Tool Calls: {self.tool_calls}"
        )


class MessageBuilder:
    def __init__(self):
        self.__role: RoleType = RoleType.User
        self.__content: list[tuple[str, str] | str] = []
        self.__tool_call_id: str | None = None
        self.__tool_calls: Optional[List[ToolCall]] = None

    def set_role(self, role: RoleType = RoleType.User) -> "MessageBuilder":
        """
        设置角色（默认为User）
        :param role: 角色
        :return: MessageBuilder对象
        """
        self.__role = role
        return self

    def add_text_content(self, text: str) -> "MessageBuilder":
        """
        添加文本内容
        :param text: 文本内容
        :return: MessageBuilder对象
        """
        self.__content.append(text)
        return self

    def add_image_content(
        self,
        image_format: str,
        image_base64: str,
        support_formats: list[str] = SUPPORTED_IMAGE_FORMATS,  # 默认支持格式
    ) -> "MessageBuilder":
        """
        添加图片内容
        :param image_format: 图片格式
        :param image_base64: 图片的base64编码
        :return: MessageBuilder对象
        """
        if image_format.lower() not in support_formats:
            raise ValueError("不受支持的图片格式")
        if not image_base64:
            raise ValueError("图片的base64编码不能为空")
        self.__content.append((image_format, image_base64))
        return self

    def add_tool_call(self, tool_call_id: str) -> "MessageBuilder":
        """
        添加工具调用指令（调用时请确保已设置为Tool角色）
        :param tool_call_id: 工具调用指令的id
        :return: MessageBuilder对象
        """
        if self.__role != RoleType.Tool:
            raise ValueError("仅当角色为Tool时才能添加工具调用ID")
        if not tool_call_id:
            raise ValueError("工具调用ID不能为空")
        self.__tool_call_id = tool_call_id
        return self

    def set_tool_calls(self, tool_calls: List[ToolCall]) -> "MessageBuilder":
        """
        设置助手消息的工具调用列表
        :param tool_calls: 工具调用列表
        :return: MessageBuilder对象
        """
        if self.__role != RoleType.Assistant:
            raise ValueError("仅当角色为Assistant时才能设置工具调用列表")
        if not tool_calls:
            raise ValueError("工具调用列表不能为空")
        self.__tool_calls = tool_calls
        return self

    def build(self) -> Message:
        """
        构建消息对象
        :return: Message对象
        """
        if len(self.__content) == 0 and not (self.__role == RoleType.Assistant and self.__tool_calls):
            raise ValueError("内容不能为空")
        if self.__role == RoleType.Tool and self.__tool_call_id is None:
            raise ValueError("Tool角色的工具调用ID不能为空")

        return Message(
            role=self.__role,
            content=(
                self.__content[0]
                if (len(self.__content) == 1 and isinstance(self.__content[0], str))
                else self.__content
            ),
            tool_call_id=self.__tool_call_id,
            tool_calls=self.__tool_calls,
        )
