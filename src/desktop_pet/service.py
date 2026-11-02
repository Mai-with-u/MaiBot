"""由主程序管理桌宠生命周期，插件通过受授权的能力调用控制窗口。"""

from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Coroutine, Deque, Dict, List, Optional, Set
import asyncio
import json
import subprocess
import sys
import uuid

from src.common.logger import get_logger
from src.common.paths import get_instance_root
from src.common.runtime_loop import run_on_main_loop
from src.config.config import config_manager, global_config
from src.core.internal_chat import INTERNAL_CHAT_PLATFORM, INTERNAL_CHAT_USER_ID

from .rendering import render_parts

logger = get_logger("desktop_pet")
WINDOW_SCRIPT = Path(__file__).parent / "pet_window.py"
COMMAND_TIMEOUT = 3
STOP_TIMEOUT = 5


class DesktopPetService:
    def __init__(self) -> None:
        self._worker: Optional[asyncio.Task[None]] = None
        self._process: Optional[asyncio.subprocess.Process] = None
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

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": global_config.desktop_pet.enabled,
            "supported": sys.platform == "win32",
            "running": self._process is not None and self._process.returncode is None,
            "ready": self._ready and not self._closing,
            "visible": self._visible,
            "error": self._error,
            "skin_id": self._skin_id,
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
            config_manager.register_reload_callback(self.reload, sections=("desktop_pet",))
            self._reload_registered = True
        await self.reload()

    async def reload(self) -> None:
        await run_on_main_loop(self._reload())

    async def _reload(self) -> None:
        async with self._lifecycle_lock:
            if self._ready and global_config.desktop_pet.enabled:
                if self._skin_id != global_config.desktop_pet.skin_id:
                    await self.apply_skin(global_config.desktop_pet.skin_id)
                return
            await self._stop()
            self._error = ""
            if not global_config.desktop_pet.enabled:
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

    async def _run(self) -> None:
        stderr_tail: Deque[str] = deque(maxlen=30)
        process = None
        stderr_task = None
        try:
            # 继续使用旧桌宠的数据目录，待办和窗口位置无需复制或迁移。
            root = get_instance_root()
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(WINDOW_SCRIPT),
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
            was_ready = self._ready
            self._ready = self._visible = False
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
        elif kind == "buttons_refresh":
            self._buttons_changed()
        elif kind == "button_click":
            self._observe(self._click_button(event["plugin_id"], event["button_id"]))
        elif kind == "clicked":
            self._observe(self._dispatch_hook(kind))
        elif kind == "drag_started":
            self._observe(self._dispatch_hook(kind, **{
                key: event[key] for key in ("gesture_id", "offset_x", "offset_y", "scale")
            }))
        elif kind == "drag_ended":
            self._observe(self._dispatch_hook(kind, gesture_id=event["gesture_id"]))
        elif kind == "chat":
            self._observe(self._chat(event["request_id"]))
        elif kind == "ack":
            future = self._pending.get(event["id"])
            if future is not None and not future.done():
                if event.get("error"):
                    future.set_exception(RuntimeError(event["error"]))
                else:
                    future.set_result(event.get("result"))
        else:
            raise ValueError(f"未知的桌宠事件: {kind}")

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
                    logger.warning("插件 %s 桌宠按钮状态读取失败: %s", plugin_id, result)
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

    async def _click_button(self, plugin_id: str, button_id: str) -> None:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        error = ""
        try:
            await get_plugin_runtime_manager().desktop_pet_buttons(plugin_id, button_id)
        except Exception as exc:
            error = str(exc)
            logger.exception("插件 %s 桌宠按钮 %s 执行失败", plugin_id, button_id)
        if self._ready and not self._closing:
            await self._command({"type": "button_result", "plugin_id": plugin_id, "button_id": button_id, "error": error})
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
        process = self._process
        if not self._ready or self._closing or process is None or process.stdin is None:
            raise RuntimeError("桌宠窗口尚未就绪，请检查 desktop_pet.enabled 和 desktop_pet.status")
        command_id = uuid.uuid4().hex
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            # 回执与超时覆盖管道背压，不能把“写入管道”误报为窗口已完成操作。
            async with asyncio.timeout(15 if command["type"] == "skin" else COMMAND_TIMEOUT):
                async with self._write_lock:
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
        elif action == "set_thinking":
            command = {"type": "thinking" if active else "idle"}
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
            await self._command({"type": "reply", "parts": parts})


desktop_pet_service = DesktopPetService()
