"""Runner 端 RPC Client

负责：
1. 连接 Host RPC Server
2. 发送握手（runner.hello）
3. 发送组件注册请求
4. 接收并分发 Host 的调用请求
5. 发送能力调用请求到 Host
"""

from typing import Any, Callable, Awaitable

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

# RPC 方法处理器类型
MethodHandler = Callable[[Envelope], Awaitable[Envelope]]

SDK_VERSION = "1.0.0"


class RPCClient:
    """Runner 端 RPC 客户端

    管理与 Host 的 IPC 连接，支持双向 RPC 调用。
    """

    def __init__(
        self,
        host_address: str,
        session_token: str,
        codec: Codec | None = None,
    ):
        self._host_address = host_address
        self._session_token = session_token
        self._codec = codec or MsgPackCodec()

        self._id_gen = RequestIdGenerator()
        self._connection: Connection | None = None
        self._runner_id = str(uuid.uuid4())
        self._generation: int = 0

        # 方法处理器注册表（Host 发来的调用）
        self._method_handlers: dict[str, MethodHandler] = {}

        # 等待响应的 pending 请求: request_id -> Future
        self._pending_requests: dict[int, asyncio.Future] = {}

        # 运行状态
        self._running = False
        self._recv_task: asyncio.Task | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def is_connected(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

    def register_method(self, method: str, handler: MethodHandler) -> None:
        """注册方法处理器（处理 Host 发来的请求）"""
        self._method_handlers[method] = handler

    async def connect_and_handshake(self) -> bool:
        """连接 Host 并完成握手

        Returns:
            是否握手成功
        """
        client = create_transport_client(self._host_address)
        self._connection = await client.connect()

        # 发送 runner.hello
        hello = HelloPayload(
            runner_id=self._runner_id,
            sdk_version=SDK_VERSION,
            session_token=self._session_token,
        )
        request_id = self._id_gen.next()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.REQUEST,
            method="runner.hello",
            payload=hello.model_dump(),
        )

        data = self._codec.encode_envelope(envelope)
        await self._connection.send_frame(data)

        # 接收握手响应
        resp_data = await asyncio.wait_for(self._connection.recv_frame(), timeout=10.0)
        resp = self._codec.decode_envelope(resp_data)

        resp_payload = HelloResponsePayload.model_validate(resp.payload)
        if not resp_payload.accepted:
            logger.error(f"握手被拒绝: {resp_payload.reason}")
            await self._connection.close()
            self._connection = None
            return False

        self._generation = resp_payload.assigned_generation
        logger.info(f"握手成功: generation={self._generation}, host_version={resp_payload.host_version}")

        # 启动消息接收循环
        self._running = True
        self._recv_task = asyncio.create_task(self._recv_loop())

        return True

    async def disconnect(self) -> None:
        """断开连接"""
        self._running = False
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None

        # 取消所有 pending 请求
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(RPCError(ErrorCode.E_TIMEOUT, "连接关闭"))
        self._pending_requests.clear()

        if self._connection:
            await self._connection.close()
            self._connection = None

    async def send_request(
        self,
        method: str,
        plugin_id: str = "",
        payload: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> Envelope:
        """向 Host 发送 RPC 请求并等待响应"""
        if not self.is_connected:
            raise RPCError(ErrorCode.E_UNKNOWN, "未连接到 Host")

        request_id = self._id_gen.next()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.REQUEST,
            method=method,
            plugin_id=plugin_id,
            generation=self._generation,
            timeout_ms=timeout_ms,
            payload=payload or {},
        )

        loop = asyncio.get_event_loop()
        future: asyncio.Future[Envelope] = loop.create_future()
        self._pending_requests[request_id] = future

        try:
            data = self._codec.encode_envelope(envelope)
            await self._connection.send_frame(data)

            timeout_sec = timeout_ms / 1000.0
            return await asyncio.wait_for(future, timeout=timeout_sec)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise RPCError(ErrorCode.E_TIMEOUT, f"请求 {method} 超时 ({timeout_ms}ms)") from None
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            if isinstance(e, RPCError):
                raise
            raise RPCError(ErrorCode.E_UNKNOWN, str(e)) from e

    # ─── 内部方法 ──────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """消息接收主循环"""
        while self._running and self._connection and not self._connection.is_closed:
            try:
                data = await self._connection.recv_frame()
            except (asyncio.IncompleteReadError, ConnectionError):
                logger.info("Host 连接已断开")
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"接收帧失败: {e}")
                break

            try:
                envelope = self._codec.decode_envelope(data)
            except Exception as e:
                logger.error(f"解码消息失败: {e}")
                continue

            if envelope.is_response():
                self._handle_response(envelope)
            elif envelope.is_request():
                asyncio.create_task(self._handle_request(envelope))
            elif envelope.is_event():
                asyncio.create_task(self._handle_event(envelope))

    def _handle_response(self, envelope: Envelope) -> None:
        """处理来自 Host 的响应"""
        future = self._pending_requests.pop(envelope.request_id, None)
        if future and not future.done():
            if envelope.error:
                future.set_exception(RPCError.from_dict(envelope.error))
            else:
                future.set_result(envelope)

    async def _handle_request(self, envelope: Envelope) -> None:
        """处理来自 Host 的请求（调用插件组件）"""
        handler = self._method_handlers.get(envelope.method)
        if handler is None:
            error_resp = envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"未注册的方法: {envelope.method}",
            )
            await self._connection.send_frame(self._codec.encode_envelope(error_resp))
            return

        try:
            response = await handler(envelope)
            await self._connection.send_frame(self._codec.encode_envelope(response))
        except RPCError as e:
            error_resp = envelope.make_error_response(e.code.value, e.message, e.details)
            await self._connection.send_frame(self._codec.encode_envelope(error_resp))
        except Exception as e:
            logger.error(f"处理请求 {envelope.method} 异常: {e}", exc_info=True)
            error_resp = envelope.make_error_response(ErrorCode.E_UNKNOWN.value, str(e))
            await self._connection.send_frame(self._codec.encode_envelope(error_resp))

    async def _handle_event(self, envelope: Envelope) -> None:
        """处理来自 Host 的事件"""
        if handler := self._method_handlers.get(envelope.method):
            try:
                await handler(envelope)
            except Exception as e:
                logger.error(f"处理事件 {envelope.method} 异常: {e}", exc_info=True)
