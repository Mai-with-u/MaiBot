from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

import json
import random
import time

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from src.chat.message_receive.chat_manager import BotChatSession
from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import get_chat_type_and_target_info
from src.cli.console import console
from src.common.data_models.reply_generation_data_models import (
    GenerationMetrics,
    LLMCompletionResult,
    ReplyGenerationResult,
    build_reply_monitor_detail,
)
from src.common.data_models.message_component_data_model import (
    AtComponent,
    EmojiComponent,
    ImageComponent,
    ReplyComponent,
    TextComponent,
    VoiceComponent,
)
from src.common.logger import get_logger
from src.common.utils.utils_config import ChatConfigUtils
from src.config.config import global_config
from src.config.model_configs import ModelInfo
from src.core.types import ActionInfo
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.maisaka.context_messages import (
    AssistantMessage,
    LLMContextMessage,
    ReferenceMessage,
    SessionBackedMessage,
    ToolResultMessage,
    build_llm_message_from_context,
)
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.message_adapter import parse_speaker_content
from src.plugin_runtime.hook_payloads import serialize_prompt_messages

from .maisaka_expression_selector import maisaka_expression_selector

logger = get_logger("replyer")

DEBUG_REPLY_CACHE_DIR = Path("logs/debug_reply_cache")


@dataclass
class MaisakaReplyContext:
    """Maisaka replyer 使用的回复上下文。"""

    expression_habits: str = ""
    selected_expression_ids: List[int] = field(default_factory=list)


class BaseMaisakaReplyGenerator:
    """Maisaka replyer 的共享实现。"""

    def __init__(
        self,
        *,
        chat_stream: Optional[BotChatSession] = None,
        request_type: str = "maisaka_replyer",
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
        """按配置概率选择本次 replyer 使用的表达风格。"""
        personality_config = global_config.personality
        reply_style = personality_config.reply_style
        candidate_styles = [style.strip() for style in personality_config.multiple_reply_style if style.strip()]

        if not candidate_styles:
            return reply_style

        probability = personality_config.multiple_probability
        if probability <= 0:
            return reply_style
        if random.random() > probability:
            return reply_style

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
        target_time = reply_message.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        target_content = self._normalize_content(self._build_target_message_content(reply_message), limit=300)
        if not target_content:
            target_content = "[无可见文本内容]"

        return (
            "【本次回复目标】\n"
            f"- msg_id：{target_message_id}\n"
            f"- 时间：{target_time}\n"
            f"- 用户名：{sender_name}\n"
            f"- 发言内容：{target_content}\n\n"
            "你这次要回复的就是这条目标消息，请结合整段上下文理解，但不要把其他历史消息当成当前回复对象。"
        )

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
                target_message_id = component.target_message_id.strip()
                if target_message_id:
                    rendered_parts.append(f"[引用:quote_id={target_message_id}]")
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
    def _build_replyer_at_block() -> str:
        """构建 replyer 模式下的 at 标记说明。"""

        if not global_config.chat.enable_at:
            return ""
        return (
            "如果需要提及某人、让某人关注你的回复，可以在回复中加入 `at[msg_id]` 标记，"
            "其中 msg_id 应使用聊天记录中该用户发过的消息编号；"
            "消息发送时会检查这种标记并转换为真正的 at 消息。\n"
        )

    def _build_system_prompt(
        self,
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        reference_info: str = "",
        expression_habits: str = "",
        stream_id: Optional[str] = None,
    ) -> str:
        del reply_message
        del reply_reason
        del reference_info
        del expression_habits
        session_id = self._resolve_session_id(stream_id)

        try:
            system_prompt = self._load_prompt(
                "maisaka_replyer",
                bot_name=global_config.bot.nickname,
                group_chat_attention_block=self._build_group_chat_attention_block(session_id),
                replyer_at_block=self._build_replyer_at_block(),
                identity=self._build_personality_prompt(),
                reply_style=self._select_reply_style(),
            )
        except Exception:
            system_prompt = "你是一个友好的 AI 助手，请根据聊天记录自然回复。"

        return system_prompt

    def _build_reply_instruction(self) -> str:
        return "请自然地回复。不要输出多余说明、括号、@ 或额外标记，只输出实际要发送的内容。"

    def _build_final_user_message(
        self,
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        reference_info: str = "",
        expression_habits: str = "",
    ) -> str:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections: List[str] = [f"当前时间：{current_time}"]
        if expression_habits.strip():
            sections.append(expression_habits.strip())
        target_message_block = self._build_target_message_block(reply_message)
        if target_message_block:
            sections.append(target_message_block)
        reply_reference_lines: List[str] = []
        if reply_reason.strip():
            reply_reference_lines.append(f"【最新推理】\n{reply_reason.strip()}")
        if reference_info.strip():
            reply_reference_lines.append(f"【参考信息】\n{reference_info.strip()}")
        if reply_reference_lines:
            sections.append("【回复信息参考】\n" + "\n\n".join(reply_reference_lines))
        sections.append(self._build_reply_instruction())
        return "\n\n".join(sections)

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
        reference_info: str = "",
        expression_habits: str = "",
        stream_id: Optional[str] = None,
        enable_visual_message: bool = False,
    ) -> List[Message]:
        messages: List[Message] = []
        system_prompt = self._build_system_prompt(
            reply_message=reply_message,
            reply_reason=reply_reason,
            reference_info=reference_info,
            expression_habits=expression_habits,
            stream_id=stream_id,
        )
        final_user_message = self._build_final_user_message(
            reply_message=reply_message,
            reply_reason=reply_reason,
            reference_info=reference_info,
            expression_habits=expression_habits,
        )

        messages.append(MessageBuilder().set_role(RoleType.System).add_text_content(system_prompt).build())
        messages.extend(self._build_history_messages(chat_history, enable_visual_message))
        messages.append(MessageBuilder().set_role(RoleType.User).add_text_content(final_user_message).build())
        return messages

    def _resolve_enable_visual_message(self, model_info: Optional[ModelInfo] = None) -> bool:
        if self._enable_visual_message is not None:
            return self._enable_visual_message
        if self._replyer_mode == "multimodal":
            if model_info is not None and not model_info.visual:
                raise ValueError(f"replyer_mode=multimodal，但模型 '{model_info.name}' 未开启 visual，无法使用多模态 replyer")
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
            sub_agent_runner=sub_agent_runner,
        )
        return MaisakaReplyContext(
            expression_habits=selection_result.expression_habits,
            selected_expression_ids=selection_result.selected_expression_ids,
        )

    async def generate_reply_with_context(
        self,
        extra_info: str = "",
        reply_reason: str = "",
        reference_info: str = "",
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
        sub_agent_runner: Optional[Callable[[str], Awaitable[str]]] = None,
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

        filtered_history = [
            message
            for message in chat_history
            if not isinstance(message, (ReferenceMessage, ToolResultMessage))
        ]

        if self.express_model is None:
            logger.error("回复模型未初始化")
            result.error_message = "回复模型尚未初始化"
            return finalize(False)

        try:
            reply_context = await self._build_reply_context(
                chat_history=filtered_history,
                reply_message=reply_message,
                reply_reason=reply_reason or "",
                stream_id=stream_id,
                sub_agent_runner=sub_agent_runner,
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

        # logger.info(
        #     f"回复上下文完成 流={stream_id} 已选表达={result.selected_expression_ids!r}"
        # )

        prompt_started_at = time.perf_counter()
        try:
            request_messages = self._build_request_messages(
                chat_history=filtered_history,
                reply_message=reply_message,
                reply_reason=reply_reason or "",
                reference_info=reference_info or "",
                expression_habits=merged_expression_habits,
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
        show_replyer_prompt = bool(getattr(global_config.debug, "show_replyer_prompt", False))
        show_replyer_reasoning = bool(getattr(global_config.debug, "show_replyer_reasoning", False))

        def message_factory(_client: object, model_info: Optional[ModelInfo] = None) -> List[Message]:
            nonlocal prompt_ms, prompt_preview, request_messages
            prompt_started_at = time.perf_counter()
            request_messages = self._build_request_messages(
                chat_history=filtered_history,
                reply_message=reply_message,
                reply_reason=reply_reason or "",
                reference_info=reference_info or "",
                expression_habits=merged_expression_habits,
                stream_id=stream_id,
                enable_visual_message=self._resolve_enable_visual_message(model_info),
            )
            prompt_ms = round((time.perf_counter() - prompt_started_at) * 1000, 2)
            prompt_preview = PromptCLIVisualizer._build_prompt_dump_text(request_messages)
            return request_messages

        preview_chat_id = self._resolve_session_id(stream_id)
        replyer_prompt_section: RenderableType | None = None
        if show_replyer_prompt:
            replyer_prompt_section = Panel(
                PromptCLIVisualizer.build_prompt_access_panel(
                    request_messages,
                    category="replyer",
                    chat_id=preview_chat_id,
                    request_kind="replyer",
                    selection_reason=f"ID: {preview_chat_id}",
                ),
                title="Reply Prompt",
                border_style="bright_yellow",
                padding=(0, 1),
            )

        llm_started_at = time.perf_counter()
        try:
            generation_result = await self.express_model.generate_response_with_messages(
                message_factory=message_factory
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
        )
        llm_ms = round((time.perf_counter() - llm_started_at) * 1000, 2)
        response_text = (generation_result.response or "").strip()
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
            prompt_cache_hit_tokens / prompt_cache_total_tokens * 100
            if prompt_cache_total_tokens > 0
            else 0
        )
        result.metrics.extra["prompt_cache_hit_tokens"] = prompt_cache_hit_tokens
        result.metrics.extra["prompt_cache_miss_tokens"] = prompt_cache_miss_tokens
        result.metrics.extra["prompt_cache_hit_rate"] = round(prompt_cache_hit_rate, 2)
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
            f"总耗时ms={result.metrics.overall_ms} 已选表达={result.selected_expression_ids!r}"
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
