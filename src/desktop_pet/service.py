"""由主程序管理桌宠生命周期，插件通过受授权的能力调用控制窗口。"""

from collections import deque
from contextlib import suppress
from typing import Any, Callable, Coroutine, Deque, Dict, List, Optional, Set
import asyncio
import json
import subprocess
import sys
import time
import uuid

from src.common.data_models.message_component_data_model import ImageComponent
from src.common.logger import get_logger
from src.common.paths import get_instance_root
from src.common.runtime_loop import run_on_main_loop
from src.config.config import config_manager, global_config
from src.core.internal_chat import INTERNAL_CHAT_PLATFORM, INTERNAL_CHAT_USER_ID

from .app_conditions import matching_application, running_applications
from .rendering import render_parts
from .remote_protocol import RemotePeer, decode_screenshot
from .screen import DesktopScreenshot, ScreenTimeline, capture_desktop, compose_screen_timeline
from .thinking_caption import ThinkingCaption, caption_enabled
from .todo_dispatch import execute_todo
from .todos import TodoStore

logger = get_logger("desktop_pet")
COMMAND_TIMEOUT = 3
STOP_TIMEOUT = 5


class DesktopPetService:
    def __init__(self) -> None:
        self._worker: Optional[asyncio.Task[None]] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._remote: Optional[RemotePeer] = None
        self._remote_todos: Optional[TodoStore] = None
        self._todo_task: Optional[asyncio.Task[None]] = None
        self._todo_operations: Set[asyncio.Task[Dict[str, Any]]] = set()
        self._active_mode: Optional[str] = None
        self._ready = False
        self._visible = False
        self._error = ""
        self._skin_id = "builtin"
        self._closing = True
        self._reload_registered = False
        self._lifecycle_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._pending: Dict[str, asyncio.Future[Any]] = {}
        self._notifications: Set[asyncio.Task[None]] = set()
        self._button_unsubscribe: Optional[Callable[[], None]] = None
        self._button_sync_task: Optional[asyncio.Task[None]] = None
        self._button_revision = 0
        self._proactive_wake_task: Optional[asyncio.Task[None]] = None
        self._screen_reader_task: Optional[asyncio.Task[None]] = None
        self._screen_busy = False
        self._screen_error = ""
        self._screen_last_capture = ""
        self._thinking_caption = ThinkingCaption(self._command)
        # 供 WebUI 首页卡片展示：窗口当前是否在思考、最近一次显示的气泡文字
        self._thinking = False
        self._bubble = ""

    def observe_stage(self, data: Dict[str, Any]) -> None:
        from .chat_tools import is_owner_chat

        if self._ready and not self._closing and is_owner_chat(data["session_id"]):
            self._thinking_caption.update(data if caption_enabled() else {"session_id": data["session_id"]})

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": global_config.desktop_pet.enabled,
            "supported": global_config.desktop_pet.mode == "remote" or sys.platform == "win32",
            "running": self._remote is not None or (self._process is not None and self._process.returncode is None),
            "mode": global_config.desktop_pet.mode,
            "client_id": self._remote.client_id if self._remote is not None else "",
            "ready": self._ready and not self._closing,
            "visible": self._visible,
            "error": self._error,
            "skin_id": self._skin_id,
            "screen_reading": self._screen_reading(),
            "thinking": self._thinking and self._ready and not self._closing,
            "bubble": self._bubble,
        }

    async def start(self) -> None:
        if self._button_unsubscribe is None:
            from src.plugin_runtime.integration import get_plugin_runtime_manager

            loop = asyncio.get_running_loop()
            def changed() -> None:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._buttons_changed)
            self._button_unsubscribe = get_plugin_runtime_manager().subscribe_status_changes(changed)
        if not self._reload_registered:
            config_manager.register_reload_callback(self.reload, sections=("desktop_pet", "experimental"))
            self._reload_registered = True
        await self.reload()

    async def reload(self) -> None:
        await run_on_main_loop(self._reload())

    async def _reload(self) -> None:
        async with self._lifecycle_lock:
            mode = global_config.desktop_pet.mode
            if (global_config.desktop_pet.enabled and not self._closing and self._active_mode == mode
                    and (self._ready or mode == "remote")):
                if not caption_enabled():
                    self._thinking_caption.update({"session_id": ""})
                if self._ready and self._skin_id != global_config.desktop_pet.skin_id:
                    await self.apply_skin(global_config.desktop_pet.skin_id)
                self._restart_proactive_wake()
                if not global_config.desktop_pet.screen_read_enabled:
                    await self._stop_screen_reader()
                await self._publish_screen_state()
                await self._publish_exploration_state()
                return
            await self._stop()
            self._error = ""
            if not global_config.desktop_pet.enabled:
                return
            self._active_mode = mode
            if mode == "remote":
                def open_todos() -> TodoStore:
                    folder = get_instance_root() / "data" / "plugins" / "sengokucola.desktop-pet"
                    folder.mkdir(parents=True, exist_ok=True)
                    return TodoStore(folder / "todos.json")

                self._remote_todos = await asyncio.to_thread(open_todos)
                self._closing = False
                self._todo_task = asyncio.create_task(self._run_remote_todos(), name="remote-desktop-pet-todos")
                self._restart_proactive_wake()
                logger.info("远端桌宠已启用，等待客户端连接")
                return
            if sys.platform != "win32":
                self._error = "桌宠目前仅支持 Windows 桌面环境"
                logger.error(self._error)
                return
            self._closing = False
            self._worker = asyncio.create_task(self._run(), name="desktop-pet")

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop()
        if self._button_unsubscribe is not None:
            self._button_unsubscribe()
            self._button_unsubscribe = None

    async def _stop(self) -> None:
        self._closing = True
        self._thinking = False
        self._thinking_caption.cancel()
        if self._todo_task is not None:
            self._todo_task.cancel()
            await asyncio.gather(self._todo_task, return_exceptions=True)
            self._todo_task = None
        peer = self._remote
        if peer is not None:
            await self._detach_remote(peer)
            await peer.close("桌宠已关闭或运行模式已切换")
        # to_thread 的文件提交不能被取消；切换到本机窗口前等待唯一写入者完成。
        await asyncio.gather(*self._todo_operations, return_exceptions=True)
        self._remote_todos = None
        self._active_mode = None
        for task in self._notifications:
            task.cancel()
        await asyncio.gather(*self._notifications, return_exceptions=True)
        self._notifications.clear()
        worker, self._worker = self._worker, None
        if worker is None:
            return
        process = self._process
        if process is not None and process.stdin is not None:
            # 关闭指令管道后窗口进程会自行收起退出。
            process.stdin.close()
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=STOP_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("桌宠窗口未及时退出，强制结束")
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    def _require_remote(self, peer: RemotePeer) -> None:
        if self._remote is not peer or self._closing or not self._ready:
            raise ConnectionError("远端桌宠连接已断开或尚未就绪")

    async def attach_remote(self, peer: RemotePeer) -> None:
        """宿主完成认证后，在主循环排他注册一个设备；Linux 不启动 GUI。"""
        async with self._lifecycle_lock:
            if not global_config.desktop_pet.enabled or global_config.desktop_pet.mode != "remote" or self._closing:
                raise RuntimeError("请先启用桌宠并将运行模式设为 remote")
            if self._remote is not None:
                raise RuntimeError("已有桌宠连接，请先关闭原客户端")
            self._remote = peer
            self._error = ""

    async def detach_remote(self, peer: RemotePeer) -> None:
        # 新设备必须等待旧设备的命令、采样和通知全部清理完成。
        async with self._lifecycle_lock:
            await self._detach_remote(peer)

    async def _detach_remote(self, peer: RemotePeer) -> None:
        if self._remote is not peer:
            return
        was_ready = self._ready
        self._remote = None
        self._ready = self._visible = False
        self._thinking_caption.cancel()
        await self._stop_screen_reader()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ConnectionError("远端桌宠已断开"))
        # 旧设备的窗口通知不能在稍后重连时发到新连接；服务器定时任务独立保留。
        pending = [task for task in self._notifications if task is not self._proactive_wake_task]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if was_ready:
            await self._dispatch_hook("closed", reason="stopped" if self._closing else "unexpected")

    async def receive_remote_event(self, peer: RemotePeer, event: Dict[str, Any]) -> None:
        if self._remote is not peer or self._closing:
            raise ConnectionError("远端桌宠连接已失效")
        if event["type"] == "ready":
            if self._ready:
                raise ValueError("桌宠不能重复上报就绪")
            # 确认业务已可用后客户端才开放 HTTP；初始化指令在确认帧之后发送。
            async with self._write_lock:
                self._ready = True
                await peer.send({"type": "ready", "version": 1})
        else:
            self._require_remote(peer)
        self._handle_event(event)

    async def remote_chat(self, peer: RemotePeer, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        from src.webui.routers.chat.service import relay_plugin_chat_message

        self._require_remote(peer)
        await relay_plugin_chat_message("builtin.desktop_pet", payload["text"], images=payload["images"])
        return {"accepted": True, "request_id": request_id}

    async def remote_todo(self, peer: RemotePeer, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_remote(peer)
        if action not in {"list", "image", "add", "update", "complete", "delete"}:
            raise ValueError("不允许客户端调用此待办操作")
        return await self._todo_command({**payload, "type": "todo", "action": action})

    async def _todo_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        store = self._remote_todos
        if store is None:
            raise RuntimeError("远端待办存储尚未启动")
        operation = asyncio.create_task(asyncio.to_thread(execute_todo, store, command))
        self._todo_operations.add(operation)

        def finished(task: asyncio.Task[Dict[str, Any]]) -> None:
            self._todo_operations.discard(task)
            if not task.cancelled():
                task.exception()  # HTTP 断开后仍消费后台提交的异常。

        operation.add_done_callback(finished)
        result = await asyncio.shield(operation)
        if self._ready and command["action"] not in {"list", "image", "claim_due"}:
            # 持久化成功即返回；窗口刷新失败不能把已完成的写入误报为失败。
            self._observe(self._command({"type": "todo_refresh"}))
        return result

    def _backend_active(self) -> bool:
        return not self._closing and (self._ready or self._active_mode == "remote")

    async def _run(self) -> None:
        stderr_tail: Deque[str] = deque(maxlen=30)
        process = None
        stderr_task = None
        try:
            # 继续使用旧桌宠的数据目录，待办和窗口位置无需复制或迁移。
            root = get_instance_root()
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "src.desktop_pet.pet_window",
                "--data-dir", str(root / "data" / "plugins" / "sengokucola.desktop-pet"),
                "--runtime-dir", str(root / "temp" / "desktop_pet"),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._process = process
            stderr_task = asyncio.create_task(self._collect_stderr(process, stderr_tail))
            assert process.stdout is not None
            while line := await asyncio.wait_for(process.stdout.readline(), timeout=None if self._ready else 30):
                self._handle_event(json.loads(line))
            code = await process.wait()
            await stderr_task
            if not self._closing:
                raise RuntimeError(f"桌宠窗口退出: code={code}\n" + "\n".join(stderr_tail))
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._error = "桌宠窗口在 30 秒内未就绪"
            logger.exception(self._error)
        except Exception as exc:
            self._error = str(exc)
            logger.exception("桌宠窗口运行失败")
        finally:
            self._thinking_caption.cancel()
            was_ready = self._ready
            self._ready = self._visible = False
            self._restart_proactive_wake()
            await self._stop_screen_reader()
            self._process = None
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("桌宠窗口已退出"))
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            if stderr_task is not None:
                stderr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stderr_task
            if was_ready:
                self._observe(self._dispatch_hook("closed", reason="stopped" if self._closing else "unexpected"))

    async def _collect_stderr(self, process: asyncio.subprocess.Process, tail: Deque[str]) -> None:
        # 窗口进程的 stderr 混有 WebView2 的噪声，平时只记调试日志，异常退出时再整体输出。
        assert process.stderr is not None
        while line := await process.stderr.readline():
            text = line.decode("utf-8", "replace").rstrip()
            tail.append(text)
            logger.debug("桌宠窗口: %s", text)

    def _handle_event(self, event: Dict[str, Any]) -> None:
        kind = event["type"]
        if kind == "ready":
            self._ready = self._visible = True
            logger.info("内置桌宠已出现在桌面上")
            self._observe(self.apply_skin(global_config.desktop_pet.skin_id))
            self._observe(self._dispatch_hook("ready"))
            self._buttons_changed()
            if self._remote is None:
                self._observe(self._run_todos())
            self._restart_proactive_wake()
            self._observe(self._publish_screen_state())
            self._observe(self._publish_exploration_state())
        elif kind == "buttons_refresh":
            self._buttons_changed()
        elif kind == "button_click":
            self._observe(self._click_button(event["plugin_id"], event["button_id"], event.get("action_id")))
        elif kind == "clicked":
            self._observe(self._dispatch_hook(kind))
        elif kind == "drag_started":
            self._observe(self._dispatch_hook(kind, **{
                key: event[key] for key in ("gesture_id", "offset_x", "offset_y", "scale")
            }))
        elif kind == "drag_ended":
            self._observe(self._dispatch_hook(kind, gesture_id=event["gesture_id"]))
        elif kind == "screen_action":
            self._observe(self._screen_action(event["action"]))
        elif kind == "chat":
            self._observe(self._chat(event["request_id"]))
        elif kind == "exploration_tasks":
            self._observe(self._publish_exploration_tasks(event["task_id"], event["request_id"]))
        elif kind == "explore":
            self._observe(self._explore(event["goal"]))
        elif kind == "ack":
            future = self._pending.get(event["id"])
            if future is not None and not future.done():
                if event.get("error"):
                    future.set_exception(RuntimeError(event["error"]))
                else:
                    future.set_result(event.get("result"))
        else:
            raise ValueError(f"未知的桌宠事件: {kind}")

    def _screen_reading(self) -> bool:
        return self._screen_reader_task is not None and not self._screen_reader_task.done()

    def _require_screen_access(self) -> None:
        if not global_config.desktop_pet.screen_read_enabled:
            raise PermissionError("请先在 WebUI 桌宠设置开启允许读取桌面")
        if not self._ready or self._closing:
            raise RuntimeError("桌宠未就绪，无法读取桌面")
        if self._remote is not None and not self._remote.capabilities["screen_read"]:
            raise PermissionError("请在远端桌宠客户端允许读取本机桌面")

    async def capture_screen(self) -> DesktopScreenshot:
        self._require_screen_access()
        peer = self._remote
        if peer is None:
            shot = await asyncio.to_thread(capture_desktop)
        else:
            result = await self._command({"type": "capture_screen"})
            shot = await asyncio.to_thread(decode_screenshot, result)
            if self._remote is not peer:
                raise ConnectionError("截图过程中远端桌宠已断开")
        # 截图期间关闭权限时，不再把结果交给模型。
        self._require_screen_access()
        return shot

    async def _publish_screen_state(self) -> None:
        if self._ready and not self._closing:
            enabled = global_config.desktop_pet.screen_read_enabled and (
                self._remote is None or self._remote.capabilities["screen_read"])
            await self._command({"type": "screen_state", "enabled": enabled,
                "reading": self._screen_reading(), "busy": self._screen_busy,
                "captured_at": self._screen_last_capture, "error": self._screen_error})

    async def _stop_screen_reader(self) -> None:
        task, self._screen_reader_task = self._screen_reader_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _screen_action(self, action: str) -> None:
        try:
            self._screen_error = ""
            if action == "stop":
                await self._stop_screen_reader()
            elif action == "start":
                self._require_screen_access()
                if not self._screen_reading():
                    self._screen_reader_task = self._observe(self._run_screen_reader())
            elif action == "read":
                await self._read_screen_once()
            else:
                raise ValueError("未知桌面读取操作")
        except Exception as exc:
            self._screen_error = str(exc)
            logger.exception("桌宠桌面读取失败")
        finally:
            await self._publish_screen_state()

    async def _run_screen_reader(self) -> None:
        timeline = ScreenTimeline()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + global_config.desktop_pet.screen_read_interval_seconds
        first_sample = True
        try:
            while self._ready and not self._closing and global_config.desktop_pet.screen_read_enabled:
                if self._remote is None:
                    shot = await self.capture_screen()
                else:
                    # 在设备侧筛选变化，静止画面只返回小回执，不反复上传完整截图。
                    self._require_screen_access()
                    peer = self._remote
                    result = await self._command({"type": "sample_screen", "reset": first_sample,
                        "threshold": global_config.desktop_pet.screen_change_threshold})
                    self._require_screen_access()
                    if self._remote is not peer:
                        raise ConnectionError("屏幕采样期间设备已断开")
                    shot = None if result.get("unchanged") is True else await asyncio.to_thread(decode_screenshot, result)
                first_sample = False
                if shot is not None:
                    await asyncio.to_thread(timeline.add, shot, global_config.desktop_pet.screen_change_threshold)
                if loop.time() >= deadline:
                    if timeline.changed:
                        frames = list(timeline.frames)
                        storyboard = await asyncio.to_thread(compose_screen_timeline, frames)
                        await self._deliver_screen(storyboard, frame_times=[frame.captured_at for frame in frames])
                        timeline.delivered()
                    # 不追赶休眠期间的周期；采样与投递串行，不堆积待处理截图。
                    deadline = loop.time() + global_config.desktop_pet.screen_read_interval_seconds
                await asyncio.sleep(min(global_config.desktop_pet.screen_sample_interval_seconds,
                                        max(0, deadline - loop.time())))
        except Exception as exc:
            self._screen_error = str(exc)
            logger.exception("桌宠持续阅读停止")
        finally:
            if self._screen_reader_task is asyncio.current_task():
                self._screen_reader_task = None
            await self._publish_screen_state()

    async def _read_screen_once(self) -> None:
        if self._screen_busy:
            return
        self._screen_busy = True
        try:
            await self._publish_screen_state()
            shot = await self.capture_screen()
            await self._deliver_screen(shot)
        finally:
            self._screen_busy = False
            await self._publish_screen_state()

    async def _deliver_screen(self, shot: DesktopScreenshot, *, frame_times: Optional[List[str]] = None) -> None:
        self._screen_busy = True
        try:
            await self._publish_screen_state()
            from src.prompt.prompt_manager import prompt_manager

            template = await asyncio.to_thread(prompt_manager.get_prompt, "desktop_pet_screen_read")
            text = await prompt_manager.render_prompt(template)
            mode = "continuous" if frame_times is not None else "once"
            text += f"\n[captured_at={shot.captured_at}; mode={mode}; screen=primary]"
            if frame_times is not None:
                text += "\n[timeline: left-to-right, top-to-bottom; selected keyframes; not a live single frame]\n"
                text += json.dumps({"frame_times": frame_times}, ensure_ascii=False)
            self._require_screen_access()
            await self._enqueue_owner_task(source_kind="desktop_pet_screen",
                text=text, reason="桌宠桌面阅读",
                images=[ImageComponent(binary_hash=shot.digest, binary_data=shot.data)])
            self._screen_last_capture = shot.captured_at
        finally:
            self._screen_busy = False
            await self._publish_screen_state()

    def _restart_proactive_wake(self) -> None:
        if self._proactive_wake_task is not None:
            self._proactive_wake_task.cancel()
            self._proactive_wake_task = None
        if self._backend_active() and global_config.desktop_pet.proactive_wake_enabled:
            self._proactive_wake_task = self._observe(self._run_proactive_wake())

    async def _run_proactive_wake(self) -> None:
        # 从就绪或配置热更新起等待完整间隔；恢复后只触发一次，不累积离线周期。
        while self._backend_active() and global_config.desktop_pet.proactive_wake_enabled:
            await asyncio.sleep(global_config.desktop_pet.proactive_wake_interval_minutes * 60)
            if not self._backend_active() or not global_config.desktop_pet.proactive_wake_enabled:
                return
            try:
                await self._wake_proactively()
            except Exception as exc:
                self._error = str(exc)
                logger.exception("桌宠定期主动思考唤醒失败")

    async def _publish_exploration_state(self) -> None:
        if self._ready and not self._closing:
            await self._command({"type": "exploration_state",
                                 "enabled": global_config.experimental.desktop_pet_exploration_enabled})

    async def _exploration_session_id(self) -> str:
        from src.chat.message_receive.chat_manager import chat_manager

        sessions = await asyncio.to_thread(chat_manager.resolve_sessions_by_target,
            platform=INTERNAL_CHAT_PLATFORM, target_id=INTERNAL_CHAT_USER_ID, chat_type="private")
        if len(sessions) != 1:
            raise ValueError("请先在桌宠或网页聊天中建立唯一的主人会话")
        return sessions[0].session_id

    async def _publish_exploration_tasks(self, task_id: str, request_id: str) -> None:
        from .exploration import experience_ref, exploration_tasks

        payload: Dict[str, Any] = {"type": "exploration_tasks", "request_id": request_id,
                                   "items": [], "selected": None, "error": ""}
        try:
            session_id = await self._exploration_session_id()
            records = await asyncio.to_thread(exploration_tasks, session_id)
            payload["items"] = [{**{key: item[key] for key in ("id", "goal", "status", "created_at", "updated_at")},
                                 "resource_count": len(item.get("resources", [])),
                                 "experience_count": len(item.get("experiences", []))} for item in records]
            payload["selected"] = next((item for item in records if item["id"] == task_id), None)
            if not task_id and records:
                payload["selected"] = records[0]
            if payload["selected"] is not None:
                payload["selected"]["experience_refs"] = [experience_ref(item)
                    for item in payload["selected"].get("experiences", [])]
        except Exception as exc:
            payload["error"] = str(exc)
            logger.exception("读取探索任务失败")
        await self._command(payload)

    async def _explore(self, goal: str) -> None:
        from src.prompt.prompt_manager import prompt_manager
        from .exploration import exploration_tasks, validate_goal

        error = ""
        task = None
        try:
            if not global_config.experimental.desktop_pet_exploration_enabled:
                raise PermissionError("请先在实验性功能中开启桌宠自由探索")
            goal = validate_goal(goal)
            session_id = await self._exploration_session_id()
            task = await asyncio.to_thread(exploration_tasks, session_id, "create", goal=goal)
            template = await asyncio.to_thread(prompt_manager.get_prompt, "desktop_pet_exploration")
            text = await prompt_manager.render_prompt(template)
            await self._enqueue_owner_task(source_kind="desktop_pet_exploration",
                text=text + f"\n[exploration_task_id={task['id']}]\n" + goal, reason="主人发起探索任务")
        except Exception as exc:
            error = str(exc)
            logger.exception("桌宠探索任务投递失败")
            if task is not None:
                await asyncio.to_thread(exploration_tasks, session_id, "fail", task["id"], summary=error[:2000])
        await self._command({"type": "exploration_result", "error": error, "task_id": task["id"] if task else ""})

    async def _wake_proactively(self) -> None:
        from src.prompt.prompt_manager import prompt_manager

        template = await asyncio.to_thread(prompt_manager.get_prompt, "desktop_pet_proactive_wake")
        text = await prompt_manager.render_prompt(template)
        await self._enqueue_owner_task(
            source_kind="desktop_pet_proactive_wake", text=text, reason="桌宠定期主动思考",
        )

    async def _run_todos(self) -> None:
        next_app_check = 0.0
        while self._backend_active():
            # 离线时仍调度 Bot 自身任务，用户提醒保留 pending，设备重连后再领取。
            claim: Dict[str, Any] = {"type": "todo", "action": "claim_due"}
            if self._active_mode == "remote" and not self._ready:
                claim["kind"] = "bot"
            result = await self._command(claim)
            for item in result["items"]:
                # 编辑、删除已领取但尚未投递的事项时，不再执行旧版本。
                current = await self._command({"type": "todo", "action": "list"})
                if not any(entry["id"] == item["id"] and entry["revision"] == item["revision"]
                           for entry in current["items"]):
                    continue
                error = ""
                try:
                    await self._activate_todo(item)
                except Exception as exc:
                    error = str(exc)
                    logger.exception("桌宠定时事项 %s 触发失败", item["id"])
                await self._command({"type": "todo", "action": "finish", "todo_id": item["id"],
                                     "revision": item["revision"], "error": error})
            if self._ready and not self._closing and time.monotonic() >= next_app_check:
                try:
                    await self._check_app_todos()
                except Exception as exc:
                    self._error = str(exc)
                    logger.exception("桌宠应用条件检测失败")
                next_app_check = time.monotonic() + 5
            await asyncio.sleep(1)

    async def _run_remote_todos(self) -> None:
        try:
            await self._run_todos()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            logger.exception("远端桌宠待办调度停止")

    async def _check_app_todos(self) -> None:
        result = await self._command({"type": "todo", "action": "list"})
        candidates = [item for item in result["items"] if item.get("condition_type") == "app"
                      and item["status"] == "pending" and (item["due_at"] is None or item["due_at"] <= time.time())]
        if not candidates:
            return
        applications = await self._running_applications()
        for item in candidates:
            evidence = matching_application(item["condition"], applications)
            if evidence:
                try:
                    await self.trigger_condition_todo(item["id"], item["revision"], evidence, applications=applications)
                except ValueError as exc:
                    logger.info("应用条件待办本次未触发: %s", exc)

    async def trigger_condition_todo(
        self, todo_id: str, revision: str, evidence: str, *, applications: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        snapshot = await self._command({"type": "todo", "action": "list"})
        original = next((item for item in snapshot["items"] if item["id"] == todo_id), None)
        if original is None or original["revision"] != revision:
            raise ValueError("待办已修改或删除，请重新查询")
        if original.get("condition_type") == "app":
            if applications is None:
                applications = await self._running_applications()
            evidence = matching_application(original["condition"], applications)
            if not evidence:
                raise ValueError("未检测到匹配的应用，不能触发")
        result = await self._command({"type": "todo", "action": "claim_condition", "todo_id": todo_id,
                                      "revision": revision, "evidence": evidence})
        item = result["item"]
        current = await self._command({"type": "todo", "action": "list"})
        if not any(entry["id"] == todo_id and entry["revision"] == revision for entry in current["items"]):
            raise ValueError("待办已修改或删除，本次触发取消")
        try:
            await self._activate_todo(item)
        except Exception as exc:
            await self._command({"type": "todo", "action": "finish", "todo_id": todo_id,
                                 "revision": revision, "error": str(exc)})
            raise
        await self._command({"type": "todo", "action": "finish", "todo_id": todo_id, "revision": revision})
        return {"id": todo_id, "status": "triggered", "evidence": item["evidence"]}

    async def _activate_todo(self, item: Dict[str, Any]) -> None:
        if item["kind"] == "user":
            await self.control("show")
            await self._command({"type": "reply", "parts": [{"kind": "text", "text": "⏰ 提醒：" + item["text"]}]})
            return
        await self._enqueue_owner_task(source_kind="desktop_pet_todo",
            text=f"[Bot 待办已触发，事项 ID: {item['id']}，这是内部任务]\n{item['text']}\n"
                 "请执行此待办；完成后使用 desktop_pet_todo 的 complete 操作归档事项。触发不代表任务已完成。",
            reason="桌宠待办触发")

    async def _running_applications(self) -> List[Dict[str, str]]:
        if self._active_mode != "remote":
            return await asyncio.to_thread(running_applications)
        response = await self._command({"type": "applications"})
        items = response["items"]
        if not isinstance(items, list) or len(items) > 10000:
            raise ValueError("远端应用列表无效")
        for item in items:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(key), str) or len(item[key]) > 4096 for key in ("process", "title")
            ):
                raise ValueError("远端应用信息无效")
        return items

    async def _enqueue_owner_task(
        self, *, source_kind: str, text: str, reason: str, images: Optional[List[ImageComponent]] = None,
    ) -> None:
        from src.chat.message_receive.chat_manager import chat_manager
        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        sessions = await asyncio.to_thread(chat_manager.resolve_sessions_by_target,
            platform=INTERNAL_CHAT_PLATFORM, target_id=INTERNAL_CHAT_USER_ID, chat_type="private")
        if len(sessions) != 1:
            raise ValueError("无法确定主人聊天流，请先在桌宠或网页聊天中建立唯一的主人会话")
        session = sessions[0]
        if not await heartflow_manager._is_session_allowed(session):
            raise PermissionError("当前策略禁止唤醒主人聊天流")
        runtime = await heartflow_manager.get_or_create_heartflow_chat(session.session_id)
        runtime.enqueue_internal_task(source_kind=source_kind, text=text, reason=reason, images=images if images is not None else [])

    async def _dispatch_hook(self, name: str, **kwargs: Any) -> None:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        await get_plugin_runtime_manager().invoke_hook(f"desktop_pet.{name}", **kwargs)

    def _observe(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        # 窗口通信故障需要完整记录，但不能阻断聊天发送或模型请求；SDK 调用则直接抛出异常。
        def finished(task: asyncio.Task[None]) -> None:
            self._notifications.discard(task)
            if not task.cancelled() and (exc := task.exception()) is not None:
                self._error = str(exc)
                logger.error("桌宠通知失败", exc_info=(type(exc), exc, exc.__traceback__))

        task = asyncio.create_task(coro)
        self._notifications.add(task)
        task.add_done_callback(finished)
        return task

    def _buttons_changed(self) -> None:
        self._button_revision += 1
        if not self._ready or self._closing:
            return
        if self._button_sync_task is None or self._button_sync_task.done():
            self._button_sync_task = self._observe(self._sync_buttons())

    async def _sync_buttons(self) -> None:
        """读取各插件的桌宠按钮状态并推送给窗口；同步期间按钮又有变动时重新同步一轮。"""
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        runtime = get_plugin_runtime_manager()
        while self._ready and not self._closing:
            revision = self._button_revision
            plugins = runtime.get_desktop_pet_button_plugins()
            results = await asyncio.gather(
                *(runtime.desktop_pet_buttons(plugin_id) for plugin_id in plugins), return_exceptions=True,
            )
            current = runtime.get_desktop_pet_button_plugins()
            buttons: List[Dict[str, Any]] = []
            # gather 按 plugins 的顺序逐个返回结果，两边数量必然相等。
            for (plugin_id, contribution), result in zip(plugins.items(), results, strict=True):
                if current.get(plugin_id) != contribution:
                    continue  # 请求期间已卸载或重载的贡献不能重新出现在窗口里。
                if isinstance(result, BaseException):
                    logger.warning("插件 {} 桌宠按钮状态读取失败: {}", plugin_id, result)
                    states = {button["id"]: {"visible": True, "disabled": True, "text": str(result)[:200]}
                              for button in contribution["buttons"]}
                else:
                    states = result["states"]
                buttons.extend({**button, **states[button["id"]], "plugin_id": plugin_id,
                                "plugin_name": contribution["name"]} for button in contribution["buttons"])
            buttons.sort(key=lambda button: (button["order"], button["plugin_id"], button["id"]))
            await self._command({"type": "buttons", "buttons": buttons})
            if revision == self._button_revision:
                return

    async def _click_button(self, plugin_id: str, button_id: str, action_id: Optional[str] = None) -> None:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        error = ""
        panel = None
        try:
            runtime = get_plugin_runtime_manager()
            result = (await runtime.desktop_pet_buttons(plugin_id, button_id) if action_id is None
                      else await runtime.desktop_pet_buttons(plugin_id, button_id, action_id))
            panel = result.get("panel")
        except Exception as exc:
            error = str(exc)
            logger.exception("插件 %s 桌宠按钮 %s 执行失败", plugin_id, button_id)
        if self._ready and not self._closing:
            await self._command({"type": "button_result", "plugin_id": plugin_id, "button_id": button_id,
                                 "error": error, "panel": panel})
            self._buttons_changed()

    async def _chat(self, request_id: str) -> None:
        from src.webui.routers.chat.service import relay_plugin_chat_message
        from .attachments import read_request

        try:
            request = await asyncio.to_thread(
                read_request, get_instance_root() / "temp" / "desktop_pet" / "requests", request_id,
            )
            await relay_plugin_chat_message("builtin.desktop_pet", request["text"], images=request["images"])
        except Exception as exc:
            # 单条消息投递失败只影响这一句，桌宠窗口继续运行。
            await self._command({"type": "failed", "request_id": request_id, "error": str(exc)})
            raise
        else:
            await self._command({"type": "accepted", "request_id": request_id})

    async def _command(self, command: Dict[str, Any]) -> Any:
        if command["type"] == "todo" and self._active_mode == "remote" and not self._closing:
            return await self._todo_command(command)
        process = self._process
        peer = self._remote
        if not self._ready or self._closing or (peer is None and (process is None or process.stdin is None)):
            raise RuntimeError("桌宠窗口尚未就绪，请检查 desktop_pet.enabled 和 desktop_pet.status")
        command_id = uuid.uuid4().hex
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            # 回执与超时覆盖管道背压，不能把“写入管道”误报为窗口已完成操作。
            timeout = 30 if peer is not None else (15 if command["type"] == "skin" else COMMAND_TIMEOUT)
            async with asyncio.timeout(timeout):
                async with self._write_lock:
                    if peer is not None:
                        if self._remote is not peer:
                            raise ConnectionError("远端桌宠连接已变化")
                        await peer.send({**command, "id": command_id})
                    else:
                        # 图片气泡可能携带较大的 base64，序列化不能占用主循环。
                        payload = await asyncio.to_thread(
                            lambda: json.dumps({**command, "id": command_id}).encode("ascii") + b"\n"
                        )
                        process.stdin.write(payload)
                        await process.stdin.drain()
                result = await future
                if command["type"] in {"show", "hide"}:
                    visible = command["type"] == "show"
                    if self._visible != visible:
                        self._visible = visible
                        self._observe(self._dispatch_hook("visibility_changed", visible=visible))
                return result
        finally:
            self._pending.pop(command_id, None)
            if not future.done():
                future.cancel()

    async def apply_skin(self, skin_id: str) -> None:
        from .skins import find_skin

        skin = await asyncio.to_thread(find_skin, get_instance_root(), skin_id)
        await self._command({"type": "skin", "skin": skin})
        changed = self._skin_id != skin_id
        self._skin_id = skin_id
        self._error = ""
        if changed:
            self._observe(self._dispatch_hook("skin_changed", skin_id=skin_id))

    async def animation(self, owner: str, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._command({"type": "animation", "owner": owner, "action": action, "args": args})
        if "error" in result:
            error = result["error"]
            raise ValueError(f"{error['code']}: {error['message']}")
        return result

    async def control(self, action: str, *, text: str = "", active: bool = True) -> Dict[str, Any]:
        """仅接受已校验的公开操作，调用方负责切换到主循环。"""
        if action == "status":
            return self.status()
        if action == "refresh_buttons":
            self._buttons_changed()
            return self.status()
        if action in {"show", "hide"}:
            command = {"type": action}
        elif action == "say":
            command = {"type": "notice", "parts": [{"kind": "text", "text": text}]}
            self._bubble = _bubble_text(text)
        elif action == "set_thinking":
            command = {"type": "thinking" if active else "idle"}
            self._thinking = active
        else:
            raise ValueError(f"未知桌宠操作: {action}")
        await self._command(command)
        return self.status()

    async def notify_thinking(self, session_id: str) -> None:
        from src.chat.message_receive.chat_manager import chat_manager

        if self._ready and not self._closing:
            session = chat_manager.get_session_by_session_id(session_id)
            if (session is not None and session.platform == INTERNAL_CHAT_PLATFORM
                    and not session.group_id and session.user_id == INTERNAL_CHAT_USER_ID):
                self._thinking = True
                self._observe(self._command({"type": "thinking"}))

    async def notify_reply(self, message: Dict[str, Any]) -> None:
        if not self._ready or self._closing or message["platform"] != INTERNAL_CHAT_PLATFORM:
            return
        info = message["message_info"]
        if info.get("group_info") or info.get("additional_config", {}).get("platform_io_target_user_id") != INTERNAL_CHAT_USER_ID:
            return
        self._observe(self._render_reply(message["raw_message"]))

    async def _render_reply(self, segments: List[Dict[str, Any]]) -> None:
        skipped: List[str] = []
        parts = await asyncio.to_thread(render_parts, segments, skipped)
        if skipped:
            logger.info("桌宠气泡不显示 %s 消息段，完整内容见网页聊天室", "、".join(skipped))
        if parts:
            # 主人会话的实际发言主动显示窗口；插件 notice 仍保持原有隐藏语义。
            if not self._visible:
                await self.control("show")
            await self._command({"type": "reply", "parts": parts})
            self._thinking = False
            text = "".join(part.get("text", "") for part in parts if part.get("kind") == "text")
            self._bubble = _bubble_text(text) or "[图片]"


def _bubble_text(text: str) -> str:
    """首页卡片只放一行摘要，完整内容在聊天记录里。"""
    text = " ".join(text.split())
    return text if len(text) <= 120 else f"{text[:119]}…"


desktop_pet_service = DesktopPetService()
