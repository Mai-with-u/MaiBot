"""宿主运行主流程与本机控制通道；CLI 经进程间通道联系活着的宿主，从不按 PID 结束进程。"""

from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import asyncio
import json
import os
import signal
import sys

from src.common.ipc import allocate_endpoint
from src.common.process_lease import ProcessLease
from src.plugin_runtime.transport.base import Connection, ConnectionClosed
from src.plugin_runtime.transport.factory import create_transport_client, create_transport_server

from .layout import Workspace
from .console import HostConsole
from .maintenance import HostMaintenance
from .supervisor import BotSupervisor
from .webui import HostWebUI


_CONTROL_TIMEOUT = 120.0
# 安装依赖要停止全部 Bot 并下载安装包，耗时远超普通控制操作。
_ACTION_TIMEOUTS = {"install_plugin_dependencies": 1800.0, "start": 1800.0, "restart": 1800.0}


def _descriptor_path(workspace: Workspace) -> Path:
    return workspace.host_directory / "control.json"


def _publish(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class HostControl:
    def __init__(self, supervisor: BotSupervisor) -> None:
        self.supervisor = supervisor
        self.maintenance = HostMaintenance(supervisor)
        self.stopping = asyncio.Event()
        self.webui: Optional[HostWebUI] = None

    async def dispatch(self, request: Any) -> Any:
        if not isinstance(request, dict):
            raise ValueError("控制请求必须是 JSON 对象")
        action = request.get("action")
        if self.stopping.is_set() and action != "status":
            raise RuntimeError("宿主正在关闭")
        if action == "status":
            return await self.supervisor.statuses()
        if action == "open_webui":
            if self.webui is None:
                raise RuntimeError("宿主 WebUI 未启用")
            return {"url": await asyncio.to_thread(self.webui.open_url)}
        if action == "shutdown":
            # 先禁止新命令再回执；关闭是否完成由 host run 的退出结果表示。
            self.stopping.set()
            return {"status": "stopping"}
        if action == "plugin_dependencies":
            return await self.maintenance.analyze_plugin_dependencies()
        if action == "install_plugin_dependencies":
            return await self.maintenance.install_plugin_dependencies()
        if action == "prepare_plugin_dependencies":
            await self.supervisor.status(request["bot_id"])
            return {"status": "pending"}
        if action in {"start", "stop", "restart"}:
            bot_id = request.get("bot_id")
            if not isinstance(bot_id, str):
                raise ValueError("缺少 Bot ID")
            operation = {"start": self.supervisor.start, "stop": self.supervisor.stop, "restart": self.supervisor.restart}
            return await operation[action](bot_id)
        raise ValueError(f"未知宿主操作: {action}")

    async def handle(self, connection: Connection) -> None:
        try:
            request = json.loads(await asyncio.wait_for(connection.recv_frame(), timeout=10))
            response = {"ok": True, "result": await self.dispatch(request)}
        except ConnectionClosed:
            return
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            await connection.send_frame(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        except ConnectionClosed:
            pass  # 客户端断开不撤销已执行的启停操作。
        if response["ok"] and request.get("action") == "prepare_plugin_dependencies":
            self.maintenance.queue_plugin_dependencies()


async def run_host(workspace: Workspace, *, upgrade_confirmed: set[str]) -> bool:
    """持有宿主租约运行，直到收到 Ctrl+C、SIGTERM 或控制通道的 shutdown。"""
    descriptor_path = _descriptor_path(workspace)
    with ProcessLease(workspace.host_directory / "run.lock"):
        supervisor = BotSupervisor(workspace, public_base_url=workspace.config.advertised_base_url())
        supervisor.upgrade_confirmed |= upgrade_confirmed
        control = HostControl(supervisor)
        webui = await asyncio.to_thread(HostWebUI, supervisor, control.maintenance, control.stopping)
        control.webui = webui if workspace.config.webui_enabled else None
        endpoint = allocate_endpoint("control")
        supervisor.control_endpoint = endpoint
        server = create_transport_server(endpoint)
        loop = asyncio.get_running_loop()
        previous_handlers: Dict[int, Any] = {}
        console_task: Optional[asyncio.Task[None]] = None
        try:
            await server.start(control.handle)
            await webui.start()
            await asyncio.to_thread(_publish, descriptor_path, {"version": 1, "endpoint": endpoint})
            signals = [signal.SIGINT, signal.SIGTERM]
            if os.name == "nt":
                signals.append(signal.SIGBREAK)
            for signum in signals:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda _sig, _frame: loop.call_soon_threadsafe(control.stopping.set))
            print(f"MaiBot 宿主已启动：{workspace.root}", flush=True)
            if webui.management and webui.credentials is not None:
                print(f"WebUI：{await asyncio.to_thread(webui.open_url)}（登录链接一分钟内有效）", flush=True)
                if webui.credentials.token_source == "temporary":
                    print(f"WebUI 临时登录密码：{webui.credentials.token}", flush=True)
            print("可用 maibot host status/start/stop/restart 管理 Bot；Ctrl+C 关闭宿主。", flush=True)
            starter = asyncio.create_task(supervisor.start_desired())
            console_task = asyncio.create_task(HostConsole(supervisor, control.stopping).run())
            stop_task = asyncio.create_task(control.stopping.wait())
            assert webui.task is not None
            pending = {stop_task, webui.task, starter, console_task}
            while True:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if starter in done:
                    for failure in starter.result():
                        print(f"Bot 启动失败 {failure}", file=sys.stderr, flush=True)
                if console_task in done:
                    # EOF / exit() 只关闭终端；异常不能成为无人读取的后台任务错误。
                    await console_task
                if stop_task in done or webui.task in done:
                    break
            if webui.task in done:
                await webui.task
                raise RuntimeError("宿主 WebUI 意外退出")
        finally:
            control.stopping.set()
            if console_task is not None:
                console_task.cancel()
                await asyncio.gather(console_task, return_exceptions=True)
            await asyncio.to_thread(descriptor_path.unlink, missing_ok=True)
            await server.stop()
            await control.maintenance.finish()
            try:
                # WebSocket/SSE 可能等到 Bot 关闭才结束，HTTP 与 Bot 同时关停。
                results = await asyncio.gather(supervisor.shutdown(), webui.close(), return_exceptions=True)
                failures = [result for result in results if isinstance(result, BaseException)]
                if failures:
                    if control.maintenance.update_requested:
                        from src.common.self_update import clear_pending_update

                        await asyncio.to_thread(clear_pending_update, control.maintenance.update_root)
                    raise BaseExceptionGroup("宿主关闭未完成", failures)
            finally:
                for signum, previous in previous_handlers.items():
                    signal.signal(signum, previous)
        return control.maintenance.update_requested


async def control_request(workspace_root: Path, action: str, bot_id: Optional[str] = None) -> Any:
    """连接正在运行的宿主；control.json 只提供地址，宿主未运行时地址无法连接。"""
    descriptor_path = workspace_root / "host" / "control.json"
    try:
        descriptor = json.loads(await asyncio.to_thread(descriptor_path.read_text, encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("宿主尚未运行，请先执行 maibot run") from exc
    if not isinstance(descriptor, dict) or descriptor.get("version") != 1 or not isinstance(descriptor.get("endpoint"), str):
        raise ValueError("宿主连接信息格式无效")
    try:
        connection = await asyncio.wait_for(create_transport_client(descriptor["endpoint"]).connect(), timeout=5)
    except (OSError, TimeoutError) as exc:
        raise RuntimeError("无法连接宿主，连接记录可能已过期；请检查宿主是否仍在运行") from exc
    try:
        await connection.send_frame(json.dumps({"action": action, "bot_id": bot_id}).encode("utf-8"))
        timeout = _ACTION_TIMEOUTS.get(action, _CONTROL_TIMEOUT)
        response = json.loads(await asyncio.wait_for(connection.recv_frame(), timeout=timeout))
    finally:
        await connection.close()
    if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
        raise ValueError("宿主返回了无效响应")
    if not response["ok"]:
        raise RuntimeError(response["error"])
    return response["result"]
