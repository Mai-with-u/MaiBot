"""Maisaka 推理引擎。"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Optional

import asyncio
import difflib
import json
import time
import traceback

from src.chat.heart_flow.heartFC_utils import CycleDetail
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import EmojiComponent, ImageComponent, MessageSequence, TextComponent
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.llm_models.exceptions import ReqAbortException
from src.llm_models.payload_content.tool_option import ToolCall
from src.services import database_service as database_api
from src.services.memory_service import memory_service

from .builtin_tool import build_builtin_tool_handlers as build_split_builtin_tool_handlers
from .builtin_tool import get_builtin_tool_visibility, is_builtin_tool_in_action_stage
from .builtin_tool import get_timing_tools
from .chat_loop_service import ChatResponse
from .chat_history_visual_refresher import refresh_chat_history_visual_placeholders
from .builtin_tool.context import BuiltinToolRuntimeContext
from .context_messages import (
    AssistantMessage,
    ComplexSessionMessage,
    LLMContextMessage,
    SessionBackedMessage,
    TIMING_GATE_INVALID_TOOL_HINT_SOURCE,
    ToolResultMessage,
    contains_complex_message,
)
from .history_post_processor import process_chat_history_after_cycle
from .history_utils import build_prefixed_message_sequence, build_session_message_visible_text
from .monitor_events import (
    emit_cycle_end,
    emit_cycle_start,
    emit_message_ingested,
    emit_planner_finalized,
    emit_timing_gate_result,
)
from .person_profile_injector import build_person_profile_injection_messages
from .planner_message_utils import build_planner_user_prefix_from_session_message
from .visual_mode_utils import resolve_enable_visual_planner

if TYPE_CHECKING:
    from .runtime import MaisakaHeartFlowChatting
    from .tool_provider import BuiltinToolHandler

logger = get_logger("maisaka_reasoning_engine")

TIMING_GATE_CONTEXT_DROP_HEAD_RATIO = 0.7
TIMING_GATE_MAX_ATTEMPTS = 3
TIMING_GATE_TOOL_NAMES = {"continue", "no_reply", "wait"}
HISTORY_SILENT_TOOL_NAMES = {"finish"}


class MaisakaReasoningEngine:
    """负责内部思考、推理与工具执行。"""

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self._last_reasoning_content: str = ""

    @staticmethod
    def _get_runtime_manager() -> Any:
        """获取插件运行时管理器。

        Returns:
            Any: 插件运行时管理器单例。
        """

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager()

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
                tool_definitions=tool_definitions,
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

    async def _run_timing_gate_sub_agent(
        self,
        *,
        system_prompt: str,
        tool_definitions: list[dict[str, Any]],
    ) -> Any:
        """运行一轮 Timing Gate 子代理请求。

        Timing Gate 阶段不再响应新的 planner 打断，只有主 planner 阶段允许被打断。
        """

        return await self._runtime.run_sub_agent(
            context_message_limit=self._runtime._max_context_size,
            drop_head_context_count=int(
                self._runtime._max_context_size * TIMING_GATE_CONTEXT_DROP_HEAD_RATIO,
            ),
            system_prompt=system_prompt,
            request_kind="timing_gate",
            interrupt_flag=None,
            tool_definitions=tool_definitions,
        )

    def _build_timing_gate_system_prompt(self) -> str:
        """构造 Timing Gate 子代理使用的系统提示词。"""

        return load_prompt(
            "maisaka_timing_gate",
            **self._runtime._chat_loop_service.build_prompt_template_context(),
        )

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
            deferred_tool_specs.append(tool_spec)

        self._runtime.update_deferred_tool_specs(deferred_tool_specs)
        selected_history, _ = self._runtime._chat_loop_service.select_llm_context_messages(
            self._runtime._chat_history,
            request_kind="planner",
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
        anchor_message: SessionMessage,
        source_messages: list[SessionMessage],
        deferred_tools_reminder: str,
    ) -> list[str]:
        """构造本轮 planner 的一次性注入消息。"""

        injected_messages: list[str] = []
        if deferred_tools_reminder:
            injected_messages.append(deferred_tools_reminder)

        try:
            profile_messages = await build_person_profile_injection_messages(
                anchor_message=anchor_message,
                pending_messages=source_messages,
            )
        except Exception as exc:
            logger.debug(f"{self._runtime.log_prefix} 人物画像自动注入失败，已跳过: {exc}")
            profile_messages = []
        injected_messages.extend(profile_messages)
        return injected_messages

    async def _invoke_tool_call(
        self,
        tool_call: ToolCall,
        latest_thought: str,
        anchor_message: SessionMessage,
        *,
        append_history: bool = True,
        store_record: bool = True,
    ) -> tuple[ToolInvocation, ToolExecutionResult, Optional[ToolSpec]]:
        """执行单个工具调用，并按需写入记录与历史。"""

        invocation = self._build_tool_invocation(tool_call, latest_thought)
        if self._runtime._tool_registry is None:
            result = ToolExecutionResult(
                tool_name=tool_call.func_name,
                success=False,
                error_message="统一工具注册表尚未初始化。",
            )
            if store_record:
                await self._store_tool_execution_record(invocation, result, None)
            if append_history:
                self._append_tool_execution_result(tool_call, result)
            return invocation, result, None

        execution_context = self._build_tool_execution_context(latest_thought, anchor_message)
        tool_spec = await self._runtime._tool_registry.get_tool_spec(invocation.tool_name)
        result = await self._runtime._tool_registry.invoke(invocation, execution_context)
        if store_record:
            await self._store_tool_execution_record(invocation, result, tool_spec)
        if append_history:
            self._append_tool_execution_result(tool_call, result)
        return invocation, result, tool_spec

    async def _run_timing_gate(
        self,
        anchor_message: SessionMessage,
    ) -> tuple[Literal["continue", "no_reply", "wait"], Any, list[str], list[dict[str, Any]]]:
        """运行 Timing Gate 子代理并返回控制决策。"""

        if self._runtime._force_next_timing_continue:
            return self._build_forced_continue_timing_result()

        tool_result_summaries: list[str] = []
        tool_monitor_results: list[dict[str, Any]] = []
        response: Any = None
        selected_tool_call: Optional[ToolCall] = None
        invalid_tool_text = ""
        for attempt_index in range(TIMING_GATE_MAX_ATTEMPTS):
            timing_tool_definitions = get_timing_tools(self._build_tool_availability_context())
            available_timing_tool_names = {
                str(tool_definition.get("name") or "").strip()
                for tool_definition in timing_tool_definitions
                if str(tool_definition.get("name") or "").strip()
            }
            response = await self._run_timing_gate_sub_agent(
                system_prompt=self._build_timing_gate_system_prompt(),
                tool_definitions=timing_tool_definitions,
            )
            selected_tool_call = None
            for tool_call in response.tool_calls:
                if tool_call.func_name in available_timing_tool_names:
                    selected_tool_call = tool_call
                    break

            if selected_tool_call is not None:
                break

            invalid_tool_names = [
                str(tool_call.func_name).strip()
                for tool_call in response.tool_calls
                if str(tool_call.func_name).strip()
            ]
            invalid_tool_text = "、".join(invalid_tool_names) if invalid_tool_names else "无工具"
            self._append_timing_gate_invalid_tool_hint(invalid_tool_text)
            remaining_attempts = TIMING_GATE_MAX_ATTEMPTS - attempt_index - 1
            if remaining_attempts > 0:
                logger.warning(
                    f"{self._runtime.log_prefix} Timing Gate 未返回有效控制工具：{invalid_tool_text}，"
                    f"将重试 ({attempt_index + 1}/{TIMING_GATE_MAX_ATTEMPTS})"
                )
                tool_result_summaries.append(
                    f"- retry [非法 Timing 工具]: 返回了 {invalid_tool_text}，将重试 "
                    f"({attempt_index + 1}/{TIMING_GATE_MAX_ATTEMPTS})"
                )
                continue

            logger.warning(
                f"{self._runtime.log_prefix} Timing Gate 连续 {TIMING_GATE_MAX_ATTEMPTS} 次未返回有效控制工具："
                f"{invalid_tool_text}，将按 no_reply 处理"
            )
            self._runtime._enter_stop_state()
            tool_result_summaries.append(
                f"- no_reply [非法 Timing 工具]: 返回了 {invalid_tool_text}，已停止本轮并等待新消息"
            )
            return "no_reply", response, tool_result_summaries, tool_monitor_results

        if selected_tool_call is None:
            self._runtime._enter_stop_state()
            tool_result_summaries.append(
                "- no_reply [非法 Timing 工具]: 已停止本轮并等待新消息"
            )
            return "no_reply", response, tool_result_summaries, tool_monitor_results

        if invalid_tool_text:
            self._runtime._chat_history = [
                message
                for message in self._runtime._chat_history
                if message.source != TIMING_GATE_INVALID_TOOL_HINT_SOURCE
            ]

        append_history = False
        store_record = selected_tool_call.func_name != "continue"
        invocation, result, tool_spec = await self._invoke_tool_call(
            selected_tool_call,
            response.content or "",
            anchor_message,
            append_history=append_history,
            store_record=store_record,
        )
        tool_result_summaries.append(self._build_tool_result_summary(selected_tool_call, result))
        tool_monitor_results.append(
            self._build_tool_monitor_result(
                selected_tool_call,
                invocation,
                result,
                duration_ms=0.0,
                tool_spec=tool_spec,
            )
        )
        self._append_timing_gate_execution_result(response, selected_tool_call, result)

        timing_action = str(result.metadata.get("timing_action") or selected_tool_call.func_name).strip()
        available_timing_action_names = {
            str(tool_definition.get("name") or "").strip()
            for tool_definition in get_timing_tools(self._build_tool_availability_context())
            if str(tool_definition.get("name") or "").strip()
        }
        if timing_action not in available_timing_action_names:
            logger.warning(
                f"{self._runtime.log_prefix} Timing Gate 返回未知动作 {timing_action!r}，将按 no_reply 处理"
            )
            self._runtime._enter_stop_state()
            tool_result_summaries.append(
                f"- no_reply [未知 Timing 动作]: 返回了 {timing_action!r}，已停止本轮并等待新消息"
            )
            return "no_reply", response, tool_result_summaries, tool_monitor_results
        return timing_action, response, tool_result_summaries, tool_monitor_results

    def _build_forced_continue_timing_result(
        self,
    ) -> tuple[Literal["continue"], ChatResponse, list[str], list[dict[str, Any]]]:
        """构造跳过 Timing Gate 时使用的伪 continue 结果。"""

        reason = self._runtime._consume_force_next_timing_continue_reason() or "本轮直接跳过 Timing Gate 并视作 continue。"
        logger.info(f"{self._runtime.log_prefix} {reason}")
        return (
            "continue",
            ChatResponse(
                content=reason,
                tool_calls=[],
                request_messages=[],
                raw_message=AssistantMessage(
                    content="",
                    timestamp=datetime.now(),
                    source_kind="perception",
                ),
                selected_history_count=min(
                    sum(1 for message in self._runtime._chat_history if message.count_in_context),
                    self._runtime._max_context_size,
                ),
                tool_count=0,
                prompt_tokens=0,
                built_message_count=0,
                completion_tokens=0,
                total_tokens=0,
                prompt_section=None,
            ),
            [f"- continue [强制跳过]: {reason}"],
            [],
        )

    def _append_timing_gate_invalid_tool_hint(self, invalid_tool_text: str) -> None:
        """写入一条仅 Timing Gate 可见的非法工具提示，并保证最多保留最新一条。"""

        self._runtime._chat_history = [
            message
            for message in self._runtime._chat_history
            if message.source != TIMING_GATE_INVALID_TOOL_HINT_SOURCE
        ]
        normalized_tool_text = invalid_tool_text.strip() or "无工具"
        hint_content = (
            "Timing Gate 上一轮选择了非法工具："
            f"{normalized_tool_text}。\n"
            "Timing Gate 只能调用当前可用的 continue、no_reply 或 wait 中的一个工具。"
        )
        self._runtime._chat_history.append(
            SessionBackedMessage(
                raw_message=MessageSequence([TextComponent(hint_content)]),
                visible_text=hint_content,
                timestamp=datetime.now(),
                source_kind=TIMING_GATE_INVALID_TOOL_HINT_SOURCE,
            )
        )

    @staticmethod
    def _mark_timing_gate_completed(timing_action: str) -> bool:
        """根据门控动作决定下一轮是否还需要重新执行 timing。"""

        return timing_action != "continue"

    @staticmethod
    def _should_retry_planner_after_interrupt(
        *,
        round_index: int,
        max_internal_rounds: int,
        has_pending_messages: bool,
    ) -> bool:
        return has_pending_messages and round_index < max_internal_rounds

    async def run_loop(self) -> None:
        """独立消费消息批次，并执行对应的内部思考轮次。"""
        try:
            while self._runtime._running:
                queued_trigger = await self._runtime._internal_turn_queue.get()
                message_triggered, timeout_triggered = self._drain_ready_turn_triggers(queued_trigger)

                if self._runtime._agent_state == self._runtime._STATE_WAIT and not timeout_triggered:
                    self._runtime._message_turn_scheduled = False
                    logger.debug(f"{self._runtime.log_prefix} 当前仍处于 wait 状态，忽略消息触发并继续等待超时")
                    continue

                if message_triggered:
                    await self._runtime._wait_for_message_quiet_period()
                    self._runtime._message_turn_scheduled = False

                cached_messages = (
                    self._runtime._collect_pending_messages()
                    if self._runtime._has_pending_messages()
                    else []
                )
                if cached_messages:
                    self._runtime._agent_state = self._runtime._STATE_RUNNING
                    self._runtime._update_stage_status(
                        "消息整理",
                        f"待处理消息 {len(cached_messages)} 条",
                    )
                    asyncio.create_task(self._runtime._trigger_batch_learning(cached_messages))
                    if timeout_triggered:
                        self._runtime._chat_history.append(
                            self._build_wait_completed_message(has_new_messages=True)
                        )
                    await self._ingest_messages(cached_messages)
                    anchor_message = cached_messages[-1]
                else:
                    anchor_message = self._get_timeout_anchor_message()
                    if anchor_message is None:
                        logger.warning(f"{self._runtime.log_prefix} wait 超时后没有可复用的锚点消息，跳过本轮")
                        continue
                    logger.info(f"{self._runtime.log_prefix} 等待超时后开始新一轮思考")
                    if self._runtime._pending_wait_tool_call_id:
                        self._runtime._chat_history.append(
                            self._build_wait_completed_message(has_new_messages=False)
                        )

                try:
                    timing_gate_required = True
                    round_index = 0
                    while round_index < self._runtime._max_internal_rounds:
                        cycle_detail = self._start_cycle()
                        round_text = f"第 {round_index + 1}/{self._runtime._max_internal_rounds} 轮"
                        self._runtime._log_cycle_started(cycle_detail, round_index)
                        self._runtime._update_stage_status("启动循环", f"循环 {cycle_detail.cycle_id}", round_text=round_text)
                        await emit_cycle_start(
                            session_id=self._runtime.session_id,
                            cycle_id=cycle_detail.cycle_id,
                            round_index=round_index,
                            max_rounds=self._runtime._max_internal_rounds,
                            history_count=len(self._runtime._chat_history),
                        )
                        planner_started_at = 0.0
                        planner_duration_ms = 0.0
                        timing_duration_ms = 0.0
                        current_stage_started_at = 0.0
                        timing_action: Optional[str] = None
                        timing_response: Optional[ChatResponse] = None
                        timing_tool_results: Optional[list[str]] = None
                        timing_tool_monitor_results: Optional[list[dict[str, Any]]] = None
                        response: Optional[ChatResponse] = None
                        action_tool_definitions: list[dict[str, Any]] = []
                        planner_extra_lines: list[str] = []
                        planner_interrupted = False
                        cycle_end_reason = "continue"
                        cycle_end_detail = "本轮思考完成，继续后续内部轮次。"
                        tool_result_summaries: list[str] = []
                        tool_monitor_results: list[dict[str, Any]] = []
                        try:
                            visual_refresh_started_at = time.time()
                            refreshed_message_count = await self._refresh_chat_history_visual_placeholders()
                            cycle_detail.time_records["visual_refresh"] = time.time() - visual_refresh_started_at
                            if refreshed_message_count > 0:
                                logger.info(
                                    f"{self._runtime.log_prefix} 本轮思考前已刷新 {refreshed_message_count} 条视觉占位历史消息"
                                )

                            if timing_gate_required:
                                self._runtime._update_stage_status("Timing Gate", "等待门控决策", round_text=round_text)
                                current_stage_started_at = time.time()
                                timing_started_at = time.time()
                                (
                                    timing_action,
                                    timing_response,
                                    timing_tool_results,
                                    timing_tool_monitor_results,
                                ) = await self._run_timing_gate(anchor_message)
                                timing_elapsed_seconds = time.time() - timing_started_at
                                if timing_action == "no_reply":
                                    await self._runtime._wait_for_timing_gate_non_continue_cooldown(
                                        timing_elapsed_seconds
                                    )
                                timing_duration_ms = (time.time() - timing_started_at) * 1000
                                cycle_detail.time_records["timing_gate"] = timing_duration_ms / 1000
                                await emit_timing_gate_result(
                                    session_id=self._runtime.session_id,
                                    cycle_id=cycle_detail.cycle_id,
                                    action=timing_action,
                                    content=timing_response.content,
                                    tool_calls=timing_response.tool_calls,
                                    messages=[],
                                    prompt_tokens=timing_response.prompt_tokens,
                                    selected_history_count=timing_response.selected_history_count,
                                    duration_ms=timing_duration_ms,
                                )
                                timing_gate_required = self._mark_timing_gate_completed(timing_action)
                                if timing_action != "continue":
                                    if timing_action == "wait":
                                        cycle_end_reason = "timing_wait"
                                        cycle_end_detail = "Timing Gate 选择 wait，本轮不会进入 Planner，将在等待结束后继续。"
                                    else:
                                        cycle_end_reason = "timing_no_reply"
                                        cycle_end_detail = "Timing Gate 选择 no_reply，本轮不会进入 Planner。"
                                    logger.debug(
                                        f"{self._runtime.log_prefix} Timing Gate 结束当前回合: "
                                        f"回合={round_index + 1} 动作={timing_action}"
                                    )
                                    break
                            else:
                                logger.info(
                                    f"{self._runtime.log_prefix} 跳过 Timing Gate，继续执行 Planner: "
                                    f"回合={round_index + 1}"
                                )

                            planner_started_at = time.time()
                            current_stage_started_at = planner_started_at
                            self._runtime._update_stage_status("Planner", "组织上下文并请求模型", round_text=round_text)
                            action_tool_definitions, deferred_tools_reminder = await self._build_action_tool_definitions()
                            injected_user_messages = await self._build_planner_injected_user_messages(
                                anchor_message=anchor_message,
                                source_messages=cached_messages or [anchor_message],
                                deferred_tools_reminder=deferred_tools_reminder,
                            )
                            logger.info(
                                f"{self._runtime.log_prefix} 规划器开始执行: "
                                f"回合={round_index + 1} "
                                f"历史消息数={len(self._runtime._chat_history)} "
                                f"开始时间={planner_started_at:.3f}"
                            )
                            response = await self._run_interruptible_planner(
                                injected_user_messages=injected_user_messages or None,
                                tool_definitions=action_tool_definitions,
                            )
                            planner_duration_ms = (time.time() - planner_started_at) * 1000
                            cycle_detail.time_records["planner"] = planner_duration_ms / 1000
                            # logger.info(
                            #     f"{self._runtime.log_prefix} 规划器执行完成: "
                            #     f"回合={round_index + 1} "
                            #     f"耗时={cycle_detail.time_records['planner']:.3f} 秒"
                            # )
                            reasoning_content = response.content or ""
                            if self._should_replace_reasoning(reasoning_content):
                                response.content = "我应该根据我上面思考的内容进行反思，重新思考我下一步的行动，我需要分析当前场景，对话，以及我可以使用的工具，然后直接输出我的想法"
                                response.raw_message.content = "我应该根据我上面思考的内容进行反思，重新思考我下一步的行动，我需要分析当前场景，对话，以及我可以使用的工具，然后直接输出我的想法"
                                logger.info(f"{self._runtime.log_prefix} 当前思考与上一轮过于相似，已替换为重新思考提示")

                            self._last_reasoning_content = reasoning_content
                            self._runtime._chat_history.append(response.raw_message)
                            tool_monitor_results = []

                            if response.tool_calls:
                                tool_started_at = time.time()
                                (
                                    should_pause,
                                    pause_tool_name,
                                    tool_result_summaries,
                                    tool_monitor_results,
                                ) = await self._handle_tool_calls(
                                    response.tool_calls,
                                    response.content or "",
                                    anchor_message,
                                )
                                cycle_detail.time_records["tool_calls"] = time.time() - tool_started_at
                                if should_pause:
                                    if pause_tool_name == "finish":
                                        cycle_end_reason = "finish"
                                        cycle_end_detail = "Planner 调用 finish，结束本轮思考并等待新消息。"
                                    elif pause_tool_name:
                                        cycle_end_reason = f"tool_pause:{pause_tool_name}"
                                        cycle_end_detail = f"工具 {pause_tool_name} 要求暂停当前思考循环。"
                                    else:
                                        cycle_end_reason = "tool_pause"
                                        cycle_end_detail = "工具要求暂停当前思考循环。"
                                    break
                                cycle_end_reason = "tool_continue"
                                cycle_end_detail = "Planner 工具执行完成，继续下一轮内部思考。"
                                continue

                            if not response.content:
                                cycle_end_reason = "empty_planner_response"
                                cycle_end_detail = "Planner 没有返回文本或工具调用，本轮思考结束。"
                                break
                        except ReqAbortException as exc:
                            planner_interrupted = True
                            cycle_end_reason = "planner_interrupted"
                            cycle_end_detail = "Planner 被新消息打断，当前轮结束。"
                            self._runtime._update_stage_status(
                                "Planner 已打断",
                                str(exc) or "收到外部中断信号",
                                round_text=round_text,
                            )
                            interrupted_at = time.time()
                            interrupted_stage_label = "Planner"
                            interrupted_text = "Planner 收到新消息，开始重新决策"
                            interrupted_response = ChatResponse(
                                content=interrupted_text or None,
                                tool_calls=[],
                                request_messages=[],
                                raw_message=AssistantMessage(
                                    content=interrupted_text,
                                    timestamp=datetime.now(),
                                    tool_calls=[],
                                    source_kind="perception",
                                ),
                                selected_history_count=len(self._runtime._chat_history),
                                tool_count=len(action_tool_definitions),
                                prompt_tokens=0,
                                built_message_count=0,
                                completion_tokens=0,
                                total_tokens=0,
                                prompt_section=None,
                            )
                            interrupted_extra_lines = [
                                "状态：已被新消息打断",
                                f"打断位置：{interrupted_stage_label} 请求流式响应阶段",
                                f"打断耗时：{interrupted_at - current_stage_started_at:.3f} 秒",
                            ]
                            response = interrupted_response
                            planner_extra_lines = interrupted_extra_lines
                            logger.info(
                                f"{self._runtime.log_prefix} {interrupted_stage_label} 打断成功: "
                                f"回合={round_index + 1} "
                                f"开始时间={current_stage_started_at:.3f} "
                                f"打断时间={interrupted_at:.3f} "
                                f"耗时={interrupted_at - current_stage_started_at:.3f} 秒"
                            )
                            if not self._should_retry_planner_after_interrupt(
                                round_index=round_index,
                                max_internal_rounds=self._runtime._max_internal_rounds,
                                has_pending_messages=self._runtime._has_pending_messages(),
                            ):
                                break

                            await self._runtime._wait_for_message_quiet_period()
                            self._runtime._message_turn_scheduled = False
                            interrupted_messages = self._runtime._collect_pending_messages()
                            if not interrupted_messages:
                                break

                            asyncio.create_task(self._runtime._trigger_batch_learning(interrupted_messages))
                            await self._ingest_messages(interrupted_messages)
                            cached_messages = interrupted_messages
                            anchor_message = interrupted_messages[-1]
                            logger.info(
                                f"{self._runtime.log_prefix} 淇濇寔娲昏穬鐘舵€侊紝璺宠繃 Timing Gate 鐩存帴閲嶈瘯 Planner: "
                                f"鍥炲悎={round_index + 2}"
                            )
                            continue
                        finally:
                            completed_cycle = self._end_cycle(cycle_detail)
                            if (
                                round_index + 1 >= self._runtime._max_internal_rounds
                                and cycle_end_reason in {"continue", "tool_continue"}
                            ):
                                cycle_end_reason = "max_rounds"
                                cycle_end_detail = (
                                    f"已达到内部思考轮次上限 {self._runtime._max_internal_rounds}，"
                                    "本轮处理结束。"
                                )
                            self._runtime._render_context_usage_panel(
                                cycle_id=cycle_detail.cycle_id,
                                time_records=dict(completed_cycle.time_records),
                                timing_selected_history_count=(
                                    timing_response.selected_history_count if timing_response is not None else None
                                ),
                                timing_prompt_tokens=(
                                    timing_response.prompt_tokens if timing_response is not None else None
                                ),
                                timing_action=timing_action or "",
                                timing_response=timing_response.content or "" if timing_response is not None else "",
                                timing_tool_calls=timing_response.tool_calls if timing_response is not None else None,
                                timing_tool_results=timing_tool_results,
                                timing_tool_detail_results=timing_tool_monitor_results,
                                timing_prompt_section=(
                                    timing_response.prompt_section if timing_response is not None else None
                                ),
                                planner_selected_history_count=(
                                    response.selected_history_count if response is not None else None
                                ),
                                planner_prompt_tokens=response.prompt_tokens if response is not None else None,
                                planner_response=response.content or "" if response is not None else "",
                                planner_tool_calls=response.tool_calls if response is not None else None,
                                planner_tool_results=tool_result_summaries,
                                planner_tool_detail_results=tool_monitor_results,
                                planner_prompt_section=response.prompt_section if response is not None else None,
                                planner_extra_lines=planner_extra_lines,
                            )
                            await emit_planner_finalized(
                                session_id=self._runtime.session_id,
                                cycle_id=cycle_detail.cycle_id,
                                timing_request_messages=(
                                    timing_response.request_messages if timing_response is not None else None
                                ),
                                timing_selected_history_count=(
                                    timing_response.selected_history_count if timing_response is not None else None
                                ),
                                timing_tool_count=timing_response.tool_count if timing_response is not None else None,
                                timing_action=timing_action,
                                timing_content=timing_response.content if timing_response is not None else None,
                                timing_tool_calls=timing_response.tool_calls if timing_response is not None else None,
                                timing_tool_results=timing_tool_results,
                                timing_prompt_tokens=timing_response.prompt_tokens if timing_response is not None else None,
                                timing_completion_tokens=(
                                    timing_response.completion_tokens if timing_response is not None else None
                                ),
                                timing_total_tokens=timing_response.total_tokens if timing_response is not None else None,
                                timing_duration_ms=timing_duration_ms if timing_response is not None else None,
                                planner_request_messages=response.request_messages if response is not None else None,
                                planner_selected_history_count=(
                                    response.selected_history_count if response is not None else None
                                ),
                                planner_tool_count=response.tool_count if response is not None else None,
                                planner_content=response.content if response is not None else None,
                                planner_tool_calls=response.tool_calls if response is not None else None,
                                planner_prompt_tokens=response.prompt_tokens if response is not None else None,
                                planner_completion_tokens=(
                                    response.completion_tokens if response is not None else None
                                ),
                                planner_total_tokens=response.total_tokens if response is not None else None,
                                planner_duration_ms=planner_duration_ms if response is not None else None,
                                planner_prompt_html_uri=response.prompt_html_uri if response is not None else None,
                                tools=tool_monitor_results,
                                time_records=dict(completed_cycle.time_records),
                                agent_state=self._runtime._agent_state,
                                planner_interrupted=planner_interrupted,
                                end_reason=cycle_end_reason,
                                end_detail=cycle_end_detail,
                            )
                            await emit_cycle_end(
                                session_id=self._runtime.session_id,
                                cycle_id=cycle_detail.cycle_id,
                                time_records=dict(completed_cycle.time_records),
                                agent_state=self._runtime._agent_state,
                                end_reason=cycle_end_reason,
                                end_detail=cycle_end_detail,
                            )
                            if not planner_interrupted:
                                round_index += 1
                finally:
                    if self._runtime._agent_state == self._runtime._STATE_RUNNING:
                        self._runtime._agent_state = self._runtime._STATE_STOP
                    if self._runtime._running:
                        self._runtime._update_stage_status("等待消息", "本轮处理结束")
        except asyncio.CancelledError:
            self._runtime._log_internal_loop_cancelled()
            raise
        except Exception:
            logger.exception(f"{self._runtime.log_prefix} Maisaka 内部循环发生异常")
            logger.error(traceback.format_exc())
            raise

    def _drain_ready_turn_triggers(
        self,
        queued_trigger: Literal["message", "timeout"],
    ) -> tuple[bool, bool]:
        """合并当前已就绪的消息触发信号。"""

        message_triggered = queued_trigger == "message"
        timeout_triggered = queued_trigger == "timeout"

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

        return message_triggered, timeout_triggered

    def _get_timeout_anchor_message(self) -> Optional[SessionMessage]:
        """在 wait 超时后复用最近一条真实用户消息作为锚点。"""
        if self._runtime.message_cache:
            return self._runtime.message_cache[-1]
        return None

    def _build_wait_completed_message(self, *, has_new_messages: bool) -> ToolResultMessage:
        """构造 wait 完成后的工具结果消息。"""
        tool_call_id = self._runtime._pending_wait_tool_call_id or "wait_timeout"
        self._runtime._pending_wait_tool_call_id = None
        content = (
            "等待已结束，期间收到了新的用户输入。请结合这些新消息继续下一轮思考。"
            if has_new_messages
            else "等待已超时，期间没有收到新的用户输入。请基于现有上下文继续下一轮思考。"
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

            # 向监控前端广播新消息注入事件
            user_info = message.message_info.user_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
            await emit_message_ingested(
                session_id=self._runtime.session_id,
                speaker_name=speaker_name,
                content=(message.processed_plain_text or "").strip(),
                message_id=message.message_id,
                timestamp=message.timestamp.timestamp(),
            )

    async def _build_history_message(
        self,
        message: SessionMessage,
        *,
        source_kind: str = "user",
    ) -> Optional[LLMContextMessage]:
        """根据真实消息构造对应的上下文消息。"""

        source_sequence = message.raw_message
        visible_text = self._build_legacy_visible_text(message, source_sequence, source_kind=source_kind)
        planner_prefix = build_planner_user_prefix_from_session_message(message)
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
        self._runtime._current_cycle_detail = CycleDetail(cycle_id=self._runtime._cycle_counter)
        self._runtime._current_cycle_detail.thinking_id = f"maisaka_tid{round(time.time(), 2)}"
        return self._runtime._current_cycle_detail

    def _end_cycle(self, cycle_detail: CycleDetail, only_long_execution: bool = True) -> CycleDetail:
        """结束并记录一轮 Maisaka 思考循环。"""
        cycle_detail.end_time = time.time()
        self._runtime.history_loop.append(cycle_detail)
        self._post_process_chat_history_after_cycle()

        timer_strings = [
            f"{name}: {duration:.2f}s"
            for name, duration in cycle_detail.time_records.items()
            if not only_long_execution or duration >= 0.1
        ]
        self._runtime._log_cycle_completed(cycle_detail, timer_strings)
        return cycle_detail

    def _post_process_chat_history_after_cycle(self) -> None:
        """裁剪聊天历史，保证用户消息数量不超过配置限制。"""
        process_result = process_chat_history_after_cycle(
            self._runtime._chat_history,
            max_context_size=self._runtime._max_context_size,
        )
        if process_result.changed_count <= 0:
            return

        self._runtime._chat_history = process_result.history
        if process_result.removed_count <= 0:
            return
        self._runtime._log_history_trimmed(
            process_result.removed_count,
            process_result.remaining_context_count,
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
            logger.info(
                f"{self._runtime.log_prefix} 跳过思考相似度判定: "
                f"上一轮为空={not bool(self._last_reasoning_content)} "
                f"当前为空={not bool(current_content)} 相似度=0.00"
            )
            return False

        similarity = self._calculate_similarity(current_content, self._last_reasoning_content)
        logger.debug(f"{self._runtime.log_prefix} 思考内容相似度: {similarity:.2f}")
        return similarity > 0.9

    @staticmethod
    def _post_process_reply_text(reply_text: str) -> list[str]:
        """沿用旧回复链的文本后处理，执行分段与错别字注入。"""
        return BuiltinToolRuntimeContext.post_process_reply_text(reply_text)

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
        anchor_message: SessionMessage,
    ) -> ToolExecutionContext:
        """构造统一工具执行上下文。

        Args:
            latest_thought: 当前轮的最新思考文本。
            anchor_message: 当前轮的锚点消息。

        Returns:
            ToolExecutionContext: 统一工具执行上下文。
        """

        return ToolExecutionContext(
            session_id=self._runtime.session_id,
            stream_id=self._runtime.session_id,
            reasoning=latest_thought,
            metadata={"anchor_message": anchor_message},
        )

    @staticmethod
    def _normalize_tool_record_value(value: Any) -> Any:
        """将工具记录中的任意值规范化为可序列化结构。

        Args:
            value: 原始值。

        Returns:
            Any: 适合写入 JSON 的规范化结果。
        """

        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            normalized_dict: dict[str, Any] = {}
            for key, item in value.items():
                normalized_dict[str(key)] = MaisakaReasoningEngine._normalize_tool_record_value(item)
            return normalized_dict
        if isinstance(value, (list, tuple, set)):
            return [MaisakaReasoningEngine._normalize_tool_record_value(item) for item in value]
        if isinstance(value, bytes):
            return f"<bytes:{len(value)}>"
        if hasattr(value, "model_dump"):
            try:
                return MaisakaReasoningEngine._normalize_tool_record_value(value.model_dump())
            except Exception:
                return str(value)
        if hasattr(value, "__dict__"):
            try:
                return MaisakaReasoningEngine._normalize_tool_record_value(dict(value.__dict__))
            except Exception:
                return str(value)
        return str(value)

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

    def _build_tool_record_payload(
        self,
        invocation: ToolInvocation,
        result: ToolExecutionResult,
        tool_spec: Optional[ToolSpec],
    ) -> dict[str, Any]:
        """构造统一工具落库数据。

        Args:
            invocation: 工具调用对象。
            result: 工具执行结果。
            tool_spec: 对应的工具声明。

        Returns:
            dict[str, Any]: 可直接写入数据库的工具记录数据。
        """

        payload: dict[str, Any] = {
            "call_id": invocation.call_id,
            "session_id": invocation.session_id,
            "stream_id": invocation.stream_id,
            "arguments": self._normalize_tool_record_value(invocation.arguments),
            "success": result.success,
            "content": result.content,
            "error_message": result.error_message,
            "history_content": result.get_history_content(),
            "structured_content": self._normalize_tool_record_value(result.structured_content),
            "metadata": self._normalize_tool_record_value(result.metadata),
        }
        if tool_spec is not None:
            payload["provider_name"] = tool_spec.provider_name
            payload["provider_type"] = tool_spec.provider_type
            payload["brief_description"] = tool_spec.brief_description
            payload["detailed_description"] = tool_spec.detailed_description
            payload["title"] = tool_spec.title
        return payload

    def _build_tool_display_prompt(
        self,
        invocation: ToolInvocation,
        result: ToolExecutionResult,
        tool_spec: Optional[ToolSpec],
    ) -> str:
        """构造展示给历史回放与 UI 的工具摘要。

        Args:
            invocation: 工具调用对象。
            result: 工具执行结果。
            tool_spec: 对应的工具声明。

        Returns:
            str: 用于展示的工具摘要文本。
        """

        custom_display_prompt = result.metadata.get("record_display_prompt")
        if isinstance(custom_display_prompt, str) and custom_display_prompt.strip():
            return custom_display_prompt.strip()

        structured_content = (
            result.structured_content
            if isinstance(result.structured_content, dict)
            else {}
        )
        history_content = self._truncate_tool_record_text(result.get_history_content(), max_length=200)
        normalized_args = self._normalize_tool_record_value(invocation.arguments)

        if invocation.tool_name == "reply":
            target_user_name = str(structured_content.get("target_user_name") or "对方").strip() or "对方"
            reply_text = str(structured_content.get("reply_text") or "").strip()
            if result.success and reply_text:
                return f"你对{target_user_name}进行了回复：{reply_text}"
            target_message_id = str(invocation.arguments.get("msg_id") or "").strip()
            error_text = self._truncate_tool_record_text(result.error_message or history_content, max_length=120)
            return f"你尝试回复消息 {target_message_id or 'unknown'}，但失败了：{error_text}"

        if invocation.tool_name == "send_emoji":
            if result.success:
                return "你发送了表情包。"
            return f"你尝试发送表情包，但失败了：{self._truncate_tool_record_text(result.error_message or history_content, 120)}"

        if invocation.tool_name == "wait":
            wait_seconds = invocation.arguments.get("seconds", 30)
            return f"你让当前对话先等待 {wait_seconds} 秒。"

        if invocation.tool_name == "no_reply":
            return "你暂停了当前对话循环，等待新的外部消息。"

        if invocation.tool_name == "finish":
            return "你结束了本轮思考，等待新的外部消息后再继续。"

        if invocation.tool_name == "continue":
            return "你允许当前对话继续进入下一轮完整思考与工具执行。"

        if invocation.tool_name == "query_jargon":
            words = invocation.arguments.get("words", [])
            if isinstance(words, list):
                words_text = "、".join(str(item).strip() for item in words if str(item).strip())
            else:
                words_text = ""
            if words_text:
                return f"你查询了这些黑话或词条：{words_text}"
            return "你查询了一次黑话或词条信息。"

        if invocation.tool_name == "query_person_info":
            person_name = str(invocation.arguments.get("person_name") or "").strip()
            if person_name:
                return f"你查询了人物信息：{person_name}"
            return "你查询了一次人物信息。"

        if invocation.tool_name == "query_memory":
            query_text = str(invocation.arguments.get("query") or "").strip()
            mode = str(invocation.arguments.get("mode") or "search").strip() or "search"
            hit_items = structured_content.get("hits")
            hit_count = len(hit_items) if isinstance(hit_items, list) else 0
            if query_text:
                return f"你查询了长期记忆：{query_text}（模式：{mode}，命中 {hit_count} 条）"
            return f"你按时间范围查询了一次长期记忆（模式：{mode}，命中 {hit_count} 条）。"

        if invocation.tool_name == "view_complex_message":
            target_message_id = str(invocation.arguments.get("msg_id") or "").strip()
            if target_message_id:
                return f"你查看了复杂消息 {target_message_id} 的完整内容。"
            return "你查看了一条复杂消息的完整内容。"

        brief_description = ""
        if tool_spec is not None:
            brief_description = tool_spec.brief_description.strip()

        if normalized_args:
            arguments_text = self._truncate_tool_record_text(
                json.dumps(normalized_args, ensure_ascii=False),
                max_length=160,
            )
        else:
            arguments_text = "{}"

        if result.success:
            if brief_description:
                return f"{brief_description} 参数={arguments_text}；结果：{history_content or '执行成功'}"
            return f"你调用了工具 {invocation.tool_name}，参数={arguments_text}；结果：{history_content or '执行成功'}"

        error_text = self._truncate_tool_record_text(result.error_message or history_content, max_length=160)
        return f"你调用了工具 {invocation.tool_name}，参数={arguments_text}；执行失败：{error_text}"

    async def _store_tool_execution_record(
        self,
        invocation: ToolInvocation,
        result: ToolExecutionResult,
        tool_spec: Optional[ToolSpec],
    ) -> None:
        """将工具执行结果落库到统一工具记录表。

        Args:
            invocation: 工具调用对象。
            result: 工具执行结果。
            tool_spec: 对应的工具声明。
        """

        if self._runtime.chat_stream is None:
            logger.debug(
                f"{self._runtime.log_prefix} 当前没有 chat_stream，跳过工具记录存储: "
                f"工具={invocation.tool_name}"
            )
            return

        builtin_prompt = ""
        if tool_spec is not None:
            builtin_prompt = tool_spec.build_llm_description()

        try:
            tool_record_payload = self._build_tool_record_payload(invocation, result, tool_spec)
            saved_record = await database_api.store_tool_info(
                chat_stream=self._runtime.chat_stream,
                builtin_prompt=builtin_prompt,
                display_prompt=self._build_tool_display_prompt(invocation, result, tool_spec),
                tool_id=invocation.call_id,
                tool_data=tool_record_payload,
                tool_name=invocation.tool_name,
                tool_reasoning=invocation.reasoning,
            )
        except Exception:
            logger.exception(
                f"{self._runtime.log_prefix} 写入工具记录失败: 工具={invocation.tool_name} 调用编号={invocation.call_id}"
            )
            return

        if invocation.tool_name == "query_memory" and isinstance(saved_record, dict):
            try:
                enqueue_payload = await memory_service.enqueue_feedback_task(
                    query_tool_id=str(saved_record.get("tool_id") or invocation.call_id or "").strip(),
                    session_id=str(saved_record.get("session_id") or self._runtime.chat_stream.session_id or "").strip(),
                    query_timestamp=saved_record.get("timestamp"),
                    structured_content=tool_record_payload.get("structured_content")
                    if isinstance(tool_record_payload.get("structured_content"), dict)
                    else {},
                )
            except Exception:
                logger.exception(
                    f"{self._runtime.log_prefix} 反馈纠错任务入队失败: tool_call_id={invocation.call_id}"
                )
            else:
                if not bool(enqueue_payload.get("success")):
                    logger.debug(
                        f"{self._runtime.log_prefix} 反馈纠错任务未入队: "
                        f"tool_call_id={invocation.call_id} reason={enqueue_payload.get('reason', '')}"
                    )

    def _append_tool_execution_result(self, tool_call: ToolCall, result: ToolExecutionResult) -> None:
        """将统一工具执行结果写回 Maisaka 历史。

        Args:
            tool_call: 原始工具调用对象。
            result: 统一工具执行结果。
        """

        if tool_call.func_name in HISTORY_SILENT_TOOL_NAMES:
            self._remove_tool_call_from_history(tool_call)
            return

        history_content = result.get_history_content()
        if not history_content:
            history_content = "工具执行成功。" if result.success else f"工具 {tool_call.func_name} 执行失败。"

        self._runtime._chat_history.append(
            ToolResultMessage(
                content=history_content,
                timestamp=datetime.now(),
                tool_call_id=tool_call.call_id,
                tool_name=tool_call.func_name,
                success=result.success,
            )
        )

    def _remove_tool_call_from_history(self, tool_call: ToolCall) -> None:
        """从历史里的 assistant 消息中移除控制类工具调用。"""

        tool_call_id = str(tool_call.call_id or "").strip()
        if not tool_call_id:
            return

        for index in range(len(self._runtime._chat_history) - 1, -1, -1):
            message = self._runtime._chat_history[index]
            if not isinstance(message, AssistantMessage) or not message.tool_calls:
                continue

            remaining_tool_calls = [
                existing_tool_call
                for existing_tool_call in message.tool_calls
                if str(existing_tool_call.call_id or "").strip() != tool_call_id
            ]
            if len(remaining_tool_calls) == len(message.tool_calls):
                continue

            if remaining_tool_calls:
                message.tool_calls = remaining_tool_calls
            elif message.content.strip():
                message.tool_calls = []
            else:
                del self._runtime._chat_history[index]
            return

    def _append_timing_gate_execution_result(
        self,
        response: ChatResponse,
        tool_call: ToolCall,
        result: ToolExecutionResult,
    ) -> None:
        """将 Timing Gate 的决策链写入历史，供后续门控复用。"""

        self._runtime._chat_history.append(
            AssistantMessage(
                content=response.content or "",
                timestamp=response.raw_message.timestamp,
                tool_calls=[tool_call],
                source_kind="timing_gate",
            )
        )
        if tool_call.func_name == "wait":
            return
        self._append_tool_execution_result(tool_call, result)

    def _build_tool_result_summary(self, tool_call: ToolCall, result: ToolExecutionResult) -> str:
        """构建用于终端展示的工具结果摘要。"""

        history_content = result.get_history_content().strip()
        if not history_content:
            history_content = result.error_message.strip()
        if not history_content:
            history_content = "执行成功" if result.success else "执行失败"

        summary_prefix = "[成功]" if result.success else "[失败]"
        normalized_content = self._truncate_tool_record_text(history_content, max_length=200)
        return f"- {tool_call.func_name} {summary_prefix}: {normalized_content}"

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
            normalized_detail = self._normalize_tool_record_value(monitor_detail)

        monitor_card = result.metadata.get("monitor_card")
        normalized_card = None
        if monitor_card is not None:
            normalized_card = self._normalize_tool_record_value(monitor_card)

        monitor_sub_cards = result.metadata.get("monitor_sub_cards")
        normalized_sub_cards = None
        if monitor_sub_cards is not None:
            normalized_sub_cards = self._normalize_tool_record_value(monitor_sub_cards)

        return {
            "tool_call_id": tool_call.call_id,
            "tool_name": tool_call.func_name,
            "tool_title": tool_spec.title.strip() if tool_spec is not None and tool_spec.title.strip() else "",
            "tool_args": self._normalize_tool_record_value(
                invocation.arguments if isinstance(invocation.arguments, dict) else {}
            ),
            "success": result.success,
            "duration_ms": round(duration_ms, 2),
            "summary": self._build_tool_result_summary(tool_call, result),
            "detail": normalized_detail,
            "card": normalized_card,
            "sub_cards": normalized_sub_cards,
        }

    async def _handle_tool_calls(
        self,
        tool_calls: list[ToolCall],
        latest_thought: str,
        anchor_message: SessionMessage,
    ) -> tuple[bool, str, list[str], list[dict[str, Any]]]:
        """执行一批统一工具调用。

        Args:
            tool_calls: 模型返回的工具调用列表。
            latest_thought: 当前轮的最新思考文本。
            anchor_message: 当前轮的锚点消息。

        Returns:
            tuple[bool, str, list[str], list[dict[str, Any]]]: 是否需要暂停当前思考循环、
            触发暂停的工具名、工具结果摘要列表，以及最终监控事件使用的工具详情列表。
        """

        tool_result_summaries: list[str] = []
        tool_monitor_results: list[dict[str, Any]] = []

        if self._runtime._tool_registry is None:
            for tool_call in tool_calls:
                invocation = self._build_tool_invocation(tool_call, latest_thought)
                result = ToolExecutionResult(
                    tool_name=tool_call.func_name,
                    success=False,
                    error_message="统一工具注册表尚未初始化。",
                )
                await self._store_tool_execution_record(invocation, result, None)
                self._append_tool_execution_result(tool_call, result)
                tool_result_summaries.append(self._build_tool_result_summary(tool_call, result))
                tool_monitor_results.append(
                    self._build_tool_monitor_result(tool_call, invocation, result, duration_ms=0.0, tool_spec=None)
                )
            return False, "", tool_result_summaries, tool_monitor_results

        execution_context = self._build_tool_execution_context(latest_thought, anchor_message)
        availability_context = self._build_tool_availability_context()
        tool_spec_map = {
            tool_spec.name: tool_spec
            for tool_spec in await self._runtime._tool_registry.list_tools(availability_context)
        }
        total_tool_count = len(tool_calls)
        for tool_index, tool_call in enumerate(tool_calls, start=1):
            invocation = self._build_tool_invocation(tool_call, latest_thought)
            self._runtime._update_stage_status(
                f"工具执行 · {invocation.tool_name}",
                f"第 {tool_index}/{total_tool_count} 个工具",
            )
            tool_started_at = time.time()
            if not self._runtime.is_action_tool_currently_available(invocation.tool_name):
                result = ToolExecutionResult(
                    tool_name=invocation.tool_name,
                    success=False,
                    error_message=(
                        f"工具 {invocation.tool_name} 当前未直接暴露给 planner。"
                        "如果它在 deferred tools 提示中，请先调用 tool_search。"
                    ),
                )
            else:
                result = await self._runtime._tool_registry.invoke(invocation, execution_context)
            tool_duration_ms = (time.time() - tool_started_at) * 1000
            await self._store_tool_execution_record(
                invocation,
                result,
                tool_spec_map.get(invocation.tool_name),
            )
            self._append_tool_execution_result(tool_call, result)
            tool_result_summaries.append(self._build_tool_result_summary(tool_call, result))
            tool_monitor_results.append(
                self._build_tool_monitor_result(
                    tool_call,
                    invocation,
                    result,
                    tool_duration_ms,
                    tool_spec=tool_spec_map.get(invocation.tool_name),
                )
            )

            if not result.success and tool_call.func_name == "reply":
                logger.warning(f"{self._runtime.log_prefix} 回复工具未生成可见消息，将继续下一轮循环")

            if bool(result.metadata.get("pause_execution", False)):
                return True, invocation.tool_name, tool_result_summaries, tool_monitor_results

        return False, "", tool_result_summaries, tool_monitor_results
