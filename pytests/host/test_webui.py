"""宿主对外 HTTP：登录、按 Bot 代理、文件传输、防爬虫与静态资源；Bot 在进程内经真实通道提供。"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

import asyncio
import json
import socket

from aiohttp import ClientSession, CookieJar, WSMsgType
from fastapi import FastAPI, Request, WebSocket
import pytest
import uvicorn

from src.common.ipc import IpcHTTPServer, allocate_endpoint
from src.host import webui as webui_module
from src.host.config import HostConfig
from src.host.control import HostControl, control_request
from src.host.layout import Workspace
from src.host.maintenance import HostMaintenance
from src.host.registry import Registry
from src.host.supervisor import BotSupervisor
from src.host.webui import HostWebUI
from src.plugin_runtime.transport.factory import create_transport_server


def bot_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/webui/echo")
    async def echo(request: Request) -> dict:
        return {"cookie": request.headers.get("cookie"), "query": request.url.query}

    @app.get("/api/files/transfers/{token}")
    async def transfer(token: str) -> dict:
        return {"token": token}

    @app.websocket("/api/webui/ws")
    async def websocket(socket_: WebSocket) -> None:
        await socket_.accept()
        await socket_.send_text("echo:" + await socket_.receive_text())
        await socket_.close()

    return app


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@asynccontextmanager
async def running_host(
    tmp_path: Path, *, config: Optional[HostConfig] = None, static_path: Optional[Path] = None,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[HostWebUI, str, str]]:
    registry = Registry(tmp_path)
    registry.create()
    bot_directory = tmp_path / "bots" / "bot"
    bot_directory.mkdir(parents=True)
    bot = registry.add_bot("bot", bot_directory, owned=True, initialization="initialized")
    config = config or HostConfig(hosts=["127.0.0.1"], port=free_port())
    workspace = Workspace(root=tmp_path, registry=registry, config=config)
    supervisor = BotSupervisor(workspace, public_base_url=config.local_base_url())
    endpoint = allocate_endpoint("test")
    monkeypatch.setattr(supervisor, "proxy_endpoint", lambda bot_id: endpoint)
    monkeypatch.setattr(webui_module, "resolve_static_path", lambda: static_path)
    server = IpcHTTPServer(uvicorn.Config(bot_app(), log_config=None, lifespan="off"), endpoint)
    server_task = asyncio.create_task(server.serve())
    webui = HostWebUI(supervisor, HostMaintenance(supervisor), asyncio.Event())
    await webui.start()
    try:
        yield webui, bot.bot_id, config.local_base_url()
    finally:
        await webui.close()
        server.should_exit = True
        await server_task


async def login(client: ClientSession, base: str, webui: HostWebUI) -> None:
    async with client.post(f"{base}/api/webui/auth/verify", json={"token": webui.credentials.token}) as response:
        assert (await response.json())["valid"]


@pytest.mark.asyncio
async def test_proxy_requires_host_login_and_never_forwards_host_cookie(tmp_path: Path, monkeypatch) -> None:
    async with running_host(tmp_path, monkeypatch=monkeypatch) as (webui, bot_id, base):
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
            url = f"{base}/api/host/bots/{bot_id}/proxy/api/webui/echo?x=1"
            async with client.get(url) as response:
                assert response.status == 401
            await login(client, base, webui)
            async with client.get(url) as response:
                assert response.status == 200
                assert await response.json() == {"cookie": None, "query": "x=1"}
            async with client.get(f"{base}/api/host/bots/{bot_id}/proxy/api/webui/auth/check") as response:
                assert response.status == 404
            async with client.ws_connect(f"{base}/api/host/bots/{bot_id}/proxy/api/webui/ws") as websocket:
                await websocket.send_str("hi")
                message = await websocket.receive()
                assert message.type == WSMsgType.TEXT and message.data == "echo:hi"


@pytest.mark.asyncio
async def test_pet_login_behind_proxy_that_strips_port(tmp_path: Path, monkeypatch) -> None:
    origin = "https://pet.example:8446"
    headers = {"Host": "pet.example", "Origin": origin}
    async with running_host(tmp_path, monkeypatch=monkeypatch) as (webui, bot_id, base):
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
            login_url = base + "/api/webui/auth/verify"
            body = {"token": webui.credentials.token}
            for site in ("same-site", "cross-site"):
                async with client.post(login_url, headers={**headers, "Sec-Fetch-Site": site}, json=body) as response:
                    assert response.status == 403
            async with client.post(login_url, headers={**headers, "Sec-Fetch-Site": "same-origin"}, json=body) as response:
                assert (await response.json())["valid"]
            async with client.get(base + "/api/host/desktop-pet/session", headers={"Host": "pet.example"}) as response:
                assert "wss://pet.example:8446" in response.headers["Content-Security-Policy"]
            proxy = f"{base}/api/host/bots/{bot_id}/proxy/api/webui"
            for other in ("https://pet.example:8447", "https://other.example:8446", "https://pet.example"):
                async with client.get(proxy + "/echo", headers={**headers, "Origin": other}) as response:
                    assert response.status == 403
            async with client.ws_connect(proxy + "/ws", headers=headers) as ws:
                await ws.send_str("ping")
                assert (await ws.receive()).data == "echo:ping"


@pytest.mark.asyncio
async def test_file_transfer_needs_no_login(tmp_path: Path, monkeypatch) -> None:
    async with running_host(tmp_path, monkeypatch=monkeypatch) as (_webui, bot_id, base):
        async with ClientSession() as client:
            async with client.get(f"{base}/api/files/bots/{bot_id}/transfers/abc_123") as response:
                assert await response.json() == {"token": "abc_123"}


@pytest.mark.asyncio
async def test_disabled_webui_keeps_only_file_transfers(tmp_path: Path, monkeypatch) -> None:
    config = HostConfig(webui_enabled=False, hosts=["127.0.0.1"], port=free_port())
    async with running_host(tmp_path, config=config, monkeypatch=monkeypatch) as (_webui, bot_id, base):
        async with ClientSession() as client:
            async with client.get(f"{base}/api/host/mode") as response:
                assert response.status == 404
            async with client.get(f"{base}/api/files/bots/{bot_id}/transfers/t") as response:
                assert response.status == 200


@pytest.mark.asyncio
async def test_strict_anti_crawler_blocks_crawlers_but_not_logged_in_users(tmp_path: Path, monkeypatch) -> None:
    config = HostConfig(hosts=["127.0.0.1"], port=free_port(), anti_crawler_mode="strict", allowed_ips="")
    async with running_host(tmp_path, config=config, monkeypatch=monkeypatch) as (webui, _bot_id, base):
        crawler = {"User-Agent": "Googlebot/2.1"}
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
            async with client.get(f"{base}/api/host/mode", headers=crawler) as response:
                assert response.status == 403
            async with client.get(f"{base}/robots.txt") as response:
                assert "Disallow: /" in await response.text()
            await login(client, base, webui)
            async with client.get(f"{base}/api/host/mode", headers=crawler) as response:
                assert response.status == 200


@pytest.mark.asyncio
async def test_control_channel_reaches_running_host(tmp_path: Path) -> None:
    registry = Registry(tmp_path)
    registry.create()
    workspace = Workspace(root=tmp_path, registry=registry, config=HostConfig())
    control = HostControl(BotSupervisor(workspace, public_base_url="http://127.0.0.1:8001"))
    endpoint = allocate_endpoint("control")
    server = create_transport_server(endpoint)
    await server.start(control.handle)
    (tmp_path / "host" / "control.json").write_text(json.dumps({"version": 1, "endpoint": endpoint}), encoding="utf-8")
    try:
        assert await control_request(tmp_path, "status") == []
        with pytest.raises(RuntimeError, match="未知宿主操作"):
            await control_request(tmp_path, "explode")
        assert await control_request(tmp_path, "shutdown") == {"status": "stopping"}
        assert control.stopping.is_set()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_host_management_routes_require_auth_and_return_update_ack(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    prepare = AsyncMock(return_value={'status': 'restarting', 'target': 'a' * 40})
    monkeypatch.setattr(HostMaintenance, 'prepare_update', prepare)
    async with running_host(tmp_path, monkeypatch=monkeypatch) as (webui, bot_id, base):
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
            headers = {'X-MaiBot-Host': '1'}
            async with client.patch(f'{base}/api/host/bots/{bot_id}', headers=headers, json={'name': 'renamed'}) as response:
                assert response.status == 401
            await login(client, base, webui)
            async with client.patch(f'{base}/api/host/bots/{bot_id}', headers=headers, json={'name': 'renamed'}) as response:
                assert response.status == 200
                assert (await response.json())['name'] == 'renamed'
            async with client.post(f'{base}/api/host/maintenance/update', headers=headers, json={'target': 'a' * 40}) as response:
                assert response.status == 200
                assert (await response.json())['status'] == 'restarting'
            prepare.assert_awaited_once_with('a' * 40)


@pytest.mark.asyncio
async def test_host_revocation_blocks_http_and_existing_websocket_without_bot_tokens(tmp_path, monkeypatch):
    async with running_host(tmp_path, monkeypatch=monkeypatch) as (webui, bot_id, base):
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
            prefix = f'{base}/api/host/bots/{bot_id}/proxy'
            # 请求在宿主边界被拒绝，不能到达 Bot 的模型、文件或插件路由。
            for path in ('api/webui/models/probe-thinking', 'api/webui/files', 'api/webui/plugins/runtime/webui'):
                async with client.get(f'{prefix}/{path}') as response:
                    assert response.status == 401
            async with client.ws_connect(f'{prefix}/api/webui/ws') as rejected:
                await rejected.receive(timeout=3)
                assert rejected.close_code == 4001
            await login(client, base, webui)
            async with client.ws_connect(f'{prefix}/api/webui/ws') as active:
                # 仅使用宿主 Cookie 建立连接；退出后已有连接也不能继续向 Bot 发消息。
                async with client.delete(f'{base}/api/host/session', headers={'X-MaiBot-Host': '1'}) as response:
                    assert response.status == 200
                await active.send_str('must-not-reach-bot')
                await active.receive(timeout=3)
                assert active.close_code == 4001
            async with client.get(f'{prefix}/api/webui/echo') as response:
                assert response.status == 401
