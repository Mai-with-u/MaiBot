"""独立于桌宠窗口的单次日程调度，向真实聊天流投递内部任务。"""

from pathlib import Path
from typing import Any, Dict, Optional, Set

import asyncio
import json

from src.common.logger import get_logger
from src.common.paths import get_instance_root
from src.common.prompt_i18n import load_prompt
from src.core.internal_chat import INTERNAL_CHAT_PLATFORM, INTERNAL_CHAT_USER_ID

from .commands import execute_todo
from .store import TodoStore

logger = get_logger("schedule")


class ScheduleService:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._store: Optional[TodoStore] = None
        self._lock = asyncio.Lock()
        self._worker: Optional[asyncio.Task[None]] = None
        self._operations: Set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        async with self._lock:
            if self._store is None:
                # 保留既有事项和附件路径，无需复制数据或依赖桌宠启动。
                path = self._path or get_instance_root() / "data/plugins/sengokucola.desktop-pet/todos.json"
                self._store = await asyncio.to_thread(TodoStore, path)
            if self._worker is None or self._worker.done():
                self._worker = asyncio.create_task(self._run(), name="chat-schedules")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        # 文件提交不可取消，关机前等待唯一写入方完成提交。
        await asyncio.gather(*self._operations, return_exceptions=True)

    def _execute(self, command: Dict[str, Any], session_id: Optional[str]) -> Dict[str, Any]:
        store = self._store
        if store is None:
            raise RuntimeError("日程服务尚未启动")
        with store._lock:
            if session_id is not None:
                from src.desktop_pet.chat_tools import is_owner_chat

                owner = is_owner_chat(session_id)
                def belongs(item: Dict[str, Any]) -> bool:
                    return item["session_id"] == session_id or (owner and not item["session_id"])

                action = command["action"]
                if action == "list":
                    return {"items": [item for item in store.list(command.get("include_completed", False))
                                      if belongs(item)]}
                if action not in {"add", "update", "cancel", "complete"}:
                    raise ValueError("不支持的日程操作")
                if action != "add":
                    item = next((item for item in store.list(True) if item["id"] == command["todo_id"]
                                 and belongs(item)), None)
                    if item is None:
                        raise ValueError("当前聊天中不存在此日程，请先查询真实 ID")
                options = command.get("options", {})
                if action in {"add", "update"} and options.get("session_id", session_id) != session_id:
                    raise ValueError("不能修改日程所属聊天")
            return execute_todo(store, command)

    async def command(self, command: Dict[str, Any], *, session_id: Optional[str] = None) -> Dict[str, Any]:
        async def commit() -> Dict[str, Any]:
            async with self._lock:
                return await asyncio.to_thread(self._execute, command, session_id)

        operation = asyncio.create_task(commit())
        self._operations.add(operation)
        def finished(task: asyncio.Task[Any]) -> None:
            self._operations.discard(task)
            if not task.cancelled():
                task.exception()

        operation.add_done_callback(finished)
        result = await asyncio.shield(operation)
        if command["action"] not in {"list", "image", "claim_due"}:
            self._refresh_desktop()
        return result

    @staticmethod
    def _refresh_desktop() -> None:
        from src.desktop_pet.service import desktop_pet_service

        if desktop_pet_service.status()["ready"]:
            desktop_pet_service._observe(desktop_pet_service._command({"type": "todo_refresh"}))

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("日程调度检查失败")
            await asyncio.sleep(1)

    async def tick(self) -> None:
        from src.desktop_pet.service import desktop_pet_service

        # 用户桌面提醒离线时保留 pending；Bot 日程始终可投递。
        command: Dict[str, Any] = {"action": "claim_due"}
        if not desktop_pet_service.status()["ready"]:
            command["kind"] = "bot"
        claimed = await self.command(command)
        for item in claimed["items"]:
            try:
                await self.dispatch(item)
            except asyncio.CancelledError:
                # 取消可能发生于领取后，恢复 pending 由下一次启动重新投递。
                raise
            except Exception as exc:
                logger.exception("日程 %s 触发失败", item["id"])
                await self.command({"action": "finish", "todo_id": item["id"],
                                    "revision": item["revision"], "error": str(exc)})
            else:
                await self.command({"action": "finish", "todo_id": item["id"], "revision": item["revision"]})

    async def dispatch(self, item: Dict[str, Any]) -> None:
        from src.chat.heart_flow.heartflow_manager import heartflow_manager
        from src.chat.message_receive.chat_manager import chat_manager
        from src.desktop_pet.service import desktop_pet_service

        # 编辑、取消、完成和投递按同一把锁排序，旧版本不能覆盖新计划。
        async with self._lock:
            snapshot = await asyncio.to_thread(self._execute, {"action": "list"}, None)
            if not any(entry["id"] == item["id"] and entry["revision"] == item["revision"]
                       and entry["status"] == "dispatching" for entry in snapshot["items"]):
                return
            if item["kind"] == "user":
                await desktop_pet_service.control("show")
                await desktop_pet_service._command({"type": "reply", "parts": [
                    {"kind": "text", "text": "⏰ 提醒：" + item["text"]},
                ]})
                return
            if item["session_id"]:
                session = chat_manager.get_session_by_session_id(item["session_id"])
                if session is None:
                    raise ValueError("日程所属聊天流已不存在")
            else:
                # 既有桌宠事项归主人内部私聊，解析真实会话，禁止自行计算 ID。
                sessions = await asyncio.to_thread(chat_manager.resolve_sessions_by_target,
                    platform=INTERNAL_CHAT_PLATFORM, target_id=INTERNAL_CHAT_USER_ID, chat_type="private")
                if len(sessions) != 1:
                    raise ValueError("无法确定主人聊天流，请先建立唯一的主人会话")
                session = sessions[0]
            if not await heartflow_manager._is_session_allowed(session):
                raise PermissionError("当前策略禁止唤醒日程所属聊天流")
            runtime = await heartflow_manager.get_or_create_heartflow_chat(session.session_id)
            payload = json.dumps({"id": item["id"], "text": item["text"], "due_at": item["due_at"]}, ensure_ascii=False)
            text = await asyncio.to_thread(load_prompt, "schedule_due", schedule=payload)
            runtime.enqueue_internal_task(source_kind="schedule", text=text.strip(), reason="日程到期")
            logger.info("日程已投递: id=%s session=%s", item["id"], item["session_name"])


schedule_service = ScheduleService()
