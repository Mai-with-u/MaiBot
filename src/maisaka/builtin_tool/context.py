"""Maisaka 内置工具执行上下文。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from functools import partial
from typing import Any, Awaitable, Dict, List, Optional, Sequence, TYPE_CHECKING
from uuid import uuid4

import asyncio

from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import (
    ProcessedResponseSegment,
    process_llm_response,
    process_llm_response_segments,
    process_llm_response_segments_async,
)
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
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
from src.core.local_operator import MAISAKA_CLI_BOT_ID
from src.maisaka.context.image_attachment import resolve_context_image
from src.maisaka.context.message_id_alias import build_alias_map, to_display_message_id
from src.maisaka.context.messages import ContentContextMessage
from src.plugin_runtime.integration import get_plugin_runtime_manager

if TYPE_CHECKING:
    from src.maisaka.reasoning_engine import MaisakaReasoningEngine
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_builtin_context")


@dataclass
class PostProcessedReplyMessage:
    """一条待发送的后处理回复及其引用提示。"""

    sequence: MessageSequence
    quote_previous: bool = False


@dataclass
class ResolvedReplyAttachments:
    mentions: List[AtComponent]
    images: List[ImageComponent]
    emoji: Optional[EmojiComponent]


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
    def post_process_reply_text(
        reply_text: str,
        *,
        skip_post_process: bool = False,
        enable_splitter: bool = True,
    ) -> List[str]:
        """沿用旧回复链的文本后处理，执行分段。"""

        if skip_post_process:
            return [reply_text]

        processed_segments: List[str] = []
        for segment in process_llm_response(
            reply_text,
            enable_splitter=enable_splitter,
        ):
            normalized_segment = segment.strip()
            if normalized_segment:
                processed_segments.append(normalized_segment)

        if processed_segments:
            return processed_segments
        return [reply_text.strip()]

    @staticmethod
    def post_process_reply_segments(
        reply_text: str,
        *,
        skip_post_process: bool = False,
        enable_splitter: bool = True,
    ) -> List[ProcessedResponseSegment]:
        """执行文本后处理，并保留错别字纠正段的引用提示。"""

        if skip_post_process:
            return [ProcessedResponseSegment(reply_text.strip())]

        processed_segments: List[ProcessedResponseSegment] = []
        for segment in process_llm_response_segments(
            reply_text,
            enable_splitter=enable_splitter,
        ):
            normalized_text = segment.text.strip()
            if normalized_text:
                processed_segments.append(
                    ProcessedResponseSegment(
                        normalized_text,
                        quote_previous=segment.quote_previous,
                        is_correction=segment.is_correction,
                    )
                )

        if processed_segments:
            return processed_segments
        return [ProcessedResponseSegment(reply_text.strip())]

    def post_process_reply_message_items(
        self,
        reply_text: str,
        *,
        skip_post_process: bool = False,
        enable_splitter: bool = True,
    ) -> List[PostProcessedReplyMessage]:
        """将纯文本回复处理为带发送提示的组件序列。"""

        return [
            PostProcessedReplyMessage(
                sequence=MessageSequence([TextComponent(segment.text)]),
                quote_previous=segment.quote_previous,
            )
            for segment in self.post_process_reply_segments(
                reply_text,
                skip_post_process=skip_post_process,
                enable_splitter=enable_splitter,
            )
        ]

    async def post_process_reply_message_sequences_async(
        self,
        reply_text: str,
        attachments: Optional[Dict[str, Any]] = None,
        *,
        skip_post_process: bool = False,
        enable_splitter: bool = True,
        enable_chinese_typo: bool = True,
    ) -> List[MessageSequence]:
        """将 replyer 输出处理为可发送组件序列。"""

        items = await self.post_process_reply_message_items_async(
            reply_text,
            attachments,
            skip_post_process=skip_post_process,
            enable_splitter=enable_splitter,
            enable_chinese_typo=enable_chinese_typo,
        )
        return [item.sequence for item in items]

    def post_process_reply_message_sequences(
        self,
        reply_text: str,
        *,
        skip_post_process: bool = False,
        enable_splitter: bool = True,
    ) -> List[MessageSequence]:
        """将纯文本回复处理为可发送组件序列。"""

        return [
            item.sequence
            for item in self.post_process_reply_message_items(
                reply_text,
                skip_post_process=skip_post_process,
                enable_splitter=enable_splitter,
            )
        ]

    def _resolve_at_attachment(self, raw_target: Any) -> AtComponent:
        """把 attach_at 参数解析为对目标消息发送者的 at 组件。"""

        if isinstance(raw_target, dict):
            target_user_id = str(raw_target.get("user_id") or "").strip()
            target_message_id = str(raw_target.get("msg_id") or raw_target.get("message_id") or "").strip()
        else:
            target_user_id = ""
            target_message_id = str(raw_target or "").strip()

        if target_user_id:
            return AtComponent(target_user_id=target_user_id)

        target_message = self.runtime.find_source_message_by_id(target_message_id)
        if target_message is None:
            raise ValueError(f"无法解析 at 目标消息：msg_id={to_display_message_id(target_message_id)}")

        user_info = target_message.message_info.user_info
        return AtComponent(
            target_user_id=user_info.user_id,
            target_user_nickname=user_info.user_nickname,
            target_user_cardname=user_info.user_cardname,
        )

    async def _resolve_image_attachment(self, raw_attachment: Any) -> ImageComponent:
        """把 attach_pic 参数按消息或工具媒体索引解析为图片组件。"""

        if isinstance(raw_attachment, dict):
            target_message_id = str(
                raw_attachment.get("media_index")
                or raw_attachment.get("msg_id")
                or raw_attachment.get("message_id")
                or raw_attachment.get("source")
                or ""
            ).strip()
            raw_index = raw_attachment.get("index", raw_attachment.get("image_index", 0))
        else:
            target_message_id = str(raw_attachment or "").strip()
            raw_index = 0

        try:
            image_index = int(raw_index or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"图片序号无效：index={raw_index}") from exc

        return await resolve_context_image(self.runtime, target_message_id, image_index)

    async def _resolve_emoji_attachment(self, reference: Any) -> EmojiComponent:
        """把 attach_emoji 参数解析为表情包组件。"""

        if isinstance(reference, str):
            return await self.load_emoji_by_description(reference, self.runtime.session_id)
        if isinstance(reference, dict):
            if reference.keys() & {"emotion", "content"}:
                if reference.keys() - {"emotion", "content"} or any(
                    not isinstance(value, str) or not value.strip() for value in reference.values()
                ):
                    raise ValueError("语义表情引用只能填写非空的 emotion 和 content 文本。")
                return await self.load_emoji_by_description(
                    reference.get("emotion", ""), self.runtime.session_id, content=reference.get("content", ""),
                )
            source_id = reference.get("msg_id")
            index = reference.get("index", 0)
            if reference.keys() - {"msg_id", "index"} or not isinstance(source_id, str) or not source_id.strip():
                raise ValueError("上下文表情引用需要提供 msg_id，可选 index。")
            history = [
                message for message in reversed(self.runtime._chat_history)
                if isinstance(message, ContentContextMessage)
            ]
            aliases = build_alias_map(message.message_id for message in history)
            source_id = source_id.strip()
            source_id = aliases.get(source_id, source_id)
            for message in history:
                if message.message_id == source_id:
                    emojis = [item for item in message.raw_message.components if isinstance(item, EmojiComponent)]
                    return await self.load_context_emoji(emojis, index)
            raise ValueError("上下文中找不到所引用的表情包消息。")
        raise ValueError("attach_emoji 需要上下文表情引用、情绪或画面描述。")

    @staticmethod
    async def load_context_emoji(emojis: List[EmojiComponent], index: int) -> EmojiComponent:
        """读取上下文中的表情组件，包括尚未注册到可发送表情库的表情。"""
        if type(index) is not int or not 0 <= index < len(emojis):
            raise ValueError(f"上下文表情包序号无效，该消息共有 {len(emojis)} 张表情包。")
        emoji = emojis[index]
        await emoji.load_emoji_binary()
        if not emoji.binary_data:
            raise ValueError("上下文表情包文件为空。")
        return EmojiComponent(binary_hash=emoji.binary_hash, content=emoji.content, binary_data=emoji.binary_data)

    @staticmethod
    async def load_emoji_by_description(emotion: str, session_id: str, *, content: str = "") -> EmojiComponent:
        from src.emoji_system.semantic_match import match_emoji_description

        emoji_hash = await match_emoji_description(emotion, session_id, content=content)
        return await BuiltinToolRuntimeContext.load_emoji_attachment(emoji_hash)

    @staticmethod
    async def load_emoji_attachment(emoji_hash: str) -> EmojiComponent:
        """按已确定的哈希加载表情，补充器可复用请求快照而不重查活动历史。"""
        from src.common.utils.image_path import resolve_stored_image_path
        from src.emoji_system.emoji_manager import emoji_manager

        selected_emoji = emoji_manager.get_emoji_by_hash(emoji_hash)
        if selected_emoji is None:
            raise ValueError(f"表情包 {emoji_hash} 已不可用。")

        emoji_path = resolve_stored_image_path(selected_emoji.full_path)
        emoji_bytes = await asyncio.to_thread(emoji_path.read_bytes)
        # content 是表情的文本表示，会写入已发送消息的纯文本、数据库和 Maisaka 历史；
        # 必须与入站表情一致渲染为「[表情包: 描述]」，裸描述会被模型当作自身正文模仿。
        emoji_description = selected_emoji.description.strip()
        return EmojiComponent(
            binary_hash=selected_emoji.file_hash,
            content=f"[表情包: {emoji_description}]" if emoji_description else "[表情包]",
            binary_data=emoji_bytes,
        )

    @staticmethod
    def _normalize_attachment_list(raw_value: Any, argument_name: str) -> List[Any]:
        """将可重复附件参数规范化为列表。"""

        if raw_value is None or raw_value == "":
            return []
        if isinstance(raw_value, list):
            return raw_value
        if isinstance(raw_value, (str, dict)):
            return [raw_value]
        raise ValueError(f"{argument_name} 参数类型无效，应为字符串、对象或列表。")

    async def _transform_reply_segments(
        self, segments: List[ProcessedResponseSegment], *, enable_chinese_typo: bool
    ) -> List[ProcessedResponseSegment]:
        result = await self.get_runtime_manager().invoke_hook(
            "maisaka.reply.after_split",
            segments=[asdict(segment) for segment in segments],
            session_id=self.runtime.session_id,
            enable_chinese_typo=enable_chinese_typo,
        )
        raw_segments = result.kwargs["segments"]
        if not isinstance(raw_segments, list) or not raw_segments:
            raise ValueError("回复分段 Hook 必须返回非空 segments 列表")
        validated: List[ProcessedResponseSegment] = []
        for raw in raw_segments:
            if (
                not isinstance(raw, dict)
                or set(raw) != {"text", "separator", "quote_previous", "is_correction"}
                or not isinstance(raw["text"], str)
                or not raw["text"].strip()
                or not isinstance(raw["separator"], str)
                or not isinstance(raw["quote_previous"], bool)
                or not isinstance(raw["is_correction"], bool)
            ):
                raise ValueError("回复分段 Hook 返回了无效的消息段")
            validated.append(ProcessedResponseSegment(**raw))
        return validated

    async def post_process_reply_message_items_async(
        self,
        reply_text: str,
        attachments: Optional[Dict[str, Any]] = None,
        *,
        resolved_attachments: Optional[Awaitable[ResolvedReplyAttachments]] = None,
        skip_post_process: bool = False,
        enable_splitter: bool = True,
        enable_chinese_typo: bool = True,
    ) -> List[PostProcessedReplyMessage]:
        """异步处理 replyer 正文和附件，并保留每条消息的发送提示。"""

        if skip_post_process:
            segments = [ProcessedResponseSegment(reply_text.strip())]
        else:
            segments = await process_llm_response_segments_async(
                reply_text,
                enable_splitter=enable_splitter,
                transform_segments=partial(
                    self._transform_reply_segments, enable_chinese_typo=enable_chinese_typo
                ),
            )
        items = [
            PostProcessedReplyMessage(
                sequence=MessageSequence([TextComponent(segment.text.strip())]),
                quote_previous=segment.quote_previous,
            )
            for segment in segments
            if segment.text.strip()
        ]
        resolved = (
            await resolved_attachments if resolved_attachments is not None
            else await self.resolve_reply_attachments(attachments)
        )
        if not resolved.mentions and not resolved.images and resolved.emoji is None:
            return items
        if not items:
            raise ValueError("回复附件需要搭配非空的文字回复。")

        at_prefix_components = []
        for at_component in resolved.mentions:
            at_prefix_components.extend([at_component, TextComponent(" ")])

        items[0].sequence.components = at_prefix_components + items[0].sequence.components
        for image_component in resolved.images:
            items.append(PostProcessedReplyMessage(sequence=MessageSequence([image_component])))
        if resolved.emoji is not None:
            items.append(PostProcessedReplyMessage(sequence=MessageSequence([resolved.emoji])))
        return items

    async def resolve_reply_attachments(
        self, attachments: Optional[Dict[str, Any]],
    ) -> ResolvedReplyAttachments:
        """在生成正文的同时解析已经确定的附件参数。"""
        attachment_args = dict(attachments or {})
        raw_emoji = attachment_args.get("attach_emoji")
        # 0 表示不附加表情包，与省略参数一致；其他无效类型仍交给严格校验。
        if type(raw_emoji) is int and raw_emoji == 0:
            raw_emoji = None

        if attachment_args.get("attach_at") and not global_config.chat.enable_reply_at:
            raise ValueError("回复时 @ 用户已关闭，不能使用 attach_at。")
        at_components = [
            self._resolve_at_attachment(raw_target)
            for raw_target in self._normalize_attachment_list(attachment_args.get("attach_at"), "attach_at")
        ]
        image_components = [
            await self._resolve_image_attachment(raw_attachment)
            for raw_attachment in self._normalize_attachment_list(attachment_args.get("attach_pic"), "attach_pic")
        ]
        emoji = None
        if raw_emoji is not None:
            source = {dict: "context", str: "semantic"}.get(type(raw_emoji), "invalid")
            if isinstance(raw_emoji, dict) and raw_emoji.keys() & {"emotion", "content"}:
                source = "semantic"
            try:
                emoji = await self._resolve_emoji_attachment(raw_emoji)
            except Exception as exc:
                logger.warning(
                    f"attach_emoji resolution=failed source={source} error={type(exc).__name__}", exc_info=True
                )
            else:
                logger.info(f"attach_emoji resolution=resolved source={source}")

        return ResolvedReplyAttachments(at_components, image_components, emoji)

    def get_runtime_manager(self) -> Any:
        """获取插件运行时管理器。"""

        return get_plugin_runtime_manager()

    def _append_cli_message(self, sequence: MessageSequence, visible_text: str) -> None:
        """记录已经在 CLI 展示的真实发言，与平台发送复用同一历史写入链路。"""
        message = SessionMessage(
            message_id=f"cli_sent_{uuid4().hex}", timestamp=datetime.now(),
            platform=self.runtime.chat_stream.platform,
        )
        message.session_id = self.runtime.session_id
        message.account_id = self.runtime.chat_stream.account_id
        message.scope = self.runtime.chat_stream.scope
        message.message_info = MessageInfo(
            user_info=UserInfo(user_id=MAISAKA_CLI_BOT_ID, user_nickname=global_config.bot.nickname),
            additional_config={},
        )
        message.raw_message = sequence
        message.processed_plain_text = visible_text
        self.runtime.append_sent_message_to_chat_history(message, source_kind="guided_reply")

    def append_guided_reply_to_chat_history(self, reply_text: str) -> None:
        """将 CLI 已展示的文字回复写回 Maisaka 历史。"""
        self._append_cli_message(MessageSequence([TextComponent(reply_text)]), reply_text)
