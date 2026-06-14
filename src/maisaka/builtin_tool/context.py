"""Maisaka 内置工具执行上下文。"""

from __future__ import annotations

from base64 import b64decode
from datetime import datetime
from html import unescape
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

import re

from src.chat.utils.utils import process_llm_response
from src.common.data_models.message_component_data_model import (
    AtComponent,
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.common.logger import get_logger
from src.config.config import global_config
from src.core.tooling import ToolExecutionResult

from src.maisaka.context.messages import SessionBackedMessage
from src.maisaka.context.message_adapter import format_speaker_content
from src.maisaka.context.planner_messages import (
    build_planner_prefix,
    build_session_backed_text_message,
    extract_quote_ids_from_message_sequence,
)

if TYPE_CHECKING:
    from src.maisaka.reasoning_engine import MaisakaReasoningEngine
    from src.maisaka.runtime import MaisakaHeartFlowChatting

FORMATTED_REPLY_TAG_PATTERN = re.compile(
    r"<(?P<tag>text|at|emoji|image)(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
FORMATTED_REPLY_ATTR_PATTERN = re.compile(
    r"(?P<key>[a-zA-Z_][\w:-]*)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)
FORMATTED_REPLY_CODE_FENCE_PATTERN = re.compile(
    r"^```(?:xml|html|reply|text)?\s*(?P<body>.*?)\s*```$",
    re.IGNORECASE | re.DOTALL,
)
logger = get_logger("maisaka_builtin_context")


class BuiltinToolRuntimeContext:
    """为拆分后的内置工具提供统一运行时能力。"""

    def __init__(
        self,
        engine: "MaisakaReasoningEngine",
        runtime: "MaisakaHeartFlowChatting",
    ) -> None:
        self.engine = engine
        self.runtime = runtime

    @staticmethod
    def build_success_result(
        tool_name: str,
        content: str = "",
        structured_content: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        post_history_messages: Optional[Sequence[Any]] = None,
    ) -> ToolExecutionResult:
        """构造统一工具成功结果。"""

        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=content,
            structured_content=structured_content,
            post_history_messages=list(post_history_messages or []),
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def build_failure_result(
        tool_name: str,
        error_message: str,
        structured_content: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ToolExecutionResult:
        """构造统一工具失败结果。"""

        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error_message=error_message,
            structured_content=structured_content,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def normalize_words(raw_words: Any) -> List[str]:
        """清洗黑话查询词条列表。"""

        if not isinstance(raw_words, list):
            return []

        normalized_words: List[str] = []
        seen_words: set[str] = set()
        for item in raw_words:
            if not isinstance(item, str):
                continue
            word = item.strip()
            if not word or word in seen_words:
                continue
            seen_words.add(word)
            normalized_words.append(word)
        return normalized_words

    @staticmethod
    def normalize_jargon_query_results(raw_results: Any) -> List[Dict[str, object]]:
        """规范化黑话查询结果列表。"""

        if not isinstance(raw_results, list):
            return []

        normalized_results: List[Dict[str, object]] = []
        for raw_item in raw_results:
            if not isinstance(raw_item, dict):
                continue
            word = str(raw_item.get("word") or "").strip()
            matches = raw_item.get("matches")
            normalized_matches: List[Dict[str, str]] = []
            if isinstance(matches, list):
                for match in matches:
                    if not isinstance(match, dict):
                        continue
                    content = str(match.get("content") or "").strip()
                    meaning = str(match.get("meaning") or "").strip()
                    if not content or not meaning:
                        continue
                    normalized_matches.append({"content": content, "meaning": meaning})

            normalized_results.append(
                {
                    "word": word,
                    "found": bool(raw_item.get("found", bool(normalized_matches))),
                    "matches": normalized_matches,
                }
            )
        return normalized_results

    @staticmethod
    def post_process_reply_text(reply_text: str) -> List[str]:
        """沿用旧回复链的文本后处理，执行分段与错别字注入。"""

        processed_segments: List[str] = []
        for segment in process_llm_response(reply_text):
            normalized_segment = segment.strip()
            if normalized_segment:
                processed_segments.append(normalized_segment)

        if processed_segments:
            return processed_segments
        return [reply_text.strip()]

    @staticmethod
    def _post_process_reply_text_chunk(text: str) -> List[str]:
        """处理回复中的普通文本片段。"""

        processed_segments: List[str] = []
        for segment in process_llm_response(text):
            normalized_segment = segment.strip()
            if normalized_segment:
                processed_segments.append(normalized_segment)
        return processed_segments

    def _build_at_component_for_message_id(self, message_id: str) -> Optional[AtComponent]:
        """根据消息编号构造 at 组件。"""

        target_message = self.runtime.find_source_message_by_id(message_id)
        if target_message is None:
            return None

        message_info = getattr(target_message, "message_info", None)
        user_info = getattr(message_info, "user_info", None)
        target_user_id = str(getattr(user_info, "user_id", "") or "").strip()
        if not target_user_id:
            return None

        target_user_nickname = str(getattr(user_info, "user_nickname", "") or "").strip()
        target_user_cardname = str(getattr(user_info, "user_cardname", "") or "").strip()
        return AtComponent(
            target_user_id=target_user_id,
            target_user_nickname=target_user_nickname or None,
            target_user_cardname=target_user_cardname or None,
        )

    def _build_at_component_for_display_name(self, display_name: str) -> Optional[AtComponent]:
        """根据近期聊天中的展示名构造 at 组件。"""

        normalized_display_name = display_name.strip().lstrip("@")
        if not normalized_display_name:
            return None

        history_messages = list(getattr(self.runtime, "_chat_history", []) or [])
        for history_message in reversed(history_messages):
            candidate_message = getattr(history_message, "original_message", None) or history_message
            message_info = getattr(candidate_message, "message_info", None)
            user_info = getattr(message_info, "user_info", None)
            if user_info is None:
                continue

            target_user_id = str(getattr(user_info, "user_id", "") or "").strip()
            if not target_user_id:
                continue

            target_user_nickname = str(getattr(user_info, "user_nickname", "") or "").strip()
            target_user_cardname = str(getattr(user_info, "user_cardname", "") or "").strip()
            candidate_names = {
                value
                for value in (target_user_id, target_user_nickname, target_user_cardname)
                if value
            }
            if normalized_display_name not in candidate_names:
                continue

            return AtComponent(
                target_user_id=target_user_id,
                target_user_nickname=target_user_nickname or None,
                target_user_cardname=target_user_cardname or None,
            )

        return None

    def _build_at_component_for_formatted_tag(self, attrs: Dict[str, str], body: str) -> Optional[AtComponent]:
        """解析格式化回复中的 at 片段。"""

        message_id = (
            attrs.get("msg_id")
            or attrs.get("message_id")
            or attrs.get("id")
            or ""
        ).strip()
        if message_id:
            at_component = self._build_at_component_for_message_id(message_id)
            if at_component is not None:
                return at_component

        target_user_id = (attrs.get("user_id") or attrs.get("target_user_id") or "").strip()
        if target_user_id:
            display_name = body.strip().lstrip("@")
            return AtComponent(
                target_user_id=target_user_id,
                target_user_nickname=display_name or None,
                target_user_cardname=None,
            )

        normalized_body = body.strip()
        if not normalized_body:
            return None

        at_component = self._build_at_component_for_message_id(normalized_body)
        if at_component is not None:
            return at_component
        return self._build_at_component_for_display_name(normalized_body)

    @staticmethod
    async def _build_emoji_component_for_label(label: str) -> Optional[EmojiComponent]:
        """根据情绪、描述或哈希构造表情包组件。"""

        normalized_label = label.strip()
        if not normalized_label:
            return None

        try:
            from src.emoji_system.emoji_manager import emoji_manager

            selected_emoji = emoji_manager.get_emoji_by_hash(normalized_label)
            if selected_emoji is None:
                selected_emoji = await emoji_manager.get_emoji_for_emotion(normalized_label)
        except Exception as exc:
            logger.warning(f"解析格式化回复表情失败: label={normalized_label!r} error={exc}")
            return None

        if selected_emoji is None:
            return None

        emoji_hash = str(getattr(selected_emoji, "file_hash", "") or "").strip()
        if not emoji_hash:
            return None

        emoji_description = str(getattr(selected_emoji, "description", "") or "").strip() or normalized_label
        return EmojiComponent(binary_hash=emoji_hash, content=f"[表情包: {emoji_description}]")

    async def _build_image_component_for_formatted_tag(
        self,
        attrs: Dict[str, str],
        body: str,
    ) -> Optional[ImageComponent]:
        """根据 send_image 的参数语义解析格式化回复图片片段。"""

        target_message_id = (
            attrs.get("media_index")
            or attrs.get("msg_id")
            or attrs.get("message_id")
            or attrs.get("source_id")
            or attrs.get("id")
            or ""
        ).strip()
        body_used_as_target = False
        if not target_message_id:
            target_message_id = body.strip()
            body_used_as_target = bool(target_message_id)
        if not target_message_id:
            return None

        try:
            from .send_image import _collect_message_images, _normalize_image_index

            image_index = _normalize_image_index(attrs)
            images, error = await _collect_message_images(self, target_message_id)
        except Exception as exc:
            logger.warning(f"解析格式化回复图片失败: msg_id={target_message_id!r} error={exc}")
            return None

        if error is not None:
            logger.warning(f"解析格式化回复图片失败: msg_id={target_message_id!r} error={error}")
            return None
        if image_index < 0 or image_index >= len(images):
            logger.warning(
                f"解析格式化回复图片失败: msg_id={target_message_id!r} index={image_index} "
                f"图片数量={len(images)}"
            )
            return None

        image_component = images[image_index].clone()
        description = "" if body_used_as_target else body.strip()
        if description:
            image_component.content = f"[图片: {description}]"
        elif not image_component.content:
            image_component.content = f"[图片: {target_message_id} 的第 {image_index} 张图片]"
        return image_component

    @staticmethod
    def _parse_formatted_reply_attrs(raw_attrs: str) -> Dict[str, str]:
        """解析格式化回复片段中的属性。"""

        attrs: Dict[str, str] = {}
        for match in FORMATTED_REPLY_ATTR_PATTERN.finditer(raw_attrs or ""):
            key = match.group("key").strip().lower().replace("-", "_")
            value = unescape(match.group("value").strip())
            if key:
                attrs[key] = value
        return attrs

    @staticmethod
    def _strip_formatted_reply_code_fence(reply_text: str) -> str:
        """移除模型偶尔包住格式化回复的代码块。"""

        normalized_reply_text = reply_text.strip()
        match = FORMATTED_REPLY_CODE_FENCE_PATTERN.match(normalized_reply_text)
        if match is None:
            return reply_text
        return match.group("body").strip()

    @staticmethod
    def _build_formatted_tag_fallback_text(tag_name: str, body: str) -> str:
        """在片段无法解析成真实组件时，生成可见文本兜底。"""

        normalized_body = body.strip()
        if tag_name == "at":
            if not normalized_body:
                return ""
            return f"@{normalized_body.lstrip('@')}"
        if tag_name == "emoji":
            if not normalized_body:
                return ""
            return f"[表情包: {normalized_body}]"
        if tag_name == "image":
            if not normalized_body:
                return "[图片]"
            return f"[图片: {normalized_body}]"
        if not normalized_body:
            return ""
        return normalized_body

    def _append_processed_text_components(self, components: List[Any], text: str) -> None:
        """将普通文本片段追加为 TextComponent。"""

        normalized_text = unescape(text)
        if not normalized_text.strip():
            return

        for segment in self._post_process_reply_text_chunk(normalized_text):
            normalized_segment = segment.strip()
            if not normalized_segment:
                continue
            if components and isinstance(components[-1], AtComponent):
                normalized_segment = f" {normalized_segment}"
            components.append(TextComponent(normalized_segment))

    async def _post_process_formatted_reply_message_sequences(self, reply_text: str) -> List[MessageSequence]:
        """解析 replyer 的 XML-like 格式化输出。"""

        normalized_reply_text = self._strip_formatted_reply_code_fence(reply_text)
        if not FORMATTED_REPLY_TAG_PATTERN.search(normalized_reply_text):
            return self.post_process_reply_message_sequences(normalized_reply_text)

        components: List[Any] = []
        cursor = 0
        for match in FORMATTED_REPLY_TAG_PATTERN.finditer(normalized_reply_text):
            self._append_processed_text_components(components, normalized_reply_text[cursor : match.start()])

            tag_name = match.group("tag").lower()
            attrs = self._parse_formatted_reply_attrs(match.group("attrs") or "")
            body = unescape(match.group("body") or "")
            if tag_name == "text":
                self._append_processed_text_components(components, body)
            elif tag_name == "at":
                at_component = self._build_at_component_for_formatted_tag(attrs, body)
                if at_component is None:
                    fallback_text = self._build_formatted_tag_fallback_text(tag_name, body)
                    self._append_processed_text_components(components, fallback_text)
                else:
                    components.append(at_component)
            elif tag_name == "emoji":
                emoji_component = await self._build_emoji_component_for_label(body)
                if emoji_component is None:
                    fallback_text = self._build_formatted_tag_fallback_text(tag_name, body)
                    self._append_processed_text_components(components, fallback_text)
                else:
                    components.append(emoji_component)
            elif tag_name == "image":
                image_component = await self._build_image_component_for_formatted_tag(attrs, body)
                if image_component is None:
                    fallback_text = self._build_formatted_tag_fallback_text(tag_name, body)
                    self._append_processed_text_components(components, fallback_text)
                else:
                    components.append(image_component)
            cursor = match.end()

        self._append_processed_text_components(components, normalized_reply_text[cursor:])

        if components:
            return [MessageSequence(components)]
        return self.post_process_reply_message_sequences(normalized_reply_text)

    async def post_process_reply_message_sequences_async(self, reply_text: str) -> List[MessageSequence]:
        """将 replyer 输出处理为可发送组件序列。"""

        if global_config.experimental.enable_replyer_format_output:
            return await self._post_process_formatted_reply_message_sequences(reply_text)
        return self.post_process_reply_message_sequences(reply_text)

    def post_process_reply_message_sequences(self, reply_text: str) -> List[MessageSequence]:
        """将纯文本回复处理为可发送组件序列。"""

        return [MessageSequence([TextComponent(segment)]) for segment in self.post_process_reply_text(reply_text)]

    def get_runtime_manager(self) -> Any:
        """获取插件运行时管理器。"""

        return self.engine._get_runtime_manager()

    def _should_include_planner_chat_id(self) -> bool:
        """当前上下文写入规划器历史时是否需要保留聊天流 ID。"""

        return self.runtime._is_focus_mode_active_for_current_chat()

    def append_guided_reply_to_chat_history(self, reply_text: str) -> None:
        """将引导回复写回 Maisaka 历史。"""

        bot_name = global_config.bot.nickname.strip() or "MaiSaka"
        reply_timestamp = datetime.now()
        include_chat_id = self._should_include_planner_chat_id()
        history_message = build_session_backed_text_message(
            speaker_name=bot_name,
            text=reply_text,
            timestamp=reply_timestamp,
            source_kind="guided_reply",
            chat_id=self.runtime.session_id,
            include_chat_id=include_chat_id,
            is_self_message=global_config.chat.self_message_special_mark,
        )
        self.runtime._chat_history.append(history_message)

    def append_sent_message_to_chat_history(self, message: Any, *, source_kind: str = "guided_reply") -> bool:
        """将已发送消息写回 Maisaka 历史。"""

        runtime_append = getattr(self.runtime, "append_sent_message_to_chat_history", None)
        if callable(runtime_append):
            return bool(runtime_append(message, source_kind=source_kind))

        from src.maisaka.context.messages import SessionBackedMessage
        from src.maisaka.context.history import build_prefixed_message_sequence, build_session_message_visible_text
        user_info = message.message_info.user_info
        speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        include_chat_id = self._should_include_planner_chat_id()
        planner_prefix = build_planner_prefix(
            timestamp=message.timestamp,
            user_name=speaker_name,
            group_card=user_info.user_cardname or "",
            message_id=message.message_id,
            chat_id=message.session_id,
            quote_ids=extract_quote_ids_from_message_sequence(message.raw_message),
            include_message_id=not message.is_notify and bool(message.message_id),
            include_chat_id=include_chat_id,
            is_self_message=source_kind == "guided_reply" and global_config.chat.self_message_special_mark,
        )
        history_message = SessionBackedMessage.from_session_message(
            message,
            raw_message=build_prefixed_message_sequence(message.raw_message, planner_prefix),
            visible_text=build_session_message_visible_text(
                message,
                include_reply_components=source_kind != "guided_reply",
            ),
            source_kind=source_kind,
        )
        self.runtime._chat_history.append(history_message)
        return True

    def append_sent_emoji_to_chat_history(
        self,
        *,
        emoji_base64: str,
        success_message: str,
    ) -> None:
        """将 bot 主动发送的表情包同步到 Maisaka 历史。"""

        bot_name = global_config.bot.nickname.strip() or "MaiSaka"
        reply_timestamp = datetime.now()
        include_chat_id = self._should_include_planner_chat_id()
        planner_prefix = build_planner_prefix(
            timestamp=reply_timestamp,
            user_name=bot_name,
            chat_id=self.runtime.session_id,
            include_chat_id=include_chat_id,
            is_self_message=global_config.chat.self_message_special_mark,
        )
        history_message = SessionBackedMessage(
            raw_message=MessageSequence(
                [
                    TextComponent(planner_prefix),
                    EmojiComponent(
                        binary_hash="",
                        content=success_message,
                        binary_data=b64decode(emoji_base64),
                    ),
                ]
            ),
            visible_text=format_speaker_content(
                bot_name,
                "[表情包]",
                reply_timestamp,
            ),
            timestamp=reply_timestamp,
            source_kind="guided_reply",
        )
        self.runtime._chat_history.append(history_message)
