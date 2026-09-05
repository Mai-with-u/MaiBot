"""RpcSession 进程内假连接行为测试。

覆盖握手 pending、Host 入站回复、send_request 关联以及断线错误码。
若 RpcSession 尚未落地，本模块在尝试导入后 skip。
"""

from typing import Any, Callable, List, Optional, Tuple

import asyncio
import contextlib

import pytest

from src.plugin_runtime.protocol.codec import MsgPackCodec
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.transport.base import ConnectionClosed

try:
    from src.plugin_runtime.rpc_session import RpcSession
except ImportError:
    _rpc_session_module = pytest.importorskip("src.plugin_runtime.rpc_session")
    RpcSession = getattr(_rpc_session_module, "RpcSession", None)
    if RpcSession is None:
        pytest.skip("RpcSession 尚未实现", allow_module_level=True)

_CLOSED = object()
_SESSION_TOKEN = "test-session-token"
_WAIT_SECONDS = 1.0


class _FakeConnection:
    """进程内假连接：用 Queue 代替真实 IPC 分帧通道。"""

    def __init__(
        self,
        incoming: asyncio.Queue[Any],
        outgoing: asyncio.Queue[Any],
        *,
        on_send: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        self.incoming = incoming
        self.outgoing = outgoing
        self.on_send = on_send
        self._closed = False

    async def send_frame(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionClosed("连接已关闭")
        if self.on_send is not None:
            self.on_send(data)
        await self.outgoing.put(data)

    async def recv_frame(self) -> bytes:
        if self._closed:
            raise ConnectionClosed("连接已关闭")
        data = await self.incoming.get()
        if data is _CLOSED or self._closed:
            self._closed = True
            raise ConnectionClosed("连接已关闭")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(f"假连接收到非字节帧: {type(data)!r}")
        return bytes(data)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.incoming.put(_CLOSED)
        await self.outgoing.put(_CLOSED)

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def inject_frame(self, data: bytes) -> None:
        await self.incoming.put(data)

    async def take_sent_frame(self) -> bytes:
        data = await self.outgoing.get()
        if data is _CLOSED:
            raise ConnectionClosed("连接已关闭")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(f"假连接发出非字节帧: {type(data)!r}")
        return bytes(data)


def _make_loopback_connection() -> _FakeConnection:
    return _FakeConnection(asyncio.Queue(), asyncio.Queue())


def _make_connection_pair() -> Tuple[_FakeConnection, _FakeConnection]:
    left_to_right: asyncio.Queue[Any] = asyncio.Queue()
    right_to_left: asyncio.Queue[Any] = asyncio.Queue()
    return _FakeConnection(right_to_left, left_to_right), _FakeConnection(left_to_right, right_to_left)


def _make_session(connection: _FakeConnection, role: str, codec: MsgPackCodec) -> Any:
    return RpcSession(
        connection,
        role=role,
        codec=codec,
        session_token=_SESSION_TOKEN,
        host_version="1.2.4",
        sdk_version="1.0.0",
        runner_id="runner-under-test",
    )


def _pending_mapping(session: Any) -> Any:
    pending = getattr(session, "_pending_requests", None)
    if pending is None:
        pending = getattr(session, "pending_requests", None)
    if pending is None:
        raise AssertionError("RpcSession 未暴露 pending 请求表 (_pending_requests / pending_requests)")
    return pending


def _pending_count(session: Any) -> int:
    return len(_pending_mapping(session))


async def _start_session(session: Any) -> Optional[asyncio.Task[Any]]:
    """启动会话循环。

    `start()` 若拉起后台任务后立即返回，则本函数返回 None。
    若 `start()` 本身阻塞在 recv 循环上，则将其放到 Task 中并返回该 Task。
    """
    result = session.start()
    if not hasattr(result, "__await__"):
        return None
    task = asyncio.create_task(result, name="RpcSession.start")
    await asyncio.sleep(0)
    if task.done():
        exc = task.exception()
        if exc is not None:
            raise exc
        return None
    return task


async def _stop_session(session: Any, start_task: Optional[asyncio.Task[Any]] = None) -> None:
    with contextlib.suppress(Exception):
        await session.disconnect()
    if start_task is not None and not start_task.done():
        start_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await start_task


async def _stop_send_queue_worker(session: Any) -> None:
    """取消发送队列 worker，用于证明入站回复不依赖它。"""
    worker = getattr(session, "_send_worker_task", None)
    if worker is None or worker.done():
        return
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = _WAIT_SECONDS) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_handshake_never_creates_pending_entries() -> None:
    """握手应直接收发 runner.hello，不得把 request_id 登记进 pending。"""
    codec = MsgPackCodec()
    pending_at_send: List[Tuple[str, int]] = []
    host_conn, runner_conn = _make_connection_pair()
    host = _make_session(host_conn, "host", codec)
    runner = _make_session(runner_conn, "runner", codec)
    host_conn.on_send = lambda _data: pending_at_send.append(("host", _pending_count(host)))
    runner_conn.on_send = lambda _data: pending_at_send.append(("runner", _pending_count(runner)))

    try:
        await asyncio.wait_for(
            asyncio.gather(host.handshake(), runner.handshake()),
            timeout=_WAIT_SECONDS,
        )
    finally:
        await _stop_session(host)
        await _stop_session(runner)

    assert pending_at_send, "握手过程中双方都应发出 hello 帧"
    assert all(count == 0 for _role, count in pending_at_send)
    assert _pending_count(host) == 0
    assert _pending_count(runner) == 0


@pytest.mark.asyncio
async def test_host_inbound_request_reply_does_not_require_send_queue_worker() -> None:
    """Host 处理入站 REQUEST 的回复必须直接写连接，不能依赖 send-queue worker。"""
    codec = MsgPackCodec()
    connection = _make_loopback_connection()
    host = _make_session(connection, "host", codec)

    async def handle_ping(envelope: Envelope) -> Envelope:
        return envelope.make_response(payload={"pong": True})

    host.register_method("cap.ping", handle_ping)
    start_task = await _start_session(host)
    try:
        await _stop_send_queue_worker(host)
        request = Envelope(
            request_id=42,
            message_type=MessageType.REQUEST,
            method="cap.ping",
            payload={"ping": True},
        )
        await connection.inject_frame(codec.encode_envelope(request))
        reply_data = await asyncio.wait_for(connection.take_sent_frame(), timeout=_WAIT_SECONDS)
        reply = codec.decode_envelope(reply_data)
        assert reply.is_response()
        assert reply.request_id == 42
        assert reply.method == "cap.ping"
        assert reply.payload.get("pong") is True
        assert reply.error is None
    finally:
        await _stop_session(host, start_task)


@pytest.mark.asyncio
async def test_send_request_pending_correlation() -> None:
    """send_request 应按 request_id 把 RESPONSE 关联回 pending future。"""
    codec = MsgPackCodec()
    connection = _make_loopback_connection()
    session = _make_session(connection, "runner", codec)
    start_task = await _start_session(session)
    request_task: Optional[asyncio.Task[Envelope]] = None
    try:
        request_task = asyncio.create_task(
            session.send_request("plugin.health", payload={"probe": True}, timeout_ms=5000)
        )
        sent = await asyncio.wait_for(connection.take_sent_frame(), timeout=_WAIT_SECONDS)
        request = codec.decode_envelope(sent)
        assert request.is_request()
        assert request.method == "plugin.health"
        await _wait_until(lambda: _pending_count(session) == 1)
        assert request.request_id in _pending_mapping(session)

        await connection.inject_frame(
            codec.encode_envelope(request.make_response(payload={"healthy": True}))
        )
        response = await asyncio.wait_for(request_task, timeout=_WAIT_SECONDS)
        assert response.is_response()
        assert response.request_id == request.request_id
        assert response.payload.get("healthy") is True
        assert _pending_count(session) == 0
    finally:
        if request_task is not None and not request_task.done():
            request_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await request_task
        await _stop_session(session, start_task)


@pytest.mark.asyncio
async def test_host_disconnect_fails_pending_with_plugin_crashed() -> None:
    """Host 断线应把等待中的请求失败为 E_PLUGIN_CRASHED。"""
    codec = MsgPackCodec()
    connection = _make_loopback_connection()
    host = _make_session(connection, "host", codec)
    start_task = await _start_session(host)
    request_task: Optional[asyncio.Task[Envelope]] = None
    try:
        request_task = asyncio.create_task(
            host.send_request("plugin.health", payload={"probe": True}, timeout_ms=30000)
        )
        await _wait_until(lambda: _pending_count(host) == 1)
        await host.disconnect()
        with pytest.raises(RPCError) as exc_info:
            await asyncio.wait_for(request_task, timeout=_WAIT_SECONDS)
        assert exc_info.value.code == ErrorCode.E_PLUGIN_CRASHED
        assert _pending_count(host) == 0
    finally:
        if request_task is not None and not request_task.done():
            request_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await request_task
        await _stop_session(host, start_task)


@pytest.mark.asyncio
async def test_runner_disconnect_fails_pending_with_timeout() -> None:
    """Runner 断线应把等待中的请求失败为 E_TIMEOUT。"""
    codec = MsgPackCodec()
    connection = _make_loopback_connection()
    runner = _make_session(connection, "runner", codec)
    start_task = await _start_session(runner)
    request_task: Optional[asyncio.Task[Envelope]] = None
    try:
        request_task = asyncio.create_task(
            runner.send_request("cap.db_query", payload={"probe": True}, timeout_ms=30000)
        )
        await _wait_until(lambda: _pending_count(runner) == 1)
        await runner.disconnect()
        with pytest.raises(RPCError) as exc_info:
            await asyncio.wait_for(request_task, timeout=_WAIT_SECONDS)
        assert exc_info.value.code == ErrorCode.E_TIMEOUT
        assert _pending_count(runner) == 0
    finally:
        if request_task is not None and not request_task.done():
            request_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await request_task
        await _stop_session(runner, start_task)
