"""传输层抽象基类

定义 TransportServer 和 TransportClient 的统一接口。
所有传输后端（UDS、Named Pipe、显式 TCP）必须实现此接口。
业务层仅依赖此抽象，禁止直接使用具体传输实现的细节。

分帧协议：4-byte big-endian length prefix + payload
"""

import asyncio
import contextlib
import struct
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from src.config.startup_bindings import get_startup_max_frame_size_mib

# 分帧常量
FRAME_HEADER_SIZE = 4  # 4 字节长度前缀
MAX_FRAME_SIZE = get_startup_max_frame_size_mib() * 1024 * 1024  # 默认 16 MiB，可由 plugin_runtime.max_frame_size_mib 配置


class ConnectionClosed(Exception):
    """连接已关闭"""

    pass


class Connection:
    """单个连接的抽象

    封装了底层 StreamReader/StreamWriter，提供分帧读写能力。
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False
        self._write_lock = asyncio.Lock()  # 保护并发写入的帧完整性

    async def send_frame(self, data: bytes) -> None:
        """发送一帧数据（4-byte length prefix + payload）"""
        if self._closed:
            raise ConnectionClosed("连接已关闭")
        length = len(data)
        if length > MAX_FRAME_SIZE:
            raise ValueError(f"帧大小 {length} 超过最大限制 {MAX_FRAME_SIZE}")
        header = struct.pack(">I", length)
        async with self._write_lock:
            self._writer.write(header + data)
            await self._writer.drain()

    async def recv_frame(self) -> bytes:
        """接收一帧数据"""
        if self._closed:
            raise ConnectionClosed("连接已关闭")
        # 读取 4 字节长度头
        header = await self._reader.readexactly(FRAME_HEADER_SIZE)
        (length,) = struct.unpack(">I", header)
        if length > MAX_FRAME_SIZE:
            raise ValueError(f"帧大小 {length} 超过最大限制 {MAX_FRAME_SIZE}")
        # 读取 payload
        return await self._reader.readexactly(length)

    async def close(self) -> None:
        """关闭连接"""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._writer.close()
            await self._writer.wait_closed()

    @property
    def is_closed(self) -> bool:
        return self._closed


# 连接回调类型：收到新连接时调用
ConnectionHandler = Callable[[Connection], Awaitable[None]]


class TransportServer(ABC):
    """传输服务端抽象

    Host 端使用，监听来自 Runner 的连接。
    """

    @abstractmethod
    async def start(self, handler: ConnectionHandler) -> None:
        """启动服务端，开始监听连接

        Args:
            handler: 新连接到来时的回调函数
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止服务端"""
        ...

    @abstractmethod
    def get_address(self) -> str:
        """获取监听地址（供 Runner 连接用）"""
        ...


class TransportClient(ABC):
    """传输客户端抽象

    Runner 端使用，主动连接 Host。
    """

    @abstractmethod
    async def connect(self) -> Connection:
        """建立到 Host 的连接"""
        ...
