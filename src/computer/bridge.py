"""沙盒生命周期和文件协议；不执行宿主机上的模型命令。"""

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring
import asyncio
import json
import os
import re
import shutil
import subprocess
import time

from src.common.async_work import run_sync_work

from src.agent.config import AgentConfig
from .actions import validate_actions
from src.agent.persistence import read_json, write_json


def sandbox_xml(bootstrap: Path, exchange: Path, workspace: Path, config: AgentConfig) -> str:
    root = Element("Configuration")
    for name, value in {
        "Networking": "Enable" if config.networking else "Disable", "ClipboardRedirection": "Disable",
        "AudioInput": "Disable", "VideoInput": "Disable", "PrinterRedirection": "Disable",
        "MemoryInMB": str(config.memory_mb),
    }.items():
        SubElement(root, name).text = value
    folders = SubElement(root, "MappedFolders")
    for source, destination, readonly in (
        (bootstrap, r"C:\MaiBootstrap", True), (exchange, r"C:\MaiExchange", False),
        (workspace, r"C:\MaiWorkspace", False),
    ):
        folder = SubElement(folders, "MappedFolder")
        SubElement(folder, "HostFolder").text = str(source.resolve())
        SubElement(folder, "SandboxFolder").text = destination
        SubElement(folder, "ReadOnly").text = str(readonly).lower()
    SubElement(SubElement(root, "LogonCommand"), "Command").text = (
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\\MaiBootstrap\\guest.ps1"'
    )
    return tostring(root, encoding="unicode")


class SandboxBridge:
    def __init__(self, runtime_dir: Path, workspace: Path, config: AgentConfig) -> None:
        self.runtime_dir = runtime_dir
        self.workspace = workspace
        self.config = config
        self._viewer: Optional[asyncio.subprocess.Process] = None
        self.sandbox_id: Optional[str] = None
        self.cli = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "wsb.exe"
        self.exchange: Optional[Path] = None
        self.last_frame: Optional[Dict[str, Any]] = None
        self.pending: Optional[str] = None
        self.last_operation: Optional[str] = None
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._closing = False
        self.connected = False
        self.observed_running = False
        self.state_file = runtime_dir / "session.json"

    async def _save(self) -> None:
        async with self._state_lock:
            if self.sandbox_id is not None and self.exchange is not None:
                await run_sync_work(write_json, self.state_file, self._session_state())

    def _session_state(self) -> Dict[str, Any]:
        return {"sandbox_id": self.sandbox_id, "exchange": str(self.exchange.resolve()),
                "pending": self.pending, "last_operation": self.last_operation}

    async def _publish(self, request: Dict[str, Any], channel: Literal["request", "observe"]) -> None:
        async with self._state_lock:
            exchange = self.exchange
            state = self._session_state() if channel == "request" else None

            def publish() -> None:
                # 提交与发布是同一个不可中途取消的持久步骤，避免留下永远等不到结果的 pending。
                write_json(exchange / f"{request['id']}.submitted.json", request)
                if state is not None:
                    write_json(self.state_file, state)
                write_json(exchange / f"{request['id']}.{channel}.json", request)

            await run_sync_work(publish)

    async def restore(self) -> bool:
        """仅恢复明确记录为本体创建的实例，不扫描或接管其他沙盒。"""
        if not await asyncio.to_thread(self.state_file.exists):
            return False
        state = await asyncio.to_thread(read_json, self.state_file)
        exchange = Path(state["exchange"]).resolve()
        if not exchange.is_relative_to(self.runtime_dir.resolve()):
            raise ValueError("记录的沙盒通信目录不属于本实例")
        self.sandbox_id, self.exchange, self.pending = state["sandbox_id"], exchange, state["pending"]
        self.last_operation = state.get("last_operation")
        environments = await self._command("list")
        if self.sandbox_id not in [item["Id"] for item in environments["WindowsSandboxEnvironments"]]:
            self.connected = False
            self.observed_running = False
            raise ConnectionError("记录的沙盒已关闭；在 WebUI 重建沙盒 创建新桌面，工作目录仍保留。")
        ready = await self._wait_json(exchange / "ready.json", self.config.startup_timeout)
        if ready.get("protocol") != 2:
            raise ConnectionError("原沙盒代理版本不支持恢复，请在 WebUI 重建沙盒 更新代理。")
        if self.pending is not None:
            claimed = [exchange / f"{self.pending}.{suffix}.json" for suffix in ("request", "running", "result")]
            if not any(await asyncio.gather(*(asyncio.to_thread(path.exists) for path in claimed))):
                # 宿主进程可能在持久提交与发布请求之间退出；没有认领记录才能继续发布，绝不重放 running。
                submitted = await asyncio.to_thread(read_json, exchange / f"{self.pending}.submitted.json")
                await asyncio.to_thread(write_json, exchange / f"{self.pending}.request.json", submitted)
        await self._connect()
        self.connected = True
        self.observed_running = True
        if self.last_operation is not None:
            self.last_frame = await asyncio.to_thread(read_json, exchange / f"{self.last_operation}.result.json")
        return True

    async def _connect(self) -> None:
        if self.sandbox_id is None:
            raise ConnectionError("没有可连接的沙盒实例")
        instance = self.sandbox_id.replace("'", "''")
        # connect 每次都会创建一个查看窗口；重启恢复时优先复用已有窗口。
        probe = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "@(Get-CimInstance Win32_Process -Filter \"Name='WindowsSandboxRemoteSession.exe'\" | "
            f"Where-Object {{ $_.CommandLine.Contains('{instance}') }}).Count",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            output, error = await asyncio.wait_for(probe.communicate(), self.config.startup_timeout)
        finally:
            if probe.returncode is None:
                probe.kill()
                await probe.wait()
        if probe.returncode != 0:
            raise OSError(error.decode(errors="replace"))
        if int(output.strip()) == 0:
            self._viewer = await asyncio.create_subprocess_exec(str(self.cli), "connect", "--id", self.sandbox_id,
                                                                creationflags=subprocess.CREATE_NO_WINDOW)

    @property
    def running(self) -> bool:
        return self.sandbox_id is not None and self.observed_running

    async def _command(self, *arguments: str) -> Dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            str(self.cli), *arguments, "--raw", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), self.config.startup_timeout)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0:
            raise OSError((output + error).decode("utf-8", errors="replace"))
        return json.loads(output.decode("utf-8-sig")) if output.strip() else {}

    async def _wait_json(self, path: Path, timeout: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        next_probe = 0.0
        while timeout == 0 or time.monotonic() < deadline:
            if self._closing:
                raise ConnectionError("沙盒正在关闭")
            if await asyncio.to_thread(path.exists):
                return await asyncio.to_thread(read_json, path)
            if self.sandbox_id is not None and time.monotonic() >= next_probe:
                environments = await self._command("list")
                if self.sandbox_id not in [item["Id"] for item in environments["WindowsSandboxEnvironments"]]:
                    self.observed_running = False
                    self.connected = False
                    raise ConnectionError("Windows Sandbox 实例已关闭")
                self.observed_running = True
                next_probe = time.monotonic() + 2
            agent_error = path.parent / "agent-error.txt"
            if await asyncio.to_thread(agent_error.exists):
                raise ConnectionError(await asyncio.to_thread(agent_error.read_text, encoding="utf-8-sig"))
            heartbeat = path.parent / "heartbeat"
            if await asyncio.to_thread(heartbeat.exists):
                heartbeat_time = (await asyncio.to_thread(heartbeat.stat)).st_mtime
                if time.time() - heartbeat_time > 30:
                    self.connected = False
                    raise ConnectionError("沙盒实例仍存在，但来宾代理心跳已中断；原请求未重放，请检查桌面或重建代理。")
            await asyncio.sleep(0.1)
        raise TimeoutError("沙盒请求仍未返回，未重放操作；使用 computer_poll(operation_id) 查询原操作结果。")

    async def _start(self) -> None:
        async with self._start_lock:
            await self._start_owned()

    async def _start_owned(self) -> None:
        if self._closing:
            raise ConnectionError("沙盒正在关闭")
        if self.sandbox_id is not None:
            if not self.connected:
                await self.restore()
            return
        if await self.restore():
            return
        if os.name != "nt" or not await asyncio.to_thread(self.cli.is_file):
            raise ConnectionError("需要支持 wsb.exe 实例管理的新版 Windows Sandbox；请更新 Windows 并启用沙盒功能。")
        environments = await self._command("list")
        if environments["WindowsSandboxEnvironments"]:
            raise ConnectionError("已有其他 Windows Sandbox 在运行，请先关闭；不会接管其他桌面。")
        session = self.runtime_dir / uuid4().hex
        bootstrap, exchange = session / "bootstrap", session / "exchange"

        def prepare() -> Path:
            bootstrap.mkdir(parents=True)
            exchange.mkdir()
            self.workspace.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(__file__).with_name("guest.ps1"), bootstrap / "guest.ps1")
            (bootstrap / "settings.json").write_text(
                json.dumps({"screenshot_max_edge": self.config.screenshot_max_edge}), encoding="utf-8",
            )
            launch = session / "maimai.wsb"
            launch.write_text(sandbox_xml(bootstrap, exchange, self.workspace, self.config), encoding="utf-8")
            return launch

        launch = await asyncio.to_thread(prepare)
        self.exchange = exchange
        # 只使用持久实例 ID 管理沙盒，查看窗口进程不再承担虚拟机生命周期。
        self.sandbox_id = str(uuid4())
        await self._save()
        start = asyncio.create_task(self._command("start", "--id", self.sandbox_id, "--config",
                                                await asyncio.to_thread(launch.read_text, encoding="utf-8")))
        try:
            await asyncio.shield(start)
            await self._connect()
            ready = await self._wait_json(exchange / "ready.json", self.config.startup_timeout)
            if not ready["success"]:
                raise ConnectionError(ready["error"])
            self.connected = True
            self.observed_running = True
        except BaseException:
            try:
                await start
            finally:
                await self._close()
            raise

    async def perform(self, actions: List[Dict[str, Any]], timeout: Optional[float] = None) -> Dict[str, Any]:
        if not actions:
            raise ValueError("actions 不能为空；观察桌面请使用 computer_observe，查询操作请使用 computer_poll。")
        validate_actions(actions)
        async with self._lock:
            await self._start()
            if self.exchange is None:
                raise ConnectionError("沙盒交换目录未初始化")
            if self.pending is not None:
                raise RuntimeError(f"上一条操作尚未领取结果；请调用 computer_poll(operation_id={self.pending})。")
            self.pending = uuid4().hex
            await self._publish({"id": self.pending, "actions": actions}, "request")
            return await self._collect(self.pending, timeout)

    async def _collect(self, operation_id: str, timeout: Optional[float]) -> Dict[str, Any]:
        if self.exchange is None:
            raise ConnectionError("沙盒交换目录未初始化")
        try:
            result = await self._wait_json(self.exchange / f"{operation_id}.result.json",
                                           self.config.request_timeout if timeout is None else timeout)
        except TimeoutError:
            return {"success": True, "status": "running", "operation_id": operation_id,
                    "message": "原操作仍在执行，使用 computer_poll 查询；没有重放输入。"}
        if result["id"] != operation_id:
            raise ConnectionError("沙盒响应 ID 不匹配")
        if self.pending == operation_id:
            self.pending = None
            await self._save()
        # 结果保留在本次实例目录，重复查询同一 ID 始终返回同一结果。
        result["operation_id"] = operation_id
        result["status"] = result.get("status", "completed" if result["success"] else "failed")
        if "image" in result:
            self.last_frame = result
            self.last_operation = operation_id
            await self._save()
        return result

    async def poll(self, operation_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
            raise ValueError("无效的操作 ID")
        await self._start()
        if self.exchange is None:
            raise ConnectionError("沙盒交换目录未初始化")
        if not await asyncio.to_thread((self.exchange / f"{operation_id}.submitted.json").exists):
            raise ValueError("该操作不属于当前沙盒实例")
        return await self._collect(operation_id, timeout)

    async def observe(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        await self._start()
        if self.exchange is None:
            raise ConnectionError("沙盒交换目录未初始化")
        operation_id = uuid4().hex
        request = {"id": operation_id, "actions": [{"action": "observe"}]}
        await self._publish(request, "observe")
        return await self._collect(operation_id, timeout)

    async def cancel(self, operation_id: str) -> Dict[str, Any]:
        if operation_id != self.pending:
            return await self.poll(operation_id, 0.01)
        if self.exchange is None:
            raise ConnectionError("沙盒交换目录未初始化")
        await asyncio.to_thread((self.exchange / f"{operation_id}.cancel").touch)
        return await self.poll(operation_id, 5)

    async def detach(self) -> None:
        """麦麦退出仅断开执行器，保留桌面、后台程序和原请求，下一次启动恢复。"""
        self._closing = True
        try:
            async with self._lock:
                await self._save()
                self.connected = False
                self._viewer = None
        finally:
            self._closing = False

    async def _close(self) -> None:
        if self.sandbox_id is None and await asyncio.to_thread(self.state_file.exists):
            state = await asyncio.to_thread(read_json, self.state_file)
            self.sandbox_id = state["sandbox_id"]
        if self.sandbox_id is not None:
            environments = await self._command("list")
            if self.sandbox_id in [item["Id"] for item in environments["WindowsSandboxEnvironments"]]:
                await self._command("stop", "--id", self.sandbox_id)
            self.sandbox_id = None
        if self._viewer is not None and self._viewer.returncode is None:
            try:
                self._viewer.terminate()
            except ProcessLookupError:
                pass
            await self._viewer.wait()
        self._viewer = None
        self.exchange = None
        self.pending = None
        self.last_frame = None
        self.last_operation = None
        self.connected = False
        self.observed_running = False
        await asyncio.to_thread(self.state_file.unlink, missing_ok=True)

    async def close(self) -> None:
        # 先发布关闭状态，使等待中的调用及时释放操作锁，再关闭自己拥有的实例。
        self._closing = True
        try:
            # 等待请求看到 closing 后释放操作锁，再停止指定实例。
            async with self._lock:
                await self._close()
        finally:
            self._closing = False
