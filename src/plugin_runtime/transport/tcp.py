"""TCP 传输实现。

用于显式 TCP 地址场景或调试场景。
绑定到 127.0.0.1 避免远程访问，但仍需会话令牌做身份校验。
"""

import asyncio

from .base import Connection, TransportClient


class TCPConnection(Connection):
    """基于 TCP 的连接"""

    pass


class TCPTransportClient(TransportClient):
    """TCP 传输客户端"""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    async def connect(self) -> Connection:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        return TCPConnection(reader, writer)
