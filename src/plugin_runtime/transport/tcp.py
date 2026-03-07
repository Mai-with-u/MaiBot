"""TCP 传输实现（回退方案）

仅当 UDS / Named Pipe 不可用时启用。
绑定到 127.0.0.1 避免远程访问，但仍需会话令牌做身份校验。
"""

import asyncio

from .base import Connection, ConnectionHandler, TransportClient, TransportServer


class TCPConnection(Connection):
    """基于 TCP 的连接"""
    pass


class TCPTransportServer(TransportServer):
    """TCP 传输服务端（回退方案）"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._host = host
        self._port = port  # 0 表示自动分配
        self._server: asyncio.AbstractServer | None = None
        self._actual_port: int = 0

    async def start(self, handler: ConnectionHandler) -> None:
        async def _on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            conn = TCPConnection(reader, writer)
            try:
                await handler(conn)
            finally:
                await conn.close()

        self._server = await asyncio.start_server(_on_connect, self._host, self._port)

        # 获取实际分配的端口
        addr = self._server.sockets[0].getsockname()
        self._actual_port = addr[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def get_address(self) -> str:
        return f"{self._host}:{self._actual_port}"


class TCPTransportClient(TransportClient):
    """TCP 传输客户端"""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port

    async def connect(self) -> Connection:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        return TCPConnection(reader, writer)
