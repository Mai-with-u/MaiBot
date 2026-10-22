"""Host 与 WebUI 出站请求的 SSL 上下文复用。

httpx.AsyncClient 在构造时会创建 SSLContext，启用代理时还可能为代理 transport
创建额外的 SSLContext，每个 SSLContext 都要完整加载一遍 CA 证书包
（certifi 的 cacert.pem）。实测在 Windows 上单次加载约 1.8s，也就是每请求新建
客户端要白付 5s 以上——插件市场的清单拉取、统计代理等链路每个请求都会触发一次，
且构造过程是在 async 端点内同步执行的，会直接阻塞 WebUI 事件循环。

这里把 SSLContext 收敛为进程内唯一一份。各调用方仍按请求创建 AsyncClient：
不同调用方的 timeout / follow_redirects / headers 参数互不影响，也避免了客户端
跨事件循环复用带来的生命周期问题。
"""

from ssl import SSLContext
from threading import Lock
from typing import Optional

import asyncio
import httpx

_shared_ssl_context: Optional[SSLContext] = None
_ssl_context_lock = Lock()


def get_shared_ssl_context() -> SSLContext:
    """获取进程内共享的 SSLContext，仅在首次调用时创建。"""
    global _shared_ssl_context
    if _shared_ssl_context is not None:
        return _shared_ssl_context
    with _ssl_context_lock:
        if _shared_ssl_context is None:
            _shared_ssl_context = httpx.create_ssl_context()
        return _shared_ssl_context


async def get_shared_ssl_context_async() -> SSLContext:
    """首次证书加载及并发初始化等待在线程中执行，热路径直接复用。"""
    if _shared_ssl_context is not None:
        return _shared_ssl_context
    return await asyncio.to_thread(get_shared_ssl_context)
