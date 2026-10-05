"""MCP 连接生命周期回归测试（#2086）。

MCP 传输与 ``ClientSession`` 内部的 anyio CancelScope 必须在进入它的同一任务内退出，否则会留下
永不退出的作用域，其取消投递以 ``call_soon`` 无限自我重排，让事件循环陷入 100% CPU 的忙循环。
这些测试使用真实的 stdio MCP 服务器（``mcp_stdio_server_fixture.py``），覆盖在短命任务中建连、
在其他任务中关闭等场景，确认连接被完整回收、没有残留任务，事件循环保持空闲。
"""

from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, List, Tuple

import anyio
import asyncio
import gc
import pytest
import sys
import time

from src.config.official_configs import MCPConfig, MCPServerItemConfig
from src.core.tooling import ToolInvocation
from src.mcp_module import connection as connection_module
from src.mcp_module.config import MCPClientRuntimeConfig, build_mcp_server_runtime_config
from src.mcp_module.connection import MCPConnection
from src.mcp_module.manager import MCPManager
from src.mcp_module.service import MCPService

FIXTURE_SERVER = Path(__file__).with_name("mcp_stdio_server_fixture.py")
# 与 mcp_stdio_server_fixture.py 中 reject 模式返回的错误信息保持一致
REJECT_MESSAGE = "fixture server rejected initialize"
# 空闲事件循环在观察窗口内只会排入少量回调；泄漏作用域的取消投递每秒会自我重排数十万次
BUSY_LOOP_CALLBACK_THRESHOLD = 1000


def _build_server_config(name: str, state_file: Path, mode: str = "echo") -> MCPServerItemConfig:
    """构建指向测试夹具服务器的 stdio 配置。"""

    return MCPServerItemConfig(
        name=name,
        command=sys.executable,
        args=[str(FIXTURE_SERVER), str(state_file), mode],
    )


def _read_state(state_file: Path) -> str:
    """读取夹具服务器写入的进程状态。"""

    return state_file.read_text(encoding="utf-8")


async def _wait_until(predicate: Callable[[], bool], description: str, timeout_seconds: float = 20.0) -> None:
    """轮询等待条件成立，超时则断言失败。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{description}：等待 {timeout_seconds:g} 秒仍未满足")


async def _count_loop_callbacks(monkeypatch: pytest.MonkeyPatch, duration_seconds: float = 0.5) -> int:
    """统计观察窗口内事件循环通过 ``call_soon`` 排入的回调数量。"""

    loop = asyncio.get_running_loop()
    original_call_soon = loop.call_soon
    callback_count = 0

    def counting_call_soon(callback: Callable[..., Any], *args: Any, context: Any = None) -> asyncio.Handle:
        nonlocal callback_count
        callback_count += 1
        return original_call_soon(callback, *args, context=context)

    with monkeypatch.context() as patch:
        patch.setattr(loop, "call_soon", counting_call_soon)
        await asyncio.sleep(duration_seconds)
    return callback_count


async def _assert_loop_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """断言事件循环没有陷入取消投递忙循环，且没有残留任务。"""

    # 触发被丢弃对象的回收，让异步生成器终结器等延迟清理路径在观察窗口前发生
    gc.collect()
    await asyncio.sleep(0.1)

    callback_count = await _count_loop_callbacks(monkeypatch)
    assert callback_count < BUSY_LOOP_CALLBACK_THRESHOLD, f"事件循环忙循环：0.5 秒内排入 {callback_count} 个回调"
    leftover_tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    assert leftover_tasks == []


@pytest.mark.asyncio
async def test_connection_closed_from_another_task_leaves_loop_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在短命任务中建连、在另一任务中关闭后，连接应被完整回收且事件循环保持空闲。"""

    state_file = tmp_path / "echo.state"
    service = MCPService()

    # 与生产一致：reload 任务内由 asyncio.gather 包出的短命任务建连
    await asyncio.create_task(service.reload(MCPConfig(servers=[_build_server_config("echo", state_file)])))
    assert service.get_status_snapshot()["servers"][0]["connected"] is True
    result = await service.call_tool_invocation(ToolInvocation(tool_name="echo", arguments={"text": "ping"}))
    assert result.success is True
    assert result.content == "ping"

    # 关闭发生在另一个任务中，对应进程退出时的 MCPService.close()
    await asyncio.create_task(service.close())

    assert _read_state(state_file) == "exited"
    await _assert_loop_idle(monkeypatch)


@pytest.mark.asyncio
async def test_hot_reload_closes_previous_connections_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """热重载在工具调用进行中与空闲时都应在其他任务中完整回收旧连接。"""

    first_state = tmp_path / "first.state"
    second_state = tmp_path / "second.state"
    third_state = tmp_path / "third.state"
    release_file = tmp_path / "release"
    service = MCPService()
    await service.reload(MCPConfig(servers=[_build_server_config("first", first_state)]))

    # 工具调用进行中热重载：旧管理器被租用，需等调用结束后在调用任务内关闭
    call_task = asyncio.create_task(
        service.call_tool_invocation(
            ToolInvocation(tool_name="wait_for_release", arguments={"release_file": str(release_file)})
        )
    )
    await asyncio.sleep(0.1)
    await service.reload(MCPConfig(servers=[_build_server_config("second", second_state)]))
    assert _read_state(first_state) == "started"

    release_file.touch()
    result = await call_task
    assert result.success is True
    assert result.content == "released"
    assert _read_state(first_state) == "exited"

    # 没有进行中的调用时热重载：旧管理器在 reload 任务内立即关闭
    await service.reload(MCPConfig(servers=[_build_server_config("third", third_state)]))
    assert _read_state(second_state) == "exited"
    callback_count = await _count_loop_callbacks(monkeypatch)
    assert callback_count < BUSY_LOOP_CALLBACK_THRESHOLD, f"事件循环忙循环：0.5 秒内排入 {callback_count} 个回调"

    await service.close()
    assert _read_state(third_state) == "exited"
    await _assert_loop_idle(monkeypatch)


@pytest.mark.asyncio
async def test_cancelled_connect_releases_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """建连被取消（如外层超时或进程退出）时，应回滚已进入的上下文并回收子进程。"""

    state_file = tmp_path / "hang.state"
    service = MCPService()
    reload_task = asyncio.create_task(
        service.reload(MCPConfig(servers=[_build_server_config("hang", state_file, "hang")]))
    )
    await _wait_until(
        lambda: state_file.exists() and _read_state(state_file) == "started",
        "夹具服务器未启动",
    )
    # 让客户端进入 initialize 等待阶段
    await asyncio.sleep(0.2)

    reload_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reload_task

    assert _read_state(state_file) == "exited"
    await _assert_loop_idle(monkeypatch)


@pytest.mark.asyncio
async def test_cancelled_reload_closes_already_connected_servers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多个服务器并行建连时 reload 被取消，已成功建连的其他连接也应被关闭并回收子进程。"""

    echo_state = tmp_path / "echo.state"
    hang_state = tmp_path / "hang.state"
    connected_servers: List[str] = []
    original_connect = MCPConnection.connect

    async def recording_connect(self: MCPConnection) -> bool:
        connected = await original_connect(self)
        if connected:
            connected_servers.append(self.config.name)
        return connected

    monkeypatch.setattr(MCPConnection, "connect", recording_connect)
    service = MCPService()
    reload_task = asyncio.create_task(
        service.reload(
            MCPConfig(
                servers=[
                    _build_server_config("echo", echo_state),
                    _build_server_config("hang", hang_state, "hang"),
                ]
            )
        )
    )
    # echo 已建连、hang 仍卡在 initialize 时取消 reload，此时 echo 尚未登记到管理器
    await _wait_until(lambda: connected_servers == ["echo"], "echo 服务器未完成建连")

    reload_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reload_task

    assert _read_state(echo_state) == "exited"
    assert _read_state(hang_state) == "exited"
    await _assert_loop_idle(monkeypatch)


@pytest.mark.asyncio
async def test_close_before_ready_tolerates_late_server_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """就绪前关闭连接后服务器的响应晚到时，应按正常路径关停：不记录关闭失败，子进程正常退出而不被强杀。"""

    state_file = tmp_path / "late.state"
    connection = MCPConnection(
        build_mcp_server_runtime_config(_build_server_config("late", state_file, "late")),
        MCPClientRuntimeConfig(),
    )
    connect_task = asyncio.create_task(connection.connect())
    await _wait_until(
        lambda: state_file.exists() and _read_state(state_file) == "received",
        "夹具服务器未收到 initialize 请求",
    )

    with caplog.at_level("ERROR"):
        await asyncio.create_task(connection.close())

    assert await connect_task is False
    assert "关闭连接失败" not in caplog.text
    assert _read_state(state_file) == "exited"
    await _assert_loop_idle(monkeypatch)


@pytest.mark.asyncio
async def test_failed_connect_logs_original_error_and_releases_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """建连失败时应通过日志完整记录原始异常，并回收传输资源。"""

    state_file = tmp_path / "reject.state"
    with caplog.at_level("ERROR"):
        manager = await asyncio.create_task(
            MCPManager.from_app_config(
                MCPConfig(servers=[_build_server_config("reject", state_file, "reject")]),
                allow_empty=True,
            )
        )

    assert manager is not None
    server_status = manager.get_status_snapshot()["servers"][0]
    assert server_status["connected"] is False
    assert server_status["error"] == REJECT_MESSAGE
    assert "MCP 服务器 'reject' 连接失败" in caplog.text
    assert "McpError" in caplog.text
    assert _read_state(state_file) == "exited"

    await manager.close()
    await _assert_loop_idle(monkeypatch)


@pytest.mark.asyncio
async def test_runtime_transport_failure_is_logged_without_busy_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """传输层子任务在运行期异常结束时，应断开连接并完整记录原始异常，事件循环保持空闲。"""

    state_file = tmp_path / "runtime.state"
    crash_event = asyncio.Event()
    original_stdio_client = connection_module.tolerant_stdio_client

    @asynccontextmanager
    async def crashing_stdio_client(params: Any) -> AsyncIterator[Tuple[Any, Any]]:
        """在真实 stdio 传输之上附加一个可按需异常退出的子任务，模拟远程流断开等传输层故障。"""

        async with original_stdio_client(params) as streams, anyio.create_task_group() as task_group:

            async def crash_on_demand() -> None:
                await crash_event.wait()
                raise RuntimeError("simulated transport failure")

            task_group.start_soon(crash_on_demand)
            yield streams

    monkeypatch.setattr(connection_module, "tolerant_stdio_client", crashing_stdio_client)
    connection = MCPConnection(
        build_mcp_server_runtime_config(_build_server_config("runtime", state_file)),
        MCPClientRuntimeConfig(),
    )
    assert await asyncio.create_task(connection.connect()) is True

    with caplog.at_level("ERROR"):
        crash_event.set()
        callback_count = await _count_loop_callbacks(monkeypatch)
        assert callback_count < BUSY_LOOP_CALLBACK_THRESHOLD, f"事件循环忙循环：0.5 秒内排入 {callback_count} 个回调"
        await _wait_until(lambda: connection.session is None, "连接未在传输异常后断开")

    assert "MCP 服务器 'runtime' 连接异常中断: simulated transport failure" in caplog.text
    assert connection.last_error == "simulated transport failure"
    result = await connection.call_tool("echo", {"text": "ping"})
    assert result.success is False
    assert _read_state(state_file) == "exited"

    await asyncio.create_task(connection.close())
    await _assert_loop_idle(monkeypatch)


@pytest.mark.asyncio
async def test_close_cancels_stuck_lifecycle_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """所有者任务关闭超时时应被取消，取消引发的回滚仍在所有者任务内完成。"""

    state_file = tmp_path / "stuck.state"
    original_connect_transport = MCPConnection._connect_transport

    async def connect_transport_with_stuck_teardown(
        self: MCPConnection,
        exit_stack: AsyncExitStack,
    ) -> Tuple[Any, Any]:
        streams = await original_connect_transport(self, exit_stack)
        # 在传输之上压入一个永不完成的退出回调，模拟关闭时卡住
        exit_stack.push_async_callback(asyncio.Event().wait)
        return streams

    monkeypatch.setattr(MCPConnection, "_connect_transport", connect_transport_with_stuck_teardown)
    monkeypatch.setattr(MCPConnection, "_get_close_timeout_seconds", lambda self: 0.2)
    connection = MCPConnection(
        build_mcp_server_runtime_config(_build_server_config("stuck", state_file)),
        MCPClientRuntimeConfig(),
    )

    assert await asyncio.create_task(connection.connect()) is True
    with caplog.at_level("WARNING"):
        await asyncio.create_task(connection.close())

    assert "未完成关闭" in caplog.text
    assert connection.session is None
    assert _read_state(state_file) == "exited"
    await _assert_loop_idle(monkeypatch)
