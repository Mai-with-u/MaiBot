"""Focus 共享组的配置解析与运行时归属。"""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Dict, Optional

import asyncio
import json

from src.chat.message_receive.chat_manager import chat_manager
from src.common.utils.utils_config import ChatConfigUtils
from src.config.config import config_manager, global_config
from src.maisaka.context.messages import SessionBackedMessage

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting
    from .group_runtime import FocusGroupRuntime


class FocusModeManager:
    """同一 bot 循环内维护每个共享组唯一的 Planner。"""

    def __init__(self) -> None:
        self.groups: Dict[str, FocusGroupRuntime] = {}
        self._lock: Optional[asyncio.Lock] = None
        self._owner_loop: Optional[asyncio.AbstractEventLoop] = None
        self._reload_registered = False

    def bind_loop(self) -> None:
        """配置可从 WebUI 循环重载，运行时变更必须投递回 bot 所属循环。"""
        loop = asyncio.get_running_loop()
        if self._owner_loop is not loop:
            if self.groups:
                raise RuntimeError("Focus 共享组不能跨事件循环修改")
            self._owner_loop = loop
            self._lock = None
        if not self._reload_registered:
            config_manager.register_reload_callback(self.reconcile_all, sections=("experimental",))
            self._reload_registered = True

    async def reconcile_all(self) -> None:
        if self._owner_loop is None or not self._owner_loop.is_running():
            return
        if asyncio.get_running_loop() is not self._owner_loop:
            future = asyncio.run_coroutine_threadsafe(self._reconcile_all_on_owner(), self._owner_loop)
            await asyncio.wrap_future(future)
            return
        await self._reconcile_all_on_owner()

    async def _reconcile_all_on_owner(self) -> None:
        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        for runtime in list(heartflow_manager.heartflow_chat_list.values()):
            await self.reconcile(runtime)

    def is_enabled(self) -> bool:
        return global_config.experimental.focus_mode

    def is_enabled_for_chat(self, *, is_group_chat: Optional[bool] = None) -> bool:
        return self.is_enabled() and (is_group_chat is not False or global_config.experimental.focus_on_private)

    def is_enabled_for_session(self, session_id: str, *, is_group_chat: Optional[bool] = None) -> bool:
        if not session_id:
            return False
        if is_group_chat is None:
            session = chat_manager.get_session_by_session_id(session_id)
            if session is None:
                return False
            is_group_chat = session.is_group_session
        if not self.is_enabled_for_chat(is_group_chat=is_group_chat):
            return False
        targets = global_config.experimental.focus_chat_whitelist
        return not targets or any(
            ChatConfigUtils.target_matches_session_with_wildcards(target, session_id, is_group_chat)
            for target in targets
        )

    def get_focus_scope_key(self, session_id: str) -> str:
        """组身份基于规则内容，不因配置列表重新排序而改变；不作为业务 session_id。"""
        session = chat_manager.get_session_by_session_id(session_id)
        if session is None:
            raise ValueError(f"Focus 聊天流不存在：{session_id}")
        groups = global_config.experimental.focus_groups
        if not groups:
            return "global"
        for group in groups:
            if any(
                ChatConfigUtils.target_matches_session_with_wildcards(target, session_id, session.is_group_session)
                for target in group.targets
            ):
                rules = [target.model_dump(mode="json") for target in group.targets]
                canonical = json.dumps(sorted(rules, key=lambda rule: json.dumps(rule, sort_keys=True)), sort_keys=True)
                return "group:" + sha256(canonical.encode()).hexdigest()[:24]
        return "session:" + session_id

    async def attach(self, runtime: MaisakaHeartFlowChatting) -> None:
        from .group_runtime import FocusGroupRuntime

        self.bind_loop()
        if self._lock is None:
            self._lock = asyncio.Lock()
        created = False
        async with self._lock:
            scope = self.get_focus_scope_key(runtime.session_id)
            group = self.groups.get(scope)
            if group is None:
                group = FocusGroupRuntime(scope)
                group._hydrating = True
                await group.restore()
                self.groups[scope] = group
                created = True
            group.add_member(runtime)
            runtime._focus_group = group
        if created:
            try:
                # 新版检查点里的其他真实会话按需恢复服务，工具可以继续指向它们，
                # 无须等这些聊天再次发消息；此处不持有组锁，避免递归注册死锁。
                from src.chat.heart_flow.heartflow_manager import heartflow_manager

                for sid in list(group.restored_session_ids):
                    if sid not in group.members:
                        session = chat_manager.get_session_by_session_id(sid)
                        if session is not None and await heartflow_manager._is_session_allowed(session):
                            await heartflow_manager.get_or_create_heartflow_chat(sid)
                        else:
                            group.entries = [entry for entry in group.entries if sid not in entry.session_ids]
            finally:
                group._hydrating = False
            group._ensure_worker()

    async def detach(self, runtime: MaisakaHeartFlowChatting, *, clear_context: bool = False) -> None:
        if runtime._focus_group is None:
            return
        if self._lock is None:
            raise RuntimeError("Focus 成员存在但共享组归属锁未初始化")
        async with self._lock:
            await self._detach_member(runtime, clear_context=clear_context)

    async def _detach_member(self, runtime: MaisakaHeartFlowChatting, *, clear_context: bool) -> None:
        group = runtime._focus_group
        if group is None:
            return
        runtime._focus_group = None
        await group.remove_member(runtime.session_id, clear_context=clear_context)
        if not group.members:
            await group.stop()
            self.groups.pop(group.scope_id, None)

    async def clear_context(self, session_id: str) -> None:
        from .context import FocusCheckpointStore

        if (
            self._owner_loop is not None
            and self._owner_loop.is_running()
            and asyncio.get_running_loop() is not self._owner_loop
        ):
            future = asyncio.run_coroutine_threadsafe(self.clear_context(session_id), self._owner_loop)
            await asyncio.wrap_future(future)
            return
        for group in list(self.groups.values()):
            if session_id in group.members:
                runtime = group.members[session_id]
                async with runtime._message_ingest_lock:
                    await self.detach(runtime, clear_context=True)
            elif any(session_id in entry.session_ids for entry in group.entries):
                await group.remove_member(session_id, clear_context=True)
        excluded = {group._store.path for group in self.groups.values()}
        await asyncio.to_thread(FocusCheckpointStore.purge_session, session_id, excluded)

    async def reconcile(self, runtime: MaisakaHeartFlowChatting) -> None:
        """在入站与配置刷新边界交接归属，避免普通循环与共享循环同时处理同一聊天。"""
        async with runtime._message_ingest_lock:
            if runtime._running:
                await self._reconcile_member(runtime)

    async def _reconcile_member(self, runtime: MaisakaHeartFlowChatting) -> None:
        enabled = self.is_enabled_for_session(runtime.session_id, is_group_chat=runtime.chat_stream.is_group_session)
        group = runtime._focus_group
        pending = []
        if group is not None and (not enabled or group.scope_id != self.get_focus_scope_key(runtime.session_id)):
            pending = await group.unprocessed_messages(runtime.session_id)
            await runtime._stop_background_subagents()
            await self.detach(runtime, clear_context=True)
        if enabled and runtime._focus_group is None:
            if runtime._internal_loop_task is not None:
                runtime._internal_loop_task.cancel()
                await asyncio.gather(runtime._internal_loop_task, return_exceptions=True)
                runtime._internal_loop_task = None
            runtime._clear_wait_state()
            await self.attach(runtime)
            pending.extend(runtime.message_cache[runtime._last_processed_index :])
            for message in {message.message_id: message for message in pending}.values():
                history = await runtime._reasoning_engine._build_history_message(message)
                await runtime._focus_group.register_message(runtime, message, history)
        elif not enabled:
            if pending:
                pending_ids = {message.message_id for message in pending}
                runtime._chat_history = [
                    message
                    for message in runtime._chat_history
                    if not isinstance(message, SessionBackedMessage) or message.message_id not in pending_ids
                ]
                runtime._last_processed_index = len(runtime.message_cache)
                runtime.message_cache.extend(pending)
                runtime._enter_stop_state()
            runtime._ensure_background_tasks_running()
            if pending:
                runtime._schedule_message_turn()


focus_mode_manager = FocusModeManager()
