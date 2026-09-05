"""Runner 端 RPC 客户端。"""

from typing import Any, Dict, Optional

import asyncio
import contextlib
import uuid

from src.common.logger import get_logger
from src.plugin_runtime.local_sdk import read_local_sdk_version
from src.plugin_runtime.protocol.codec import Codec, MsgPackCodec
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.rpc_session import MethodHandler, RpcSession
from src.plugin_runtime.transport.base import Connection
from src.plugin_runtime.transport.factory import create_transport_client

logger = get_logger("plugin_runtime.runner.rpc_client")


def _get_sdk_version() -> str:
    """读取 SDK 版本号。

    Returns:
        str: SDK 版本；读取失败时回退到 ``1.0.0``。
    """
    local_sdk_version = read_local_sdk_version()
    if local_sdk_version:
        return local_sdk_version

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
        self._session = RpcSession(
            None,
            role="runner",
            codec=codec or MsgPackCodec(),
            session_token=session_token,
            sdk_version=SDK_VERSION,
            runner_id=str(uuid.uuid4()),
            logger=logger,
            enable_pending_metadata=False,
        )
        self._recv_task: Optional[asyncio.Task[None]] = None

    @property
    def is_connected(self) -> bool:
        """返回当前连接是否可用。"""
        return self._session.is_connected

    def register_method(self, method: str, handler: MethodHandler) -> None:
        """注册 Host -> Runner 的 RPC 处理器。

        Args:
            method: RPC 方法名。
            handler: 方法处理函数。
        """
        self._session.register_method(method, handler)

    def _require_connection(self) -> Connection:
        """返回当前可用连接。

        Returns:
            Connection: 当前连接对象。

        Raises:
            RPCError: 当前未连接到 Host。
        """
        return self._session.require_connection()

    async def connect_and_handshake(self) -> bool:
        """连接 Host 并完成握手。

        Returns:
            bool: 是否握手成功。
        """
        client = create_transport_client(self._host_address)
        self._session.set_connection(await client.connect())
        if not await self._session.handshake():
            await self.disconnect()
            return False

        self._session.mark_running()
        self._recv_task = asyncio.create_task(
            self._session.run_recv_loop(on_request=self._handle_request),
            name="RPCClient.recv",
        )
        return True

    async def disconnect(self) -> None:
        """断开与 Host 的连接并清理状态。"""
        self._session.mark_stopped()

        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None

        await self._session.disconnect()

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
        return await self._session.send_request(
            method,
            plugin_id=plugin_id,
            payload=payload,
            timeout_ms=timeout_ms,
        )

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
        request_id = await self._session.next_request_id()
        envelope = Envelope(
            request_id=request_id,
            message_type=MessageType.BROADCAST,
            method=method,
            plugin_id=plugin_id,
            payload=payload or {},
        )
        await connection.send_frame(self._session.encode_envelope(envelope))

    async def _handle_request(self, envelope: Envelope) -> None:
        """处理 Host 发来的请求。

        Args:
            envelope: 请求信封。
        """
        connection = self._session.connection
        if connection is None or connection.is_closed:
            logger.warning(f"处理请求 {envelope.method} 时连接已关闭，跳过响应")
            return

        await self._session.handle_inbound_request(envelope, connection)
