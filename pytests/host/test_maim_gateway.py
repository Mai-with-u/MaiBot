from contextlib import asynccontextmanager
from types import SimpleNamespace

from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError
from fastapi import FastAPI, WebSocket

import asyncio
import pytest
import uvicorn

from src.common.ipc import IpcHTTPServer, allocate_endpoint, http_connector, release_endpoint
from src.host.maim import register_maim_gateway


@asynccontextmanager
async def serve(app):
    endpoint = allocate_endpoint("maim-test")
    server = IpcHTTPServer(uvicorn.Config(app, log_config=None, lifespan="off"), endpoint)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield endpoint
    finally:
        server.should_exit = True
        await task
        release_endpoint(endpoint)


def worker(name):
    app = FastAPI()

    @app.websocket("/_maim/{protocol}")
    async def connect(ws: WebSocket, protocol: str):
        await ws.accept()
        if ws.headers.get("authorization") != name:
            await ws.close(code=1008)
            return
        await ws.send_json({"bot": name, "protocol": protocol, "platform": ws.headers.get("platform"), "cookie": ws.headers.get("cookie")})
        await ws.close(code=1000)

    return app


@pytest.mark.asyncio
async def test_gateway_has_explicit_bot_routing_and_no_legacy_or_browser_identity():
    async with serve(worker("A")) as a, serve(worker("B")) as b:
        endpoints = {"a" * 32: a, "b" * 32: b}

        def endpoint(bot_id):
            if bot_id not in endpoints:
                raise RuntimeError("not running")
            return endpoints[bot_id]

        host = FastAPI()
        register_maim_gateway(host, SimpleNamespace(proxy_endpoint=endpoint))
        async with serve(host) as address, ClientSession(connector=http_connector(address)) as client:
            for bot_id, name in (("a" * 32, "A"), ("b" * 32, "B")):
                for protocol in ("ws", "api"):
                    async with client.ws_connect(f"http://host/maim/{bot_id}/{protocol}", headers={"Authorization": name, "platform": "qq", "cookie": "host-secret"}) as ws:
                        assert await ws.receive_json() == {"bot": name, "protocol": protocol, "platform": "qq", "cookie": None}
            async with client.ws_connect(f"http://host/maim/{'b' * 32}/ws", headers={"Authorization": "A"}) as ws:
                assert (await ws.receive()).type == WSMsgType.CLOSE
                assert ws.close_code == 1008
            for path in ("/ws", "/maim/ws", f"/maim/{'c' * 32}/ws"):
                with pytest.raises(WSServerHandshakeError):
                    await client.ws_connect("http://host" + path)


@pytest.mark.asyncio
async def test_real_protocols_use_ipc_and_execute_on_bot_loop(monkeypatch):
    from src.common import runtime_loop
    from src.common.http_service import app as app_module
    from src.common.http_service.maim import MaimApplication
    from src.common.http_service.server import ThreadedHTTPServer
    from src.common.message_server import api
    from src.config.config import global_config

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(runtime_loop, "_main_loop", loop)
    monkeypatch.setattr(global_config.maim_message, "enabled", True)
    monkeypatch.setattr(global_config.maim_message, "auth_token", ["test-token"])
    monkeypatch.setattr(global_config.maim_message, "enable_api_server", True)
    monkeypatch.setattr(global_config.maim_message, "api_server_allowed_api_keys", ["test-key"])
    monkeypatch.setattr(api, "message_app", FastAPI())
    instance = api.HostedMessageServer()
    monkeypatch.setattr(api, "global_api", instance)
    app = FastAPI()
    app.mount("/_maim", MaimApplication())
    monkeypatch.setattr(app_module, "create_app", lambda: app)

    async def handle(message):
        assert asyncio.get_running_loop() is loop
        await instance.connection.send_message("qq", {"received": message["content"]})

    instance.register_custom_message_handler("probe", handle)
    server = ThreadedHTTPServer(allocate_endpoint("real-maim"))
    await instance.run()
    server.start()
    try:
        await server.wait_started(5)
        async with ClientSession(connector=http_connector(server.endpoint)) as client:
            async with client.ws_connect("http://bot/_maim/ws", headers={"Authorization": "test-token", "platform": "qq"}) as ws:
                await ws.send_json({"is_custom_message": True, "message_type_name": "probe", "content": "hello"})
                assert await ws.receive_json(timeout=3) == {"received": "hello"}
            async with client.ws_connect("http://bot/_maim/api?api_key=test-key&platform=qq"):
                async with asyncio.timeout(3):
                    while not instance.extra_server.connection_users:
                        await asyncio.sleep(0.01)
            async with client.ws_connect("http://bot/_maim/ws", headers={"Authorization": "wrong"}) as ws:
                assert (await ws.receive(timeout=3)).type == WSMsgType.CLOSE
                assert ws.close_code == 1008
    finally:
        await instance.stop()
        await server.shutdown()
