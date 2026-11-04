"""运行契约与进程间 HTTP 通道。"""

from pathlib import Path

import asyncio

from aiohttp import ClientSession, WSMsgType
from fastapi import FastAPI, Request, WebSocket
import pytest
import uvicorn

from src.common.ipc import IpcHTTPServer, allocate_endpoint, http_connector
from src.common.runtime_context import RuntimeContext


BOT_ID = "a" * 32


def make_context(tmp_path: Path) -> RuntimeContext:
    return RuntimeContext(
        bot_id=BOT_ID, http_endpoint=allocate_endpoint("test"),
        public_base_url="http://127.0.0.1:8001/", status_path=tmp_path / "status.json",
    )


@pytest.mark.asyncio
async def test_ipc_http_server_serves_http_streams_and_websocket() -> None:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping() -> dict:
        return {"ok": True}

    @app.post("/api/upload")
    async def upload(request: Request) -> dict:
        return {"bytes": sum([len(chunk) async for chunk in request.stream()])}

    @app.websocket("/api/ws")
    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("echo:" + await websocket.receive_text())
        await websocket.close()

    endpoint = allocate_endpoint("test")
    server = IpcHTTPServer(uvicorn.Config(app, log_config=None, lifespan="off"), endpoint)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)

        async def body():
            for _ in range(8):
                yield b"x" * 1024 * 1024

        async with ClientSession(connector=http_connector(endpoint)) as client:
            async with client.get("http://bot/api/ping") as response:
                assert await response.json() == {"ok": True}
            async with client.post("http://bot/api/upload", data=body()) as response:
                assert await response.json() == {"bytes": 8 * 1024 * 1024}
            async with client.ws_connect("http://bot/api/ws") as websocket:
                await websocket.send_str("hi")
                message = await websocket.receive()
                assert message.type == WSMsgType.TEXT and message.data == "echo:hi"
    finally:
        server.should_exit = True
        await task
