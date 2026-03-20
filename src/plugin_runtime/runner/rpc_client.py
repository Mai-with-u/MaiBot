"""Runner 端 RPC 客户端。"""

from typing import Any, Awaitable, Callable, Dict, Optional, Set, cast

import asyncio
import contextlib
import uuid

from src.common.logger import get_logger
from src.plugin_runtime.protocol.codec import Codec, MsgPackCodec
from src.plugin_runtime.protocol.envelope import (
    Envelope,
    HelloPayload,
    HelloResponsePayload,
    MessageType,
    RequestIdGenerator,
)
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.transport.base import Connection
from src.plugin_runtime.transport.factory import create_transport_client

logger = get_logger("plugin_runtime.runner.rpc_client")

MethodHandler = Callable[[Envelope], Awaitable[Envelope]]


def _get_sdk_version() -> str:
    """读取 SDK 版本号。

    Returns:
        str: 已安装的 SDK 版本；读取失败时回退到 ``1.0.0``。
    """
    try:
        from importlib.metadata import version

        return version("maibot-plugin-sdk")
    except Exception:
        return "1.0.0"


SDK_VERSION = _get_sdk_version()


class RPCClient:
    """Runner 端 RPC 客户端。"""

    def __init__(
        self,
        host_address: str,
        session_token: str,
        codec: Optional[Codec] = None,
    ) -> None:
        """初始化 RPC 客户端。

        Args:
            host_address: Host 的 IPC 地址。
            session_token: 握手用会话令牌。
            codec: 可选的编解码器实现。
        """
        self._host_address: str = host_address
        self._session_token: str = session_token
        self._codec: Codec = codec or MsgPackCodec()

        self._id_gen = RequestIdGenerator()
        self._connection: Optional[Connection] = None
        self._runner_id: str = str(uuid.uuid4())
        self._method_handlers: Dict[str, MethodHandler] = {}
        self._pending_requests: Dict[int, asyncio.Future[Envelope]] = {}
        self._running: bool = False
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._background_tasks: Set[asyncio.Task[Any]] = set()

    @property
    def is_connected(self) -> bool:
        """返回当前连接是否可用。"""
        return self._connection is not None and not self._connection.is_closed

    def register_method(self, method: str, handler: MethodHandler) -> None:
        """注册 Host -> Runner 的 RPC 处理器。

        Args:
            method: RPC 方法名。
            handler: 方法处理函数。
        """
        self._method_handlers[method] = handler

    def _require_connection(self) -> Connection:
        """返回当前可用连接。

        Returns:
            Connection: 当前连接对象。

        Raises:
            RPCError: 当前未连接到 Host。
        """
        connection = self._connection
        if connection is None or connection.is_closed:
            raise RPCError(ErrorCode.E_UNKNOWN, "未连接到 Host")
        return cast(Connection, connection)

    async def connect_and_handshake(self) -> bool:
        """连接 Host 并完成握手。

        Returns:
            bool: 是否握手成功。
        """
        client = create_transport_client(self._host_address)
        self._connection = await client.connect()
        connection = self._require_connection()

        hello = HelloPayload(
            runner_id=self._runner_id,
            sdk_version=SDK_VERSION,
            session_token=self._session_token,
        )
        request_id = await self._id_gen.next()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.REQUEST,
            method="runner.hello",
            payload=hello.model_dump(),
        )

        await connection.send_frame(self._codec.encode_envelope(envelope))

        resp_data = await asyncio.wait_for(connection.recv_frame(), timeout=10.0)
        response = self._codec.decode_envelope(resp_data)
        resp_payload = HelloResponsePayload.model_validate(response.payload)

        if not resp_payload.accepted:
            logger.error(f"握手被拒绝: {resp_payload.reason}")
            await self.disconnect()
            return False

        logger.info(f"握手成功: host_version={resp_payload.host_version}")
        self._running = True
        self._recv_task = asyncio.create_task(self._recv_loop(), name="RPCClient.recv")
        return True

    async def disconnect(self) -> None:
        """断开与 Host 的连接并清理状态。"""
        self._running = False

        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None

        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(RPCError(ErrorCode.E_TIMEOUT, "连接关闭"))
        self._pending_requests.clear()

        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def send_request(
        self,
        method: str,
        plugin_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Envelope:
        """向 Host 发送 RPC 请求并等待响应。

        Args:
            method: RPC 方法名。
            plugin_id: 目标插件 ID。
            payload: 请求载荷。
            timeout_ms: 超时时间，单位毫秒。

        Returns:
            Envelope: Host 返回的响应信封。

        Raises:
            RPCError: 发送失败、超时或连接异常。
        """
        connection = self._require_connection()
        request_id = await self._id_gen.next()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.REQUEST,
            method=method,
            plugin_id=plugin_id,
            timeout_ms=timeout_ms,
            payload=payload or {},
        )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Envelope] = loop.create_future()
        self._pending_requests[request_id] = future

        try:
            await connection.send_frame(self._codec.encode_envelope(envelope))
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise RPCError(ErrorCode.E_TIMEOUT, f"请求 {method} 超时 ({timeout_ms}ms)") from None
        except Exception as exc:
            self._pending_requests.pop(request_id, None)
            if isinstance(exc, RPCError):
                raise
            raise RPCError(ErrorCode.E_UNKNOWN, str(exc)) from exc

    async def send_event(
        self,
        method: str,
        plugin_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """向 Host 发送单向广播消息。

        Args:
            method: RPC 方法名。
            plugin_id: 目标插件 ID。
            payload: 广播载荷。
        """
        if not self.is_connected:
            return

        connection = self._require_connection()
        request_id = await self._id_gen.next()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.BROADCAST,
            method=method,
            plugin_id=plugin_id,
            payload=payload or {},
        )
        await connection.send_frame(self._codec.encode_envelope(envelope))

    async def _recv_loop(self) -> None:
        """持续接收 Host 发来的消息并分发。"""
        while self._running and self._connection is not None and not self._connection.is_closed:
            try:
                data = await self._connection.recv_frame()
            except (asyncio.IncompleteReadError, ConnectionError):
                logger.info("Host 连接已断开")
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"接收帧失败: {exc}")
                break

            try:
                envelope = self._codec.decode_envelope(data)
            except Exception as exc:
                logger.error(f"解码消息失败: {exc}")
                continue

            if envelope.is_response():
                self._handle_response(envelope)
            elif envelope.is_request():
                self._track_background_task(asyncio.create_task(self._handle_request(envelope)))
            elif envelope.is_broadcast():
                self._track_background_task(asyncio.create_task(self._handle_broadcast(envelope)))

    def _handle_response(self, envelope: Envelope) -> None:
        """处理 Host 返回的响应。

        Args:
            envelope: 响应信封。
        """
        future = self._pending_requests.pop(envelope.request_id, None)
        if future is None or future.done():
            return
        if envelope.error:
            future.set_exception(RPCError.from_dict(envelope.error))
        else:
            future.set_result(envelope)

    async def _handle_request(self, envelope: Envelope) -> None:
        """处理 Host 发来的请求。

        Args:
            envelope: 请求信封。
        """
        connection = self._connection
        if connection is None or connection.is_closed:
            logger.warning(f"处理请求 {envelope.method} 时连接已关闭，跳过响应")
            return

        handler = self._method_handlers.get(envelope.method)
        if handler is None:
            error_resp = envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"未注册的方法: {envelope.method}",
            )
            await connection.send_frame(self._codec.encode_envelope(error_resp))
            return

        try:
            response = await handler(envelope)
            await connection.send_frame(self._codec.encode_envelope(response))
        except RPCError as exc:
            error_resp = envelope.make_error_response(exc.code.value, exc.message, exc.details)
            await connection.send_frame(self._codec.encode_envelope(error_resp))
        except Exception as exc:
            logger.error(f"处理请求 {envelope.method} 异常: {exc}", exc_info=True)
            error_resp = envelope.make_error_response(ErrorCode.E_UNKNOWN.value, str(exc))
            await connection.send_frame(self._codec.encode_envelope(error_resp))

    async def _handle_broadcast(self, envelope: Envelope) -> None:
        """处理 Host 发来的广播事件。

        Args:
            envelope: 广播信封。
        """
        handler = self._method_handlers.get(envelope.method)
        if handler is None:
            return

        try:
            await handler(envelope)
        except Exception as exc:
            logger.error(f"处理广播 {envelope.method} 异常: {exc}", exc_info=True)

    def _track_background_task(self, task: asyncio.Task[Any]) -> None:
        """持有后台任务强引用直到其结束。

        Args:
            task: 后台任务。
        """
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
