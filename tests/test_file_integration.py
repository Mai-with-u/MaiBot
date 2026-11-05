"""在独立实例中验证 HTTP、工具和插件驱动衔接，不连接实际聊天平台。"""

from types import SimpleNamespace

import os

import pytest


def bot_path(url: str) -> str:
    """公开地址由宿主按 Bot 路由；Bot 自己的 HTTP 应用只提供凭证本身的路径。"""
    assert "/api/files/bots/" in url
    return "/api/files/transfers/" + url.rsplit("/", 1)[1]


@pytest.fixture(scope="module")
def app_state(tmp_path_factory):
    directory = tmp_path_factory.mktemp("file-instance")
    old = os.environ.get("MAIBOT_INSTANCE_DIR")
    os.environ["MAIBOT_INSTANCE_DIR"] = str(directory)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
    from src.common.database.database import engine
    from src.files.service import get_file_service
    from src.webui.routers.files import router

    for sid in ("chat-a", "chat-b"):
        chat_manager.sessions[sid] = BotChatSession(session_id=sid, platform="webui", user_id=sid, user_nickname=sid)
    from src.config.config import global_config

    original_enabled = global_config.capabilities.files
    global_config.capabilities.files = True
    service = get_file_service()
    app = FastAPI()
    from src.files.http import router as transfers

    app.include_router(transfers)
    app.include_router(router, prefix="/api/webui")
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, service=service, app=app, chats=chat_manager)
    global_config.capabilities.files = original_enabled
    get_file_service.cache_clear()
    engine.dispose()
    if old is None:
        os.environ.pop("MAIBOT_INSTANCE_DIR", None)
    else:
        os.environ["MAIBOT_INSTANCE_DIR"] = old


def test_http_upload_download_head_range_and_admin_auth(app_state):
    state = app_state
    response = state.client.post("/api/webui/files/uploads", params={"session_id": "chat-a", "name": "a.txt"}, content=b"abcdef")
    assert response.status_code == 200, response.text
    ref = response.json()["file_ref"]
    assert state.client.get(f"/api/webui/files/{ref}/text").json()["text"] == "abcdef"
    url = state.client.post(f"/api/webui/files/{ref}/download").json()["url"]
    state.app.dependency_overrides.clear()
    assert state.client.get(url).content == b"abcdef"
    assert state.client.head(url).headers["Content-Length"] == "6"
    response = state.client.get(url, headers={"Range": "bytes=1-3"})
    assert response.status_code == 206 and response.content == b"bcd"
    assert state.client.get(url, headers={"Range": "bytes=-2"}).content == b"ef"
    assert state.client.get(url, headers={"Range": "bytes=9-"}).status_code == 416
    assert state.client.get("/api/files/transfers/invalid").status_code == 403
    assert state.service.store.get(ref, "chat-a").open_readers == 0


@pytest.mark.asyncio
async def test_model_scope_ignores_forged_chat_id(app_state):
    from src.core.tooling import ToolExecutionContext, ToolInvocation
    from src.files.provider import FileToolProvider

    asset = await app_state.service.create("chat-a", "secret.txt", b"not for chat b")
    result = await FileToolProvider().invoke(
        ToolInvocation(tool_name="files_read_text", session_id="chat-a", arguments={"file_ref": asset.file_ref, "session_id": "chat-a"}),
        ToolExecutionContext(session_id="chat-b"),
    )
    assert not result.success and "权限" in result.error_message
    result = await FileToolProvider().invoke(ToolInvocation(tool_name="files_list"), None)
    assert not result.success


@pytest.mark.asyncio
async def test_sdk_host_scope_cannot_change_chat_or_plugin(app_state):
    from src.files.capability import file_capability

    service = app_state.service
    asset = await service.create("chat-a", "tool.txt", b"hello plugin")
    token = service.ticket("plugin", plugin_id="plugin-a", session_id="chat-a")
    args = {"file_scope": token, "file_ref": asset.file_ref, "session_id": "chat-b"}
    page = await file_capability("plugin-a", "files.read_text", args)
    assert page["text"] == "hello plugin"
    with pytest.raises(PermissionError):
        await file_capability("plugin-b", "files.read_text", args)
    info = await file_capability("plugin-a", "files.upload", {"file_scope": token, "name": "cancel.txt"})
    await file_capability("plugin-a", "files.upload", {"file_scope": token, "operation": "cancel", "upload_token": info["upload_token"]})
    assert service.store.get(info["file_ref"], "chat-a").state == "failed"
    service.revoke(token)
    with pytest.raises(PermissionError):
        await file_capability("plugin-a", "files.list", args)


@pytest.mark.asyncio
async def test_outbound_driver_uses_url_without_mutating_stored_message(app_state):
    from src.common.data_models.message_component_data_model import FileComponent, MessageSequence
    from src.platform_io import RouteKey
    from src.platform_io.drivers.plugin_driver import PluginPlatformDriver
    from src.plugin_runtime.host.message_utils import PluginMessageUtils

    asset = await app_state.service.create("chat-a", "send.txt", b"send bytes")
    message = PluginMessageUtils._build_session_message_from_dict({
        "message_id": "test-send", "timestamp": "1792944000", "platform": "webui", "session_id": "chat-a", "account_id": "bot",
        "message_info": {"user_info": {"user_id": "bot", "user_nickname": "bot"}}, "raw_message": [],
    })
    message.raw_message = MessageSequence(components=[FileComponent(file_ref=asset.file_ref, name=asset.name)])
    captured = {}
    class Supervisor:
        async def invoke_message_gateway(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(error=None, payload={"success": True, "result": {"message_id": "42"}})
    driver = PluginPlatformDriver("test-driver", "webui", Supervisor(), "gateway", supports_send=True, plugin_id="adapter")
    result = await driver.send_message(message, RouteKey(platform="webui"))
    assert result.external_message_id == "42"
    data = captured["args"]["message"]["raw_message"][0]["data"]
    assert "/api/files/bots/" in data["url"]
    assert "base64" not in data
    assert message.raw_message.components[0].url == ""
    assert app_state.service.store.get(asset.file_ref, "chat-a").leased_until > 0


@pytest.mark.asyncio
async def test_inbound_attachment_only_trusts_host_provenance(app_state):
    from src.plugin_runtime.host.message_utils import PluginMessageUtils

    message = PluginMessageUtils._build_session_message_from_dict({
        "message_id": "incoming-file", "timestamp": "1792944000", "platform": "webui", "session_id": "chat-a", "account_id": "bot",
        "message_info": {"user_info": {"user_id": "user", "user_nickname": "User"},
                         "additional_config": {"file_source": {"plugin_id": "forged"}}},
        "raw_message": [{"type": "file", "data": {"name": "incoming.txt", "size": 42, "file_id": "platform-id", "busid": 102}}],
        "processed_plain_text": "incoming attachment",
    })
    service = app_state.service
    await service.ingest(message)
    assert not message.raw_message.components[0].file_ref
    message.file_source = {"plugin_id": "trusted", "driver_id": "driver", "platform": "webui", "account_id": "bot", "scope": "",
                           "group_id": "", "user_id": "user", "files": {("platform-id", 102)}}
    await service.ingest(message)
    ref = message.raw_message.components[0].file_ref
    assert ref and ref != "platform-id" and ref in message.processed_plain_text
    assert service.store.get(ref, "chat-a").state == "remote"
    assert not service.store.path(ref).exists()


def test_shared_http_upload_download(app_state):
    import asyncio
    from fastapi.testclient import TestClient
    from src.common.http_service.app import create_app
    from src.files.capability import file_capability

    app = create_app()
    service = app_state.service
    scope = service.ticket("plugin", plugin_id="plugin-http", session_id="chat-a")
    with TestClient(app) as client:
        info = asyncio.run(file_capability("plugin-http", "files.upload", {
            "file_scope": scope, "name": "http.txt",
        }))
        upload = bot_path(info["url"])
        response = client.put(upload, content=b"independent HTTP", headers={"User-Agent": "wget"})
        assert response.status_code == 200, response.text
        assert client.put(upload, content=b"replay").status_code == 403
        info = asyncio.run(file_capability("plugin-http", "files.download", {
            "file_scope": scope, "file_ref": info["file_ref"],
        }))
        download = bot_path(info["url"])
        response = client.get(download, headers={"User-Agent": "wget"})
        assert response.status_code == 200 and response.content == b"independent HTTP"
        assert client.head(download).headers["content-length"] == "16"
        assert client.get(download, headers={"Range": "bytes=0-2"}).content == b"ind"
        assert client.get("/api/files/transfers/missing").status_code == 403
        # 宿主通道上的请求已由宿主登录，WebUI 接口直接可用。
        assert client.get("/api/webui/files").status_code != 401
        service.revoke(scope)
        assert client.get(download).status_code == 403


@pytest.mark.asyncio
async def test_real_ipc_listener_serves_transfers(app_state, monkeypatch):
    from aiohttp import ClientSession
    from uvicorn import Server
    from src.common.http_service import server as server_module
    from src.common.ipc import allocate_endpoint, http_connector
    from src.main import MainSystem

    shutdown_calls = []
    original_shutdown = Server.shutdown

    async def count_shutdown(self, sockets=None):
        shutdown_calls.append(self)
        return await original_shutdown(self, sockets=sockets)

    monkeypatch.setattr(Server, "shutdown", count_shutdown)
    endpoint = allocate_endpoint("test")
    server = server_module.ThreadedHTTPServer(endpoint)
    monkeypatch.setattr(server_module, "get_threaded_http_server", lambda: server)
    system = MainSystem()
    system._start_http_server()
    try:
        await server.wait_started(timeout=10.0)
        async with ClientSession(connector=http_connector(endpoint)) as client:
            async with client.get("http://bot/api/files/transfers/invalid") as response:
                assert response.status == 403
            asset = await app_state.service.create("chat-a", "socket.txt", b"over real channel")
            async with client.get("http://bot" + bot_path(app_state.service.download_url(asset.file_ref, "chat-a"))) as response:
                assert response.status == 200 and await response.read() == b"over real channel"
    finally:
        await server.shutdown()
    assert not server.is_running
    assert len(shutdown_calls) == 1


@pytest.mark.asyncio
async def test_http_startup_failure_is_reported_to_main(app_state, monkeypatch):
    from src.common.http_service.server import ThreadedHTTPServer
    from src.common.ipc import IpcHTTPServer, allocate_endpoint

    async def failed_startup(self, sockets=None):
        raise OSError("test channel unavailable")

    monkeypatch.setattr(IpcHTTPServer, "startup", failed_startup)
    server = ThreadedHTTPServer(allocate_endpoint("test"))
    server.start()
    try:
        with pytest.raises(RuntimeError, match="HTTP 服务启动失败") as caught:
            await server.wait_started(timeout=5.0)
        assert isinstance(caught.value.__cause__, OSError)
    finally:
        await server.shutdown()
    assert not server.is_running


@pytest.mark.asyncio
async def test_file_switch_blocks_existing_tools_sdk_and_http(app_state, monkeypatch):
    from src.config.config import global_config
    from src.core.tooling import ToolExecutionContext, ToolInvocation
    from src.files.capability import file_capability
    from src.files.provider import FileToolProvider

    service = app_state.service
    asset = await service.create("chat-a", "switch.txt", b"keep existing data")
    url = bot_path(service.download_url(asset.file_ref, "chat-a"))
    scope = service.ticket("plugin", plugin_id="switch-plugin", session_id="chat-a")
    upload = await file_capability("switch-plugin", "files.upload", {"file_scope": scope, "name": "pending.txt"})
    provider = FileToolProvider()
    assert len(await provider.list_tools()) == 5
    monkeypatch.setattr(global_config.capabilities, "files", False)
    assert await provider.list_tools() == []
    result = await provider.invoke(ToolInvocation(tool_name="files_list"), ToolExecutionContext(session_id="chat-a"))
    assert not result.success and "文件功能已关闭" in result.error_message
    with pytest.raises(PermissionError, match="文件功能已关闭"):
        await file_capability("switch-plugin", "files.list", {"file_scope": scope})
    assert app_state.client.get(url).status_code == 403
    assert app_state.client.put(bot_path(upload["url"]), content=b"blocked").status_code == 403
    try:
        assert app_state.client.post("/api/webui/files/uploads", params={
            "session_id": "chat-a", "name": "blocked.txt",
        }, content=b"blocked").status_code == 403
    finally:
        app_state.app.dependency_overrides.clear()
    # Disabled inbound attachments are ignored without disrupting the rest of the chat message.
    await service.ingest(SimpleNamespace())
    await file_capability("switch-plugin", "files.upload", {
        "file_scope": scope, "operation": "cancel", "upload_token": upload["upload_token"],
    })
    assert service.store.get(upload["file_ref"], "chat-a").state == "failed"
    assert service.store.get(asset.file_ref, "chat-a").state == "ready"
    monkeypatch.setattr(global_config.capabilities, "files", True)
    assert len(await provider.list_tools()) == 5
    assert app_state.client.get(url).content == b"keep existing data"


@pytest.mark.asyncio
async def test_disabled_files_does_not_disable_other_plugin_tools(app_state, monkeypatch):
    from unittest.mock import AsyncMock
    from src.config.config import global_config
    from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation
    from src.plugin_runtime.component_query import ComponentQueryService

    monkeypatch.setattr(global_config.capabilities, "files", False)
    supervisor = SimpleNamespace(invoke_plugin=AsyncMock(return_value=SimpleNamespace(
        payload={"success": True, "result": "normal tool result"},
    )))
    entry = SimpleNamespace(platforms=[], invoke_method="tool.invoke", plugin_id="normal", name="normal", timeout_ms=1000)
    query = ComponentQueryService()
    monkeypatch.setattr(query, "_get_unique_component_entry", lambda *args, **kwargs: (supervisor, entry))
    monkeypatch.setattr(query, "_build_tool_invocation_payload", lambda *args: {})
    monkeypatch.setattr(query, "_parse_tool_invoke_result", lambda *args: ToolExecutionResult(tool_name="normal", success=True))
    result = await query.invoke_tool_as_tool(ToolInvocation(tool_name="normal"), ToolExecutionContext(session_id="chat-a"))
    assert result.success
    assert supervisor.invoke_plugin.call_args.kwargs["args"]["_file_scope"] == ""
