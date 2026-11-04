"""本机进程间通道：Windows 使用 Named Pipe，其他平台使用 Unix Domain Socket。

宿主为每次运行分配随机地址；地址只在本机可达，并且路径本身绑定到一次运行，
因此不需要端口分配，也不会因端口复用把请求送到另一个 Bot。
"""

from pathlib import Path
from typing import Any, List

import asyncio
import os
import secrets
import stat

import aiohttp
import uvicorn


_PIPE_PREFIX = "\\\\.\\pipe\\"
# sun_path 最长 104 字节（macOS）；固定短目录保证随机文件名不会超限。
_SOCKET_DIRECTORY = Path("/tmp")


def _socket_directory() -> Path:
    directory = _SOCKET_DIRECTORY / f"maibot-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    # 目录可能被其他用户预先创建；只接受当前用户独占的真实目录。
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError(f"进程间通信目录不安全: {directory}")
    return directory


def allocate_endpoint(purpose: str) -> str:
    """返回一个尚未使用的随机地址；由服务端在启动时创建。"""
    name = f"maibot-{purpose}-{secrets.token_hex(12)}"
    if os.name == "nt":
        return _PIPE_PREFIX + name
    return str(_socket_directory() / f"{name}.sock")


def release_endpoint(endpoint: str) -> None:
    """删除 Unix Socket 文件；Named Pipe 随最后一个句柄关闭而消失。"""
    if os.name != "nt":
        Path(endpoint).unlink(missing_ok=True)


def http_connector(endpoint: str) -> aiohttp.BaseConnector:
    if os.name == "nt":
        return aiohttp.NamedPipeConnector(endpoint)
    return aiohttp.UnixConnector(endpoint)


class _PipeListener:
    """给 Named Pipe 服务端补上 Uvicorn 关闭流程需要的 wait_closed。"""

    def __init__(self, servers: List[Any]) -> None:
        self._servers = servers

    def close(self) -> None:
        for server in self._servers:
            server.close()

    async def wait_closed(self) -> None:
        return None


class IpcHTTPServer(uvicorn.Server):
    """只在进程间通道上提供 HTTP / WebSocket；其余生命周期沿用 Uvicorn。"""

    def __init__(self, config: uvicorn.Config, endpoint: str) -> None:
        super().__init__(config)
        self.endpoint = endpoint

    async def startup(self, sockets: Any = None) -> None:
        await self.lifespan.startup()
        if self.lifespan.should_exit:
            self.should_exit = True
            return
        config = self.config

        def create_protocol() -> asyncio.Protocol:
            return config.http_protocol_class(  # type: ignore[call-arg]
                config=config, server_state=self.server_state, app_state=self.lifespan.state,
            )

        loop = asyncio.get_running_loop()
        if os.name == "nt":
            self.servers = [_PipeListener(await loop.start_serving_pipe(create_protocol, self.endpoint))]  # type: ignore[attr-defined, list-item]
        else:
            self.servers = [await loop.create_unix_server(create_protocol, path=self.endpoint)]
            os.chmod(self.endpoint, 0o600)
        self.started = True
