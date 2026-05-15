"""Maisaka 内部上下文消息抽象。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from io import BytesIO
from typing import Optional, Sequence
import base64

from PIL import Image as PILImage

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import (
    AtComponent,
    DictComponent,
    EmojiComponent,
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    StandardMessageComponents,
    TextComponent,
    VoiceComponent,
)
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.llm_models.payload_content.tool_option import ToolCall

FORWARD_PREVIEW_LIMIT = 4
TIMING_GATE_INVALID_TOOL_HINT_SOURCE = "timing_gate_invalid_tool_hint"


def _guess_image_format(image_bytes: bytes) -> Optional[str]:
    if not image_bytes:
        return None

    try:
        with PILImage.open(BytesIO(image_bytes)) as image:
            return image.format.lower() if image.format else None
    except Exception:
        return None


def _append_emoji_component(
    builder: MessageBuilder,
    component: EmojiComponent,
    *,
    enable_visual_message: bool,
) -> bool:
    """将表情组件追加到 LLM 消息构建器。"""
    image_format = _guess_image_format(component.binary_data)
    if enable_visual_message and image_format and component.binary_data:
        builder.add_text_content("[消息类型]表情包")
        builder.add_image_content(image_format, base64.b64encode(component.binary_data).decode("utf-8"))
        return True

    normalized_content = component.content.strip()
    if normalized_content:
        builder.add_text_content(normalized_content)
        return True

    builder.add_text_content("[表情包]")
    return True


def _append_image_component(
    builder: MessageBuilder,
    component: ImageComponent,
    *,
    enable_visual_message: bool,
) -> bool:
    """将图片组件追加到 LLM 消息构建器。"""
    image_format = _guess_image_format(component.binary_data)
    if enable_visual_message and image_format and component.binary_data:
        builder.add_text_content("[消息类型]图片")
        builder.add_image_content(image_format, base64.b64encode(component.binary_data).decode("utf-8"))
        return True

    normalized_content = component.content.strip()
    if normalized_content:
        builder.add_text_content(normalized_content)
        return True

    builder.add_text_content("[图片，识别中.....]")
    return True


def _append_reply_component(builder: MessageBuilder, component: ReplyComponent) -> bool:
    """将回复组件追加到 LLM 消息构建器。"""
    target_message_id = component.target_message_id.strip()
    if not target_message_id:
        return False

    builder.add_text_content(f"[引用消息]{target_message_id}")
    return True


def _render_at_component_text(component: AtComponent) -> str:
    """灏?AtComponent 娓叉煋涓烘枃鏈舰寮忋€?"""

    target_name = component.target_user_cardname or component.target_user_nickname or component.target_user_id
    return f"@{target_name}".strip()


def _append_at_component(builder: MessageBuilder, component: AtComponent) -> bool:
    """灏?@ 缁勪欢杞崲涓烘枃鏈苟鍐欏叆 LLM 娑堟伅銆?"""

    rendered_text = _render_at_component_text(component)
    if not rendered_text:
        return False

    builder.add_text_content(rendered_text)
    return True


def contains_complex_message(message_sequence: MessageSequence) -> bool:
    """判断消息序列中是否包含复杂消息组件。"""

    return any(isinstance(component, ForwardNodeComponent) for component in message_sequence.components)


async def build_full_complex_message_content(message: SessionMessage) -> str:
    """构造复杂消息的完整文本内容。"""

    if not message.processed_plain_text:
        await message.process()
    return (message.processed_plain_text or "").strip()


def _build_complex_message_prompt_text(message_sequence: MessageSequence) -> str:
    """将复杂消息转换为适合注入 Prompt 的摘要文本。"""

    prompt_parts: list[str] = []
    for component in message_sequence.components:
        rendered_text = _render_component_for_prompt(component)
        if rendered_text:
            prompt_parts.append(rendered_text)
    return "\n".join(part for part in prompt_parts if part).strip()


def _render_component_for_prompt(component: StandardMessageComponents) -> str:
    """将单个组件渲染为 Prompt 文本。"""

    if isinstance(component, TextComponent):
        return (component.text or "").strip()

    if isinstance(component, ImageComponent):
        return component.content.strip() if component.content else "[图片，识别中.....]"

    if isinstance(component, EmojiComponent):
        return component.content.strip() if component.content else "[表情包]"

    if isinstance(component, VoiceComponent):
        return component.content.strip() if component.content else "[语音消息]"

    if isinstance(component, AtComponent):
        return _render_at_component_text(component)

    if isinstance(component, ReplyComponent):
        sender_name = (
            component.target_message_sender_cardname
            or component.target_message_sender_nickname
            or component.target_message_sender_id
        )
        target_content = (component.target_message_content or "").strip()
        if sender_name and target_content:
            return f"[回复了{sender_name}的消息: {target_content}]"
        if target_content:
            return f"[回复消息: {target_content}]"
        target_message_id = component.target_message_id.strip()
        return f"[引用消息]{target_message_id}" if target_message_id else "[回复消息]"

    if isinstance(component, ForwardNodeComponent):
        return _build_forward_preview_block(component)

    if isinstance(component, DictComponent):
        raw_type = component.data.get("type") if isinstance(component.data, dict) else None
        if isinstance(raw_type, str) and raw_type.strip():
            return f"[{raw_type.strip()}消息]"
        return "[复杂消息]"

    return ""


def _build_forward_preview_block(component: ForwardNodeComponent) -> str:
    """构造转发消息的预览块。"""

    preview_lines = ["[消息类型]复杂消息", "转发消息", f"预览前{FORWARD_PREVIEW_LIMIT}条："]
    preview_nodes = component.forward_components[:FORWARD_PREVIEW_LIMIT]

    for node in preview_nodes:
        sender_name = node.user_cardname or node.user_nickname or node.user_id or "未知用户"
        content = _render_components_inline(node.content) or "[空消息]"
        preview_lines.append(f"{sender_name}：{content}")

    total_count = len(component.forward_components)
    if total_count > FORWARD_PREVIEW_LIMIT:
        preview_lines.append("......")
        preview_lines.append(f"共{total_count}条，可以选择使用 view_complex_message 查看完整内容。")

    return "\n".join(preview_lines).strip()


def _render_components_inline(components: Sequence[StandardMessageComponents]) -> str:
    """将组件序列压缩为单行预览文本。"""

    rendered_parts: list[str] = []
    for component in components:
        if isinstance(component, ForwardNodeComponent):
            rendered_parts.append("[转发消息]")
            continue

        rendered_text = _render_component_for_prompt(component)
        normalized_text = _normalize_inline_text(rendered_text)
        if normalized_text:
            rendered_parts.append(normalized_text)

    return " ".join(rendered_parts).strip()


def _normalize_inline_text(text: str) -> str:
    """将多行文本压缩为适合预览的一行。"""

    return " ".join((text or "").split()).strip()


def _build_message_from_sequence(
    role: RoleType,
    message_sequence: MessageSequence,
    fallback_text: str,
    *,
    enable_visual_message: bool = True,
    tool_call_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    tool_calls: Optional[list[ToolCall]] = None,
    reasoning_content: Optional[str] = None,
) -> Optional[Message]:
    """根据消息片段构造统一 LLM 消息。"""
    builder = MessageBuilder().set_role(role)
    if role == RoleType.Assistant and tool_calls:
        builder.set_tool_calls(tool_calls)
    if role == RoleType.Assistant and reasoning_content:
        builder.set_reasoning_content(reasoning_content)
    if role == RoleType.Tool and tool_call_id:
        builder.add_tool_call(tool_call_id)
    if role == RoleType.Tool and tool_name:
        builder.set_tool_name(tool_name)

    has_content = False
    for component in message_sequence.components:
        if isinstance(component, TextComponent):
            if component.text:
                builder.add_text_content(component.text)
                has_content = True
            continue

        if isinstance(component, EmojiComponent):
            has_content = (
                _append_emoji_component(
                    builder,
                    component,
                    enable_visual_message=enable_visual_message,
                )
                or has_content
            )
            continue

        if isinstance(component, ImageComponent):
            has_content = (
                _append_image_component(
                    builder,
                    component,
                    enable_visual_message=enable_visual_message,
                )
                or has_content
            )
            continue

        if isinstance(component, AtComponent):
            has_content = _append_at_component(builder, component) or has_content
            continue

        if isinstance(component, ReplyComponent):
            has_content = _append_reply_component(builder, component) or has_content
            continue

    if not has_content and fallback_text:
        builder.add_text_content(fallback_text)
        has_content = True

    if not has_content and not (role == RoleType.Assistant and tool_calls):
        return None
    return builder.build()


class ReferenceMessageType(str, Enum):
    """参考消息类型。"""

    CUSTOM = "custom"
    JARGON = "jargon"
    MEMORY = "memory"
    TOOL_HINT = "tool_hint"


class LLMContextMessage(ABC):
    """Maisaka 内部用于组织 LLM 上下文的统一消息抽象。"""

    timestamp: datetime

    @property
    @abstractmethod
    def role(self) -> str:
        """返回 LLM 消息角色。"""

    @property
    @abstractmethod
    def processed_plain_text(self) -> str:
        """返回可读的纯文本内容。"""

    @property
    def count_in_context(self) -> bool:
        """是否占用普通 user/assistant 上下文窗口。"""
        return True

    @property
    def source(self) -> str:
        """返回消息来源。"""
        return self.__class__.__name__

    @abstractmethod
    def to_llm_message(self, enable_visual_message: bool = True) -> Optional[Message]:
        """转换为统一 LLM 消息。"""

    def consume_once(self) -> bool:
        """消费一次生命周期，返回是否继续保留。"""
        return True


@dataclass(slots=True)
class SessionBackedMessage(LLMContextMessage):
    """真实会话上下文消息。"""

    raw_message: MessageSequence
    visible_text: str
    timestamp: datetime
    message_id: Optional[str] = None
    original_message: Optional[SessionMessage] = None
    source_kind: str = "user"

    @property
    def role(self) -> str:
        return RoleType.User.value

    @property
    def processed_plain_text(self) -> str:
        return self.visible_text

    @property
    def source(self) -> str:
        return self.source_kind

    def to_llm_message(self, enable_visual_message: bool = True) -> Optional[Message]:
        return _build_message_from_sequence(
            RoleType.User,
            self.raw_message,
            self.processed_plain_text,
            enable_visual_message=enable_visual_message,
        )

    @classmethod
    def from_session_message(
        cls,
        session_message: SessionMessage,
        *,
        raw_message: MessageSequence,
        visible_text: str,
        source_kind: str = "user",
    ) -> "SessionBackedMessage":
        """从真实 SessionMessage 构造上下文消息。"""
        return cls(
            raw_message=raw_message,
            visible_text=visible_text,
            timestamp=session_message.timestamp,
            message_id=session_message.message_id,
            original_message=session_message,
            source_kind=source_kind,
        )


@dataclass(slots=True)
class ComplexSessionMessage(SessionBackedMessage):
    """复杂消息上下文消息。"""

    prompt_text: str = ""
    complex_message_type: str = "forward"

    @property
    def source(self) -> str:
        return f"{self.source_kind}:{self.complex_message_type}"

    def to_llm_message(self, enable_visual_message: bool = True) -> Optional[Message]:
        del enable_visual_message
        message_sequence = MessageSequence([TextComponent(self.prompt_text)])
        return _build_message_from_sequence(
            RoleType.User,
            message_sequence,
            self.prompt_text,
        )

    @classmethod
    def from_session_message(
        cls,
        session_message: SessionMessage,
        *,
        planner_prefix: str,
        visible_text: str,
        source_kind: str = "user",
    ) -> Optional["ComplexSessionMessage"]:
        """从真实 SessionMessage 构造复杂消息上下文消息。"""

        prompt_text = _build_complex_message_prompt_text(session_message.raw_message)
        if not prompt_text:
            return None

        return cls(
            raw_message=session_message.raw_message,
            visible_text=visible_text,
            timestamp=session_message.timestamp,
            message_id=session_message.message_id,
            original_message=session_message,
            source_kind=source_kind,
            prompt_text=f"{planner_prefix}{prompt_text}",
        )


@dataclass(slots=True)
class ReferenceMessage(LLMContextMessage):
    """参考消息。"""

    content: str
    timestamp: datetime
    reference_type: ReferenceMessageType = ReferenceMessageType.CUSTOM
    remaining_uses_value: Optional[int] = 1
    display_prefix: str = "[参考消息]"

    @property
    def role(self) -> str:
        return RoleType.User.value

    @property
    def processed_plain_text(self) -> str:
        return f"{self.display_prefix}\n{self.content}".strip()

    @property
    def count_in_context(self) -> bool:
        return False

    @property
    def source(self) -> str:
        return self.reference_type.value

    def to_llm_message(self, enable_visual_message: bool = True) -> Optional[Message]:
        del enable_visual_message
        message_sequence = MessageSequence([TextComponent(self.processed_plain_text)])
        return _build_message_from_sequence(RoleType.User, message_sequence, self.processed_plain_text)

    def consume_once(self) -> bool:
        if self.remaining_uses_value is None:
            return True

        self.remaining_uses_value -= 1
        return self.remaining_uses_value > 0


@dataclass(slots=True)
class AssistantMessage(LLMContextMessage):
    """内部 assistant 消息。"""

    content: str
    timestamp: datetime
    tool_calls: list[ToolCall] = field(default_factory=list)
    source_kind: str = "assistant"
    reasoning_content: str = ""

    @property
    def role(self) -> str:
        return RoleType.Assistant.value

    @property
    def processed_plain_text(self) -> str:
        return self.content

    @property
    def count_in_context(self) -> bool:
        return self.source_kind != "perception"

    @property
    def source(self) -> str:
        return self.source_kind

    def to_llm_message(self, enable_visual_message: bool = True) -> Optional[Message]:
        del enable_visual_message
        message_sequence = MessageSequence([])
        if self.content:
            message_sequence.text(self.content)
        return _build_message_from_sequence(
            RoleType.Assistant,
            message_sequence,
            self.content,
            tool_calls=self.tool_calls or None,
            reasoning_content=self.reasoning_content,
        )


@dataclass(slots=True)
class ToolResultMessage(LLMContextMessage):
    """工具返回结果消息。"""

    content: str
    timestamp: datetime
    tool_call_id: str
    tool_name: str = ""
    success: bool = True

    @property
    def role(self) -> str:
        return RoleType.Tool.value

    @property
    def processed_plain_text(self) -> str:
        return self.content

    @property
    def count_in_context(self) -> bool:
        return False

    @property
    def source(self) -> str:
        return self.tool_name or "tool"

    def to_llm_message(self, enable_visual_message: bool = True) -> Optional[Message]:
        del enable_visual_message
        message_sequence = MessageSequence([TextComponent(self.content)])
        return _build_message_from_sequence(
            RoleType.Tool,
            message_sequence,
            self.content,
            tool_call_id=self.tool_call_id,
            tool_name=self.tool_name,
        )


def build_llm_message_from_context(
    context_message: LLMContextMessage,
    *,
    enable_visual_message: bool = True,
) -> Optional[Message]:
    """将 Maisaka 内部上下文消息转换为发给 LLM 的统一消息。"""

    return context_message.to_llm_message(enable_visual_message=enable_visual_message)
