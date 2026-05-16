"""Maisaka 非 CLI 运行时。"""

from collections import deque
from datetime import datetime
from math import ceil
from typing import Any, Literal, Optional, Sequence

import asyncio
import json
import time

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text

from src.cli.console import console
from src.chat.heart_flow.heartFC_utils import CycleDetail
from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import is_mentioned_bot_in_message
from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.common.logger import get_logger
from src.common.utils.utils_config import ChatConfigUtils, ExpressionConfigUtils, JargonConfigUtils
from src.config.config import global_config
from src.core.tooling import ToolRegistry, ToolSpec
from src.learners.expression_learner import ExpressionLearner
from src.learners.jargon_miner import JargonMiner
from src.llm_models.payload_content.resp_format import RespFormat
from src.llm_models.payload_content.tool_option import ToolDefinitionInput
from src.mcp_module import MCPManager
from src.mcp_module.config import build_mcp_server_runtime_configs
from src.mcp_module.host_llm_bridge import MCPHostLLMBridge
from src.mcp_module.provider import MCPToolProvider
from src.plugin_runtime.tool_provider import PluginToolProvider
from src.plugin_runtime.hook_payloads import deserialize_prompt_messages

from .chat_loop_service import ChatResponse, MaisakaChatLoopService
from .context_messages import (
    AssistantMessage,
    LLMContextMessage,
    ReferenceMessage,
    ReferenceMessageType,
    SessionBackedMessage,
    ToolResultMessage,
)
from .display.display_utils import build_tool_call_summary_lines, format_token_count
from .display.prompt_cli_renderer import PromptCLIVisualizer
from .display.stage_status_board import remove_stage_status, update_stage_status
from .history_utils import drop_leading_orphan_tool_results
from .monitor_events import emit_message_sent, emit_session_start
from .reasoning_engine import MaisakaReasoningEngine
from .reply_effect import ReplyEffectTracker
from .reply_effect.image_utils import extract_visual_attachments_from_sequence
from .reply_effect.quote_utils import extract_quote_target_ids, message_id_from_context_message
from .tool_provider import MaisakaBuiltinToolProvider

logger = get_logger("maisaka_runtime")

MAX_INTERNAL_ROUNDS = 10
MAX_RETAINED_MESSAGE_CACHE_SIZE = 200


class MaisakaHeartFlowChatting:
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
        self._oldest_pending_message_received_at: Optional[float] = None
        self._last_message_received_at = 0.0
        self._talk_frequency_adjust = 1.0
        self._reply_latency_measurement_started_at: Optional[float] = None
        self._recent_reply_latencies: deque[tuple[float, float]] = deque()
        self._wait_timeout_task: Optional[asyncio.Task[None]] = None
        self._max_internal_rounds = MAX_INTERNAL_ROUNDS
        self._agent_state: Literal["running", "wait", "stop"] = self._STATE_STOP
        self._pending_wait_tool_call_id: Optional[str] = None
        self._force_next_timing_continue = False
        self._force_next_timing_message_id = ""
        self._force_next_timing_reason = ""
        self._timing_gate_non_continue_cooldown_seconds = max(
            0.0,
            float(global_config.chat.timing_gate_non_continue_cooldown_seconds),
        )
        self._planner_interrupt_flag: Optional[asyncio.Event] = None
        self._planner_interrupt_requested = False
        self._planner_interrupt_consecutive_count = 0
        self._current_action_tool_names: set[str] = set()
        self.discovered_tool_names: set[str] = set()
        self.deferred_tool_specs_by_name: dict[str, ToolSpec] = {}
        self._planner_interrupt_max_consecutive_count = max(
            0,
            int(global_config.chat.planner_interrupt_max_consecutive_count),
        )

        expr_use, expr_learn = ExpressionConfigUtils.get_expression_config_for_chat(session_id)
        self._enable_expression_use = expr_use
        self._enable_expression_learning = expr_learn
        self._enable_jargon_use, self._enable_jargon_learning = JargonConfigUtils.get_jargon_config_for_chat(session_id)
        self._min_extraction_interval = 30
        self._last_expression_extraction_time = 0.0
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

        self._running = True
        self._ensure_background_tasks_running()
        self._schedule_message_turn()
        self._update_stage_status("空闲", "等待消息触发")
        logger.info(f"{self.log_prefix} Maisaka 运行时已启动")

    async def stop(self) -> None:
        """停止运行时主循环。"""
        if not self._running:
            return

        self._running = False
        self._message_turn_scheduled = False
        self._message_debounce_required = False
        self._cancel_deferred_message_turn_task()
        self._cancel_wait_timeout_task()
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
        await self._tool_registry.close()
        self._mcp_manager = None
        self._mcp_host_bridge = None
        remove_stage_status(self.session_id)

        logger.info(f"{self.log_prefix} Maisaka 运行时已停止")

    def adjust_talk_frequency(self, frequency: float) -> None:
        """调整当前会话的回复频率倍率。"""
        self._talk_frequency_adjust = max(0.01, float(frequency))
        self._schedule_message_turn()

    def append_sent_message_to_chat_history(
        self,
        message: SessionMessage,
        *,
        source_kind: str = "guided_reply",
    ) -> bool:
        """将一条已发送成功的消息同步到 Maisaka 内部历史。"""

        try:
            from .context_messages import SessionBackedMessage
            from .history_utils import build_prefixed_message_sequence, build_session_message_visible_text
            from .planner_message_utils import build_planner_prefix

            user_info = message.message_info.user_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
            planner_prefix = build_planner_prefix(
                timestamp=message.timestamp,
                user_name=speaker_name,
                group_card=user_info.user_cardname or "",
                message_id=message.message_id,
                include_message_id=not message.is_notify and bool(message.message_id),
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
        if self._oldest_pending_message_received_at is None:
            self._oldest_pending_message_received_at = received_at
        self._update_message_trigger_state(message)
        self.message_cache.append(message)
        self._prune_processed_message_cache()
        if self._is_reply_effect_tracking_enabled():
            asyncio.create_task(self._reply_effect_tracker.observe_user_message(message))
        if self._agent_state == self._STATE_RUNNING:
            self._message_debounce_required = True
        if self._agent_state == self._STATE_RUNNING and self._planner_interrupt_flag is not None:
            if self._planner_interrupt_requested:
                logger.info(
                    f"{self.log_prefix} 收到新消息，但当前请求已发起过一次规划器打断，"
                    f"本次不重复打断; 消息编号={message.message_id} "
                    f"连续打断次数={self._planner_interrupt_consecutive_count}/"
                    f"{self._planner_interrupt_max_consecutive_count}"
                )
            elif self._planner_interrupt_consecutive_count >= self._planner_interrupt_max_consecutive_count:
                logger.info(
                    f"{self.log_prefix} 收到新消息，但已达到规划器连续打断上限，"
                    f"将等待当前请求自然完成; 消息编号={message.message_id} "
                    f"连续打断次数={self._planner_interrupt_consecutive_count}/"
                    f"{self._planner_interrupt_max_consecutive_count}"
                )
            else:
                self._planner_interrupt_requested = True
                self._planner_interrupt_consecutive_count += 1
                logger.info(
                    f"{self.log_prefix} 收到新消息，发起规划器打断; "
                    f"消息编号={message.message_id} 缓存条数={len(self.message_cache)} "
                    f"时间戳={time.time():.3f} "
                    f"连续打断次数={self._planner_interrupt_consecutive_count}/"
                    f"{self._planner_interrupt_max_consecutive_count}"
                )
                self._planner_interrupt_flag.set()
        if self._running:
            self._schedule_message_turn()

    def _get_effective_reply_frequency(self) -> float:
        """返回当前会话生效的回复频率。"""
        talk_value = max(
            0.01,
            float(
                ChatConfigUtils.get_talk_value(
                    self.session_id,
                    is_group_chat=self.chat_stream.is_group_session,
                )
            ),
        )
        return max(0.01, talk_value * self._talk_frequency_adjust)

    async def track_reply_effect(
        self,
        *,
        tool_call_id: str,
        target_message: SessionMessage,
        set_quote: bool,
        reply_text: str,
        reply_segments: list[str],
        planner_reasoning: str,
        reference_info: str,
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
                reference_info=reference_info,
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

    def _prune_recent_reply_latencies(self, now: Optional[float] = None) -> None:
        """仅保留最近 10 分钟内的回复时长记录。"""
        current_time = time.time() if now is None else now
        expire_before = current_time - 600.0
        while self._recent_reply_latencies and self._recent_reply_latencies[0][0] < expire_before:
            self._recent_reply_latencies.popleft()

    def _get_recent_average_reply_latency(self) -> Optional[float]:
        """获取最近 10 分钟平均消息回复时长。"""
        self._prune_recent_reply_latencies()
        if not self._recent_reply_latencies:
            return None

        total_duration = sum(duration for _, duration in self._recent_reply_latencies)
        return total_duration / len(self._recent_reply_latencies)

    def _record_reply_sent(self) -> None:
        """在成功发送 reply 后记录本轮消息回复时长。"""
        if self._reply_latency_measurement_started_at is None:
            return

        reply_duration = max(0.0, time.time() - self._reply_latency_measurement_started_at)
        self._reply_latency_measurement_started_at = None
        self._recent_reply_latencies.append((time.time(), reply_duration))
        self._prune_recent_reply_latencies()
        logger.debug(
            f"{self.log_prefix} 已记录消息回复时长: {reply_duration:.2f} 秒 "
            f"最近10分钟样本数={len(self._recent_reply_latencies)}"
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
        """在新消息不足阈值时，按空窗时间折算补齐触发条件。"""
        average_reply_latency = self._get_recent_average_reply_latency()
        if average_reply_latency is None or average_reply_latency <= 0:
            return False

        idle_seconds = max(0.0, time.time() - self._last_message_received_at)
        equivalent_message_count = pending_count + idle_seconds / average_reply_latency
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
        if global_config.chat.enable_independent_timing_gate:
            reason = (
                f"检测到新的{trigger_reason}（消息编号={trigger_message_id}），"
                "本轮直接跳过 Timing Gate 并视作 continue。"
            )
        else:
            reason = (
                f"检测到新的{trigger_reason}（消息编号={trigger_message_id}），"
                "本轮直接交由 Planner 处理。"
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

    def _has_forced_timing_trigger(self) -> bool:
        """判断是否已有 @/提及必回触发，需绕过普通频率阈值。"""

        return self._force_next_timing_continue

    async def _wait_for_timing_gate_non_continue_cooldown(self, elapsed_seconds: float) -> None:
        """仅对 Timing Gate 的 no_action 动作应用冷却窗口。"""

        cooldown_seconds = self._timing_gate_non_continue_cooldown_seconds
        if cooldown_seconds <= 0:
            return

        remaining_seconds = cooldown_seconds - max(0.0, elapsed_seconds)
        if remaining_seconds <= 0:
            return

        logger.info(f"{self.log_prefix} Timing Gate 非 continue 冷却中，等待 {remaining_seconds:.2f} 秒后结束")
        await asyncio.sleep(remaining_seconds)

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
        if self._agent_state == self._STATE_WAIT:
            return

        if not self._has_pending_messages() or self._message_turn_scheduled:
            return

        pending_count = self._get_pending_message_count()
        if pending_count <= 0:
            return

        if self._has_forced_timing_trigger():
            self._cancel_deferred_message_turn_task()
            self._message_turn_scheduled = True
            self._internal_turn_queue.put_nowait("message")
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

        average_reply_latency = self._get_recent_average_reply_latency()
        if average_reply_latency is None or average_reply_latency <= 0:
            return

        idle_seconds = max(0.0, time.time() - self._last_message_received_at)
        delay_seconds = max(0.0, (trigger_threshold - pending_count) * average_reply_latency - idle_seconds)
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
        # logger.info(
            # f"{self.log_prefix} 已从消息缓存区[{start_index}:{self._last_processed_index}] "
            # f"收集 {len(unique_messages)} 条新消息"
        # )
        if unique_messages and self._reply_latency_measurement_started_at is None:
            self._reply_latency_measurement_started_at = (
                self._oldest_pending_message_received_at or self._last_message_received_at
            )
        self._oldest_pending_message_received_at = None
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
        """对 Maisaka 裁切掉的真实聊天历史触发表达学习。"""

        if not context_messages:
            return
        if not self._enable_expression_learning:
            logger.debug(f"{self.log_prefix} 表达学习未启用，跳过裁切历史学习")
            return

        pending_context_count = len(context_messages)
        if not self._should_trigger_learning(
            enabled=self._enable_expression_learning,
            feature_name="表达学习",
            last_extraction_time=self._last_expression_extraction_time,
            pending_count=pending_context_count,
            min_messages_for_extraction=self._expression_learner.min_messages_for_extraction,
        ):
            return

        self._last_expression_extraction_time = time.time()
        logger.info(
            f"{self.log_prefix} 触发裁切历史表达学习: "
            f"裁切上下文消息数量={pending_context_count} "
            f"是否启用黑话学习={self._enable_jargon_learning}"
        )

        try:
            jargon_miner = self._jargon_miner if self._enable_jargon_learning else None
            learnt_style = await self._expression_learner.learn_from_context_messages(
                context_messages,
                jargon_miner,
            )
            if learnt_style:
                logger.info(f"{self.log_prefix} 裁切历史表达学习成功")
            else:
                logger.debug(f"{self.log_prefix} 裁切历史表达学习未产生结果")
        except Exception:
            logger.exception(f"{self.log_prefix} 裁切历史表达学习异常")

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

    def _render_context_usage_panel(
        self,
        *,
        cycle_id: Optional[int] = None,
        time_records: Optional[dict[str, float]] = None,
        timing_selected_history_count: Optional[int] = None,
        timing_prompt_tokens: Optional[int] = None,
        timing_model_name: Optional[str] = None,
        timing_action: str = "",
        timing_response: str = "",
        timing_tool_calls: Optional[list[Any]] = None,
        timing_tool_results: Optional[list[str]] = None,
        timing_tool_detail_results: Optional[list[dict[str, Any]]] = None,
        timing_prompt_section: Optional[RenderableType] = None,
        planner_selected_history_count: Optional[int] = None,
        planner_prompt_tokens: Optional[int] = None,
        planner_model_name: Optional[str] = None,
        planner_response: str = "",
        planner_tool_calls: Optional[list[Any]] = None,
        planner_tool_results: Optional[list[str]] = None,
        planner_tool_detail_results: Optional[list[dict[str, Any]]] = None,
        planner_prompt_section: Optional[RenderableType] = None,
        planner_extra_lines: Optional[list[str]] = None,
    ) -> None:
        """在终端展示当前聊天流本轮 cycle 的最终结果。"""
        if not global_config.debug.show_maisaka_thinking:
            return

        body_lines = [
            f"聊天流名称：{getattr(self, 'session_name', self.session_id)}",
            f"聊天流ID：{self.session_id}",
        ]

        panel_title = "MaiSaka 循环"
        if cycle_id is not None:
            panel_title = f"{panel_title} [{cycle_id}]"
        panel_subtitle = self._build_cycle_time_records_text(time_records or {})
        renderables: list[RenderableType] = [Text("\n".join(body_lines))]
        timing_panel = self._build_cycle_stage_panel(
            title="Timing Gate",
            border_style="bright_magenta",
            selected_history_count=timing_selected_history_count,
            prompt_tokens=timing_prompt_tokens,
            model_name=timing_model_name,
            response_text=timing_response,
            prompt_section=timing_prompt_section,
            extra_lines=None,
        )
        if timing_panel is not None:
            renderables.append(timing_panel)

        timing_tool_cards = self._build_tool_activity_cards(
            stage_title="Timing Tool",
            tool_calls=timing_tool_calls,
            tool_results=timing_tool_results,
            tool_detail_results=timing_tool_detail_results,
            planner_style=False,
        )
        if timing_tool_cards:
            renderables.extend(timing_tool_cards)

        planner_panel = self._build_cycle_stage_panel(
            title="Planner",
            border_style="green",
            selected_history_count=planner_selected_history_count,
            prompt_tokens=planner_prompt_tokens,
            model_name=planner_model_name,
            response_text=planner_response,
            prompt_section=planner_prompt_section,
            extra_lines=planner_extra_lines,
        )
        if planner_panel is not None:
            renderables.append(planner_panel)

        planner_tool_cards = self._build_tool_activity_cards(
            stage_title="Planner Tool",
            tool_calls=planner_tool_calls,
            tool_results=planner_tool_results,
            tool_detail_results=planner_tool_detail_results,
            planner_style=True,
        )
        if planner_tool_cards:
            renderables.extend(planner_tool_cards)

        console.print(
            Panel(
                Group(*renderables),
                title=panel_title,
                subtitle=panel_subtitle,
                border_style="bright_blue",
                padding=(0, 1),
            )
        )

    def _build_cycle_stage_panel(
        self,
        *,
        title: str,
        border_style: str,
        selected_history_count: Optional[int],
        prompt_tokens: Optional[int],
        model_name: Optional[str] = None,
        response_text: str = "",
        prompt_section: Optional[RenderableType] = None,
        extra_lines: Optional[list[str]] = None,
    ) -> Optional[Panel]:
        """构建单个 cycle 阶段的展示卡片。"""

        has_content = any([
            selected_history_count is not None,
            prompt_tokens is not None,
            bool((model_name or "").strip()),
            bool(response_text.strip()),
            prompt_section is not None,
            bool(extra_lines),
        ])
        if not has_content:
            return None

        body_lines: list[str] = []
        normalized_model_name = (model_name or "").strip()
        if normalized_model_name:
            body_lines.append(f"请求模型：{normalized_model_name}")
        if prompt_tokens is not None:
            body_lines.append(f"本次请求token消耗：{format_token_count(prompt_tokens)}")
        if extra_lines:
            body_lines.extend([line for line in extra_lines if isinstance(line, str) and line.strip()])

        renderables: list[RenderableType] = []
        if body_lines:
            renderables.append(Text("\n".join(body_lines)))
        if prompt_section is not None:
            renderables.append(prompt_section)

        normalized_response = response_text.strip()
        if normalized_response:
            renderables.append(
                Panel(
                    Text(normalized_response),
                    title="Maisaka 返回",
                    border_style=border_style,
                    padding=(0, 1),
                )
            )

        return Panel(
            Group(*renderables),
            title=title,
            border_style=border_style,
            padding=(0, 1),
        )

    def _build_tool_activity_cards(
        self,
        *,
        stage_title: str,
        tool_calls: Optional[list[Any]] = None,
        tool_results: Optional[list[str]] = None,
        tool_detail_results: Optional[list[dict[str, Any]]] = None,
        planner_style: bool = False,
    ) -> list[RenderableType]:
        """构建与阶段同级的工具执行卡片列表。"""

        detail_results = tool_detail_results or []
        cards = self._build_tool_detail_cards(
            detail_results,
            stage_title=stage_title,
            planner_style=planner_style,
        )
        if cards:
            return cards

        # 兼容旧数据结构：若尚无 detail，则降级为简单文本卡片。
        fallback_lines = self._filter_redundant_tool_results(
            tool_results=tool_results or [],
            tool_detail_results=detail_results,
        )
        if not fallback_lines and tool_calls:
            fallback_lines = build_tool_call_summary_lines(tool_calls)
        if not fallback_lines:
            return []

        fallback_border_style = "yellow"
        return [
            Panel(
                Text("\n".join(fallback_lines)),
                title=stage_title,
                border_style=fallback_border_style,
                padding=(0, 1),
            )
        ]

    @staticmethod
    def _build_cycle_time_records_text(time_records: dict[str, float]) -> str:
        """构建循环最外层面板展示的阶段耗时文本。"""

        if not time_records:
            return "流程耗时：无"

        label_map = {
            "timing_gate": "Timing Gate",
            "planner": "Planner",
            "tool_calls": "工具执行",
        }
        ordered_keys = ["timing_gate", "planner", "tool_calls"]

        parts: list[str] = []
        for key in ordered_keys:
            duration = time_records.get(key)
            if isinstance(duration, (int, float)):
                parts.append(f"{label_map.get(key, key)} {float(duration):.2f} s")

        for key, duration in time_records.items():
            if key in ordered_keys or not isinstance(duration, (int, float)):
                continue
            parts.append(f"{label_map.get(key, key)} {float(duration):.2f} s")

        if not parts:
            return "流程耗时：无"
        return "流程耗时：" + " | ".join(parts)

    @staticmethod
    def _filter_redundant_tool_results(
        *,
        tool_results: list[str],
        tool_detail_results: list[dict[str, Any]],
    ) -> list[str]:
        """过滤掉已经在详情卡片中展示过的工具摘要。"""

        detailed_summaries = {
            str(tool_result.get("summary") or "").strip()
            for tool_result in tool_detail_results
            if isinstance(tool_result.get("detail"), dict) and tool_result.get("detail")
        }
        return [
            result.strip()
            for result in tool_results
            if isinstance(result, str)
            and result.strip()
            and result.strip() not in detailed_summaries
        ]

    @staticmethod
    def _build_tool_metrics_text(metrics: dict[str, Any]) -> str:
        """将工具监控 metrics 转换为便于 CLI 阅读的文本。"""

        lines: list[str] = []
        model_name = str(metrics.get("model_name") or "").strip()
        if model_name:
            lines.append(f"模型：{model_name}")

        prompt_tokens = metrics.get("prompt_tokens")
        completion_tokens = metrics.get("completion_tokens")
        total_tokens = metrics.get("total_tokens")
        if isinstance(prompt_tokens, int) or isinstance(completion_tokens, int) or isinstance(total_tokens, int):
            lines.append(
                "Token："
                f"输入 {format_token_count(int(prompt_tokens or 0))} / "
                f"输出 {format_token_count(int(completion_tokens or 0))} / "
                f"总计 {format_token_count(int(total_tokens or 0))}"
            )

        prompt_ms = metrics.get("prompt_ms")
        llm_ms = metrics.get("llm_ms")
        overall_ms = metrics.get("overall_ms")
        timing_parts: list[str] = []
        if isinstance(prompt_ms, (int, float)):
            timing_parts.append(f"prompt {round(float(prompt_ms), 2)} ms")
        if isinstance(llm_ms, (int, float)):
            timing_parts.append(f"llm {round(float(llm_ms), 2)} ms")
        if isinstance(overall_ms, (int, float)):
            timing_parts.append(f"overall {round(float(overall_ms), 2)} ms")
        if timing_parts:
            lines.append("耗时：" + " / ".join(timing_parts))

        return "\n".join(lines)

    @staticmethod
    def _get_tool_detail_labels(tool_name: str) -> dict[str, str]:
        """返回不同工具对应的详情区标题与预览类别。"""

        normalized_tool_name = str(tool_name or "").strip().lower()
        if normalized_tool_name == "reply":
            return {
                "prompt_title": "Reply Prompt",
                "reasoning_title": "Reply 思考",
                "output_title": "Reply 输出",
                "prompt_category": "replyer",
                "request_kind": "replyer",
            }
        if normalized_tool_name == "send_emoji":
            return {
                "prompt_title": "Emotion Prompt",
                "reasoning_title": "Emotion 思考",
                "output_title": "Emotion 输出",
                "prompt_category": "emotion",
                "request_kind": "emotion",
            }
        display_name = normalized_tool_name or "tool"
        return {
            "prompt_title": f"{display_name} Prompt",
            "reasoning_title": f"{display_name} 思考",
            "output_title": f"{display_name} 输出",
            "prompt_category": display_name,
            "request_kind": "sub_agent",
        }

    def _build_tool_prompt_access_panel(
        self,
        *,
        tool_name: str,
        prompt_text: str,
        request_messages: Optional[list[Any]] = None,
        tool_call_id: str,
        border_style: str = "bright_yellow",
        output_content: str = "",
    ) -> Panel:
        """将工具 prompt 渲染为可点击查看的预览入口。"""

        labels = self._get_tool_detail_labels(tool_name)
        subtitle = f"会话ID: {self.session_id}"
        if tool_call_id:
            subtitle += f"\n调用ID: {tool_call_id}"

        if isinstance(request_messages, list) and request_messages:
            try:
                normalized_messages = deserialize_prompt_messages(request_messages)
            except Exception as exc:
                logger.warning(f"工具 {tool_name} 的 request_messages 无法反序列化，已回退为文本预览: {exc}")
            else:
                return Panel(
                    PromptCLIVisualizer.build_prompt_access_panel(
                        normalized_messages,
                        category=labels["prompt_category"],
                        chat_id=self.session_id,
                        request_kind=labels["request_kind"],
                        selection_reason=subtitle,
                        output_content=output_content,
                    ),
                    title=labels["prompt_title"],
                    border_style=border_style,
                    padding=(0, 1),
                )

        return Panel(
            PromptCLIVisualizer.build_text_access_panel(
                prompt_text,
                category=labels["prompt_category"],
                chat_id=self.session_id,
                request_kind=labels["request_kind"],
                subtitle=subtitle,
                output_content=output_content,
            ),
            title=labels["prompt_title"],
            border_style=border_style,
            padding=(0, 1),
        )

    def _normalize_tool_card_body_lines(self, body: Any) -> list[str]:
        """将工具卡片正文规范化为行列表。"""

        if isinstance(body, str):
            return [line for line in body.splitlines() if line.strip()]
        if isinstance(body, list):
            return [
                str(item).strip()
                for item in body
                if str(item).strip()
            ]
        return []

    def _build_custom_tool_sub_cards(
        self,
        sub_cards: Any,
        *,
        default_border_style: str,
    ) -> list[RenderableType]:
        """构建工具自定义子卡片。"""

        if not isinstance(sub_cards, list):
            return []

        renderables: list[RenderableType] = []
        for sub_card in sub_cards:
            if not isinstance(sub_card, dict):
                continue
            title = str(sub_card.get("title") or "").strip() or "附加信息"
            border_style = str(sub_card.get("border_style") or "").strip() or default_border_style
            body_lines = self._normalize_tool_card_body_lines(
                sub_card.get("body_lines", sub_card.get("content", ""))
            )
            if not body_lines:
                continue
            renderables.append(
                Panel(
                    Text("\n".join(body_lines)),
                    title=title,
                    border_style=border_style,
                    padding=(0, 1),
                )
            )
        return renderables

    def _build_default_tool_detail_parts(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        tool_args: Any,
        summary: str,
        duration_ms: Any,
        detail: dict[str, Any],
        planner_style: bool,
    ) -> list[RenderableType]:
        """构建工具卡片默认内容块。"""

        argument_border_style = "yellow"
        metrics_border_style = "bright_yellow"
        prompt_border_style = "bright_yellow"
        reasoning_border_style = "yellow"
        output_border_style = "bright_yellow"
        extra_info_border_style = "yellow"
        detail_labels = self._get_tool_detail_labels(tool_name)

        parts: list[RenderableType] = []
        header_lines: list[str] = []
        if summary:
            header_lines.append(summary)
        if tool_call_id:
            header_lines.append(f"调用ID：{tool_call_id}")
        if isinstance(duration_ms, (int, float)):
            header_lines.append(f"执行耗时：{round(float(duration_ms), 2)} ms")
        if header_lines:
            parts.append(Text("\n".join(header_lines)))

        if isinstance(tool_args, dict) and tool_args:
            parts.append(
                Panel(
                    Pretty(tool_args, expand_all=True),
                    title="工具参数",
                    border_style=argument_border_style,
                    padding=(0, 1),
                )
            )

        metrics = detail.get("metrics")
        if isinstance(metrics, dict):
            metrics_text = self._build_tool_metrics_text(metrics)
            if metrics_text:
                parts.append(
                    Panel(
                        Text(metrics_text),
                        title="执行指标",
                        border_style=metrics_border_style,
                        padding=(0, 1),
                    )
                )

        output_text = str(detail.get("output_text") or "").strip()
        prompt_text = str(detail.get("prompt_text") or "").strip()
        if prompt_text:
            parts.append(
                self._build_tool_prompt_access_panel(
                    tool_name=tool_name,
                    prompt_text=prompt_text,
                    request_messages=detail.get("request_messages") if isinstance(detail.get("request_messages"), list) else None,
                    tool_call_id=tool_call_id,
                    border_style=prompt_border_style,
                    output_content=output_text,
                )
            )

        reasoning_text = str(detail.get("reasoning_text") or "").strip()
        if reasoning_text:
            parts.append(
                Panel(
                    Text(reasoning_text),
                    title=detail_labels["reasoning_title"],
                    border_style=reasoning_border_style,
                    padding=(0, 1),
                )
            )

        if output_text:
            parts.append(
                Panel(
                    Text(output_text),
                    title=detail_labels["output_title"],
                    border_style=output_border_style,
                    padding=(0, 1),
                )
            )

        extra_sections = detail.get("extra_sections")
        if isinstance(extra_sections, list):
            for section in extra_sections:
                if not isinstance(section, dict):
                    continue
                section_title = str(section.get("title") or "").strip() or "附加信息"
                section_content = str(section.get("content") or "").strip()
                if not section_content:
                    continue
                parts.append(
                    Panel(
                        Text(section_content),
                        title=section_title,
                        border_style=extra_info_border_style,
                        padding=(0, 1),
                    )
                )

        return parts

    def _build_tool_detail_cards(
        self,
        tool_detail_results: list[dict[str, Any]],
        *,
        stage_title: str,
        planner_style: bool = False,
    ) -> list[RenderableType]:
        """将 tool monitor detail 渲染为与 Planner/Timing 平级的工具卡片。"""

        detail_panel_border_style = "yellow"
        sub_card_border_style = "bright_yellow"

        panels: list[RenderableType] = []
        for tool_result in tool_detail_results:
            detail = tool_result.get("detail")
            detail_dict = detail if isinstance(detail, dict) else {}
            tool_name = str(tool_result.get("tool_name") or "unknown").strip() or "unknown"
            tool_title = str(tool_result.get("tool_title") or "").strip() or tool_name
            tool_call_id = str(tool_result.get("tool_call_id") or "").strip()
            tool_args = tool_result.get("tool_args")
            summary = str(tool_result.get("summary") or "").strip()
            duration_ms = tool_result.get("duration_ms")
            custom_card = tool_result.get("card")

            parts: list[RenderableType] = []
            custom_title = ""
            card_border_style = detail_panel_border_style
            replace_default_children = False
            if isinstance(custom_card, dict):
                custom_title = str(custom_card.get("title") or "").strip()
                card_border_style = str(custom_card.get("border_style") or "").strip() or detail_panel_border_style
                replace_default_children = bool(custom_card.get("replace_default_children", False))
                custom_body_lines = self._normalize_tool_card_body_lines(
                    custom_card.get("body_lines", custom_card.get("content", ""))
                )
                if custom_body_lines:
                    parts.append(Text("\n".join(custom_body_lines)))

            if not replace_default_children:
                parts.extend(
                    self._build_default_tool_detail_parts(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        tool_args=tool_args,
                        summary=summary,
                        duration_ms=duration_ms,
                        detail=detail_dict,
                        planner_style=planner_style,
                    )
                )

            if isinstance(custom_card, dict):
                parts.extend(
                    self._build_custom_tool_sub_cards(
                        custom_card.get("sub_cards"),
                        default_border_style=sub_card_border_style,
                    )
                )
            parts.extend(
                self._build_custom_tool_sub_cards(
                    tool_result.get("sub_cards"),
                    default_border_style=sub_card_border_style,
                )
            )

            if parts:
                panels.append(
                    Panel(
                        Group(*parts),
                        title=custom_title or f"{stage_title} · {tool_title}",
                        border_style=card_border_style,
                        padding=(0, 1),
                    )
                )

        return panels

    def _log_cycle_started(self, cycle_detail: CycleDetail, round_index: int) -> None:
        logger.debug(
            f"{self.log_prefix} MaiSaka 轮次开始: 循环编号={cycle_detail.cycle_id} "
            f"回合={round_index + 1}/{self._max_internal_rounds} "
            f"上下文消息数={len(self._chat_history)}"
        )

    def _log_cycle_completed(self, cycle_detail: CycleDetail, timer_strings: list[str]) -> None:
        end_time = cycle_detail.end_time if cycle_detail.end_time is not None else cycle_detail.start_time
        logger.debug(
            f"{self.log_prefix} MaiSaka 轮次结束: 循环编号={cycle_detail.cycle_id} "
            f"总耗时={end_time - cycle_detail.start_time:.2f} 秒; "
            f"阶段耗时={', '.join(timer_strings) if timer_strings else '无'}"
        )

    def _log_history_trimmed(self, removed_count: int, user_message_count: int) -> None:
        logger.debug(
            f"{self.log_prefix} 已裁剪 {removed_count} 条历史消息; "
            # f"剩余计入上下文的消息数={user_message_count}"
        )

    def _log_internal_loop_cancelled(self) -> None:
        logger.info(f"{self.log_prefix} Maisaka 内部循环已取消")
