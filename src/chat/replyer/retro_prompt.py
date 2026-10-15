"""复古 replyer 提示词组装。

开启 `experimental.replyer_retro_prompt` 后，replyer 按旧版（0.12.x）的组织方式生成提示词：

- 人设和聊天注意事项合并为首条 system 消息，其余回复指令由模板占位符拼接而成；
- 群聊、群聊简短回复、私聊、私聊补充自己发言各用一套模板；
- 回复风格保留在模板原位置，整段模板作为一条 user 消息发送。

历史 Planner 模型输出直接丢弃，用户消息与麦麦实际发送的消息一起填入 `{dialogue_prompt}`。
旧版模板里由知识检索、工具信息、动作描述填充的块在当前版本没有对应来源，因此不再保留这些占位符。
"""

from __future__ import annotations

from copy import copy
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4
import base64

from PIL import Image, ImageOps

from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import get_chat_type_and_target_info, is_bot_self
from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    TextComponent,
)
from src.common.utils.math_utils import translate_timestamp_to_human_readable
from src.config.config import global_config
from src.llm_models.payload_content.context_item import (
    ContextImagePart,
    ContextItem,
    ContextItemBuilder,
    RoleType,
    UserMessageItem,
)
from src.maisaka.attention_drift import build_attention_drift_prompt_block
from src.maisaka.context.history import build_session_message_visible_text
from src.maisaka.context.message_adapter import (
    build_visible_text_from_sequence,
    format_speaker_content,
    parse_speaker_content,
)
from src.maisaka.context.messages import (
    LLMContextMessage,
    ModelOutputContextMessage,
    SessionBackedMessage,
    build_context_items_from_history_entry,
)

RETRO_GROUP_PROMPT = "retro_replyer"
"""群聊复古模板名。"""

RETRO_GROUP_LIGHT_PROMPT = "retro_replyer_light"
"""群聊简短回复复古模板名，对应旧版 think_level=0 的轻量回复模板。"""

RETRO_PRIVATE_PROMPT = "retro_private_replyer"
"""私聊复古模板名。"""

RETRO_PRIVATE_SELF_PROMPT = "retro_private_replyer_self"
"""私聊补充自己发言的复古模板名。"""

SHORT_REPLY_STYLE = "简短表达"
"""reply 工具请求简短表达时的风格取值，用于挑选群聊轻量模板。"""

RETRO_REPLY_TARGET_CONTENT_LIMIT = 300
"""复古模板里目标消息内容的截断长度，与旧版保持一致。"""

RETRO_RECENT_IMAGE_COUNT = 3
RETRO_RECENT_IMAGE_MAX_SIDE = 640
RETRO_OLDER_IMAGE_MAX_SIDE = 128


def _resize_retro_image(part: ContextImagePart, max_side: int) -> ContextImagePart:
    """等比例缩小本次请求的图片，小图保持原样；缩放时取首帧并保留透明度。"""
    with Image.open(BytesIO(base64.b64decode(part.image_base64, validate=True))) as image:
        if max(image.size) <= max_side:
            return part
        # 编码后不携带 EXIF，先摆正照片，再约束长边，极窄图片至少保留一个像素。
        frame = ImageOps.exif_transpose(image)
        frame = frame.convert("RGBA" if frame.has_transparency_data else "RGB")
        scale = max_side / max(frame.size)
        frame = frame.resize(
            (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
            Image.Resampling.LANCZOS,
        )
        output = BytesIO()
        if frame.has_transparency_data:
            frame.convert("RGBA").save(output, format="PNG")
            image_format = "png"
        else:
            frame.convert("RGB").save(output, format="JPEG", quality=95)
            image_format = "jpeg"
    return ContextImagePart(image_format, base64.b64encode(output.getvalue()).decode("ascii"))


class RetroReplyPromptMixin:
    """按旧版组织方式拼装 replyer 请求的 mixin。"""

    def _build_retro_request_messages(
        self,
        *,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        expression_habits: str = "",
        reply_requirements: str = "",
        stream_id: Optional[str] = None,
        think_level: int = 1,
        reply_tool_args: Optional[Dict[str, Any]] = None,
        enable_visual_message: bool = False,
    ) -> List[ContextItem]:
        """构建首条人设与注意事项 system 消息，以及复古模板 user 消息。

        聊天记录渲染进模板的对话块；启用视觉输入时，图片在同一 user 消息中跟随对应发言。
        """

        # Planner 模型输出不是真实发言；guided_reply 是已发送消息，必须保留在聊天记录中。
        chat_history = [
            message
            for message in chat_history
            if not isinstance(message, ModelOutputContextMessage)
            and not self._is_replyer_filtered_history_message(message)
        ]
        visual_parts: Dict[int, List[ContextImagePart]] = {}
        if enable_visual_message:
            chat_history, visual_parts = self._prepare_retro_visual_history(chat_history)
            # 目标消息和引用共用去掉视觉描述后的副本，不修改真实消息及其缓存。
            if reply_message is not None:
                for message_index, message in enumerate(chat_history):
                    if (
                        isinstance(message, SessionBackedMessage)
                        and message_index in visual_parts
                        and message.message_id == reply_message.message_id
                    ):
                        if message.original_message is not None:
                            reply_message = message.original_message
                        else:
                            reply_message = copy(reply_message)
                            reply_message.raw_message = message.raw_message
                        break
        prompt_name = self._select_retro_prompt_name(
            reply_message=reply_message,
            stream_id=stream_id,
            think_level=think_level,
            reply_tool_args=reply_tool_args,
        )
        system_prompt = "\n\n".join(
            block.strip()
            for block in (
                self._build_personality_prompt(),
                self._build_group_chat_attention_block(self._resolve_session_id(stream_id)),
                build_attention_drift_prompt_block(),
            )
            if block.strip()
        )
        template_context = self._build_retro_template_context(
            chat_history=chat_history,
            reply_message=reply_message,
            reply_reason=reply_reason,
            reply_reference=str((reply_tool_args or {}).get("reply_reference") or ""),
            expression_habits=expression_habits,
            reply_requirements=reply_requirements,
        )
        user_builder = ContextItemBuilder().set_role(RoleType.User)
        if enable_visual_message:
            dialogue_marker = uuid4().hex
            template_context["dialogue_prompt"] = dialogue_marker
            prefix, suffix = self._load_prompt(prompt_name, **template_context).split(dialogue_marker)
            pending_text = prefix
            history_by_id = {
                message.message_id: message
                for message in chat_history
                if isinstance(message, SessionBackedMessage) and message.message_id
            }
            history_started = False
            for message_index, message in enumerate(chat_history):
                if not isinstance(message, SessionBackedMessage):
                    continue
                history_line = self._render_retro_history_line(message, history_by_id)
                if not history_line:
                    continue
                pending_text += ("\n" if history_started else "") + history_line
                history_started = True
                image_parts = visual_parts.get(message_index, [])
                if image_parts:
                    # 在历史发言处插入图片，模板后半段的回复指令始终保持在最后。
                    user_builder.add_text_content(pending_text)
                    pending_text = ""
                    for part in image_parts:
                        user_builder.add_image_content(part.image_format, part.image_base64)
            user_builder.add_text_content(pending_text + suffix)
        else:
            user_builder.add_text_content(self._load_prompt(prompt_name, **template_context))
        items = [
            ContextItemBuilder().set_role(RoleType.System).add_text_content(system_prompt).build(),
            user_builder.build(),
        ]
        return items

    @staticmethod
    def _prepare_retro_visual_history(
        chat_history: List[LLMContextMessage],
    ) -> Tuple[List[LLMContextMessage], Dict[int, List[ContextImagePart]]]:
        """确定实际发送的图片并按新旧缩小，仅在本次模板副本中去掉对应的图片和表情包描述。"""

        candidates: List[Tuple[int, int, ContextImagePart]] = []
        for message_index, message in enumerate(chat_history):
            if not isinstance(message, SessionBackedMessage):
                continue
            media_index = -1
            for component in message.raw_message.components:
                if not isinstance(component, (ImageComponent, EmojiComponent)):
                    continue
                media_index += 1
                component_message = replace(message, raw_message=MessageSequence([component]))
                for item in build_context_items_from_history_entry(component_message, enable_visual_message=True):
                    if isinstance(item, UserMessageItem):
                        candidates.extend(
                            (message_index, media_index, part)
                            for part in item.parts
                            if isinstance(part, ContextImagePart)
                        )

        image_limit = max(0, int(global_config.visual.max_image_num))
        retained = candidates[-image_limit:] if image_limit else []
        visual_parts: Dict[int, List[ContextImagePart]] = {}
        visual_indices: Dict[int, Set[int]] = {}
        recent_start = max(0, len(retained) - RETRO_RECENT_IMAGE_COUNT)
        for image_index, (message_index, media_index, part) in enumerate(retained):
            # 按实际发送的图片计数，同一发言中的多张图片和表情包也各占一个位置。
            max_side = RETRO_RECENT_IMAGE_MAX_SIDE if image_index >= recent_start else RETRO_OLDER_IMAGE_MAX_SIDE
            visual_parts.setdefault(message_index, []).append(_resize_retro_image(part, max_side))
            visual_indices.setdefault(message_index, set()).add(media_index)

        def without_visual_descriptions(sequence: MessageSequence, sent_indices: Set[int]) -> MessageSequence:
            # 保留媒体类型标记；未发送的媒体仍使用原描述，包括超出图片配额的旧图片。
            components = []
            media_index = 0
            for component in sequence.components:
                if isinstance(component, (ImageComponent, EmojiComponent)):
                    if media_index in sent_indices:
                        components.append(
                            TextComponent("[表情包]" if isinstance(component, EmojiComponent) else "[图片]")
                        )
                    else:
                        components.append(component)
                    media_index += 1
                else:
                    components.append(component)
            return MessageSequence(components)

        rendered_history: List[LLMContextMessage] = []
        for message_index, message in enumerate(chat_history):
            if not isinstance(message, SessionBackedMessage) or message_index not in visual_indices:
                rendered_history.append(message)
                continue
            sent_indices = visual_indices[message_index]
            sequence = without_visual_descriptions(message.raw_message, sent_indices)
            original = message.original_message
            if original is not None:
                original = copy(original)
                original.raw_message = without_visual_descriptions(original.raw_message, sent_indices)
                visible_text = build_session_message_visible_text(original)
            else:
                speaker, _ = parse_speaker_content(message.processed_plain_text)
                _, content = parse_speaker_content(build_visible_text_from_sequence(sequence))
                visible_text = format_speaker_content(speaker or "", content)
            rendered_history.append(
                replace(message, raw_message=sequence, original_message=original, visible_text=visible_text)
            )
        return rendered_history, visual_parts

    def _select_retro_prompt_name(
        self,
        *,
        reply_message: Optional[SessionMessage],
        stream_id: Optional[str],
        think_level: int,
        reply_tool_args: Optional[Dict[str, Any]],
    ) -> str:
        """按群聊/私聊、是否简短回复、是否补充自己发言挑选模板。"""

        if self._is_retro_group_chat(stream_id):
            if self._is_retro_short_reply(think_level, reply_tool_args):
                return RETRO_GROUP_LIGHT_PROMPT
            return RETRO_GROUP_PROMPT

        if reply_message is not None:
            user_info = reply_message.message_info.user_info
            if is_bot_self(reply_message.platform, user_info.user_id):
                return RETRO_PRIVATE_SELF_PROMPT
        return RETRO_PRIVATE_PROMPT

    def _is_retro_group_chat(self, stream_id: Optional[str]) -> bool:
        """判断当前会话是否为群聊，群聊与私聊使用不同模板。"""

        if self.chat_stream is not None:
            return bool(self.chat_stream.is_group_session)

        session_id = self._resolve_session_id(stream_id)
        if not session_id:
            return False
        is_group_chat, _ = get_chat_type_and_target_info(session_id)
        return is_group_chat is True

    def _is_retro_short_reply(self, think_level: int, reply_tool_args: Optional[Dict[str, Any]]) -> bool:
        """判断本次回复是否属于简短回复，对应旧版 think_level=0 的轻量模板。"""

        if think_level == 0:
            return True
        requested_style = str((reply_tool_args or {}).get("reply_style") or "").strip()
        return requested_style == SHORT_REPLY_STYLE

    def _build_retro_template_context(
        self,
        *,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        reply_reference: str,
        expression_habits: str,
        reply_requirements: str,
    ) -> Dict[str, str]:
        """按旧版的块顺序准备模板占位符内容。"""

        combined_reference = self._build_reply_reference_message(reply_reason, reply_reference)
        # 旧版按概率用备选表达风格整体替换人设里的表达风格，而不是追加一条风格消息
        reply_style = self._select_temporary_reply_style() or self._select_reply_style()
        return {
            "bot_name": global_config.bot.nickname,
            "sender_name": self._build_retro_sender_name(chat_history, reply_message),
            "reply_style": reply_style,
            "expression_habits_block": expression_habits.strip(),
            "extra_info_block": self._build_retro_extra_info_block(reply_requirements),
            "dialogue_prompt": self._build_retro_dialogue_block(chat_history),
            "time_block": f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "reply_target_block": self._build_retro_reply_target_block(reply_message, chat_history),
            "planner_reasoning": combined_reference,
            "keywords_reaction_prompt": self._build_keyword_reaction_prompt(
                chat_history=chat_history,
                reply_message=reply_message,
            ),
        }

    @staticmethod
    def _build_retro_extra_info_block(reply_requirements: str) -> str:
        """按旧版措辞包裹本次回复要求、重试约束和插件追加信息。"""

        normalized_requirements = reply_requirements.strip()
        if not normalized_requirements:
            return ""
        return (
            "以下是你在回复时需要参考的信息，现在请你阅读以下内容，进行决策\n"
            f"{normalized_requirements}\n"
            "以上是你在回复时需要参考的信息，现在请你阅读以下内容，进行决策"
        )

    def _build_retro_sender_name(
        self,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
    ) -> str:
        """取私聊模板里的对话者名字，优先使用本次回复的目标消息发送者。"""

        if reply_message is not None:
            user_info = reply_message.message_info.user_info
            sender_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
            if sender_name:
                return sender_name

        # 没有目标消息时（插件直接调用回复器），沿用最近一条对方发言的发送者
        for message in reversed(chat_history):
            if not isinstance(message, SessionBackedMessage) or message.source_kind != "user":
                continue
            speaker, _ = parse_speaker_content(message.processed_plain_text)
            if speaker:
                return speaker
        return ""

    def _build_retro_reply_target_block(
        self,
        reply_message: Optional[SessionMessage],
        chat_history: Optional[List[LLMContextMessage]] = None,
    ) -> str:
        """按旧版措辞描述本次要回复的目标消息。"""

        if reply_message is None:
            return ""

        user_info = reply_message.message_info.user_info
        sender_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        _, visible_content = parse_speaker_content(
            build_session_message_visible_text(reply_message, include_reply_components=False)
        )
        history_by_id = {
            message.message_id: message
            for message in (chat_history or [])
            if isinstance(message, SessionBackedMessage) and message.message_id
        }
        references = self._build_retro_references(reply_message.raw_message, history_by_id)
        target_content = self._normalize_content(
            visible_content,
            limit=RETRO_REPLY_TARGET_CONTENT_LIMIT,
        )
        if not target_content and not references:
            return ""

        reference_text = " ".join(references)
        if is_bot_self(reply_message.platform, user_info.user_id):
            if references:
                return f"你现在想补充说明你刚刚自己的发言内容：你{reference_text} 说：{target_content}"
            return f"你现在想补充说明你刚刚自己的发言内容：{target_content}"
        if references:
            return f"现在{sender_name}{reference_text} 说：{target_content}。引起了你的注意"
        return f"现在{sender_name}说的：{target_content}。引起了你的注意"

    def _build_retro_dialogue_block(self, chat_history: List[LLMContextMessage]) -> str:
        """把聊天历史渲染成旧版那样的可读文本块。"""

        lines: List[str] = []
        history_by_id = {
            message.message_id: message
            for message in chat_history
            if isinstance(message, SessionBackedMessage) and message.message_id
        }

        for message in chat_history:
            # 复用普通上下文的可见文本渲染，只调整复古模式的时间与说话人外层格式。
            history_line = self._render_retro_history_line(message, history_by_id)
            if history_line:
                lines.append(history_line)

        return "\n".join(lines)

    def _render_retro_history_line(
        self,
        message: LLMContextMessage,
        history_by_id: Optional[Dict[str, SessionBackedMessage]] = None,
    ) -> str:
        """渲染历史正文与引用关系，引用图片优先使用上下文中的最新描述。"""

        if not isinstance(message, SessionBackedMessage):
            return ""

        speaker, content = parse_speaker_content(message.processed_plain_text)
        original = message.original_message
        if original is not None:
            speaker, content = parse_speaker_content(
                build_session_message_visible_text(original, include_reply_components=False)
            )
        content = content.strip()

        sequence = original.raw_message if original is not None else message.raw_message
        references = self._build_retro_references(sequence, history_by_id)

        if not content and not references:
            return ""
        bot_name = global_config.bot.nickname
        if message.source_kind == "guided_reply":
            speaker = f"{bot_name}(你)"
        elif bot_name and speaker == bot_name:
            speaker = "你"

        timestamp = translate_timestamp_to_human_readable(message.timestamp.timestamp(), mode="normal_no_YMD")
        reference_text = " ".join(references)
        if references:
            prefix = f"[{timestamp}] {speaker or ''}{reference_text} 说："
        else:
            prefix = f"[{timestamp}] {speaker}说：" if speaker else f"[{timestamp}]"
        return f"{prefix}{content}"

    @staticmethod
    def _build_retro_references(
        sequence: MessageSequence,
        history_by_id: Optional[Dict[str, SessionBackedMessage]] = None,
    ) -> List[str]:
        """统一渲染聊天历史和当前回复目标的引用，优先使用上下文中最新的原消息。"""

        references: List[str] = []
        for component in sequence.components:
            if not isinstance(component, ReplyComponent):
                continue
            target = history_by_id.get(component.target_message_id) if history_by_id is not None else None
            if target is not None:
                target_speaker, target_content = parse_speaker_content(target.processed_plain_text)
                if target.original_message is not None:
                    target_speaker, target_content = parse_speaker_content(
                        build_session_message_visible_text(target.original_message, include_reply_components=False)
                    )
            else:
                # 引用目标不在当前上下文时，使用引用组件已保存的原消息内容，不查询数据库。
                target_speaker = (
                    component.target_message_sender_cardname
                    or component.target_message_sender_nickname
                    or component.target_message_sender_id
                )
                target_content = component.target_message_content or ""
            target_name = target_speaker or component.target_message_id
            target_content = target_content.strip()
            reference = (
                f"[回复了{target_name}的消息: {target_content}]" if target_content else f"[回复了{target_name}的消息]"
            )
            references.append(reference)

        return references
