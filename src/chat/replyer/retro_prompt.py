"""复古 replyer 提示词组装。

开启 `experimental.replyer_retro_prompt` 后，replyer 按旧版（0.12.x）的组织方式生成提示词：

- 全部回复指令集中在一份完整模板里，模板由若干块占位符拼接而成；
- 群聊、群聊简短回复、私聊、私聊补充自己发言各用一套模板；
- 整段模板作为唯一一条 user 消息发送，而不是当前的「system + 历史 + 末条 user」三段式。

历史聊天记录在这一模式下渲染成纯文本填入 `{dialogue_prompt}`，不再作为独立 ContextItem 发送。
旧版模板里由知识检索、工具信息、动作描述填充的块在当前版本没有对应来源，因此不再保留这些占位符。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import get_chat_type_and_target_info, is_bot_self
from src.common.utils.math_utils import translate_timestamp_to_human_readable
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItem, ContextItemBuilder, RoleType
from src.maisaka.context.message_adapter import parse_speaker_content
from src.maisaka.context.messages import LLMContextMessage, ModelOutputContextMessage, SessionBackedMessage
from src.services.message_service import build_readable_messages

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
    ) -> List[ContextItem]:
        """构建唯一一条承载完整复古模板的 user 消息。

        聊天记录已经渲染进模板的对话块，因此这里不再附加图片 Item，与旧版的纯文本请求保持一致。
        """

        prompt_name = self._select_retro_prompt_name(
            reply_message=reply_message,
            stream_id=stream_id,
            think_level=think_level,
            reply_tool_args=reply_tool_args,
        )
        template_context = self._build_retro_template_context(
            chat_history=chat_history,
            reply_message=reply_message,
            reply_reason=reply_reason,
            expression_habits=expression_habits,
            reply_requirements=reply_requirements,
            stream_id=stream_id,
        )
        prompt = self._load_prompt(prompt_name, **template_context)
        return [ContextItemBuilder().set_role(RoleType.User).add_text_content(prompt).build()]

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
        expression_habits: str,
        reply_requirements: str,
        stream_id: Optional[str],
    ) -> Dict[str, str]:
        """按旧版的块顺序准备模板占位符内容。"""

        session_id = self._resolve_session_id(stream_id)
        normalized_reason = reply_reason.strip()
        # 旧版按概率用备选表达风格整体替换人设里的表达风格，而不是追加一条风格消息
        reply_style = self._select_temporary_reply_style() or self._select_reply_style()
        return {
            "bot_name": global_config.bot.nickname,
            "sender_name": self._build_retro_sender_name(chat_history, reply_message),
            "identity": self._build_personality_prompt(),
            "reply_style": reply_style,
            "expression_habits_block": expression_habits.strip(),
            "extra_info_block": self._build_retro_extra_info_block(reply_requirements),
            "dialogue_prompt": self._build_retro_dialogue_block(chat_history),
            "time_block": f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "reply_target_block": self._build_retro_reply_target_block(reply_message),
            "planner_reasoning": f"你的想法是：{normalized_reason}" if normalized_reason else "",
            "keywords_reaction_prompt": self._build_keyword_reaction_prompt(
                chat_history=chat_history,
                reply_message=reply_message,
            ),
            "group_chat_attention_block": self._build_group_chat_attention_block(session_id),
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

    def _build_retro_reply_target_block(self, reply_message: Optional[SessionMessage]) -> str:
        """按旧版措辞描述本次要回复的目标消息。"""

        if reply_message is None:
            return ""

        user_info = reply_message.message_info.user_info
        sender_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        target_content = self._normalize_content(
            self._build_target_message_content(reply_message),
            limit=RETRO_REPLY_TARGET_CONTENT_LIMIT,
        )
        if not target_content:
            return ""

        if is_bot_self(reply_message.platform, user_info.user_id):
            return f"你现在想补充说明你刚刚自己的发言内容：{target_content}"
        return f"现在{sender_name}说的：{target_content}。引起了你的注意"

    def _build_retro_dialogue_block(self, chat_history: List[LLMContextMessage]) -> str:
        """把聊天历史渲染成旧版那样的可读文本块。"""

        lines: List[str] = []
        pending_session_messages: List[SessionMessage] = []

        def flush_pending() -> None:
            if not pending_session_messages:
                return
            readable = build_readable_messages(
                list(pending_session_messages),
                replace_bot_name=True,
                timestamp_mode="normal_no_YMD",
                truncate=True,
            )
            lines.extend(readable.splitlines())
            pending_session_messages.clear()

        for message in chat_history:
            # 有原始会话消息时交给统一的可读渲染，保证与其它提示词中的聊天记录格式一致
            if isinstance(message, SessionBackedMessage) and message.original_message is not None:
                pending_session_messages.append(message.original_message)
                continue

            flush_pending()
            history_line = self._render_retro_history_line(message)
            if history_line:
                lines.append(history_line)

        flush_pending()
        return "\n".join(lines)

    @staticmethod
    def _render_retro_history_line(message: LLMContextMessage) -> str:
        """渲染没有原始会话消息的历史条目。"""

        bot_name = global_config.bot.nickname
        if isinstance(message, ModelOutputContextMessage):
            content = " ".join((message.content or "").split()).strip()
            speaker = bot_name
        elif isinstance(message, SessionBackedMessage):
            speaker, content = parse_speaker_content(message.processed_plain_text)
            speaker = speaker or ""
            content = " ".join(content.split()).strip()
        else:
            return ""

        if not content:
            return ""
        if bot_name and speaker == bot_name:
            speaker = "你"

        timestamp = translate_timestamp_to_human_readable(message.timestamp.timestamp(), mode="normal_no_YMD")
        prefix = f"[{timestamp}] {speaker}说：" if speaker else f"[{timestamp}]"
        return f"{prefix}{content}"
