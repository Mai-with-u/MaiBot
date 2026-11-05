"""远端桌宠经真实宿主代理登录，现有长连接也受注销约束。"""

from aiohttp import ClientSession, CookieJar, WSMsgType
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

import asyncio
import socket

import pytest
import uvicorn

from src.common.ipc import IpcHTTPServer, allocate_endpoint
from src.host.authentication import HostCredentials
from src.host.proxy import BotProxy, register_bot_proxy
from src.host.sessions import HostSessions
from src.host.webui_auth import register_host_auth


@pytest.mark.asyncio
async def test_remote_pet_http_and_websocket_require_current_host_session(tmp_path):
    bot_app = FastAPI()

    @bot_app.get("/api/webui/desktop-pet/remote/test")
    async def endpoint():
        return {"ok": True}

    @bot_app.websocket("/api/webui/desktop-pet/remote/ws")
    async def echo(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_text(await websocket.receive_text())
        except WebSocketDisconnect:
            pass

    address = allocate_endpoint("remote-pet-test")

    class Supervisor:
        def proxy_endpoint(self, _bot_id):
            return address

    host_app = FastAPI()
    credentials = HostCredentials(tmp_path / "webui.json")
    sessions = HostSessions(tmp_path, credentials=credentials)

    def require_session(request: Request):
        if not sessions.valid(request.cookies.get(sessions.cookie_name)):
            raise HTTPException(401, "请先登录")

    register_host_auth(host_app, sessions, credentials, require_session)
    register_bot_proxy(host_app, BotProxy(Supervisor(), sessions), management=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    bot_server = IpcHTTPServer(uvicorn.Config(bot_app, log_config=None, lifespan="off"), address)
    host_server = uvicorn.Server(uvicorn.Config(host_app, host="127.0.0.1", port=port, log_config=None, lifespan="off"))
    tasks = [asyncio.create_task(server.serve()) for server in (bot_server, host_server)]
    try:
        async with asyncio.timeout(5):
            while not (bot_server.started and host_server.started):
                await asyncio.sleep(0.01)
        base = f"http://127.0.0.1:{port}"
        root = f"{base}/api/host/bots/{'a' * 32}/proxy/api/webui/desktop-pet/remote"
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
            async with client.get(root + "/test") as response:
                assert response.status == 401
            async with client.ws_connect(root + "/ws") as denied:
                message = await denied.receive(timeout=5)
                assert message.type == WSMsgType.CLOSE and message.data == 4001
            async with client.post(base + "/api/webui/auth/verify", json={"token": credentials.token}) as response:
                assert (await response.json())["valid"]
            async with client.get(root + "/test") as response:
                assert response.status == 200
            async with client.ws_connect(root + "/ws") as websocket:
                await websocket.send_str("ping")
                assert (await websocket.receive(timeout=5)).data == "ping"
                async with client.post(base + "/api/webui/auth/logout") as response:
                    assert response.status == 200
                await websocket.send_str("ping")
                message = await websocket.receive(timeout=5)
                assert message.type == WSMsgType.CLOSE and message.data == 4001
    finally:
        bot_server.should_exit = host_server.should_exit = True
        await asyncio.gather(*tasks)
