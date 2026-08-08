from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

import asyncio
import re

from src.chat.utils.utils import is_bot_self
from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.common.data_models.message_component_data_model import EmojiComponent, ReplyComponent
from src.common.logger import get_logger
from src.config.config import global_config
from src.llm_models.payload_content.context_item import AssistantMessageItem, ContextItem, ContextItemBuilder, RoleType
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.jargon_context_matcher import is_jargon_reference_text
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

from .expression_utils import parse_jargon_response
from .jargon_miner import JargonEntry, JargonEvidenceMessageGroup, JargonMiner

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
    from src.maisaka.context.messages import LLMContextMessage


logger = get_logger("jargon_learner")

jargon_learn_model = LLMServiceClient(task_name="learner", request_type="jargon.learner")
ALLOWED_LEARNING_SOURCE_KINDS = {
    "assistant",
    "guided_reply",
    "optimized_tool_history",
    "outbound_send",
    "planner_assistant",
    "planner_assistant_visible",
    "planner_tool_result",
    "planner_user",
    "session",
    "tool",
    "tool_result_media",
    "user",
}
FILTERED_TOOL_NAMES = {"query_person_profile", "wait"}
MESSAGE_OPEN_TAG_PATTERN = re.compile(r"^<message\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
MESSAGE_ID_ATTR_PATTERN = re.compile(r'\s+msg_id\s*=\s*"[^"]*"', re.IGNORECASE)


def _is_filtered_tool_name(tool_name: Optional[str]) -> bool:
    return (tool_name or "").strip() in FILTERED_TOOL_NAMES


def _is_standalone_emoji_message(message: "SessionMessage") -> bool:
    if message.is_emoji:
        return True

    components = list(message.raw_message.components)
    content_components = [component for component in components if not isinstance(component, ReplyComponent)]
    return bool(content_components) and all(isinstance(component, EmojiComponent) for component in content_components)


def _is_learnable_session_message(message: "SessionMessage") -> bool:
    return not message.is_notify and not _is_standalone_emoji_message(message)


@dataclass(frozen=True)
class JargonLearningAcquireResult:
    """黑话学习批次并发闸门的申请结果。"""

    acquired: bool
    reason: str = ""
    active_count: int = 0
    max_count: int = 0


@dataclass(frozen=True)
class JargonLearningSourceItem:
    """一次黑话学习中可引用的上下文素材。"""

    source_kind: str
    speaker_kind: str
    speaker_name: str
    content: str
    timestamp: datetime
    original_message: Optional["SessionMessage"] = None


class JargonLearningBatchGate:
    """控制黑话学习批次的聊天流互斥与全局并发上限。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_session_ids: set[str] = set()

    async def acquire(self, session_id: str) -> JargonLearningAcquireResult:
        max_count = int(global_config.expression.max_expression_learner)
        if max_count <= 0:
            return JargonLearningAcquireResult(False, "max_expression_learner <= 0", 0, max_count)

        async with self._lock:
            active_count = len(self._active_session_ids)
            if session_id in self._active_session_ids:
                return JargonLearningAcquireResult(False, "session_busy", active_count, max_count)
            if active_count >= max_count:
                return JargonLearningAcquireResult(False, "global_limit", active_count, max_count)

            self._active_session_ids.add(session_id)
            return JargonLearningAcquireResult(True, active_count=active_count + 1, max_count=max_count)

    async def release(self, session_id: str) -> None:
        async with self._lock:
            self._active_session_ids.discard(session_id)


jargon_learning_batch_gate = JargonLearningBatchGate()


class JargonLearner:
    def __init__(self, session_id: str) -> None:
        """初始化黑话学习器。

        Args:
            session_id: 当前会话 ID。
        """

        self.session_id = session_id
        self.min_messages_for_extraction = 10

    @staticmethod
    def _get_session_display_name(session_id: str) -> str:
        """获取聊天流展示名称，无法解析时回退到 session_id。"""

        from src.chat.message_receive.chat_manager import chat_manager

        session_name = chat_manager.get_session_name(session_id)
        if session_name:
            return session_name

        chat_manager.get_existing_session_by_session_id(session_id)
        return chat_manager.get_session_name(session_id) or session_id

    async def learn_from_context_messages(
        self,
        context_messages: Sequence["LLMContextMessage"],
        jargon_miner: JargonMiner,
    ) -> bool:
        """从 Maisaka 被裁切的上下文消息中学习黑话候选。"""

        source_items = self._extract_learning_sources_from_context(context_messages)
        if not source_items:
            logger.debug("裁切历史中没有可用于黑话学习的上下文消息")
            return False
        if len(source_items) < self.min_messages_for_extraction:
            logger.debug(f"裁切历史可学习消息不足: 可学习={len(source_items)} 阈值={self.min_messages_for_extraction}")
            return False

        return await self._learn_from_sources(source_items, jargon_miner=jargon_miner)

    @staticmethod
    def _extract_learning_sources_from_context(
        context_messages: Sequence["LLMContextMessage"],
    ) -> List[JargonLearningSourceItem]:
        """从上下文消息中提取可学习素材，过滤表情包消息。"""

        from src.maisaka.context.messages import (
            ModelOutputContextMessage,
            ReferenceMessage,
            ReferenceMessageType,
            SessionBackedMessage,
            ToolResultMessage,
        )

        allowed_session_source_kinds = {
            "user",
            "guided_reply",
            "outbound_send",
            "optimized_tool_history",
            "tool_result_media",
        }
        source_items: List[JargonLearningSourceItem] = []
        seen_message_ids: set[str] = set()
        seen_object_ids: set[int] = set()
        seen_context_keys: set[tuple[str, str, str]] = set()

        for context_message in context_messages:
            if (
                isinstance(context_message, ReferenceMessage)
                and context_message.reference_type == ReferenceMessageType.JARGON
            ):
                continue

            if isinstance(context_message, ModelOutputContextMessage):
                assistant_content = JargonLearner._render_assistant_context_text(context_message)
                if assistant_content:
                    source_items.append(
                        JargonLearningSourceItem(
                            source_kind=context_message.source_kind,
                            speaker_kind="ASSISTANT",
                            speaker_name=global_config.bot.nickname or "assistant",
                            content=assistant_content,
                            timestamp=context_message.timestamp,
                        )
                    )
                continue

            if isinstance(context_message, ToolResultMessage):
                if _is_filtered_tool_name(context_message.tool_name):
                    continue
                tool_result_content = JargonLearner._render_tool_result_context_text(context_message)
                if tool_result_content:
                    source_items.append(
                        JargonLearningSourceItem(
                            source_kind=context_message.source,
                            speaker_kind="TOOL_RESULT",
                            speaker_name=context_message.tool_name or "tool_result",
                            content=tool_result_content,
                            timestamp=context_message.timestamp,
                        )
                    )
                continue

            if isinstance(context_message, SessionBackedMessage):
                if context_message.source_kind not in allowed_session_source_kinds:
                    continue

                original_message = context_message.original_message
                if original_message is not None:
                    if not _is_learnable_session_message(original_message):
                        continue

                    message_id = str(original_message.message_id or "").strip()
                    if message_id:
                        if message_id in seen_message_ids:
                            continue
                        seen_message_ids.add(message_id)
                    else:
                        object_id = id(original_message)
                        if object_id in seen_object_ids:
                            continue
                        seen_object_ids.add(object_id)
                else:
                    context_key = (
                        context_message.source_kind,
                        str(context_message.message_id or ""),
                        context_message.processed_plain_text,
                    )
                    if context_key in seen_context_keys:
                        continue
                    seen_context_keys.add(context_key)

                content = (context_message.processed_plain_text or "").strip()
                if not content:
                    continue
                if is_jargon_reference_text(content):
                    continue

                source_items.append(JargonLearner._build_source_item_from_context_message(context_message))

        return source_items

    @staticmethod
    def _render_assistant_context_text(message: "LLMContextMessage") -> str:
        """渲染 assistant 正文，供黑话学习引用。"""

        from src.maisaka.context.messages import ModelOutputContextMessage

        if not isinstance(message, ModelOutputContextMessage) or not isinstance(
            message.output_item,
            AssistantMessageItem,
        ):
            return ""

        content = message.content.strip()
        if not content:
            return ""

        return content

    @staticmethod
    def _render_tool_result_context_text(message: "LLMContextMessage") -> str:
        """渲染工具结果，供黑话学习引用。"""

        from src.maisaka.context.messages import ToolResultMessage

        if not isinstance(message, ToolResultMessage):
            return ""

        status = "success" if message.success else "failed"
        return "\n".join(
            [
                f"[tool_result:{status}]",
                f"tool_call_id: {message.tool_call_id}",
                f"tool_name: {message.tool_name or 'tool'}",
                "[content]",
                message.content.strip() or "[空工具结果]",
            ]
        ).strip()

    @staticmethod
    def _build_session_message_learning_content(
        message: "SessionMessage",
        *,
        is_self_message: bool,
    ) -> str:
        """复用规划器消息格式构造黑话学习素材正文。"""

        from src.maisaka.context.history import build_prefixed_message_sequence
        from src.maisaka.context.message_adapter import build_visible_text_from_sequence
        from src.maisaka.context.planner_messages import build_planner_user_prefix_from_session_message

        planner_prefix = build_planner_user_prefix_from_session_message(
            message,
            include_message_id=False,
            is_self_message=is_self_message,
        )
        visible_sequence = build_prefixed_message_sequence(message.raw_message, planner_prefix)
        return build_visible_text_from_sequence(visible_sequence).strip()

    @staticmethod
    def _build_source_item_from_context_message(context_message: "LLMContextMessage") -> JargonLearningSourceItem:
        """把上下文消息转换为黑话学习素材。"""

        from src.maisaka.context.messages import SessionBackedMessage

        if not isinstance(context_message, SessionBackedMessage):
            raise TypeError(f"不支持的上下文消息类型: {type(context_message)}")

        original_message = context_message.original_message
        source_kind = context_message.source_kind
        if source_kind == "optimized_tool_history":
            speaker_kind = "TOOL_CALL"
            speaker_name = "tool_call"
        elif source_kind == "tool_result_media":
            speaker_kind = "TOOL_RESULT"
            speaker_name = "tool_result_media"
        elif original_message is not None and is_bot_self(
            original_message.platform,
            original_message.message_info.user_info.user_id,
        ):
            speaker_kind = "ASSISTANT"
            speaker_name = global_config.bot.nickname or "assistant"
        elif source_kind in {"guided_reply", "outbound_send"}:
            speaker_kind = "ASSISTANT"
            speaker_name = global_config.bot.nickname or "assistant"
        else:
            speaker_kind = "USER"
            if original_message is None:
                speaker_name = "未知用户"
            else:
                user_info = original_message.message_info.user_info
                speaker_name = user_info.user_cardname or user_info.user_nickname or "未知用户"

        if original_message is not None:
            content = JargonLearner._build_session_message_learning_content(
                original_message,
                is_self_message=speaker_kind == "ASSISTANT",
            )
        else:
            content = (context_message.processed_plain_text or "").strip()

        return JargonLearningSourceItem(
            source_kind=source_kind,
            speaker_kind=speaker_kind,
            speaker_name=speaker_name,
            content=content,
            timestamp=context_message.timestamp,
            original_message=original_message,
        )

    async def _learn_from_session_messages(
        self,
        pending_messages: List["SessionMessage"],
        *,
        jargon_miner: JargonMiner,
    ) -> bool:
        """对一批真实会话消息执行黑话学习。"""

        return await self._learn_from_sources(pending_messages, jargon_miner=jargon_miner)

    async def _learn_from_sources(
        self,
        pending_messages: Sequence["SessionMessage"] | Sequence[JargonLearningSourceItem],
        *,
        jargon_miner: JargonMiner,
    ) -> bool:
        """对一批上下文素材执行黑话学习。"""

        learning_session_id = self._resolve_learning_session_id(pending_messages)
        if learning_session_id is None:
            logger.warning(f"黑话学习已跳过：无法解析到有效聊天流，learner_session_id={self.session_id}")
            return False
        if learning_session_id != self.session_id:
            logger.info(
                f"黑话学习会话 ID 已按真实消息修正: learner_session_id={self.session_id} "
                f"learning_session_id={learning_session_id}"
            )

        acquire_result = await jargon_learning_batch_gate.acquire(learning_session_id)
        if not acquire_result.acquired:
            if acquire_result.reason == "session_busy":
                logger.info(f"{learning_session_id} 已有黑话学习批次正在运行，放弃新的批次")
            elif acquire_result.reason == "global_limit":
                logger.info(
                    f"黑话学习全局并发已满，放弃新的批次: "
                    f"active={acquire_result.active_count}, max={acquire_result.max_count}, "
                    f"session_id={learning_session_id}"
                )
            else:
                logger.warning(
                    f"黑话学习并发配置无效，放弃新的批次: "
                    f"max_expression_learner={acquire_result.max_count}, session_id={learning_session_id}"
                )
            return False

        try:
            return await self._run_learning_batch(
                pending_messages,
                learning_session_id=learning_session_id,
                jargon_miner=jargon_miner,
            )
        finally:
            await jargon_learning_batch_gate.release(learning_session_id)

    async def _run_learning_batch(
        self,
        pending_messages: Sequence["SessionMessage"] | Sequence[JargonLearningSourceItem],
        *,
        learning_session_id: str,
        jargon_miner: JargonMiner,
    ) -> bool:
        """执行已经获得并发闸门的黑话学习批次。"""

        source_items = await self._prepare_learning_source_items(pending_messages)
        if not source_items:
            logger.debug("没有可用于黑话学习的消息素材")
            return False

        readable_message = "聊天上下文将在后续多条 user message 中给出；请以每条消息中的 source_id 作为来源行编号。"
        prompt_template = prompt_manager.get_prompt("learn_jargon")
        prompt_template.add_context("bot_name", global_config.bot.nickname)
        prompt_template.add_context("chat_str", readable_message)
        prompt = await prompt_manager.render_prompt(prompt_template)

        try:
            learning_messages = await self._build_multi_learning_messages(pending_messages, prompt)
            generation_result = await jargon_learn_model.generate_response_with_context(
                lambda _client: learning_messages,
                options=LLMGenerationOptions(temperature=0.3),
                session_id=learning_session_id,
            )
            self._log_learning_context_preview(
                learning_messages,
                session_id=learning_session_id,
                source_message_count=len(source_items),
                source_type="trimmed_history",
                generation_result=generation_result,
            )
            response = generation_result.response
        except Exception as e:
            logger.error(f"学习黑话失败: {e}")
            return False

        jargon_entries = parse_jargon_response(response)
        cached_jargon_entries = self._check_cached_jargons_in_messages(source_items, jargon_miner)
        if cached_jargon_entries:
            existing_contents = {content for content, _ in jargon_entries}
            for content, source_id in cached_jargon_entries:
                if content in existing_contents:
                    continue
                jargon_entries.append((content, source_id))
                existing_contents.add(content)
                logger.info(f"从缓存中找到黑话: {content}")

        if not jargon_entries:
            logger.info("没有可学习的黑话")
            self._log_jargon_update_process(
                status="no_extracted_entries",
                jargon_miner=jargon_miner,
                source_items=source_items,
                parsed_entries=[],
                accepted_entries=[],
                skipped_entries=[],
                saved=0,
                updated=0,
            )
            return False

        original_jargon_session_id = jargon_miner.session_id
        original_jargon_session_name = jargon_miner.session_name
        if learning_session_id != original_jargon_session_id:
            jargon_miner.session_id = learning_session_id
            jargon_miner.session_name = self._get_session_display_name(learning_session_id)
        try:
            return await self._process_jargon_entries(jargon_entries, source_items, jargon_miner)
        finally:
            jargon_miner.session_id = original_jargon_session_id
            jargon_miner.session_name = original_jargon_session_name

    def _resolve_learning_session_id(
        self,
        messages: Sequence["SessionMessage"] | Sequence[JargonLearningSourceItem],
    ) -> Optional[str]:
        """根据真实消息解析本轮黑话学习应该归属的会话 ID。"""

        from src.chat.message_receive.chat_manager import chat_manager

        candidates: list[str] = []
        for message in messages:
            original_message = message.original_message if isinstance(message, JargonLearningSourceItem) else message
            if original_message is None:
                continue
            session_id = str(original_message.session_id or "").strip()
            if session_id:
                candidates.append(session_id)

        def session_exists(session_id: str) -> bool:
            if not session_id:
                return False
            return chat_manager.get_existing_session_by_session_id(session_id) is not None

        for session_id, _ in Counter(candidates).most_common():
            if session_exists(session_id):
                return session_id

        if session_exists(self.session_id):
            return self.session_id

        logger.warning(
            f"黑话学习无法从真实消息中找到已注册聊天流，也无法确认 learner_session_id; "
            f"learner_session_id={self.session_id} "
            f"候选 session_id={dict(Counter(candidates))}"
        )
        return None

    async def _prepare_learning_source_items(
        self,
        messages: Sequence["SessionMessage"] | Sequence[JargonLearningSourceItem],
    ) -> List[JargonLearningSourceItem]:
        """统一准备黑话学习素材，并过滤表情包消息。"""

        source_items: List[JargonLearningSourceItem] = []
        for message in messages:
            if isinstance(message, JargonLearningSourceItem):
                if message.source_kind not in ALLOWED_LEARNING_SOURCE_KINDS:
                    continue
                if is_jargon_reference_text(message.content):
                    continue
                source_items.append(message)
                continue

            if not _is_learnable_session_message(message):
                continue

            await message.process()
            source_items.append(self._build_source_item_from_session_message(message))

        return source_items

    @staticmethod
    def _xml_attr(value: str) -> str:
        return escape(value, quote=True)

    @staticmethod
    def _build_message_open_tag_for_learning(source_id: int, attrs: str) -> str:
        learning_attrs = MESSAGE_ID_ATTR_PATTERN.sub("", attrs or "")
        if re.search(r"\bsource_id\s*=", learning_attrs, re.IGNORECASE):
            return f"<message{learning_attrs}>"
        return f'<message source_id="{source_id}"{learning_attrs}>'

    @classmethod
    def _build_learning_source_content(cls, source_id: int, source_item: JargonLearningSourceItem) -> str:
        """构造单条黑话学习素材，真实消息直接复用 `<message ...>` 元信息。"""

        content = source_item.content.strip() or "[空消息]"
        if content.startswith("<message"):
            return MESSAGE_OPEN_TAG_PATTERN.sub(
                lambda match: cls._build_message_open_tag_for_learning(source_id, match.group("attrs") or ""),
                content,
                count=1,
            )

        attrs = [
            f'source_id="{source_id}"',
            f'speaker="{cls._xml_attr(source_item.speaker_kind)}"',
            f'source_kind="{cls._xml_attr(source_item.source_kind)}"',
            f'name="{cls._xml_attr(source_item.speaker_name)}"',
        ]
        return f"<learning-source {' '.join(attrs)}>\n{content}"

    @staticmethod
    def _build_source_item_from_session_message(message: "SessionMessage") -> JargonLearningSourceItem:
        """把真实会话消息转换为黑话学习素材。"""

        user_info = message.message_info.user_info
        if is_bot_self(message.platform, user_info.user_id):
            speaker_kind = "ASSISTANT"
            speaker_name = global_config.bot.nickname or "assistant"
        else:
            speaker_kind = "USER"
            speaker_name = user_info.user_cardname or user_info.user_nickname or "未知用户"

        content = JargonLearner._build_session_message_learning_content(
            message,
            is_self_message=speaker_kind == "ASSISTANT",
        )
        return JargonLearningSourceItem(
            source_kind="session",
            speaker_kind=speaker_kind,
            speaker_name=speaker_name,
            content=content,
            timestamp=message.timestamp,
            original_message=message,
        )

    async def _build_multi_learning_messages(
        self,
        messages: Sequence["SessionMessage"] | Sequence[JargonLearningSourceItem],
        system_prompt: str,
    ) -> List[ContextItem]:
        """构造黑话学习使用的多 message 请求。"""

        source_items = await self._prepare_learning_source_items(messages)
        learning_messages = [
            ContextItemBuilder()
            .set_role(RoleType.System)
            .add_text_content(
                f"{system_prompt}\n\n"
                "注意：聊天上下文会在后续多条 user message 中给出。真实聊天消息会带有 "
                '<message source_id="..."> 属性，source_id 是本轮学习的来源编号。'
                '非真实聊天消息会使用 <learning-source source_id="..."> 标注来源。'
            )
            .build()
        ]

        for index, source_item in enumerate(source_items, start=1):
            learning_messages.append(
                ContextItemBuilder()
                .set_role(RoleType.User)
                .add_text_content(self._build_learning_source_content(index, source_item))
                .build()
            )

        learning_messages.append(
            ContextItemBuilder().set_role(RoleType.User).add_text_content("请根据以上聊天消息输出 JSON。").build()
        )
        return learning_messages

    def _log_learning_context_preview(
        self,
        messages: List[ContextItem],
        *,
        session_id: str,
        source_message_count: int,
        source_type: str,
        generation_result: LLMResponseResult,
    ) -> None:
        """保存黑话抽取的可重放 LLM Prompt，并在日志中输出查看入口。"""

        try:
            preview_access = PromptCLIVisualizer.build_prompt_preview_access(
                messages,
                category="jargon_learner",
                chat_id=session_id,
                request_kind="jargon_learner",
                selection_reason=(
                    f"会话ID: {session_id}\n"
                    f"Learner会话ID: {self.session_id}\n"
                    f"来源: {source_type}\n"
                    f"学习素材数: {source_message_count}\n"
                    f"构建消息数: {len(messages)}\n"
                    "用途: 从聊天记录中抽取黑话候选，本记录保存完整 LLM messages，可在推理过程页面直接重放。"
                ),
                output_title="黑话抽取 LLM 输出",
                output_items=generation_result.output_items,
                metadata={"model_name": generation_result.model_name},
                generation_attempts=generation_result.generation_attempts,
            )
        except Exception as exc:
            logger.warning(f"{self.session_id} 黑话抽取 Prompt 保存失败: {exc}")
            return

        logger.info(
            f"{self.session_id} 黑话抽取 Prompt 已生成: "
            f"WebUI={preview_access.preview_web_uri} "
            f"推理详情={preview_access.reasoning_web_uri} "
            f"JSON={preview_access.record_path}"
        )

    def _log_jargon_update_process(
        self,
        *,
        status: str,
        jargon_miner: JargonMiner,
        source_items: Sequence[JargonLearningSourceItem],
        parsed_entries: Sequence[Tuple[str, str]],
        accepted_entries: Sequence[JargonEntry],
        skipped_entries: Sequence[dict[str, str]],
        saved: int,
        updated: int,
    ) -> None:
        """记录黑话学习解析到写库的过程摘要。"""

        logger.info(
            f"{self.session_id} 黑话学习更新过程: "
            f"status={status}, learning_session_id={jargon_miner.session_id}, "
            f"session_name={jargon_miner.session_name}, source_items={len(source_items)}, "
            f"parsed={len(parsed_entries)}, accepted={len(accepted_entries)}, "
            f"skipped={len(skipped_entries)}, saved={saved}, updated={updated}"
        )

    def _check_cached_jargons_in_messages(
        self,
        messages: Sequence[JargonLearningSourceItem],
        jargon_miner: JargonMiner,
    ) -> List[Tuple[str, str]]:
        """检查缓存中的黑话是否出现在 messages 中。"""

        cached_jargons = jargon_miner.get_cached_jargons()
        if not cached_jargons:
            return []

        matched_entries: List[Tuple[str, str]] = []

        for i, msg in enumerate(messages):
            msg_text = msg.content.strip()
            if not msg_text:
                continue

            for jargon in cached_jargons:
                if not jargon or not jargon.strip():
                    continue

                jargon_content = jargon.strip()
                pattern = re.escape(jargon_content)
                if re.search(r"[\u4e00-\u9fff]", jargon_content):
                    search_pattern = pattern
                else:
                    search_pattern = r"\b" + pattern + r"\b"

                if re.search(search_pattern, msg_text, re.IGNORECASE):
                    matched_entries.append((jargon_content, str(i + 1)))

        return matched_entries

    async def _process_jargon_entries(
        self,
        jargon_entries: List[Tuple[str, str]],
        messages: Sequence[JargonLearningSourceItem],
        jargon_miner: JargonMiner,
    ) -> bool:
        """处理黑话条目，并路由到 JargonMiner。"""

        if not jargon_entries or not messages:
            self._log_jargon_update_process(
                status="empty_input",
                jargon_miner=jargon_miner,
                source_items=messages,
                parsed_entries=jargon_entries,
                accepted_entries=[],
                skipped_entries=[],
                saved=0,
                updated=0,
            )
            return False

        entries: List[JargonEntry] = []
        skipped_entries: list[dict[str, str]] = []

        for content, source_id in jargon_entries:
            content = content.strip()
            if not content:
                skipped_entries.append({"content": content, "source_id": source_id, "reason": "empty_content"})
                continue

            if "SELF" in content:
                logger.info(f"跳过包含 SELF 的黑话：{content}")
                skipped_entries.append({"content": content, "source_id": source_id, "reason": "contains_self"})
                continue

            # TODO: 多平台兼容
            bot_nickname = global_config.bot.nickname
            if bot_nickname and bot_nickname in content:
                logger.info(f"跳过包含机器人昵称的黑话：{content}")
                skipped_entries.append({"content": content, "source_id": source_id, "reason": "contains_bot_nickname"})
                continue

            if not source_id.isdigit():
                logger.warning(f"黑话条目 source_id 无效：content={content}, source_id={source_id}")
                skipped_entries.append({"content": content, "source_id": source_id, "reason": "invalid_source_id"})
                continue

            line_index = int(source_id) - 1
            if line_index < 0 or line_index >= len(messages):
                logger.warning(f"黑话条目 source_id 超出范围：content={content}, source_id={source_id}")
                skipped_entries.append({"content": content, "source_id": source_id, "reason": "source_id_out_of_range"})
                continue

            start_idx = max(0, line_index - 3)
            end_idx = min(len(messages), line_index + 4)
            context_items = messages[start_idx:end_idx]

            context_paragraph = "\n".join(
                [
                    f"[{start_idx + i + 1}] ({item.speaker_kind}/{item.source_kind}) {item.content or ''}"
                    for i, item in enumerate(context_items)
                ]
            )
            if not context_paragraph:
                logger.warning(f"黑话条目上下文为空：content={content}, source_id={source_id}")
                skipped_entries.append({"content": content, "source_id": source_id, "reason": "empty_context"})
                continue

            evidence_messages: JargonEvidenceMessageGroup = []
            for item in context_items:
                msg = item.original_message
                if msg is None:
                    continue
                platform = str(msg.platform or "").strip()
                message_id = str(msg.message_id or "").strip()
                if platform and message_id:
                    evidence_messages.append({"platform": platform, "message_id": message_id})

            if not evidence_messages:
                logger.debug(
                    f"黑话条目上下文没有真实消息证据，仅保留本轮原始上下文：content={content}, source_id={source_id}"
                )

            entries.append(
                {
                    "content": content,
                    "raw_content": {context_paragraph},
                    "evidence_messages": [evidence_messages] if evidence_messages else [],
                }
            )

        if not entries:
            self._log_jargon_update_process(
                status="no_accepted_entries",
                jargon_miner=jargon_miner,
                source_items=messages,
                parsed_entries=jargon_entries,
                accepted_entries=[],
                skipped_entries=skipped_entries,
                saved=0,
                updated=0,
            )
            return False

        saved, updated = await jargon_miner.process_extracted_entries(entries)
        self._log_jargon_update_process(
            status="persisted" if saved + updated > 0 else "no_database_change",
            jargon_miner=jargon_miner,
            source_items=messages,
            parsed_entries=jargon_entries,
            accepted_entries=entries,
            skipped_entries=skipped_entries,
            saved=saved,
            updated=updated,
        )
        logger.info(f"成功处理 {len(entries)} 个黑话条目")
        return saved + updated > 0
