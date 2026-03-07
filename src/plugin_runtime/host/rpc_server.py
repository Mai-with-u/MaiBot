"""Host 端 RPC Server

负责：
1. 监听 Runner 连接
2. 处理握手（runner.hello）
3. 分发调用请求给 Runner / 处理 Runner 的能力调用
4. 请求-响应关联与超时管理
"""

from typing import Any, Callable, Awaitable

import asyncio
import secrets

from src.common.logger import get_logger
from src.plugin_runtime.protocol.codec import Codec, MsgPackCodec
from src.plugin_runtime.protocol.envelope import (
    PROTOCOL_VERSION,
    MIN_SDK_VERSION,
    MAX_SDK_VERSION,
    Envelope,
    HelloPayload,
    HelloResponsePayload,
    MessageType,
    RequestIdGenerator,
)
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.transport.base import Connection, TransportServer

logger = get_logger("plugin_runtime.host.rpc_server")

# RPC 方法处理器类型
MethodHandler = Callable[[Envelope], Awaitable[Envelope]]


class RPCServer:
    """Host 端 RPC 服务器

    管理与 Runner 的 IPC 连接，处理双向 RPC 调用。
    """

    def __init__(
        self,
        transport: TransportServer,
        session_token: str | None = None,
        codec: Codec | None = None,
        send_queue_size: int = 128,
    ):
        self._transport = transport
        self._session_token = session_token or secrets.token_hex(32)
        self._codec = codec or MsgPackCodec()
        self._send_queue_size = send_queue_size

        self._id_gen = RequestIdGenerator()
        self._connection: Connection | None = None  # 当前活跃的 Runner 连接
        self._runner_id: str | None = None
        self._runner_generation: int = 0

        # 方法处理器注册表
        self._method_handlers: dict[str, MethodHandler] = {}

        # 等待响应的 pending 请求: request_id -> Future
        self._pending_requests: dict[int, asyncio.Future] = {}

        # 发送队列（背压控制）
        self._send_queue: asyncio.Queue[bytes] | None = None

        # 运行状态
        self._running: bool = False
        self._tasks: list[asyncio.Task] = []

    @property
    def session_token(self) -> str:
        return self._session_token

    @property
    def is_connected(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

    def register_method(self, method: str, handler: MethodHandler) -> None:
        """注册 RPC 方法处理器"""
        self._method_handlers[method] = handler

    async def start(self) -> None:
        """启动 RPC 服务器"""
        self._running = True
        self._send_queue = asyncio.Queue(maxsize=self._send_queue_size)
        await self._transport.start(self._handle_connection)
        logger.info(f"RPC Server 已启动，监听地址: {self._transport.get_address()}")

    async def stop(self) -> None:
        """停止 RPC 服务器"""
        self._running = False

        # 取消所有 pending 请求
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(RPCError(ErrorCode.E_TIMEOUT, "服务器关闭"))
        self._pending_requests.clear()

        # 取消后台任务
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        # 关闭连接
        if self._connection:
            await self._connection.close()
            self._connection = None

        await self._transport.stop()
        logger.info("RPC Server 已停止")

    async def send_request(
        self,
        method: str,
        plugin_id: str = "",
        payload: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> Envelope:
        """向 Runner 发送 RPC 请求并等待响应

        Args:
            method: RPC 方法名
            plugin_id: 目标插件 ID
            payload: 请求数据
            timeout_ms: 超时时间(ms)

        Returns:
            响应 Envelope

        Raises:
            RPCError: 调用失败
        """
        if not self.is_connected:
            raise RPCError(ErrorCode.E_PLUGIN_CRASHED, "Runner 未连接")

        request_id = self._id_gen.next()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.REQUEST,
            method=method,
            plugin_id=plugin_id,
            generation=self._runner_generation,
            timeout_ms=timeout_ms,
            payload=payload or {},
        )

        # 背压检查
        if self._send_queue and self._send_queue.full():
            raise RPCError(ErrorCode.E_BACKPRESSURE, "发送队列已满")

        # 注册 pending future
        loop = asyncio.get_event_loop()
        future: asyncio.Future[Envelope] = loop.create_future()
        self._pending_requests[request_id] = future

        try:
            # 发送请求
            data = self._codec.encode_envelope(envelope)
            await self._connection.send_frame(data)

            # 等待响应
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

    async def send_event(self, method: str, plugin_id: str = "", payload: dict[str, Any] | None = None) -> None:
        """向 Runner 发送单向事件（不等待响应）"""
        if not self.is_connected:
            return

        request_id = self._id_gen.next()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.EVENT,
            method=method,
            plugin_id=plugin_id,
            generation=self._runner_generation,
            payload=payload or {},
        )
        data = self._codec.encode_envelope(envelope)
        await self._connection.send_frame(data)

    # ─── 内部方法 ──────────────────────────────────────────────

    async def _handle_connection(self, conn: Connection) -> None:
        """处理新的 Runner 连接"""
        logger.info("收到 Runner 连接")

        # 第一条消息必须是 runner.hello 握手
        try:
            handshake_ok = await self._handle_handshake(conn)
            if not handshake_ok:
                await conn.close()
                return
        except Exception as e:
            logger.error(f"握手失败: {e}")
            await conn.close()
            return

        # 握手成功，保存连接
        self._connection = conn
        logger.info(f"Runner 握手成功: runner_id={self._runner_id}, generation={self._runner_generation}")

        # 启动消息接收循环
        try:
            await self._recv_loop(conn)
        except Exception as e:
            logger.error(f"连接异常断开: {e}")
        finally:
            self._connection = None
            self._runner_id = None

    async def _handle_handshake(self, conn: Connection) -> bool:
        """处理 runner.hello 握手"""
        # 接收握手请求
        data = await asyncio.wait_for(conn.recv_frame(), timeout=10.0)
        envelope = self._codec.decode_envelope(data)

        if envelope.method != "runner.hello":
            logger.error(f"期望 runner.hello，收到 {envelope.method}")
            error_resp = envelope.make_error_response(
                ErrorCode.E_PROTOCOL_MISMATCH.value,
                "首条消息必须为 runner.hello",
            )
            await conn.send_frame(self._codec.encode_envelope(error_resp))
            return False

        # 解析握手 payload
        hello = HelloPayload.model_validate(envelope.payload)

        # 校验会话令牌
        if hello.session_token != self._session_token:
            logger.error("会话令牌不匹配")
            resp_payload = HelloResponsePayload(
                accepted=False,
                reason="会话令牌无效",
            )
            resp = envelope.make_response(payload=resp_payload.model_dump())
            await conn.send_frame(self._codec.encode_envelope(resp))
            return False

        # 校验 SDK 版本
        if not self._check_sdk_version(hello.sdk_version):
            logger.error(f"SDK 版本不兼容: {hello.sdk_version}")
            resp_payload = HelloResponsePayload(
                accepted=False,
                reason=f"SDK 版本 {hello.sdk_version} 不在支持范围 [{MIN_SDK_VERSION}, {MAX_SDK_VERSION}]",
            )
            resp = envelope.make_response(payload=resp_payload.model_dump())
            await conn.send_frame(self._codec.encode_envelope(resp))
            return False

        # 握手成功
        self._runner_id = hello.runner_id
        self._runner_generation += 1

        resp_payload = HelloResponsePayload(
            accepted=True,
            host_version=PROTOCOL_VERSION,
            assigned_generation=self._runner_generation,
        )
        resp = envelope.make_response(payload=resp_payload.model_dump())
        await conn.send_frame(self._codec.encode_envelope(resp))

        return True

    async def _recv_loop(self, conn: Connection) -> None:
        """消息接收主循环"""
        while self._running and not conn.is_closed:
            try:
                data = await conn.recv_frame()
            except (asyncio.IncompleteReadError, ConnectionError):
                logger.info("Runner 连接已断开")
                break
            except Exception as e:
                logger.error(f"接收帧失败: {e}")
                break

            try:
                envelope = self._codec.decode_envelope(data)
            except Exception as e:
                logger.error(f"解码消息失败: {e}")
                continue

            # 分发消息
            if envelope.is_response():
                self._handle_response(envelope)
            elif envelope.is_request():
                # 异步处理请求（Runner 发来的能力调用）
                task = asyncio.create_task(self._handle_request(envelope, conn))
                self._tasks.append(task)
                task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)
            elif envelope.is_event():
                task = asyncio.create_task(self._handle_event(envelope))
                self._tasks.append(task)
                task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)

    def _handle_response(self, envelope: Envelope) -> None:
        """处理来自 Runner 的响应"""
        future = self._pending_requests.pop(envelope.request_id, None)
        if future and not future.done():
            if envelope.error:
                future.set_exception(RPCError.from_dict(envelope.error))
            else:
                future.set_result(envelope)

    async def _handle_request(self, envelope: Envelope, conn: Connection) -> None:
        """处理来自 Runner 的请求（通常是能力调用 cap.*）"""
        handler = self._method_handlers.get(envelope.method)
        if handler is None:
            error_resp = envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"未注册的方法: {envelope.method}",
            )
            await conn.send_frame(self._codec.encode_envelope(error_resp))
            return

        try:
            response = await handler(envelope)
            await conn.send_frame(self._codec.encode_envelope(response))
        except RPCError as e:
            error_resp = envelope.make_error_response(e.code.value, e.message, e.details)
            await conn.send_frame(self._codec.encode_envelope(error_resp))
        except Exception as e:
            logger.error(f"处理请求 {envelope.method} 异常: {e}", exc_info=True)
            error_resp = envelope.make_error_response(ErrorCode.E_UNKNOWN.value, str(e))
            await conn.send_frame(self._codec.encode_envelope(error_resp))

    async def _handle_event(self, envelope: Envelope) -> None:
        """处理来自 Runner 的事件"""
        if handler := self._method_handlers.get(envelope.method):
            try:
                await handler(envelope)
            except Exception as e:
                logger.error(f"处理事件 {envelope.method} 异常: {e}", exc_info=True)

    @staticmethod
    def _check_sdk_version(sdk_version: str) -> bool:
        """检查 SDK 版本是否在支持范围内"""
        try:
            sdk_parts = [int(x) for x in sdk_version.split(".")]
            min_parts = [int(x) for x in MIN_SDK_VERSION.split(".")]
            max_parts = [int(x) for x in MAX_SDK_VERSION.split(".")]
            return min_parts <= sdk_parts <= max_parts
        except (ValueError, AttributeError):
            return False
