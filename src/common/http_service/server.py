"""Bot 的 HTTP 服务：在专用线程中只监听宿主分配的进程间通道。"""

from typing import Optional

import asyncio
import threading

from src.common.ipc import IpcHTTPServer, release_endpoint
from src.common.logger import get_logger
from src.common.runtime_context import runtime_context

logger = get_logger("http_server")


class HTTPServer:
    """在当前事件循环中运行 Uvicorn；serve 是唯一负责执行 Uvicorn shutdown 的任务。"""

    def __init__(self, endpoint: str) -> None:
        from .app import create_app

        self.endpoint = endpoint
        self.app = create_app()
        self._server: Optional[IpcHTTPServer] = None
        self._watchdog_task: Optional["asyncio.Task[None]"] = None
        self._serve_task: Optional[asyncio.Task] = None

    @property
    def started(self) -> bool:
        return self._server is not None and self._server.started

    async def start(self) -> None:
        from uvicorn import Config

        from .app import limit_sync_endpoint_concurrency
        from src.common.event_loop_watchdog import start_watchdog

        self._serve_task = asyncio.current_task()
        try:
            # 必须在本循环内设置：anyio 的线程池上限按事件循环保存。
            logger.debug(f"同步路由端点并发上限: {limit_sync_endpoint_concurrency()}")
            # 看门狗同样必须挂在本循环上，才能检测 HTTP 服务卡顿。
            self._watchdog_task = start_watchdog("http")
            config = Config(app=self.app, log_config=None, access_log=False, ws_max_size=104_857_600, timeout_graceful_shutdown=3.0)
            self._server = IpcHTTPServer(config, self.endpoint)
            logger.info("Bot HTTP 服务已在宿主通道上启动；WebUI 请使用宿主地址访问")
            await self._server.serve()
        finally:
            release_endpoint(self.endpoint)
            if self._watchdog_task is not None:
                self._watchdog_task.cancel()
                self._watchdog_task = None
            self._server = None
            self._serve_task = None

    def request_shutdown(self) -> None:
        """由监听循环执行退出请求，清理由 serve 唯一负责。"""
        if self._server is not None:
            self._server.should_exit = True


class ThreadedHTTPServer:
    """在专用线程中运行 HTTP，避免阻塞主事件循环。"""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: HTTPServer | None = None
        self._startup_event = threading.Event()
        self._startup_error: BaseException | None = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """启动 HTTP 专用线程。"""
        if self.is_running:
            return
        self._startup_error = None
        self._startup_event.clear()
        self._thread = threading.Thread(target=self._run, name="maibot-http", daemon=True)
        self._thread.start()

    async def wait_started(self, timeout: float = 30.0) -> None:
        """等待实际监听就绪，向主启动链路暴露线程中的初始化错误。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if self._startup_error is not None:
                raise RuntimeError("HTTP 服务启动失败") from self._startup_error
            server = self._server
            if server is not None and server.started:
                return
            if not self.is_running:
                raise RuntimeError("HTTP 服务线程已退出")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("等待 HTTP 服务监听超时")
            await asyncio.sleep(0.05)

    def _run(self) -> None:
        # Windows 的默认事件循环是 Proactor，Named Pipe 服务端依赖它。
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._server = HTTPServer(self.endpoint)
            self._startup_event.set()
            loop.run_until_complete(self._server.start())
        except Exception as exc:
            self._startup_error = exc
            logger.error(f"HTTP 独立线程运行失败: {exc}", exc_info=True)
        finally:
            self._startup_event.set()
            try:
                pending_tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending_tasks:
                    task.cancel()
                if pending_tasks:
                    loop.run_until_complete(asyncio.gather(*pending_tasks, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as exc:
                logger.debug(f"清理 HTTP 线程事件循环时出现异常: {exc}")
            finally:
                loop.close()
                self._server = None
                self._loop = None

    async def shutdown(self, timeout: float = 5.0) -> None:
        """关闭 HTTP 专用线程。"""
        thread = self._thread
        if thread is None:
            return
        if not self._startup_event.is_set():
            await asyncio.to_thread(self._startup_event.wait, timeout)
        loop = self._loop
        server = self._server
        if loop is not None and server is not None and loop.is_running():
            # 在线程内发退出信号；在线程外 join，避免监听任务结束时取消正在等待它的 shutdown 协程。
            loop.call_soon_threadsafe(server.request_shutdown)
        if thread.is_alive():
            await asyncio.to_thread(thread.join, timeout)
        if thread.is_alive():
            logger.warning("HTTP 线程未能在超时时间内退出")
        else:
            logger.info("HTTP 线程已关闭")
            self._thread = None


_threaded_http_server: ThreadedHTTPServer | None = None


def get_threaded_http_server() -> ThreadedHTTPServer:
    """获取运行在专用线程中的 HTTP 服务器实例，监听地址来自宿主下发的运行契约。"""
    global _threaded_http_server
    if _threaded_http_server is None:
        _threaded_http_server = ThreadedHTTPServer(runtime_context().http_endpoint)
    return _threaded_http_server
