"""Maisaka 推理引擎。"""

from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING, Any, Literal, Optional

from rich.panel import Panel

import asyncio
import difflib
import json
import time

from src.chat.heart_flow.heartFC_utils import CycleDetail
from src.chat.message_receive.message import SessionMessage
from src.cli.console import console
from src.common.data_models.message_component_data_model import EmojiComponent, ImageComponent, MessageSequence, TextComponent
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.learners.behavior_selector import behavior_pattern_selector
from src.llm_models.exceptions import ReqAbortException, RespNotOkException
from src.llm_models.payload_content.tool_option import ToolCall
from src.services import database_service as database_api
from src.services.memory_service import memory_service

from src.maisaka.builtin_tool import (
    build_builtin_tool_handlers as build_split_builtin_tool_handlers,
    get_builtin_tool_visibility,
    get_timing_tools,
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
    TIMING_GATE_INVALID_TOOL_HINT_SOURCE,
    ToolResultMessage,
    contains_complex_message,
)
from src.maisaka.focus import focus_mode_manager
from src.maisaka.context.post_processor import process_chat_history_after_cycle
from src.maisaka.context.history import build_prefixed_message_sequence, build_session_message_visible_text
from src.maisaka.memory.heuristic_injector import heuristic_memory_injector
from src.maisaka.memory.mid_term import build_mid_term_memory_message, insert_mid_term_memory_message
from src.maisaka.monitor.events import (
    emit_cycle_end,
    emit_cycle_start,
    emit_message_ingested,
    emit_planner_finalized,
    emit_timing_gate_result,
)
from src.maisaka.memory.person_profile import build_person_profile_injection_messages
from src.maisaka.context.planner_messages import build_planner_user_prefix_from_session_message
from src.maisaka.visual.mode_utils import resolve_enable_visual_planner

if TYPE_CHECKING:
    from .runtime import MaisakaHeartFlowChatting
    from src.maisaka.builtin_tool.provider import BuiltinToolHandler

logger = get_logger("maisaka_reasoning_engine")

TIMING_GATE_CONTEXT_DROP_HEAD_RATIO = 0.7
TIMING_GATE_MAX_ATTEMPTS = 3
TIMING_GATE_TOOL_NAMES = {"continue", "no_action", "wait"}
PLANNER_NO_TOOL_FINISH_THRESHOLD = 3
PLANNER_NO_TOOL_HINT_DISPLAY_PREFIX = "[Planner 工具选择提示]"
HISTORY_SILENT_TOOL_NAMES = {"finish"}
HISTORY_DEFERRED_TOOL_RESULT_NAMES = {"wait"}
TOOL_RESULT_MEDIA_SOURCE_KIND = "tool_result_media"
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
        anchor_message: Optional[SessionMessage] = None,
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

        if anchor_message is not None:
            self._append_behavior_selector_context_item(
                context_items,
                text=str(anchor_message.processed_plain_text or ""),
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
        anchor_message: Optional[SessionMessage] = None,
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
                anchor_message=anchor_message,
                source_messages=source_messages,
                selected_history=selected_history,
            ),
            include_context_in_prompt=False,
        )
        return self._insert_behavior_reference_message(selection.reference_text, history=target_history)

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
        anchor_message: SessionMessage,
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
                    anchor_message=anchor_message,
                )
            except Exception as exc:
                logger.debug(f"{self._runtime.log_prefix} 启发式记忆自然拉起失败，已跳过: {exc}")
                return ""

        async def build_profile_messages() -> list[str]:
            try:
                return await build_person_profile_injection_messages(
                    anchor_message=anchor_message,
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
        availability_context = self._build_tool_availability_context()
        tool_spec = await self._runtime._tool_registry.get_tool_spec(invocation.tool_name, availability_context)
        result = await self._runtime._tool_registry.invoke(invocation, execution_context)
        if store_record:
            await self._store_tool_execution_record(invocation, result, tool_spec)
        if append_history:
            self._append_tool_execution_result(tool_call, result)
        return invocation, result, tool_spec

    async def _run_timing_gate(
        self,
        anchor_message: SessionMessage,
    ) -> tuple[Literal["continue", "no_action", "wait"], Any, list[str], list[dict[str, Any]]]:
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
                f"{invalid_tool_text}，将按 no_action 处理"
            )
            self._runtime._enter_stop_state()
            tool_result_summaries.append(
                f"- no_action [非法 Timing 工具]: 返回了 {invalid_tool_text}，已停止本轮并等待新消息"
            )
            return "no_action", response, tool_result_summaries, tool_monitor_results

        if selected_tool_call is None:
            self._runtime._enter_stop_state()
            tool_result_summaries.append(
                "- no_action [非法 Timing 工具]: 已停止本轮并等待新消息"
            )
            return "no_action", response, tool_result_summaries, tool_monitor_results

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
                f"{self._runtime.log_prefix} Timing Gate 返回未知动作 {timing_action!r}，将按 no_action 处理"
            )
            self._runtime._enter_stop_state()
            tool_result_summaries.append(
                f"- no_action [未知 Timing 动作]: 返回了 {timing_action!r}，已停止本轮并等待新消息"
            )
            return "no_action", response, tool_result_summaries, tool_monitor_results
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
                model_name="",
                prompt_section=None,
            ),
            [f"- continue [强制跳过]: {reason}"],
            [],
        )

    @staticmethod
    def _build_planner_no_tool_hint(attempt_count: int) -> str:
        """构造 Planner 未选择工具时注入的重试提示。"""

        if attempt_count <= 1:
            return (
                "你刚刚只输出了分析，但没有选择任何工具。Planner 每轮思考后必须调用至少一个当前可用工具："
                "需要回复时调用 reply，需要先等待新消息时调用 no_action，需要结束本轮时调用 finish，"
                "需要信息时调用查询或查看类工具。请重新思考，并在本轮选择一个工具。"
            )

        return (
            "严厉提醒：这是你连续第二次没有调用工具。不要只输出分析文本；"
            "你必须立刻调用一个当前可用工具。没有要回复或查询的内容，也必须调用 no_action 或 finish。"
        )

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

    def _insert_planner_no_tool_hint(self, attempt_count: int) -> None:
        """在最新真实用户消息之后插入 Planner 工具选择提示。"""

        self._clear_planner_no_tool_hints()
        hint_message = ReferenceMessage(
            content=self._build_planner_no_tool_hint(attempt_count),
            timestamp=datetime.now(),
            reference_type=ReferenceMessageType.PLANNER_TOOL_HINT,
            remaining_uses_value=None,
            display_prefix=PLANNER_NO_TOOL_HINT_DISPLAY_PREFIX,
        )

        insert_index = len(self._runtime._chat_history)
        for index in range(len(self._runtime._chat_history) - 1, -1, -1):
            message = self._runtime._chat_history[index]
            if isinstance(message, SessionBackedMessage) and message.source_kind == "user":
                insert_index = index + 1
                break

        self._runtime._chat_history.insert(insert_index, hint_message)

    def _handle_planner_no_tool_retry(
        self,
        planner_no_tool_count: int,
        planner_extra_lines: list[str],
    ) -> tuple[int, str, str, bool]:
        """处理 Planner 未调用工具时的递进提示与终止策略。"""

        planner_no_tool_count += 1
        if planner_no_tool_count >= PLANNER_NO_TOOL_FINISH_THRESHOLD:
            self._clear_planner_no_tool_hints()
            self._finish_planner_continuation()
            self._runtime._enter_stop_state()
            cycle_end_detail = (
                f"Planner 连续 {PLANNER_NO_TOOL_FINISH_THRESHOLD} 次没有调用工具，"
                "已视为 finish，结束本轮思考并等待新消息。"
            )
            planner_extra_lines.append(
                f"状态：连续 {PLANNER_NO_TOOL_FINISH_THRESHOLD} 次未调用工具，已视为 finish"
            )
            return planner_no_tool_count, "finish", cycle_end_detail, True

        self._insert_planner_no_tool_hint(planner_no_tool_count)
        cycle_end_detail = (
            f"Planner 第 {planner_no_tool_count} 次没有调用工具，"
            "已插入工具选择提示并重试。"
        )
        planner_extra_lines.append(
            f"状态：第 {planner_no_tool_count} 次未调用工具，已插入工具选择提示"
        )
        logger.warning(
            f"{self._runtime.log_prefix} Planner 第 {planner_no_tool_count} 次未调用工具，"
            "已插入工具选择提示并重试"
        )
        return planner_no_tool_count, "planner_missing_tool_retry", cycle_end_detail, False

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
            "Timing Gate 只能调用当前可用的 continue、no_action 或 wait 中的一个工具。"
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

    def _is_planner_continuation_active(self) -> bool:
        """判断当前是否处于连续 Planner 状态。"""

        is_active = getattr(self._runtime, "_is_planner_continuation_active", None)
        if callable(is_active):
            return bool(is_active())
        return bool(getattr(self._runtime, "_planner_continuation_active", False))

    def _start_planner_continuation(self) -> None:
        """进入连续 Planner 状态。"""

        start_continuation = getattr(self._runtime, "_start_planner_continuation", None)
        if callable(start_continuation):
            start_continuation()
            return
        self._runtime._planner_continuation_active = True

    def _finish_planner_continuation(self) -> None:
        """结束连续 Planner 状态。"""

        finish_continuation = getattr(self._runtime, "_finish_planner_continuation", None)
        if callable(finish_continuation):
            finish_continuation()
            return
        self._runtime._planner_continuation_active = False

    def _should_run_initial_timing_gate(self) -> bool:
        """决定本批消息开始时是否需要先进入 Timing Gate。"""

        if not self._is_planner_continuation_active():
            return True

        consume_force_continue = getattr(self._runtime, "_consume_force_next_timing_continue_reason", None)
        if callable(consume_force_continue):
            force_continue_reason = consume_force_continue()
            if force_continue_reason:
                logger.info(f"{self._runtime.log_prefix} {force_continue_reason}")
        logger.info(f"{self._runtime.log_prefix} 连续 Planner 状态未结束，本轮跳过 Timing Gate")
        return False

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
                if not focus_mode_manager.can_decide(
                    self._runtime.session_id,
                    is_group_chat=self._runtime.chat_stream.is_group_session,
                ):
                    self._runtime._message_turn_scheduled = False
                    logger.debug(f"{self._runtime.log_prefix} 当前不在 focus 状态，忽略已排队的 Maisaka 触发")
                    continue

                message_triggered, timeout_triggered, proactive_triggered = self._drain_ready_turn_triggers(
                    queued_trigger
                )
                if proactive_triggered:
                    self._runtime._focus_cooldown_wakeup_scheduled = False
                silent_reply_frequency = self._runtime._is_reply_frequency_silent()

                if (
                    self._runtime._agent_state == self._runtime._STATE_WAIT
                    and not (timeout_triggered or proactive_triggered)
                    and not silent_reply_frequency
                ):
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
                    if timeout_triggered:
                        self._runtime._chat_history.append(
                            self._build_wait_completed_message(has_new_messages=True)
                        )
                    await self._ingest_messages(cached_messages)
                    anchor_message = cached_messages[-1]
                else:
                    anchor_message = (
                        self._runtime._proactive_anchor_message
                        if proactive_triggered
                        else self._get_timeout_anchor_message()
                    )
                    if anchor_message is None:
                        logger.warning(f"{self._runtime.log_prefix} wait 超时后没有可复用的锚点消息，跳过本轮")
                        continue
                    if proactive_triggered:
                        self._runtime._proactive_anchor_message = None
                    logger.info(f"{self._runtime.log_prefix} 等待超时后开始新一轮思考")
                    if self._runtime._pending_wait_tool_call_id:
                        self._runtime._chat_history.append(
                            self._build_wait_completed_message(has_new_messages=False)
                        )

                if silent_reply_frequency:
                    await self._handle_silent_turn(
                        cached_messages=cached_messages,
                        timeout_triggered=timeout_triggered,
                        proactive_triggered=proactive_triggered,
                    )
                    continue

                try:
                    timing_gate_required = self._should_run_initial_timing_gate()
                    planner_no_tool_count = 0
                    round_index = 0
                    while round_index < self._runtime._max_internal_rounds:
                        if round_index > 0 and self._runtime._has_pending_messages():
                            await self._runtime._wait_for_message_quiet_period()
                            self._runtime._message_turn_scheduled = False
                            pending_round_messages = self._runtime._collect_pending_messages()
                            if pending_round_messages:
                                await self._ingest_messages(pending_round_messages)
                                cached_messages = pending_round_messages
                                anchor_message = pending_round_messages[-1]
                                logger.info(
                                    f"{self._runtime.log_prefix} 内部轮次开始前已合并新消息: "
                                    f"消息数={len(pending_round_messages)} 回合={round_index + 1}"
                                )

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
                                self._runtime.record_no_action_decision_result(
                                    timing_action,
                                    source="timing_gate",
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
                                    self._finish_planner_continuation()
                                    if timing_action == "wait":
                                        cycle_end_reason = "timing_wait"
                                        cycle_end_detail = "Timing Gate 选择 wait，本轮不会进入 Planner，将在等待结束后继续。"
                                    else:
                                        cycle_end_reason = "timing_no_action"
                                        cycle_end_detail = "Timing Gate 选择 no_action，本轮不会进入 Planner。"
                                    logger.debug(
                                        f"{self._runtime.log_prefix} Timing Gate 结束当前回合: "
                                        f"回合={round_index + 1} 动作={timing_action}"
                                    )
                                    break
                            else:
                                logger.info(
                                    f"{self._runtime.log_prefix} Timing Gate 已完成 continue，继续执行 Planner: "
                                    f"回合={round_index + 1}"
                                )

                            self._start_planner_continuation()
                            planner_started_at = time.time()
                            current_stage_started_at = planner_started_at
                            self._runtime._update_stage_status("Planner", "组织上下文并请求模型", round_text=round_text)
                            action_tool_definitions, deferred_tools_reminder = await self._build_action_tool_definitions()
                            injected_user_messages = await self._build_planner_injected_user_messages(
                                anchor_message=anchor_message,
                                source_messages=cached_messages or [anchor_message],
                                deferred_tools_reminder=deferred_tools_reminder,
                            )
                            if not resolve_enable_visual_planner():
                                log_pending_image_recognition_before_text_planner(
                                    self._runtime._chat_history,
                                    log_prefix=self._runtime.log_prefix,
                                )
                            logger.info(
                                f"{self._runtime.log_prefix} 规划器开始执行: "
                                f"回合={round_index + 1} "
                                f"历史消息数={len(self._runtime._chat_history)} "
                                f"开始时间={planner_started_at:.3f}"
                            )
                            response = await self._run_interruptible_planner(
                                injected_user_messages=injected_user_messages or None,
                                tail_user_messages=self._runtime.build_focus_tail_user_messages() or None,
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
                                response.content = "我应该根据我上面思考的内容进行反思，重新思考我下一步的行动，我需要分析当前场景，对话，然后直接输出我的想法："
                                response.raw_message.content = "我应该根据我上面思考的内容进行反思，重新思考我下一步的行动，我需要分析当前场景，对话，然后直接输出我的想法："
                                logger.info(f"{self._runtime.log_prefix} 当前思考与上一轮过于相似，已替换为重新思考提示")

                            self._last_reasoning_content = reasoning_content
                            self._runtime._chat_history.append(response.raw_message)
                            tool_monitor_results = []

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
                                    response.content or "",
                                    anchor_message,
                                )
                                cycle_detail.time_records["tool_calls"] = time.time() - tool_started_at
                                if pause_tool_name == "no_action":
                                    self._runtime.record_no_action_decision_result("no_action", source="planner")
                                if should_pause:
                                    if pause_tool_name == "finish":
                                        self._finish_planner_continuation()
                                        cycle_end_reason = "finish"
                                        cycle_end_detail = "Planner 调用 finish，结束连续 Planner 并等待新消息。"
                                    elif pause_tool_name == "no_action":
                                        cycle_end_reason = "tool_pause:no_action"
                                        cycle_end_detail = "Planner 调用 no_action，保留连续 Planner 状态并等待新消息。"
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

                            (
                                planner_no_tool_count,
                                cycle_end_reason,
                                cycle_end_detail,
                                should_finish_after_no_tool,
                            ) = self._handle_planner_no_tool_retry(
                                planner_no_tool_count,
                                planner_extra_lines,
                            )
                            if should_finish_after_no_tool:
                                break
                            continue
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
                                model_name="",
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

                            await self._ingest_messages(interrupted_messages)
                            cached_messages = interrupted_messages
                            anchor_message = interrupted_messages[-1]
                            logger.info(
                                f"{self._runtime.log_prefix} 淇濇寔娲昏穬鐘舵€侊紝璺宠繃 Timing Gate 鐩存帴閲嶈瘯 Planner: "
                                f"鍥炲悎={round_index + 2}"
                            )
                            continue
                        finally:
                            completed_cycle = await self._end_cycle(cycle_detail)
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
                                timing_model_name=timing_response.model_name if timing_response is not None else None,
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
                                planner_model_name=response.model_name if response is not None else None,
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
                            self._runtime.record_no_action_cycle_result(cycle_end_reason)
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
        except RespNotOkException as exc:
            logger.error(
                f"{self._runtime.log_prefix} Maisaka 内部循环发生异常: "
                f"模型响应异常 HTTP {exc.status_code} - {exc}"
            )
            raise
        except Exception:
            logger.exception(f"{self._runtime.log_prefix} Maisaka 内部循环发生异常")
            raise

    async def _handle_silent_turn(
        self,
        *,
        cached_messages: list[SessionMessage],
        timeout_triggered: bool,
        proactive_triggered: bool,
    ) -> None:
        """回复频率为 0 时只消费消息和维护历史，不进入 Timing Gate/Planner。"""

        self._runtime._clear_force_next_timing_continue_state()
        if proactive_triggered:
            self._runtime._proactive_anchor_message = None

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
            f"不进入 Timing Gate/Planner；{trigger_text}"
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
            if self._runtime._has_chat_history_message(message.message_id):
                logger.debug(
                    f"{self._runtime.log_prefix} 跳过已恢复的重复消息上下文: "
                    f"message_id={message.message_id}"
                )
                continue

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
                f"{self._runtime.log_prefix} 开始生成中期聊天记录摘要: "
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
                logger.exception(f"{self._runtime.log_prefix} 生成中期聊天记录摘要失败，已跳过本次摘要插入")
                summary_result = None

            cycle_detail.time_records["mid_term_memory"] = time.time() - summary_started_at
            if summary_result is not None:
                final_history = insert_mid_term_memory_message(
                    final_history,
                    summary_result.message,
                    max_summary_count=max(0, int(global_config.chat.mid_term_memory_lenth)),
                )
                logger.info(
                    f"{self._runtime.log_prefix} 已生成中期聊天记录摘要: "
                    f"msg_id={summary_result.message.message_id} "
                    f"模型={summary_result.model_name or 'unknown'} "
                    f"token={summary_result.total_tokens}"
                )
            else:
                logger.debug(f"{self._runtime.log_prefix} 中期聊天记录摘要未产生可插入内容，已跳过")
        elif process_result.removed_messages:
            logger.debug(f"{self._runtime.log_prefix} 中期聊天记录摘要未启用，跳过摘要生成")

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

        chat_stream = self._runtime.chat_stream
        return ToolExecutionContext(
            session_id=self._runtime.session_id,
            stream_id=self._runtime.session_id,
            reasoning=latest_thought,
            is_group_chat=chat_stream.is_group_session,
            group_id=str(getattr(chat_stream, "group_id", "") or "").strip(),
            user_id=str(getattr(chat_stream, "user_id", "") or "").strip(),
            platform=str(getattr(chat_stream, "platform", "") or "").strip(),
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
            payload["description"] = tool_spec.description
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

        if invocation.tool_name == "no_action":
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

        description = ""
        if tool_spec is not None:
            description = tool_spec.description.strip()

        if normalized_args:
            arguments_text = self._truncate_tool_record_text(
                json.dumps(normalized_args, ensure_ascii=False),
                max_length=160,
            )
        else:
            arguments_text = "{}"

        if result.success:
            if description:
                return f"{description} 参数={arguments_text}；结果：{history_content or '执行成功'}"
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

        if tool_call.func_name in HISTORY_SILENT_TOOL_NAMES:
            self._remove_tool_call_from_history(tool_call)
            return

        if (
            tool_call.func_name in HISTORY_DEFERRED_TOOL_RESULT_NAMES
            and result.success
            and bool(result.metadata.get("pause_execution", False))
        ):
            return

        history_content = self._build_tool_result_history_content(tool_call, result)
        if not history_content:
            history_content = "工具执行成功。" if result.success else f"工具 {tool_call.func_name} 执行失败。"

        normalized_metadata = self._normalize_tool_record_value(result.metadata)
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
        deferred_post_history_messages: list[LLMContextMessage] = []

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
            is_unexpanded_tool = not self._runtime.is_action_tool_currently_available(invocation.tool_name)
            result = await self._runtime._tool_registry.invoke(invocation, execution_context)
            if is_unexpanded_tool and not result.success:
                result = self._append_deferred_tool_parameter_hint(result)
            tool_duration_ms = (time.time() - tool_started_at) * 1000
            await self._store_tool_execution_record(
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
                self._append_tool_post_history_messages(deferred_post_history_messages)
                return True, invocation.tool_name, tool_result_summaries, tool_monitor_results

        self._append_tool_post_history_messages(deferred_post_history_messages)
        return False, "", tool_result_summaries, tool_monitor_results
