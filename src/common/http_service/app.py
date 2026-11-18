"""组装 Bot 的 HTTP 应用：文件传输与 WebUI 接口；只经宿主分配的进程间通道访问。"""

from fastapi import FastAPI

# 同步路由端点由 Starlette 交给 anyio 线程池执行，anyio 默认上限是 40；
# 底层 SQLite 连接池只有 5 条常驻 + 10 条溢出连接，并发过高会让线程排队等连接，
# 因此把同步端点的并发数限制在连接池容量以内。
MAX_CONCURRENT_SYNC_ENDPOINTS = 8


def limit_sync_endpoint_concurrency() -> int:
    """把同步路由端点的并发数限制在 SQLite 连接池容量以内，并返回生效值。

    必须在目标事件循环内调用：anyio 的线程池上限是按事件循环保存的，在循环外设置
    不会作用到 HTTP 服务的循环上。
    """

    import anyio.to_thread

    limiter = anyio.to_thread.current_default_thread_limiter()
    if limiter.total_tokens > MAX_CONCURRENT_SYNC_ENDPOINTS:
        limiter.total_tokens = MAX_CONCURRENT_SYNC_ENDPOINTS
    return int(limiter.total_tokens)


def create_app() -> FastAPI:
    from .console import router as console_router
    from .maim import MaimApplication
    from src.files.http import router as file_transfers
    from src.webui.app import create_app as create_webui_app

    # 公共服务不公开管理文档；WebUI 自己保留原有文档入口。
    app = FastAPI(title="MaiBot HTTP", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(file_transfers)
    app.include_router(console_router)
    app.mount("/_maim", MaimApplication())
    # 最后挂载，既保留原有接口 URL，又隔离 WebUI 的压缩等中间件。
    app.mount("/", create_webui_app())
    return app
