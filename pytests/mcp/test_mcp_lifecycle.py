"""使用真实 SDK 作用域验证 MCP 连接持有、异常及清理。"""

from typing import Any, List

import asyncio
import json
import traceback

import anyio
import pytest

from src.mcp_module import stdio_filter
from src.config.official_configs import MCPConfig, MCPServerItemConfig
from src.mcp_module.config import MCPClientRuntimeConfig, MCPServerRuntimeConfig
from src.mcp_module.connection import MCPConnection
from src.mcp_module.manager import MCPManager
from src.mcp_module.service import MCPService


class _Process:
    """仅模拟子进程字节流，保留真实 ClientSession 和 anyio TaskGroup。"""

    def __init__(self) -> None:
        self.output, self.stdout = anyio.create_memory_object_stream(10)
        self.stdin = self
        self.closed = anyio.Event()
        self.entered_task = None
        self.exited_task = None

    async def __aenter__(self):
        self.entered_task = asyncio.current_task()
        return self

    async def __aexit__(self, *_args) -> None:
        self.exited_task = asyncio.current_task()
        await self.stdout.aclose()
        await self.output.aclose()

    async def send(self, payload: bytes) -> None:
        message = json.loads(payload)
        if "id" not in message:
            return
        result: Any = {}
        if message["method"] == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {},
                "serverInfo": {"name": "test", "version": "1"},
            }
        response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        await self.output.send((json.dumps(response) + "\n").encode())

    async def aclose(self) -> None:
        self.closed.set()

    async def wait(self) -> int:
        await self.closed.wait()
        return 0


@pytest.fixture
def processes(monkeypatch) -> List[_Process]:
    created: List[_Process] = []

    async def create_process(**_kwargs):
        process = _Process()
        created.append(process)
        return process

    monkeypatch.setattr(stdio_filter, "_create_platform_compatible_process", create_process)
    return created


def _connection() -> MCPConnection:
    return MCPConnection(MCPServerRuntimeConfig(name="test", command="test-server"), MCPClientRuntimeConfig())


@pytest.mark.asyncio
async def test_connection_survives_connect_task_and_closes_from_another_task(processes) -> None:
    connection = _connection()
    assert await asyncio.create_task(connection.connect())
    try:
        await connection.session.send_ping()
        await processes[0].output.send(b"\x00BANNER\n")
        await connection.session.send_ping()
        assert not connection._lifecycle_task.done()
    finally:
        await asyncio.create_task(connection.close())
    assert connection.session is None
    assert connection.last_error == ""
    assert processes[0].entered_task is processes[0].exited_task
    assert connection._lifecycle_task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid_encoding", "eof"])
async def test_transport_failure_stops_owner_and_marks_connection_disconnected(processes, caplog, failure) -> None:
    manager = MCPManager(MCPClientRuntimeConfig())
    await manager._connect_all([MCPServerRuntimeConfig(name="test", command="test-server")])
    connection = manager._connections["test"]
    try:
        if failure == "invalid_encoding":
            await processes[0].output.send(b"\xff\xfeBANNER\n")
        else:
            await processes[0].output.aclose()
        await asyncio.wait_for(asyncio.shield(connection._lifecycle_task), 2)
        assert connection.session is None
        assert connection.last_error
        status = manager.get_status_snapshot()["servers"][0]
        assert not status["connected"]
        assert status["error"] == connection.last_error
        result = await connection.call_tool("test", {})
        assert not result.success
        assert "未连接" in result.error_message
        assert processes[0].entered_task is processes[0].exited_task
        exceptions = "".join(
            "".join(traceback.format_exception(*record.exc_info)) for record in caplog.records if record.exc_info
        )
        assert ("UnicodeDecodeError" if failure == "invalid_encoding" else "EOFError") in exceptions
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cancelled_connect_cleans_up_owner(processes, monkeypatch) -> None:
    connection = _connection()
    discovering = asyncio.Event()

    async def blocked_discovery():
        discovering.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(connection, "_load_server_features", blocked_discovery)
    task = asyncio.create_task(connection.connect())
    await asyncio.wait_for(discovering.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert connection.session is None
    assert connection._lifecycle_task.done()
    assert processes[0].entered_task is processes[0].exited_task


@pytest.mark.asyncio
async def test_invalid_output_during_connect_fails_and_cleans_up(processes, monkeypatch) -> None:
    async def create_process(**_kwargs):
        process = _Process()
        processes.append(process)
        await process.output.send(b"\xff\xfeBANNER\n")
        return process

    monkeypatch.setattr(stdio_filter, "_create_platform_compatible_process", create_process)
    connection = _connection()
    try:
        assert not await asyncio.wait_for(connection.connect(), 2)
        assert connection.session is None
        assert connection.last_error
        assert connection._lifecycle_task.done()
        assert processes[0].entered_task is processes[0].exited_task
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_concurrent_connect_and_reconnect_after_close(processes) -> None:
    connection = _connection()
    try:
        assert await asyncio.gather(connection.connect(), connection.connect()) == [True, True]
        assert len(processes) == 1
        await connection.close()
        assert await connection.connect()
        assert len(processes) == 2
        await connection.session.send_ping()
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_cancelled_close_does_not_interrupt_owner_cleanup(processes, monkeypatch) -> None:
    """关闭调用者取消后，持有任务仍在原作用域内完成清理。"""
    connection = _connection()
    assert await connection.connect()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def blocked_wait() -> int:
        cleanup_started.set()
        await finish_cleanup.wait()
        return 0

    monkeypatch.setattr(processes[0], "wait", blocked_wait)
    close_task = asyncio.create_task(connection.close())
    try:
        await asyncio.wait_for(cleanup_started.wait(), 2)
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_task
        assert not connection._lifecycle_task.done()
    finally:
        finish_cleanup.set()
        await asyncio.wait_for(connection.close(), 2)
    assert processes[0].entered_task is processes[0].exited_task
    assert connection.last_error == ""


@pytest.mark.asyncio
async def test_service_publishes_disconnect_without_reload(processes, monkeypatch) -> None:
    """真实连接断开后立即刷新服务缓存，不需要再次调用工具或重载配置。"""
    manager = MCPManager(MCPClientRuntimeConfig())
    await manager._connect_all([MCPServerRuntimeConfig(name="test", command="test-server")])

    async def create_manager(cls, *args, **kwargs):
        return manager

    monkeypatch.setattr(MCPManager, "from_app_config", classmethod(create_manager))
    service = MCPService()
    await service.reload(MCPConfig(servers=[MCPServerItemConfig(name="test", command="test-server")]))
    connection = manager._connections["test"]
    try:
        assert service.get_status_snapshot()["servers"][0]["connected"]
        await processes[0].output.aclose()
        await asyncio.wait_for(asyncio.shield(connection._lifecycle_task), 2)
        status = service.get_status_snapshot()["servers"][0]
        assert not status["connected"]
        assert status["error"] == connection.last_error
        assert status["error"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancelled_batch_connect_closes_already_connected_servers(processes, monkeypatch) -> None:
    """取消并行建连时不能遗留已经成功、尚未注册的连接。"""
    fast_connected = asyncio.Event()
    slow_discovering = asyncio.Event()
    connections: List[MCPConnection] = []
    original_connect = MCPConnection.connect
    original_discovery = MCPConnection._load_server_features

    async def connect(connection: MCPConnection) -> bool:
        connections.append(connection)
        result = await original_connect(connection)
        if connection.config.name == "fast":
            fast_connected.set()
        return result

    async def discover(connection: MCPConnection) -> None:
        if connection.config.name == "slow":
            slow_discovering.set()
            await asyncio.Event().wait()
        await original_discovery(connection)

    monkeypatch.setattr(MCPConnection, "connect", connect)
    monkeypatch.setattr(MCPConnection, "_load_server_features", discover)
    manager = MCPManager(MCPClientRuntimeConfig())
    task = asyncio.create_task(manager._connect_all([
        MCPServerRuntimeConfig(name="fast", command="test-server"),
        MCPServerRuntimeConfig(name="slow", command="test-server"),
    ]))
    try:
        await asyncio.wait_for(fast_connected.wait(), 2)
        await asyncio.wait_for(slow_discovering.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert all(connection._lifecycle_task.done() for connection in connections)
        assert all(connection.session is None for connection in connections)
        assert all(process.entered_task is process.exited_task for process in processes)
    finally:
        for connection in connections:
            await connection.close()


@pytest.mark.asyncio
async def test_repeated_close_during_initialization_does_not_cancel_cleanup_twice(processes, monkeypatch) -> None:
    """多方关闭同一条建连中的连接时，第二次关闭不能打断首次清理。"""
    connection = _connection()
    discovering = asyncio.Event()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def discover() -> None:
        discovering.set()
        await asyncio.Event().wait()

    async def wait() -> int:
        # SDK 作用域已经取消，进程清理仍需等待模拟的资源释放完成。
        with anyio.CancelScope(shield=True):
            cleanup_started.set()
            await finish_cleanup.wait()
        return 0

    monkeypatch.setattr(connection, "_load_server_features", discover)
    connect_task = asyncio.create_task(connection.connect())
    close_tasks = []
    try:
        await asyncio.wait_for(discovering.wait(), 2)
        monkeypatch.setattr(processes[0], "wait", wait)
        close_tasks.append(asyncio.create_task(connection.close()))
        await asyncio.wait_for(cleanup_started.wait(), 2)
        close_tasks.append(asyncio.create_task(connection.close()))
        await asyncio.sleep(0)
        assert connection._lifecycle_task.cancelling() == 1
        finish_cleanup.set()
        await asyncio.wait_for(asyncio.gather(*close_tasks), 2)
        assert not await asyncio.wait_for(connect_task, 2)
        assert processes[0].entered_task is processes[0].exited_task
        assert connection.last_error == ""
    finally:
        finish_cleanup.set()
        await connection.close()
        await asyncio.gather(connect_task, *close_tasks, return_exceptions=True)
