"""Maisaka 非 CLI 运行时。"""

from datetime import datetime
from math import ceil
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Literal, Optional, Sequence
import asyncio
import json
import time
import uuid

from src.chat.debounce import MessageDebouncer
from src.chat.debounce.message_adapter import get_message_shape
from src.chat.heart_flow.heartFC_utils import CycleDetail
from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.chat.message_receive.message import SessionMessage
from src.chat.replyer.expression_vector_index import expression_vector_index
from src.chat.utils.utils import is_bot_self, is_mentioned_bot_in_message
from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.common.logger import get_logger
from src.common.message_repository import find_messages
from src.common.utils.utils_config import ChatConfigUtils, ExpressionConfigUtils, JargonConfigUtils
from src.config.config import global_config, model_config
from src.agent.provider import AgentToolProvider
from src.core.tooling import ToolRegistry, ToolSpec
from src.learners.expression_learner import ExpressionLearner
from src.learners.jargon_learner import JargonLearner
from src.learners.jargon_miner import JargonMiner
from src.maisaka.quote import ReplyQuoteContext, ReplyQuoteCounter
from src.llm_models.payload_content.resp_format import RespFormat
from src.llm_models.payload_content.tool_option import ToolDefinitionInput
from src.maisaka.builtin_tool.provider import MaisakaBuiltinToolProvider
from src.maisaka.context.clear_context import select_messages_after_latest_clear_marker
from src.maisaka.context.history import drop_leading_orphan_tool_results
from src.maisaka.context.message_id_alias import build_alias_map, expand_message_id_aliases
from src.maisaka.context.messages import (
    ContentContextMessage,
    InternalContextMessage,
    LLMContextMessage,
    ModelOutputContextMessage,
    ReferenceMessage,
    ReferenceMessageType,
    SessionBackedMessage,
    ToolResultMessage,
)
from src.maisaka.display.runtime_mixin import MaisakaRuntimeDisplayMixin
from src.maisaka.display.stage_status_board import remove_stage_status, update_stage_status
from src.maisaka.focus import focus_mode_manager
from src.maisaka.monitor.events import (
    emit_message_ingested,
    emit_message_sent,
    emit_message_updated,
    emit_session_start,
    emit_subagent_finished,
)
from src.maisaka.monitor.message_payload import (
    build_monitor_message_content,
    build_monitor_message_media,
    build_monitor_reply_preview,
)
from src.maisaka.turn_trigger import IdleBackoffController, MessageTurnScheduler
from src.mcp_module.provider import MCPToolProvider
from src.mcp_module.service import get_mcp_service
from src.web_search.provider import WebSearchToolProvider
from src.web_search.config import resolve_web_search_config
from src.web_search.service import WebSearchService
from src.plugin_runtime.tool_provider import PluginToolProvider
from src.services.message_word_frequency_service import update_high_frequency_terms_from_context_messages

from .chat_loop_service import ChatResponse, MaisakaChatLoopService
from .reasoning_engine import MaisakaReasoningEngine
from .subagent_tasks import SubAgentTask, SubAgentTaskManager, format_tool_usage

if TYPE_CHECKING:
    from .focus.group_runtime import FocusGroupRuntime

logger = get_logger("maisaka_runtime")

MAX_INTERNAL_ROUNDS = 16
MAX_RETAINED_MESSAGE_CACHE_SIZE = 200
CONTEXT_RESTORE_FILL_RATIO = 0.5
CONTEXT_RESTORE_SHORT_RESTART_SECONDS = 5 * 60
CONTEXT_RESTORE_SHORT_OFFLINE_SECONDS = 30 * 60
CONTEXT_RESTORE_LONG_SLEEP_SECONDS = 6 * 60 * 60
CONTEXT_RESTORE_DAY_SECONDS = 24 * 60 * 60


class PlannerInterruptController:
    """维护当前可打断的 Planner 请求；同一次请求只打断一次。"""

    def __init__(self) -> None:
        self._flag: Optional[asyncio.Event] = None
        self._requested = False

    def bind(self, interrupt_flag: asyncio.Event) -> None:
        """绑定当前可打断请求使用的中断标记。"""

        self._flag = interrupt_flag
        self._requested = False

    def unbind(self, interrupt_flag: asyncio.Event) -> None:
        """解绑当前可打断请求的中断标记。"""

        if self._flag is interrupt_flag:
            self._flag = None
        self._requested = False

    def request(self) -> Literal["request", "duplicate", "idle"]:
        """尝试发起打断，返回 request/duplicate/idle。"""

        if self._flag is None:
            return "idle"
        if self._requested:
            return "duplicate"

        self._requested = True
        self._flag.set()
        return "request"


class MaisakaHeartFlowChatting(MaisakaRuntimeDisplayMixin):
    """会话级别的 Maisaka 运行时。"""

    _focus_group: Optional["FocusGroupRuntime"] = None

    _STATE_RUNNING: Literal["running"] = "running"
    _STATE_WAIT: Literal["wait"] = "wait"
    _STATE_STOP: Literal["stop"] = "stop"

    def __init__(self, session_id: str):
        self.session_id = session_id
        chat_stream = chat_manager.get_session_by_session_id(session_id)
        if chat_stream is None:
            raise ValueError(f"未找到会话 {session_id} 对应的 Maisaka 运行时")
        self.chat_stream: BotChatSession = chat_stream

        session_name = chat_manager.get_session_name(session_id) or session_id
        self.session_name = session_name
        self.log_prefix = f"[{session_name}]"
        self._chat_loop_service = MaisakaChatLoopService(
            session_id=session_id,
            is_group_chat=self.chat_stream.is_group_session,
        )
        self._chat_history: list[LLMContextMessage] = []
        self._quote_counter = ReplyQuoteCounter(session_id)

        # Keep all original messages for batching and later learning.
        self.message_cache: list[SessionMessage] = []
        self._last_processed_index = 0
        self._internal_turn_queue: asyncio.Queue[Literal["message", "timeout", "proactive"]] = asyncio.Queue()
        self._proactive_trigger_message: Optional[SessionMessage] = None
        self._proactive_logical_turn_id: Optional[str] = None
        self._subagent_tasks = SubAgentTaskManager()
        # Focus 成员保留自己的聊天服务，前台 Planner 只在所属共享组中运行。
        self._focus_group: Optional[FocusGroupRuntime] = None
        self._message_ingest_lock = asyncio.Lock()

        self._current_cycle_detail: Optional[CycleDetail] = None
        self._running = False
        self._cycle_counter = 0
        self._monitor_run_id = uuid.uuid4().hex
        self._internal_loop_task: Optional[asyncio.Task] = None
        self._message_turn_scheduled = False
        self._deferred_message_turn_task: Optional[asyncio.Task[None]] = None
        self._fixed_message_debouncer = MessageDebouncer()
        self._message_debounce_was_enabled = False
        self._last_message_received_at = 0.0
        self._talk_frequency_adjust = 1.0
        self._wait_timeout_task: Optional[asyncio.Task[None]] = None
        self._max_internal_rounds = MAX_INTERNAL_ROUNDS
        self._agent_state: Literal["running", "wait", "stop"] = self._STATE_STOP
        self._pending_wait_tool_call_id: Optional[str] = None
        self._pending_wait_logical_turn_id: Optional[str] = None
        self._pending_wait_started_at: Optional[float] = None
        self._pending_wait_seconds: Optional[float] = None
        self._consecutive_wait_count = 0
        self._consecutive_idle_count = 0
        self._current_action_tool_names: set[str] = set()
        self.discovered_tool_names: set[str] = set()
        self.deferred_tool_specs_by_name: dict[str, ToolSpec] = {}

        self._min_extraction_interval = 30
        self._last_expression_extraction_time = 0.0
        self._trimmed_history_learning_task: Optional[asyncio.Task[None]] = None
        self._queued_trimmed_learning_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._expression_learner = ExpressionLearner(session_id)
        self._jargon_learner = JargonLearner(session_id)
        self._jargon_miner = JargonMiner(session_id, session_name=session_name)

        self._reasoning_engine = MaisakaReasoningEngine(self)
        self._message_turn_scheduler = MessageTurnScheduler(self)
        self._idle_backoff = IdleBackoffController(self)
        self._forced_turn_enabled = False
        self._forced_turn_message_id = ""
        self._forced_turn_reason = ""
        self._planner_continuation_active = False
        self._planner_interrupt = PlannerInterruptController()
        self._monitor_session_start_task: Optional[asyncio.Task[None]] = None
        self._monitor_visual_refresh_keys: set[tuple[str, str]] = set()
        self._tool_registry = ToolRegistry()
        self._register_tool_providers()
        self._emit_monitor_session_start()

    @property
    def _max_context_size(self) -> int:
        """返回当前会话实时生效的上下文窗口大小。"""

        configured_context_size = (
            global_config.chat.max_context_size
            if self.chat_stream.is_group_session
            else global_config.chat.max_private_context_size
        )
        return max(1, int(configured_context_size))

    @property
    def _enable_expression_learning(self) -> bool:
        """返回当前会话实时生效的表达学习开关。"""

        _, enable_learning = ExpressionConfigUtils.get_expression_config_for_chat(self.session_id)
        return enable_learning


    @property
    def _enable_jargon_learning(self) -> bool:
        """返回当前会话实时生效的黑话学习开关。"""

        _, enable_learning = JargonConfigUtils.get_jargon_config_for_chat(self.session_id)
        return enable_learning

    def _emit_monitor_session_start(self) -> None:
        """向 WebUI 监控面板同步当前会话的展示标识。"""

        try:
            self._monitor_session_start_task = asyncio.create_task(
                emit_session_start(
                    session_id=self.session_id,
                    session_name=self.session_name,
                    is_group_chat=self.chat_stream.is_group_session,
                    group_id=self.chat_stream.group_id,
                    user_id=self.chat_stream.user_id,
                    platform=self.chat_stream.platform,
                )
            )
        except RuntimeError:
            logger.debug("MaiSaka 监控会话开始事件未发送：当前没有运行中的事件循环")


    def _update_stage_status(
        self, stage: str, detail: str = "", *, round_text: str = "", wait_until: Optional[float] = None,
    ) -> None:
        """更新当前会话的阶段状态。"""
        if (wait_until is None and self._agent_state == self._STATE_WAIT
                and self._wait_timeout_task is not None and self._pending_wait_started_at is not None
                and self._pending_wait_seconds is not None):
            wait_until = self._pending_wait_started_at + self._pending_wait_seconds
        update_stage_status(
            session_id=self.session_id,
            session_name=self.session_name,
            stage=stage,
            detail=detail,
            round_text=round_text,
            agent_state=self._agent_state,
            wait_until=wait_until,
        )

    async def start(self) -> None:
        """启动运行时主循环。"""
        if self._running:
            self._ensure_background_tasks_running()
            return

        if global_config.mcp.enable:
            await self._init_mcp()

        await self._restore_recent_context_from_db()
        self._running = True
        if self._is_focus_mode_active_for_current_chat():
            await focus_mode_manager.attach(self)
        self._ensure_background_tasks_running()
        self._schedule_message_turn()
        self._update_stage_status("空闲", "等待消息触发")
        logger.info(f"{self.log_prefix} Maisaka 运行时已启动")

    async def _restore_recent_context_from_db(self) -> None:
        """启动时从消息库恢复最近上下文，避免重启后丢失短期对话窗口。"""

        if self._chat_history or self.message_cache:
            return

        try:
            recent_messages = await asyncio.to_thread(
                find_messages,
                session_id=self.session_id,
                limit=self._get_context_restore_limit(),
                limit_mode="latest",
            )
        except Exception as exc:
            logger.warning(f"{self.log_prefix} 恢复最近上下文失败: {exc}", exc_info=True)
            return

        recent_messages = select_messages_after_latest_clear_marker(recent_messages)
        restored_user_messages: list[SessionMessage] = []
        restored_history: list[LLMContextMessage] = []
        for message in recent_messages:
            if message.is_notify:
                continue

            self._quote_counter.record(message)

            source_kind = self._resolve_restored_message_source_kind(message)
            history_message = await self._reasoning_engine._build_history_message(
                message,
                source_kind=source_kind,
            )
            if history_message is not None:
                restored_history.append(history_message)

            if source_kind == "user":
                restored_user_messages.append(message)

        if not restored_history:
            return

        self._chat_history.extend(restored_history)
        restore_reference_message = self._build_context_restore_reference_message(
            restored_history,
            now=datetime.now(),
        )
        if restore_reference_message is not None:
            self._chat_history.append(restore_reference_message)
        self.message_cache = restored_user_messages[-MAX_RETAINED_MESSAGE_CACHE_SIZE:]
        self._last_processed_index = len(self.message_cache)
        logger.info(
            f"{self.log_prefix} 已恢复最近上下文: "
            f"历史消息={len(restored_history)} 用户消息缓存={len(self.message_cache)}"
        )

    def _get_context_restore_limit(self) -> int:
        """返回启动时最多回灌的真实消息数量。"""

        return max(1, ceil(self._max_context_size * CONTEXT_RESTORE_FILL_RATIO))

    async def restore_proactive_user_context(self) -> int:
        """主动回合前确保上下文含有可回复的真实用户消息。

        主动任务触发于长期静默的聊天流，运行时历史中可能没有任何带
        msg_id 的用户消息，Planner 唯一的文本出口 reply 会因缺少引用
        目标而无法发言。此处复用启动恢复的取数与转换链路，从消息库
        回填最近的用户消息作为可引用目标。返回本次回填的消息条数。
        """

        if any(
            isinstance(message, SessionBackedMessage) and message.source_kind == "user" and message.message_id
            for message in self._chat_history
        ):
            return 0

        try:
            recent_messages = await asyncio.to_thread(
                find_messages,
                session_id=self.session_id,
                limit=self._get_context_restore_limit(),
                limit_mode="latest",
                filter_bot=True,
            )
            # 会话流发生分裂迁移时，历史消息仍挂在旧 session_id 下，
            # 此时按平台目标（群号或用户号）回退查询，保证仍能取到历史。
            if not recent_messages:
                if self.chat_stream.is_group_session:
                    recent_messages = await asyncio.to_thread(
                        find_messages,
                        platform=self.chat_stream.platform,
                        group_id=self.chat_stream.group_id,
                        limit=self._get_context_restore_limit(),
                        limit_mode="latest",
                        filter_bot=True,
                    )
                else:
                    recent_messages = await asyncio.to_thread(
                        find_messages,
                        platform=self.chat_stream.platform,
                        user_id=self.chat_stream.user_id,
                        limit=self._get_context_restore_limit(),
                        limit_mode="latest",
                        filter_bot=True,
                    )
        except Exception as exc:
            logger.warning(f"{self.log_prefix} 主动回合回填用户消息失败: {exc}", exc_info=True)
            return 0

        recent_messages = select_messages_after_latest_clear_marker(recent_messages)
        existing_message_ids = {
            message.message_id
            for message in self._chat_history
            if isinstance(message, SessionBackedMessage) and message.message_id
        }
        restored_history: list[LLMContextMessage] = []
        for message in recent_messages:
            if message.is_notify or message.message_id in existing_message_ids:
                continue
            history_message = await self._reasoning_engine._build_history_message(message, source_kind="user")
            if history_message is not None:
                restored_history.append(history_message)

        if not restored_history:
            return 0

        insert_index = next(
            (index for index, message in enumerate(self._chat_history) if isinstance(message, SessionBackedMessage)),
            len(self._chat_history),
        )
        self._chat_history[insert_index:insert_index] = restored_history
        logger.info(f"{self.log_prefix} 主动回合已回填用户消息 {len(restored_history)} 条，作为可回复引用目标")
        return len(restored_history)

    @staticmethod
    def _build_context_restore_reference_message(
        restored_history: Sequence[LLMContextMessage],
        *,
        now: datetime,
    ) -> ReferenceMessage | None:
        """构造启动上下文恢复提示，帮助模型理解离线间隔。"""

        if not restored_history:
            return None

        last_context_time = restored_history[-1].timestamp
        elapsed_seconds = max(0.0, (now - last_context_time).total_seconds())
        elapsed_text = MaisakaHeartFlowChatting._format_context_restore_elapsed(elapsed_seconds)
        wakeup_text = MaisakaHeartFlowChatting._build_context_restore_wakeup_text(elapsed_seconds, elapsed_text)
        content = (
            "这是启动时恢复的历史上下文提醒，不代表当前用户刚刚发来新消息。\n"
            f"距离上次关机前最后一条可恢复聊天记录已经过去 {elapsed_text}。\n"
            f"{wakeup_text}\n"
            "前面恢复出来的历史消息是你上次关机前记得的聊天内容；"
            "回复时请结合实际间隔，短间隔自然续聊，长间隔可以表现出刚醒来或重新上线后的状态。"
        )
        return ReferenceMessage(
            content=content,
            timestamp=now,
            reference_type=ReferenceMessageType.CONTEXT_RESTORE,
            remaining_uses_value=None,
            display_prefix="[上下文恢复]",
        )

    @staticmethod
    def _format_context_restore_elapsed(elapsed_seconds: float) -> str:
        """将上下文恢复间隔格式化为适合 Prompt 阅读的中文时间。"""

        total_seconds = max(0, int(elapsed_seconds))
        if total_seconds < 60:
            return "不到 1 分钟"

        total_minutes = total_seconds // 60
        if total_minutes < 60:
            seconds = total_seconds % 60
            if seconds == 0:
                return f"{total_minutes} 分钟"
            return f"{total_minutes} 分 {seconds} 秒"

        total_hours = total_minutes // 60
        minutes = total_minutes % 60
        if total_hours < 24:
            if minutes == 0:
                return f"{total_hours} 小时"
            return f"{total_hours} 小时 {minutes} 分钟"

        days = total_hours // 24
        hours = total_hours % 24
        if hours == 0:
            return f"{days} 天"
        return f"{days} 天 {hours} 小时"

    @staticmethod
    def _build_context_restore_wakeup_text(elapsed_seconds: float, elapsed_text: str) -> str:
        """根据离线间隔生成不同强度的恢复描述。"""

        if elapsed_seconds < CONTEXT_RESTORE_SHORT_RESTART_SECONDS:
            return "你只是短暂重启了一下，几乎没有明显中断，可以自然接上刚才的话题。"
        if elapsed_seconds < CONTEXT_RESTORE_SHORT_OFFLINE_SECONDS:
            return f"你短暂离线了 {elapsed_text}，仍然清楚记得刚才的聊天内容。"
        if elapsed_seconds < CONTEXT_RESTORE_LONG_SLEEP_SECONDS:
            return f"你离线休息了 {elapsed_text}，醒来后仍记得上次关机前的聊天内容。"
        if elapsed_seconds < CONTEXT_RESTORE_DAY_SECONDS:
            return f"你沉睡了 {elapsed_text}，重新上线后仍记得上次关机前的聊天内容。"
        return f"你已经离线了 {elapsed_text}，像沉睡了一段时间；恢复后仍记得上次关机前的聊天内容。"

    @staticmethod
    def _resolve_restored_message_source_kind(message: SessionMessage) -> str:
        """根据发送者身份区分恢复消息来自用户还是麦麦自己。"""

        user_info = message.message_info.user_info
        if is_bot_self(message.platform, user_info.user_id):
            return "guided_reply"
        return "user"

    async def stop(self) -> None:
        """停止运行时主循环。"""
        async with self._message_ingest_lock:
            await self._stop_runtime()

    async def _stop_runtime(self) -> None:
        """在入站边界完成归属撤销，再停止会话自身的后台服务。"""
        if not self._running:
            return

        self._running = False
        # 先向普通推理循环发出取消，再等待 Focus、学习器或子代理的清理。
        # 否则后台服务退出期间仍可能继续规划和调用即将销毁的插件。
        if self._internal_loop_task is not None:
            self._internal_loop_task.cancel()
        await focus_mode_manager.detach(self)
        self._fixed_message_debouncer.consume()
        self._mark_message_turn_unscheduled()
        self._cancel_deferred_message_turn_task()
        self._cancel_wait_timeout_task()
        await self._cancel_trimmed_history_learning_task()
        await self._stop_background_subagents()
        while not self._internal_turn_queue.empty():
            _ = self._internal_turn_queue.get_nowait()

        if self._internal_loop_task is not None:
            try:
                await self._internal_loop_task
            except asyncio.CancelledError:
                pass
            finally:
                self._internal_loop_task = None

        await self._tool_registry.close()
        remove_stage_status(self.session_id)

        logger.info(f"{self.log_prefix} Maisaka 运行时已停止")

    def adjust_talk_frequency(self, frequency: float) -> None:
        """调整当前会话的回复频率倍率。"""
        self._talk_frequency_adjust = max(0.0, float(frequency))
        self._schedule_message_turn()

    def append_sent_message_to_chat_history(
        self,
        message: SessionMessage,
        *,
        source_kind: str = "guided_reply",
    ) -> bool:
        """将一条已发送成功的消息同步到 Maisaka 内部历史。"""

        try:
            from src.maisaka.context.history import build_prefixed_message_sequence, build_session_message_visible_text
            from src.maisaka.context.messages import SessionBackedMessage
            from src.maisaka.context.planner_messages import (
                build_planner_prefix,
                extract_quote_ids_from_message_sequence,
            )

            user_info = message.message_info.user_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
            include_chat_id = self._is_focus_mode_active_for_current_chat()
            planner_prefix = build_planner_prefix(
                timestamp=message.timestamp,
                user_name=speaker_name,
                group_card=user_info.user_cardname or "",
                message_id=message.message_id,
                chat_id=message.session_id,
                quote_ids=extract_quote_ids_from_message_sequence(message.raw_message),
                include_message_id=not message.is_notify and bool(message.message_id),
                include_chat_id=include_chat_id,
                is_self_message=source_kind == "guided_reply",
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
            self._chat_history.append(history_message)
            if self._focus_group is not None:
                self._focus_group.record_sent_message(self, history_message)
            self._quote_counter.record(message)
            self._schedule_sent_image_recognition(message)
            self._emit_monitor_message_sent(
                message=message,
                speaker_name=speaker_name,
                source_kind=source_kind,
            )
            return True
        except Exception as exc:
            logger.warning(
                f"{self.log_prefix} 同步已发送消息到 Maisaka 历史失败: message_id={message.message_id} error={exc}"
            )
            return False

    def _schedule_sent_image_recognition(self, message: SessionMessage) -> None:
        """为已发送并同步进历史的图片消息调度后台识图。"""

        images = self._collect_sent_image_components(message.raw_message.components)
        readable_images = [image for image in images if image.binary_data]
        if not readable_images:
            return

        try:
            asyncio.get_running_loop().create_task(self._recognize_sent_images(readable_images, message.message_id))
        except RuntimeError:
            logger.debug(f"{self.log_prefix} 当前无运行中的事件循环，跳过已发送图片后台识图调度")

    def _collect_sent_image_components(self, components: Sequence[object]) -> list[ImageComponent]:
        """递归收集消息序列中的图片组件。"""

        images: list[ImageComponent] = []
        for component in components:
            if isinstance(component, ImageComponent):
                images.append(component)
                continue
            if not isinstance(component, ForwardNodeComponent):
                continue
            for forward_component in component.forward_components:
                images.extend(self._collect_sent_image_components(forward_component.content))
        return images

    async def _recognize_sent_images(self, images: list[ImageComponent], message_id: str) -> None:
        """后台触发已发送图片的描述构建，不阻塞发送链路。"""

        from src.chat.image_system.image_manager import image_manager

        for image in images:
            try:
                await image_manager.get_image_description(
                    image_hash=image.binary_hash,
                    image_bytes=image.binary_data,
                    wait_for_build=False,
                )
            except Exception as exc:
                logger.debug(
                    f"{self.log_prefix} 调度已发送图片识别失败: "
                    f"message_id={message_id} image_hash={image.binary_hash} error={exc}"
                )

    async def enqueue_proactive_task(
        self,
        *,
        plugin_id: str,
        intent: str,
        reason: str = "",
        priority: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """追加一个插件主动聊天任务，并唤醒 Maisaka 主循环。"""

        normalized_plugin_id = str(plugin_id or "").strip() or "unknown"
        normalized_intent = str(intent or "").strip()
        if not normalized_intent:
            raise ValueError("主动聊天任务缺少 intent")

        task_id = f"proactive:{normalized_plugin_id}:{int(time.time() * 1000)}"
        detail_lines = [
            f'<plugin_proactive_task id="{task_id}" plugin_id="{normalized_plugin_id}">',
            f"插件请求你主动处理一轮聊天：{normalized_intent}",
        ]
        if reason:
            detail_lines.append(f"触发原因：{reason}")
        if priority:
            detail_lines.append(f"优先级：{priority}")
        if metadata:
            detail_lines.append(f"附加信息：{json.dumps(metadata, ensure_ascii=False, default=str)}")
        detail_lines.extend(
            [
                "请结合当前聊天关系、记忆和上下文，自行决定是否回复以及如何表达。",
                "</plugin_proactive_task>",
            ]
        )
        visible_text = "\n".join(detail_lines)
        self._chat_history.append(
            InternalContextMessage(
                raw_message=MessageSequence([TextComponent(visible_text)]),
                visible_text=visible_text,
                timestamp=datetime.now(),
                message_id=task_id,
                source_kind=f"plugin_proactive:{normalized_plugin_id}",
            )
        )
        proactive_trigger_message = self._build_proactive_trigger_message(task_id)
        self._arm_forced_turn_state(message_id=task_id, reason="插件主动聊天任务")
        self._queue_proactive_turn(proactive_trigger_message)
        logger.info(f"{self.log_prefix} 已接收插件主动聊天任务: plugin_id={normalized_plugin_id} task_id={task_id}")
        return {
            "stream_id": self.session_id,
            "task_id": task_id,
            "queued": True,
        }

    def enqueue_internal_task(
        self, *, source_kind: str, text: str, reason: str, images: Sequence[ImageComponent] = (),
    ) -> Dict[str, Any]:
        """本体后台任务复用真实会话上下文和正常 Planner 调度。"""
        if not self._running:
            raise RuntimeError("不能向已停止的聊天运行实例投递任务")
        task_id = f"{source_kind}:{uuid.uuid4().hex}"
        self._chat_history.append(InternalContextMessage(
            raw_message=MessageSequence([TextComponent(text), *images]), visible_text=text,
            timestamp=datetime.now(), message_id=task_id, source_kind=source_kind,
        ))
        self._ensure_background_tasks_running()
        self._arm_forced_turn_state(message_id=task_id, reason=reason)
        self._queue_proactive_turn(self._build_proactive_trigger_message(task_id))
        return {"task_id": task_id, "queued": True}

    async def _stop_background_subagents(self) -> None:
        """运行时停止时终止全部后台子代理，并让观察页知道它们已被终止。"""

        running_tasks = [task for task in self._subagent_tasks.list_tasks() if task.status == "running"]
        await self._subagent_tasks.cancel_all()
        for task in running_tasks:
            await emit_subagent_finished(
                session_id=self.session_id,
                task_id=task.task_id,
                status="stopped",
                conclusion="",
                error_message="",
                tool_usage=task.tool_usage,
                duration_ms=task.elapsed_seconds * 1000,
            )

    def deliver_subagent_result(self, task: SubAgentTask) -> None:
        """后台子代理结束时，把结论作为一条新的 user 消息追加到上下文末尾并唤醒 Planner。

        结论只追加在历史末尾，不回填到当初调用 subagent 的位置，已经发送过的上下文前缀保持不变。
        """

        if not self._running:
            return

        # 任务说明是 Planner 自己写的，这里只带一段摘要帮它对上是哪个任务
        task_preview = task.description.replace("\n", " ")
        if len(task_preview) > 200:
            task_preview = f"{task_preview[:200]}…"
        detail_lines = [
            f'<subagent_result id="{task.task_id}" status="{task.status}">',
            f"你派发的子代理任务已结束。任务：{task_preview}",
            f"工具使用：{format_tool_usage(task.tool_usage)}",
        ]
        if task.status == "completed":
            detail_lines.append(f"结论：\n{task.conclusion}")
        else:
            detail_lines.append(f"子代理没有完成任务：{task.error_message}")
        detail_lines.extend(
            [
                "请结合当前聊天自行决定是否使用这个结论，以及是否需要回复。",
                "</subagent_result>",
            ]
        )
        visible_text = "\n".join(detail_lines)
        self._chat_history.append(
            InternalContextMessage(
                raw_message=MessageSequence([TextComponent(visible_text)]),
                visible_text=visible_text,
                timestamp=datetime.now(),
                message_id=task.task_id,
                source_kind="subagent_result",
            )
        )
        self._arm_forced_turn_state(message_id=task.task_id, reason="子代理任务结束")
        self._queue_proactive_turn(self._build_proactive_trigger_message(task.task_id, "子代理任务结束"))
        logger.info(f"{self.log_prefix} 后台子代理任务结束并已送回结论: task_id={task.task_id} status={task.status}")

    def _build_proactive_trigger_message(self, task_id: str, description: str = "插件主动聊天任务") -> SessionMessage:
        """构造主动任务触发消息，不写入消息数据库。"""

        message = SessionMessage(
            message_id=task_id,
            timestamp=datetime.now(),
            platform=self.chat_stream.platform,
        )
        message.session_id = self.session_id
        message.message_info = MessageInfo(
            user_info=self._build_runtime_user_info(),
            group_info=self._build_group_info(),
            additional_config={},
        )
        message.account_id = self.chat_stream.account_id
        message.scope = self.chat_stream.scope
        message.raw_message = MessageSequence([TextComponent(description)])
        message.processed_plain_text = description
        return message

    def _queue_proactive_turn(
        self,
        trigger_message: SessionMessage,
        *,
        logical_turn_id: str | None = None,
    ) -> None:
        """设置主动触发消息，并投递 proactive 内部循环。"""
        if self._focus_group is not None:
            self._focus_group.register_proactive(self, trigger_message)
            return

        self._proactive_trigger_message = trigger_message
        self._proactive_logical_turn_id = logical_turn_id
        self._resume_from_wait_for_proactive_trigger()
        self._internal_turn_queue.put_nowait("proactive")

    def _consume_proactive_trigger_message(self) -> Optional[SessionMessage]:
        """消费当前主动触发消息。"""

        trigger_message = self._proactive_trigger_message
        self._proactive_trigger_message = None
        return trigger_message

    def _consume_proactive_logical_turn_id(self) -> str | None:
        """消费主动触发需要继承的逻辑工具轮次。"""

        logical_turn_id = self._proactive_logical_turn_id
        self._proactive_logical_turn_id = None
        return logical_turn_id

    def _emit_monitor_message_sent(
        self,
        *,
        message: SessionMessage,
        speaker_name: str,
        source_kind: str,
    ) -> None:
        """异步广播 MaiSaka 自己发出的消息，供 WebUI 实时展示。"""

        try:
            asyncio.create_task(
                emit_message_sent(
                    session_id=self.session_id,
                    speaker_name=speaker_name,
                    content=self._build_monitor_message_content(message),
                    message_id=message.message_id,
                    timestamp=message.timestamp.timestamp(),
                    source_kind=source_kind,
                    platform=message.platform,
                    user_id=message.message_info.user_info.user_id,
                    group_id=message.message_info.group_info.group_id if message.message_info.group_info else "",
                    reply_to=self._build_monitor_reply_preview(message),
                    media=self._build_monitor_message_media(message),
                )
            )
        except RuntimeError as exc:
            logger.debug(f"{self.log_prefix} 广播已发送消息到监控面板失败: {exc}")

    def _build_monitor_message_content(self, message: SessionMessage) -> str:
        """生成监控面板使用的消息可见文本。"""

        return build_monitor_message_content(
            message,
            refresh_visual_components=self._refresh_monitor_visual_components,
            log_prefix=self.log_prefix,
        )

    def _refresh_monitor_visual_components(self, message: SessionMessage) -> None:
        """刷新待识别图片描述，并登记识别完成后的监控面板更新。"""

        try:
            from src.maisaka.visual.chat_history_refresher import _refresh_pending_visual_components

            _refresh_pending_visual_components(message.raw_message.components)
            self._register_monitor_visual_placeholder_refresh(message)
        except Exception as exc:
            logger.debug(f"{self.log_prefix} 刷新监控消息媒体描述失败: message_id={message.message_id} error={exc}")

    def _build_monitor_message_media(self, message: SessionMessage) -> list[dict[str, Any]]:
        """构造监控面板可切换展示的原始图片或表情。"""

        return build_monitor_message_media(message, log_prefix=self.log_prefix)

    def _build_monitor_reply_preview(self, message: SessionMessage) -> Optional[dict[str, str]]:
        """构造监控面板中的回复引用预览。"""

        return build_monitor_reply_preview(
            message,
            find_source_message_by_id=self.find_source_message_by_id,
            build_source_content=self._build_monitor_message_content,
        )

    def _register_monitor_visual_placeholder_refresh(self, message: SessionMessage) -> None:
        """登记监控面板中待识别图片的后续刷新。"""

        from src.maisaka.visual.chat_history_refresher import register_monitor_image_placeholder_refresh

        for image in self._collect_sent_image_components(message.raw_message.components):
            if not image.binary_hash:
                continue
            if image.content.strip() not in {"", "[图片，识别中.....]"}:
                continue

            refresh_key = (message.message_id, image.binary_hash)
            if refresh_key in self._monitor_visual_refresh_keys:
                continue

            self._monitor_visual_refresh_keys.add(refresh_key)
            register_monitor_image_placeholder_refresh(
                image.binary_hash,
                lambda _image_hash, tracked_message=message, key=refresh_key: (
                    self._schedule_monitor_visual_placeholder_refresh(tracked_message, key)
                ),
            )

    def _schedule_monitor_visual_placeholder_refresh(
        self,
        message: SessionMessage,
        refresh_key: tuple[str, str],
    ) -> None:
        try:
            asyncio.create_task(self._emit_monitor_message_updated(message, refresh_key))
        except RuntimeError as exc:
            self._monitor_visual_refresh_keys.discard(refresh_key)
            logger.debug(f"{self.log_prefix} 调度监控消息图片刷新失败: {exc}")

    async def _emit_monitor_message_updated(
        self,
        message: SessionMessage,
        refresh_key: tuple[str, str],
    ) -> None:
        """图片识别完成后，原地刷新监控面板中的消息内容。"""

        try:
            from src.maisaka.visual.chat_history_refresher import _refresh_pending_visual_components

            if not _refresh_pending_visual_components(message.raw_message.components):
                return

            user_info = message.message_info.user_info
            group_info = message.message_info.group_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
            await emit_message_updated(
                session_id=self.session_id,
                speaker_name=speaker_name,
                content=self._build_monitor_message_content(message),
                message_id=message.message_id,
                timestamp=message.timestamp.timestamp(),
                received_at=message.received_at.timestamp() if message.received_at is not None else None,
                platform=message.platform,
                user_id=user_info.user_id,
                group_id=group_info.group_id if group_info else "",
                reply_to=self._build_monitor_reply_preview(message),
                media=self._build_monitor_message_media(message),
            )
        finally:
            self._monitor_visual_refresh_keys.discard(refresh_key)

    def _emit_monitor_message_ingested(self, message: SessionMessage) -> None:
        """异步广播收到的新消息，供 WebUI 实时展示。"""

        try:
            user_info = message.message_info.user_info
            group_info = message.message_info.group_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
            asyncio.create_task(
                emit_message_ingested(
                    session_id=self.session_id,
                    speaker_name=speaker_name,
                    content=self._build_monitor_message_content(message),
                    message_id=message.message_id,
                    timestamp=message.timestamp.timestamp(),
                    received_at=message.received_at.timestamp() if message.received_at is not None else None,
                    platform=message.platform,
                    user_id=user_info.user_id,
                    group_id=group_info.group_id if group_info else "",
                    reply_to=self._build_monitor_reply_preview(message),
                    media=self._build_monitor_message_media(message),
                )
            )
        except RuntimeError as exc:
            logger.debug(f"{self.log_prefix} 广播收到消息到监控面板失败: {exc}")

    async def register_message(self, message: SessionMessage) -> None:
        """缓存一条新消息并唤醒主循环。"""
        await focus_mode_manager.reconcile(self)
        async with self._message_ingest_lock:
            if not self._running:
                raise RuntimeError(f"聊天运行时已停止，不能接收入站消息：{self.session_id}")
            await self._register_message(message)

    async def _register_message(self, message: SessionMessage) -> None:
        """按聊天串行登记，避免媒体构建期间同一条消息重复进入共享上下文。"""
        if self._focus_group is not None and self._focus_group.has_message(self.session_id, message.message_id):
            return
        if self._running:
            self._ensure_background_tasks_running()
        if message.received_at is None:
            raise ValueError("入站消息缺少主程序记录的 received_at")
        self._quote_counter.record(message)
        received_at = message.received_at.timestamp()
        # 媒体预处理可能使较早收到的消息较晚入队，不能回退静默窗口。
        self._last_message_received_at = max(self._last_message_received_at, received_at)
        self._update_message_trigger_state(message)
        self._observe_message_debounce(message, received_at)
        self.message_cache.append(message)
        self._emit_monitor_message_ingested(message)
        self._prune_processed_message_cache()
        if self._focus_group is not None:
            history_message = await self._reasoning_engine._build_history_message(message)
            if history_message is not None:
                self._chat_history.append(history_message)
            await self._focus_group.register_message(self, message, history_message)
            return
        self._request_planner_interrupt_for_message(message)
        if self._running:
            self._schedule_message_turn()

    def _is_focus_mode_active_for_current_chat(self) -> bool:
        return focus_mode_manager.is_enabled_for_session(
            self.session_id, is_group_chat=self.chat_stream.is_group_session
        )

    def _request_planner_interrupt_for_message(self, message: SessionMessage) -> None:
        """快速反应模式下，用新消息打断运行中的 Planner。"""
        if not global_config.chat.reply_timing.planner_message_steering or self._agent_state != self._STATE_RUNNING:
            return

        request_result = self._planner_interrupt.request()
        if request_result == "idle":
            return
        if request_result == "duplicate":
            logger.info(
                f"{self.log_prefix} 收到新消息，但当前请求已发起过一次规划器打断，"
                f"本次不重复打断; 消息编号={message.message_id}"
            )
            return

        logger.info(
            f"{self.log_prefix} 收到新消息，发起规划器打断; "
            f"消息编号={message.message_id} 缓存条数={len(self.message_cache)} "
            f"时间戳={time.time():.3f}"
        )

    def _get_effective_reply_frequency(self) -> float:
        """返回当前会话生效的回复频率。"""
        if self._is_focus_mode_active_for_current_chat():
            return 1.0

        base_talk_value = self._get_base_reply_frequency()
        if base_talk_value <= 0 or self._talk_frequency_adjust <= 0:
            return 0.0

        talk_value = float(
            ChatConfigUtils.get_talk_value(
                self.session_id,
                is_group_chat=self.chat_stream.is_group_session,
            )
        )
        if talk_value <= 0:
            return 0.0
        return max(0.0, talk_value * self._talk_frequency_adjust)

    @staticmethod
    def _format_reply_frequency_for_display(frequency: float) -> str:
        """将回复频率格式化为日志中易读的数值。"""
        normalized_frequency = max(0.0, float(frequency))
        return f"{normalized_frequency:.3f}（{normalized_frequency * 100:.1f}%）"

    def _get_base_reply_frequency(self) -> float:
        """返回当前会话类型对应的基础回复频率。"""
        if self.chat_stream.is_group_session:
            return float(global_config.chat.reply_timing.talk_value)
        return float(global_config.chat.reply_timing.private_talk_value)

    def _is_reply_frequency_silent(self) -> bool:
        """判断当前会话是否处于回复频率为 0 的静默接收模式。"""
        return self._get_effective_reply_frequency() <= 0.0


    def _get_pending_message_count(self) -> int:
        """统计当前尚未进入内部循环的新消息数量。"""
        pending_messages = self.message_cache[self._last_processed_index :]
        return len(pending_messages)

    def find_source_message_by_id(self, message_id: str) -> Optional[SessionMessage]:
        """从 Maisaka 历史中查找指定消息编号对应的原始消息。"""
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            return None

        for history_message in reversed(self._chat_history):
            if isinstance(history_message, SessionBackedMessage) and history_message.message_id == normalized_message_id:
                return history_message.original_message

        return None

    def get_reply_quote_context(self, target: SessionMessage) -> ReplyQuoteContext | None:
        """读取发送前快照；合法历史目标无序号时由规则直接引用。"""
        return self._quote_counter.snapshot(target)

    def expand_message_id_aliases(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """把工具参数中模型可见的消息 ID 别名换回原始消息 ID；别名冲突时取最新的消息。"""
        alias_map = build_alias_map(
            history_message.message_id for history_message in reversed(self._chat_history)
            if isinstance(history_message, ContentContextMessage)
        )
        return expand_message_id_aliases(arguments, alias_map)

    def _prune_processed_message_cache(self) -> None:
        """裁剪 runtime 已经消费过的旧消息。"""
        excess_count = len(self.message_cache) - MAX_RETAINED_MESSAGE_CACHE_SIZE
        if excess_count <= 0:
            return

        removable_count = min(
            excess_count,
            self._last_processed_index,
        )
        if removable_count <= 0:
            return

        del self.message_cache[:removable_count]
        self._last_processed_index = max(0, self._last_processed_index - removable_count)
        logger.debug(
            f"{self.log_prefix} 已清理 Maisaka 旧消息缓存: "
            f"清理数量={removable_count} 保留数量={len(self.message_cache)}"
        )

    def _cancel_deferred_message_turn_task(self) -> None:
        """取消延迟重新检查消息触发的任务。"""
        if self._deferred_message_turn_task is None:
            return
        self._deferred_message_turn_task.cancel()
        self._deferred_message_turn_task = None

    def _enqueue_message_turn(self) -> None:
        """投递一次消息触发的内部循环。"""
        self._cancel_deferred_message_turn_task()
        self._message_turn_scheduled = True
        self._internal_turn_queue.put_nowait("message")

    def _defer_message_turn_check(self, delay_seconds: float) -> None:
        """延迟后重新检查是否应投递消息触发。"""

        self._cancel_deferred_message_turn_task()
        self._deferred_message_turn_task = asyncio.create_task(self._schedule_deferred_message_turn(delay_seconds))

    def _mark_message_turn_unscheduled(self) -> None:
        """释放当前消息触发占位，允许后续重新调度。"""

        self._message_turn_scheduled = False

    async def _schedule_deferred_message_turn(self, delay_seconds: float) -> None:
        """延迟结束后再次检查是否应触发循环。"""
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            if not self._running:
                return
            self._schedule_message_turn()
        except asyncio.CancelledError:
            return
        finally:
            self._deferred_message_turn_task = None

    def _update_message_trigger_state(self, message: SessionMessage) -> None:
        """补齐消息中的 @/提及 标记，并在命中时启用强制触发。"""

        detected_mentioned, detected_at, reply_probability_boost = is_mentioned_bot_in_message(message)
        if detected_at:
            message.is_at = True
        if detected_mentioned:
            message.is_mentioned = True

        should_force_reply = (
            reply_probability_boost >= 1.0
            or (message.is_at and global_config.chat.reply_timing.inevitable_at_reply)
            or (message.is_mentioned and global_config.chat.reply_timing.mentioned_bot_reply)
        )
        if not should_force_reply or (not message.is_at and not message.is_mentioned):
            return

        self._arm_forced_turn(
            message,
            is_at=message.is_at,
            is_mentioned=message.is_mentioned,
        )

    def _arm_forced_turn(
        self,
        message: SessionMessage,
        *,
        is_at: bool,
        is_mentioned: bool,
    ) -> None:
        """在检测到 @ 或提及时，强制下一轮思考绕过普通频率阈值。"""

        trigger_reason = "@消息" if is_at else "提及消息" if is_mentioned else "触发消息"
        was_armed = self._arm_forced_turn_state(message_id=message.message_id, reason=trigger_reason)
        self._idle_backoff.reset()

        if was_armed:
            logger.info(
                f"{self.log_prefix} 检测到新的{trigger_reason}，刷新强制 continue 状态；消息编号={message.message_id}"
            )
            return

        logger.info(
            f"{self.log_prefix} 检测到{trigger_reason}，下一轮 Planner 将强制触发；消息编号={message.message_id}"
        )

    def _arm_forced_turn_state(self, *, message_id: str, reason: str) -> bool:
        """启用一次强制触发，并返回启用前是否已存在强制触发。"""

        was_armed = self._forced_turn_enabled
        self._forced_turn_enabled = True
        self._forced_turn_message_id = str(message_id or "")
        self._forced_turn_reason = str(reason or "")
        return was_armed

    def _consume_forced_turn_reason(self) -> str | None:
        """消费一次性强制触发状态，并返回原因描述。"""

        if not self._forced_turn_enabled:
            return None

        trigger_reason = self._forced_turn_reason or "@/提及消息"
        trigger_message_id = self._forced_turn_message_id or "unknown"
        reason = f"检测到新的{trigger_reason}（消息编号={trigger_message_id}），本轮直接进入 Planner。"
        logger.info(
            f"{self.log_prefix} 已结束本次强制触发状态；触发原因={trigger_reason} 触发消息编号={trigger_message_id}"
        )
        self._clear_forced_turn_state()
        return reason

    def _clear_forced_turn_state(self) -> None:
        """清理一次性强制触发状态，不产生伪门控提示。"""
        self._forced_turn_enabled = False
        self._forced_turn_message_id = ""
        self._forced_turn_reason = ""

    def _has_forced_turn_trigger(self) -> bool:
        """判断是否已有 @/提及必回触发，需绕过普通频率阈值。"""

        return self._forced_turn_enabled

    def _start_planner_continuation(self) -> None:
        """标记已进入连续 Planner 状态。"""

        self._planner_continuation_active = True

    def _end_planner_continuation(self) -> None:
        """结束连续 Planner 状态。"""

        self._planner_continuation_active = False

    def _is_planner_continuation_active(self) -> bool:
        """判断当前是否保持连续 Planner 状态。"""

        return self._planner_continuation_active

    def _ensure_background_tasks_running(self) -> None:
        """确保后台任务仍在运行，若崩溃则自动拉起。"""
        if not self._running:
            return
        if self._focus_group is not None:
            self._ensure_expression_vector_history_backfill_running()
            return

        if self._internal_loop_task is None or self._internal_loop_task.done():
            is_restart = self._internal_loop_task is not None
            if self._internal_loop_task is not None and not self._internal_loop_task.cancelled():
                try:
                    exc = self._internal_loop_task.exception()
                except Exception:
                    exc = None
                if exc is not None:
                    logger.error(f"{self.log_prefix} 内部循环任务异常退出: {exc}")
            self._internal_loop_task = asyncio.create_task(self._reasoning_engine.run_loop())
            if is_restart:
                logger.warning(f"{self.log_prefix} 已重新拉起 Maisaka 内部循环任务")
            else:
                logger.debug(f"{self.log_prefix} 已启动 Maisaka 内部循环任务")

        self._ensure_expression_vector_history_backfill_running()

    @staticmethod
    def _should_run_expression_vector_history_backfill() -> bool:
        """判断是否需要启动表达向量历史补建任务。"""

        return global_config.expression.use_vector_expression

    def _ensure_expression_vector_history_backfill_running(self) -> None:
        """在向量表达模式下拉起全局历史表达向量补建任务。"""

        if not self._should_run_expression_vector_history_backfill():
            return

        expression_config = global_config.expression
        expression_vector_index.ensure_history_backfill_task(
            index_path=expression_config.expression_vector_index_path,
        )

    def _register_tool_providers(self) -> None:
        """注册 Maisaka 运行时默认启用的工具 Provider。"""

        self._tool_registry.register_provider(
            MaisakaBuiltinToolProvider(self._reasoning_engine.build_builtin_tool_handlers())
        )
        self._tool_registry.register_provider(
            WebSearchToolProvider(
                WebSearchService(get_mcp_service()),
                lambda: resolve_web_search_config(model_config.web, model_config.model_task_config.web_search),
            )
        )
        self._tool_registry.register_provider(PluginToolProvider())
        self._tool_registry.register_provider(AgentToolProvider())
        self._tool_registry.register_provider(MCPToolProvider(get_mcp_service()))
        self._chat_loop_service.set_tool_registry(self._tool_registry)

    async def run_sub_agent(
        self,
        *,
        context_message_limit: int,
        drop_head_context_count: int = 0,
        system_prompt: str,
        request_kind: str = "sub_agent",
        extra_messages: Optional[Sequence[LLMContextMessage]] = None,
        interrupt_flag: asyncio.Event | None = None,
        model_task_name: str = "planner",
        response_format: RespFormat | None = None,
        tool_definitions: Optional[Sequence[ToolDefinitionInput]] = None,
        include_parent_context: bool = True,
    ) -> ChatResponse:
        """运行一个可选继承父上下文的临时子代理，并在完成后立即销毁。"""

        sub_agent_history: list[LLMContextMessage] = []
        if include_parent_context:
            selected_history, _ = MaisakaChatLoopService.select_llm_context_messages(
                self._chat_history,
                request_kind=request_kind,
                max_context_size=context_message_limit,
                is_group_chat=self.chat_stream.is_group_session,
            )
            sub_agent_history = self._drop_head_context_messages(
                selected_history,
                drop_head_context_count,
                trim_threshold_context_count=context_message_limit,
            )
        if extra_messages:
            sub_agent_history.extend(list(extra_messages))

        sub_agent = MaisakaChatLoopService(
            chat_system_prompt=system_prompt,
            session_id=self.session_id,
            is_group_chat=self.chat_stream.is_group_session,
            model_task_name=model_task_name,
        )
        sub_agent.set_interrupt_flag(interrupt_flag)
        return await sub_agent.chat_loop_step(
            sub_agent_history,
            request_kind=request_kind,
            response_format=response_format,
            tool_definitions=[] if tool_definitions is None else tool_definitions,
            max_context_size=context_message_limit,
        )

    @staticmethod
    def _drop_head_context_messages(
        chat_history: Sequence[LLMContextMessage],
        drop_context_count: int,
        *,
        trim_threshold_context_count: int | None = None,
    ) -> list[LLMContextMessage]:
        """从已选上下文头部丢弃指定数量的普通上下文消息。"""

        if drop_context_count <= 0:
            return list(chat_history)

        context_message_count = sum(1 for message in chat_history if message.count_in_context)
        if trim_threshold_context_count is not None and context_message_count <= trim_threshold_context_count:
            return list(chat_history)

        if context_message_count <= drop_context_count:
            return list(chat_history)

        first_kept_index = 0
        dropped_context_count = 0
        while first_kept_index < len(chat_history) and dropped_context_count < drop_context_count:
            message = chat_history[first_kept_index]
            if message.count_in_context:
                dropped_context_count += 1
            first_kept_index += 1

        trimmed_history = list(chat_history[first_kept_index:])
        trimmed_history, _ = drop_leading_orphan_tool_results(trimmed_history)
        return trimmed_history


    def set_current_action_tool_names(self, tool_names: Sequence[str]) -> None:
        """记录当前 Action Loop 已实际暴露给 planner 的工具名集合。"""

        self._current_action_tool_names = {tool_name for tool_name in tool_names if str(tool_name).strip()}

    def is_action_tool_currently_available(self, tool_name: str) -> bool:
        """判断指定工具在当前 Action Loop 轮次中是否真实可用。"""

        normalized_name = str(tool_name).strip()
        return bool(normalized_name) and normalized_name in self._current_action_tool_names

    def update_deferred_tool_specs(self, deferred_tool_specs: Sequence[ToolSpec]) -> None:
        """刷新当前会话的 deferred tools 池，并清理失效的已发现工具。"""

        next_specs_by_name: dict[str, ToolSpec] = {}
        for tool_spec in deferred_tool_specs:
            normalized_name = tool_spec.name.strip()
            if not normalized_name:
                continue
            next_specs_by_name[normalized_name] = tool_spec

        self.deferred_tool_specs_by_name = next_specs_by_name
        self.discovered_tool_names.intersection_update(next_specs_by_name.keys())

    def sync_discovered_deferred_tools_with_context(
        self,
        selected_history: Sequence[LLMContextMessage],
    ) -> None:
        """根据当前实际上下文中的 tool_search 调用链同步已发现 deferred tools。

        已激活 deferred tool 必须能在本轮上下文中找到对应的 tool_search call 与 result。
        当这条调用链被上下文窗口裁掉后，工具会重新折回 deferred tools 提示中。
        """

        visible_tool_names = self._extract_visible_tool_search_discoveries(selected_history)
        self.discovered_tool_names = visible_tool_names.intersection(self.deferred_tool_specs_by_name.keys())

    def _extract_visible_tool_search_discoveries(
        self,
        selected_history: Sequence[LLMContextMessage],
    ) -> set[str]:
        """提取当前上下文中仍有完整 tool_search call/result 支撑的工具名。"""

        tool_search_call_ids = {
            tool_call.call_id
            for message in selected_history
            if isinstance(message, ModelOutputContextMessage)
            for tool_call in message.tool_calls
            if tool_call.func_name == "tool_search" and tool_call.call_id
        }
        discovered_tool_names: set[str] = set()
        for message in selected_history:
            if not isinstance(message, ToolResultMessage):
                continue
            if message.tool_name != "tool_search" or message.tool_call_id not in tool_search_call_ids:
                continue
            if not message.success:
                continue
            discovered_tool_names.update(self._parse_tool_search_result_tool_names(message.content))
        return discovered_tool_names

    def _parse_tool_search_result_tool_names(self, content: str) -> set[str]:
        """从 tool_search 的历史结果文本中解析有效 deferred tool 名称。"""

        discovered_tool_names: set[str] = set()
        try:
            structured_content = json.loads(content)
        except (TypeError, ValueError):
            structured_content = None

        if isinstance(structured_content, dict):
            raw_tool_names = structured_content.get("matched_tool_names")
            if isinstance(raw_tool_names, list):
                for raw_tool_name in raw_tool_names:
                    normalized_name = str(raw_tool_name).strip()
                    if normalized_name in self.deferred_tool_specs_by_name:
                        discovered_tool_names.add(normalized_name)

        for raw_line in content.splitlines():
            normalized_line = raw_line.strip()
            if not normalized_line.startswith("- "):
                continue
            normalized_name = normalized_line[2:].split("（", 1)[0].strip()
            if normalized_name in self.deferred_tool_specs_by_name:
                discovered_tool_names.add(normalized_name)

        return discovered_tool_names

    def get_discovered_deferred_tool_specs(self) -> list[ToolSpec]:
        """返回当前会话中已发现、且仍然有效的 deferred tools。"""

        return [
            tool_spec
            for tool_name, tool_spec in self.deferred_tool_specs_by_name.items()
            if tool_name in self.discovered_tool_names
        ]

    def build_deferred_tools_reminder(self) -> str:
        """构造供 planner 使用的 deferred tools 提示消息。"""

        undiscovered_tool_specs = [
            tool_spec
            for tool_name, tool_spec in self.deferred_tool_specs_by_name.items()
            if tool_name not in self.discovered_tool_names
        ]
        if not undiscovered_tool_specs:
            return ""

        tool_lines: list[str] = []
        for index, tool_spec in enumerate(undiscovered_tool_specs, start=1):
            tool_name = tool_spec.name.strip()
            tool_description = tool_spec.description.strip()
            if tool_description:
                tool_lines.append(f"{index}. {tool_name}: {tool_description}")
            else:
                tool_lines.append(f"{index}. {tool_name}")

        reminder_lines = [
            "<system-reminder>",
            "以下工具当前未直接暴露给你，但可以通过 tool_search 工具发现并在后续轮次中使用：",
            *tool_lines,
            "",
            "如需其中某个工具，请先调用 tool_search。tool_search 只负责发现工具，不直接执行。",
            "</system-reminder>",
        ]
        return "\n".join(reminder_lines)

    def search_deferred_tool_specs(
        self,
        query: str,
        *,
        limit: int,
    ) -> list[ToolSpec]:
        """按名称或简要描述搜索 deferred tools。"""

        normalized_query = " ".join(query.lower().split()).strip()
        if not normalized_query:
            return []

        scored_matches: list[tuple[int, str, ToolSpec]] = []
        query_terms = [term for term in normalized_query.replace("_", " ").replace("-", " ").split() if term]
        for tool_name, tool_spec in self.deferred_tool_specs_by_name.items():
            lower_name = tool_name.lower()
            lower_description = tool_spec.description.lower()
            score = 0

            if normalized_query == lower_name:
                score += 1000
            if lower_name.startswith(normalized_query):
                score += 300
            if normalized_query in lower_name:
                score += 200
            if normalized_query in lower_description:
                score += 100

            for query_term in query_terms:
                if query_term in lower_name:
                    score += 25
                if query_term in lower_description:
                    score += 10

            if score <= 0:
                continue

            scored_matches.append((score, tool_name, tool_spec))

        scored_matches.sort(key=lambda item: (-item[0], item[1]))
        return [tool_spec for _, _, tool_spec in scored_matches[: max(1, limit)]]

    def discover_deferred_tools(self, tool_names: Sequence[str]) -> list[str]:
        """将指定 deferred tools 标记为已发现，并返回本次新发现的工具名。"""

        newly_discovered_tool_names: list[str] = []
        for raw_tool_name in tool_names:
            normalized_name = str(raw_tool_name).strip()
            if not normalized_name or normalized_name not in self.deferred_tool_specs_by_name:
                continue
            if normalized_name in self.discovered_tool_names:
                continue
            self.discovered_tool_names.add(normalized_name)
            newly_discovered_tool_names.append(normalized_name)
        return newly_discovered_tool_names

    def record_planner_reply(self) -> None:
        """记录 Planner 成功调用了一次 reply，一次调用拆成多条消息发送也只计一次。"""

        self._message_turn_scheduler.record_reply()

    def _has_pending_messages(self) -> bool:
        return self._last_processed_index < len(self.message_cache)

    def _schedule_message_turn(self) -> None:
        """为当前待处理消息安排一次内部 turn。"""
        if self._focus_group is not None:
            return
        self._message_turn_scheduler.schedule_message_turn()

    def _collect_pending_messages(self) -> list[SessionMessage]:
        """从消息缓存中收集一批尚未处理的消息。"""
        start_index = self._last_processed_index
        pending_messages = self.message_cache[start_index:]
        if not pending_messages:
            return []

        self._last_processed_index = len(self.message_cache)
        self._fixed_message_debouncer.consume()
        # logger.info(
        # f"{self.log_prefix} 已从消息缓存区[{start_index}:{self._last_processed_index}] "
        # f"收集 {len(pending_messages)} 条新消息"
        # )
        return pending_messages

    def _get_message_quiet_period_remaining_seconds(self) -> float:
        """返回本批消息距离防抖截止还需等待的秒数；关闭防抖或批次已到期时为 0。"""
        if not self._is_message_debounce_enabled():
            return 0.0
        deadline = self._fixed_message_debouncer.deadline
        return 0.0 if deadline is None else max(0.0, deadline - time.monotonic())

    async def _wait_for_message_quiet_period(self) -> None:
        """等待消息批次允许判断；新消息会唤醒等待并重算，兼容截止时间提前和延后；停止时可取消。"""
        debouncer = self._fixed_message_debouncer
        while self._running:
            remaining = self._get_message_quiet_period_remaining_seconds()
            if remaining <= 0:
                break
            debouncer.changed.clear()
            try:
                # 定期重算，使关闭配置也能结束本轮等待，无需再来一条消息。
                await asyncio.wait_for(debouncer.changed.wait(), timeout=min(remaining, 0.5))
            except TimeoutError:
                pass

    def _is_message_debounce_enabled(self) -> bool:
        return global_config.chat.reply_timing.message_debounce_mode != "off"

    def _observe_message_debounce(self, message: SessionMessage, received_at: float) -> None:
        """在触发调度之前更新截止时间；开关热重载时清除旧批次。"""
        mode = global_config.chat.reply_timing.message_debounce_mode
        enabled = mode != "off"
        if enabled != self._message_debounce_was_enabled:
            self._fixed_message_debouncer.consume()
            self._message_debounce_was_enabled = enabled
        if mode == "off":
            return
        if message.is_notify or message.is_command or is_bot_self(message.platform, message.message_info.user_info.user_id):
            return
        kind, length = get_message_shape(message)
        reason = self._fixed_message_debouncer.observe(
            author=message.message_info.user_info.user_id,
            is_bare_image=kind == 1 and length == 0,
            is_pure_emoji=kind == 2 and length == 0,
            mode=mode,
            at_bot=message.is_at or message.is_mentioned,
            received_at=received_at,
            wall_time=time.time(),
            now=time.monotonic(),
        )
        deadline = self._fixed_message_debouncer.deadline
        if deadline is not None:
            mode_label = {"fast": "快速", "natural": "自然", "steady": "沉稳"}[mode]
            logger.info(
                f"{self.log_prefix} 消息防抖：档位={mode_label}，规则={reason}，"
                f"剩余等待={max(0.0, deadline - time.monotonic()):.3f} 秒，"
                f"待处理={self._get_pending_message_count() + 1} 条，消息编号={message.message_id}"
            )

    def _enter_stop_state(self) -> None:
        """切换到停止状态。"""
        self._agent_state = self._STATE_STOP
        self._reset_consecutive_wait_count("stop_state")
        self._clear_wait_state()

    def _enter_running_state(self) -> None:
        """切换到运行状态，不清理 wait 工具挂起信息。"""

        self._agent_state = self._STATE_RUNNING

    def _clear_wait_state(self) -> None:
        """清理 wait 工具挂起状态与超时任务。"""

        self._pending_wait_tool_call_id = None
        self._pending_wait_logical_turn_id = None
        self._pending_wait_started_at = None
        self._pending_wait_seconds = None
        self._cancel_wait_timeout_task()

    def _reset_consecutive_wait_count(self, reason: str) -> None:
        """重置连续 wait 计数。"""

        if self._consecutive_wait_count <= 0:
            return
        logger.debug(f"{self.log_prefix} 连续 wait 计数已重置: 原计数={self._consecutive_wait_count} 原因={reason}")
        self._consecutive_wait_count = 0

    def _try_enter_wait_state(
        self,
        seconds: Optional[float] = None,
        tool_call_id: Optional[str] = None,
        logical_turn_id: Optional[str] = None,
    ) -> tuple[bool, int, int]:
        """尝试进入 wait 状态，并返回是否成功、当前连续次数和上限。"""

        max_count = max(1, int(global_config.chat.reply_timing.max_consecutive_wait_count))
        if self._consecutive_wait_count >= max_count:
            return False, self._consecutive_wait_count, max_count

        self._consecutive_wait_count += 1
        self._enter_wait_state(
            seconds=seconds,
            tool_call_id=tool_call_id,
            logical_turn_id=logical_turn_id,
        )
        return True, self._consecutive_wait_count, max_count

    def _resume_from_wait_for_proactive_trigger(self) -> None:
        """主动触发到来时从 wait 状态恢复运行。"""

        if self._agent_state != self._STATE_WAIT:
            return

        self._enter_running_state()

    def _enter_wait_state(
        self,
        seconds: Optional[float] = None,
        tool_call_id: Optional[str] = None,
        logical_turn_id: Optional[str] = None,
    ) -> None:
        """切换到等待状态。"""

        if not tool_call_id or not logical_turn_id:
            raise ValueError("进入 wait 状态必须绑定非空 tool_call_id 和 logical_turn_id")
        self._agent_state = self._STATE_WAIT
        self._pending_wait_tool_call_id = tool_call_id
        self._pending_wait_logical_turn_id = logical_turn_id
        self._pending_wait_started_at = time.time()
        self._pending_wait_seconds = seconds
        self._mark_message_turn_unscheduled()
        self._cancel_deferred_message_turn_task()
        self._cancel_wait_timeout_task()
        if seconds is not None:
            self._wait_timeout_task = asyncio.create_task(
                self._schedule_wait_timeout(seconds=seconds, tool_call_id=tool_call_id)
            )
        if global_config.chat.reply_timing.planner_message_steering and self._has_pending_messages():
            # 工具执行期间收到的消息也能唤醒刚进入的 wait，避免漏掉已有新输入。
            self._schedule_message_turn()

    def _cancel_wait_timeout_task(self) -> None:
        """取消当前 wait 对应的超时任务。"""
        if self._wait_timeout_task is None:
            return
        self._wait_timeout_task.cancel()
        self._wait_timeout_task = None

    async def _schedule_wait_timeout(self, seconds: float, tool_call_id: Optional[str]) -> None:
        """在 wait 到期后向内部循环投递 timeout 触发。"""
        try:
            if seconds > 0:
                await asyncio.sleep(seconds)
            if not self._running:
                return
            if self._agent_state != self._STATE_WAIT:
                return
            if self._pending_wait_tool_call_id != tool_call_id:
                return

            logger.debug(f"{self.log_prefix} Maisaka 等待已超时")
            self._enter_running_state()
            await self._internal_turn_queue.put("timeout")
        except asyncio.CancelledError:
            return
        finally:
            if self._wait_timeout_task is not None and self._pending_wait_tool_call_id == tool_call_id:
                self._wait_timeout_task = None

    def _consume_pending_wait_state(self) -> tuple[str, str, float, Optional[float]]:
        """消费当前 wait 挂起状态，供 wait 完成消息回填。"""

        tool_call_id = self._pending_wait_tool_call_id
        logical_turn_id = self._pending_wait_logical_turn_id
        if not tool_call_id or not logical_turn_id:
            raise RuntimeError("不存在可补齐的 wait 工具调用")
        started_at = self._pending_wait_started_at
        requested_seconds = self._pending_wait_seconds
        elapsed_seconds = max(0.0, time.time() - started_at) if started_at is not None else 0.0
        self._cancel_wait_timeout_task()
        self._pending_wait_tool_call_id = None
        self._pending_wait_logical_turn_id = None
        self._pending_wait_started_at = None
        self._pending_wait_seconds = None
        return tool_call_id, logical_turn_id, elapsed_seconds, requested_seconds

    def _has_pending_wait_tool_call(self) -> bool:
        """返回当前是否存在等待完成后需要回填的 wait 工具调用。"""

        return self._pending_wait_tool_call_id is not None

    def _get_pending_wait_tool_call_ids(self) -> set[str]:
        """返回历史清理期间允许暂时未闭合的 wait 调用 ID。"""

        if self._pending_wait_tool_call_id is None:
            return set()
        return {self._pending_wait_tool_call_id}

    async def _trigger_trimmed_history_learning(
        self,
        context_messages: Sequence[LLMContextMessage],
        *,
        on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        """提交对 Maisaka 裁切历史的后台学习任务。"""

        async def report_skip(reason: str) -> None:
            if on_progress is not None:
                await on_progress({"status": "skipped", "detail": reason, "learners": []})

        if not context_messages:
            await report_skip("没有可用于学习的裁切消息")
            return
        enable_expression_learning = self._enable_expression_learning
        enable_jargon_learning = self._enable_jargon_learning
        enable_high_frequency_learning = enable_expression_learning or enable_jargon_learning
        # 必须在检查后台任务/触发间隔之前收集，否则等待期间裁切的素材会永久漏学。
        for enabled, learner in (
            (enable_expression_learning, self._expression_learner),
            (enable_jargon_learning, self._jargon_learner),
        ):
            if enabled:
                learner.collect_context_messages(context_messages)
            else:
                learner.pending_messages.clear()
        if not enable_high_frequency_learning:
            await report_skip("表达与黑话学习未启用")
            return

        if self._trimmed_history_learning_task is not None and not self._trimmed_history_learning_task.done():
            if on_progress is not None:
                async def report_queued_result(progress: Dict[str, Any]) -> None:
                    # 后续批次复用后台任务；新卡片也要收到结果，不能永久停在“等待”。
                    await on_progress({
                        **progress,
                        "learners": [
                            {**item, "status": "skipped", "detail": "本轮与上一批合并学习，未单独统计高频词", "contents": []}
                            if item["name"] == "高频词学习" else item
                            for item in progress["learners"]
                        ],
                    })

                previous_progress = self._queued_trimmed_learning_progress
                self._queued_trimmed_learning_progress = report_queued_result
                if previous_progress is not None:
                    await previous_progress({
                        "status": "skipped", "detail": "素材已合并到后续裁切批次，结果请查看最新的学习卡片", "learners": [],
                    })
                await on_progress({
                    "status": "waiting",
                    "detail": "上一批学习仍在运行，已收集本轮素材；达到门槛后自动继续学习",
                    "learners": [
                        {
                            "name": name,
                            "status": "waiting" if enabled else "disabled",
                            "contents": [],
                            "detail": (
                                learner.pending_messages.waiting_detail(learner.min_messages_for_extraction)
                                if enabled else "学习未启用"
                            ),
                        }
                        for name, enabled, learner in (
                            ("表达学习", enable_expression_learning, self._expression_learner),
                            ("黑话学习", enable_jargon_learning, self._jargon_learner),
                        )
                    ] + [{
                        "name": "高频词学习", "status": "skipped", "contents": [],
                        "detail": "上一批学习仍在运行，本轮未执行高频词统计",
                    }],
                })
            return

        pending_context_count = len(context_messages)
        logger.info(
            f"{self.log_prefix} 提交裁切历史后台学习: "
            f"裁切上下文消息数量={pending_context_count} "
            f"是否启用表达学习={enable_expression_learning} "
            f"是否启用黑话学习={enable_jargon_learning} "
            f"是否启用高频词学习={enable_high_frequency_learning}"
        )

        self._queued_trimmed_learning_progress = None
        self._trimmed_history_learning_task = asyncio.create_task(
            self._run_trimmed_history_learning(
                list(context_messages),
                enable_expression_learning=enable_expression_learning,
                enable_jargon_learning=enable_jargon_learning,
                enable_high_frequency_learning=enable_high_frequency_learning,
                on_progress=on_progress,
            )
        )
        self._trimmed_history_learning_task.add_done_callback(self._handle_trimmed_history_learning_done)

    async def _run_trimmed_history_learning(
        self,
        context_messages: Sequence[LLMContextMessage],
        *,
        enable_expression_learning: bool,
        enable_jargon_learning: bool,
        enable_high_frequency_learning: bool,
        on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        """并行执行学习，按批次回传进度与实际处理内容。"""

        learners: List[Dict[str, Any]] = [
            {"name": name, "status": "running" if enabled else "disabled", "contents": [], "detail": ""}
            for name, enabled in (
                ("表达学习", enable_expression_learning),
                ("黑话学习", enable_jargon_learning),
                ("高频词学习", enable_high_frequency_learning),
            )
        ]

        async def publish(status: str) -> None:
            progress = {"status": status, "detail": "", "learners": [dict(item) for item in learners]}
            if on_progress is not None:
                # 每次发送完整快照，回放时同一张裁切卡片可恢复到最后状态。
                await on_progress(progress)
            if self._queued_trimmed_learning_progress is not None:
                await self._queued_trimmed_learning_progress(progress)

        learning_sources = {
            "表达学习": self._expression_learner,
            "黑话学习": self._jargon_learner,
        }

        async def run_learning(item: Dict[str, Any], delay: float) -> bool:
            contents: List[str] = []
            try:
                if item["name"] in learning_sources:
                    learner = learning_sources[item["name"]]
                    if len(learner.pending_messages) >= learner.min_messages_for_extraction and delay > 0:
                        item.update(status="waiting", detail=f"距离上次学习不足 {self._min_extraction_interval} 秒，等待 {delay:.1f} 秒后自动执行")
                        await publish("waiting")
                        await asyncio.sleep(delay)
                    enabled = self._enable_expression_learning if item["name"] == "表达学习" else self._enable_jargon_learning
                    if not enabled:
                        learner.pending_messages.clear()
                        item.update(status="disabled", detail="学习开关已关闭，已清空待学习素材")
                        return False
                    dropped_count = learner.pending_messages.dropped_count
                    item["status"] = "running"
                    if len(learner.pending_messages) >= learner.min_messages_for_extraction:
                        self._last_expression_extraction_time = time.time()
                    if item["name"] == "表达学习":
                        changed = await self._expression_learner.learn_from_context_messages([], learned_contents=contents)
                    else:
                        changed = await self._jargon_learner.learn_from_context_messages(
                            [], self._jargon_miner, learned_contents=contents
                        )
                    item.update(status=learner.learning_status, detail=learner.learning_detail)
                    if dropped_count and "淘汰" not in item["detail"]:
                        item["detail"] += f"；积压超过 100 条，已淘汰最旧的 {dropped_count} 条素材"
                else:
                    updated_count = await asyncio.to_thread(
                        update_high_frequency_terms_from_context_messages,
                        context_messages,
                        learned_contents=contents,
                    )
                    changed = updated_count > 0
                    item.update(
                        status="completed" if changed else "no_results",
                        detail=f"更新 {updated_count} 个词条" if changed else "用户正文中没有出现至少 2 次的有效词条",
                    )
            except asyncio.CancelledError:
                item.update(status="cancelled", detail="学习已取消")
                raise
            except Exception as exc:
                logger.exception(f"{self.log_prefix} 裁切历史{item['name']}异常")
                item.update(status="failed", detail=str(exc))
                changed = False
            finally:
                item["contents"] = contents
                await publish("running")
            return changed

        await publish("running")
        batch = [item for item in learners if item["status"] == "running"]
        while batch:
            delay = max(0.0, self._min_extraction_interval - (time.time() - self._last_expression_extraction_time))
            try:
                results = await asyncio.gather(*(run_learning(item, delay) for item in batch))
            except asyncio.CancelledError:
                await publish("cancelled")
                raise
            statuses = {item["status"] for item in learners}
            final_status = next(
                (status for status in ("failed", "waiting", "completed", "no_results") if status in statuses),
                "skipped",
            )
            await publish(final_status)
            if any(results):
                logger.info(f"{self.log_prefix} 裁切历史学习成功")
            # 执行期间的新素材和并发闸门暂拒的素材，满足数量条件后自动继续；
            # 无效配置、无法解析聊天流等明确跳过的情况不会自动重试。
            batch = [
                item for item in learners
                if item["name"] in learning_sources
                and item["status"] not in ("disabled", "failed")
                and not learning_sources[item["name"]].learning_retry_blocked
                and len(learning_sources[item["name"]].pending_messages)
                >= learning_sources[item["name"]].min_messages_for_extraction
            ]

    def _handle_trimmed_history_learning_done(self, task: asyncio.Task[None]) -> None:
        """清理裁切历史后台学习任务状态。"""

        if self._trimmed_history_learning_task is task:
            self._trimmed_history_learning_task = None
            self._queued_trimmed_learning_progress = None
        if task.cancelled():
            logger.debug(f"{self.log_prefix} 裁切历史后台学习已取消")
            return
        try:
            task.result()
        except Exception as exc:
            logger.error(f"{self.log_prefix} 裁切历史后台学习任务异常: {exc}")

    async def _cancel_trimmed_history_learning_task(self) -> None:
        """取消当前会话正在运行的裁切历史后台学习任务。"""

        task = self._trimmed_history_learning_task
        if task is None:
            return
        if task.done():
            self._trimmed_history_learning_task = None
            return

        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            logger.warning(f"{self.log_prefix} 等待裁切历史后台学习取消超时，继续停止运行时")
        except Exception as exc:
            logger.error(f"{self.log_prefix} 裁切历史后台学习取消时异常: {exc}")
        finally:
            if self._trimmed_history_learning_task is task:
                self._trimmed_history_learning_task = None

    async def _init_mcp(self) -> None:
        """初始化 MCP 工具并注册到统一工具层。"""
        mcp_service = get_mcp_service()
        await mcp_service.ensure_initialized(global_config.mcp)
        mcp_tool_specs = await mcp_service.list_tools()
        if not mcp_tool_specs:
            logger.debug(f"{self.log_prefix} 当前没有可供使用的 MCP 工具")
            return

        logger.info(f"{self.log_prefix} 已挂载 {len(mcp_tool_specs)} 个共享 MCP 工具")

    def _build_runtime_user_info(self) -> UserInfo:
        if self.chat_stream.user_id:
            user_nickname = "用户"
            if self.chat_stream.context and self.chat_stream.context.message:
                context_user_info = self.chat_stream.context.message.message_info.user_info
                user_nickname = context_user_info.user_nickname or context_user_info.user_id or user_nickname
            return UserInfo(
                user_id=self.chat_stream.user_id,
                user_nickname=user_nickname,
                user_cardname=None,
            )
        return UserInfo(user_id="maisaka_user", user_nickname="用户", user_cardname=None)

    def _build_group_info(self, message: Optional[SessionMessage] = None) -> Optional[GroupInfo]:
        group_info = None
        if message is not None:
            group_info = message.message_info.group_info
        elif self.chat_stream.context and self.chat_stream.context.message:
            group_info = self.chat_stream.context.message.message_info.group_info

        if group_info is None:
            return None

        return GroupInfo(group_id=group_info.group_id, group_name=group_info.group_name)
