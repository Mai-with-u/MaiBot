"""每个 Focus 共享组唯一的前台 Planner；聊天服务保持独立归属。"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Set, Tuple

import asyncio
import time
import uuid

from src.chat.message_receive.message import SessionMessage
from src.chat.message_receive.chat_manager import chat_manager
from src.common.logger import get_logger
from src.common.message_repository import find_messages
from src.common.utils.utils_config import ChatConfigUtils
from src.config.config import global_config
from src.core.tooling import ToolExecutionResult
from src.llm_models.exceptions import (
    EmptyResponseException,
    LLMTaskTimeoutError,
    NetworkConnectionError,
    RespNotOkException,
    RespParseException,
)
from src.llm_models.payload_content.context_item import FunctionCallItem
from src.maisaka.chat_loop_service import ChatResponse, MaisakaChatLoopService
from src.maisaka.context.messages import (
    ContentContextMessage,
    LLMContextMessage,
    ModelOutputContextMessage,
    ReferenceMessage,
    SessionBackedMessage,
    ToolResultMessage,
    ToolResultMediaMessage,
)
from src.maisaka.context.compaction import ContextSummaryMessage, build_context_summary
from src.maisaka.context.post_processor import process_chat_history_after_cycle
from src.maisaka.mode_policy import build_consecutive_wait_reminders
from src.maisaka.monitor.events import emit_flow_step, emit_planner_snapshot, emit_session_start
from src.maisaka.display.stage_status_board import remove_stage_status, update_stage_status
from src.maisaka.tool_result_condenser import condense_tool_result, should_condense_tool_result

from .context import FocusCheckpointStore, FocusContextEntry
from .tools import FocusToolRouter

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_focus")
MAX_SEEN_MESSAGES = 20000


class FocusChatLoopService(MaisakaChatLoopService):
    """组级请求没有默认聊天，不把某个成员的身份或额外规则冒充成整个组。"""

    def _get_chat_prompt_name(self) -> str:
        return "maisaka_chat_focus"


class FocusGroupRuntime:
    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        self.members: Dict[str, MaisakaHeartFlowChatting] = {}
        self.entries: List[FocusContextEntry] = []
        self._pending: List[FocusContextEntry] = []
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._considered: Dict[str, int] = {}
        self._sequence = 0
        self._generation = 0
        self._event = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        self._closed = False
        self._hydrating = False
        self._wait_until: Optional[float] = None
        self._wait_session_id: Optional[str] = None
        self._consecutive_waits = 0
        self._sent: List[FocusContextEntry] = []
        self._active_batch: Optional[FocusContextEntry] = None
        self._post_messages: List[LLMContextMessage] = []
        self._run_id = uuid.uuid4().hex
        self._cycle = 0
        self._state = "stop"
        self._planner_started_at = 0.0
        self._planner_ended_at = 0.0
        self._planner_retry_after_sequence: Optional[int] = None
        self._service = FocusChatLoopService(is_group_chat=True)
        self.tools = FocusToolRouter(self)
        self._store = FocusCheckpointStore(scope_id)
        self._checkpoint_lock = asyncio.Lock()
        self._checkpoint_task: Optional[asyncio.Task] = None
        self._checkpoint_dirty = False

    @property
    def monitor_id(self) -> str:
        """仅用于观察事件归档，不作为业务聊天流 ID。"""
        return "focus:" + self.scope_id

    @property
    def monitor_name(self) -> str:
        return "Focus · " + "、".join(member.session_name for member in self.members.values())

    async def publish_monitor_session(self) -> None:
        await emit_session_start(
            self.monitor_id, self.monitor_name,
            is_group_chat=False, group_id=None, user_id=None, platform="",
            focus_session_ids=list(self.members),
        )

    def _publish_monitor_stage(self, stage: str, detail: str) -> None:
        update_stage_status(
            session_id=self.monitor_id, session_name=self.monitor_name,
            stage=stage, detail=detail, agent_state=self.state, wait_until=self._wait_until,
            focus_session_ids=list(self.members),
        )

    @property
    def history(self) -> List[LLMContextMessage]:
        return [message for entry in self.entries for message in entry.messages]

    @property
    def max_context_size(self) -> int:
        return max(2, global_config.chat.max_context_size)

    @property
    def state(self) -> str:
        if self._state == "failed":
            return "failed"
        if self._wait_until is not None:
            return "wait"
        return self._state

    async def restore(self) -> Set[str]:
        payload = await asyncio.to_thread(self._store.load)
        if not payload:
            return set()
        self.entries = payload["entries"]
        session_ids = set().union(*(entry.session_ids for entry in self.entries))
        from .manager import focus_mode_manager

        for sid in session_ids:
            session = await asyncio.to_thread(chat_manager.get_existing_session_by_session_id, sid)
            if (
                session is None
                or not focus_mode_manager.is_enabled_for_session(sid)
                or focus_mode_manager.get_focus_scope_key(sid) != self.scope_id
            ):
                self.entries = [entry for entry in self.entries if sid not in entry.session_ids]
        self._sequence = payload["sequence"]
        self._considered = payload["considered"]
        self._seen = OrderedDict((key, None) for key in payload["seen"])
        self._wait_until = payload["wait_until"]
        self._wait_session_id = payload["wait_session_id"]
        restored_session_ids = set().union(*(entry.session_ids for entry in self.entries))
        if self._wait_session_id not in restored_session_ids:
            self._wait_until = None
            self._wait_session_id = None
        self._consecutive_waits = payload["consecutive_waits"]
        # 未完成判断的输入仍在时间线中，运行循环按进度继续处理；不重放旧工具。
        return restored_session_ids

    async def checkpoint(self) -> None:
        async with self._checkpoint_lock:
            entries = self._closed_entries()
            entries.extend(self._pending)
            entries.extend(self._sent)
            state = {
                "sequence": self._sequence,
                "considered": dict(self._considered),
                "seen": list(self._seen),
                "wait_until": self._wait_until,
                "wait_session_id": self._wait_session_id,
                "consecutive_waits": self._consecutive_waits,
            }
            await asyncio.to_thread(self._store.save, entries, state)

    def _closed_entries(self) -> List[FocusContextEntry]:
        """只在快照/取消时闭合未完成调用，不修改仍在执行的实时工具批次。"""
        entries = [
            FocusContextEntry(
                list(entry.messages),
                set(entry.session_ids),
                entry.sequence,
                dict(entry.watermarks),
                {sid: list(records) for sid, records in entry.inputs.items()},
            )
            for entry in self.entries
        ]
        for original, entry in zip(self.entries, entries, strict=True):
            completed = {message.tool_call_id for message in entry.messages if isinstance(message, ToolResultMessage)}
            for message in list(entry.messages):
                if isinstance(message, ModelOutputContextMessage):
                    for call in message.tool_calls:
                        if call.call_id not in completed:
                            entry.messages.append(
                                ToolResultMessage(
                                    content="运行进程在工具批次中断；此调用的完成状态需要核实，禁止自动重放。",
                                    timestamp=datetime.now(),
                                    tool_call_id=call.call_id,
                                    logical_turn_id=message.output_item.meta.logical_turn_id,
                                    tool_name=call.func_name,
                                    success=False,
                                    metadata={"execution_state": "unknown"},
                                )
                            )
            if original is self._active_batch:
                entry.messages.extend(self._post_messages)
        return entries

    def add_member(self, runtime: MaisakaHeartFlowChatting) -> None:
        self.members[runtime.session_id] = runtime
        # 恢复只属于目标聊天的工具媒体，不把共享思考链送给回复器。
        for message in self.history:
            if (
                isinstance(message, ToolResultMediaMessage)
                and message.session_id == runtime.session_id
            ):
                runtime._chat_history.append(message)
        if self._wait_until is not None or any(
            entry.progress.get(runtime.session_id, 0) > self._considered.get(runtime.session_id, 0)
            for entry in self.entries
        ):
            self._event.set()
            self._ensure_worker()

    def is_member_valid(self, runtime: MaisakaHeartFlowChatting) -> bool:
        from .manager import focus_mode_manager

        return (
            runtime._running
            and runtime._focus_group is self
            and focus_mode_manager.is_enabled_for_session(
                runtime.session_id, is_group_chat=runtime.chat_stream.is_group_session
            )
            and focus_mode_manager.get_focus_scope_key(runtime.session_id) == self.scope_id
        )

    async def remove_member(self, session_id: str, *, clear_context: bool) -> None:
        # 先撤销本轮资格，再取消本地请求；迟到结果不能继续产生动作。
        self._generation += 1
        self._service.planner_request_snapshot = None
        await self._cancel_worker()
        self.members.pop(session_id, None)
        pending = [entry for entry in self._pending if session_id in entry.session_ids]
        self._pending = [entry for entry in self._pending if session_id not in entry.session_ids]
        if not clear_context:
            self.entries.extend(pending)
            self.entries.extend(entry for entry in self._sent if session_id in entry.session_ids)
        self._sent = [entry for entry in self._sent if session_id not in entry.session_ids]
        if clear_context:
            # 混合思考/摘要不能可靠拆分，涉及被清空会话的整个原子段都失效。
            self.entries = [entry for entry in self.entries if session_id not in entry.session_ids]
            self._considered.pop(session_id, None)
            self._seen = OrderedDict((key, None) for key in self._seen if not key.startswith(session_id + "\0"))
        if self._wait_session_id == session_id:
            self._finish_wait("发起等待的聊天已退出共享组")
        await self.checkpoint()
        if self.members:
            await self.publish_monitor_session()
            self._ensure_worker()
            self._event.set()

    async def register_message(
        self,
        runtime: MaisakaHeartFlowChatting,
        message: SessionMessage,
        history_message: Optional[LLMContextMessage],
    ) -> None:
        if not self._remember(runtime.session_id, message.message_id):
            return
        if history_message is not None:
            self._queue_entry(runtime.session_id, history_message)
        if message.is_at and self._wait_until is not None:
            self._finish_wait(f"{runtime.session_name} 收到 @")
        # 所有成员输入统一进入下一次快照；@ 只负责提前结束整组等待。
        self._event.set()
        await self.checkpoint()
        self._ensure_worker()

    def _remember(self, session_id: str, message_id: str) -> bool:
        key = session_id + "\0" + message_id
        if key in self._seen:
            return False
        self._seen[key] = None
        while len(self._seen) > MAX_SEEN_MESSAGES:
            self._seen.popitem(last=False)
        return True

    def _queue_entry(self, session_id: str, message: LLMContextMessage) -> None:
        self._sequence += 1
        inputs = (
            {session_id: [(self._sequence, message.message_id)]} if isinstance(message, SessionBackedMessage) else {}
        )
        self._pending.append(
            FocusContextEntry(
                [message],
                {session_id},
                self._sequence,
                inputs=inputs,
            )
        )

    async def unprocessed_messages(self, session_id: str) -> List[SessionMessage]:
        """分组变更只交接未判断的真实输入，不搬运旧组思考或跨聊天摘要。"""
        entries = [*self.entries, *self._pending]
        records = sorted(
            {
                (sequence, message_id)
                for entry in entries
                for sequence, message_id in entry.inputs.get(session_id, [])
                if sequence > self._considered.get(session_id, 0)
            }
        )
        runtime = self.members[session_id]
        messages = []
        for _, message_id in records:
            message = runtime.find_source_message_by_id(message_id)
            if message is None:
                stored = await asyncio.to_thread(find_messages, session_id=session_id, message_id=message_id, limit=1)
                if not stored:
                    raise RuntimeError(f"Focus 待处理消息不存在：session_id={session_id} message_id={message_id}")
                message = stored[0]
            messages.append(message)
        return messages

    def register_proactive(self, runtime: MaisakaHeartFlowChatting, trigger: SessionMessage) -> None:
        if not self._remember(runtime.session_id, "internal:" + trigger.message_id):
            return
        text = trigger.processed_plain_text
        if runtime._chat_history:
            last = runtime._chat_history[-1]
            if isinstance(last, ContentContextMessage) and last.message_id == trigger.message_id:
                text = last.processed_plain_text
        notice = ReferenceMessage(
            content=f'<focus_event session_id="{escape(runtime.session_id, quote=True)}" '
            f'chat_name="{escape(runtime.session_name, quote=True)}">\n{text}\n</focus_event>',
            timestamp=datetime.now(),
            remaining_uses_value=None,
            display_prefix="[聊天任务事件]",
        )
        self._queue_entry(runtime.session_id, notice)
        self._event.set()
        self._ensure_worker()
        self._schedule_checkpoint()

    def record_sent_message(self, runtime: MaisakaHeartFlowChatting, message: LLMContextMessage) -> None:
        # 发言可在工具执行过程中到达，延后追加，保证 function call/result 原子段不被拆开。
        self._sent.append(FocusContextEntry([message], {runtime.session_id}))
        self._schedule_checkpoint()

    def _schedule_checkpoint(self) -> None:
        """同步回调也要持久化；等待期间不能等到下一次 Planner 才保存后台结果。"""
        self._checkpoint_dirty = True
        if self._checkpoint_task is None or self._checkpoint_task.done():
            self._checkpoint_task = asyncio.create_task(self._flush_callbacks())

    async def _flush_callbacks(self) -> None:
        try:
            while self._checkpoint_dirty:
                self._checkpoint_dirty = False
                await self.checkpoint()
        except Exception:
            self._state = "failed"
            self._generation += 1
            logger.exception("Focus 回调事件保存失败: scope=%s", self.scope_id)
            await self._cancel_worker()
            self._set_stage("失败", "后台事件保存失败，已停止共享 Planner；请检查日志")

    def has_message(self, session_id: str, message_id: str) -> bool:
        return session_id + "\0" + message_id in self._seen

    def _ensure_worker(self) -> None:
        if self._closed or self._state == "failed" or self._hydrating:
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self.run())

    def _set_stage(self, stage: str, detail: str, *, agent_state: Literal["running", "stop"] = "running") -> None:
        if self._state != "failed":
            self._state = agent_state
        self._publish_monitor_stage(stage, detail)
        for runtime in self.members.values():
            runtime._agent_state = runtime._STATE_STOP if self.state == "failed" else self.state
            # Planner 阶段只属于组级流，成员展示归属，避免看起来各自运行一轮规划。
            runtime._update_stage_status(
                "失败" if self.state == "failed" else "Focus 共享",
                f"由 {self.monitor_name} 统一处理；{detail}",
                wait_until=self._wait_until,
            )

    def _take_pending_inputs(self) -> None:
        """按登记顺序一次接入整组输入，成员不再维护另一份待处理队列。"""
        self.entries.extend(self._pending)
        self._pending.clear()
        self.entries.extend(self._sent)
        self._sent.clear()

    def _roster(self) -> str:
        chats = []
        for sid, runtime in self.members.items():
            if not self.is_member_valid(runtime):
                continue
            instructions = ChatConfigUtils.get_chat_prompt_for_chat(sid, runtime.chat_stream.is_group_session)
            chats.append(f"在 {runtime.session_name}({sid}) 中的注意信息：{instructions or '无额外注意信息'}")
        return "\n".join(chats)

    def _apply_reply_control(self, result: ToolExecutionResult, session_id: str) -> None:
        """成功回复后的控制作用于整个共享组，复用 reply 已校验的行为与等待时长。"""
        if not result.success:
            return
        action = result.metadata.get("after_reply", "continue")
        if action == "continue":
            return
        if action == "wait":
            seconds = result.metadata["wait_seconds"]
            self._consecutive_waits += 1
            self._wait_until = time.time() + seconds
            self._wait_session_id = session_id
            content = f"已为整个 Focus 共享组登记等待 {seconds} 秒；@ 可以提前唤醒。"
            if any(
                isinstance(message, SessionBackedMessage) and message.original_message.is_at
                for entry in self._pending
                for message in entry.messages
            ):
                self._finish_wait("等待登记前已有尚未提交的 @ 消息")
                content = "已登记整组等待，但已有尚未提交的 @ 消息，立即继续下一次思考。"
        else:
            self._consecutive_waits = 0
            content = "已结束整个 Focus 共享组的本轮思考。"
        result.content = f"{result.get_history_content()}\n{content}"
        result.metadata["pause_execution"] = True

    def _finish_wait(self, reason: str) -> None:
        sid = self._wait_session_id
        self._wait_until = None
        self._wait_session_id = None
        if sid is not None and self.members:
            # 等待结束是整组事件；发起者退出后仍交给剩余成员，不留下孤立队列。
            self._sequence += 1
            self._pending.append(FocusContextEntry(
                [ReferenceMessage(
                    content=f"session_id={sid} 发起的整组等待已结束：{reason}。请结合所有聊天的新消息继续判断。",
                    timestamp=datetime.now(),
                    remaining_uses_value=None,
                    display_prefix="[Focus 等待结束]",
                )],
                set(self.members),
                self._sequence,
            ))
        self._event.set()

    async def run(self) -> None:
        try:
            while not self._closed and self.members:
                if self._planner_retry_after_sequence is not None:
                    # 失败请求没有确认输入进度，不能因旧输入仍未处理而立即重试。
                    # 请求期间到达的新输入也能触发下一轮，无需再等一条消息。
                    if self._sequence <= self._planner_retry_after_sequence:
                        self._event.clear()
                        await self._event.wait()
                        continue
                    self._planner_retry_after_sequence = None
                if self._wait_until is not None:
                    self._set_stage("等待", "整组暂停，@ 可以提前唤醒")
                    remaining = max(0.0, self._wait_until - time.time())
                    self._event.clear()
                    try:
                        await asyncio.wait_for(self._event.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        self._finish_wait("等待到期")
                    continue
                restored_pending = any(
                    sequence > self._considered.get(sid, 0)
                    for entry in self.entries
                    for sid, sequence in entry.progress.items()
                    if sid in self.members
                )
                if not self._pending and not restored_pending:
                    self._state = "stop"
                    self._publish_monitor_stage("空闲", "等待组内新消息")
                    for runtime in self.members.values():
                        runtime._agent_state = runtime._STATE_STOP
                        runtime._update_stage_status("Focus 共享", f"由 {self.monitor_name} 统一处理，等待组内新消息")
                    self._event.clear()
                    await self._event.wait()
                    continue
                await self._run_turn()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._state = "failed"
            self._set_stage("失败", "共享 Planner 运行失败，输入已保留；请检查日志并重启运行时")
            logger.exception("Focus 共享 Planner 运行失败: scope=%s", self.scope_id)
        finally:
            await self.checkpoint()

    async def _run_turn(self) -> None:
        from src.maisaka.runtime import MAX_INTERNAL_ROUNDS

        for _ in range(MAX_INTERNAL_ROUNDS):
            if self._closed or self._wait_until is not None:
                return
            self._take_pending_inputs()
            await self._compact()
            await self.tools.refresh()
            generation = self._generation
            references = await self._build_references()
            # 整理参考信息期间到达的所有输入也接入本次请求；请求发出后使用固定快照。
            self._take_pending_inputs()
            related = set(self.members)
            covered = [(sid, sequence) for entry in self.entries for sid, sequence in entry.progress.items()]
            history = self.history
            self._cycle += 1
            logical_turn_id = uuid.uuid4().hex
            self._set_stage("Planner", f"汇合 {len(self.members)} 个聊天，等待模型响应")
            started = time.time()
            self._planner_started_at = started
            request_sequence = self._sequence
            try:
                response = await self._service.chat_loop_step(
                    history,
                    tool_definitions=self.tools.definitions(),
                    injected_user_messages=[self._roster(), self.tools.deferred_notice(), *references],
                    tail_user_messages=build_consecutive_wait_reminders(self._consecutive_waits),
                    max_context_size=max(self.max_context_size, sum(message.count_in_context for message in history) + 1),
                    logical_turn_id=logical_turn_id,
                )
            except (
                EmptyResponseException,
                LLMTaskTimeoutError,
                NetworkConnectionError,
                RespNotOkException,
                RespParseException,
            ) as exc:
                self._planner_ended_at = time.time()
                self._service.planner_request_snapshot = None
                self._planner_retry_after_sequence = request_sequence
                self._set_stage("请求失败", f"输入已保留，等待新消息后继续；{exc}", agent_state="stop")
                logger.exception(f"Focus 共享 Planner 请求失败，等待新输入: scope={self.scope_id}")
                # 只恢复已知模型请求失败；保存或上下文一致性错误仍由外层停止并完整报告。
                await self.checkpoint()
                return
            self._planner_ended_at = time.time()
            if generation != self._generation or self._closed:
                self._service.planner_request_snapshot = None
                return
            # 有效模型响应确认本次快照覆盖的消息；工具成功与否单独记录，不能误认为已回复。
            for sid, sequence in covered:
                self._considered[sid] = max(self._considered.get(sid, 0), sequence)
            for entry in self.entries:
                entry.inputs = {
                    sid: [
                        (sequence, message_id)
                        for sequence, message_id in records
                        if sequence > self._considered.get(sid, 0)
                    ]
                    for sid, records in entry.inputs.items()
                }
            batch = FocusContextEntry(list(response.raw_messages), related)
            self.entries.append(batch)
            await self.checkpoint()
            await self._emit_response(
                response,
                [],
                started,
                False,
                event_type="planner.progress",
                active_tool_call_id=response.tool_calls[0].call_id if response.tool_calls else "",
            )
            monitor_tools, paused = await self._execute_tools(response, batch, generation)
            if self._sent:
                self.entries.extend(self._sent)
                self._sent.clear()
            await self._emit_response(response, monitor_tools, started, paused)
            await self.checkpoint()
            if paused or not response.tool_calls:
                if self._wait_until is None:
                    self._consecutive_waits = 0
                return
        logger.info("Focus 达到本轮内部思考上限: scope=%s", self.scope_id)

    async def _build_references(self) -> List[str]:
        """沿用各聊天的自动记忆/人物注入，并为每份参考信息标明真实来源。"""
        grouped: Dict[str, List[SessionMessage]] = {}
        for entry in self.entries:
            for message in entry.messages:
                if not isinstance(message, SessionBackedMessage):
                    continue
                sid = message.original_message.session_id
                if entry.progress.get(sid, 0) > self._considered.get(sid, 0) and sid in self.members:
                    grouped.setdefault(sid, []).append(message.original_message)
        references = []
        for sid, messages in grouped.items():
            member = self.members[sid]
            injected = await member._reasoning_engine._build_planner_injected_user_messages(
                profile_message=messages[-1],
                source_messages=messages,
                deferred_tools_reminder="",
            )
            jargon = member._reasoning_engine._refresh_jargon_reference_message()
            if jargon is not None:
                injected.append(jargon.processed_plain_text)
            if injected:
                references.append(
                    f'<focus_reference session_id="{escape(sid, quote=True)}">\n'
                    + "\n".join(injected)
                    + "\n</focus_reference>"
                )
        return references

    async def _execute_tools(
        self,
        response: ChatResponse,
        batch: FocusContextEntry,
        generation: int,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        monitor = []
        paused = False
        self._active_batch = batch
        self._post_messages = []
        call_turns = {
            item.tool_call.call_id: item.meta.logical_turn_id
            for item in response.output_items
            if isinstance(item, FunctionCallItem)
        }
        for index, call in enumerate(response.tool_calls):
            tool_started = time.time()
            runtime = None
            spec = None
            condensed = None
            if paused or generation != self._generation or self._closed:
                result = ToolExecutionResult(
                    tool_name=call.func_name,
                    success=False,
                    error_message="本批次已暂停或会话归属已失效，此工具未执行。",
                )
            else:
                self._set_stage("工具执行", f"{call.func_name} → {call.args.get('session_id', '')}")
                invocation, result, runtime, spec = await self.tools.invoke(
                    call.func_name,
                    dict(call.args),
                    call.call_id,
                    response.content or "",
                )
                if runtime is not None:
                    if result.metadata.get("after_reply") != "wait":
                        self._consecutive_waits = 0
                    if spec is not None and spec.provider_name == "maisaka_builtin" and spec.name == "reply":
                        self._apply_reply_control(result, runtime.session_id)
                    await runtime._reasoning_engine._record_tool_execution_effects(invocation, result, spec)
                    raw_content = result.get_history_content()
                    if global_config.capabilities.tool_result_condense and should_condense_tool_result(
                        call.func_name,
                        raw_content,
                        success=result.success,
                        already_previewed=result.metadata.get("web_result_preview") is True,
                    ):
                        condensed = await condense_tool_result(runtime, call, raw_content, response.content or "")
            metadata = dict(result.metadata)
            metadata["focus_scope_id"] = self.scope_id
            content = condensed if condensed is not None else result.get_history_content()
            if runtime is not None:
                content = runtime._reasoning_engine._build_tool_result_history_content(
                    call, result, condensed_content=condensed
                )
            batch.messages.append(
                ToolResultMessage(
                    content=content or ("工具执行成功。" if result.success else "工具执行失败。"),
                    timestamp=datetime.now(),
                    tool_call_id=call.call_id,
                    logical_turn_id=call_turns[call.call_id],
                    tool_name=call.func_name,
                    success=result.success,
                    metadata=metadata,
                )
            )
            if runtime is not None:
                before_media = len(runtime._chat_history)
                runtime._reasoning_engine._append_tool_result_media_messages(call, result)
                media = runtime._chat_history[before_media:]
                self._post_messages.extend(media)
                post = [message for message in result.post_history_messages if isinstance(message, LLMContextMessage)]
                runtime._chat_history.extend(post)
                self._post_messages.extend(post)
            item = {
                "tool_call_id": call.call_id,
                "tool_name": call.func_name,
                "tool_args": dict(call.args),
                "session_id": call.args.get("session_id", ""),
                "session_name": runtime.session_name if runtime is not None else "",
                "success": result.success,
                "error_message": result.error_message,
                "summary": content,
                "started_at": tool_started,
                "ended_at": time.time(),
                "duration_ms": (time.time() - tool_started) * 1000,
                "detail": result.metadata.get("monitor_detail"),
                "card": result.metadata.get("monitor_card"),
                "sub_cards": result.metadata.get("monitor_sub_cards"),
            }
            if runtime is not None:
                item.update(
                    runtime._reasoning_engine._build_tool_monitor_result(
                        call,
                        invocation,
                        result,
                        item["duration_ms"],
                        spec,
                        condensed,
                    )
                )
                # 监控仍展示模型实际填写的路由参数，底层 Provider 使用的是剥离后的业务参数。
                item["tool_args"] = dict(call.args)
            monitor.append(item)
            await self.checkpoint()
            if result.success and (result.metadata.get("pause_execution") or result.stop_after_execution):
                paused = True
            next_call = (
                response.tool_calls[index + 1].call_id if index + 1 < len(response.tool_calls) and not paused else ""
            )
            await self._emit_response(
                response,
                monitor,
                self._planner_started_at,
                paused,
                event_type="planner.progress",
                active_tool_call_id=next_call,
            )
        # 所有 tool result 连续闭合后再追加媒体/user 材料，不能拆开多工具调用协议段。
        batch.messages.extend(self._post_messages)
        self._active_batch = None
        self._post_messages = []
        return monitor, paused

    async def _emit_response(
        self,
        response: ChatResponse,
        tools: List[Dict[str, Any]],
        started: float,
        paused: bool,
        *,
        event_type: Literal["planner.progress", "planner.finalized"] = "planner.finalized",
        active_tool_call_id: str = "",
    ) -> None:
        if not self.members:
            return
        session_id = next(iter(self.members))
        owner = self.members[session_id]
        finished = paused or not response.tool_calls
        if event_type == "planner.finalized":
            self._state = "stop" if finished else "running"
            panels = {}
            for sid, member in self.members.items():
                selected = [tool for tool in tools if tool.get("session_id") == sid]
                if selected:
                    panels.update(await asyncio.to_thread(member._prepare_tool_prompt_records, selected))
            await asyncio.to_thread(
                owner._render_context_usage_panel,
                cycle_id=self._cycle,
                time_records={"planner": self._planner_ended_at - started},
                planner_selected_history_count=response.selected_history_count,
                planner_prompt_tokens=response.prompt_tokens,
                planner_model_name=response.model_name,
                planner_response=response.content or "",
                planner_tool_calls=list(response.tool_calls),
                planner_tool_results=[tool.get("summary", "") for tool in tools],
                planner_tool_detail_results=tools,
                planner_prompt_section=response.prompt_section,
                tool_prompt_panels=panels,
                scope_lines=[
                    "Focus 共享组：" + "、".join(member.session_name for member in self.members.values()),
                    "工具通过 session_id 指定目标，按输出顺序执行。",
                ],
            )
        await emit_planner_snapshot(
            event_type=event_type,
            session_id=self.monitor_id,
            run_id=self._run_id,
            cycle_id=self._cycle,
            planner_request_messages=response.request_messages,
            planner_selected_history_count=response.selected_history_count,
            planner_tool_count=response.tool_count,
            planner_content=response.content or "",
            planner_reasoning=response.reasoning,
            planner_tool_calls=list(response.tool_calls),
            planner_native_tool_calls=list(response.native_tool_calls),
            planner_prompt_tokens=response.prompt_tokens,
            planner_completion_tokens=response.completion_tokens,
            planner_total_tokens=response.total_tokens,
            planner_duration_ms=(self._planner_ended_at - started) * 1000,
            planner_started_at=started,
            planner_ended_at=self._planner_ended_at,
            planner_prompt_html_uri=response.prompt_html_uri,
            planner_model_name=response.model_name,
            planner_prompt_cache_hit_tokens=response.prompt_cache_hit_tokens,
            planner_prompt_cache_miss_tokens=response.prompt_cache_miss_tokens,
            planner_context_sections=list(response.context_sections),
            tools=tools,
            agent_state=self.state,
            end_reason="tool_pause:wait"
            if self._wait_until is not None
            else ("planner_no_tool_end" if finished else "tool_continue"),
            active_tool_call_id=active_tool_call_id,
            focus_scope_id=self.scope_id,
            focus_session_ids=list(self.members),
        )

    async def _compact(self) -> None:
        planner_snapshot = self._service.planner_request_snapshot
        if planner_snapshot is None:
            return
        snapshot = list(self.entries)
        generation = self._generation
        history = self.history
        plan = process_chat_history_after_cycle(
            history, max_context_size=self.max_context_size,
            consume_references=False,
        )
        if not plan.removed_messages:
            return
        removed_ids = {id(message) for message in plan.removed_messages}
        split = 0
        # 共享时间线以完整入站片段或模型—工具批次为单位；不能在条目内部截断。
        for item in snapshot:
            if not all(id(message) in removed_ids or isinstance(message, ContextSummaryMessage) for message in item.messages):
                break
            split += 1
        if split <= 0:
            return
        old = snapshot[:split]
        related = set().union(*(entry.session_ids for entry in old))
        previous = next((message for message in reversed(history) if isinstance(message, ContextSummaryMessage)), None)
        sources = [
            message for item in old for message in item.messages if not isinstance(message, ContextSummaryMessage)
        ]
        self._set_stage("上下文压缩", "复用 Planner 请求快照并保留会话来源与近期原文")
        summary: ContextSummaryMessage | None = None
        try:
            summary = await build_context_summary(
                sources, planner_snapshot=planner_snapshot, previous_summary=previous,
                recent_messages=[message for item in snapshot[split:] for message in item.messages],
                source_context="\n".join(
                    f"session_ids={sorted(item.session_ids)}; "
                    f"messages={[(message.timestamp.isoformat(), message.source) for message in item.messages]}"
                    for item in snapshot
                ),
                session_id=self.scope_id,
            )
        except Exception:
            logger.exception("Focus 上下文压缩失败，将丢弃本批旧原文并继续共享 Planner: scope=%s", self.scope_id)
        # 清空/成员变更会使旧来源失效；不能在模型返回后恢复被移除的聊天材料。
        if generation != self._generation or self._closed or len(snapshot) != len(self.entries) or any(
            old_entry is not current for old_entry, current in zip(snapshot, self.entries, strict=True)
        ):
            return
        watermarks: Dict[str, int] = {}
        inputs = {}
        for item in old:
            for sid, sequence in item.progress.items():
                watermarks[sid] = max(watermarks.get(sid, 0), sequence)
            for sid, records in item.inputs.items():
                inputs.setdefault(sid, []).extend(records)
        if summary is not None:
            source_names = [self.members[sid].session_name if sid in self.members else sid for sid in sorted(related)]
            summary.display_prefix = f"[Focus 上下文状态摘要 来源：{'、'.join(source_names)}]"
            entry = FocusContextEntry([summary], related, watermarks=watermarks, inputs=inputs)
        else:
            # 失败后保留已有有效摘要与进度，不保留本批旧原文或用于恢复它们的输入编号。
            # 即使没有旧摘要，也保留一个仅含水位的条目，避免破坏后续输入进度的确认。
            summaries = [message for item in old for message in item.messages if isinstance(message, ContextSummaryMessage)]
            entry = FocusContextEntry(summaries, related, watermarks=watermarks)
        self.entries[:split] = [entry]
        self._service.planner_request_snapshot = None
        if self.members:
            await emit_flow_step(
                session_id=self.monitor_id,
                run_id=self._run_id,
                cycle_id=self._cycle,
                step="focus.context.compacted" if summary is not None else "focus.context.trimmed",
                title="共享上下文压缩" if summary is not None else "共享上下文裁切",
                detail=(
                    f"已压缩 {len(old)} 个完整片段，保留会话来源与近期完整轮次。"
                    if summary is not None else
                    f"压缩失败，已丢弃 {len(old)} 个旧片段，保留近期完整轮次并继续运行。"
                ),
            )
        for sid, runtime in self.members.items():
            own = [
                message
                for item in old
                for message in item.messages
                if isinstance(message, SessionBackedMessage) and message.original_message.session_id == sid
            ]
            if own:
                await runtime._trigger_trimmed_history_learning(own)
            # Focus 成员历史只保存本聊天材料；不包含组级模型—工具协议段。
            keep = max(20, runtime._max_context_size * 2)
            if len(runtime._chat_history) > keep:
                runtime._chat_history = runtime._chat_history[-keep:]

    async def _cancel_worker(self) -> None:
        worker = self._worker
        if worker is asyncio.current_task():
            return
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._worker = None
        self.entries = self._closed_entries()
        self._active_batch = None
        self._post_messages = []

    async def stop(self) -> None:
        self._closed = True
        await self._cancel_worker()
        remove_stage_status(self.monitor_id)
        if self._checkpoint_task is not None:
            await self._checkpoint_task
        await self.checkpoint()
