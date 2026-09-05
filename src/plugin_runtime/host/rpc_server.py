"""Host 端 RPC Server

负责：
1. 监听 Runner 连接
2. 处理握手（runner.hello）
3. 分发调用请求给 Runner / 处理 Runner 的能力调用
4. 请求-响应关联与超时管理
"""

from typing import Any, Dict, List, Optional

import asyncio
import secrets

from src.common.logger import get_logger
from src.plugin_runtime import detect_host_application_version
from src.plugin_runtime.protocol.codec import Codec, MsgPackCodec
from src.plugin_runtime.protocol.envelope import Envelope
from src.plugin_runtime.protocol.errors import ErrorCode
from src.plugin_runtime.rpc_session import MethodHandler, RpcSession
from src.plugin_runtime.transport.base import Connection, TransportServer

_DEFAULT_LOGGER_NAME = "plugin_runtime.host.rpc_server"


class RPCServer:
    """Host 端 RPC 服务器

    管理与 Runner 的 IPC 连接，处理双向 RPC 调用。
    """

    def __init__(
        self,
        transport: TransportServer,
        session_token: Optional[str] = None,
        codec: Optional[Codec] = None,
        send_queue_size: int = 128,
        host_version: str = "",
        logger_name: str = _DEFAULT_LOGGER_NAME,
    ):
        self._transport = transport
        self._logger = get_logger(logger_name)
        self._session = RpcSession(
            None,
            role="host",
            codec=codec or MsgPackCodec(),
            session_token=session_token or secrets.token_hex(32),
            host_version=host_version or detect_host_application_version(),
            logger=self._logger,
            send_queue_size=send_queue_size,
            enable_pending_metadata=True,
        )

        self._connection_lock: asyncio.Lock = asyncio.Lock()

    @property
    def session_token(self) -> str:
        return self._session.session_token

    @property
    def is_connected(self) -> bool:
        return self._session.is_connected

    @property
    def last_handshake_rejection_reason(self) -> str:
        """返回最近一次握手被拒绝的原因。"""
        return self._session.last_handshake_rejection_reason

    def clear_handshake_state(self) -> None:
        """清空最近一次握手拒绝状态。"""
        self._session.clear_handshake_state()

    def register_method(self, method: str, handler: MethodHandler) -> None:
        """注册 RPC 方法处理器"""
        self._session.register_method(method, handler)

    def get_pending_request_snapshot(self, min_duration_ms: int = 0) -> List[Dict[str, Any]]:
        """返回 Host 当前等待 Runner 响应的请求快照。"""

        return self._session.build_pending_request_snapshot(min_duration_ms)

    async def start(self) -> None:
        """启动 RPC 服务器"""
        self.clear_handshake_state()
        self._session.mark_running()
        self._session.ensure_host_send_worker()
        await self._transport.start(self._handle_connection)
        self._logger.debug(f"RPC Server 已启动，监听地址: {self._transport.get_address()}")

    async def stop(self) -> None:
        """停止 RPC 服务器"""
        self._session.mark_stopped()
        self.clear_handshake_state()
        self.abort_pending_requests("服务器正在关闭")

        await self._session.stop_host_send_worker()

        # 取消后台任务
        self._session.cancel_background_tasks()

        # 关闭连接
        connection = self._session.connection
        if connection:
            await connection.close()
            self._session.clear_connection()

        await self._transport.stop()
        self._logger.debug("RPC Server 已停止")

    def abort_pending_requests(self, message: str = "服务器正在关闭") -> int:
        """中止所有等待 Runner 响应的请求。"""

        failed_pending_count = self._session.fail_pending_requests(ErrorCode.E_SHUTTING_DOWN, message)
        failed_send_count = self._session.fail_queued_sends(ErrorCode.E_SHUTTING_DOWN, message)
        return failed_pending_count + failed_send_count

    async def send_request(
        self,
        method: str,
        plugin_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
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
        return await self._session.send_request(
            method,
            plugin_id=plugin_id,
            payload=payload,
            timeout_ms=timeout_ms,
        )

    async def _handle_connection(self, conn: Connection) -> None:
        """处理新的 Runner 连接"""
        self._logger.debug("收到 Runner 连接")
        try:
            async with self._connection_lock:
                self.clear_handshake_state()
                success = await self._handle_handshake(conn)
                if not success:
                    await conn.close()
                    return
                self._logger.debug("Runner staged 握手成功")
                self._session.set_connection(conn)
        except Exception as e:
            self._logger.error(f"握手失败: {e}")
            await conn.close()
            return

        # 启动消息接收循环
        try:
            await self._session.run_recv_loop(
                conn,
                on_request=lambda envelope: self._handle_request(envelope, conn),
            )
        except Exception as e:
            self._logger.error(f"连接异常断开: {e}")
        finally:
            should_fail_pending_requests = False
            async with self._connection_lock:
                if self._session.connection is conn:
                    self._session.clear_connection()
                    should_fail_pending_requests = True
            if should_fail_pending_requests:
                self._session.fail_pending_requests(ErrorCode.E_PLUGIN_CRASHED, "Runner 连接已断开")

    async def _handle_handshake(self, conn: Connection) -> bool:
        """处理 runner.hello 握手"""
        return await self._session.handshake(conn)

    async def _handle_request(self, envelope: Envelope, conn: Connection) -> None:
        """处理来自 Runner 的请求（通常是能力调用 cap.*）"""
        await self._session.handle_inbound_request(envelope, conn)
