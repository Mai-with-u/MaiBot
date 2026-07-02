"""Maisaka 内置工具执行上下文。"""

from __future__ import annotations

from base64 import b64decode
from datetime import datetime
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
from src.plugin_runtime.integration import get_plugin_runtime_manager

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

logger = get_logger("maisaka_builtin_context")

RICH_REPLY_TAG_PATTERN = re.compile(
    r"<(?P<tag>send|text|pic|emoji|at|split)\b(?P<body>[^>]*)>",
    re.IGNORECASE,
)


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

    async def post_process_reply_message_sequences_async(self, reply_text: str) -> List[MessageSequence]:
        """将 replyer 输出处理为可发送组件序列。"""

        return self.post_process_reply_message_sequences(reply_text)

    def post_process_reply_message_sequences(self, reply_text: str) -> List[MessageSequence]:
        """将纯文本回复处理为可发送组件序列。"""

        return [MessageSequence([TextComponent(segment)]) for segment in self.post_process_reply_text(reply_text)]

    @staticmethod
    def is_rich_reply_send(reply_text: str) -> bool:
        """判断检查器是否要求原样发送 replyer 输出。"""

        return (reply_text or "").strip().lower() == "<send>"

    @staticmethod
    def _parse_rich_reply_tag_body(raw_body: str) -> tuple[List[str], Dict[str, str]]:
        """解析 `<pic msg_id=... index=0>` 这类标签参数。"""

        body = str(raw_body or "").strip()
        if not body:
            return [], {}

        positional: List[str] = []
        keyword_args: Dict[str, str] = {}
        for token in re.split(r"\s+", body):
            if not token:
                continue
            if "=" not in token:
                positional.append(token)
                continue
            key, value = token.split("=", 1)
            normalized_key = key.strip().lower()
            if normalized_key:
                keyword_args[normalized_key] = value.strip()
        return positional, keyword_args

    def _resolve_at_component(self, raw_body: str) -> AtComponent:
        """把 `<at msg_id>` 解析为对该消息发送者的 at 组件。"""

        positional, keyword_args = self._parse_rich_reply_tag_body(raw_body)
        target_user_id = str(keyword_args.get("user_id") or "").strip()
        target_message_id = str(keyword_args.get("msg_id") or keyword_args.get("message_id") or "").strip()
        if not target_user_id and positional:
            target_message_id = positional[0].strip()

        if target_user_id:
            return AtComponent(target_user_id=target_user_id)

        target_message = self.runtime.find_source_message_by_id(target_message_id)
        if target_message is None:
            raise ValueError(f"无法解析 at 目标消息：msg_id={target_message_id}")

        user_info = target_message.message_info.user_info
        return AtComponent(
            target_user_id=user_info.user_id,
            target_user_nickname=user_info.user_nickname,
            target_user_cardname=user_info.user_cardname,
        )

    async def _resolve_image_component(self, raw_body: str) -> ImageComponent:
        """把 `<pic ...>` 按 send_image 的 msg_id/index 语义解析为图片组件。"""

        from .send_image import _collect_message_images

        positional, keyword_args = self._parse_rich_reply_tag_body(raw_body)
        target_message_id = str(
            keyword_args.get("media_index")
            or keyword_args.get("msg_id")
            or keyword_args.get("message_id")
            or keyword_args.get("source")
            or ""
        ).strip()
        if not target_message_id and positional:
            target_message_id = positional[0].strip()

        raw_index = keyword_args.get("index") or keyword_args.get("image_index") or ""
        if not raw_index and len(positional) >= 2:
            raw_index = positional[1]
        try:
            image_index = int(raw_index or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"图片序号无效：index={raw_index}") from exc

        images, error = await _collect_message_images(self, target_message_id)
        if error is not None:
            raise ValueError(error)
        if image_index < 0 or image_index >= len(images):
            raise ValueError(f"图片序号超出范围：index={image_index}，该消息共有 {len(images)} 张图片。")

        image = images[image_index]
        return ImageComponent(
            binary_hash=image.binary_hash,
            content=image.content,
            binary_data=image.binary_data,
        )

    async def _resolve_emoji_component(self, raw_body: str) -> EmojiComponent:
        """把 `<emoji 情绪>` 解析为表情包组件。"""

        from src.common.utils.image_path import resolve_stored_image_path
        from src.emoji_system.emoji_manager import emoji_manager

        requested_emotion = str(raw_body or "").strip()
        selected_emoji = await emoji_manager.get_emoji_for_emotion(requested_emotion)
        if selected_emoji is None:
            available_emojis = list(emoji_manager.emojis)
            if not available_emojis:
                raise ValueError("当前表情包库中没有可用表情。")
            selected_emoji = min(available_emojis, key=lambda item: int(getattr(item, "query_count", 0) or 0))

        emoji_path = resolve_stored_image_path(selected_emoji.full_path)
        emoji_bytes = emoji_path.read_bytes()
        emoji_manager.update_emoji_usage(selected_emoji)
        return EmojiComponent(
            binary_hash=selected_emoji.file_hash,
            content=selected_emoji.description.strip() or "[表情包]",
            binary_data=emoji_bytes,
        )

    async def post_process_rich_reply_message_sequences_async(
        self,
        checker_output: str,
        original_reply_text: str,
    ) -> List[MessageSequence]:
        """将检查器格式化输出处理为可发送组件序列。"""

        normalized_output = (checker_output or "").strip()
        if self.is_rich_reply_send(normalized_output):
            return self.post_process_reply_message_sequences(original_reply_text)
        if not RICH_REPLY_TAG_PATTERN.search(normalized_output):
            return self.post_process_reply_message_sequences(normalized_output)

        sequences: List[MessageSequence] = []
        components: List[Any] = []

        def flush_components() -> None:
            nonlocal components
            if not components:
                return
            sequences.append(MessageSequence(components))
            components = []

        cursor = 0
        for match in RICH_REPLY_TAG_PATTERN.finditer(normalized_output):
            prefix_text = normalized_output[cursor : match.start()]
            if prefix_text:
                components.append(TextComponent(prefix_text))

            tag = match.group("tag").lower()
            body = match.group("body") or ""
            if tag == "split":
                flush_components()
            elif tag == "send":
                components.append(TextComponent(original_reply_text))
            elif tag == "text":
                replacement_text = body.strip() or original_reply_text
                if replacement_text:
                    components.append(TextComponent(replacement_text))
            elif tag == "pic":
                components.append(await self._resolve_image_component(body))
            elif tag == "emoji":
                components.append(await self._resolve_emoji_component(body))
            elif tag == "at":
                components.append(self._resolve_at_component(body))
            cursor = match.end()

        suffix_text = normalized_output[cursor:]
        if suffix_text:
            components.append(TextComponent(suffix_text))
        flush_components()

        if not sequences:
            return self.post_process_reply_message_sequences(original_reply_text)
        return sequences

    def get_runtime_manager(self) -> Any:
        """获取插件运行时管理器。"""

        return get_plugin_runtime_manager()

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
