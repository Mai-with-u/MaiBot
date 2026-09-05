"""Host 与 Runner 共享的 RPC 会话核心。

负责 pending 请求、方法注册、recv 分发、codec 封装和 send_request。
握手仍在 recv_loop 之外直写 Connection.send_frame；Host / Runner 写入路径不统一。
"""

from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Set, Tuple

import asyncio
import contextlib
import re
import time

from src.common.logger import get_logger
from src.plugin_runtime.protocol.codec import Codec, MsgPackCodec
from src.plugin_runtime.protocol.envelope import (
    Envelope,
    HelloPayload,
    HelloResponsePayload,
    MAX_SDK_VERSION,
    MIN_SDK_VERSION,
    MessageType,
    RequestIdGenerator,
)
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.transport.base import Connection

MethodHandler = Callable[[Envelope], Awaitable[Envelope]]
SendBytes = Callable[[bytes], Awaitable[None]]
SpawnTask = Callable[[Coroutine[Any, Any, None]], None]
OnInboundRequest = Callable[[Envelope], Coroutine[Any, Any, None]]

_HOST_LOGGER_NAME = "plugin_runtime.host.rpc_server"
_RUNNER_LOGGER_NAME = "plugin_runtime.runner.rpc_client"


class RpcSession:
    """按 role 区分 Host / Runner 行为的共享 RPC 会话。"""

    def __init__(
        self,
        connection: Optional[Connection] = None,
        *,
        role: str,
        codec: Optional[Codec] = None,
        session_token: str = "",
        host_version: str = "",
        sdk_version: str = "",
        runner_id: str = "",
        logger: Any = None,
        send_queue_size: int = 128,
        enable_pending_metadata: Optional[bool] = None,
    ) -> None:
        if role not in ("host", "runner"):
            raise ValueError(f"未知 RPC 角色: {role}")

        self._role = role
        self._connection = connection
        self._codec = codec or MsgPackCodec()
        self._session_token = session_token
        self._host_version = host_version
        self._sdk_version = sdk_version
        self._runner_id = runner_id
        self._send_queue_size = send_queue_size
        self._logger = logger or get_logger(_HOST_LOGGER_NAME if self.is_host else _RUNNER_LOGGER_NAME)

        # pending metadata 仅 Host 默认打开，供 RPCServer 诊断快照使用
        self._enable_pending_metadata = self.is_host if enable_pending_metadata is None else enable_pending_metadata

        self._id_gen = RequestIdGenerator()
        self._method_handlers: Dict[str, MethodHandler] = {}
        self._pending_requests: Dict[int, asyncio.Future[Envelope]] = {}
        self._pending_request_metadata: Dict[int, Dict[str, Any]] = {}

        self._send_queue: Optional[asyncio.Queue[Tuple[Connection, bytes, asyncio.Future[None]]]] = None
        self._send_worker_task: Optional[asyncio.Task[None]] = None

        self._running: bool = False
        self._tasks: List[asyncio.Task[None]] = []
        self._background_tasks: Set[asyncio.Task[Any]] = set()
        self._last_handshake_rejection_reason: str = ""

    @property
    def is_host(self) -> bool:
        return self._role == "host"

    @property
    def session_token(self) -> str:
        return self._session_token

    @property
    def connection(self) -> Optional[Connection]:
        return self._connection

    @property
    def is_connected(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

    @property
    def last_handshake_rejection_reason(self) -> str:
        return self._last_handshake_rejection_reason

    def set_connection(self, connection: Connection) -> None:
        """绑定当前活跃连接。握手成功后由 facade 调用。"""
        self._connection = connection

    def clear_connection(self) -> None:
        """清除当前活跃连接，不关闭底层传输。"""
        self._connection = None

    def clear_handshake_state(self) -> None:
        """清空最近一次握手拒绝状态。"""
        self._last_handshake_rejection_reason = ""

    def register_method(self, method: str, handler: MethodHandler) -> None:
        """注册 RPC 方法处理器。"""
        self._method_handlers[method] = handler

    def encode_envelope(self, envelope: Envelope) -> bytes:
        """将信封编码为传输帧 payload。"""
        return self._codec.encode_envelope(envelope)

    def decode_envelope(self, data: bytes) -> Envelope:
        """将传输帧 payload 解码为信封。"""
        return self._codec.decode_envelope(data)

    async def next_request_id(self) -> int:
        """分配下一个请求 ID。握手不得把该 ID 写入 pending map。"""
        return await self._id_gen.next()

    def mark_running(self) -> None:
        """允许 recv_loop 继续读取。"""
        self._running = True

    def mark_stopped(self) -> None:
        """请求 recv_loop 退出。"""
        self._running = False

    def require_connection(self) -> Connection:
        """返回当前可用连接。"""
        connection = self._connection
        if connection is None or connection.is_closed:
            if self.is_host:
                raise RPCError(ErrorCode.E_PLUGIN_CRASHED, "Runner 未连接")
            raise RPCError(ErrorCode.E_UNKNOWN, "未连接到 Host")
        return connection

    @staticmethod
    def _summarize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """提取请求 payload 摘要，避免诊断记录写入完整消息内容。"""

        if not isinstance(payload, dict):
            return {}

        summary: Dict[str, Any] = {"payload_keys": sorted(str(key) for key in payload.keys())}
        if component_name := str(payload.get("component_name") or "").strip():
            summary["component_name"] = component_name
        args = payload.get("args")
        if isinstance(args, dict):
            summary["args_keys"] = sorted(str(key) for key in args.keys())
            summary["args_count"] = len(args)
        if client_type := str(payload.get("client_type") or "").strip():
            summary["client_type"] = client_type
        if operation := str(payload.get("operation") or "").strip():
            summary["operation"] = operation
        return summary

    def build_pending_request_snapshot(self, min_duration_ms: int = 0) -> List[Dict[str, Any]]:
        """组装 pending 元数据快照。Supervisor 诊断仍通过 RPCServer 对外暴露。"""

        now_monotonic = time.monotonic()
        snapshot: List[Dict[str, Any]] = []
        for metadata in self._pending_request_metadata.values():
            duration_ms = int((now_monotonic - float(metadata.get("started_at_monotonic", now_monotonic))) * 1000)
            if duration_ms < min_duration_ms:
                continue
            item = dict(metadata)
            item.pop("started_at_monotonic", None)
            item["duration_ms"] = duration_ms
            snapshot.append(item)
        snapshot.sort(key=lambda item: int(item.get("duration_ms", 0)), reverse=True)
        return snapshot

    async def start(self) -> None:
        """启动会话：Host 拉起 send queue worker，然后进入 recv_loop。"""
        self._running = True
        if self.is_host:
            self.ensure_host_send_worker()
        await self.run_recv_loop(self._connection)

    async def handshake(self, conn: Optional[Connection] = None) -> bool:
        """在 recv_loop 之外完成 runner.hello，直写 send_frame，不进 pending。"""
        connection = conn if conn is not None else self._connection
        if connection is None:
            raise RPCError(
                ErrorCode.E_PLUGIN_CRASHED if self.is_host else ErrorCode.E_UNKNOWN,
                "Runner 未连接" if self.is_host else "未连接到 Host",
            )
        if self.is_host:
            return await self._handshake_as_host(connection)
        return await self._handshake_as_runner(connection)

    async def send_request(
        self,
        method: str,
        plugin_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
        *,
        send_bytes: Optional[SendBytes] = None,
    ) -> Envelope:
        """构造请求、登记 pending，并经角色自己的写入路径发出。"""
        if send_bytes is None:
            if self.is_host:
                if not self._connection or self._connection.is_closed:
                    raise RPCError(ErrorCode.E_PLUGIN_CRASHED, "Runner 未连接")
                send_bytes = self._write_outbound_request
            else:
                connection = self.require_connection()
                send_bytes = connection.send_frame

        request_id = await self._id_gen.next()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.REQUEST,
            method=method,
            plugin_id=plugin_id,
            timeout_ms=timeout_ms,
            payload=payload or {},
        )

        # 注册 pending future
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Envelope] = loop.create_future()
        self._pending_requests[request_id] = future
        if self._enable_pending_metadata:
            self._pending_request_metadata[request_id] = {
                "request_id": request_id,
                "method": method,
                "plugin_id": plugin_id,
                "timeout_ms": timeout_ms,
                "started_at_epoch": time.time(),
                "started_at_monotonic": time.monotonic(),
                "payload_summary": self._summarize_payload(payload),
            }

        try:
            # 发送请求
            await send_bytes(self.encode_envelope(envelope))

            # 等待响应
            timeout_sec = timeout_ms / 1000.0
            return await asyncio.wait_for(future, timeout=timeout_sec)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            self._pending_request_metadata.pop(request_id, None)
            raise RPCError(ErrorCode.E_TIMEOUT, f"请求 {method} 超时 ({timeout_ms}ms)") from None
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            self._pending_request_metadata.pop(request_id, None)
            if isinstance(e, RPCError):
                raise
            raise RPCError(ErrorCode.E_UNKNOWN, str(e)) from e

    def dispatch_inbound(
        self,
        envelope: Envelope,
        spawn: SpawnTask,
        on_request: OnInboundRequest,
    ) -> None:
        """分发已解码的入站信封。握手消息不得进入此路径。"""
        if envelope.is_response():
            self.handle_response(envelope)
        elif envelope.is_request():
            spawn(on_request(envelope))
        elif envelope.is_broadcast():
            spawn(self.handle_broadcast(envelope))
        elif self.is_host:
            self._logger.warning(f"未知的消息类型: {envelope.message_type}")

    def handle_response(self, envelope: Envelope) -> None:
        """完成与 request_id 对应的 pending future。"""
        pending_future = self._pending_requests.pop(envelope.request_id, None)
        self._pending_request_metadata.pop(envelope.request_id, None)
        if pending_future is None or pending_future.done():
            return
        if envelope.error:
            pending_future.set_exception(RPCError.from_dict(envelope.error))
        else:
            pending_future.set_result(envelope)

    async def handle_inbound_request(self, envelope: Envelope, conn: Connection) -> None:
        """处理入站 REQUEST，并直写传入的 conn，不走 send_request 队列。"""
        handler = self._method_handlers.get(envelope.method)
        if not handler:
            error_response = envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"未注册的方法: {envelope.method}",
            )
            await conn.send_frame(self.encode_envelope(error_response))
            return

        try:
            response = await handler(envelope)
            await conn.send_frame(self.encode_envelope(response))
        except RPCError as e:
            error_resp = envelope.make_error_response(e.code.value, e.message, e.details)
            await conn.send_frame(self.encode_envelope(error_resp))
        except Exception as e:
            self._logger.error(f"处理请求 {envelope.method} 异常: {e}", exc_info=True)
            error_resp = envelope.make_error_response(ErrorCode.E_UNKNOWN.value, str(e))
            await conn.send_frame(self.encode_envelope(error_resp))

    async def handle_broadcast(self, envelope: Envelope) -> None:
        """处理入站 BROADCAST。"""
        handler = self._method_handlers.get(envelope.method)
        if handler is None:
            return

        try:
            if self.is_host:
                result = await handler(envelope)
                # 检查 handler 返回的信封是否包含错误信息
                if result.error:
                    self._logger.warning(
                        f"事件 {envelope.method} handler 返回错误: {result.error.get('message', '')}"
                    )
            else:
                await handler(envelope)
        except Exception as e:
            log_label = "事件" if self.is_host else "广播"
            self._logger.error(f"处理{log_label} {envelope.method} 异常: {e}", exc_info=True)

    def fail_pending_requests(self, error_code: ErrorCode, message: str) -> int:
        """失败所有等待中的请求（如连接断开时）。"""
        aborted_request_count = 0
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(RPCError(error_code, message))
                aborted_request_count += 1
        self._pending_requests.clear()
        self._pending_request_metadata.clear()
        return aborted_request_count

    def fail_queued_sends(self, error_code: ErrorCode, message: str) -> int:
        """失败发送队列中尚未写出的 Host 出站请求。"""
        if self._send_queue is None:
            return 0

        failed_count = 0
        while True:
            try:
                _conn, _data, send_future = self._send_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if not send_future.done():
                send_future.set_exception(RPCError(error_code, message))
                failed_count += 1
            self._send_queue.task_done()

        return failed_count

    async def disconnect(self) -> None:
        """断开连接并按角色失败 pending 请求。"""
        self._running = False
        if self.is_host:
            self.fail_pending_requests(ErrorCode.E_PLUGIN_CRASHED, "Runner 连接已断开")
            self.fail_queued_sends(ErrorCode.E_PLUGIN_CRASHED, "Runner 连接已断开")
            self.cancel_background_tasks()
        else:
            for task in list(self._background_tasks):
                task.cancel()
            if self._background_tasks:
                with contextlib.suppress(Exception):
                    await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
            self.fail_pending_requests(ErrorCode.E_TIMEOUT, "连接关闭")
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
        await self.stop_host_send_worker()

    async def run_recv_loop(
        self,
        conn: Optional[Connection] = None,
        on_request: Optional[OnInboundRequest] = None,
    ) -> None:
        """消息接收主循环。握手不得走这条路径。"""
        connection = conn if conn is not None else self._connection
        if connection is None:
            raise RPCError(
                ErrorCode.E_PLUGIN_CRASHED if self.is_host else ErrorCode.E_UNKNOWN,
                "Runner 未连接" if self.is_host else "未连接到 Host",
            )

        request_handler = on_request
        if request_handler is None:
            if self.is_host:
                recv_conn = connection

                def host_on_request(envelope: Envelope) -> Coroutine[Any, Any, None]:
                    return self.handle_inbound_request(envelope, recv_conn)

                request_handler = host_on_request
            else:
                request_handler = self._handle_runner_request

        while self._recv_loop_should_continue(connection):
            try:
                if self.is_host:
                    data = await connection.recv_frame()
                else:
                    current = self._connection
                    if current is None:
                        break
                    data = await current.recv_frame()
            except (asyncio.IncompleteReadError, ConnectionError):
                if self.is_host:
                    self._logger.debug("Runner 连接已断开")
                else:
                    self._logger.info("Host 连接已断开")
                break
            except asyncio.CancelledError:
                if self.is_host:
                    raise
                break
            except Exception as e:
                self._logger.error(f"接收帧失败: {e}")
                break

            try:
                envelope = self.decode_envelope(data)
            except Exception as e:
                self._logger.error(f"解码消息失败: {e}")
                continue

            # 分发消息
            self.dispatch_inbound(
                envelope,
                spawn=self._spawn_background_task,
                on_request=request_handler,
            )

    def ensure_host_send_worker(self) -> None:
        """Host 出站 send_request 使用的发送队列 worker。"""
        if not self.is_host:
            return
        if self._send_queue is None:
            self._send_queue = asyncio.Queue(maxsize=self._send_queue_size)
        if self._send_worker_task is None or self._send_worker_task.done():
            self._send_worker_task = asyncio.create_task(self._send_loop())

    async def stop_host_send_worker(self) -> None:
        """停止 Host 发送队列 worker。"""
        if self._send_worker_task:
            self._send_worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._send_worker_task
            self._send_worker_task = None

    def cancel_background_tasks(self) -> None:
        """取消 Host 入站请求/广播后台任务，不等待结束。"""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def _handshake_as_host(self, conn: Connection) -> bool:
        """处理 runner.hello 握手。"""
        # 接收握手请求
        data = await asyncio.wait_for(conn.recv_frame(), timeout=10.0)
        envelope = self.decode_envelope(data)
        if envelope.method != "runner.hello":
            self._logger.error(f"期望 runner.hello，收到 {envelope.method}")
            self._last_handshake_rejection_reason = "首条消息必须为 runner.hello"
            error_resp = envelope.make_error_response(
                ErrorCode.E_PROTOCOL_MISMATCH.value,
                "首条消息必须为 runner.hello",
            )
            await conn.send_frame(self.encode_envelope(error_resp))
            return False

        # 解析握手 payload
        hello = HelloPayload.model_validate(envelope.payload)
        # 校验会话令牌
        if hello.session_token != self._session_token:
            self._logger.error("会话令牌不匹配")
            self._last_handshake_rejection_reason = "会话令牌无效"
            resp_payload = HelloResponsePayload(accepted=False, reason=self._last_handshake_rejection_reason)
            resp = envelope.make_response(payload=resp_payload.model_dump())
            await conn.send_frame(self.encode_envelope(resp))
            return False

        # 若已有活跃连接，直接拒绝新的握手，避免后来的连接抢占当前通道。
        if self.is_connected and self._connection is not conn:
            self._logger.warning("拒绝新的 Runner 连接：已有活跃连接")
            self._last_handshake_rejection_reason = "已有活跃 Runner 连接，拒绝新的握手"
            resp_payload = HelloResponsePayload(accepted=False, reason=self._last_handshake_rejection_reason)
            resp = envelope.make_response(payload=resp_payload.model_dump())
            await conn.send_frame(self.encode_envelope(resp))
            return False

        # 校验 SDK 版本
        if not self._check_sdk_version(hello.sdk_version):
            self._logger.error(f"SDK 版本不兼容: {hello.sdk_version}")
            self._last_handshake_rejection_reason = (
                f"SDK 版本 {hello.sdk_version} 不在支持范围 [{MIN_SDK_VERSION}, {MAX_SDK_VERSION}]"
            )
            resp_payload = HelloResponsePayload(
                accepted=False,
                reason=self._last_handshake_rejection_reason,
            )
            resp = envelope.make_response(payload=resp_payload.model_dump())
            await conn.send_frame(self.encode_envelope(resp))
            return False

        # 发送响应
        self.clear_handshake_state()
        resp_payload = HelloResponsePayload(accepted=True, host_version=self._host_version)
        resp = envelope.make_response(payload=resp_payload.model_dump())
        await conn.send_frame(self.encode_envelope(resp))
        return True

    async def _handshake_as_runner(self, connection: Connection) -> bool:
        """向 Host 发送 runner.hello 并等待接受。"""
        hello = HelloPayload(
            runner_id=self._runner_id,
            sdk_version=self._sdk_version,
            session_token=self._session_token,
        )
        request_id = await self.next_request_id()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.REQUEST,
            method="runner.hello",
            payload=hello.model_dump(),
        )

        await connection.send_frame(self.encode_envelope(envelope))

        resp_data = await asyncio.wait_for(connection.recv_frame(), timeout=10.0)
        response = self.decode_envelope(resp_data)
        resp_payload = HelloResponsePayload.model_validate(response.payload)

        if not resp_payload.accepted:
            self._logger.error(f"握手被拒绝: {resp_payload.reason}")
            return False

        self._logger.info(f"握手成功: host_version={resp_payload.host_version}")
        return True

    def _check_sdk_version(self, sdk_version: str) -> bool:
        """检查 SDK 版本是否在支持范围内。"""
        try:
            sdk_parts = _parse_version_tuple(sdk_version)
            min_parts = _parse_version_tuple(MIN_SDK_VERSION)
            max_parts = _parse_version_tuple(MAX_SDK_VERSION)
            return min_parts <= sdk_parts <= max_parts
        except (ValueError, AttributeError):
            return False

    def _recv_loop_should_continue(self, connection: Connection) -> bool:
        if not self._running or connection.is_closed:
            return False
        if self.is_host:
            return True
        return self._connection is not None

    async def _handle_runner_request(self, envelope: Envelope) -> None:
        connection = self._connection
        if connection is None or connection.is_closed:
            self._logger.warning(f"处理请求 {envelope.method} 时连接已关闭，跳过响应")
            return
        await self.handle_inbound_request(envelope, connection)

    async def _send_loop(self) -> None:
        """后台发送循环：串行消费发送队列，统一执行连接写入。"""
        if self._send_queue is None:
            raise RuntimeError("没有消息队列")

        while True:
            try:
                conn, data, send_future = await self._send_queue.get()
            except asyncio.CancelledError:
                break

            try:
                if conn.is_closed:
                    raise RPCError(ErrorCode.E_PLUGIN_CRASHED, "Runner 未连接")
                await conn.send_frame(data)
                if not send_future.done():
                    send_future.set_result(None)
            except asyncio.CancelledError:
                if not send_future.done():
                    send_future.set_exception(RPCError(ErrorCode.E_TIMEOUT, "服务器关闭"))
                raise
            except Exception as e:
                send_error = RPCError.from_exception(e, {ConnectionError: ErrorCode.E_PLUGIN_CRASHED})
                if not send_future.done():
                    send_future.set_exception(send_error)
            finally:
                self._send_queue.task_done()

    async def _write_outbound_request(self, data: bytes) -> None:
        """Host 发起的 send_request 走发送队列，提供真实背压。"""
        await self._enqueue_send(self._connection, data)

    async def _enqueue_send(self, conn: Connection, data: bytes) -> None:
        """通过发送队列串行发送消息，提供真实背压。"""
        if conn.is_closed:
            raise RPCError(ErrorCode.E_PLUGIN_CRASHED, "Runner 未连接")

        if self._send_queue is None:
            await conn.send_frame(data)
            return

        loop = asyncio.get_running_loop()
        send_future: asyncio.Future[None] = loop.create_future()

        try:
            self._send_queue.put_nowait((conn, data, send_future))
        except asyncio.QueueFull:
            raise RPCError(ErrorCode.E_BACK_PRESSURE, "发送队列已满") from None

        await send_future

    def _spawn_background_task(self, coro: Coroutine[Any, Any, None]) -> None:
        """跟踪入站请求/广播的后台任务。"""
        task = asyncio.create_task(coro)
        if self.is_host:
            # 异步处理请求（Runner 发来的能力调用）
            self._tasks.append(task)
            task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)
            return
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)


def _parse_version_tuple(version: str) -> Tuple[int, int, int]:
    base_version = re.split(r"[-.](?:snapshot|dev|alpha|beta|rc)", version or "", flags=re.IGNORECASE)[0]
    base_version = base_version.split("+", 1)[0]
    parts = [part for part in base_version.split(".") if part != ""]
    while len(parts) < 3:
        parts.append("0")
    return (int(parts[0]), int(parts[1]), int(parts[2]))
