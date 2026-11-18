from collections import Counter
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

import asyncio
import re

from src.chat.utils.utils import is_bot_self
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.logger import get_logger
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItem, ContextItemBuilder, RoleType
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.jargon_context_matcher import is_jargon_reference_text
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

from .expression_utils import parse_jargon_response
from .learning_text import extract_learning_text
from .pending_learning_buffer import PendingLearningBuffer
from .jargon_miner import JargonEntry, JargonEvidenceMessageGroup, JargonMiner

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
    from src.maisaka.context.messages import LLMContextMessage


logger = get_logger("jargon_learner")

jargon_learn_model = LLMServiceClient(task_name="learner", request_type="jargon.learner")
MESSAGE_OPEN_TAG_PATTERN = re.compile(r"^<message\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)


def _is_learnable_session_message(message: "SessionMessage") -> bool:
    return (
        not is_bot_self(message.platform, message.message_info.user_info.user_id)
        and not message.is_notify
    )


@dataclass(frozen=True)
class JargonLearningAcquireResult:
    """黑话学习批次并发闸门的申请结果。"""

    acquired: bool
    reason: str = ""
    active_count: int = 0
    max_count: int = 0


@dataclass(frozen=True)
class JargonLearningSourceItem:
    """关联真实消息的学习正文，身份、时间和归属只从原始消息读取。"""

    original_message: "SessionMessage"
    content: str

    def __post_init__(self) -> None:
        if self.original_message is None:
            raise TypeError("黑话学习素材必须关联原始消息")
        message = self.original_message
        if not all(value.strip() for value in (message.platform, message.message_id, message.session_id)):
            raise ValueError("黑话学习原始消息必须具有平台、消息编号和真实会话归属")



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
        self.pending_messages: PendingLearningBuffer[JargonLearningSourceItem] = PendingLearningBuffer(lambda item: item.original_message.message_id)
        self.learning_status = "pending"
        self.learning_retry_blocked = False
        self.learning_detail = ""

    @staticmethod
    def _get_session_display_name(session_id: str) -> str:
        """获取聊天流展示名称，无法解析时回退到 session_id。"""

        from src.chat.message_receive.chat_manager import chat_manager

        session_name = chat_manager.get_session_name(session_id)
        if session_name:
            return session_name

        chat_manager.get_existing_session_by_session_id(session_id)
        return chat_manager.get_session_name(session_id) or session_id

    def collect_context_messages(self, context_messages: Sequence["LLMContextMessage"]) -> None:
        """先收集真实素材；间隔或并发条件不允许执行时也不丢掉本轮消息。"""
        self.pending_messages.extend(self._extract_learning_sources_from_context(context_messages))

    async def learn_from_context_messages(
        self,
        context_messages: Sequence["LLMContextMessage"],
        jargon_miner: JargonMiner,
        *,
        learned_contents: Optional[List[str]] = None,
    ) -> bool:
        """从 Maisaka 被裁切的上下文消息中学习黑话候选。"""

        self.learning_retry_blocked = False
        self.collect_context_messages(context_messages)
        source_items = self.pending_messages.snapshot()
        if len(source_items) < self.min_messages_for_extraction:
            self.learning_status = "waiting" if source_items else "skipped"
            self.learning_detail = (
                self.pending_messages.waiting_detail(self.min_messages_for_extraction)
                if source_items else "裁切历史中没有可学习的真实聊天消息"
            )
            logger.debug(self.learning_detail)
            return False

        self.learning_status, self.learning_detail = "running", ""
        return await self._learn_from_sources(
            source_items, jargon_miner=jargon_miner, learned_contents=learned_contents
        )

    @staticmethod
    def _extract_learning_sources_from_context(
        context_messages: Sequence["LLMContextMessage"],
    ) -> List[JargonLearningSourceItem]:
        """提取用户本次文字正文，过滤模型输出、机器人发言和无文字消息。"""

        from src.maisaka.context.messages import SessionBackedMessage

        source_items: List[JargonLearningSourceItem] = []
        seen_message_ids: set[str] = set()
        for context_message in context_messages:
            if not isinstance(context_message, SessionBackedMessage):
                continue
            original_message = context_message.original_message
            if not _is_learnable_session_message(original_message):
                continue
            message_id = context_message.message_id
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            source_item = JargonLearner._build_source_item_from_session_message(original_message)
            if source_item.content and not is_jargon_reference_text(source_item.content):
                source_items.append(source_item)

        return source_items

    @staticmethod
    def _build_session_message_learning_content(message: "SessionMessage") -> str:
        """只读取用户本次顶层文字并省略群名片，不触发或等待媒体解析。"""

        body = extract_learning_text(message.raw_message)
        if not body:
            return ""
        user_info = message.message_info.user_info
        user_name = escape(user_info.user_nickname or user_info.user_id, quote=True)
        prefix = f'<message user="{user_name}">\n'
        return prefix + body

    async def _learn_from_sources(
        self,
        pending_messages: Sequence[JargonLearningSourceItem],
        *,
        jargon_miner: JargonMiner,
        learned_contents: Optional[List[str]] = None,
    ) -> bool:
        """对一批上下文素材执行黑话学习。"""

        learning_session_id = self._resolve_learning_session_id(pending_messages)
        if learning_session_id is None:
            logger.warning(f"黑话学习已跳过：无法解析到有效聊天流，learner_session_id={self.session_id}")
            self.learning_retry_blocked = True
            self.learning_status, self.learning_detail = "skipped", "无法解析到有效聊天流，素材已保留"
            return False
        if learning_session_id != self.session_id:
            logger.info(
                f"黑话学习会话 ID 已按真实消息修正: learner_session_id={self.session_id} "
                f"learning_session_id={learning_session_id}"
            )

        acquire_result = await jargon_learning_batch_gate.acquire(learning_session_id)
        if not acquire_result.acquired:
            reasons = {
                "session_busy": "当前会话已有学习批次正在运行",
                "global_limit": f"全局学习并发已满（{acquire_result.active_count}/{acquire_result.max_count}）",
                "max_expression_learner <= 0": "学习并发配置小于或等于 0",
            }
            self.learning_retry_blocked = acquire_result.max_count <= 0
            self.learning_status = "skipped" if self.learning_retry_blocked else "waiting"
            self.learning_detail = reasons[acquire_result.reason] + f"，已保留 {len(self.pending_messages)} 条素材等待重试"
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

        self.pending_messages.discard(pending_messages)
        try:
            return await self._run_learning_batch(
                pending_messages,
                learning_session_id=learning_session_id,
                jargon_miner=jargon_miner,
                learned_contents=learned_contents,
            )
        finally:
            await jargon_learning_batch_gate.release(learning_session_id)

    async def _run_learning_batch(
        self,
        pending_messages: Sequence[JargonLearningSourceItem],
        *,
        learning_session_id: str,
        jargon_miner: JargonMiner,
        learned_contents: Optional[List[str]] = None,
    ) -> bool:
        """执行已经获得并发闸门的黑话学习批次。"""

        # 线上入口已从真实聊天历史提取、清理并去重，批次直接复用同一份素材。
        source_items = pending_messages
        if not source_items:
            logger.debug("没有可用于黑话学习的消息素材")
            self.learning_status, self.learning_detail = "skipped", "没有可用于黑话学习的消息素材"
            return False

        readable_message = "聊天上下文将在后续多条 user message 中给出；请以每条消息中的 id 作为来源行编号。"
        prompt_template = prompt_manager.get_prompt("learn_jargon")
        prompt_template.add_context("bot_name", global_config.bot.nickname)
        prompt_template.add_context("chat_str", readable_message)
        prompt = await prompt_manager.render_prompt(prompt_template)

        try:
            learning_messages = self._build_multi_learning_messages(source_items, prompt)
            generation_result = await jargon_learn_model.generate_response_with_context(
                lambda _client: learning_messages,
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
            if learned_contents is not None:
                raise
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
            self.learning_status, self.learning_detail = "no_results", "模型未提取出黑话候选，已有黑话缓存也未命中"
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
            return await self._process_jargon_entries(
                jargon_entries, source_items, jargon_miner, learned_contents=learned_contents
            )
        finally:
            jargon_miner.session_id = original_jargon_session_id
            jargon_miner.session_name = original_jargon_session_name

    def _resolve_learning_session_id(
        self,
        messages: Sequence[JargonLearningSourceItem],
    ) -> Optional[str]:
        """根据真实消息解析本轮黑话学习应该归属的会话 ID。"""

        from src.chat.message_receive.chat_manager import chat_manager

        candidates: list[str] = []
        for message in messages:
            original_message = message.original_message
            session_id = original_message.session_id.strip()
            if session_id:
                candidates.append(session_id)

        for session_id, _ in Counter(candidates).most_common():
            if chat_manager.get_existing_session_by_session_id(session_id) is not None:
                return session_id

        logger.warning(
            f"黑话学习无法从真实消息中找到已注册聊天流; "
            f"learner_session_id={self.session_id} "
            f"候选 session_id={dict(Counter(candidates))}"
        )
        return None

    @staticmethod
    def _build_learning_source_content(source_id: int, source_item: JargonLearningSourceItem) -> str:
        """来源元信息只从真实消息生成，不接受素材文本声明的身份与会话。"""
        original = source_item.original_message
        user_info = original.message_info.user_info
        user_name = escape(user_info.user_nickname or user_info.user_id, quote=True)
        prefix = f'<message id="{source_id}" user="{user_name}">\n'
        body = MESSAGE_OPEN_TAG_PATTERN.sub("", source_item.content.strip(), count=1).strip()
        return prefix + body

    @staticmethod
    def _build_source_item_from_session_message(message: "SessionMessage") -> JargonLearningSourceItem:
        """把真实会话消息转换为黑话学习素材。"""

        return JargonLearningSourceItem(message, JargonLearner._build_session_message_learning_content(message))

    def _build_multi_learning_messages(
        self,
        source_items: Sequence[JargonLearningSourceItem],
        system_prompt: str,
    ) -> List[ContextItem]:
        """构造黑话学习使用的多 message 请求。"""

        learning_messages = [
            ContextItemBuilder()
            .set_role(RoleType.System)
            .add_text_content(
                f"{system_prompt}\n\n"
                "注意：聊天上下文会在后续多条 user message 中给出。真实聊天消息会带有 "
                '<message id="..."> 属性，id 是本轮学习的来源编号。'
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
        *,
        learned_contents: Optional[List[str]] = None,
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

            # 在候选和缓存命中汇合处校验真实来源，配置变更也能作用于待处理批次。
            original_message = messages[line_index].original_message
            if not _is_learnable_session_message(original_message) or any(
                user.platform == original_message.platform
                and user.user_id == original_message.message_info.user_info.user_id
                for user in global_config.jargon.learning_blacklist
            ):
                skipped_entries.append({"content": content, "source_id": source_id, "reason": "excluded_source"})
                continue

            start_idx = max(0, line_index - 3)
            end_idx = min(len(messages), line_index + 4)
            context_items = messages[start_idx:end_idx]

            context_paragraph = "\n".join(
                [
                    f"[{start_idx + i + 1}] {item.content}"
                    for i, item in enumerate(context_items)
                ]
            )
            if not context_paragraph:
                logger.warning(f"黑话条目上下文为空：content={content}, source_id={source_id}")
                skipped_entries.append({"content": content, "source_id": source_id, "reason": "empty_context"})
                continue

            evidence_messages: JargonEvidenceMessageGroup = [
                {"platform": item.original_message.platform, "message_id": item.original_message.message_id}
                for item in context_items
            ]

            entries.append(
                {
                    "content": content,
                    "raw_content": {context_paragraph},
                    "evidence_messages": [evidence_messages],
                }
            )

        if not entries:
            self.learning_status, self.learning_detail = "no_results", f"{len(jargon_entries)} 个黑话候选均未通过内容或来源校验"
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
        # 展示本批交给黑话矿工处理的候选，不能把候选数量当成新增词条数量。
        if learned_contents is not None:
            learned_contents.extend(str(entry["content"]) for entry in entries)
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
        self.learning_status = "completed" if saved + updated > 0 else "no_results"
        self.learning_detail = f"处理 {len(entries)} 个候选，新增 {saved} 个、更新 {updated} 个词条"
        return saved + updated > 0
