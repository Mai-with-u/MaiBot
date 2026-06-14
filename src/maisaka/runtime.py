"""Maisaka 非 CLI 运行时。"""

from collections import deque
from datetime import datetime
from math import ceil
from typing import Any, Literal, Optional, Sequence

import asyncio
import json
import time

from src.chat.heart_flow.heartFC_utils import CycleDetail
from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import get_bot_account, is_bot_self, is_mentioned_bot_in_message
from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.common.logger import get_logger
from src.common.message_repository import find_messages
from src.common.utils.utils_config import BehaviorConfigUtils, ChatConfigUtils, ExpressionConfigUtils, JargonConfigUtils
from src.config.config import global_config
from src.core.tooling import ToolRegistry, ToolSpec
from src.learners.behavior_learner import BehaviorLearner
from src.learners.expression_learner import ExpressionLearner
from src.learners.jargon_miner import JargonMiner
from src.llm_models.payload_content.resp_format import RespFormat
from src.llm_models.payload_content.tool_option import ToolDefinitionInput
from src.mcp_module import MCPManager
from src.mcp_module.config import build_mcp_server_runtime_configs
from src.mcp_module.host_llm_bridge import MCPHostLLMBridge
from src.mcp_module.provider import MCPToolProvider
from src.plugin_runtime.tool_provider import PluginToolProvider
from src.services.message_word_frequency_service import update_high_frequency_terms_from_context_messages

from .chat_loop_service import ChatResponse, MaisakaChatLoopService
from src.maisaka.context.messages import (
    AssistantMessage,
    LLMContextMessage,
    ReferenceMessage,
    ReferenceMessageType,
    SessionBackedMessage,
    ToolResultMessage,
)
from src.maisaka.display.runtime_mixin import MaisakaRuntimeDisplayMixin
from src.maisaka.display.stage_status_board import remove_stage_status, update_stage_status
from src.maisaka.focus import MaisakaFocusRuntimeMixin, focus_mode_manager
from src.maisaka.context.history import drop_leading_orphan_tool_results
from src.maisaka.monitor.events import emit_message_sent, emit_session_start
from .reasoning_engine import MaisakaReasoningEngine
from src.maisaka.reply_effect import ReplyEffectTracker
from src.maisaka.reply_effect.image_utils import extract_visual_attachments_from_sequence
from src.maisaka.reply_effect.quote_utils import extract_quote_target_ids, message_id_from_context_message
from src.maisaka.builtin_tool.provider import MaisakaBuiltinToolProvider

logger = get_logger("maisaka_runtime")

MAX_INTERNAL_ROUNDS = 10
MAX_RETAINED_MESSAGE_CACHE_SIZE = 200
CONTEXT_RESTORE_FILL_RATIO = 0.5
EXTERNAL_MESSAGE_INTERVAL_SAMPLE_WINDOW_SECONDS = 1800.0
# 低于该间隔的相邻外部消息视为同一阵「连发」抖动，不计入平均消息间隔统计，
# 避免连发把平均间隔严重拉低、令空窗补偿过早触发。
# 注意：判定只看时间间隔、不区分发言者——同一人连续敲几条短消息是常见成因，
# 但跨发言者的快速对答同样会被过滤。
EXTERNAL_MESSAGE_BURST_INTERVAL_SECONDS = 5.0
# 空窗补偿所用平均消息间隔的下限：即便统计值偏小也不会低于该值，
# 限制「沉默时间」被折算成消息的速度，避免低活跃群聊里反复触发回复。
IDLE_COMPENSATION_MIN_AVERAGE_INTERVAL_SECONDS = 30.0


class MaisakaHeartFlowChatting(MaisakaFocusRuntimeMixin, MaisakaRuntimeDisplayMixin):
    """会话级别的 Maisaka 运行时。"""

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
        self.history_loop: list[CycleDetail] = []

        # Keep all original messages for batching and later learning.
        self.message_cache: list[SessionMessage] = []
        self._last_processed_index = 0
        self._internal_turn_queue: asyncio.Queue[Literal["message", "timeout", "proactive"]] = asyncio.Queue()
        self._proactive_anchor_message: Optional[SessionMessage] = None
        self._focus_cooldown_wakeup_scheduled = False
        self._focus_cooldown_timer_task: Optional[asyncio.Task[None]] = None

        self._mcp_manager: Optional[MCPManager] = None
        self._mcp_host_bridge: Optional[MCPHostLLMBridge] = None
        self._current_cycle_detail: Optional[CycleDetail] = None
        self._running = False
        self._cycle_counter = 0
        self._internal_loop_task: Optional[asyncio.Task] = None
        self._message_turn_scheduled = False
        self._deferred_message_turn_task: Optional[asyncio.Task[None]] = None
        self._message_debounce_seconds = 1.0
        self._message_debounce_required = False
        self._last_message_received_at = 0.0
        self._last_external_message_received_at: Optional[float] = None
        self._talk_frequency_adjust = 1.0
        self._recent_external_message_intervals: deque[tuple[float, float]] = deque()
        self._wait_timeout_task: Optional[asyncio.Task[None]] = None
        self._max_internal_rounds = MAX_INTERNAL_ROUNDS
        self._agent_state: Literal["running", "wait", "stop"] = self._STATE_STOP
        self._pending_wait_tool_call_id: Optional[str] = None
        self._force_next_timing_continue = False
        self._force_next_timing_message_id = ""
        self._force_next_timing_reason = ""
        self._planner_continuation_active = False
        self._planner_interrupt_flag: Optional[asyncio.Event] = None
        self._planner_interrupt_requested = False
        self._planner_interrupt_consecutive_count = 0
        self._consecutive_no_action_count = 0
        self._no_action_backoff_count = 0
        self._no_action_backoff_until = 0.0
        self._current_action_tool_names: set[str] = set()
        self.discovered_tool_names: set[str] = set()
        self.deferred_tool_specs_by_name: dict[str, ToolSpec] = {}

        self._min_extraction_interval = 30
        self._last_expression_extraction_time = 0.0
        self._trimmed_history_learning_task: Optional[asyncio.Task[None]] = None
        self._behavior_learner = BehaviorLearner(session_id)
        self._expression_learner = ExpressionLearner(session_id)
        self._jargon_miner = JargonMiner(session_id, session_name=session_name)

        self._reasoning_engine = MaisakaReasoningEngine(self)
        self._monitor_session_start_task: Optional[asyncio.Task[None]] = None
        self._tool_registry = ToolRegistry()
        self._reply_effect_tracker = ReplyEffectTracker(
            session_id=self.session_id,
            session_name=self.session_name,
            chat_stream=self.chat_stream,
            judge_runner=self._run_reply_effect_judge,
        )
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
    def _planner_interrupt_max_consecutive_count(self) -> int:
        """返回当前实时生效的 Planner 连续打断上限。"""

        return max(0, int(global_config.chat.planner_interrupt_max_consecutive_count))

    @property
    def _no_action_backoff_base_seconds(self) -> float:
        """返回当前实时生效的 no_action 退避基准秒数。"""

        return max(0.0, float(global_config.chat.no_action_backoff_base_seconds))

    @property
    def _no_action_backoff_cap_seconds(self) -> float:
        """返回当前实时生效的 no_action 退避上限秒数。"""

        return max(0.0, float(global_config.chat.no_action_backoff_cap_seconds))

    @property
    def _no_action_backoff_start_count(self) -> int:
        """返回连续第几次 no_action 后开始退避。"""

        return max(1, int(global_config.chat.no_action_backoff_start_count))

    @property
    def _no_action_backoff_bypass_pending_count(self) -> int:
        """返回退避期间直接绕过所需的待处理消息数。"""

        return max(0, int(global_config.chat.no_action_backoff_bypass_pending_count))

    @property
    def _enable_expression_use(self) -> bool:
        """返回当前会话实时生效的表达使用开关。"""

        enable_use, _ = ExpressionConfigUtils.get_expression_config_for_chat(self.session_id)
        return enable_use

    @property
    def _enable_expression_learning(self) -> bool:
        """返回当前会话实时生效的表达学习开关。"""

        _, enable_learning = ExpressionConfigUtils.get_expression_config_for_chat(self.session_id)
        return enable_learning

    @property
    def _enable_behavior_learning(self) -> bool:
        """返回当前会话实时生效的行为表现学习开关，默认开启。"""

        _, enable_learning = BehaviorConfigUtils.get_behavior_config_for_chat(self.session_id)
        return enable_learning

    @property
    def _enable_jargon_use(self) -> bool:
        """返回当前会话实时生效的黑话使用开关。"""

        enable_use, _ = JargonConfigUtils.get_jargon_config_for_chat(self.session_id)
        return enable_use

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

    @staticmethod
    def _is_reply_effect_tracking_enabled() -> bool:
        """判断是否启用回复效果评分追踪。"""

        return bool(global_config.debug.enable_reply_effect_tracking)

    def _update_stage_status(self, stage: str, detail: str = "", *, round_text: str = "") -> None:
        """更新当前会话的阶段状态。"""

        update_stage_status(
            session_id=self.session_id,
            session_name=self.session_name,
            stage=stage,
            detail=detail,
            round_text=round_text,
            agent_state=self._agent_state,
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
                filter_command=True,
            )
        except Exception as exc:
            logger.warning(f"{self.log_prefix} 恢复最近上下文失败: {exc}", exc_info=True)
            return

        restored_user_messages: list[SessionMessage] = []
        restored_history: list[LLMContextMessage] = []
        for message in recent_messages:
            if message.is_notify:
                continue

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
        self.message_cache = restored_user_messages[-MAX_RETAINED_MESSAGE_CACHE_SIZE:]
        self._last_processed_index = len(self.message_cache)
        logger.info(
            f"{self.log_prefix} 已恢复最近上下文: "
            f"历史消息={len(restored_history)} 用户消息缓存={len(self.message_cache)}"
        )

    def _get_context_restore_limit(self) -> int:
        """返回启动时最多回灌的真实消息数量。"""

        return max(1, ceil(self._max_context_size * CONTEXT_RESTORE_FILL_RATIO))

    @staticmethod
    def _resolve_restored_message_source_kind(message: SessionMessage) -> str:
        """根据发送者身份区分恢复消息来自用户还是麦麦自己。"""

        user_info = message.message_info.user_info
        bot_account = get_bot_account(message.platform)
        if bot_account and user_info.user_id == bot_account:
            return "guided_reply"
        return "user"

    async def stop(self) -> None:
        """停止运行时主循环。"""
        if not self._running:
            return

        self._running = False
        self._message_turn_scheduled = False
        self._message_debounce_required = False
        self._cancel_deferred_message_turn_task()
        self._cancel_focus_cooldown_timer_task()
        self._cancel_wait_timeout_task()
        await self._cancel_trimmed_history_learning_task()
        while not self._internal_turn_queue.empty():
            _ = self._internal_turn_queue.get_nowait()

        if self._internal_loop_task is not None:
            self._internal_loop_task.cancel()
            try:
                await self._internal_loop_task
            except asyncio.CancelledError:
                pass
            finally:
                self._internal_loop_task = None

        if self._is_reply_effect_tracking_enabled():
            await self._reply_effect_tracker.finalize_all("runtime_stop")
        focus_mode_manager.release_focus(self.session_id)
        await self._tool_registry.close()
        self._mcp_manager = None
        self._mcp_host_bridge = None
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
            from src.maisaka.context.messages import SessionBackedMessage
            from src.maisaka.context.history import build_prefixed_message_sequence, build_session_message_visible_text
            from src.maisaka.context.planner_messages import build_planner_prefix, extract_quote_ids_from_message_sequence

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
            self._chat_history.append(history_message)
            self._schedule_sent_image_recognition(message)
            self._emit_monitor_message_sent(
                message=message,
                speaker_name=speaker_name,
                source_kind=source_kind,
            )
            return True
        except Exception as exc:
            logger.warning(
                f"{self.log_prefix} 同步已发送消息到 Maisaka 历史失败: "
                f"message_id={message.message_id} error={exc}"
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
            SessionBackedMessage(
                raw_message=MessageSequence([TextComponent(visible_text)]),
                visible_text=visible_text,
                timestamp=datetime.now(),
                message_id=task_id,
                source_kind=f"plugin_proactive:{normalized_plugin_id}",
            )
        )
        self._proactive_anchor_message = self._build_proactive_anchor_message(task_id)
        self._force_next_timing_continue = True
        self._force_next_timing_message_id = task_id
        self._force_next_timing_reason = "插件主动聊天任务"
        if self._agent_state == self._STATE_WAIT:
            self._agent_state = self._STATE_RUNNING
            self._pending_wait_tool_call_id = None
            self._cancel_wait_timeout_task()
        self._internal_turn_queue.put_nowait("proactive")
        logger.info(f"{self.log_prefix} 已接收插件主动聊天任务: plugin_id={normalized_plugin_id} task_id={task_id}")
        return {
            "stream_id": self.session_id,
            "task_id": task_id,
            "queued": True,
        }

    def _build_proactive_anchor_message(self, task_id: str) -> SessionMessage:
        """构造仅供工具上下文使用的主动任务锚点消息，不写入消息数据库。"""

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
        message.raw_message = MessageSequence([TextComponent("插件主动聊天任务")])
        message.processed_plain_text = "插件主动聊天任务"
        return message

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
                    content=(message.processed_plain_text or "").strip(),
                    message_id=message.message_id,
                    timestamp=message.timestamp.timestamp(),
                    source_kind=source_kind,
                )
            )
        except RuntimeError as exc:
            logger.debug(f"{self.log_prefix} 广播已发送消息到监控面板失败: {exc}")

    async def register_message(self, message: SessionMessage) -> None:
        """缓存一条新消息并唤醒主循环。"""
        if self._running:
            self._ensure_background_tasks_running()
        received_at = time.time()
        self._last_message_received_at = received_at
        self._record_external_message_interval(message, received_at)
        self._update_message_trigger_state(message)
        self.message_cache.append(message)
        self._prune_processed_message_cache()
        if self._is_reply_effect_tracking_enabled():
            asyncio.create_task(self._reply_effect_tracker.observe_user_message(message))
        if focus_mode_manager.is_enabled_for_chat(is_group_chat=self.chat_stream.is_group_session):
            can_enter_focus = focus_mode_manager.try_enter_focus(
                self.session_id,
                is_group_chat=self.chat_stream.is_group_session,
            )
            if not can_enter_focus and message.is_at:
                self._maybe_schedule_focus_at_wakeup(trigger_session_id=self.session_id)
            else:
                self._maybe_schedule_focus_cooldown_wakeup(trigger_session_id=self.session_id)
            if not can_enter_focus:
                logger.debug(
                    f"{self.log_prefix} focus_mode 已启用且当前会话未获得关注槽，"
                    f"仅缓存消息不进入 Maisaka 决策；消息编号={message.message_id}"
                )
                return
        if self._agent_state == self._STATE_RUNNING:
            self._message_debounce_required = True
        if self._agent_state == self._STATE_RUNNING and self._planner_interrupt_flag is not None:
            planner_interrupt_max_count = self._planner_interrupt_max_consecutive_count
            if self._planner_interrupt_requested:
                logger.info(
                    f"{self.log_prefix} 收到新消息，但当前请求已发起过一次规划器打断，"
                    f"本次不重复打断; 消息编号={message.message_id} "
                    f"连续打断次数={self._planner_interrupt_consecutive_count}/"
                    f"{planner_interrupt_max_count}"
                )
            elif self._planner_interrupt_consecutive_count >= planner_interrupt_max_count:
                logger.info(
                    f"{self.log_prefix} 收到新消息，但已达到规划器连续打断上限，"
                    f"将等待当前请求自然完成; 消息编号={message.message_id} "
                    f"连续打断次数={self._planner_interrupt_consecutive_count}/"
                    f"{planner_interrupt_max_count}"
                )
            else:
                self._planner_interrupt_requested = True
                self._planner_interrupt_consecutive_count += 1
                logger.info(
                    f"{self.log_prefix} 收到新消息，发起规划器打断; "
                    f"消息编号={message.message_id} 缓存条数={len(self.message_cache)} "
                    f"时间戳={time.time():.3f} "
                    f"连续打断次数={self._planner_interrupt_consecutive_count}/"
                    f"{planner_interrupt_max_count}"
                )
                self._planner_interrupt_flag.set()
        if self._running:
            self._schedule_message_turn()

    def _get_effective_reply_frequency(self) -> float:
        """返回当前会话生效的回复频率。"""
        if focus_mode_manager.is_enabled_for_chat(is_group_chat=self.chat_stream.is_group_session):
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
            return float(global_config.chat.talk_value)
        return float(global_config.chat.private_talk_value)

    def _is_reply_frequency_silent(self) -> bool:
        """判断当前会话是否处于回复频率为 0 的静默接收模式。"""
        return self._get_effective_reply_frequency() <= 0.0

    async def track_reply_effect(
        self,
        *,
        tool_call_id: str,
        target_message: SessionMessage,
        set_quote: bool,
        reply_text: str,
        reply_segments: list[str],
        planner_reasoning: str,
        tool_context: Optional[dict[str, Any]] = None,
        send_results: Optional[list[dict[str, Any]]] = None,
        reply_metadata: Optional[dict[str, Any]] = None,
        replyer_context_messages: Optional[Sequence[LLMContextMessage]] = None,
    ) -> None:
        """登记一次已成功发送的 reply 工具回复，供后续用户反馈评分。"""

        if not self._is_reply_effect_tracking_enabled():
            return

        try:
            context_snapshot = self._build_reply_effect_context_snapshot(
                context_messages=replyer_context_messages,
                exclude_reply_segments=reply_segments if replyer_context_messages is None else None,
            )
            enriched_reply_metadata = dict(reply_metadata or {})
            enriched_reply_metadata["replyer_context_count"] = (
                len(replyer_context_messages) if replyer_context_messages is not None else len(self._chat_history)
            )
            enriched_reply_metadata["recorded_context_count"] = len(context_snapshot)
            await self._reply_effect_tracker.record_reply(
                tool_call_id=tool_call_id,
                target_message=target_message,
                set_quote=set_quote,
                reply_text=reply_text,
                reply_segments=reply_segments,
                planner_reasoning=planner_reasoning,
                tool_context=tool_context,
                send_results=send_results,
                reply_metadata=enriched_reply_metadata,
                context_snapshot=context_snapshot,
            )
        except Exception as exc:
            logger.warning(f"{self.log_prefix} 创建回复效果观察记录失败: {exc}")

    def _build_reply_effect_context_snapshot(
        self,
        *,
        context_messages: Optional[Sequence[LLMContextMessage]] = None,
        exclude_reply_segments: Optional[Sequence[str]] = None,
    ) -> list[dict[str, Any]]:
        """构建回复效果观察使用的上下文快照。

        优先记录 replyer 当次生成时实际收到的完整上下文列表；只有旧调用未传入时才回退到当前运行时历史。
        """

        source_messages = list(context_messages) if context_messages is not None else list(self._chat_history)
        snapshot: list[dict[str, Any]] = []
        excluded_segments = [segment.strip() for segment in (exclude_reply_segments or []) if segment.strip()]
        for message in source_messages:
            text = str(message.processed_plain_text or "").strip()
            if not text:
                continue
            if message.source == "guided_reply" and any(segment in text for segment in excluded_segments):
                continue
            snapshot.append(
                {
                    "message_id": message_id_from_context_message(message),
                    "source": message.source,
                    "role": message.role,
                    "timestamp": message.timestamp.isoformat(timespec="seconds"),
                    "text": text,
                    "quote_target_ids": extract_quote_target_ids(getattr(message, "raw_message", None)),
                    "attachments": extract_visual_attachments_from_sequence(getattr(message, "raw_message", None)),
                }
            )
        return snapshot

    def _get_message_trigger_threshold(self) -> int:
        """根据回复频率折算出触发一轮循环所需的消息数。"""
        effective_frequency = min(1.0, self._get_effective_reply_frequency())
        if effective_frequency <= 0:
            return 0
        return max(1, int(ceil(1.0 / effective_frequency)))

    def _get_pending_message_count(self) -> int:
        """统计当前尚未进入内部循环的新消息数量。"""
        pending_messages = self.message_cache[self._last_processed_index :]
        if not pending_messages:
            return 0

        seen_message_ids: set[str] = set()
        for message in pending_messages:
            seen_message_ids.add(message.message_id)
        return len(seen_message_ids)

    def _prune_recent_external_message_intervals(self, now: Optional[float] = None) -> None:
        """仅保留最近 30 分钟内的外部消息间隔记录。"""
        current_time = time.time() if now is None else now
        expire_before = current_time - EXTERNAL_MESSAGE_INTERVAL_SAMPLE_WINDOW_SECONDS
        while (
            self._recent_external_message_intervals
            and self._recent_external_message_intervals[0][0] < expire_before
        ):
            self._recent_external_message_intervals.popleft()

    def _get_recent_average_external_message_interval(self) -> Optional[float]:
        """获取最近 30 分钟外部消息的平均接收间隔。

        返回值会施加 ``IDLE_COMPENSATION_MIN_AVERAGE_INTERVAL_SECONDS`` 下限，
        避免统计值过小导致空窗补偿把沉默时间过快折算成消息、反复触发回复。

        见过外部消息但暂无可用间隔样本时（如启动后只收到一阵全被 burst 过滤的连发、
        或样本已因超出 30 分钟窗口而全部过期），回退到下限值作为保守估计，
        确保空窗补偿与延迟自唤醒不会因返回 None 而失效、令待处理消息在
        没有新消息到来时永久挂起；从未见过外部消息时仍返回 None。
        """
        self._prune_recent_external_message_intervals()
        if not self._recent_external_message_intervals:
            if self._last_external_message_received_at is None:
                return None
            return IDLE_COMPENSATION_MIN_AVERAGE_INTERVAL_SECONDS

        total_interval = sum(interval for _, interval in self._recent_external_message_intervals)
        average_interval = total_interval / len(self._recent_external_message_intervals)
        return max(average_interval, IDLE_COMPENSATION_MIN_AVERAGE_INTERVAL_SECONDS)

    def _record_external_message_interval(self, message: SessionMessage, received_at: float) -> None:
        """记录最近外部消息之间的接收间隔，用于低频触发补偿。"""

        user_info = message.message_info.user_info
        if is_bot_self(message.platform, user_info.user_id):
            return

        previous_received_at = self._last_external_message_received_at
        self._last_external_message_received_at = received_at
        if previous_received_at is None:
            return

        message_interval = max(0.0, received_at - previous_received_at)
        if message_interval < EXTERNAL_MESSAGE_BURST_INTERVAL_SECONDS:
            # 连发抖动：同一阵内的短间隔不代表真实发言节奏，跳过以免拉低平均间隔。
            return

        self._recent_external_message_intervals.append((received_at, message_interval))
        self._prune_recent_external_message_intervals(received_at)
        logger.debug(
            f"{self.log_prefix} 已记录外部消息接收间隔: {message_interval:.2f} 秒 "
            f"最近30分钟样本数={len(self._recent_external_message_intervals)}"
        )

    def find_source_message_by_id(self, message_id: str) -> Optional[SessionMessage]:
        """从 Maisaka 历史中查找指定消息编号对应的原始消息。"""
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            return None

        for history_message in reversed(self._chat_history):
            if str(getattr(history_message, "message_id", "") or "").strip() != normalized_message_id:
                continue

            original_message = getattr(history_message, "original_message", None)
            if original_message is None:
                continue
            return original_message

        return None

    def _has_chat_history_message(self, message_id: str) -> bool:
        """判断指定真实消息是否已经注入过 Maisaka 上下文。"""

        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            return False

        return any(
            str(getattr(history_message, "message_id", "") or "").strip() == normalized_message_id
            for history_message in self._chat_history
        )

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

    def _should_trigger_message_turn_by_idle_compensation(
        self,
        *,
        pending_count: int,
        trigger_threshold: int,
    ) -> bool:
        """在新消息不足阈值时，按空窗时间折算补齐触发条件。

        空窗折算量被限制在 ``trigger_threshold - 1`` 以内，确保至少要有一条真实新消息
        才可能触发，杜绝纯靠沉默累积反复唤醒回复。
        """
        # 双保险（与下方折算封顶互为冗余）：纯沉默（pending_count == 0）一律不触发。
        # 二者任一存在即可保证该不变量，重构时请勿因看似重复而删除其一。
        if pending_count < 1:
            return False

        average_message_interval = self._get_recent_average_external_message_interval()
        if average_message_interval is None or average_message_interval <= 0:
            return False

        last_external_received_at = self._last_external_message_received_at or self._last_message_received_at
        idle_seconds = max(0.0, time.time() - last_external_received_at)
        # 折算量封顶到 trigger_threshold - 1：与上方 pending_count 守卫互为冗余的双保险，
        # 即便空窗无限长，纯沉默（pending_count == 0）也无法跨过阈值。
        idle_equivalent_count = min(
            idle_seconds / average_message_interval,
            float(max(0, trigger_threshold - 1)),
        )
        equivalent_message_count = pending_count + idle_equivalent_count
        return equivalent_message_count >= trigger_threshold

    def _cancel_deferred_message_turn_task(self) -> None:
        """取消等待空窗补偿触发的延迟任务。"""
        if self._deferred_message_turn_task is None:
            return
        self._deferred_message_turn_task.cancel()
        self._deferred_message_turn_task = None

    async def _schedule_deferred_message_turn(self, delay_seconds: float) -> None:
        """在预计满足空窗补偿条件时再次检查是否应触发循环。"""
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
        """补齐消息中的 @/提及 标记，并在命中时启用强制 continue。"""

        detected_mentioned, detected_at, reply_probability_boost = is_mentioned_bot_in_message(message)
        if detected_at:
            message.is_at = True
        if detected_mentioned:
            message.is_mentioned = True

        should_force_reply = (
            reply_probability_boost >= 1.0
            or (message.is_at and global_config.chat.inevitable_at_reply)
            or (message.is_mentioned and global_config.chat.mentioned_bot_reply)
        )
        if not should_force_reply or (not message.is_at and not message.is_mentioned):
            return

        self._arm_force_next_timing_continue(
            message,
            is_at=message.is_at,
            is_mentioned=message.is_mentioned,
        )

    def _arm_force_next_timing_continue(
        self,
        message: SessionMessage,
        *,
        is_at: bool,
        is_mentioned: bool,
    ) -> None:
        """在检测到 @ 或提及时，要求下一次 Timing Gate 直接 continue。"""

        trigger_reason = "@消息" if is_at else "提及消息" if is_mentioned else "触发消息"
        was_armed = self._force_next_timing_continue
        self._force_next_timing_continue = True
        self._force_next_timing_message_id = message.message_id
        self._force_next_timing_reason = trigger_reason
        self._reset_no_action_backoff()

        if was_armed:
            logger.info(
                f"{self.log_prefix} 检测到新的{trigger_reason}，刷新强制 continue 状态；"
                f"消息编号={message.message_id}"
            )
            return

        logger.info(
            f"{self.log_prefix} 检测到{trigger_reason}，下一次 Timing Gate 将直接视作 continue；"
            f"消息编号={message.message_id}"
        )

    def _consume_force_next_timing_continue_reason(self) -> str | None:
        """消费一次性 Timing Gate continue 状态，并返回原因描述。"""

        if not self._force_next_timing_continue:
            return None

        trigger_reason = self._force_next_timing_reason or "@/提及消息"
        trigger_message_id = self._force_next_timing_message_id or "unknown"
        reason = (
            f"检测到新的{trigger_reason}（消息编号={trigger_message_id}），"
            "本轮直接跳过 Timing Gate 并视作 continue。"
        )
        logger.info(
            f"{self.log_prefix} 已结束本次强制 continue 状态；"
            f"触发原因={trigger_reason} "
            f"触发消息编号={trigger_message_id}"
        )
        self._force_next_timing_continue = False
        self._force_next_timing_message_id = ""
        self._force_next_timing_reason = ""
        return reason

    def _clear_force_next_timing_continue_state(self) -> None:
        """清理一次性 Timing Gate continue 状态，不触发门控提示。"""
        self._force_next_timing_continue = False
        self._force_next_timing_message_id = ""
        self._force_next_timing_reason = ""

    def _has_forced_timing_trigger(self) -> bool:
        """判断是否已有 @/提及必回触发，需绕过普通频率阈值。"""

        return self._force_next_timing_continue

    def _start_planner_continuation(self) -> None:
        """标记已进入连续 Planner 状态。"""

        self._planner_continuation_active = True

    def _finish_planner_continuation(self) -> None:
        """结束连续 Planner 状态。"""

        self._planner_continuation_active = False

    def _is_planner_continuation_active(self) -> bool:
        """判断当前是否保持连续 Planner 状态。"""

        return self._planner_continuation_active

    def _get_no_action_backoff_seconds(self) -> float:
        """按连续 no_action 次数计算下一次退避秒数。"""

        base_seconds = self._no_action_backoff_base_seconds
        cap_seconds = self._no_action_backoff_cap_seconds
        if base_seconds <= 0 or cap_seconds <= 0:
            return 0.0

        start_count = self._no_action_backoff_start_count
        no_action_count = self._no_action_backoff_count
        if no_action_count < start_count:
            return 0.0

        exponent = max(0, no_action_count - start_count)
        return min(cap_seconds, base_seconds * (2**exponent))

    def _reset_no_action_backoff(self) -> None:
        """清理连续 no_action 退避状态。"""

        self._no_action_backoff_count = 0
        self._no_action_backoff_until = 0.0

    def record_no_action_decision_result(self, action_name: str, *, source: str = "planner") -> None:
        """记录决策结果并维护 no_action 退避状态。"""

        if not self.chat_stream.is_group_session:
            self._reset_no_action_backoff()
            return

        normalized_action_name = str(action_name).strip()
        if normalized_action_name != "no_action":
            self._reset_no_action_backoff()
            return

        self._no_action_backoff_count += 1
        backoff_seconds = self._get_no_action_backoff_seconds()
        if backoff_seconds <= 0:
            self._no_action_backoff_until = 0.0
            return

        self._no_action_backoff_until = time.time() + backoff_seconds
        logger.info(
            f"{self.log_prefix} 连续 no_action 退避已更新: "
            f"来源={source} "
            f"连续次数={self._no_action_backoff_count} "
            f"退避={backoff_seconds:.2f} 秒"
        )

    def _should_delay_for_no_action_backoff(self, pending_count: int) -> bool:
        """判断当前消息触发是否应被 no_action 退避延迟。"""

        if focus_mode_manager.is_enabled_for_chat(is_group_chat=self.chat_stream.is_group_session):
            self._reset_no_action_backoff()
            return False

        if not self.chat_stream.is_group_session:
            return False

        backoff_until = self._no_action_backoff_until
        if backoff_until <= 0:
            return False

        now = time.time()
        remaining_seconds = backoff_until - now
        if remaining_seconds <= 0:
            self._no_action_backoff_until = 0.0
            return False

        bypass_pending_count = self._no_action_backoff_bypass_pending_count
        if bypass_pending_count > 0 and pending_count >= bypass_pending_count:
            logger.info(
                f"{self.log_prefix} no_action 退避被待处理消息数绕过: "
                f"待处理={pending_count} 阈值={bypass_pending_count}"
            )
            return False

        logger.debug(f"{self.log_prefix} no_action 退避中，延迟 {remaining_seconds:.2f} 秒后再检查")
        self._cancel_deferred_message_turn_task()
        self._deferred_message_turn_task = asyncio.create_task(
            self._schedule_deferred_message_turn(remaining_seconds)
        )
        return True

    def _bind_planner_interrupt_flag(self, interrupt_flag: asyncio.Event) -> None:
        """绑定当前可打断请求使用的中断标记。"""
        self._planner_interrupt_flag = interrupt_flag
        self._planner_interrupt_requested = False

    def _unbind_planner_interrupt_flag(
        self,
        interrupt_flag: asyncio.Event,
        *,
        interrupted: bool,
    ) -> None:
        """解绑当前可打断请求的中断标记，并维护连续打断计数。"""
        if self._planner_interrupt_flag is interrupt_flag:
            self._planner_interrupt_flag = None
        self._planner_interrupt_requested = False
        if not interrupted:
            self._planner_interrupt_consecutive_count = 0

    def _ensure_background_tasks_running(self) -> None:
        """确保后台任务仍在运行，若崩溃则自动拉起。"""
        if not self._running:
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

    def _register_tool_providers(self) -> None:
        """注册 Maisaka 运行时默认启用的工具 Provider。"""

        self._tool_registry.register_provider(
            MaisakaBuiltinToolProvider(self._reasoning_engine.build_builtin_tool_handlers())
        )
        self._tool_registry.register_provider(PluginToolProvider())
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
    ) -> ChatResponse:
        """运行一个复制上下文的临时子代理，并在完成后立即销毁。"""

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
        while (
            first_kept_index < len(chat_history)
            and dropped_context_count < drop_context_count
        ):
            message = chat_history[first_kept_index]
            if message.count_in_context:
                dropped_context_count += 1
            first_kept_index += 1

        trimmed_history = list(chat_history[first_kept_index:])
        trimmed_history, _ = drop_leading_orphan_tool_results(trimmed_history)
        return trimmed_history

    async def _run_reply_effect_judge(self, prompt: str) -> str:
        """运行回复效果观察器使用的临时 LLM 评审。"""

        judge_message = ReferenceMessage(
            content=prompt,
            timestamp=datetime.now(),
            reference_type=ReferenceMessageType.TOOL_HINT,
            remaining_uses_value=1,
            display_prefix="[回复效果评分任务]",
        )
        response = await self.run_sub_agent(
            context_message_limit=1,
            system_prompt="你是回复效果评分器。请严格按用户给出的 JSON 格式输出，不要输出 JSON 之外的内容。",
            request_kind="reply_effect_judge",
            extra_messages=[judge_message],
            tool_definitions=[],
        )
        return (response.content or "").strip()

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
            if isinstance(message, AssistantMessage)
            for tool_call in message.tool_calls
            if tool_call.func_name == "tool_search" and tool_call.call_id
        }
        discovered_tool_names: set[str] = set()
        for message in selected_history:
            if isinstance(message, SessionBackedMessage) and message.source_kind == "optimized_tool_history":
                discovered_tool_names.update(self._parse_folded_tool_search_result_tool_names(message.visible_text))
                continue
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

    def _parse_folded_tool_search_result_tool_names(self, content: str) -> set[str]:
        """从优化上下文折叠后的 tool_search 文本中恢复已发现工具名。"""

        discovered_tool_names: set[str] = set()
        for raw_line in content.splitlines():
            normalized_line = raw_line.strip()
            if not normalized_line.startswith("- tool_search:"):
                continue
            raw_names = normalized_line.removeprefix("- tool_search:").split("(", 1)[0]
            for raw_tool_name in raw_names.split(","):
                normalized_name = raw_tool_name.strip()
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
            "如需其中某个工具，请先调用 tool_search。tool_search 只负责发现工具，不直接执行业务。",
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

    def _has_pending_messages(self) -> bool:
        return self._last_processed_index < len(self.message_cache)

    def _schedule_message_turn(self) -> None:
        """为当前待处理消息安排一次内部 turn。"""
        if not focus_mode_manager.can_decide(self.session_id, is_group_chat=self.chat_stream.is_group_session):
            logger.debug(f"{self.log_prefix} 当前不在 focus 状态，跳过 Maisaka 决策调度")
            return

        if self._agent_state == self._STATE_WAIT:
            if not self._is_reply_frequency_silent():
                return
            self._enter_stop_state()

        if not self._has_pending_messages() or self._message_turn_scheduled:
            return

        pending_count = self._get_pending_message_count()
        if pending_count <= 0:
            return

        if self._is_reply_frequency_silent():
            self._cancel_deferred_message_turn_task()
            self._message_turn_scheduled = True
            self._internal_turn_queue.put_nowait("message")
            return

        if self._has_forced_timing_trigger():
            self._cancel_deferred_message_turn_task()
            self._message_turn_scheduled = True
            self._internal_turn_queue.put_nowait("message")
            return

        if self._should_delay_for_no_action_backoff(pending_count):
            return

        trigger_threshold = self._get_message_trigger_threshold()
        if pending_count >= trigger_threshold or self._should_trigger_message_turn_by_idle_compensation(
            pending_count=pending_count,
            trigger_threshold=trigger_threshold,
        ):
            self._cancel_deferred_message_turn_task()
            self._message_turn_scheduled = True
            self._internal_turn_queue.put_nowait("message")
            return

        average_message_interval = self._get_recent_average_external_message_interval()
        if average_message_interval is None or average_message_interval <= 0:
            return

        last_external_received_at = self._last_external_message_received_at or self._last_message_received_at
        idle_seconds = max(0.0, time.time() - last_external_received_at)
        delay_seconds = max(0.0, (trigger_threshold - pending_count) * average_message_interval - idle_seconds)
        self._cancel_deferred_message_turn_task()
        self._deferred_message_turn_task = asyncio.create_task(
            self._schedule_deferred_message_turn(delay_seconds)
        )

    def _collect_pending_messages(self) -> list[SessionMessage]:
        """从消息缓存中收集一批尚未处理的消息。"""
        start_index = self._last_processed_index
        pending_messages = self.message_cache[start_index:]
        if not pending_messages:
            return []

        unique_messages: list[SessionMessage] = []
        seen_message_ids: set[str] = set()
        for message in pending_messages:
            message_id = message.message_id
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            unique_messages.append(message)

        self._last_processed_index = len(self.message_cache)
        if unique_messages:
            focus_mode_manager.mark_read(self.session_id)
        # logger.info(
            # f"{self.log_prefix} 已从消息缓存区[{start_index}:{self._last_processed_index}] "
            # f"收集 {len(unique_messages)} 条新消息"
        # )
        return unique_messages

    async def _wait_for_message_quiet_period(self) -> None:
        """等待消息静默窗口结束后，再启动由打断触发的新一轮。"""
        if not self._message_debounce_required:
            return

        if self._message_debounce_seconds <= 0:
            self._message_debounce_required = False
            return

        while self._running:
            elapsed = time.time() - self._last_message_received_at
            remaining = self._message_debounce_seconds - elapsed
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)

        self._message_debounce_required = False

    def _enter_stop_state(self) -> None:
        """切换到停止状态。"""
        self._agent_state = self._STATE_STOP
        self._pending_wait_tool_call_id = None
        self._cancel_wait_timeout_task()

    def _enter_wait_state(self, seconds: Optional[float] = None, tool_call_id: Optional[str] = None) -> None:
        """切换到等待状态。"""
        self._agent_state = self._STATE_WAIT
        self._pending_wait_tool_call_id = tool_call_id
        self._message_turn_scheduled = False
        self._cancel_deferred_message_turn_task()
        self._cancel_wait_timeout_task()
        if seconds is not None:
            self._wait_timeout_task = asyncio.create_task(
                self._schedule_wait_timeout(seconds=seconds, tool_call_id=tool_call_id)
            )

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
            self._agent_state = self._STATE_RUNNING
            await self._internal_turn_queue.put("timeout")
        except asyncio.CancelledError:
            return
        finally:
            if self._wait_timeout_task is not None and self._pending_wait_tool_call_id == tool_call_id:
                self._wait_timeout_task = None

    async def _trigger_trimmed_history_learning(self, context_messages: Sequence[LLMContextMessage]) -> None:
        """提交对 Maisaka 裁切历史的后台学习任务。"""

        if not context_messages:
            return
        if self._trimmed_history_learning_task is not None and not self._trimmed_history_learning_task.done():
            logger.info(f"{self.log_prefix} 裁切历史学习仍在后台运行，跳过新的学习批次")
            return

        enable_expression_learning = self._enable_expression_learning
        enable_behavior_learning = self._enable_behavior_learning
        enable_jargon_learning = self._enable_jargon_learning
        enable_high_frequency_learning = enable_expression_learning or enable_jargon_learning
        if (
            not enable_expression_learning
            and not enable_behavior_learning
            and not enable_jargon_learning
            and not enable_high_frequency_learning
        ):
            logger.debug(f"{self.log_prefix} 表达学习、行为学习、黑话学习和高频词学习均未启用，跳过裁切历史学习")
            return

        pending_context_count = len(context_messages)
        if not self._should_trigger_learning(
            enabled=(
                enable_expression_learning
                or enable_behavior_learning
                or enable_jargon_learning
                or enable_high_frequency_learning
            ),
            feature_name="表达/行为/黑话/高频词学习",
            last_extraction_time=self._last_expression_extraction_time,
            pending_count=pending_context_count,
            min_messages_for_extraction=min(
                self._expression_learner.min_messages_for_extraction,
                self._behavior_learner.min_messages_for_extraction,
            ),
        ):
            return

        self._last_expression_extraction_time = time.time()
        logger.info(
            f"{self.log_prefix} 提交裁切历史后台学习: "
            f"裁切上下文消息数量={pending_context_count} "
            f"是否启用表达学习={enable_expression_learning} "
            f"是否启用行为学习={enable_behavior_learning} "
            f"是否启用黑话学习={enable_jargon_learning} "
            f"是否启用高频词学习={enable_high_frequency_learning}"
        )

        self._trimmed_history_learning_task = asyncio.create_task(
            self._run_trimmed_history_learning(
                list(context_messages),
                enable_expression_learning=enable_expression_learning,
                enable_behavior_learning=enable_behavior_learning,
                enable_jargon_learning=enable_jargon_learning,
                enable_high_frequency_learning=enable_high_frequency_learning,
            )
        )
        self._trimmed_history_learning_task.add_done_callback(self._handle_trimmed_history_learning_done)

    async def _run_trimmed_history_learning(
        self,
        context_messages: Sequence[LLMContextMessage],
        *,
        enable_expression_learning: bool,
        enable_behavior_learning: bool,
        enable_jargon_learning: bool,
        enable_high_frequency_learning: bool,
    ) -> None:
        """在后台执行表达、行为、黑话与高频词学习。"""

        async def run_expression_and_jargon_learning() -> bool:
            jargon_miner = self._jargon_miner if enable_jargon_learning else None
            try:
                return await self._expression_learner.learn_from_context_messages(
                    context_messages,
                    jargon_miner,
                    enable_expression_learning=enable_expression_learning,
                )
            except Exception:
                logger.exception(f"{self.log_prefix} 裁切历史表达/黑话学习异常")
                return False

        async def run_behavior_learning() -> bool:
            try:
                return await self._behavior_learner.learn_from_context_messages(context_messages)
            except Exception:
                logger.exception(f"{self.log_prefix} 裁切历史行为学习异常")
                return False

        async def run_high_frequency_learning() -> bool:
            try:
                updated_count = update_high_frequency_terms_from_context_messages(context_messages)
            except Exception:
                logger.exception(f"{self.log_prefix} 裁切历史高频词学习异常")
                return False
            if updated_count <= 0:
                logger.debug(f"{self.log_prefix} 裁切历史高频词学习未产生词条")
                return False
            logger.info(f"{self.log_prefix} 裁切历史高频词学习完成: 更新词条数={updated_count}")
            return True

        learner_tasks: list[asyncio.Task[bool]] = []
        if enable_expression_learning or enable_jargon_learning:
            learner_tasks.append(asyncio.create_task(run_expression_and_jargon_learning()))
        if enable_behavior_learning:
            learner_tasks.append(asyncio.create_task(run_behavior_learning()))
        if enable_high_frequency_learning:
            learner_tasks.append(asyncio.create_task(run_high_frequency_learning()))
        if not learner_tasks:
            return

        results = await asyncio.gather(*learner_tasks)
        if any(results):
            logger.info(f"{self.log_prefix} 裁切历史学习成功")
        else:
            logger.debug(f"{self.log_prefix} 裁切历史学习未产生结果")

    def _handle_trimmed_history_learning_done(self, task: asyncio.Task[None]) -> None:
        """清理裁切历史后台学习任务状态。"""

        if self._trimmed_history_learning_task is task:
            self._trimmed_history_learning_task = None
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

    def _should_trigger_learning(
        self,
        *,
        enabled: bool,
        feature_name: str,
        last_extraction_time: float,
        pending_count: int,
        min_messages_for_extraction: int,
    ) -> bool:
        """判断周期性学习任务是否满足执行条件。"""

        if not enabled:
            logger.debug(f"{self.log_prefix} {feature_name}未启用，跳过本轮学习")
            return False

        elapsed = time.time() - last_extraction_time
        if elapsed < self._min_extraction_interval:
            logger.debug(
                f"{self.log_prefix} {feature_name}触发间隔不足: "
                f"已过={elapsed:.2f} 秒 阈值={self._min_extraction_interval} 秒"
            )
            return False

        if pending_count < min_messages_for_extraction:
            logger.debug(
                f"{self.log_prefix} {feature_name}待处理消息不足: "
                f"待处理={pending_count} 阈值={min_messages_for_extraction} "
                f"缓存总量={len(self.message_cache)}"
            )
            return False

        return True

    async def _init_mcp(self) -> None:
        """初始化 MCP 工具并注册到统一工具层。"""
        if not build_mcp_server_runtime_configs(global_config.mcp):
            logger.debug(f"{self.log_prefix} 未配置可用的 MCP 服务，跳过 Maisaka MCP 初始化")
            return

        self._mcp_host_bridge = MCPHostLLMBridge(
            sampling_task_name=global_config.mcp.client.sampling.task_name,
        )
        self._mcp_manager = await MCPManager.from_app_config(
            global_config.mcp,
            host_callbacks=self._mcp_host_bridge.build_callbacks(),
        )
        if self._mcp_manager is None:
            logger.warning(f"{self.log_prefix} Maisaka MCP 管理器初始化失败，MCP 工具不会注册")
            return

        mcp_tool_specs = self._mcp_manager.get_tool_specs()
        if not mcp_tool_specs:
            logger.info(f"{self.log_prefix} Maisaka 没有可供使用的 MCP 工具")
            return

        self._tool_registry.register_provider(MCPToolProvider(self._mcp_manager))
        logger.info(
            f"{self.log_prefix} 已向 Maisaka 加载 {len(mcp_tool_specs)} 个 MCP 工具。\n"
            f"{self._mcp_manager.get_feature_summary()}"
        )

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
