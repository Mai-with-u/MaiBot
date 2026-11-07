"""终端输入的目标隔离、私有 IPC 边界与跨循环投递。"""

from types import SimpleNamespace

from fastapi import FastAPI

import asyncio
import pytest

from src.common import runtime_loop
from src.common.http_service import app as app_module, console as worker_console
from src.common.http_service.server import ThreadedHTTPServer
from src.common.ipc import allocate_endpoint
from src.host.console import HostConsole
from src.host.proxy import _allowed_path


@pytest.mark.asyncio
async def test_ipc_console_runs_on_bot_loop_and_respects_switch(monkeypatch):
    main_loop = asyncio.get_running_loop()
    monkeypatch.setattr(runtime_loop, "_main_loop", main_loop)
    monkeypatch.setattr(worker_console.global_config.debug, "enable_console_input", True)
    received = []

    async def receive(text):
        assert asyncio.get_running_loop() is main_loop
        received.append(text)

    monkeypatch.setattr(worker_console, "_submit", receive)
    app = FastAPI()
    app.include_router(worker_console.router)
    monkeypatch.setattr(app_module, "create_app", lambda: app)
    endpoint = allocate_endpoint("console-test")
    server = ThreadedHTTPServer(endpoint)
    supervisor = SimpleNamespace(proxy_endpoint=lambda bot_id: endpoint)
    terminal = HostConsole(supervisor, asyncio.Event())
    terminal.bot_id = "a" * 32
    server.start()
    try:
        await server.wait_started(timeout=5)
        await terminal.handle("/pm plugin list")
        assert received == ["/pm plugin list"]
        monkeypatch.setattr(worker_console.global_config.debug, "enable_console_input", False)
        with pytest.raises(RuntimeError, match="403"):
            await terminal.handle("不可投递")
        assert received == ["/pm plugin list"]
    finally:
        await server.shutdown()


def test_browser_proxy_cannot_forward_operator_console():
    for path in ("_host/console", "_host/console/completions", "api/../_host/console"):
        assert not _allowed_path(path)
