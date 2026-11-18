"""ACP v1 的双向 NDJSON 连接，支持子进程 stdio 和 Desktop 本机桥接。"""

from collections import deque
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Awaitable, Callable, Dict, List, Optional

import asyncio
import json
import os
import shutil
import subprocess

from src.common.logger import get_logger

from .config import AgentConfig

logger = get_logger("agent.acp")


def session_cwd(config: AgentConfig, local_cwd: Path) -> str:
    """远端路径不能经过本机的 resolve/expanduser，也不能在本机 mkdir。"""
    remote = config.acp_remote_cwd
    if not remote:
        return str(local_cwd)
    if config.connection == "dsh":
        raise ValueError("ACP 远程会话工作目录仅适用于自定义 ACP 或 Desktop 隧道连接")
    if not (PurePosixPath(remote).is_absolute() or PureWindowsPath(remote).is_absolute()):
        raise ValueError("ACP 远程会话工作目录必须是远端已存在的绝对路径，不能使用 ~ 或相对路径")
    return remote


def desktop_manifest(config: AgentConfig) -> Path:
    return (Path(config.desktop_manifest).expanduser() if config.desktop_manifest
            else Path.home() / ".dsh" / "maibot-desktop.json").absolute()


class ACPRequestError(RuntimeError):
    """对端已返回请求错误，与断线或超时导致的结果未知区分。"""

    def __init__(self, error: Dict[str, Any]) -> None:
        self.error = error
        super().__init__("ACP: " + json.dumps(error, ensure_ascii=False))


class ACPConnection:
    def __init__(self, update: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self.update = update
        self.process: Optional[asyncio.subprocess.Process] = None
        self.stdout: Optional[asyncio.StreamReader] = None
        self.stdin: Optional[asyncio.StreamWriter] = None
        self._reader: Optional[asyncio.Task[None]] = None
        self._stderr: Optional[asyncio.Task[None]] = None
        self._errors = deque(maxlen=20)
        self._pending: Dict[int, asyncio.Future[Any]] = {}
        self._sequence = 0
        self._write_lock = asyncio.Lock()
        self.failure: Optional[Exception] = None

    async def initialize(
        self, config: AgentConfig, cwd: Path, client_name: str, *, mcp_port: Optional[int] = None,
    ) -> Dict[str, Any]:
        """两种 ACP 使用方共用连接选择和握手，业务状态由各自保存。"""
        if config.connection == "dsh_desktop":
            await self.connect_desktop(desktop_manifest(config), config.acp_timeout)
        else:
            args = ["--profile", "acp"] if config.connection == "dsh" else config.acp_args
            if any("{mcp_port}" in arg for arg in args):
                if mcp_port is None:
                    raise ValueError("ACP 启动参数使用了 {mcp_port}，但工具服务尚未启动")
                # 仅替换已知占位符，保留远端命令中其他 JSON/shell 花括号。
                args = [arg.replace("{mcp_port}", str(mcp_port)) for arg in args]
            await self.start(config.acp_command, args, str(cwd), config.acp_env)
        initialized = await self.request("initialize", {
            "protocolVersion": 1, "clientInfo": {"name": client_name, "version": "1"},
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        }, config.acp_timeout)
        if initialized["protocolVersion"] != 1:
            raise ValueError("外部 Agent 未协商 ACP v1")
        capabilities = initialized["agentCapabilities"]
        if not capabilities.get("mcpCapabilities", {}).get("http", False):
            raise ValueError("外部 Agent 未声明 HTTP MCP 能力，无法挂载麦麦工具")
        return capabilities

    async def open_session(
        self, cwd: str, mount: Dict[str, Any], session_id: Optional[str], timeout: float,
    ) -> Dict[str, Any]:
        params = {"cwd": str(cwd), "mcpServers": [mount]}
        if session_id:
            params["sessionId"] = session_id
        return await self.request("session/resume" if session_id else "session/new", params, timeout)

    async def select_model(
        self, config: AgentConfig, session: Dict[str, Any], session_id: str,
    ) -> None:
        if not config.acp_model:
            return
        if not any(option["id"] == "model" for option in session.get("configOptions", [])):
            raise ValueError("外部 Agent 未提供 model 配置选项")
        await self.request("session/set_config_option", {
            "sessionId": session_id, "configId": "model", "value": config.acp_model,
        }, config.acp_timeout)

    async def start(self, command: str, args: List[str], cwd: str, env: Dict[str, str]) -> None:
        process_env = {**os.environ, **env}
        executable = await asyncio.to_thread(shutil.which, command, path=process_env.get("PATH"))
        if executable is None:
            raise FileNotFoundError(f"找不到 ACP 启动程序：{command}")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        self.process = await asyncio.create_subprocess_exec(
            executable, *args, cwd=cwd, env=process_env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024, **options,
        )
        self.stdout, self.stdin = self.process.stdout, self.process.stdin
        self._reader = asyncio.create_task(self._read(), name="agent.acp.read")
        self._stderr = asyncio.create_task(self._read_errors(), name="agent.acp.stderr")

    async def connect_desktop(self, manifest: Path, timeout: float) -> None:
        def read_manifest() -> Dict[str, Any]:
            if not manifest.is_file():
                raise FileNotFoundError("未找到 dsh Desktop 桥接信息；请安装 dsh-maibot-bridge 插件并启动 Desktop。普通 dsh CLI 无需桥接插件，可选择 dsh CLI 连接；详见 docs/agent-connections.md。")
            return json.loads(manifest.read_text(encoding="utf-8"))

        info = await asyncio.to_thread(read_manifest)
        if info["version"] != 1 or info["host"] != "127.0.0.1":
            raise ValueError("Desktop 桥接只接受本机的 v1 连接信息。")
        if not isinstance(info["port"], int) or not 1 <= info["port"] <= 65535 or not isinstance(info["token"], str) or not info["token"]:
            raise ValueError("无效的 Desktop 桥接端口或认证信息。")
        async with asyncio.timeout(timeout):
            self.stdout, self.stdin = await asyncio.open_connection(info["host"], info["port"], limit=64 * 1024 * 1024)
            try:
                self.stdin.write((json.dumps({"token": info["token"]}) + "\n").encode())
                await self.stdin.drain()
                if json.loads(await self.stdout.readline()) != {"authenticated": True}:
                    raise PermissionError("dsh Desktop 桥接认证失败。")
            except BaseException:
                self.stdin.close()
                await self.stdin.wait_closed()
                raise
        self._reader = asyncio.create_task(self._read(), name="agent.desktop-acp.read")

    async def send(self, message: Dict[str, Any]) -> None:
        if self.failure is not None:
            raise self.failure
        if self.stdin is None or self.stdin.is_closing():
            raise ConnectionError("ACP 连接未运行")
        async with self._write_lock:
            self.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            await self.stdin.drain()

    async def notify(self, method: str, params: Dict[str, Any]) -> None:
        await self.send({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method: str, params: Dict[str, Any], timeout: Optional[float] = None) -> Any:
        self._sequence += 1
        request_id = self._sequence
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout)
            except asyncio.TimeoutError:
                details = "\n外部进程 stderr：\n" + "\n".join(self._errors) if self._errors else ""
                raise TimeoutError(f"ACP {method} 超时（{timeout} 秒），请求未重试{details}") from None
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _read_errors(self) -> None:
        while line := await self.process.stderr.readline():
            text = line.decode("utf-8", errors="replace").rstrip()
            self._errors.append(text)
            logger.info("ACP stderr: %s", text)

    async def _read(self) -> None:
        try:
            while line := await self.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError("ACP stdout 包含非协议输出，请使用 ACP/stdio 模式并将日志写入 stderr") from exc
                if message.get("jsonrpc") != "2.0":
                    raise ValueError("ACP stdout 必须仅包含 JSON-RPC 2.0 消息")
                if "method" in message:
                    if message["method"] == "session/update":
                        await self.update(message["params"])
                    elif "id" in message:
                        await self._server_request(message)
                elif message.get("id") in self._pending:
                    future = self._pending[message["id"]]
                    if not future.done():
                        if "error" in message:
                            future.set_exception(ACPRequestError(message["error"]))
                        else:
                            future.set_result(message["result"])
            raise ConnectionError("ACP 连接已关闭\n" + "\n".join(self._errors))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failure = exc
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(self.failure or ConnectionError("ACP 连接已关闭"))

    async def _server_request(self, message: Dict[str, Any]) -> None:
        response = {"jsonrpc": "2.0", "id": message["id"]}
        if message["method"] == "session/request_permission":
            # 电脑工作区已由用户启用自主执行；只授权这一次，不创建外部持久授权。
            option = next((option for option in message["params"]["options"] if option["kind"] == "allow_once"), None)
            response["result"] = {"outcome": {"outcome": "selected", "optionId": option["optionId"]}
                                  if option is not None else {"outcome": "cancelled"}}
        else:
            response["error"] = {"code": -32601, "message": "客户端未提供此能力：" + message["method"]}
        await self.send(response)

    async def close(self, timeout: float) -> None:
        if self.stdin is not None:
            self.stdin.close()
            if self.process is None:
                await self.stdin.wait_closed()
        if self.process is not None and self.process.returncode is None:
            try:
                await asyncio.wait_for(self.process.wait(), timeout)
            except asyncio.TimeoutError:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(self.process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await killer.wait()
                else:
                    import signal
                    os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        for task in (self._reader, self._stderr):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
