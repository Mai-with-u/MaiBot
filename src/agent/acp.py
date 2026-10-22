"""ACP v1 的双向 NDJSON 连接，支持子进程 stdio 和 Desktop 本机桥接。"""

from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import asyncio
import json
import os
import shutil
import subprocess


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

    async def start(self, command: str, args: List[str], cwd: str, env: Dict[str, str]) -> None:
        executable = shutil.which(command)
        if executable is None:
            raise FileNotFoundError(f"找不到 ACP 启动程序：{command}")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        self.process = await asyncio.create_subprocess_exec(
            executable, *args, cwd=cwd, env={**os.environ, **env}, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024, **options,
        )
        self.stdout, self.stdin = self.process.stdout, self.process.stdin
        self._reader = asyncio.create_task(self._read(), name="agent.acp.read")
        self._stderr = asyncio.create_task(self._read_errors(), name="agent.acp.stderr")

    async def connect_desktop(self, manifest: Path, timeout: float) -> None:
        def read_manifest() -> Dict[str, Any]:
            if not manifest.is_file():
                raise FileNotFoundError("未找到 dsh Desktop 桥接信息；请安装 dsh-maibot-bridge 插件并启动 Desktop。")
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
                raise TimeoutError(f"ACP {method} 超时（{timeout} 秒），请求未重试") from None
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _read_errors(self) -> None:
        while line := await self.process.stderr.readline():
            self._errors.append(line.decode("utf-8", errors="replace").rstrip())

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
                            future.set_exception(RuntimeError("ACP: " + json.dumps(message["error"], ensure_ascii=False)))
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
