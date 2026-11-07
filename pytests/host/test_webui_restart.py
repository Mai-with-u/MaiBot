"""使用真实 HTTP 连接验证宿主端口在关闭后可重用，监听期间仍独占。"""

from pathlib import Path

from aiohttp import ClientSession

import asyncio
import errno
import socket

import pytest

from src.host.config import HostConfig
from src.host.layout import Workspace
from src.host.maintenance import HostMaintenance
from src.host.registry import Registry
from src.host.supervisor import BotSupervisor
from src.host.webui import HostWebUI


def make_webui(root: Path, host: str, port: int = 0) -> HostWebUI:
    registry = Registry(root)
    registry.create()
    config = HostConfig(hosts=[host], port=port, anti_crawler_mode="false")
    workspace = Workspace(root=root, registry=registry, config=config)
    supervisor = BotSupervisor(workspace, public_base_url=config.local_base_url())
    return HostWebUI(supervisor, HostMaintenance(supervisor), asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
async def test_restart_with_unfinished_http_connection(tmp_path: Path, host: str) -> None:
    if host == "::1" and not socket.has_ipv6:
        pytest.skip("系统不支持 IPv6")
    webui = make_webui(tmp_path / "first", host)
    await webui.start()
    port = webui._sockets[0].getsockname()[1]
    restarted = make_webui(tmp_path / "second", host, port)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    loop = asyncio.get_running_loop()
    try:
        with socket.socket(family, socket.SOCK_STREAM) as client:
            client.setblocking(False)
            async with asyncio.timeout(5):
                address = (host, port, 0, 0) if family == socket.AF_INET6 else (host, port)
                await loop.sock_connect(client, address)
                await loop.sock_sendall(
                    client, b"GET /api/host/mode HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n",
                )
                response = b""
                while b"\r\n\r\n" not in response:
                    chunk = await loop.sock_recv(client, 4096)
                    assert chunk, "宿主未返回完整 HTTP 响应头"
                    response += chunk
                assert response.startswith(b"HTTP/1.1 200 OK")
                await webui.close()
                while await loop.sock_recv(client, 4096):
                    pass
                # 故意不关闭客户端的写端：服务端已经停止监听，但旧连接仍在 FIN-WAIT-2。
                await restarted.start()
                authority = f"[{host}]" if ":" in host else host
                async with ClientSession() as session:
                    async with session.get(f"http://{authority}:{port}/api/host/mode") as result:
                        assert result.status == 200
                        assert (await result.json())["host"] is True
    finally:
        await restarted.close()
        await webui.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
async def test_restart_does_not_steal_listening_port(tmp_path: Path, host: str) -> None:
    if host == "::1" and not socket.has_ipv6:
        pytest.skip("系统不支持 IPv6")
    webui = make_webui(tmp_path / "first", host)
    await webui.start()
    port = webui._sockets[0].getsockname()[1]
    second = make_webui(tmp_path / "second", host, port)
    try:
        with pytest.raises(OSError) as error:
            await second.start()
        assert error.value.errno in {errno.EADDRINUSE, 10048}
        assert all(sock.fileno() == -1 for sock in second._sockets)
    finally:
        await second.close()
        await webui.close()
