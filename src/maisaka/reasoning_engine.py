"""Maisaka 推理引擎。"""

from base64 import b64decode
from binascii import Error as BinasciiError
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING, Any, Literal, Optional

from rich.panel import Panel

import asyncio
import difflib
import time

from src.chat.heart_flow.heartFC_utils import CycleDetail
from src.chat.message_receive.message import SessionMessage
from src.cli.console import console
from src.common.data_models.message_component_data_model import EmojiComponent, ImageComponent, MessageSequence, TextComponent
from src.common.logger import get_logger
from src.config.config import global_config
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.learners.behavior_selector import behavior_pattern_selector
from src.llm_models.exceptions import ReqAbortException, RespNotOkException
from src.llm_models.payload_content.tool_option import ToolCall
from src.services import database_service as database_api
from src.maisaka.display.display_utils import format_tool_call_for_display
from src.maisaka.utils.tool_post_execution import handle_tool_post_execution_effects
from src.maisaka.utils.tool_record_payload import build_tool_record_payload, normalize_tool_record_value

from src.maisaka.builtin_tool import (
    build_builtin_tool_handlers as build_split_builtin_tool_handlers,
    get_builtin_tool_visibility,
    is_builtin_tool_in_action_stage,
)
from .chat_loop_service import ChatResponse, MaisakaChatLoopService
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.visual.chat_history_refresher import (
    has_pending_image_recognition,
    log_pending_image_recognition_before_text_planner,
    refresh_chat_history_visual_placeholders,
)
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.context.messages import (
    AssistantMessage,
    ComplexSessionMessage,
    LLMContextMessage,
    ReferenceMessage,
    ReferenceMessageType,
    SessionBackedMessage,
    ToolResultMessage,
    contains_complex_message,
)
from src.maisaka.focus import focus_mode_manager
from src.maisaka.context.post_processor import process_chat_history_after_cycle
from src.maisaka.context.history import (
    TOOL_RESULT_MEDIA_SOURCE_KIND,
    build_prefixed_message_sequence,
    build_session_message_visible_text,
)
from src.maisaka.jargon_context_matcher import (
    build_jargon_reference_message,
    extract_jargon_reference_contents,
)
from src.maisaka.memory.heuristic_injector import heuristic_memory_injector
from src.maisaka.memory.mid_term import (
    build_mid_term_memory_message,
    build_mid_term_memory_reference_message,
    insert_mid_term_memory_message,
    is_mid_term_memory_reference_message,
)
from src.maisaka.monitor.events import (
    emit_planner_finalized,
)
from src.maisaka.memory.person_profile import build_person_profile_injection_messages
from src.maisaka.context.planner_messages import build_planner_user_prefix_from_session_message
from src.maisaka.visual.mode_utils import resolve_enable_visual_planner

if TYPE_CHECKING:
    from .runtime import MaisakaHeartFlowChatting
    from src.maisaka.builtin_tool.provider import BuiltinToolHandler

logger = get_logger("maisaka_reasoning_engine")

HISTORY_DEFERRED_TOOL_RESULT_NAMES = {"wait"}
TOOL_RESULT_MEDIA_TYPES = {"image", "audio", "resource_link", "resource", "binary"}
BEHAVIOR_SELECTOR_CONTEXT_MESSAGE_LIMIT = 8
BEHAVIOR_SELECTOR_CONTEXT_TEXT_LIMIT = 1800
BEHAVIOR_SCENARIO_CONSTRAINT_TEXT = (
    "【行为表现情景分析任务约束】\n"
    "你现在不是主 planner，不要续写聊天、不要判断是否需要回复、不要选择行为表现。\n"
    "你只负责把当前上下文抽象成行为表现检索用的场景画像。\n"
    "只能输出 JSON 对象，字段必须包含 summary、tag_clusters、need、other_traits、confidence；"
    "tag_clusters 只表示领域概念，每项只能包含 tag_name、tag_aliases；"
    "need 单独输出为包含 tag_name、tag_aliases 的对象；"
    "other_traits 表示他人的特点和态度，输出 tag_name、tag_aliases 数组；"
    "不要输出 kind、phase、risk、tags、name 或 cluster_key。"
)


@dataclass(frozen=True, slots=True)
class CycleEnd:
    """内部循环结束原因。"""

    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class TurnStartContext:
    """一次内部循环触发的前置上下文。"""

    cached_messages: list[SessionMessage]
    trigger_message: Optional[SessionMessage]
    timeout_triggered: bool
    proactive_triggered: bool
    silent_reply_frequency: bool


@dataclass(slots=True)
class CycleRuntimeState:
    """一轮内部循环中逐步积累的运行产物。"""

    planner_duration_ms: float = 0.0
    current_stage_started_at: float = 0.0
    action_tool_count: int = 0
    response: Optional[ChatResponse] = None
    planner_extra_lines: list[str] = field(default_factory=list)
    planner_interrupted: bool = False
    cycle_end: CycleEnd = field(default_factory=lambda: CycleEnd("continue", "本轮思考完成，继续后续内部轮次。"))
    tool_result_summaries: list[str] = field(default_factory=list)
    tool_monitor_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PlannerInterruptResult:
    """Planner 打断后的收尾信息。"""

    response: ChatResponse
    extra_lines: list[str]
    retry_messages: list[SessionMessage]


class MaisakaReasoningEngine:
    """负责内部思考、推理与工具执行。"""

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self._last_reasoning_content: str = ""

    @property
    def last_reasoning_content(self) -> str:
        """返回最近一轮思考文本。"""

        return self._last_reasoning_content

    def build_builtin_tool_handlers(self) -> dict[str, "BuiltinToolHandler"]:
        """构造 Maisaka 内置工具处理器映射。

        Returns:
            dict[str, BuiltinToolHandler]: 工具名到处理器的映射。
        """

        return build_split_builtin_tool_handlers(BuiltinToolRuntimeContext(self, self._runtime))

    async def _run_interruptible_planner(
        self,
        *,
        injected_user_messages: Optional[list[str]] = None,
        tail_user_messages: Optional[list[str]] = None,
        tool_definitions: Optional[list[dict[str, Any]]] = None,
    ) -> Any:
        """运行一轮可被新消息打断的主 planner 请求。"""

        interrupt_flag = asyncio.Event()
        interrupted = False
        self._runtime._bind_planner_interrupt_flag(interrupt_flag)
        self._runtime._chat_loop_service.set_interrupt_flag(interrupt_flag)
        try:
            return await self._runtime._chat_loop_service.chat_loop_step(
                self._runtime._chat_history,
                injected_user_messages=injected_user_messages,
                tail_user_messages=tail_user_messages,
                tool_definitions=tool_definitions,
                max_context_size=self._runtime._max_context_size,
            )
        except ReqAbortException:
            interrupted = True
            raise
        finally:
            self._runtime._unbind_planner_interrupt_flag(
                interrupt_flag,
                interrupted=interrupted,
            )
            self._runtime._chat_loop_service.set_interrupt_flag(None)

    async def _run_behavior_scenario_analyzer_sub_agent(
        self,
        system_prompt: str,
        *,
        context_messages: Optional[list[LLMContextMessage]] = None,
    ) -> str:
        """运行行为表现情景分析子代理，并返回文本结果。"""

        constraint_message = ReferenceMessage(
            content=BEHAVIOR_SCENARIO_CONSTRAINT_TEXT,
            timestamp=datetime.now(),
            reference_type=ReferenceMessageType.TOOL_HINT,
            remaining_uses_value=1,
            display_prefix="[行为表现情景分析约束]",
        )
        if context_messages is None:
            response = await self._runtime.run_sub_agent(
                context_message_limit=self._runtime._max_context_size,
                system_prompt=system_prompt,
                request_kind="behavior_scenario_analyzer",
                extra_messages=[constraint_message],
                interrupt_flag=None,
                tool_definitions=[],
            )
        else:
            filtered_context_messages = self._filter_behavior_scenario_context_messages(context_messages)
            sub_agent = MaisakaChatLoopService(
                chat_system_prompt=system_prompt,
                session_id=str(self._runtime.session_id or ""),
                is_group_chat=self._runtime.chat_stream.is_group_session,
                model_task_name="planner",
            )
            response = await sub_agent.chat_loop_step(
                [*filtered_context_messages, constraint_message],
                request_kind="behavior_scenario_analyzer",
                tool_definitions=[],
                max_context_size=self._runtime._max_context_size,
            )
        response_text = (response.content or "").strip()
        self._log_behavior_scenario_prompt_preview(
            response,
            output_content=response_text,
        )
        return response_text

    @staticmethod
    def _filter_behavior_scenario_context_messages(
        context_messages: list[LLMContextMessage],
    ) -> list[LLMContextMessage]:
        """场景概括只看真实聊天消息，不混入参考、assistant 或工具历史。"""

        allowed_source_kinds = {"user", "guided_reply", "outbound_send"}
        return [
            message
            for message in context_messages
            if isinstance(message, SessionBackedMessage) and message.source_kind in allowed_source_kinds
        ]

    def _log_behavior_scenario_prompt_preview(
        self,
        response: ChatResponse,
        *,
        output_content: str,
    ) -> None:
        """保存行为表现情景分析 Prompt 预览，并在控制台输出查看入口。"""

        try:
            prompt_access_panel = PromptCLIVisualizer.build_prompt_access_panel(
                response.request_messages,
                category="behavior_scenario_analyzer",
                chat_id=str(self._runtime.session_id or ""),
                request_kind="behavior_scenario_analyzer",
                selection_reason=(
                    f"会话ID: {self._runtime.session_id}\n"
                    f"会话名称: {self._runtime.session_name}\n"
                    f"模型: {response.model_name or '未知'}\n"
                    f"构建消息数: {response.built_message_count}\n"
                    f"选中历史数: {response.selected_history_count}"
                ),
                output_content=output_content,
                metadata={
                    "model_name": response.model_name,
                    "duration_ms": response.duration_ms,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                },
            )
        except Exception as exc:
            logger.warning(f"{self._runtime.log_prefix} 行为表现情景分析 Prompt 预览保存失败: {exc}")
            return

        console.print(
            Panel(
                prompt_access_panel,
                title=f"{self._runtime.log_prefix} 行为表现情景分析请求预览",
                border_style="bright_magenta",
                padding=(0, 1),
            )
        )
        logger.info(f"{self._runtime.log_prefix} 行为表现情景分析请求预览已生成，已在控制台显示可点击链接")

    def _clear_behavior_reference_messages(
        self,
        history: Optional[list[LLMContextMessage]] = None,
    ) -> list[ReferenceMessage]:
        """清理当前历史中的行为表现参考，下一次裁切会写入新的参考。"""

        target_history = self._runtime._chat_history if history is None else history
        retained_history: list[LLMContextMessage] = []
        removed_messages: list[ReferenceMessage] = []
        for message in target_history:
            if isinstance(message, ReferenceMessage) and message.source == "behavior_pattern":
                removed_messages.append(message)
                continue
            retained_history.append(message)
        if removed_messages:
            target_history[:] = retained_history
        return removed_messages

    def _insert_behavior_reference_message(
        self,
        reference_text: str,
        *,
        history: Optional[list[LLMContextMessage]] = None,
    ) -> Optional[ReferenceMessage]:
        """将行为表现参考插入主循环历史。"""

        normalized_text = reference_text.strip()
        if not normalized_text:
            return None

        message = ReferenceMessage(
            content=normalized_text,
            timestamp=datetime.now(),
            reference_type=ReferenceMessageType.BEHAVIOR_PATTERN,
            remaining_uses_value=None,
            display_prefix="[行为表现参考]",
        )
        if history is None:
            self._runtime._chat_history.append(message)
        else:
            history.append(message)
        return message

    @staticmethod
    def _append_behavior_selector_context_item(
        context_items: list[str],
        *,
        text: str,
        seen_texts: set[str],
    ) -> None:
        normalized_text = " ".join(str(text or "").split()).strip()
        if not normalized_text or normalized_text in seen_texts:
            return
        seen_texts.add(normalized_text)
        context_items.append(normalized_text)

    def _build_behavior_selector_context_text(
        self,
        *,
        source_messages: Optional[list[SessionMessage]] = None,
        selected_history: Optional[list[LLMContextMessage]] = None,
    ) -> str:
        """构造行为表现本地检索使用的最近上下文文本。"""

        context_items: list[str] = []
        seen_texts: set[str] = set()

        if selected_history is not None:
            for history_message in selected_history:
                if not isinstance(history_message, SessionBackedMessage):
                    continue
                if history_message.source_kind not in {"user", "guided_reply", "outbound_send"}:
                    continue
                self._append_behavior_selector_context_item(
                    context_items,
                    text=history_message.processed_plain_text,
                    seen_texts=seen_texts,
                )
        else:
            for message in (source_messages or [])[-BEHAVIOR_SELECTOR_CONTEXT_MESSAGE_LIMIT:]:
                self._append_behavior_selector_context_item(
                    context_items,
                    text=str(message.processed_plain_text or ""),
                    seen_texts=seen_texts,
                )

        if selected_history is None:
            for history_message in reversed(self._runtime._chat_history):
                if len(context_items) >= BEHAVIOR_SELECTOR_CONTEXT_MESSAGE_LIMIT:
                    break
                if not isinstance(history_message, SessionBackedMessage):
                    continue
                if history_message.source_kind not in {"user", "guided_reply", "outbound_send"}:
                    continue
                self._append_behavior_selector_context_item(
                    context_items,
                    text=history_message.processed_plain_text,
                    seen_texts=seen_texts,
                )

        context_text = "\n".join(context_items[-BEHAVIOR_SELECTOR_CONTEXT_MESSAGE_LIMIT:])
        if len(context_text) <= BEHAVIOR_SELECTOR_CONTEXT_TEXT_LIMIT:
            return context_text
        return context_text[-BEHAVIOR_SELECTOR_CONTEXT_TEXT_LIMIT:]

    async def _select_behavior_reference_message(
        self,
        *,
        source_messages: Optional[list[SessionMessage]] = None,
        selected_history: list[LLMContextMessage],
        target_history: Optional[list[LLMContextMessage]] = None,
    ) -> Optional[ReferenceMessage]:
        """基于裁切后的保留上下文刷新行为表现参考。"""

        selection = await behavior_pattern_selector.retrieve_for_planner(
            session_id=str(self._runtime.session_id or ""),
            scenario_agent_runner=lambda system_prompt: self._run_behavior_scenario_analyzer_sub_agent(
                system_prompt,
                context_messages=selected_history,
            ),
            context_text=self._build_behavior_selector_context_text(
                source_messages=source_messages,
                selected_history=selected_history,
            ),
            include_context_in_prompt=False,
        )
        return self._insert_behavior_reference_message(selection.reference_text, history=target_history)

    async def _build_action_tool_definitions(self) -> tuple[list[dict[str, Any]], str]:
        """构造 Action Loop 阶段可见的工具定义与 deferred tools 提示。"""

        if self._runtime._tool_registry is None:
            self._runtime.update_deferred_tool_specs([])
            self._runtime.set_current_action_tool_names([])
            return [], ""

        availability_context = self._build_tool_availability_context()
        tool_specs = await self._runtime._tool_registry.list_tools(availability_context)
        visible_builtin_tool_specs: list[ToolSpec] = []
        deferred_tool_specs: list[ToolSpec] = []
        for tool_spec in tool_specs:
            if tool_spec.provider_name == "maisaka_builtin":
                if not is_builtin_tool_in_action_stage(tool_spec):
                    continue
                visibility = get_builtin_tool_visibility(tool_spec)
                if visibility == "visible":
                    visible_builtin_tool_specs.append(tool_spec)
                elif visibility == "deferred":
                    deferred_tool_specs.append(tool_spec)
                continue
            if str(tool_spec.metadata.get("visibility") or "").strip().lower() == "visible":
                visible_builtin_tool_specs.append(tool_spec)
                continue
            deferred_tool_specs.append(tool_spec)

        self._runtime.update_deferred_tool_specs(deferred_tool_specs)
        selected_history, _ = self._runtime._chat_loop_service.select_llm_context_messages(
            self._runtime._chat_history,
            request_kind="planner",
            max_context_size=self._runtime._max_context_size,
            is_group_chat=self._runtime.chat_stream.is_group_session,
        )
        self._runtime.sync_discovered_deferred_tools_with_context(selected_history)
        discovered_deferred_tool_specs = self._runtime.get_discovered_deferred_tool_specs()
        visible_tool_specs = [*visible_builtin_tool_specs, *discovered_deferred_tool_specs]
        self._runtime.set_current_action_tool_names([tool_spec.name for tool_spec in visible_tool_specs])
        return (
            [tool_spec.to_llm_definition() for tool_spec in visible_tool_specs],
            self._runtime.build_deferred_tools_reminder(),
        )

    async def _build_planner_injected_user_messages(
        self,
        *,
        profile_message: SessionMessage,
        source_messages: list[SessionMessage],
        deferred_tools_reminder: str,
    ) -> list[str]:
        """构造本轮 planner 的一次性注入消息。"""

        injected_messages: list[str] = []
        if deferred_tools_reminder:
            injected_messages.append(deferred_tools_reminder)

        async def build_heuristic_memory_message() -> str:
            try:
                return await heuristic_memory_injector.build_injection_message(
                    session_id=str(self._runtime.session_id or ""),
                )
            except Exception as exc:
                logger.debug(f"{self._runtime.log_prefix} 启发式记忆自然拉起失败，已跳过: {exc}")
                return ""

        async def build_profile_messages() -> list[str]:
            try:
                return await build_person_profile_injection_messages(
                    anchor_message=profile_message,
                    pending_messages=source_messages,
                )
            except Exception as exc:
                logger.debug(f"{self._runtime.log_prefix} 人物画像自动注入失败，已跳过: {exc}")
                return []

        heuristic_memory_message, profile_messages = await asyncio.gather(
            build_heuristic_memory_message(),
            build_profile_messages(),
        )
        if heuristic_memory_message:
            injected_messages.append(heuristic_memory_message)
        injected_messages.extend(profile_messages)
        return injected_messages

    def _refresh_jargon_reference_message(self) -> Optional[ReferenceMessage]:
        """基于当前 planner 上下文刷新黑话参考消息。"""

        existing_jargon_contents = extract_jargon_reference_contents(self._runtime._chat_history)
        selected_history, _ = self._runtime._chat_loop_service.select_llm_context_messages(
            self._runtime._chat_history,
            request_kind="planner",
            max_context_size=self._runtime._max_context_size,
            is_group_chat=self._runtime.chat_stream.is_group_session,
        )
        reference_message = build_jargon_reference_message(
            session_id=str(self._runtime.session_id or ""),
            context_messages=selected_history,
            excluded_contents=existing_jargon_contents,
        )
        if reference_message is None:
            return None
        self._runtime._chat_history.append(reference_message)
        return reference_message

    async def _refresh_mid_term_memory_reference_message(self) -> Optional[ReferenceMessage]:
        """基于当前 planner 上下文刷新聊天回想参考消息。"""

        if not bool(global_config.chat.mid_term_memory):
            return None

        selected_history, _ = self._runtime._chat_loop_service.select_llm_context_messages(
            self._runtime._chat_history,
            request_kind="planner",
            max_context_size=self._runtime._max_context_size,
            is_group_chat=self._runtime.chat_stream.is_group_session,
        )
        reference_message = await build_mid_term_memory_reference_message(
            history=self._runtime._chat_history,
            selected_history=selected_history,
            session_id=str(self._runtime.session_id or ""),
            log_prefix=self._runtime.log_prefix,
        )
        if reference_message is None:
            return None

        if self._has_mid_term_memory_reference_message(reference_message):
            return None

        self._runtime._chat_history.append(reference_message)
        return reference_message

    def _has_mid_term_memory_reference_message(self, reference_message: ReferenceMessage) -> bool:
        """判断历史中是否已经存在相同的聊天回想参考。"""

        normalized_content = " ".join(reference_message.content.split()).strip()
        if not normalized_content:
            return False

        for history_message in self._runtime._chat_history:
            if not is_mid_term_memory_reference_message(history_message):
                continue
            history_content = " ".join(str(history_message.content or "").split()).strip()
            if history_content == normalized_content:
                return True
        return False

    @staticmethod
    def _is_planner_no_tool_hint_message(message: LLMContextMessage) -> bool:
        """判断是否为 Planner 无工具重试提示。"""

        return (
            isinstance(message, ReferenceMessage)
            and message.reference_type == ReferenceMessageType.PLANNER_TOOL_HINT
        )

    def _clear_planner_no_tool_hints(self) -> None:
        """移除已过期的 Planner 无工具重试提示。"""

        self._runtime._chat_history = [
            message
            for message in self._runtime._chat_history
            if not self._is_planner_no_tool_hint_message(message)
        ]

    def _handle_planner_no_tool_retry(
        self,
        planner_no_tool_count: int,
        planner_extra_lines: list[str],
    ) -> tuple[int, CycleEnd, bool]:
        """处理 Planner 未调用工具时的终止策略。"""

        planner_no_tool_count += 1
        cycle_end = CycleEnd("planner_no_tool_end", "Planner本轮思考结束。")
        self._end_planner_no_tool_cycle(
            planner_extra_lines,
            status_line="状态：已结束本轮思考",
        )
        logger.info(f"{self._runtime.log_prefix} Planner 已结束本轮思考")
        return planner_no_tool_count, cycle_end, True

    def _end_planner_no_tool_cycle(
        self,
        planner_extra_lines: list[str],
        *,
        status_line: str,
    ) -> None:
        """结束 Planner 无工具输出导致的当前思考轮。"""

        self._clear_planner_no_tool_hints()
        self._runtime._end_planner_continuation()
        self._runtime._reset_consecutive_wait_count("planner_no_tool_end")
        self._runtime._enter_stop_state()
        planner_extra_lines.append(status_line)

    async def _handle_planner_response_actions(
        self,
        *,
        response: ChatResponse,
        cycle_detail: CycleDetail,
        state: CycleRuntimeState,
        planner_no_tool_count: int,
        planner_extra_lines: list[str],
    ) -> tuple[int, bool]:
        """处理 Planner 响应中的工具调用，或无工具输出策略。"""

        reasoning_content = self._get_effective_planner_thought(response)
        if self._should_replace_reasoning(reasoning_content):
            reasoning_content = "我应该根据我上面思考的内容进行反思，重新思考我下一步的行动，我需要分析当前场景，对话，然后直接输出我的想法："
            response.content = reasoning_content
            response.reasoning = reasoning_content
            response.raw_message.content = reasoning_content
            logger.info(f"{self._runtime.log_prefix} 当前思考与上一轮过于相似，已替换为重新思考提示")

        self._last_reasoning_content = reasoning_content
        self._runtime._chat_history.append(response.raw_message)

        if response.tool_calls:
            planner_no_tool_count = 0
            self._clear_planner_no_tool_hints()
            tool_started_at = time.time()
            (
                should_pause,
                pause_tool_name,
                tool_result_summaries,
                tool_monitor_results,
            ) = await self._handle_tool_calls(
                response.tool_calls,
                reasoning_content,
            )
            cycle_detail.time_records["tool_calls"] = time.time() - tool_started_at
            state.tool_result_summaries = tool_result_summaries
            state.tool_monitor_results = tool_monitor_results
            if should_pause:
                state.cycle_end = self._cycle_end_for_pause_tool(pause_tool_name)
                return planner_no_tool_count, True
            state.cycle_end = CycleEnd("tool_continue", "Planner 工具执行完成，继续下一轮内部思考。")
            return planner_no_tool_count, False

        planner_no_tool_count, cycle_end, should_end_after_no_tool = self._handle_planner_no_tool_retry(
            planner_no_tool_count,
            planner_extra_lines,
        )
        state.cycle_end = cycle_end
        state.tool_result_summaries = []
        state.tool_monitor_results = []
        return planner_no_tool_count, should_end_after_no_tool

    async def _run_planner_request(
        self,
        *,
        trigger_message: SessionMessage,
        source_messages: list[SessionMessage],
        round_index: int,
        round_text: str,
        state: CycleRuntimeState,
    ) -> None:
        """准备上下文并执行一次 Planner 请求。"""

        planner_started_at = time.time()
        self._runtime._update_stage_status("Planner", "组织上下文并请求模型", round_text=round_text)
        action_tool_definitions, deferred_tools_reminder = await self._build_action_tool_definitions()
        try:
            jargon_reference_message = self._refresh_jargon_reference_message()
            if jargon_reference_message is not None:
                logger.debug(f"{self._runtime.log_prefix} 已刷新黑话参考消息")
        except Exception as exc:
            logger.debug(f"{self._runtime.log_prefix} 黑话参考消息刷新失败，已跳过: {exc}")
        injected_user_messages = await self._build_planner_injected_user_messages(
            profile_message=trigger_message,
            source_messages=source_messages,
            deferred_tools_reminder=deferred_tools_reminder,
        )
        if not resolve_enable_visual_planner():
            log_pending_image_recognition_before_text_planner(
                self._runtime._chat_history,
                log_prefix=self._runtime.log_prefix,
            )
        logger.info(
            f"{self._runtime.log_prefix} 开始思考: "
            f"第 {round_index + 1} 轮"
            f"消息数={len(self._runtime._chat_history)} "
            f"开始时间={planner_started_at:.3f}"
        )
        state.current_stage_started_at = planner_started_at
        state.action_tool_count = len(action_tool_definitions)
        response = await self._run_interruptible_planner(
            injected_user_messages=injected_user_messages or None,
            tail_user_messages=self._runtime.build_focus_tail_user_messages() or None,
            tool_definitions=action_tool_definitions,
        )
        state.response = response
        state.planner_duration_ms = (time.time() - planner_started_at) * 1000

    async def _refresh_mid_term_memory_reference_for_continuation(self, cycle_detail: CycleDetail) -> None:
        """在一次连续 Planner 循环开始前刷新一次聊天回想参考。"""

        started_at = time.time()
        try:
            mid_term_reference_message = await self._refresh_mid_term_memory_reference_message()
            if mid_term_reference_message is not None:
                logger.debug(f"{self._runtime.log_prefix} 已刷新聊天回想参考消息")
        except Exception as exc:
            logger.debug(f"{self._runtime.log_prefix} 聊天回想参考刷新失败，已跳过: {exc}", exc_info=True)
        finally:
            cycle_detail.time_records["mid_term_memory_recall"] = time.time() - started_at

    async def _handle_planner_interrupt(
        self,
        *,
        exc: ReqAbortException,
        round_index: int,
        round_text: str,
        current_stage_started_at: float,
        action_tool_count: int,
    ) -> PlannerInterruptResult:
        """处理 Planner 流式请求被新消息打断后的监控响应与重试消息。"""

        self._runtime._update_stage_status(
            "Planner 已打断",
            str(exc) or "收到外部中断信号",
            round_text=round_text,
        )
        interrupted_at = time.time()
        interrupted_text = "Planner 收到新消息，开始重新决策"
        interrupted_response = ChatResponse(
            content=interrupted_text,
            tool_calls=[],
            request_messages=[],
            raw_message=AssistantMessage(
                content=interrupted_text,
                timestamp=datetime.now(),
                tool_calls=[],
                source_kind="perception",
            ),
            selected_history_count=len(self._runtime._chat_history),
            tool_count=action_tool_count,
            prompt_tokens=0,
            built_message_count=0,
            completion_tokens=0,
            total_tokens=0,
            model_name="",
            prompt_section=None,
            reasoning="",
        )
        extra_lines = [
            "状态：已被新消息打断",
            "打断位置：Planner 请求流式响应阶段",
            f"打断耗时：{interrupted_at - current_stage_started_at:.3f} 秒",
        ]
        logger.info(
            f"{self._runtime.log_prefix} Planner 打断成功: "
            f"回合={round_index + 1} "
            f"开始时间={current_stage_started_at:.3f} "
            f"打断时间={interrupted_at:.3f} "
            f"耗时={interrupted_at - current_stage_started_at:.3f} 秒"
        )
        if not self._runtime._has_pending_messages() or round_index >= self._runtime._max_internal_rounds:
            return PlannerInterruptResult(interrupted_response, extra_lines, [])

        await self._runtime._wait_for_message_quiet_period()
        self._runtime._mark_message_turn_unscheduled()
        interrupted_messages = self._runtime._collect_pending_messages()
        if not interrupted_messages:
            return PlannerInterruptResult(interrupted_response, extra_lines, [])

        await self._ingest_messages(interrupted_messages)
        logger.info(
            f"{self._runtime.log_prefix} 保持活跃状态，直接重试 Planner: "
            f"回合={round_index + 2}"
        )
        return PlannerInterruptResult(interrupted_response, extra_lines, interrupted_messages)

    @staticmethod
    def _get_effective_planner_thought(response: ChatResponse) -> str:
        """获取本轮 planner 可用于工具上下文的思考文本。"""

        reasoning_content = str(response.reasoning or "").strip()
        if reasoning_content:
            return reasoning_content
        return str(response.content or "").strip()

    @staticmethod
    def _cycle_end_for_pause_tool(pause_tool_name: Optional[str]) -> CycleEnd:
        """返回工具要求暂停时对应的结束原因。"""

        if pause_tool_name == "wait":
            return CycleEnd("tool_pause:wait", "Planner 调用 wait，本轮暂停并在等待结束后继续判断。")
        if pause_tool_name == "wait_rest":
            return CycleEnd("planner_wait_rest", "Planner 连续 wait 达到上限，本轮进入休息并等待新消息。")
        if pause_tool_name:
            return CycleEnd(f"tool_pause:{pause_tool_name}", f"工具 {pause_tool_name} 要求暂停当前思考循环。")
        return CycleEnd("tool_pause", "工具要求暂停当前思考循环。")

    @staticmethod
    def _cycle_end_for_max_rounds(max_internal_rounds: int) -> CycleEnd:
        """返回达到内部思考轮次上限时的结束原因。"""

        return CycleEnd("max_rounds", f"已达到内部思考轮次上限 {max_internal_rounds}，本轮处理结束。")

    async def _collect_pending_messages_before_next_round(self, round_index: int) -> list[SessionMessage]:
        """在后续内部轮次开始前合并新消息。"""

        if round_index <= 0 or not self._runtime._has_pending_messages():
            return []

        await self._runtime._wait_for_message_quiet_period()
        self._runtime._mark_message_turn_unscheduled()
        pending_round_messages = self._runtime._collect_pending_messages()
        if pending_round_messages:
            await self._ingest_messages(pending_round_messages)
            logger.info(
                f"{self._runtime.log_prefix} 内部轮次开始前已合并新消息: "
                f"消息数={len(pending_round_messages)} 回合={round_index + 1}"
            )
        return pending_round_messages

    async def _start_cycle_round(self, round_index: int) -> tuple[CycleDetail, str]:
        """启动一轮内部循环。"""

        cycle_detail = self._start_cycle()
        round_text = f"第 {round_index + 1}/{self._runtime._max_internal_rounds} 轮"
        self._runtime._log_cycle_started(cycle_detail, round_index)
        self._runtime._update_stage_status("启动循环", f"循环 {cycle_detail.cycle_id}", round_text=round_text)
        return cycle_detail, round_text

    async def _refresh_visual_placeholders_for_cycle(self, cycle_detail: CycleDetail) -> None:
        """刷新本轮开始前可用的视觉占位消息。"""

        visual_refresh_started_at = time.time()
        refreshed_message_count = await self._refresh_chat_history_visual_placeholders()
        cycle_detail.time_records["visual_refresh"] = time.time() - visual_refresh_started_at
        if refreshed_message_count > 0:
            logger.info(f"{self._runtime.log_prefix} 本轮思考前已刷新 {refreshed_message_count} 条视觉占位历史消息")

    async def _prepare_turn_start_context(
        self,
        queued_trigger: Literal["message", "timeout", "proactive"],
    ) -> TurnStartContext:
        """消费触发信号并准备本轮初始消息与触发消息。"""

        message_triggered, timeout_triggered, proactive_triggered = self._drain_ready_turn_triggers(queued_trigger)
        if proactive_triggered:
            self._runtime._clear_focus_cooldown_wakeup_scheduled()
        silent_reply_frequency = self._runtime._is_reply_frequency_silent()

        if (
            self._runtime._agent_state == self._runtime._STATE_WAIT
            and not (timeout_triggered or proactive_triggered)
            and not silent_reply_frequency
        ):
            self._runtime._mark_message_turn_unscheduled()
            logger.debug(f"{self._runtime.log_prefix} 当前仍处于 wait 状态，忽略消息触发并继续等待超时")
            return TurnStartContext([], None, timeout_triggered, proactive_triggered, silent_reply_frequency)

        if message_triggered:
            await self._runtime._wait_for_message_quiet_period()
            self._runtime._mark_message_turn_unscheduled()

        cached_messages = self._runtime._collect_pending_messages() if self._runtime._has_pending_messages() else []
        if cached_messages:
            self._runtime._enter_running_state()
            self._runtime._update_stage_status(
                "消息整理",
                f"待处理消息 {len(cached_messages)} 条",
            )
            if self._runtime._has_pending_wait_tool_call():
                self._runtime._chat_history.append(self._build_wait_completed_message(has_new_messages=True))
            await self._ingest_messages(cached_messages)
            trigger_message = cached_messages[-1]
        else:
            trigger_message = (
                self._runtime._consume_proactive_trigger_message()
                if proactive_triggered
                else self._runtime.message_cache[-1]
                if self._runtime.message_cache
                else None
            )
            if trigger_message is None:
                logger.warning(f"{self._runtime.log_prefix} wait 超时后没有可复用的触发消息，跳过本轮")
                return TurnStartContext([], None, timeout_triggered, proactive_triggered, silent_reply_frequency)
            logger.info(f"{self._runtime.log_prefix} 等待结束，再看一眼！")
            if self._runtime._has_pending_wait_tool_call():
                self._runtime._chat_history.append(self._build_wait_completed_message(has_new_messages=False))

        return TurnStartContext(
            cached_messages,
            trigger_message,
            timeout_triggered,
            proactive_triggered,
            silent_reply_frequency,
        )

    async def _finalize_cycle(
        self,
        *,
        cycle_detail: CycleDetail,
        round_index: int,
        state: CycleRuntimeState,
    ) -> CycleEnd:
        """渲染并广播一次内部循环的收尾信息。"""

        completed_cycle = await self._end_cycle(cycle_detail)
        cycle_end = state.cycle_end
        if (
            round_index + 1 >= self._runtime._max_internal_rounds
            and cycle_end.reason in {"continue", "tool_continue"}
        ):
            cycle_end = self._cycle_end_for_max_rounds(self._runtime._max_internal_rounds)

        response = state.response
        self._runtime._render_context_usage_panel(
            cycle_id=cycle_detail.cycle_id,
            time_records=dict(completed_cycle.time_records),
            planner_selected_history_count=response.selected_history_count if response is not None else None,
            planner_prompt_tokens=response.prompt_tokens if response is not None else None,
            planner_model_name=response.model_name if response is not None else None,
            planner_response=(response.content or "") if response is not None else "",
            planner_tool_calls=response.tool_calls if response is not None else None,
            planner_tool_results=state.tool_result_summaries,
            planner_tool_detail_results=state.tool_monitor_results,
            planner_prompt_section=response.prompt_section if response is not None else None,
            planner_extra_lines=state.planner_extra_lines,
        )
        await emit_planner_finalized(
            session_id=self._runtime.session_id,
            cycle_id=cycle_detail.cycle_id,
            planner_request_messages=response.request_messages if response is not None else None,
            planner_selected_history_count=response.selected_history_count if response is not None else None,
            planner_tool_count=response.tool_count if response is not None else None,
            planner_content=response.content if response is not None else None,
            planner_tool_calls=response.tool_calls if response is not None else None,
            planner_prompt_tokens=response.prompt_tokens if response is not None else None,
            planner_completion_tokens=response.completion_tokens if response is not None else None,
            planner_total_tokens=response.total_tokens if response is not None else None,
            planner_duration_ms=state.planner_duration_ms if response is not None else None,
            planner_prompt_html_uri=response.prompt_html_uri if response is not None else None,
            tools=state.tool_monitor_results,
            time_records=dict(completed_cycle.time_records),
            agent_state=self._runtime._agent_state,
            planner_interrupted=state.planner_interrupted,
            end_reason=cycle_end.reason,
            end_detail=cycle_end.detail,
        )
        self._runtime._idle_backoff.record_cycle_result(cycle_end.reason)
        self._runtime.record_idle_cycle_result(cycle_end.reason)
        return cycle_end

    async def run_loop(self) -> None:
        """独立消费消息批次，并执行对应的内部思考轮次。"""
        try:
            while self._runtime._running:
                queued_trigger = await self._runtime._internal_turn_queue.get()
                if not focus_mode_manager.can_decide(
                    self._runtime.session_id,
                    is_group_chat=self._runtime.chat_stream.is_group_session,
                ):
                    self._runtime._mark_message_turn_unscheduled()
                    logger.debug(f"{self._runtime.log_prefix} 当前不在 focus 状态，忽略已排队的 Maisaka 触发")
                    continue

                turn_start_context = await self._prepare_turn_start_context(queued_trigger)
                if turn_start_context.trigger_message is None:
                    continue

                cached_messages = turn_start_context.cached_messages
                trigger_message = turn_start_context.trigger_message

                if turn_start_context.silent_reply_frequency:
                    await self._handle_silent_turn(
                        cached_messages=cached_messages,
                        timeout_triggered=turn_start_context.timeout_triggered,
                        proactive_triggered=turn_start_context.proactive_triggered,
                    )
                    continue

                try:
                    if force_continue_reason := self._runtime._consume_forced_turn_reason():
                        logger.info(f"{self._runtime.log_prefix} {force_continue_reason}")
                    planner_no_tool_count = 0
                    mid_term_reference_refreshed = False
                    round_index = 0
                    while round_index < self._runtime._max_internal_rounds:
                        pending_round_messages = await self._collect_pending_messages_before_next_round(round_index)
                        if pending_round_messages:
                            cached_messages = pending_round_messages
                            trigger_message = pending_round_messages[-1]

                        cycle_detail, round_text = await self._start_cycle_round(round_index)
                        state = CycleRuntimeState()
                        try:
                            await self._refresh_visual_placeholders_for_cycle(cycle_detail)
                            if not mid_term_reference_refreshed:
                                await self._refresh_mid_term_memory_reference_for_continuation(cycle_detail)
                                mid_term_reference_refreshed = True

                            self._runtime._start_planner_continuation()
                            await self._run_planner_request(
                                trigger_message=trigger_message,
                                source_messages=cached_messages or [trigger_message],
                                round_index=round_index,
                                round_text=round_text,
                                state=state,
                            )
                            cycle_detail.time_records["planner"] = state.planner_duration_ms / 1000
                            # logger.info(
                            #     f"{self._runtime.log_prefix} 规划器执行完成: "
                            #     f"回合={round_index + 1} "
                            #     f"耗时={cycle_detail.time_records['planner']:.3f} 秒"
                            # )
                            planner_no_tool_count, should_break_after_action = await self._handle_planner_response_actions(
                                response=state.response,
                                cycle_detail=cycle_detail,
                                state=state,
                                planner_no_tool_count=planner_no_tool_count,
                                planner_extra_lines=state.planner_extra_lines,
                            )
                            if should_break_after_action:
                                break
                            continue
                        except ReqAbortException as exc:
                            state.planner_interrupted = True
                            state.cycle_end = CycleEnd("planner_interrupted", "Planner 被新消息打断，当前轮结束。")
                            interrupt_result = await self._handle_planner_interrupt(
                                exc=exc,
                                round_index=round_index,
                                round_text=round_text,
                                current_stage_started_at=state.current_stage_started_at,
                                action_tool_count=state.action_tool_count,
                            )
                            state.response = interrupt_result.response
                            state.planner_extra_lines = interrupt_result.extra_lines
                            if not interrupt_result.retry_messages:
                                break

                            cached_messages = interrupt_result.retry_messages
                            trigger_message = interrupt_result.retry_messages[-1]
                            continue
                        finally:
                            state.cycle_end = await self._finalize_cycle(
                                cycle_detail=cycle_detail,
                                round_index=round_index,
                                state=state,
                            )
                            if not state.planner_interrupted:
                                round_index += 1
                finally:
                    if self._runtime._agent_state == self._runtime._STATE_RUNNING:
                        self._runtime._enter_stop_state()
                    if self._runtime._running:
                        self._runtime._update_stage_status("等待消息", "本轮处理结束")
        except asyncio.CancelledError:
            self._runtime._log_internal_loop_cancelled()
            raise
        except RespNotOkException as exc:
            self._runtime._update_stage_status(
                "错误",
                f"模型响应异常 HTTP {exc.status_code} - {exc}",
            )
            logger.error(
                f"{self._runtime.log_prefix} Maisaka 内部循环发生异常: "
                f"模型响应异常 HTTP {exc.status_code} - {exc}"
            )
            raise
        except Exception as exc:
            self._runtime._update_stage_status("错误", str(exc))
            logger.exception(f"{self._runtime.log_prefix} Maisaka 内部循环发生异常")
            raise

    async def _handle_silent_turn(
        self,
        *,
        cached_messages: list[SessionMessage],
        timeout_triggered: bool,
        proactive_triggered: bool,
    ) -> None:
        """回复频率为 0 时只消费消息和维护历史，不进入 Planner。"""

        self._runtime._clear_forced_turn_state()
        if proactive_triggered:
            self._runtime._consume_proactive_trigger_message()

        cycle_detail = CycleDetail(cycle_id=self._runtime._cycle_counter)
        await self._post_process_chat_history_after_cycle(
            cycle_detail,
            enable_mid_term_memory=False,
        )
        self._runtime._enter_stop_state()
        if self._runtime._running:
            self._runtime._update_stage_status("等待消息", "回复频率为 0，已静默接收消息")

        trigger_labels: list[str] = []
        if cached_messages:
            trigger_labels.append(f"消息={len(cached_messages)}")
        if timeout_triggered:
            trigger_labels.append("wait_timeout")
        if proactive_triggered:
            trigger_labels.append("proactive")
        trigger_text = " ".join(trigger_labels) if trigger_labels else "无新消息"
        logger.info(
            f"{self._runtime.log_prefix} 回复频率为 0，静默接收并完成历史维护，"
            f"不进入 Planner；{trigger_text}"
        )

    def _drain_ready_turn_triggers(
        self,
        queued_trigger: Literal["message", "timeout", "proactive"],
    ) -> tuple[bool, bool, bool]:
        """合并当前已就绪的消息触发信号。"""

        message_triggered = queued_trigger == "message"
        timeout_triggered = queued_trigger == "timeout"
        proactive_triggered = queued_trigger == "proactive"

        while True:
            try:
                next_trigger = self._runtime._internal_turn_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if next_trigger == "message":
                message_triggered = True
                continue
            if next_trigger == "timeout":
                timeout_triggered = True
                continue
            if next_trigger == "proactive":
                proactive_triggered = True
                continue

        return message_triggered, timeout_triggered, proactive_triggered

    def _build_wait_completed_message(self, *, has_new_messages: bool) -> ToolResultMessage:
        """构造 wait 完成后的工具结果消息。"""
        tool_call_id, elapsed_seconds, requested_seconds = self._runtime._consume_pending_wait_state()
        elapsed_text = f"{elapsed_seconds:.1f} 秒"
        requested_text = f"，原计划等待 {requested_seconds:.1f} 秒" if requested_seconds is not None else ""
        content = (
            f"等待已结束，实际等待 {elapsed_text}{requested_text}，期间收到了新的用户输入。请结合这些新消息继续下一轮思考。"
            if has_new_messages
            else f"等待已超时，实际等待 {elapsed_text}{requested_text}，期间没有收到新的用户输入。请基于现有上下文继续下一轮思考。"
        )
        return ToolResultMessage(
            content=content,
            timestamp=datetime.now(),
            tool_call_id=tool_call_id,
            tool_name="wait",
        )

    async def _ingest_messages(self, messages: list[SessionMessage]) -> None:
        """处理传入消息列表，将其转换为历史消息并加入聊天历史缓存。"""
        for message in messages:
            history_message = await self._build_history_message(message)
            if history_message is None:
                continue

            self._insert_chat_history_message(history_message)

    async def _build_history_message(
        self,
        message: SessionMessage,
        *,
        source_kind: str = "user",
    ) -> Optional[LLMContextMessage]:
        """根据真实消息构造对应的上下文消息。"""

        source_sequence = message.raw_message
        visible_text = self._build_legacy_visible_text(message, source_sequence, source_kind=source_kind)
        include_chat_id = self._runtime._is_focus_mode_active_for_current_chat()
        planner_prefix = build_planner_user_prefix_from_session_message(
            message,
            include_chat_id=include_chat_id,
            is_self_message=source_kind == "guided_reply" and global_config.chat.self_message_special_mark,
        )
        if contains_complex_message(source_sequence):
            return ComplexSessionMessage.from_session_message(
                message,
                planner_prefix=planner_prefix,
                visible_text=visible_text,
                source_kind=source_kind,
            )

        user_sequence = await self._build_message_sequence(message, planner_prefix=planner_prefix)
        if not user_sequence.components:
            return None

        return SessionBackedMessage.from_session_message(
            message,
            raw_message=user_sequence,
            visible_text=visible_text,
            source_kind=source_kind,
        )

    async def _build_message_sequence(
        self,
        message: SessionMessage,
        *,
        planner_prefix: str,
    ) -> MessageSequence:
        message_sequence = build_prefixed_message_sequence(message.raw_message, planner_prefix)
        if resolve_enable_visual_planner():
            await self._hydrate_visual_components(message_sequence.components)
        return message_sequence

    async def _hydrate_visual_components(self, planner_components: list[object]) -> None:
        """在 Maisaka 真正需要图片或表情时，按需回填二进制数据。"""
        load_tasks: list[asyncio.Task[None]] = []
        for component in planner_components:
            if isinstance(component, ImageComponent) and not component.binary_data:
                load_tasks.append(asyncio.create_task(component.load_image_binary()))
                continue
            if isinstance(component, EmojiComponent) and not component.binary_data:
                load_tasks.append(asyncio.create_task(component.load_emoji_binary()))

        if not load_tasks:
            return

        results = await asyncio.gather(*load_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"{self._runtime.log_prefix} 回填图片或表情二进制数据失败，Maisaka 将退化为文本占位: {result}")

    async def _refresh_chat_history_visual_placeholders(self) -> int:
        """在进入新一轮规划前，尝试用已完成的识图结果刷新历史占位。"""

        refreshed_count = await self._refresh_chat_history_visual_placeholders_once()
        wait_seconds = self._resolve_image_recognition_wait_seconds()
        if wait_seconds <= 0:
            return refreshed_count

        deadline = time.monotonic() + wait_seconds
        while has_pending_image_recognition(self._runtime._chat_history):
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break

            await asyncio.sleep(min(0.2, remaining_seconds))
            refreshed_count += await self._refresh_chat_history_visual_placeholders_once()

        refreshed_count += await self._refresh_chat_history_visual_placeholders_once()
        return refreshed_count

    def _resolve_image_recognition_wait_seconds(self) -> float:
        if resolve_enable_visual_planner():
            return 0.0

        try:
            wait_seconds = float(global_config.visual.wait_image_recognize_max_time)
        except (TypeError, ValueError):
            return 0.0

        return max(0.0, wait_seconds)

    async def _refresh_chat_history_visual_placeholders_once(self) -> int:
        return await refresh_chat_history_visual_placeholders(
            chat_history=self._runtime._chat_history,
            build_history_message=lambda message, source_kind: self._build_history_message(
                message,
                source_kind=source_kind,
            ),
            build_visible_text=lambda message, source_kind: self._build_legacy_visible_text(
                message,
                message.raw_message,
                source_kind=source_kind,
            ),
        )

    def _build_legacy_visible_text(
        self,
        message: SessionMessage,
        source_sequence: MessageSequence,
        *,
        source_kind: str = "user",
    ) -> str:
        return build_session_message_visible_text(
            message,
            source_sequence,
            include_reply_components=source_kind != "guided_reply",
        )

    def _insert_chat_history_message(self, message: LLMContextMessage) -> int:
        """将消息按处理顺序追加到聊天历史末尾。"""
        self._runtime._chat_history.append(message)
        return len(self._runtime._chat_history) - 1

    def _start_cycle(self) -> CycleDetail:
        """开始一轮 Maisaka 思考循环。"""
        self._runtime._cycle_counter += 1
        focus_mode_manager.mark_cycle(self._runtime.session_id)
        self._runtime._arm_focus_cooldown_timer()
        self._runtime._current_cycle_detail = CycleDetail(cycle_id=self._runtime._cycle_counter)
        self._runtime._current_cycle_detail.thinking_id = f"maisaka_tid{round(time.time(), 2)}"
        return self._runtime._current_cycle_detail

    async def _end_cycle(self, cycle_detail: CycleDetail, only_long_execution: bool = True) -> CycleDetail:
        """结束并记录一轮 Maisaka 思考循环。"""
        self._runtime.history_loop.append(cycle_detail)
        await self._post_process_chat_history_after_cycle(cycle_detail)
        cycle_detail.end_time = time.time()

        timer_strings = [
            f"{name}: {duration:.2f}s"
            for name, duration in cycle_detail.time_records.items()
            if not only_long_execution or duration >= 0.1
        ]
        self._runtime._log_cycle_completed(cycle_detail, timer_strings)
        return cycle_detail

    async def _post_process_chat_history_after_cycle(
        self,
        cycle_detail: CycleDetail,
        *,
        enable_mid_term_memory: bool = True,
    ) -> None:
        """裁剪聊天历史，保证用户消息数量不超过配置限制。"""
        process_result = process_chat_history_after_cycle(
            self._runtime._chat_history,
            max_context_size=self._runtime._max_context_size,
            enable_context_optimization=global_config.chat.enable_context_optimization,
        )
        if process_result.changed_count <= 0:
            return

        final_history = process_result.history
        if (
            process_result.removed_messages
            and enable_mid_term_memory
            and bool(global_config.chat.mid_term_memory)
        ):
            logger.info(
                f"{self._runtime.log_prefix} 开始生成聊天回想: "
                f"裁切上下文消息数量={len(process_result.removed_messages)} "
                f"保留上限={global_config.chat.mid_term_memory_lenth}"
            )
            summary_started_at = time.time()
            try:
                summary_result = await build_mid_term_memory_message(
                    process_result.removed_messages,
                    session_id=self._runtime.session_id,
                    log_prefix=self._runtime.log_prefix,
                )
            except Exception:
                logger.exception(f"{self._runtime.log_prefix} 生成聊天回想失败，已跳过本次插入")
                summary_result = None

            cycle_detail.time_records["mid_term_memory"] = time.time() - summary_started_at
            if summary_result is not None:
                final_history = insert_mid_term_memory_message(
                    final_history,
                    summary_result.message,
                    max_summary_count=max(0, int(global_config.chat.mid_term_memory_lenth)),
                )
                logger.info(
                    f"{self._runtime.log_prefix} 已生成聊天回想: "
                    f"msg_id={summary_result.message.message_id} "
                    f"模型={summary_result.model_name or 'unknown'} "
                    f"token={summary_result.total_tokens}"
                )
            else:
                logger.debug(f"{self._runtime.log_prefix} 聊天回想未产生可插入内容，已跳过")
        elif process_result.removed_messages:
            logger.debug(f"{self._runtime.log_prefix} 聊天回想未启用，跳过生成")

        removed_behavior_reference_messages: list[ReferenceMessage] = []
        if process_result.removed_messages:
            removed_behavior_reference_messages = self._clear_behavior_reference_messages(final_history)
            try:
                reference_message = await self._select_behavior_reference_message(
                    selected_history=final_history,
                    target_history=final_history,
                )
                if reference_message is not None:
                    logger.debug(f"{self._runtime.log_prefix} 裁切后行为表现参考已刷新")
            except Exception as exc:
                logger.debug(f"{self._runtime.log_prefix} 裁切后行为表现参考刷新失败，已跳过: {exc}")

        self._runtime._chat_history = final_history
        if process_result.removed_count <= 0:
            return
        self._runtime._log_history_trimmed(
            process_result.removed_count,
            process_result.remaining_context_count,
        )
        if process_result.removed_messages:
            learning_messages = [
                *removed_behavior_reference_messages,
                *process_result.removed_messages,
            ]
            asyncio.create_task(
                self._runtime._trigger_trimmed_history_learning(learning_messages)
            )

    @staticmethod
    def _calculate_similarity(text1: str, text2: str) -> float:
        """计算两个文本之间的相似度。

        Args:
            text1: 第一个文本
            text2: 第二个文本

        Returns:
            float: 相似度值，范围 0-1，1 表示完全相同
        """
        return difflib.SequenceMatcher(None, text1, text2).ratio()

    def _should_replace_reasoning(self, current_content: str) -> bool:
        """判断是否需要替换推理内容。

        当当前推理内容与上一次相似度大于90%时，返回True。

        Args:
            current_content: 当前的推理内容

        Returns:
            bool: 是否需要替换
        """
        if not self._last_reasoning_content or not current_content:
            logger.debug(
                f"{self._runtime.log_prefix} 跳过思考相似度判定: "
                f"上一轮为空={not bool(self._last_reasoning_content)} "
                f"当前为空={not bool(current_content)} 相似度=0.00"
            )
            return False

        similarity = self._calculate_similarity(current_content, self._last_reasoning_content)
        logger.debug(f"{self._runtime.log_prefix} 思考内容相似度: {similarity:.2f}")
        return similarity > 0.9

    def _build_tool_invocation(self, tool_call: ToolCall, latest_thought: str) -> ToolInvocation:
        """将模型输出的工具调用转换为统一调用对象。

        Args:
            tool_call: 模型返回的工具调用。
            latest_thought: 当前轮的最新思考文本。

        Returns:
            ToolInvocation: 统一工具调用对象。
        """

        return ToolInvocation(
            tool_name=tool_call.func_name,
            arguments=dict(tool_call.args or {}),
            call_id=tool_call.call_id,
            session_id=self._runtime.session_id,
            stream_id=self._runtime.session_id,
            reasoning=latest_thought,
        )

    def _log_tool_call_source(self, tool_call: ToolCall, *, stage: str) -> None:
        """记录工具调用来自正文还是推理内容。"""

        normalized_tool_call = format_tool_call_for_display(tool_call)
        source = str(normalized_tool_call.get("source") or "").strip()
        source_label = str(normalized_tool_call.get("source_label") or "").strip() or "未知来源"
        if source == "reasoning":
            logger.info(
                f"{self._runtime.log_prefix} [推理中工具调用] {stage}: "
                f"工具={tool_call.func_name} 调用ID={tool_call.call_id}"
            )
            return
        if source == "response":
            logger.info(
                f"{self._runtime.log_prefix} [正文工具调用] {stage}: "
                f"工具={tool_call.func_name} 调用ID={tool_call.call_id}"
            )
            return
        logger.info(
            f"{self._runtime.log_prefix} [工具调用来源:{source_label}] {stage}: "
            f"工具={tool_call.func_name} 调用ID={tool_call.call_id}"
        )

    def _build_tool_availability_context(self) -> ToolAvailabilityContext:
        """构造当前聊天的工具暴露上下文。"""

        chat_stream = self._runtime.chat_stream
        return ToolAvailabilityContext(
            session_id=self._runtime.session_id,
            stream_id=self._runtime.session_id,
            is_group_chat=chat_stream.is_group_session,
            group_id=str(getattr(chat_stream, "group_id", "") or "").strip(),
            user_id=str(getattr(chat_stream, "user_id", "") or "").strip(),
            platform=str(getattr(chat_stream, "platform", "") or "").strip(),
        )

    def _build_tool_execution_context(
        self,
        latest_thought: str,
    ) -> ToolExecutionContext:
        """构造统一工具执行上下文。

        Args:
            latest_thought: 当前轮的最新思考文本。

        Returns:
            ToolExecutionContext: 统一工具执行上下文。
        """

        chat_stream = self._runtime.chat_stream
        return ToolExecutionContext(
            session_id=self._runtime.session_id,
            stream_id=self._runtime.session_id,
            reasoning=latest_thought,
            is_group_chat=chat_stream.is_group_session,
            group_id=str(getattr(chat_stream, "group_id", "") or "").strip(),
            user_id=str(getattr(chat_stream, "user_id", "") or "").strip(),
            platform=str(getattr(chat_stream, "platform", "") or "").strip(),
        )

    @staticmethod
    def _truncate_tool_record_text(text: str, max_length: int = 180) -> str:
        """截断工具记录中的展示文本。

        Args:
            text: 原始文本。
            max_length: 最长保留字符数。

        Returns:
            str: 截断后的文本。
        """

        normalized_text = text.strip()
        if len(normalized_text) <= max_length:
            return normalized_text
        return f"{normalized_text[: max_length - 1]}…"

    async def _store_tool_execution_record(
        self,
        invocation: ToolInvocation,
        result: ToolExecutionResult,
        tool_spec: Optional[ToolSpec],
    ) -> Optional[dict[str, Any]]:
        """将工具执行结果落库到统一工具记录表。

        Args:
            invocation: 工具调用对象。
            result: 工具执行结果。
            tool_spec: 对应的工具声明。

        Returns:
            数据库保存后的工具记录；保存失败时返回 None。
        """

        if self._runtime.chat_stream is None:
            logger.debug(
                f"{self._runtime.log_prefix} 当前没有 chat_stream，跳过工具记录存储: "
                f"工具={invocation.tool_name}"
            )
            return None

        try:
            tool_record_payload = build_tool_record_payload(invocation, result, tool_spec)
            saved_record = await database_api.store_tool_info(
                chat_stream=self._runtime.chat_stream,
                tool_id=invocation.call_id,
                tool_data=tool_record_payload,
                tool_name=invocation.tool_name,
                tool_reasoning=invocation.reasoning,
            )
        except Exception:
            logger.exception(
                f"{self._runtime.log_prefix} 写入工具记录失败: 工具={invocation.tool_name} 调用编号={invocation.call_id}"
            )
            return None

        return saved_record if isinstance(saved_record, dict) else None

    async def _record_tool_execution_effects(
        self,
        invocation: ToolInvocation,
        result: ToolExecutionResult,
        tool_spec: Optional[ToolSpec],
    ) -> None:
        """落库工具记录并执行工具后置副作用。"""

        saved_record = await self._store_tool_execution_record(invocation, result, tool_spec)
        await handle_tool_post_execution_effects(
            invocation=invocation,
            result=result,
            saved_record=saved_record,
            chat_stream=self._runtime.chat_stream,
            log_prefix=self._runtime.log_prefix,
        )

    def _append_tool_execution_result(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
        *,
        append_post_history: bool = True,
    ) -> None:
        """将统一工具执行结果写回 Maisaka 历史。

        Args:
            tool_call: 原始工具调用对象。
            result: 统一工具执行结果。
        """

        if (
            tool_call.func_name in HISTORY_DEFERRED_TOOL_RESULT_NAMES
            and result.success
            and bool(result.metadata.get("pause_execution", False))
        ):
            return

        history_content = self._build_tool_result_history_content(tool_call, result)
        if not history_content:
            history_content = "工具执行成功。" if result.success else f"工具 {tool_call.func_name} 执行失败。"

        normalized_metadata = normalize_tool_record_value(result.metadata)
        if not isinstance(normalized_metadata, dict):
            normalized_metadata = {}

        self._runtime._chat_history.append(
            ToolResultMessage(
                content=history_content,
                timestamp=datetime.now(),
                tool_call_id=tool_call.call_id,
                tool_name=tool_call.func_name,
                success=result.success,
                metadata=normalized_metadata,
            )
        )
        self._append_tool_result_media_messages(tool_call, result)
        if append_post_history:
            self._append_tool_post_history_messages(result.post_history_messages)

    def _append_tool_post_history_messages(self, messages: list[Any]) -> None:
        """Append tool-provided normal user messages after tool results."""

        seen_message_ids = {
            str(getattr(history_message, "message_id", "") or "").strip()
            for history_message in self._runtime._chat_history
            if str(getattr(history_message, "message_id", "") or "").strip()
        }
        for message in messages:
            if not isinstance(message, LLMContextMessage):
                continue
            message_id = str(getattr(message, "message_id", "") or "").strip()
            if message_id and message_id in seen_message_ids:
                continue
            self._runtime._chat_history.append(message)
            if message_id:
                seen_message_ids.add(message_id)

    @staticmethod
    def _iter_tool_result_media_items(result: ToolExecutionResult) -> list[tuple[int, Any]]:
        """获取需要从 tool result 拆分成普通上下文消息的媒体内容。"""

        media_items: list[tuple[int, Any]] = []
        for index, item in enumerate(result.content_items, start=1):
            content_type = str(getattr(item, "content_type", "") or "").strip()
            if content_type not in TOOL_RESULT_MEDIA_TYPES:
                continue
            if not any(
                str(getattr(item, field_name, "") or "").strip()
                for field_name in ("data", "uri", "name", "description", "mime_type")
            ):
                continue
            media_items.append((index, item))
        return media_items

    @staticmethod
    def _build_tool_result_media_index(tool_call: ToolCall, item_index: int) -> str:
        """构造 tool result 与媒体 user message 对齐的稳定索引。"""

        call_id = str(tool_call.call_id or "").strip() or str(tool_call.func_name or "tool").strip() or "tool"
        return f"tool_result:{call_id}:{item_index}"

    @staticmethod
    def _get_tool_result_media_metadata_value(item: Any, key: str) -> str:
        """读取工具媒体 metadata 中适合展示的简单字符串值。"""

        metadata = getattr(item, "metadata", None)
        if not isinstance(metadata, dict):
            return ""
        value = metadata.get(key)
        if isinstance(value, (dict, list, tuple, set)):
            return ""
        return str(value or "").strip()

    @staticmethod
    def _build_xml_attrs(attrs: list[tuple[str, str]]) -> str:
        """构造 XML 标签属性串。"""

        attr_parts: list[str] = []
        for key, value in attrs:
            normalized_key = str(key or "").strip()
            normalized_value = str(value or "").strip()
            if not normalized_key or not normalized_value:
                continue
            attr_parts.append(f'{normalized_key}="{escape(normalized_value, quote=True)}"')
        return " ".join(attr_parts)

    @classmethod
    def _build_tool_result_media_xml_attrs(
        cls,
        tool_call: ToolCall,
        item_index: int,
        item: Any,
    ) -> str:
        """构造工具返回媒体的精简 XML 属性。"""

        media_index = cls._build_tool_result_media_index(tool_call, item_index)
        content_type = str(getattr(item, "content_type", "") or "unknown").strip() or "unknown"
        mime_type = str(getattr(item, "mime_type", "") or "").strip()
        name = str(getattr(item, "name", "") or "").strip()
        context_key = cls._get_tool_result_media_metadata_value(item, "context_key")
        source_url = cls._get_tool_result_media_metadata_value(item, "source_url")
        return cls._build_xml_attrs(
            [
                ("msg_id", media_index),
                ("type", content_type),
                ("mime", mime_type),
                ("name", name),
                ("context_key", context_key),
                ("source_url", source_url),
            ]
        )

    @classmethod
    def _describe_tool_result_media_item(cls, item: Any) -> str:
        """生成 tool result 中的媒体索引描述。"""

        content_type = str(getattr(item, "content_type", "") or "unknown").strip() or "unknown"
        mime_type = str(getattr(item, "mime_type", "") or "").strip()
        name = str(getattr(item, "name", "") or "").strip()
        context_key = cls._get_tool_result_media_metadata_value(item, "context_key")
        source_url = cls._get_tool_result_media_metadata_value(item, "source_url")
        label_parts = [content_type]
        if mime_type:
            label_parts.append(mime_type)
        if name:
            label_parts.append(name)
        if context_key:
            label_parts.append(f"context_key={context_key}")
        if source_url:
            label_parts.append(f"source_url={source_url}")
        return " / ".join(label_parts)

    def _build_tool_result_history_content(self, tool_call: ToolCall, result: ToolExecutionResult) -> str:
        """构造纯文本 tool result，并在其中引用拆分出去的媒体索引。"""

        history_content = result.get_history_content()
        media_items = self._iter_tool_result_media_items(result)
        if not media_items:
            return history_content

        media_lines = ["<tool_result_media_list>"]
        for item_index, item in media_items:
            attrs = self._build_tool_result_media_xml_attrs(tool_call, item_index, item)
            media_lines.append(f"  <media {attrs} />" if attrs else "  <media />")
        media_lines.append("</tool_result_media_list>")

        if not history_content.strip():
            return "\n".join(media_lines).strip()
        return f"{history_content.strip()}\n\n" + "\n".join(media_lines).strip()

    @staticmethod
    def _decode_tool_result_base64_data(raw_data: str) -> bytes:
        """解析 tool result content_item 中的 base64 或 data URL 数据。"""

        normalized_data = raw_data.strip()
        if not normalized_data:
            return b""
        if normalized_data.startswith("data:") and "," in normalized_data:
            normalized_data = normalized_data.split(",", 1)[1].strip()
        try:
            return b64decode(normalized_data, validate=True)
        except (BinasciiError, ValueError):
            padded_data = normalized_data + "=" * (-len(normalized_data) % 4)
            try:
                return b64decode(padded_data)
            except (BinasciiError, ValueError):
                return b""

    def _build_tool_result_media_message_sequence(
        self,
        tool_call: ToolCall,
        item_index: int,
        item: Any,
    ) -> MessageSequence:
        """将单个 tool result 媒体项转成普通 user message 的组件序列。"""

        content_type = str(getattr(item, "content_type", "") or "unknown").strip() or "unknown"
        mime_type = str(getattr(item, "mime_type", "") or "").strip()
        uri = str(getattr(item, "uri", "") or "").strip()
        raw_data = str(getattr(item, "data", "") or "").strip()
        if not raw_data and uri.startswith("data:"):
            raw_data = uri

        attrs = self._build_tool_result_media_xml_attrs(tool_call, item_index, item)
        header_text = f"<tool_result_media {attrs} />" if attrs else "<tool_result_media />"

        message_sequence = MessageSequence([TextComponent(header_text)])
        if content_type == "image" or (content_type == "binary" and mime_type.startswith("image/")):
            image_binary = self._decode_tool_result_base64_data(raw_data)
            if image_binary:
                message_sequence.image(image_binary, content="")
        return message_sequence

    def _build_tool_result_media_visible_text(
        self,
        tool_call: ToolCall,
        item_index: int,
        item: Any,
        media_sequence: MessageSequence,
    ) -> str:
        """构造媒体 user message 在历史/监控中的可读文本。"""

        media_index = self._build_tool_result_media_index(tool_call, item_index)
        visible_parts = [f"<tool_result_media msg_id=\"{escape(media_index, quote=True)}\" />"]
        media_description = self._describe_tool_result_media_item(item)
        if media_description:
            visible_parts.append(media_description)
        if any(isinstance(component, ImageComponent) for component in media_sequence.components):
            visible_parts.append("[图片]")
        return "\n".join(part for part in visible_parts if part).strip()

    def _append_tool_result_media_messages(self, tool_call: ToolCall, result: ToolExecutionResult) -> None:
        """将 tool result 中的媒体项拆分为紧跟其后的 user context message。"""

        for item_index, item in self._iter_tool_result_media_items(result):
            media_sequence = self._build_tool_result_media_message_sequence(tool_call, item_index, item)
            visible_text = self._build_tool_result_media_visible_text(tool_call, item_index, item, media_sequence)
            media_index = self._build_tool_result_media_index(tool_call, item_index)
            self._schedule_tool_result_media_image_recognition(media_sequence, media_index)
            self._runtime._chat_history.append(
                SessionBackedMessage(
                    raw_message=media_sequence,
                    visible_text=visible_text,
                    timestamp=datetime.now(),
                    message_id=media_index,
                    source_kind=TOOL_RESULT_MEDIA_SOURCE_KIND,
                )
            )

    def _schedule_tool_result_media_image_recognition(self, media_sequence: MessageSequence, media_index: str) -> None:
        """为 tool result 拆出的图片消息调度后台识图。"""

        images = [component for component in media_sequence.components if isinstance(component, ImageComponent)]
        readable_images = [image for image in images if image.binary_data]
        if not readable_images:
            return

        try:
            asyncio.get_running_loop().create_task(self._recognize_tool_result_media_images(readable_images, media_index))
        except RuntimeError:
            runtime_log_prefix = self._runtime.log_prefix if hasattr(self._runtime, "log_prefix") else ""
            logger.debug(f"{runtime_log_prefix} 当前无运行中的事件循环，跳过 tool result 图片识别调度")

    async def _recognize_tool_result_media_images(self, images: list[ImageComponent], media_index: str) -> None:
        """后台触发 tool result 图片描述构建，不阻塞工具执行链路。"""

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
                    f"{self._runtime.log_prefix} 调度 tool result 图片识别失败: "
                    f"media_index={media_index} image_hash={image.binary_hash} error={exc}"
                )

    def _build_tool_result_summary(self, tool_call: ToolCall, result: ToolExecutionResult) -> str:
        """构建用于终端展示的工具结果摘要。"""

        normalized_tool_call = format_tool_call_for_display(tool_call)
        source_label = str(normalized_tool_call.get("source_label") or "").strip()
        source_text = f" [{source_label}]" if source_label else ""
        history_content = result.get_history_content().strip()
        if not history_content:
            history_content = result.error_message.strip()
        if not history_content:
            history_content = "执行成功" if result.success else "执行失败"

        summary_prefix = "[成功]" if result.success else "[失败]"
        normalized_content = self._truncate_tool_record_text(history_content, max_length=2000)
        return f"- {tool_call.func_name}{source_text} {summary_prefix}: {normalized_content}"

    @staticmethod
    def _append_deferred_tool_parameter_hint(result: ToolExecutionResult) -> ToolExecutionResult:
        """给未展开工具的失败结果补充参数查看提示。"""

        hint = "请通过 tool_search 查看具体的工具参数后再重试。"
        if result.success:
            return result
        if result.error_message:
            if hint not in result.error_message:
                result.error_message = f"{result.error_message}\n{hint}"
            return result
        if result.content:
            if hint not in result.content:
                result.content = f"{result.content}\n{hint}"
            return result
        result.error_message = hint
        return result

    def _build_tool_monitor_result(
        self,
        tool_call: ToolCall,
        invocation: ToolInvocation,
        result: ToolExecutionResult,
        duration_ms: float,
        tool_spec: Optional[ToolSpec] = None,
    ) -> dict[str, Any]:
        """构建 planner.finalized 中单个工具的监控结果。"""

        monitor_detail = result.metadata.get("monitor_detail")
        normalized_detail = None
        if monitor_detail is not None:
            normalized_detail = normalize_tool_record_value(monitor_detail)

        monitor_card = result.metadata.get("monitor_card")
        normalized_card = None
        if monitor_card is not None:
            normalized_card = normalize_tool_record_value(monitor_card)

        monitor_sub_cards = result.metadata.get("monitor_sub_cards")
        normalized_sub_cards = None
        if monitor_sub_cards is not None:
            normalized_sub_cards = normalize_tool_record_value(monitor_sub_cards)

        normalized_tool_call = format_tool_call_for_display(tool_call)
        tool_call_source = str(normalized_tool_call.get("source") or "").strip()
        tool_call_source_label = str(normalized_tool_call.get("source_label") or "").strip()
        tool_monitor_result = {
            "tool_call_id": tool_call.call_id,
            "tool_name": tool_call.func_name,
            "tool_title": tool_spec.title.strip() if tool_spec is not None and tool_spec.title.strip() else "",
            "tool_args": normalize_tool_record_value(
                invocation.arguments if isinstance(invocation.arguments, dict) else {}
            ),
            "tool_call_source": tool_call_source,
            "tool_call_source_label": tool_call_source_label,
            "success": result.success,
            "duration_ms": round(duration_ms, 2),
            "summary": self._build_tool_result_summary(tool_call, result),
            "detail": normalized_detail,
            "card": normalized_card,
            "sub_cards": normalized_sub_cards,
        }
        prompt_html_uri = str(result.metadata.get("prompt_html_uri") or "").strip()
        if not prompt_html_uri and isinstance(normalized_detail, dict):
            prompt_html_uri = str(normalized_detail.get("prompt_html_uri") or "").strip()
        if prompt_html_uri:
            tool_monitor_result["prompt_html_uri"] = prompt_html_uri
        return tool_monitor_result

    def _append_tool_display_results(
        self,
        *,
        tool_result_summaries: list[str],
        tool_monitor_results: list[dict[str, Any]],
        tool_call: ToolCall,
        invocation: ToolInvocation,
        result: ToolExecutionResult,
        duration_ms: float,
        tool_spec: Optional[ToolSpec],
    ) -> None:
        """追加终端摘要和监控详情。"""

        tool_result_summaries.append(self._build_tool_result_summary(tool_call, result))
        tool_monitor_results.append(
            self._build_tool_monitor_result(
                tool_call,
                invocation,
                result,
                duration_ms,
                tool_spec=tool_spec,
            )
        )

    async def _handle_tool_calls(
        self,
        tool_calls: list[ToolCall],
        latest_thought: str,
    ) -> tuple[bool, str, list[str], list[dict[str, Any]]]:
        """执行一批统一工具调用。

        Args:
            tool_calls: 模型返回的工具调用列表。
            latest_thought: 当前轮的最新思考文本。

        Returns:
            tuple[bool, str, list[str], list[dict[str, Any]]]: 是否需要暂停当前思考循环、
            触发暂停的工具名、工具结果摘要列表，以及最终监控事件使用的工具详情列表。
        """

        tool_result_summaries: list[str] = []
        tool_monitor_results: list[dict[str, Any]] = []
        deferred_post_history_messages: list[LLMContextMessage] = []

        if self._runtime._tool_registry is None:
            total_tool_count = len(tool_calls)
            for tool_index, tool_call in enumerate(tool_calls, start=1):
                self._log_tool_call_source(tool_call, stage=f"Planner {tool_index}/{total_tool_count}")
                invocation = self._build_tool_invocation(tool_call, latest_thought)
                result = ToolExecutionResult(
                    tool_name=tool_call.func_name,
                    success=False,
                    error_message="统一工具注册表尚未初始化。",
                )
                await self._record_tool_execution_effects(invocation, result, None)
                self._append_tool_execution_result(tool_call, result)
                self._append_tool_display_results(
                    tool_result_summaries=tool_result_summaries,
                    tool_monitor_results=tool_monitor_results,
                    tool_call=tool_call,
                    invocation=invocation,
                    result=result,
                    duration_ms=0.0,
                    tool_spec=None,
                )
            return False, "", tool_result_summaries, tool_monitor_results

        execution_context = self._build_tool_execution_context(latest_thought)
        availability_context = self._build_tool_availability_context()
        tool_spec_map = {
            tool_spec.name: tool_spec
            for tool_spec in await self._runtime._tool_registry.list_tools(availability_context)
        }
        total_tool_count = len(tool_calls)
        for tool_index, tool_call in enumerate(tool_calls, start=1):
            self._log_tool_call_source(tool_call, stage=f"Planner {tool_index}/{total_tool_count}")
            invocation = self._build_tool_invocation(tool_call, latest_thought)
            self._runtime._update_stage_status(
                f"工具执行 · {invocation.tool_name}",
                f"第 {tool_index}/{total_tool_count} 个工具",
            )
            tool_started_at = time.time()
            is_unexpanded_tool = not self._runtime.is_action_tool_currently_available(invocation.tool_name)
            result = await self._runtime._tool_registry.invoke(invocation, execution_context)
            if invocation.tool_name != "wait":
                self._runtime._reset_consecutive_wait_count(f"tool:{invocation.tool_name}")
            if is_unexpanded_tool and not result.success:
                result = self._append_deferred_tool_parameter_hint(result)
            tool_duration_ms = (time.time() - tool_started_at) * 1000
            await self._record_tool_execution_effects(
                invocation,
                result,
                tool_spec_map.get(invocation.tool_name),
            )
            self._append_tool_execution_result(tool_call, result, append_post_history=False)
            deferred_post_history_messages.extend(
                message
                for message in result.post_history_messages
                if isinstance(message, LLMContextMessage)
            )
            self._append_tool_display_results(
                tool_result_summaries=tool_result_summaries,
                tool_monitor_results=tool_monitor_results,
                tool_call=tool_call,
                invocation=invocation,
                result=result,
                duration_ms=tool_duration_ms,
                tool_spec=tool_spec_map.get(invocation.tool_name),
            )

            if not result.success and tool_call.func_name == "reply":
                logger.warning(f"{self._runtime.log_prefix} 回复工具未生成可见消息，将继续下一轮循环")

            if bool(result.metadata.get("wait_rest", False)):
                self._runtime._reset_consecutive_wait_count("wait_limit_rest")
                self._runtime._enter_stop_state()
                self._append_tool_post_history_messages(deferred_post_history_messages)
                return True, "wait_rest", tool_result_summaries, tool_monitor_results

            if bool(result.metadata.get("pause_execution", False)):
                self._append_tool_post_history_messages(deferred_post_history_messages)
                return True, invocation.tool_name, tool_result_summaries, tool_monitor_results

        self._append_tool_post_history_messages(deferred_post_history_messages)
        return False, "", tool_result_summaries, tool_monitor_results
