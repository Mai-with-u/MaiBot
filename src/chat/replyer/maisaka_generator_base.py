import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from src.chat.message_receive.chat_manager import BotChatSession
from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import get_chat_type_and_target_info
from src.cli.console import console
from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.data_models.message_component_data_model import (
    AtComponent,
    EmojiComponent,
    ImageComponent,
    ReplyComponent,
    TextComponent,
    VoiceComponent,
)
from src.common.data_models.reply_generation_data_models import (
    GenerationMetrics,
    LLMCompletionResult,
    ReplyGenerationResult,
    build_reply_monitor_detail,
)
from src.common.i18n import get_locale
from src.common.logger import get_logger
from src.common.utils.utils_config import ChatConfigUtils
from src.config.config import global_config
from src.config.model_configs import ModelInfo
from src.core.types import ActionInfo
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.maisaka.context.message_adapter import parse_speaker_content
from src.maisaka.context.messages import (
    AssistantMessage,
    LLMContextMessage,
    ReferenceMessage,
    SessionBackedMessage,
    ToolResultMessage,
    build_llm_message_from_context,
)
from src.maisaka.context.planner_messages import extract_quote_ids_from_message_sequence
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.memory.mid_term import is_mid_term_memory_message
from src.maisaka.visual.message_limiter import limit_latest_images_in_messages
from src.plugin_runtime.hook_payloads import deserialize_prompt_messages, serialize_prompt_messages

from .maisaka_expression_selector import maisaka_expression_selector

logger = get_logger("replyer")

DEBUG_REPLY_CACHE_DIR = Path("logs/debug_reply_cache")
REPLYER_MAX_HOOK_RETRIES = 3


@dataclass
class MaisakaReplyContext:
    """Maisaka replyer 使用的回复上下文。"""

    expression_habits: str = ""
    selected_expression_ids: List[int] = field(default_factory=list)
    selected_expressions: List[Dict[str, Any]] = field(default_factory=list)


class BaseMaisakaReplyGenerator:
    """Maisaka replyer 的共享实现。"""

    def __init__(
        self,
        *,
        chat_stream: Optional[BotChatSession] = None,
        request_type: str = "maisaka.replyer",
        llm_client_cls: Any,
        load_prompt_func: Callable[..., str],
        enable_visual_message: Optional[bool],
        replyer_mode: Literal["text", "multimodal", "auto"],
    ) -> None:
        self.chat_stream = chat_stream
        self.request_type = request_type
        self._llm_client_cls = llm_client_cls
        self._load_prompt = load_prompt_func
        self._enable_visual_message = enable_visual_message
        self._replyer_mode = replyer_mode
        self.express_model = llm_client_cls(
            task_name="replyer",
            request_type=request_type,
            session_id=getattr(chat_stream, "session_id", "") if chat_stream is not None else "",
        )

    def _build_personality_prompt(self) -> str:
        """构建 replyer 使用的人设提示。"""
        try:
            bot_name = global_config.bot.nickname
            alias_names = global_config.bot.alias_names
            bot_aliases = f"，也有人叫你{','.join(alias_names)}" if alias_names else ""

            prompt_personality = global_config.personality.personality.strip()
            if not prompt_personality:
                prompt_personality = "是人类。"

            return f"你的名字是{bot_name}{bot_aliases}。\n{prompt_personality}"
        except Exception as exc:
            logger.warning(f"构建 Maisaka 人设提示词失败: {exc}")
            return "你的名字是麦麦。\n是人类。"

    @staticmethod
    def _select_reply_style() -> str:
        """返回 replyer 使用的基础表达风格。"""
        return global_config.personality.reply_style

    @staticmethod
    def _select_temporary_reply_style() -> str:
        """按配置概率选择本次回复的一次性备用表达风格。"""
        personality_config = global_config.personality
        candidate_styles = [style.strip() for style in personality_config.multiple_reply_style if style.strip()]

        if not candidate_styles:
            return ""

        probability = personality_config.multiple_probability
        if probability <= 0:
            return ""
        if random.random() > probability:
            return ""

        return random.choice(candidate_styles)

    @staticmethod
    def _normalize_content(content: str, limit: int = 500) -> str:
        normalized = " ".join((content or "").split())
        if len(normalized) > limit:
            return normalized[:limit] + "..."
        return normalized

    @staticmethod
    def _extract_visible_assistant_reply(message: AssistantMessage) -> str:
        del message
        return ""

    def _extract_guided_bot_reply(self, message: SessionBackedMessage) -> str:
        # 只能根据结构化来源字段判断是否为 bot 自身写回的历史消息，
        # 不能依赖昵称/群名片等可控文本，避免误判和提示注入。
        if message.source_kind != "guided_reply":
            return ""

        plain_text = message.processed_plain_text.strip()
        _, body = parse_speaker_content(plain_text)
        normalized_body = body.strip()
        return self._normalize_content(normalized_body) if normalized_body else ""

    def _build_target_message_block(self, reply_message: Optional[SessionMessage]) -> str:
        if reply_message is None:
            return ""

        user_info = reply_message.message_info.user_info
        sender_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        target_message_id = reply_message.message_id.strip() if reply_message.message_id else "未知"
        # target_time = reply_message.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        quote_ids = extract_quote_ids_from_message_sequence(reply_message.raw_message)
        target_content = self._normalize_content(self._build_target_message_content(reply_message), limit=300)
        if not target_content:
            target_content = "[无可见文本内容]"

        # target_lines = [
        #     "【本次回复目标】",
        #     f"- msg_id：{target_message_id}",
        # ]
        # if quote_ids:
        #     target_lines.append(f"- quote={','.join(quote_ids)}")
        # target_lines.extend(
        #     [
        #         f"- 时间：{target_time}",
        #         f"- 用户名：{sender_name}",
        #         f"- 发言内容：{target_content}",
        #         "",
        #         "你这次要回复的就是这条目标消息，请结合整段上下文理解，但不要把其他历史消息当成当前回复对象。",
        #     ]
        # )
        # return "\n".join(target_lines)

        target_lines = [
            f"你想要回复的消息是 {sender_name} 发送的 msg_id为 {target_message_id} 的消息，你这次要回复的就是这条目标消息，不要把其他历史消息当成当前回复对象。",
        ]
        if quote_ids:
            target_lines.append(f"- quote={','.join(quote_ids)}")
        target_lines.extend(
            [
                f"- 发言内容：{target_content}",
            ]
        )
        return "\n".join(target_lines)

    @staticmethod
    def _render_target_at_component(component: AtComponent) -> str:
        target_name = component.target_user_cardname or component.target_user_nickname or component.target_user_id
        return f"@{target_name}".strip()

    def _build_target_message_content(self, reply_message: SessionMessage) -> str:
        rendered_parts: List[str] = []

        for component in reply_message.raw_message.components:
            if isinstance(component, TextComponent):
                if component.text:
                    rendered_parts.append(component.text)
                continue

            if isinstance(component, ReplyComponent):
                continue

            if isinstance(component, AtComponent):
                rendered_at = self._render_target_at_component(component)
                if rendered_at:
                    rendered_parts.append(rendered_at)
                continue

            if isinstance(component, ImageComponent):
                rendered_parts.append(component.content.strip() or "[图片，识别中.....]")
                continue

            if isinstance(component, EmojiComponent):
                rendered_parts.append(component.content.strip() or "[表情包]")
                continue

            if isinstance(component, VoiceComponent):
                rendered_parts.append(component.content.strip() or "[语音消息]")

        normalized_content = " ".join(part.strip() for part in rendered_parts if part and part.strip()).strip()
        if normalized_content:
            return normalized_content
        return (reply_message.processed_plain_text or "").strip()

    @staticmethod
    def _get_chat_prompt_for_chat(chat_id: str, is_group_chat: Optional[bool]) -> str:
        """根据聊天流 ID 获取匹配的额外 prompt。"""
        return ChatConfigUtils.get_chat_prompt_for_chat(chat_id, is_group_chat)

    def _build_group_chat_attention_block(self, session_id: str) -> str:
        """构建当前聊天场景下的额外注意事项块。"""
        if not session_id:
            return ""

        try:
            is_group_chat, _ = get_chat_type_and_target_info(session_id)
        except Exception:
            is_group_chat = None

        prompt_lines: List[str] = []

        if is_group_chat is True:
            if group_chat_prompt := global_config.chat.group_chat_prompt.strip():
                prompt_lines.append(f"通用注意事项：\n{group_chat_prompt}")
        elif is_group_chat is False:
            if private_chat_prompt := global_config.chat.private_chat_prompts.strip():
                prompt_lines.append(f"通用注意事项：\n{private_chat_prompt}")

        if chat_prompt := self._get_chat_prompt_for_chat(session_id, is_group_chat).strip():
            prompt_lines.append(f"当前聊天额外注意事项：\n{chat_prompt}")

        if not prompt_lines:
            return ""

        return "在该聊天中的注意事项：\n" + "\n\n".join(prompt_lines) + "\n"

    @staticmethod
    def _get_prompt_locale() -> str:
        """获取当前 prompt 语言。"""

        try:
            return get_locale().lower()
        except Exception:
            return "zh-cn"

    @staticmethod
    def _build_replyer_output_instruction() -> str:
        """构建 replyer 的最终输出格式说明。"""

        locale = BaseMaisakaReplyGenerator._get_prompt_locale()
        if not global_config.experimental.enable_replyer_format_output:
            if locale.startswith("en"):
                return (
                    "Please do not output any extra content (including unnecessary prefixes or suffixes, "
                    "colons, brackets, stickers, plain at, or @). Only output the message content itself."
                )
            if locale.startswith("ja"):
                return (
                    "余計な内容（不要な前置きや後置き、コロン、括弧、スタンプ、通常の at や @ など）は出力せず、"
                    "発言内容だけを出力してください。"
                )
            return "请注意不要输出多余内容(包括不必要的前后缀，冒号，括号，表情包，@等 )，只输出发言内容就好。"

        if locale.startswith("en"):
            return (
                "Only output the message fragments to send. Do not output explanations, Markdown, or code fences. "
                "Use `<text>text</text>` for normal text; "
                'to mention someone, use `<at msg_id="message id">display name</at>`; '
                "use `<emoji>emotion or sticker description</emoji>` when you want to send a sticker. "
                "To resend an existing image from context, use "
                '`<image msg_id="message id" index="0">optional description</image>`; '
                'for tool-result media, use `media_index="tool_result:call_x:0"` instead of `msg_id`. '
                "You may combine fragments in send order, for example: "
                '`<text>fine</text><image msg_id="123" index="0">that image</image>`.'
            )

        if locale.startswith("ja"):
            return (
                "送信するメッセージフラグメントだけを出力してください。説明、Markdown、コードブロックは出力しないでください。"
                "通常の文字は `<text>文字</text>` を使います；"
                '`<at msg_id="メッセージID">表示名</at>` で at できます；'
                "スタンプを送りたいときは `<emoji>感情またはスタンプ説明</emoji>` を使います。"
                "文脈中の既存画像を送りたいときは "
                '`<image msg_id="メッセージID" index="0">任意の説明</image>` を使います。'
                'ツール結果のメディアは `msg_id` の代わりに `media_index="tool_result:call_x:0"` を使います。'
                "送信順に複数のフラグメントを組み合わせてもかまいません。例："
                '`<text>まあいいか</text><image msg_id="123" index="0">その画像</image>`。'
            )

        return (
            "请只输出要发送的消息片段，不要输出解释、Markdown 或代码块。"
            "普通文字使用 `<text>文字</text>`；"
            '需要 at 某人时，使用 `<at msg_id="消息编号">显示名</at>`；'
            "想发送表情包时，使用 `<emoji>情绪或表情描述</emoji>`。"
            '想转发上下文里已有图片时，使用 `<image msg_id="消息编号" index="0">可选描述</image>`。'
            '工具返回媒体用 `media_index="tool_result:call_x:0"` 代替 `msg_id`。'
            "可以按发送顺序组合多个片段，例如："
            '`<text>行吧</text><image msg_id="123" index="0">那张图</image>`。'
        )

    @staticmethod
    def _replace_regex_capture_groups(reaction: str, match: re.Match[str]) -> str:
        """将 reaction 中的 [name] 替换为正则命名捕获组的内容。"""
        replaced_reaction = reaction
        for group_name, group_value in match.groupdict().items():
            replaced_reaction = replaced_reaction.replace(f"[{group_name}]", group_value or "")
        return replaced_reaction

    @staticmethod
    def _build_text_from_message_sequence(message: SessionBackedMessage) -> str:
        text_parts: List[str] = []
        for component in getattr(message.raw_message, "components", ()):
            if isinstance(component, TextComponent):
                text_parts.append(component.text)
                continue
            if isinstance(component, AtComponent):
                rendered_at = BaseMaisakaReplyGenerator._render_target_at_component(component)
                if rendered_at:
                    text_parts.append(rendered_at)
                continue
            if isinstance(component, ImageComponent) and component.content:
                text_parts.append(component.content)
                continue
            if isinstance(component, EmojiComponent) and component.content:
                text_parts.append(component.content)
                continue
            if isinstance(component, VoiceComponent) and component.content:
                text_parts.append(component.content)

        return " ".join(part.strip() for part in text_parts if part and part.strip()).strip()

    def _extract_keyword_reaction_match_text(
        self,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
    ) -> str:
        if reply_message is not None:
            return self._build_target_message_content(reply_message).strip()

        for message in reversed(chat_history):
            if not isinstance(message, SessionBackedMessage):
                continue
            if message.source_kind != "user":
                continue
            if message.original_message is not None:
                match_text = self._build_target_message_content(message.original_message).strip()
            else:
                match_text = self._build_text_from_message_sequence(message)
            if not match_text:
                match_text = (message.processed_plain_text or "").strip()
            if match_text:
                return match_text
        return ""

    def _build_keyword_reaction_prompt(
        self,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
    ) -> str:
        match_text = self._extract_keyword_reaction_match_text(chat_history, reply_message)
        if not match_text:
            return ""

        matched_reactions: List[str] = []
        keyword_reaction = global_config.keyword_reaction

        for rule in keyword_reaction.keyword_rules:
            keywords = [keyword.strip() for keyword in rule.keywords if keyword.strip()]
            if keywords and any(keyword in match_text for keyword in keywords):
                reaction = rule.reaction.strip()
                if reaction:
                    matched_reactions.append(reaction)

        for rule in keyword_reaction.regex_rules:
            reaction = rule.reaction.strip()
            if not reaction:
                continue
            for pattern in rule.regex:
                if not pattern.strip():
                    continue
                match = re.search(pattern, match_text)
                if match is None:
                    continue
                matched_reactions.append(self._replace_regex_capture_groups(reaction, match))
                break

        if not matched_reactions:
            return ""

        reaction_lines = "\n".join(f"- {reaction}" for reaction in matched_reactions)
        return f"【关键词反应】\n最新消息命中了预设反应规则，请在回复时优先参考以下要求：\n{reaction_lines}\n"

    def _build_system_prompt(
        self,
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        expression_habits: str = "",
        stream_id: Optional[str] = None,
    ) -> str:
        del reply_message
        del reply_reason
        del expression_habits
        session_id = self._resolve_session_id(stream_id)

        try:
            system_prompt = self._load_prompt(
                "maisaka_replyer",
                bot_name=global_config.bot.nickname,
                group_chat_attention_block=self._build_group_chat_attention_block(session_id),
                replyer_output_instruction=self._build_replyer_output_instruction(),
                identity=self._build_personality_prompt(),
                reply_style=self._select_reply_style(),
            )
        except Exception:
            system_prompt = "你是一个友好的 AI 助手，请根据聊天记录自然回复。"

        return system_prompt

    def _build_reply_instruction(self) -> str:
        if global_config.experimental.enable_replyer_format_output:
            return self._build_replyer_output_instruction()
        return "请自然地回复。不要输出多余说明、括号、@ 或额外标记，只输出实际要发送的内容。"

    def _build_final_user_message(
        self,
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        reply_requirements: str = "",
        keywords_reaction_prompt: str = "",
    ) -> str:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections: List[str] = [f"当前时间：{current_time}"]
        target_message_block = self._build_target_message_block(reply_message)
        if target_message_block:
            sections.append(target_message_block)
        reply_reference_lines: List[str] = []
        if reply_reason.strip():
            reply_reference_lines.append(f"【最新推理】\n{reply_reason.strip()}")
        if reply_reference_lines:
            sections.append("【回复信息参考】\n" + "\n\n".join(reply_reference_lines))
        if reply_requirements.strip():
            sections.append(reply_requirements.strip())
        if keywords_reaction_prompt.strip():
            sections.append(keywords_reaction_prompt.strip())
        sections.append(self._build_reply_instruction())
        return "\n\n".join(sections)

    @staticmethod
    def _build_temporary_reply_style_message(reply_style: str) -> str:
        normalized_reply_style = reply_style.strip()
        if not normalized_reply_style:
            return ""
        return f"你的说话风格可以尝试：\n{normalized_reply_style}"

    def _build_history_messages(
        self,
        chat_history: List[LLMContextMessage],
        enable_visual_message: bool,
    ) -> List[Message]:
        messages: List[Message] = []

        for message in chat_history:
            if isinstance(message, (ReferenceMessage, ToolResultMessage)):
                continue

            if isinstance(message, SessionBackedMessage):
                guided_reply = self._extract_guided_bot_reply(message)
                if guided_reply:
                    messages.append(
                        MessageBuilder().set_role(RoleType.Assistant).add_text_content(guided_reply).build()
                    )
                    continue

                llm_message = build_llm_message_from_context(
                    message,
                    enable_visual_message=enable_visual_message,
                )
                if llm_message is not None:
                    messages.append(llm_message)
                continue

            if isinstance(message, AssistantMessage):
                visible_reply = self._extract_visible_assistant_reply(message)
                if visible_reply:
                    messages.append(
                        MessageBuilder().set_role(RoleType.Assistant).add_text_content(visible_reply).build()
                    )

        return messages

    def _build_request_messages(
        self,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        expression_habits: str = "",
        reply_requirements: str = "",
        stream_id: Optional[str] = None,
        enable_visual_message: bool = False,
    ) -> List[Message]:
        messages: List[Message] = []
        keywords_reaction_prompt = self._build_keyword_reaction_prompt(
            chat_history=chat_history,
            reply_message=reply_message,
        )
        system_prompt = self._build_system_prompt(
            reply_message=reply_message,
            reply_reason=reply_reason,
            expression_habits=expression_habits,
            stream_id=stream_id,
        )
        final_user_message = self._build_final_user_message(
            reply_message=reply_message,
            reply_reason=reply_reason,
            reply_requirements=reply_requirements,
            keywords_reaction_prompt=keywords_reaction_prompt,
        )
        temporary_reply_style_message = self._build_temporary_reply_style_message(self._select_temporary_reply_style())

        messages.append(MessageBuilder().set_role(RoleType.System).add_text_content(system_prompt).build())
        messages.extend(self._build_history_messages(chat_history, enable_visual_message))
        if expression_habits.strip():
            messages.append(
                MessageBuilder().set_role(RoleType.User).add_text_content(expression_habits.strip()).build()
            )
        if temporary_reply_style_message:
            messages.append(
                MessageBuilder().set_role(RoleType.User).add_text_content(temporary_reply_style_message).build()
            )
        messages.append(MessageBuilder().set_role(RoleType.User).add_text_content(final_user_message).build())
        if enable_visual_message:
            return limit_latest_images_in_messages(
                messages,
                max_image_num=global_config.visual.max_image_num,
            )
        return messages

    async def _invoke_before_model_request_hook(
        self,
        *,
        request_messages: List[Message],
        session_id: str,
        active_task_name: str,
        active_model_name: Optional[str],
        model_info: Optional[ModelInfo],
        attempt: int,
        retry_count: int,
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        selected_expression_ids: List[int],
        reply_tool_args: Dict[str, Any],
    ) -> List[Message]:
        """触发 replyer 模型请求前 Hook，允许插件改写最终 messages。"""

        try:
            hook_result = await self._get_runtime_manager().invoke_hook(
                "maisaka.replyer.before_model_request",
                messages=serialize_prompt_messages(request_messages),
                session_id=session_id,
                request_type=self.request_type,
                task_name=active_task_name,
                requested_model_name=active_model_name or "",
                selected_model_name=str(getattr(model_info, "name", "") or ""),
                selected_model_visual=bool(getattr(model_info, "visual", False)),
                attempt=attempt,
                retry_count=retry_count,
                max_retries=REPLYER_MAX_HOOK_RETRIES,
                reply_message_id=str(reply_message.message_id if reply_message is not None else ""),
                reply_reason=reply_reason or "",
                selected_expression_ids=list(selected_expression_ids),
                reply_tool_args=dict(reply_tool_args),
            )
        except Exception as exc:
            logger.warning(f"Maisaka 回复器 before_model_request Hook 调用失败，将继续使用当前请求消息: {exc}")
            return request_messages

        raw_messages = hook_result.kwargs.get("messages")
        if not isinstance(raw_messages, list):
            return request_messages

        try:
            return deserialize_prompt_messages(raw_messages)
        except Exception as exc:
            logger.warning(f"Hook maisaka.replyer.before_model_request 返回的 messages 无法反序列化，已忽略: {exc}")
            return request_messages

    def _resolve_enable_visual_message(self, model_info: Optional[ModelInfo] = None) -> bool:
        if self._enable_visual_message is not None:
            return self._enable_visual_message
        if self._replyer_mode == "multimodal":
            if model_info is not None and not model_info.visual:
                raise ValueError(
                    f"replyer_mode=multimodal，但模型 '{model_info.name}' 未开启 visual，无法使用多模态 replyer"
                )
            return True
        if self._replyer_mode == "text":
            return False
        return bool(model_info.visual) if model_info is not None else False

    def _resolve_session_id(self, stream_id: Optional[str]) -> str:
        if stream_id:
            return stream_id
        if self.chat_stream is not None:
            return self.chat_stream.session_id
        return ""

    @staticmethod
    def _coerce_hook_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        normalized_value = str(value).strip().lower()
        if normalized_value in {"1", "true", "yes", "on", "retry"}:
            return True
        if normalized_value in {"0", "false", "no", "off", "continue"}:
            return False
        return default

    @staticmethod
    def _normalize_reply_tool_args(raw_value: Any) -> Dict[str, Any]:
        """规范化 reply 工具透传给 replyer 的额外参数。"""

        return dict(raw_value) if isinstance(raw_value, dict) else {}

    @staticmethod
    def _build_reply_requirements(extra_prompt: str, retry_constraints: List[str]) -> str:
        """构建 replyer 本轮额外回复要求。"""

        blocks: List[str] = []
        normalized_extra_prompt = extra_prompt.strip()
        if normalized_extra_prompt:
            blocks.append(f"【额外回复要求】\n{normalized_extra_prompt}")
        if retry_constraints:
            retry_lines = ["【重生成约束】"]
            retry_lines.extend(retry_constraints[-REPLYER_MAX_HOOK_RETRIES:])
            blocks.append("\n".join(retry_lines))
        return "\n\n".join(blocks)

    @staticmethod
    def _build_retry_constraint_sentence(retry_reason: str, rejected_response: str) -> str:
        normalized_reason = " ".join((retry_reason or "").split()).rstrip("。！？!?；;，,")
        if not normalized_reason:
            return ""

        normalized_response = " ".join((rejected_response or "").split()).replace('"', '\\"')
        return f'由于{normalized_reason}，之前生成的回复"{normalized_response}"不符合要求，你需要重新生成回复。'

    @staticmethod
    def _get_runtime_manager() -> Any:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager()

    @staticmethod
    def _build_debug_request_filename(stream_id: str, model_name: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        raw_name = f"{timestamp}_{stream_id or 'unknown'}_{model_name or 'unknown'}.json"
        return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in raw_name)

    def _save_debug_reply_request_body(
        self,
        *,
        stream_id: str,
        model_name: str,
        messages: List[Message],
        response_body: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not global_config.debug.record_reply_request:
            return

        try:
            DEBUG_REPLY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            request_body = {
                "model": model_name,
                "request_type": self.request_type,
                "stream_id": stream_id,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "messages": serialize_prompt_messages(messages),
                "response_body": response_body or {},
            }
            file_path = DEBUG_REPLY_CACHE_DIR / self._build_debug_request_filename(stream_id, model_name)
            with file_path.open("w", encoding="utf-8") as file:
                json.dump(request_body, file, ensure_ascii=False, indent=2)
            logger.info(f"Replyer 请求体已保存: {file_path.resolve()}")
        except Exception as exc:
            logger.warning(f"保存 Replyer 请求体失败: {exc}")

    async def _build_reply_context(
        self,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        stream_id: Optional[str],
        sub_agent_runner: Optional[Callable[[str], Awaitable[str]]],
        reply_tool_args: Optional[Dict[str, Any]] = None,
    ) -> MaisakaReplyContext:
        session_id = self._resolve_session_id(stream_id)
        if not session_id:
            logger.warning("构建 Maisaka 回复上下文失败：缺少会话标识")
            return MaisakaReplyContext()

        if sub_agent_runner is None:
            logger.info("表达方式选择跳过：缺少子代理执行器")
            return MaisakaReplyContext()

        selection_result = await maisaka_expression_selector.select_for_reply(
            session_id=session_id,
            chat_history=chat_history,
            reply_message=reply_message,
            reply_reason=reply_reason,
            reply_tool_args=reply_tool_args or {},
            sub_agent_runner=sub_agent_runner,
        )
        return MaisakaReplyContext(
            expression_habits=selection_result.expression_habits,
            selected_expression_ids=selection_result.selected_expression_ids,
            selected_expressions=selection_result.selected_expressions,
        )

    @staticmethod
    def _should_keep_replyer_history_message(message: LLMContextMessage) -> bool:
        """replyer 只接收真实聊天上下文，不接收参考、工具结果和中期摘要。"""

        if isinstance(message, (ReferenceMessage, ToolResultMessage)):
            return False
        return not is_mid_term_memory_message(message)

    async def generate_reply_with_context(
        self,
        extra_info: str = "",
        reply_reason: str = "",
        available_actions: Optional[Dict[str, ActionInfo]] = None,
        chosen_actions: Optional[List[object]] = None,
        from_plugin: bool = True,
        stream_id: Optional[str] = None,
        reply_message: Optional[SessionMessage] = None,
        reply_time_point: Optional[float] = None,
        think_level: int = 1,
        unknown_words: Optional[List[str]] = None,
        log_reply: bool = True,
        chat_history: Optional[List[LLMContextMessage]] = None,
        expression_habits: str = "",
        selected_expression_ids: Optional[List[int]] = None,
        selected_expressions: Optional[List[Dict[str, Any]]] = None,
        sub_agent_runner: Optional[Callable[[str], Awaitable[str]]] = None,
        reply_tool_args: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, ReplyGenerationResult]:
        def finalize(success_value: bool) -> Tuple[bool, ReplyGenerationResult]:
            result.monitor_detail = build_reply_monitor_detail(result)
            return success_value, result

        del available_actions
        del chosen_actions
        del extra_info
        del from_plugin
        del log_reply
        del reply_time_point
        del think_level
        del unknown_words

        result = ReplyGenerationResult()
        overall_started_at = time.perf_counter()
        if chat_history is None:
            result.error_message = "聊天历史为空"
            return finalize(False)

        # logger.info(
        #     f"Maisaka 回复器开始生成: 流={stream_id} 原因={reply_reason!r} "
        #     f"历史条数={len(chat_history)} 目标ID={reply_message.message_id if reply_message else None}"
        # )

        filtered_history = [message for message in chat_history if self._should_keep_replyer_history_message(message)]

        if self.express_model is None:
            logger.error("回复模型未初始化")
            result.error_message = "回复模型尚未初始化"
            return finalize(False)

        active_reply_tool_args = self._normalize_reply_tool_args(reply_tool_args)

        try:
            reply_context = await self._build_reply_context(
                chat_history=filtered_history,
                reply_message=reply_message,
                reply_reason=reply_reason or "",
                stream_id=stream_id,
                sub_agent_runner=sub_agent_runner,
                reply_tool_args=active_reply_tool_args,
            )
        except Exception as exc:
            import traceback

            logger.error(f"构建回复上下文失败: {exc}\n{traceback.format_exc()}")
            result.error_message = f"构建回复上下文失败: {exc}"
            result.metrics = GenerationMetrics(
                overall_ms=round((time.perf_counter() - overall_started_at) * 1000, 2),
            )
            return finalize(False)

        merged_expression_habits = expression_habits.strip() or reply_context.expression_habits
        result.selected_expression_ids = (
            list(selected_expression_ids)
            if selected_expression_ids is not None
            else list(reply_context.selected_expression_ids)
        )
        result.selected_expression_details = (
            list(selected_expressions) if selected_expressions is not None else list(reply_context.selected_expressions)
        )

        # logger.info(
        #     f"回复上下文完成 流={stream_id} 已选表达={result.selected_expression_ids!r}"
        # )

        show_replyer_prompt = bool(getattr(global_config.debug, "show_replyer_prompt", False))
        show_replyer_reasoning = bool(getattr(global_config.debug, "show_replyer_reasoning", False))
        preview_chat_id = self._resolve_session_id(stream_id)
        replyer_prompt_section: RenderableType | None = None
        retry_constraints: List[str] = []
        retry_reasons: List[str] = []
        retry_events: List[Dict[str, Any]] = []
        hook_rewrite_events: List[Dict[str, str]] = []
        retry_count = 0
        aggregate_prompt_tokens = 0
        aggregate_completion_tokens = 0
        aggregate_total_tokens = 0
        default_task_name = str(getattr(self.express_model, "task_name", "") or "replyer").strip() or "replyer"

        while True:
            try:
                before_request_result = await self._get_runtime_manager().invoke_hook(
                    "maisaka.replyer.before_request",
                    session_id=preview_chat_id,
                    request_type=self.request_type,
                    task_name=default_task_name,
                    model_name="",
                    extra_prompt="",
                    attempt=retry_count + 1,
                    retry_count=retry_count,
                    max_retries=REPLYER_MAX_HOOK_RETRIES,
                    reply_message_id=str(reply_message.message_id if reply_message is not None else ""),
                    reply_reason=reply_reason or "",
                    selected_expression_ids=list(result.selected_expression_ids),
                    reply_tool_args=dict(active_reply_tool_args),
                )
                before_request_kwargs = before_request_result.kwargs
                if isinstance(before_request_kwargs.get("reply_tool_args"), dict):
                    active_reply_tool_args = dict(before_request_kwargs["reply_tool_args"])
            except Exception as exc:
                logger.warning(f"Maisaka 回复器 before_request Hook 调用失败，将继续使用当前请求参数: {exc}")
                before_request_kwargs = {}

            active_task_name = str(before_request_kwargs.get("task_name") or default_task_name).strip()
            if not active_task_name:
                active_task_name = default_task_name
            active_model_name = str(before_request_kwargs.get("model_name") or "").strip() or None
            active_reply_requirements = self._build_reply_requirements(
                str(before_request_kwargs.get("extra_prompt") or ""),
                retry_constraints,
            )

            prompt_started_at = time.perf_counter()
            try:
                request_messages = self._build_request_messages(
                    chat_history=filtered_history,
                    reply_message=reply_message,
                    reply_reason=reply_reason or "",
                    expression_habits=merged_expression_habits,
                    reply_requirements=active_reply_requirements,
                    stream_id=stream_id,
                )
            except Exception as exc:
                import traceback

                logger.error(f"构建提示词失败: {exc}\n{traceback.format_exc()}")
                result.error_message = f"构建提示词失败: {exc}"
                result.metrics = GenerationMetrics(
                    overall_ms=round((time.perf_counter() - overall_started_at) * 1000, 2),
                )
                return finalize(False)

            prompt_ms = round((time.perf_counter() - prompt_started_at) * 1000, 2)
            prompt_preview = PromptCLIVisualizer._build_prompt_dump_text(request_messages)

            async def message_factory(
                _client: object,
                model_info: Optional[ModelInfo] = None,
                reply_requirements_for_attempt: str = active_reply_requirements,
                active_task_name_for_attempt: str = active_task_name,
                active_model_name_for_attempt: Optional[str] = active_model_name,
                retry_count_for_attempt: int = retry_count,
                selected_expression_ids_for_attempt: tuple[int, ...] = tuple(result.selected_expression_ids),
                reply_tool_args_for_attempt: tuple[tuple[str, Any], ...] = tuple(active_reply_tool_args.items()),
            ) -> List[Message]:
                nonlocal prompt_ms, prompt_preview, request_messages
                prompt_started_at = time.perf_counter()
                built_request_messages = self._build_request_messages(
                    chat_history=filtered_history,
                    reply_message=reply_message,
                    reply_reason=reply_reason or "",
                    expression_habits=merged_expression_habits,
                    reply_requirements=reply_requirements_for_attempt,
                    stream_id=stream_id,
                    enable_visual_message=self._resolve_enable_visual_message(model_info),
                )
                request_messages = await self._invoke_before_model_request_hook(
                    request_messages=built_request_messages,
                    session_id=preview_chat_id,
                    active_task_name=active_task_name_for_attempt,
                    active_model_name=active_model_name_for_attempt,
                    model_info=model_info,
                    attempt=retry_count_for_attempt + 1,
                    retry_count=retry_count_for_attempt,
                    reply_message=reply_message,
                    reply_reason=reply_reason or "",
                    selected_expression_ids=list(selected_expression_ids_for_attempt),
                    reply_tool_args=dict(reply_tool_args_for_attempt),
                )
                prompt_ms = round((time.perf_counter() - prompt_started_at) * 1000, 2)
                prompt_preview = PromptCLIVisualizer._build_prompt_dump_text(request_messages)
                return request_messages

            llm_started_at = time.perf_counter()
            try:
                active_model = self.express_model
                if active_task_name != default_task_name:
                    active_model = self._llm_client_cls(
                        task_name=active_task_name,
                        request_type=self.request_type,
                        session_id=preview_chat_id,
                    )
                generation_result = await active_model.generate_response_with_messages(
                    message_factory=message_factory,
                    options=LLMGenerationOptions(model_name=active_model_name),
                )
            except Exception as exc:
                logger.exception("Maisaka 回复器调用失败")
                result.error_message = str(exc)
                result.metrics = GenerationMetrics(
                    prompt_ms=prompt_ms,
                    llm_ms=round((time.perf_counter() - llm_started_at) * 1000, 2),
                    overall_ms=round((time.perf_counter() - overall_started_at) * 1000, 2),
                )
                return finalize(False)

            result.completion.request_prompt = prompt_preview
            result.request_messages = serialize_prompt_messages(request_messages)
            self._save_debug_reply_request_body(
                stream_id=preview_chat_id,
                model_name=generation_result.model_name or "",
                messages=request_messages,
                response_body={
                    "response": generation_result.response,
                    "reasoning": generation_result.reasoning,
                    "model_name": generation_result.model_name,
                    "tool_calls": [
                        {
                            "id": tool_call.call_id,
                            "name": tool_call.func_name,
                            "arguments": tool_call.args,
                            "extra_content": tool_call.extra_content,
                        }
                        for tool_call in (generation_result.tool_calls or [])
                    ],
                    "prompt_tokens": generation_result.prompt_tokens,
                    "completion_tokens": generation_result.completion_tokens,
                    "total_tokens": generation_result.total_tokens,
                    "prompt_cache_hit_tokens": getattr(generation_result, "prompt_cache_hit_tokens", 0) or 0,
                    "prompt_cache_miss_tokens": getattr(generation_result, "prompt_cache_miss_tokens", 0) or 0,
                    "replyer_retry_count": retry_count,
                },
            )
            llm_ms = round((time.perf_counter() - llm_started_at) * 1000, 2)
            response_text = (generation_result.response or "").strip()
            aggregate_prompt_tokens += generation_result.prompt_tokens
            aggregate_completion_tokens += generation_result.completion_tokens
            aggregate_total_tokens += generation_result.total_tokens
            hook_original_response = response_text

            try:
                after_response_result = await self._get_runtime_manager().invoke_hook(
                    "maisaka.replyer.after_response",
                    response=response_text,
                    session_id=preview_chat_id,
                    request_type=self.request_type,
                    task_name=active_task_name,
                    requested_model_name=active_model_name or "",
                    attempt=retry_count + 1,
                    retry_count=retry_count,
                    max_retries=REPLYER_MAX_HOOK_RETRIES,
                    reply_message_id=str(reply_message.message_id if reply_message is not None else ""),
                    selected_expression_ids=list(result.selected_expression_ids),
                    reply_tool_args=dict(active_reply_tool_args),
                    prompt_tokens=generation_result.prompt_tokens,
                    completion_tokens=generation_result.completion_tokens,
                    total_tokens=generation_result.total_tokens,
                )
                after_response_kwargs = after_response_result.kwargs
            except Exception as exc:
                logger.warning(f"Maisaka 回复器 after_response Hook 调用失败，将继续使用当前回复: {exc}")
                after_response_kwargs = {}
            if "response" in after_response_kwargs:
                hook_modified_response = str(after_response_kwargs.get("response") or "").strip()
                if hook_modified_response != response_text:
                    rewrite_event = {
                        "attempt": str(retry_count + 1),
                        "before": hook_original_response,
                        "after": hook_modified_response,
                    }
                    hook_rewrite_events.append(rewrite_event)
                    logger.warning(
                        "Maisaka 回复器回复被 Hook 改写: "
                        f"session={preview_chat_id} attempt={retry_count + 1} "
                        f"before={self._normalize_content(hook_original_response, limit=300)!r} "
                        f"after={self._normalize_content(hook_modified_response, limit=300)!r}"
                    )
                response_text = hook_modified_response
            retry_requested = self._coerce_hook_bool(after_response_kwargs.get("retry"), default=False)
            matched_regex = str(after_response_kwargs.get("matched_regex") or "").strip()
            matched_regex_pattern = str(after_response_kwargs.get("matched_regex_pattern") or "").strip()
            matched_regex_description = str(after_response_kwargs.get("matched_regex_description") or "").strip()
            retry_reason = str(after_response_kwargs.get("retry_reason") or "").strip()
            if retry_requested and retry_count < REPLYER_MAX_HOOK_RETRIES:
                reason_parts = []
                if matched_regex:
                    reason_parts.append(f"命中规则: {matched_regex}")
                if matched_regex_description:
                    reason_parts.append(f"规则说明: {matched_regex_description}")
                if retry_reason:
                    reason_parts.append(retry_reason)
                if response_text:
                    reason_parts.append(f"被拦截回复: {response_text!r}")
                retry_log_reason = "；".join(reason_parts) or "Hook 请求重生成"
                retry_events.append(
                    {
                        "attempt": retry_count + 1,
                        "matched_regex": matched_regex,
                        "matched_regex_pattern": matched_regex_pattern,
                        "matched_regex_description": matched_regex_description,
                        "retry_reason": retry_reason,
                        "rejected_response": response_text,
                    }
                )
                retry_reasons.append(retry_log_reason)
                retry_constraint = self._build_retry_constraint_sentence(retry_reason, response_text)
                if retry_constraint:
                    retry_constraints.append(retry_constraint)
                retry_count += 1
                logger.warning(
                    "Maisaka 回复器触发重生成: "
                    f"session={preview_chat_id} attempt={retry_count} "
                    f"retry={retry_count}/{REPLYER_MAX_HOOK_RETRIES} "
                    f"constraint={'有' if retry_reason else '无'} "
                    f"rule={matched_regex or 'unknown'} "
                    f"pattern={matched_regex_pattern or 'unknown'} "
                    f"reason={retry_log_reason} "
                    f"rejected={self._normalize_content(response_text, limit=300)!r}"
                )
                continue
            if retry_requested:
                logger.warning(
                    f"Maisaka 回复器已达到重生成上限，将使用最后一次回复: "
                    f"session={preview_chat_id} retry={retry_count}/{REPLYER_MAX_HOOK_RETRIES} "
                    f"rule={matched_regex or 'unknown'} "
                    f"pattern={matched_regex_pattern or 'unknown'} "
                    f"response={self._normalize_content(response_text, limit=300)!r}"
                )
            break

        if show_replyer_prompt:
            replyer_prompt_section = Panel(
                PromptCLIVisualizer.build_prompt_access_panel(
                    request_messages,
                    category="replyer",
                    chat_id=preview_chat_id,
                    request_kind="replyer",
                    selection_reason=f"ID: {preview_chat_id}",
                    output_content=response_text,
                    metadata={
                        "model_name": generation_result.model_name or "",
                        "duration_ms": llm_ms,
                    },
                ),
                title="Reply Prompt",
                border_style="bright_yellow",
                padding=(0, 1),
            )
        result.success = bool(response_text)
        result.completion = LLMCompletionResult(
            request_prompt=prompt_preview,
            response_text=response_text,
            reasoning_text=generation_result.reasoning or "",
            model_name=generation_result.model_name or "",
            tool_calls=generation_result.tool_calls or [],
            prompt_tokens=generation_result.prompt_tokens,
            completion_tokens=generation_result.completion_tokens,
            total_tokens=generation_result.total_tokens,
        )
        result.metrics = GenerationMetrics(
            prompt_ms=prompt_ms,
            llm_ms=llm_ms,
            overall_ms=round((time.perf_counter() - overall_started_at) * 1000, 2),
            stage_logs=[
                f"prompt: {prompt_ms} ms",
                f"llm: {llm_ms} ms",
            ],
        )
        prompt_cache_hit_tokens = getattr(generation_result, "prompt_cache_hit_tokens", 0) or 0
        prompt_cache_miss_tokens = getattr(generation_result, "prompt_cache_miss_tokens", 0) or 0
        if prompt_cache_miss_tokens == 0 and prompt_cache_hit_tokens > 0:
            prompt_cache_miss_tokens = max(generation_result.prompt_tokens - prompt_cache_hit_tokens, 0)
        prompt_cache_total_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens
        prompt_cache_hit_rate = (
            prompt_cache_hit_tokens / prompt_cache_total_tokens * 100 if prompt_cache_total_tokens > 0 else 0
        )
        result.metrics.extra["prompt_cache_hit_tokens"] = prompt_cache_hit_tokens
        result.metrics.extra["prompt_cache_miss_tokens"] = prompt_cache_miss_tokens
        result.metrics.extra["prompt_cache_hit_rate"] = round(prompt_cache_hit_rate, 2)
        result.metrics.extra["replyer_retry_count"] = retry_count
        result.metrics.extra["replyer_attempt_count"] = retry_count + 1
        result.metrics.extra["replyer_aggregate_prompt_tokens"] = aggregate_prompt_tokens
        result.metrics.extra["replyer_aggregate_completion_tokens"] = aggregate_completion_tokens
        result.metrics.extra["replyer_aggregate_total_tokens"] = aggregate_total_tokens
        if result.selected_expression_ids and merged_expression_habits.strip():
            result.metrics.extra["selected_expression_habits"] = merged_expression_habits.strip()
        if retry_reasons:
            result.metrics.extra["replyer_retry_reasons"] = list(retry_reasons)
        if retry_events:
            result.metrics.extra["replyer_retry_events"] = list(retry_events)
        if retry_constraints:
            result.metrics.extra["replyer_retry_constraints"] = list(retry_constraints)
        if hook_rewrite_events:
            result.metrics.extra["replyer_hook_rewrite_events"] = list(hook_rewrite_events)
        logger.info(
            "Replyer KV cache usage - "
            f"hit_tokens={prompt_cache_hit_tokens}, "
            f"miss_tokens={prompt_cache_miss_tokens}, "
            f"hit_rate={prompt_cache_hit_rate:.2f}%, "
            f"prompt_tokens={generation_result.prompt_tokens}"
        )

        if show_replyer_reasoning and result.completion.reasoning_text:
            logger.info(f"Maisaka 回复器思考内容:\n{result.completion.reasoning_text}")

        if not result.success:
            result.error_message = "回复器返回了空内容"
            logger.warning("Maisaka 回复器返回了空内容")
            return finalize(False)

        logger.info(
            f"Maisaka 回复器生成成功 文本={response_text!r} "
            f"总耗时ms={result.metrics.overall_ms} 重生成次数={retry_count} "
            f"已选表达={result.selected_expression_ids!r}"
        )
        if retry_count > 0:
            logger.info(
                "Maisaka 回复器重生成完成: "
                f"session={preview_chat_id} attempts={retry_count + 1} "
                f"retry_count={retry_count} final={self._normalize_content(response_text, limit=300)!r}"
            )
        if show_replyer_prompt or show_replyer_reasoning:
            summary_lines = [
                f"流ID: {preview_chat_id or 'unknown'}",
                f"耗时: {result.metrics.overall_ms} ms",
            ]
            if result.selected_expression_ids:
                summary_lines.append(f"表达编号: {result.selected_expression_ids!r}")

            renderables: List[RenderableType] = [Text("\n".join(summary_lines))]
            if replyer_prompt_section is not None:
                renderables.append(replyer_prompt_section)
            if show_replyer_reasoning and result.completion.reasoning_text:
                renderables.append(
                    Panel(
                        Text(result.completion.reasoning_text),
                        title="思考内容",
                        border_style="magenta",
                        padding=(0, 1),
                    )
                )
            renderables.append(
                Panel(
                    Text(response_text),
                    title="回复结果",
                    border_style="green",
                    padding=(0, 1),
                )
            )
            console.print(
                Panel(
                    Group(*renderables),
                    title="MaiSaka 回复器",
                    border_style="bright_yellow",
                    padding=(0, 1),
                )
            )
        result.text_fragments = [response_text]
        return finalize(True)
