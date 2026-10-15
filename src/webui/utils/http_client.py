"""WebUI 出站请求的 SSL 上下文复用。

httpx.AsyncClient 在构造时会创建 3 个 SSLContext（主 transport 与 http/https
两个代理 transport），每个 SSLContext 都要完整加载一遍 CA 证书包
（certifi 的 cacert.pem）。实测在 Windows 上单次加载约 1.8s，也就是每请求新建
客户端要白付 5s 以上——插件市场的清单拉取、统计代理等链路每个请求都会触发一次，
且构造过程是在 async 端点内同步执行的，会直接阻塞 WebUI 事件循环。

这里把 SSLContext 收敛为进程内唯一一份。各调用方仍按请求创建 AsyncClient：
不同调用方的 timeout / follow_redirects / headers 参数互不影响，也避免了客户端
跨事件循环复用带来的生命周期问题。
"""

from ssl import SSLContext
from typing import Optional

import httpx

_shared_ssl_context: Optional[SSLContext] = None


def get_shared_ssl_context() -> SSLContext:
    """获取进程内共享的 SSLContext，仅在首次调用时创建。"""
    global _shared_ssl_context
    if _shared_ssl_context is None:
        _shared_ssl_context = httpx.create_ssl_context()
    return _shared_ssl_context
