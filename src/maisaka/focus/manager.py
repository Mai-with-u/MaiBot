"""Focus mode state shared by Maisaka chat runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

import time

from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.common.utils.utils_config import ChatConfigUtils
from src.config.config import global_config

FOCUS_SLOT_LIMIT = 1
FOCUS_GLOBAL_SCOPE_KEY = "__global__"
FOCUS_ISOLATED_SCOPE_PREFIX = "session:"


@dataclass(slots=True)
class FocusTargetResolution:
    """Resolved chat target for focus-mode tools."""

    session: Optional[BotChatSession]
    error: str = ""


class FocusModeManager:
    """Track which chat sessions are currently allowed to make Maisaka decisions."""

    def __init__(self) -> None:
        self._focused_session_ids_by_scope: dict[str, list[str]] = {}
        self._next_focus_blocked_session_id_by_scope: dict[str, str] = {}
        self._next_focus_blocked_at_by_scope: dict[str, float] = {}
        self._last_cycle_at_by_session_id: dict[str, float] = {}
        self._last_read_at_by_session_id: dict[str, datetime] = {}

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        return str(session_id or "").strip()

    def is_enabled(self) -> bool:
        """Return whether focus mode is enabled in the live chat config."""

        return bool(global_config.experimental.focus_mode)

    def is_enabled_for_chat(self, *, is_group_chat: Optional[bool] = None) -> bool:
        """Return whether focus mode applies to a specific chat type."""

        if not self.is_enabled():
            return False
        if is_group_chat is False and not bool(global_config.experimental.focus_on_private):
            return False
        return True

    @staticmethod
    def _get_focus_whitelist_targets() -> Iterable[Any]:
        return global_config.experimental.focus_chat_whitelist or []

    def get_focus_cool_time(self) -> float:
        """Return the focus wake-up cool time in seconds."""

        try:
            return max(1.0, float(global_config.experimental.focus_cool_time))
        except (TypeError, ValueError):
            return 120.0

    def _resolve_is_group_chat(self, session_id: str, is_group_chat: Optional[bool] = None) -> Optional[bool]:
        if is_group_chat is not None:
            return is_group_chat

        chat_session = chat_manager.get_session_by_session_id(session_id)
        if chat_session is None:
            return None
        return chat_session.is_group_session

    def _is_focus_mode_active_for_session(
        self,
        session_id: str,
        is_group_chat: Optional[bool] = None,
    ) -> bool:
        resolved_is_group_chat = self._resolve_is_group_chat(session_id, is_group_chat)
        if not self.is_enabled_for_chat(is_group_chat=resolved_is_group_chat):
            return False

        whitelist_targets = list(self._get_focus_whitelist_targets())
        if not whitelist_targets:
            return True
        return any(
            ChatConfigUtils.target_matches_session_with_wildcards(
                target_item,
                session_id,
                resolved_is_group_chat,
            )
            for target_item in whitelist_targets
        )

    def is_enabled_for_session(self, session_id: str, *, is_group_chat: Optional[bool] = None) -> bool:
        """Return whether focus mode applies to a specific chat session."""

        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return False
        return self._is_focus_mode_active_for_session(normalized_session_id, is_group_chat)

    @staticmethod
    def _get_focus_group_targets(focus_group: Any) -> Iterable[Any]:
        if isinstance(focus_group, dict):
            return focus_group.get("targets") or []
        return focus_group.targets

    def _resolve_focus_scope_key(self, session_id: str, is_group_chat: Optional[bool] = None) -> str:
        """Resolve the focus sharing scope for a chat session."""

        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return ""

        focus_groups = list(global_config.experimental.focus_groups or [])
        if not focus_groups:
            return FOCUS_GLOBAL_SCOPE_KEY

        resolved_is_group_chat = self._resolve_is_group_chat(normalized_session_id, is_group_chat)
        for group_index, focus_group in enumerate(focus_groups):
            for target_item in self._get_focus_group_targets(focus_group):
                if ChatConfigUtils.target_matches_session_with_wildcards(
                    target_item,
                    normalized_session_id,
                    resolved_is_group_chat,
                ):
                    return f"group:{group_index}"

        return f"{FOCUS_ISOLATED_SCOPE_PREFIX}{normalized_session_id}"

    def get_focus_scope_key(self, session_id: str) -> str:
        """Return the focus sharing scope key for a session."""

        return self._resolve_focus_scope_key(session_id)

    def is_same_focus_scope(self, first_session_id: str, second_session_id: str) -> bool:
        """Return whether two sessions share one focus slot."""

        normalized_first_session_id = self._normalize_session_id(first_session_id)
        normalized_second_session_id = self._normalize_session_id(second_session_id)
        if not normalized_first_session_id or not normalized_second_session_id:
            return False
        return self._resolve_focus_scope_key(normalized_first_session_id) == self._resolve_focus_scope_key(
            normalized_second_session_id
        )

    def _normalize_state(self) -> None:
        if not self.is_enabled():
            self._focused_session_ids_by_scope.clear()
            self._next_focus_blocked_session_id_by_scope.clear()
            self._next_focus_blocked_at_by_scope.clear()
            return

        focused_session_ids: list[str] = []
        seen_session_ids: set[str] = set()
        for scope_session_ids in self._focused_session_ids_by_scope.values():
            for session_id in scope_session_ids:
                normalized_session_id = self._normalize_session_id(session_id)
                if not normalized_session_id or normalized_session_id in seen_session_ids:
                    continue
                if not self._is_session_id_focus_allowed(normalized_session_id):
                    continue
                focused_session_ids.append(normalized_session_id)
                seen_session_ids.add(normalized_session_id)

        normalized_focused_session_ids_by_scope: dict[str, list[str]] = {}
        for session_id in focused_session_ids:
            scope_key = self._resolve_focus_scope_key(session_id)
            scope_session_ids = normalized_focused_session_ids_by_scope.setdefault(scope_key, [])
            if len(scope_session_ids) < FOCUS_SLOT_LIMIT:
                scope_session_ids.append(session_id)
        self._focused_session_ids_by_scope = normalized_focused_session_ids_by_scope

        now = time.time()
        focus_cool_time = self.get_focus_cool_time()
        blocked_session_ids: list[str] = []
        seen_blocked_session_ids: set[str] = set()
        blocked_at_by_session_id: dict[str, float] = {}
        for scope_key, session_id in self._next_focus_blocked_session_id_by_scope.items():
            normalized_session_id = self._normalize_session_id(session_id)
            if not normalized_session_id or normalized_session_id in seen_blocked_session_ids:
                continue
            if not self._is_session_id_focus_allowed(normalized_session_id):
                continue
            blocked_at = self._next_focus_blocked_at_by_scope.get(scope_key, now)
            if now - blocked_at >= focus_cool_time:
                continue
            blocked_session_ids.append(normalized_session_id)
            seen_blocked_session_ids.add(normalized_session_id)
            blocked_at_by_session_id[normalized_session_id] = blocked_at

        normalized_blocked_session_id_by_scope: dict[str, str] = {}
        normalized_blocked_at_by_scope: dict[str, float] = {}
        for session_id in blocked_session_ids:
            scope_key = self._resolve_focus_scope_key(session_id)
            if session_id in self._focused_session_ids_by_scope.get(scope_key, []):
                continue
            if scope_key not in normalized_blocked_session_id_by_scope:
                normalized_blocked_session_id_by_scope[scope_key] = session_id
                normalized_blocked_at_by_scope[scope_key] = blocked_at_by_session_id[session_id]
        self._next_focus_blocked_session_id_by_scope = normalized_blocked_session_id_by_scope
        self._next_focus_blocked_at_by_scope = normalized_blocked_at_by_scope

    def _is_session_id_focus_allowed(self, session_id: str) -> bool:
        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return False
        return self._is_focus_mode_active_for_session(normalized_session_id)

    def is_in_focus_set(self, session_id: str) -> bool:
        """Return whether a session is explicitly occupying a focus slot."""

        self._normalize_state()
        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return False
        scope_key = self._resolve_focus_scope_key(normalized_session_id)
        return normalized_session_id in self._focused_session_ids_by_scope.get(scope_key, [])

    def can_decide(self, session_id: str, *, is_group_chat: Optional[bool] = None) -> bool:
        """Return whether the session may run Maisaka decision loops right now."""

        normalized_session_id = self._normalize_session_id(session_id)
        if not self._is_focus_mode_active_for_session(normalized_session_id, is_group_chat):
            self._normalize_state()
            self.release_focus(normalized_session_id)
            return True
        return self.is_in_focus_set(normalized_session_id)

    def try_enter_focus(self, session_id: str, *, is_group_chat: Optional[bool] = None) -> bool:
        """Try to put a session into its focus group's active slot."""

        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return False
        if not self._is_focus_mode_active_for_session(normalized_session_id, is_group_chat):
            self._normalize_state()
            self.release_focus(normalized_session_id)
            return True

        self._normalize_state()
        scope_key = self._resolve_focus_scope_key(normalized_session_id, is_group_chat)
        focused_session_ids = self._focused_session_ids_by_scope.setdefault(scope_key, [])
        if normalized_session_id in focused_session_ids:
            return True
        if normalized_session_id == self._next_focus_blocked_session_id_by_scope.get(scope_key, ""):
            return False
        if len(focused_session_ids) >= FOCUS_SLOT_LIMIT:
            return False

        focused_session_ids.append(normalized_session_id)
        self._next_focus_blocked_session_id_by_scope.pop(scope_key, None)
        self._next_focus_blocked_at_by_scope.pop(scope_key, None)
        self._last_cycle_at_by_session_id[normalized_session_id] = time.time()
        return True

    def release_focus(self, session_id: str) -> None:
        """Remove a session from the focus set."""

        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return
        for scope_key in list(self._focused_session_ids_by_scope):
            focused_session_ids = [
                focused_session_id
                for focused_session_id in self._focused_session_ids_by_scope[scope_key]
                if focused_session_id != normalized_session_id
            ]
            if focused_session_ids:
                self._focused_session_ids_by_scope[scope_key] = focused_session_ids
            else:
                del self._focused_session_ids_by_scope[scope_key]
        self._last_cycle_at_by_session_id.pop(normalized_session_id, None)

    def release_focus_and_block_next_entry(self, session_id: str) -> bool:
        """Remove a focused session and prevent it from claiming the next focus slot.

        The block expires after focus_cool_time, or immediately via unblock_focus_entry.
        """

        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return False
        if not self._is_focus_mode_active_for_session(normalized_session_id):
            self.release_focus(normalized_session_id)
            return False

        self._normalize_state()
        scope_key = self._resolve_focus_scope_key(normalized_session_id)
        was_focused = normalized_session_id in self._focused_session_ids_by_scope.get(scope_key, [])
        self.release_focus(normalized_session_id)
        if was_focused:
            self._next_focus_blocked_session_id_by_scope[scope_key] = normalized_session_id
            self._next_focus_blocked_at_by_scope[scope_key] = time.time()
        return was_focused

    def unblock_focus_entry(self, session_id: str) -> bool:
        """Lift the idle-exit re-entry block for a session, e.g. when it is explicitly @-summoned."""

        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return False
        self._normalize_state()
        scope_key = self._resolve_focus_scope_key(normalized_session_id)
        if self._next_focus_blocked_session_id_by_scope.get(scope_key, "") != normalized_session_id:
            return False
        self._next_focus_blocked_session_id_by_scope.pop(scope_key, None)
        self._next_focus_blocked_at_by_scope.pop(scope_key, None)
        return True

    def switch_focus(self, from_session_id: str, to_session_id: str) -> str:
        """Move one focus slot from the current session to another existing session.

        Returns an empty string on success; otherwise returns a user-facing error.
        """

        if not self.is_enabled():
            return "focus_mode 未启用，不能切换关注聊天。"

        self._normalize_state()
        normalized_from_session_id = self._normalize_session_id(from_session_id)
        normalized_to_session_id = self._normalize_session_id(to_session_id)
        if not normalized_to_session_id:
            return "缺少要切换到的 chat_id。"
        if not self._is_session_id_focus_allowed(normalized_to_session_id):
            return f"chat_id={normalized_to_session_id} 不在当前 Focus 生效范围内，不能切换。"

        from_scope_key = self._resolve_focus_scope_key(normalized_from_session_id)
        to_scope_key = self._resolve_focus_scope_key(normalized_to_session_id)
        if from_scope_key != to_scope_key:
            return (
                f"chat_id={normalized_to_session_id} 不在当前 Focus 共享组内，"
                "不能通过 switch_chat 切换；它可以独立进入自己的 Focus。"
            )

        focused_session_ids = self._focused_session_ids_by_scope.get(from_scope_key, [])
        if normalized_to_session_id in focused_session_ids:
            return f"chat_id={normalized_to_session_id} 已经处于关注状态，不能切换到已关注聊天。"
        if normalized_to_session_id == self._next_focus_blocked_session_id_by_scope.get(from_scope_key, ""):
            return f"chat_id={normalized_to_session_id} 刚因连续空闲结束退出 Focus，本次不能切换回该聊天。"
        if normalized_from_session_id not in focused_session_ids:
            return f"当前 chat_id={normalized_from_session_id} 不在关注状态，不能发起切换。"

        self.release_focus(normalized_from_session_id)
        self._focused_session_ids_by_scope.setdefault(from_scope_key, []).append(normalized_to_session_id)
        self._next_focus_blocked_session_id_by_scope.pop(from_scope_key, None)
        self._next_focus_blocked_at_by_scope.pop(from_scope_key, None)
        self._last_cycle_at_by_session_id[normalized_to_session_id] = time.time()
        self._normalize_state()
        return ""

    def mark_cycle(self, session_id: str, when: Optional[float] = None) -> None:
        """Record that a focused chat has started a Maisaka loop."""

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return
        self._last_cycle_at_by_session_id[normalized_session_id] = when if when is not None else time.time()

    def get_last_cycle_at(self, session_id: str) -> Optional[float]:
        """Return the last Maisaka loop start time for a focused chat."""

        return self._last_cycle_at_by_session_id.get(str(session_id or "").strip())

    def is_cycle_cool_time_elapsed(self, session_id: str, now: Optional[float] = None) -> bool:
        """Return whether a focused chat has exceeded the configured cool time."""

        if not self.is_enabled() or not self.is_in_focus_set(session_id):
            return False
        current_time = now if now is not None else time.time()
        last_cycle_at = self.get_last_cycle_at(session_id)
        if last_cycle_at is None:
            return True
        return current_time - last_cycle_at >= self.get_focus_cool_time()

    def mark_read(self, session_id: str, when: Optional[datetime] = None) -> None:
        """Record that Maisaka inspected messages from a chat."""

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return
        self._last_read_at_by_session_id[normalized_session_id] = when or datetime.now()

    def get_last_read_at(self, session_id: str) -> Optional[datetime]:
        """Return the last time Maisaka read a chat in focus mode."""

        return self._last_read_at_by_session_id.get(str(session_id or "").strip())

    def resolve_session_from_args(
        self,
        arguments: dict[str, Any],
        available_sessions: Iterable[BotChatSession],
    ) -> FocusTargetResolution:
        """Resolve tool arguments to a currently running chat session."""

        session_by_id = {
            session.session_id: session
            for session in available_sessions
            if str(session.session_id or "").strip()
        }

        chat_id = str(arguments.get("chat_id") or arguments.get("session_id") or "").strip()
        if chat_id:
            session = session_by_id.get(chat_id)
            if session is None:
                return FocusTargetResolution(None, f"未找到 chat_id={chat_id} 对应的运行中已创建聊天。")
            return FocusTargetResolution(session)

        platform = str(arguments.get("platform") or "").strip()
        target_id = str(
            arguments.get("id")
            or arguments.get("target_id")
            or arguments.get("item_id")
            or ""
        ).strip()
        chat_type = str(arguments.get("type") or arguments.get("chat_type") or "").strip().lower()
        if not platform or not target_id or chat_type not in {"group", "private"}:
            return FocusTargetResolution(None, "需要提供 chat_id，或提供 platform、id、type(group/private) 组合。")

        matched_sessions: list[BotChatSession] = []
        for session in session_by_id.values():
            if str(session.platform or "").strip() != platform:
                continue
            session_target_id = session.group_id if chat_type == "group" else session.user_id
            if str(session_target_id or "").strip() == target_id:
                matched_sessions.append(session)
        if not matched_sessions:
            return FocusTargetResolution(
                None,
                f"未找到 platform={platform} id={target_id} type={chat_type} 对应的运行中已创建聊天。",
            )
        if len(matched_sessions) > 1:
            matched_ids = ", ".join(session.session_id for session in matched_sessions)
            return FocusTargetResolution(None, f"匹配到多个聊天，请改用 chat_id 指定：{matched_ids}")
        return FocusTargetResolution(matched_sessions[0])


focus_mode_manager = FocusModeManager()
