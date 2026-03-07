"""传输层工厂

根据运行平台自动选择最优传输实现。
"""

import sys

from .base import TransportClient, TransportServer


def create_transport_server(socket_path: str | None = None) -> TransportServer:
    """创建传输服务端

    Linux/macOS 使用 UDS，Windows 使用 TCP 回退。

    Args:
        socket_path: UDS socket 路径（仅 Linux/macOS 有效）
    """
    if sys.platform != "win32":
        from .uds import UDSTransportServer
        return UDSTransportServer(socket_path=socket_path)
    else:
        # Windows 回退到 TCP（后续可改为 Named Pipe）
        from .tcp import TCPTransportServer
        return TCPTransportServer()


def create_transport_client(address: str) -> TransportClient:
    """创建传输客户端

    根据地址格式自动判断传输类型：
    - 包含 '/' 或 '.sock' -> UDS
    - 包含 ':' -> TCP

    Args:
        address: Host 端监听地址
    """
    if "/" in address or address.endswith(".sock"):
        from .uds import UDSTransportClient
        return UDSTransportClient(socket_path=address)
    elif ":" in address:
        from .tcp import TCPTransportClient
        host, port_str = address.rsplit(":", 1)
        return TCPTransportClient(host=host, port=int(port_str))
    else:
        raise ValueError(f"无法识别的传输地址格式: {address}")
