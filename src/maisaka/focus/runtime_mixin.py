"""Focus-mode helpers for the Maisaka runtime."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from html import escape
from typing import Any, Literal, Optional, Sequence

import asyncio
import time

from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.common.logger import get_logger
from src.config.config import global_config

from src.maisaka.context.messages import (
    FOCUS_AT_WAKEUP_SOURCE,
    FOCUS_COOLDOWN_WAKEUP_SOURCE,
    FOCUS_WAKEUP_SOURCE_KINDS,
    LLMContextMessage,
    SessionBackedMessage,
    ToolResultMessage,
)
from src.maisaka.mode_policy import is_idle_cycle_reason
from .manager import FocusTargetResolution, focus_mode_manager

FOCUS_SWITCH_NEW_MESSAGE_LIMIT = 20
FOCUS_NO_ACTION_EXIT_THRESHOLD = 5
FOCUS_EVENT_UNREAD_COUNT_THRESHOLD = 3

logger = get_logger("maisaka_runtime")


class MaisakaFocusRuntimeMixin:
    """Focus-mode behavior mixed into the session runtime."""

    def _is_focus_mode_active_for_current_chat(self) -> bool:
        """Return whether focus mode applies to this runtime's chat."""

        return focus_mode_manager.is_enabled_for_session(
            self.session_id,
            is_group_chat=self.chat_stream.is_group_session,
        )

    def _get_pending_attention_flags(self) -> tuple[bool, bool]:
        """Return whether pending messages contain @ or mention signals."""

        pending_messages = self.message_cache[self._last_processed_index :]
        has_pending_at = any(message.is_at for message in pending_messages)
        has_pending_mention = any(message.is_mentioned for message in pending_messages)
        return has_pending_at, has_pending_mention

    def _mark_focus_pending_messages_read(self) -> None:
        """Refresh unread and attention flags after focus-mode manual reading."""

        self._last_processed_index = len(self.message_cache)
        self._mark_message_turn_unscheduled()
        self._clear_message_debounce_required()
        self._clear_forced_turn_state()
        self._cancel_deferred_message_turn_task()
        focus_mode_manager.mark_read(self.session_id)

    async def build_session_messages_as_user_history(
        self,
        messages: Sequence[SessionMessage],
        *,
        source_kind: str = "user",
        existing_history: Optional[Sequence[LLMContextMessage]] = None,
    ) -> list[LLMContextMessage]:
        """Build recalled real messages as normal planner user messages."""

        existing_messages = self._chat_history if existing_history is None else existing_history
        seen_message_ids = {
            str(getattr(history_message, "message_id", "") or "").strip()
            for history_message in existing_messages
            if str(getattr(history_message, "message_id", "") or "").strip()
        }
        history_messages: list[LLMContextMessage] = []
        for message in messages:
            message_id = str(message.message_id or "").strip()
            if not message_id or message_id in seen_message_ids:
                continue
            history_message = await self._reasoning_engine._build_history_message(message, source_kind=source_kind)
            if history_message is None:
                continue
            history_messages.append(history_message)
            seen_message_ids.add(message_id)

        return history_messages

    def record_idle_cycle_result(self, cycle_end_reason: str) -> None:
        """Track consecutive idle cycles and release stale group focus."""

        if not self._is_focus_mode_active_for_current_chat():
            self._consecutive_idle_count = 0
            return
        if not self.chat_stream.is_group_session:
            self._consecutive_idle_count = 0
            return

        if not is_idle_cycle_reason(cycle_end_reason):
            self._consecutive_idle_count = 0
            return

        self._consecutive_idle_count += 1
        if self._consecutive_idle_count < FOCUS_NO_ACTION_EXIT_THRESHOLD:
            return

        self._consecutive_idle_count = 0
        self._exit_focus_after_consecutive_idle()

    def _exit_focus_after_consecutive_idle(self) -> None:
        """Release the current group from focus after repeated idle cycles."""

        released = focus_mode_manager.release_focus_and_block_next_entry(self.session_id)
        if not released:
            return

        self._cancel_focus_cooldown_timer_task()
        self._clear_focus_cooldown_wakeup_scheduled()
        logger.info(
            f"{self.log_prefix} 连续 {FOCUS_NO_ACTION_EXIT_THRESHOLD} 次空闲结束，"
            "已退出当前群的 Focus，并阻止它立即重新进入"
        )

    def _cancel_focus_cooldown_timer_task(self) -> None:
        """Cancel the focus-mode cool-time timer."""

        if self._focus_cooldown_timer_task is None:
            return
        self._focus_cooldown_timer_task.cancel()
        self._focus_cooldown_timer_task = None

    def _arm_focus_cooldown_timer(self) -> None:
        """Start a timer that wakes this focused chat when other chats stay unread."""

        self._cancel_focus_cooldown_timer_task()
        if not self._running or not focus_mode_manager.can_decide(
            self.session_id,
            is_group_chat=self.chat_stream.is_group_session,
        ):
            return
        self._focus_cooldown_timer_task = asyncio.create_task(self._run_focus_cooldown_timer())

    async def _run_focus_cooldown_timer(self) -> None:
        """Check focus cool-time once after the configured delay."""

        try:
            await asyncio.sleep(focus_mode_manager.get_focus_cool_time())
            if not self._running or not focus_mode_manager.can_decide(
                self.session_id,
                is_group_chat=self.chat_stream.is_group_session,
            ):
                return

            from src.chat.heart_flow.heartflow_manager import heartflow_manager

            running_runtimes = list(heartflow_manager.heartflow_chat_list.values())
            if self._select_focus_cooldown_wakeup_runtime(running_runtimes) is not self:
                return
            trigger_session_id = self._find_pending_other_focus_chat_id(running_runtimes, self.session_id)
            if not trigger_session_id:
                return
            self._queue_focus_cooldown_wakeup(trigger_session_id=trigger_session_id)
        except asyncio.CancelledError:
            return
        finally:
            if self._focus_cooldown_timer_task is asyncio.current_task():
                self._focus_cooldown_timer_task = None

    def resolve_running_focus_session_from_args(self, arguments: dict[str, Any]) -> FocusTargetResolution:
        """Resolve focus tool target from currently running heartflow chats."""

        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        running_sessions = [
            runtime.chat_stream
            for runtime in heartflow_manager.heartflow_chat_list.values()
        ]
        return focus_mode_manager.resolve_session_from_args(arguments, running_sessions)

    def _maybe_schedule_focus_cooldown_wakeup(self, *, trigger_session_id: str) -> None:
        """Wake one idle focused chat when other running chats have pending messages."""

        if not self._is_focus_mode_active_for_current_chat():
            return

        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        running_runtimes = list(heartflow_manager.heartflow_chat_list.values())
        target_runtime = self._select_focus_cooldown_wakeup_runtime(
            running_runtimes,
            trigger_session_id=trigger_session_id,
        )
        if target_runtime is None:
            return
        target_runtime._queue_focus_cooldown_wakeup(trigger_session_id=trigger_session_id)

    def _maybe_schedule_focus_at_wakeup(self, *, trigger_session_id: str) -> None:
        """Wake one focused chat immediately when another running chat @mentions Maibot."""

        if not self._is_focus_mode_active_for_current_chat():
            return

        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        running_runtimes = list(heartflow_manager.heartflow_chat_list.values())
        target_runtime = self._select_focus_cooldown_wakeup_runtime(
            running_runtimes,
            trigger_session_id=trigger_session_id,
            ignore_cool_time=True,
        )
        if target_runtime is None:
            return
        target_runtime._queue_focus_cooldown_wakeup(
            trigger_session_id=trigger_session_id,
            wakeup_reason="at",
        )

    @staticmethod
    def _select_focus_cooldown_wakeup_runtime(
        running_runtimes: Sequence[Any],
        *,
        trigger_session_id: str = "",
        ignore_cool_time: bool = False,
    ) -> Any | None:
        now = time.time()
        eligible_runtimes: list[tuple[float, Any]] = []
        for runtime in running_runtimes:
            if not focus_mode_manager.is_in_focus_set(runtime.session_id):
                continue
            if not focus_mode_manager.is_enabled_for_session(
                runtime.session_id,
                is_group_chat=runtime.chat_stream.is_group_session,
            ):
                continue
            if trigger_session_id and not focus_mode_manager.is_same_focus_scope(runtime.session_id, trigger_session_id):
                continue
            if runtime._agent_state == runtime._STATE_RUNNING:
                continue
            if runtime._focus_cooldown_wakeup_scheduled:
                continue
            if runtime._proactive_trigger_message is not None:
                continue
            if runtime._message_turn_scheduled and runtime._has_pending_messages():
                continue
            if not ignore_cool_time and not focus_mode_manager.is_cycle_cool_time_elapsed(runtime.session_id, now=now):
                continue
            if not MaisakaFocusRuntimeMixin._find_pending_other_focus_chat_id(
                running_runtimes,
                runtime.session_id,
            ):
                continue

            last_cycle_at = focus_mode_manager.get_last_cycle_at(runtime.session_id) or 0.0
            eligible_runtimes.append((last_cycle_at, runtime))

        if not eligible_runtimes:
            return None
        eligible_runtimes.sort(key=lambda item: (item[0], item[1].session_id != trigger_session_id))
        return eligible_runtimes[0][1]

    @staticmethod
    def _find_pending_other_focus_chat_id(
        running_runtimes: Sequence[Any],
        focus_session_id: str,
    ) -> str:
        for runtime in running_runtimes:
            if runtime.session_id == focus_session_id:
                continue
            if not focus_mode_manager.is_enabled_for_session(
                runtime.session_id,
                is_group_chat=runtime.chat_stream.is_group_session,
            ):
                continue
            if not focus_mode_manager.is_same_focus_scope(runtime.session_id, focus_session_id):
                continue
            if runtime._get_pending_message_count() > 0:
                return runtime.session_id
        return ""

    def _queue_focus_cooldown_wakeup(
        self,
        *,
        trigger_session_id: str,
        wakeup_reason: Literal["cooldown", "at"] = "cooldown",
    ) -> bool:
        """Queue a proactive focus-mode wake-up turn."""

        if self._focus_cooldown_wakeup_scheduled or not focus_mode_manager.can_decide(
            self.session_id,
            is_group_chat=self.chat_stream.is_group_session,
        ):
            return False
        if self._agent_state == self._STATE_RUNNING:
            return False

        trigger_name = chat_manager.get_session_name(trigger_session_id) or trigger_session_id
        bot_name = global_config.bot.nickname.strip()
        wakeup_timestamp = datetime.now()
        wakeup_id = f"focus_{wakeup_reason}:{int(time.time() * 1000)}"
        if wakeup_reason == "at":
            reason_text = (
                f"{trigger_name} 有人 @ {bot_name}，已无视 focus_cool_time 强制触发一次 Focus 模式思考。"
            )
        else:
            reason_text = (
                f"Focus 模式冷却时间已到，且 {trigger_name} 有尚未进入 Maisaka 决策的新消息。"
            )
        wakeup_notice = (
            f'<focus_cooldown_wakeup trigger_chat_id="{escape(trigger_session_id, quote=True)}" '
            f'focus_cool_time="{focus_mode_manager.get_focus_cool_time():.0f}" '
            f'reason="{escape(wakeup_reason, quote=True)}">\n'
            f"{reason_text}\n"
            "请结合最新的 focus_chat_event 判断是否需要切换或处理其它聊天。\n"
            "</focus_cooldown_wakeup>"
        )
        wakeup_message = SessionMessage(
            message_id=wakeup_id,
            timestamp=wakeup_timestamp,
            platform=self.chat_stream.platform,
        )
        wakeup_message.session_id = self.session_id
        wakeup_message.message_info = MessageInfo(
            user_info=self._build_runtime_user_info(),
            group_info=self._build_group_info(),
            additional_config={},
        )
        wakeup_message.raw_message = MessageSequence([TextComponent(wakeup_notice)])
        wakeup_message.processed_plain_text = wakeup_notice

        self._chat_history.append(
            SessionBackedMessage.from_session_message(
                wakeup_message,
                raw_message=wakeup_message.raw_message,
                visible_text=wakeup_notice,
                source_kind=FOCUS_AT_WAKEUP_SOURCE if wakeup_reason == "at" else FOCUS_COOLDOWN_WAKEUP_SOURCE,
            )
        )
        self._focus_cooldown_wakeup_scheduled = True
        self._queue_proactive_turn(wakeup_message)
        logger.info(
            f"{self.log_prefix} focus_mode 强制唤醒已排队: "
            f"trigger_session_id={trigger_session_id} reason={wakeup_reason} "
            f"cool_time={focus_mode_manager.get_focus_cool_time():.0f}s"
        )
        return True

    def build_focus_tail_user_messages(self) -> list[str]:
        """Build tail user messages injected at the end of focus-mode planner requests."""

        if not self._is_focus_mode_active_for_current_chat() or not focus_mode_manager.can_decide(
            self.session_id,
            is_group_chat=self.chat_stream.is_group_session,
        ):
            return []
        return self._build_focus_chat_event_messages()

    def _build_focus_chat_event_messages(self) -> list[str]:
        """Build focus-mode event messages for currently running chat sessions."""

        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        running_runtimes = sorted(
            heartflow_manager.heartflow_chat_list.values(),
            key=lambda runtime: runtime.chat_stream.last_active_timestamp
            or runtime.chat_stream.created_timestamp,
            reverse=True,
        )
        bot_name = global_config.bot.nickname.strip()
        focus_scope_key = focus_mode_manager.get_focus_scope_key(self.session_id)
        unviewed_seconds_threshold = focus_mode_manager.get_focus_cool_time()
        event_messages: list[str] = []

        def append_event(
            *,
            event_type: str,
            chat_attrs: Sequence[str],
            reason: str,
            latest_messages: Sequence[SessionMessage],
            extra_attrs: Sequence[str] = (),
        ) -> None:
            event_messages.append(
                self._format_focus_event(
                    event_type=event_type,
                    attrs=[
                        f'current_chat_id="{escape(self.session_id, quote=True)}"',
                        f'focus_scope="{escape(focus_scope_key, quote=True)}"',
                        *chat_attrs,
                        *extra_attrs,
                    ],
                    reason=reason,
                    latest_messages=latest_messages,
                )
            )

        for chat_runtime in running_runtimes:
            if not focus_mode_manager.is_enabled_for_session(
                chat_runtime.session_id,
                is_group_chat=chat_runtime.chat_stream.is_group_session,
            ):
                continue
            if not focus_mode_manager.is_same_focus_scope(chat_runtime.session_id, self.session_id):
                continue
            chat_session = chat_runtime.chat_stream
            unread_count = chat_runtime._get_pending_message_count()
            has_pending_at, has_pending_mention = chat_runtime._get_pending_attention_flags()
            latest_messages = self._get_latest_messages_for_focus_overview(chat_session, chat_runtime)
            chat_attrs = self._build_focus_event_chat_attrs(chat_session, unread_count)

            if has_pending_at:
                append_event(
                    event_type="at",
                    chat_attrs=chat_attrs,
                    reason=f"有人 @ {bot_name}",
                    latest_messages=latest_messages,
                )
            if has_pending_mention:
                append_event(
                    event_type="mention",
                    chat_attrs=chat_attrs,
                    reason=f"有人提及{bot_name}",
                    latest_messages=latest_messages,
                )
            if unread_count >= FOCUS_EVENT_UNREAD_COUNT_THRESHOLD:
                append_event(
                    event_type="unread_count",
                    chat_attrs=chat_attrs,
                    extra_attrs=[f'threshold="{FOCUS_EVENT_UNREAD_COUNT_THRESHOLD}"'],
                    reason=(
                        f"未读（未决策消息）消息数 {unread_count} "
                        f"已达到阈值 {FOCUS_EVENT_UNREAD_COUNT_THRESHOLD}"
                    ),
                    latest_messages=latest_messages,
                )

            oldest_pending_at = self._get_focus_oldest_pending_message_at(chat_runtime)
            if self._calculate_focus_unviewed_seconds(oldest_pending_at) >= unviewed_seconds_threshold:
                append_event(
                    event_type="unviewed_time",
                    chat_attrs=chat_attrs,
                    extra_attrs=[
                        f'threshold_seconds="{unviewed_seconds_threshold:.0f}"',
                        f'oldest_unread_at="{escape(self._format_focus_datetime(oldest_pending_at), quote=True)}"',
                    ],
                    reason=f"最早未查看消息已达到 {unviewed_seconds_threshold:.0f} 秒未查看阈值",
                    latest_messages=latest_messages,
                )

        return event_messages

    @staticmethod
    def _build_focus_event_chat_attrs(chat_session: BotChatSession, unread_count: int) -> list[str]:
        chat_type = "group" if chat_session.is_group_session else "private"
        target_id = chat_session.group_id if chat_session.is_group_session else chat_session.user_id
        return [
            f'chat_id="{escape(chat_session.session_id, quote=True)}"',
            f'platform="{escape(chat_session.platform, quote=True)}"',
            f'id="{escape(target_id or "", quote=True)}"',
            f'type="{escape(chat_type, quote=True)}"',
            f'unread_count="{unread_count}"',
        ]

    @staticmethod
    def _format_focus_event(
        *,
        event_type: str,
        attrs: Sequence[str],
        reason: str,
        latest_messages: Sequence[SessionMessage],
    ) -> str:
        attrs_text = " ".join(attrs)
        lines = [
            f'<focus_chat_event event_type="{escape(event_type, quote=True)}" {attrs_text}>',
            f"原因: {reason}",
            "最新一条消息:",
            *MaisakaFocusRuntimeMixin._format_focus_latest_messages(latest_messages),
            "</focus_chat_event>",
        ]
        return "\n".join(lines)

    @staticmethod
    def _get_focus_oldest_pending_message_at(chat_runtime: Any) -> Optional[datetime]:
        pending_messages = chat_runtime.message_cache[chat_runtime._last_processed_index :]
        oldest_timestamp: Optional[datetime] = None
        seen_message_ids: set[str] = set()
        for message in pending_messages:
            message_id = str(message.message_id or "").strip()
            if message_id:
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
            message_timestamp = message.timestamp
            if oldest_timestamp is None or message_timestamp < oldest_timestamp:
                oldest_timestamp = message_timestamp
        return oldest_timestamp

    @staticmethod
    def _calculate_focus_unviewed_seconds(oldest_pending_at: Optional[datetime]) -> float:
        if oldest_pending_at is None:
            return 0.0
        current_time = datetime.now(oldest_pending_at.tzinfo) if oldest_pending_at.tzinfo else datetime.now()
        return max(0.0, (current_time - oldest_pending_at).total_seconds())

    @staticmethod
    def _get_latest_messages_for_focus_overview(
        chat_session: BotChatSession,
        chat_runtime: Any | None,
        limit: int = 1,
    ) -> list[SessionMessage]:
        """Return the latest known messages for a chat session."""

        if chat_runtime is not None and chat_runtime.message_cache:
            return chat_runtime.message_cache[-max(1, int(limit)) :]
        if latest_message := chat_manager.last_messages.get(chat_session.session_id):
            return [latest_message]
        return []

    @staticmethod
    def _format_focus_datetime(value: Optional[datetime]) -> str:
        if value is None:
            return "未阅读"
        return value.isoformat(timespec="seconds")

    @staticmethod
    def _format_focus_latest_messages(messages: Sequence[SessionMessage], max_length: int = 160) -> list[str]:
        if not messages:
            return ["      无"]

        return [
            f"      [{index}] {MaisakaFocusRuntimeMixin._format_focus_latest_message(message, max_length=max_length)}"
            for index, message in enumerate(messages, start=1)
        ]

    @staticmethod
    def _format_focus_latest_message(message: SessionMessage, max_length: int = 160) -> str:
        user_info = message.message_info.user_info
        speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        text = str(message.processed_plain_text or "").strip()
        if not text:
            text = "[空消息]"
        text = " ".join(text.split())
        if len(text) > max_length:
            text = f"{text[:max_length]}..."
        return f"{message.timestamp.isoformat(timespec='seconds')} {speaker_name}: {text}"

    def _get_focus_switch_new_messages(self, *, limit: int = FOCUS_SWITCH_NEW_MESSAGE_LIMIT) -> list[SessionMessage]:
        """Return pending new messages shown automatically after switch_chat."""

        safe_limit = max(1, int(limit))
        pending_messages = self.message_cache[self._last_processed_index :]
        if not pending_messages:
            return []

        unique_messages: list[SessionMessage] = []
        seen_message_ids: set[str] = set()
        for message in pending_messages:
            message_id = str(message.message_id or "").strip()
            if message_id:
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
            unique_messages.append(message)
        return unique_messages[-safe_limit:]

    def _get_focus_fetch_history_messages(self, *, limit: int) -> list[SessionMessage]:
        """Return current-chat messages that are in the stream but not in Maisaka history."""

        safe_limit = min(50, max(1, int(limit)))
        history_message_ids = {
            str(getattr(history_message, "message_id", "") or "").strip()
            for history_message in self._chat_history
            if str(getattr(history_message, "message_id", "") or "").strip()
        }
        fetched_messages: list[SessionMessage] = []
        seen_message_ids: set[str] = set()
        for message in reversed(self.message_cache):
            message_id = str(message.message_id or "").strip()
            if not message_id:
                continue
            if message_id in history_message_ids or message_id in seen_message_ids:
                continue
            fetched_messages.append(message)
            seen_message_ids.add(message_id)
            if len(fetched_messages) >= safe_limit:
                break
        return fetched_messages

    async def build_focus_fetch_history_result(
        self,
        *,
        num: int,
    ) -> tuple[str, dict[str, Any], list[LLMContextMessage]]:
        """Fetch current-chat stream messages that are not already in Maisaka history."""

        safe_num = min(50, max(1, int(num)))
        fetched_messages = self._get_focus_fetch_history_messages(limit=safe_num)
        post_history_messages = await self.build_session_messages_as_user_history(
            fetched_messages,
            source_kind="user",
        )

        chat_session = self.chat_stream
        chat_type = "group" if chat_session.is_group_session else "private"
        target_id = chat_session.group_id if chat_session.is_group_session else chat_session.user_id
        lines = [
            f"已从当前聊天 chat_id={chat_session.session_id} 获取尚未进入 Maisaka 上下文的消息。",
            f"平台: {chat_session.platform}",
            f"id: {target_id or ''}",
            f"类型: {chat_type}",
            f"请求数量: {safe_num}",
            f"召回消息数: {len(fetched_messages)}",
            f"新增为普通 user message 的消息数: {len(post_history_messages)}",
            "召回顺序为从新到旧；召回到的消息已按普通用户消息格式逐条加入上下文，可直接用各自 msg_id 引用。",
        ]

        structured_content = {
            "chat_id": chat_session.session_id,
            "platform": chat_session.platform,
            "id": target_id or "",
            "type": chat_type,
            "num": safe_num,
            "messages": [
                {
                    "message_id": message.message_id,
                    "timestamp": message.timestamp.isoformat(timespec="seconds"),
                    "user_id": message.message_info.user_info.user_id,
                    "user_name": (
                        message.message_info.user_info.user_cardname
                        or message.message_info.user_info.user_nickname
                        or message.message_info.user_info.user_id
                    ),
                    "text": str(message.processed_plain_text or "").strip(),
                }
                for message in fetched_messages
            ],
        }
        return "\n".join(lines), structured_content, post_history_messages

    async def switch_focus_to_session(
        self,
        target_session: BotChatSession,
        *,
        tool_call_id: str,
        tool_name: str,
    ) -> tuple[bool, str, dict[str, Any], dict[str, Any]]:
        """Switch the current focus slot to another chat and copy context there."""

        if not self._is_focus_mode_active_for_current_chat():
            return False, "focus_mode 未启用，不能切换聊天。", {}, {}
        if target_session.session_id == self.session_id:
            return False, "目标聊天就是当前聊天，不能切换到自身。", {}, {}

        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        target_runtime = heartflow_manager.heartflow_chat_list.get(target_session.session_id)
        if target_runtime is None:
            return False, f"chat_id={target_session.session_id} 当前不是运行中已创建聊天，不能切换。", {}, {}

        switch_error = focus_mode_manager.switch_focus(self.session_id, target_session.session_id)
        if switch_error:
            return False, switch_error, {}, {}

        target_unread_count = target_runtime._get_pending_message_count()
        switch_new_messages = target_runtime._get_focus_switch_new_messages(limit=FOCUS_SWITCH_NEW_MESSAGE_LIMIT)
        copied_history = deepcopy(
            [
                message
                for message in self._chat_history
                if message.source not in FOCUS_WAKEUP_SOURCE_KINDS
            ]
        )
        recent_context_messages = await target_runtime.build_session_messages_as_user_history(
            switch_new_messages,
            source_kind="user",
            existing_history=copied_history,
        )
        result_content = (
            f"已从 chat_id={self.session_id} 切换到 chat_id={target_session.session_id}。"
            f"已按普通 user message 格式接入新聊天未读新消息 {len(recent_context_messages)} 条"
            f"（未读 {target_unread_count} 条，最多自动接入 {FOCUS_SWITCH_NEW_MESSAGE_LIMIT} 条）。"
        )
        if tool_call_id:
            copied_history.append(
                ToolResultMessage(
                    content=result_content,
                    timestamp=datetime.now(),
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                )
            )

        target_runtime._mark_focus_pending_messages_read()
        switch_timestamp = datetime.now()
        switch_message_id = f"focus_switch:{int(time.time() * 1000)}"
        switch_notice = (
            f"已经切换到 chat_id={target_session.session_id}。"
            f"下面已按普通 user message 格式接入这个聊天未读新消息 {len(recent_context_messages)} 条"
            f"（未读 {target_unread_count} 条，最多 {FOCUS_SWITCH_NEW_MESSAGE_LIMIT} 条）。\n"
            "</focus_switch>"
        )
        switch_trigger_message = SessionMessage(
            message_id=switch_message_id,
            timestamp=switch_timestamp,
            platform=target_session.platform,
        )
        switch_trigger_message.session_id = target_session.session_id
        switch_trigger_message.message_info = MessageInfo(
            user_info=target_runtime._build_runtime_user_info(),
            group_info=target_runtime._build_group_info(),
            additional_config={},
        )
        switch_trigger_message.raw_message = MessageSequence([TextComponent(switch_notice)])
        switch_trigger_message.processed_plain_text = switch_notice

        copied_history.append(
            SessionBackedMessage.from_session_message(
                switch_trigger_message,
                raw_message=switch_trigger_message.raw_message,
                visible_text=switch_notice,
                source_kind="focus_switch",
            )
        )
        copied_history.extend(recent_context_messages)
        target_runtime._chat_history = copied_history
        target_runtime._mark_message_turn_unscheduled()
        target_runtime._clear_message_debounce_required()
        target_runtime._enter_stop_state()
        target_runtime._queue_proactive_turn(switch_trigger_message)

        self._enter_stop_state()
        structured_content = {
            "from_chat_id": self.session_id,
            "to_chat_id": target_session.session_id,
        }
        metadata = {"pause_execution": True}
        return True, result_content, structured_content, metadata
