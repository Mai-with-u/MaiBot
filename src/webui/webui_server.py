"""独立的 WebUI 服务器。"""

from typing import Any, Optional, Sequence

import asyncio
import sys
import threading

from src.common.logger import get_logger
from src.common.utils.port_checker import assert_port_available, is_port_conflict_error, log_port_conflict

logger = get_logger("webui_server")


def _get_loaded_config_manager() -> Optional[Any]:
    config_module = sys.modules.get("src.config.config")
    if config_module is None:
        return None
    return getattr(config_module, "config_manager", None)


class _ASGIProxy:
    def __init__(self, app):
        self._app = app

    def set_app(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send):
        await self._app(scope, receive, send)


class WebUIServer:
    """独立的 WebUI 服务器"""

    def __init__(self, hosts: list[str] | None = None, port: int = 8001, register_config_reload: bool = True):
        self.hosts = hosts or ["127.0.0.1", "::1"]
        self.port = port
        from src.webui.app import create_app, show_access_token

        self._app = create_app(host=self.hosts[0], port=port, enable_static=True)
        self.app = _ASGIProxy(self._app)
        self._server: Optional[Any] = None
        self._watchdog_task: Optional["asyncio.Task[None]"] = None
        self._reload_callback_registered = False
        self._register_config_reload = register_config_reload

        show_access_token()
        if self._register_config_reload:
            self._maybe_register_reload_callback()

    def _maybe_register_reload_callback(self) -> None:
        if not self._register_config_reload:
            return
        if self._reload_callback_registered:
            return

        config_manager = _get_loaded_config_manager()
        if config_manager is None:
            return

        config_manager.register_reload_callback(self.reload_app)
        self._reload_callback_registered = True

    def _maybe_unregister_reload_callback(self) -> None:
        if not self._register_config_reload:
            return
        if not self._reload_callback_registered:
            return

        config_manager = _get_loaded_config_manager()
        if config_manager is None:
            return

        config_manager.unregister_reload_callback(self.reload_app)
        self._reload_callback_registered = False

    async def reload_app(self) -> None:
        from src.webui.app import create_app

        self._app = create_app(host=self.hosts[0], port=self.port, enable_static=True)
        self.app.set_app(self._app)
        logger.info("WebUI 应用已热重载")

    def _create_bound_sockets(self) -> list:
        import socket as _socket

        sockets = []
        for host in self.hosts:
            addr_info_list = _socket.getaddrinfo(host, self.port, _socket.AF_UNSPEC, _socket.SOCK_STREAM)
            for af, socktype, proto, _, sa in addr_info_list:
                sock = None
                try:
                    sock = _socket.socket(af, socktype, proto)
                    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                    if af == _socket.AF_INET6:
                        try:
                            sock.setsockopt(_socket.IPPROTO_IPV6, _socket.IPV6_V6ONLY, 1)
                        except OSError:
                            pass
                    sock.bind(sa)
                    sock.listen()
                    sockets.append(sock)
                    break
                except OSError as bind_err:
                    if sock is not None:
                        try:
                            sock.close()
                        except OSError:
                            pass
                    logger.warning(f"⚠️ WebUI 无法绑定到 {host}:{self.port} ({sa}): {bind_err}")
                    continue

        return sockets

    def _log_bind_addresses(self) -> None:
        has_v4_localhost = False
        has_v6_localhost = False
        has_v4_wildcard = False
        has_v6_wildcard = False

        for host in self.hosts:
            if ":" in host:
                logger.info(f"🌐 访问地址: http://[{host}]:{self.port}")
                if host == "::":
                    has_v6_wildcard = True
                elif host == "::1":
                    has_v6_localhost = True
            else:
                logger.info(f"🌐 访问地址: http://{host}:{self.port}")
                if host == "0.0.0.0":
                    has_v4_wildcard = True
                elif host == "127.0.0.1":
                    has_v4_localhost = True

        if has_v4_wildcard or has_v6_wildcard:
            local = []
            if has_v4_wildcard or has_v4_localhost:
                local.append(f"http://localhost:{self.port}")
            if has_v6_wildcard or has_v6_localhost:
                local.append(f"http://[::1]:{self.port}")
            if local:
                logger.info(f"💡 本机访问: {'， '.join(local)}")

    async def start(self):
        """启动服务器"""
        from uvicorn import Config
        from uvicorn import Server as UvicornServer

        self._maybe_register_reload_callback()

        # 必须在本循环内设置：anyio 的线程池上限按事件循环保存。
        from src.webui.app import limit_sync_endpoint_concurrency

        logger.info(f"同步路由端点并发上限: {limit_sync_endpoint_concurrency()}")

        # 看门狗同样必须挂在本循环上：实测先卡住的往往正是 WebUI 这个循环。
        from src.common.event_loop_watchdog import start_watchdog

        self._watchdog_task = start_watchdog("webui")

        for host in self.hosts:
            assert_port_available(
                host=host,
                port=self.port,
                service_name="WebUI 服务器",
                logger=logger,
                config_hint="webui.port (config/bot_config.toml)",
                allow_reuse_addr=True,
            )

        sockets = self._create_bound_sockets()
        if not sockets:
            logger.error("❌ WebUI 无法绑定到任何指定地址")
            raise OSError("WebUI 无法绑定到任何指定地址")

        config = Config(
            app=self.app,
            host=self.hosts[0],
            port=self.port,
            log_config=None,
            access_log=False,
        )
        self._server = UvicornServer(config=config)

        logger.info("🌐 WebUI 服务器启动中...")
        self._log_bind_addresses()
        if len(self.hosts) > 1:
            logger.info("🔗 WebUI 已绑定到多个地址")

        try:
            await self._server.serve(sockets=sockets)
        except OSError as e:
            if is_port_conflict_error(e):
                for host in self.hosts:
                    log_port_conflict(
                        logger,
                        service_name="WebUI 服务器",
                        host=host,
                        port=self.port,
                        config_hint="webui.port (config/bot_config.toml)",
                    )
            else:
                logger.error(f"❌ WebUI 服务器启动失败 (网络错误): {e}")
            raise
        except Exception as e:
            logger.error(f"❌ WebUI 服务器运行错误: {e}", exc_info=True)
            raise
        finally:
            self._maybe_unregister_reload_callback()

    async def shutdown(self):
        """关闭服务器"""
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._server:
            logger.info("正在关闭 WebUI 服务器...")
            self._server.should_exit = True
            try:
                await asyncio.wait_for(self._server.shutdown(), timeout=3.0)
                logger.info("✅ WebUI 服务器已关闭")
            except asyncio.TimeoutError:
                logger.warning("⚠️ WebUI 服务器关闭超时")
            except Exception as e:
                logger.error(f"❌ WebUI 服务器关闭失败: {e}")
            finally:
                self._server = None


class ThreadedWebUIServer:
    """在专用线程中运行 WebUI，避免阻塞主事件循环。"""

    def __init__(self, hosts: list[str] | None = None, port: int = 8001) -> None:
        self.hosts = hosts or ["127.0.0.1", "::1"]
        self.port = port
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: WebUIServer | None = None
        self._startup_event = threading.Event()
        self._stopped_event = threading.Event()
        self._startup_error: BaseException | None = None
        self._reload_callback_registered = False

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """启动 WebUI 专用线程。"""
        if self.is_running:
            return

        self._startup_error = None
        self._startup_event.clear()
        self._stopped_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="maibot-webui",
            daemon=True,
        )
        self._thread.start()
        self._maybe_register_reload_callback()
        logger.info("WebUI 服务器已在独立线程中启动")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        try:
            self._server = WebUIServer(
                hosts=self.hosts,
                port=self.port,
                register_config_reload=False,
            )
            self._startup_event.set()
            loop.run_until_complete(self._server.start())
        except Exception as exc:
            self._startup_error = exc
            if not self._startup_event.is_set():
                self._startup_event.set()
            logger.error(f"WebUI 独立线程运行失败: {exc}", exc_info=True)
        finally:
            if not self._startup_event.is_set():
                self._startup_event.set()
            try:
                pending_tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending_tasks:
                    task.cancel()
                if pending_tasks:
                    loop.run_until_complete(asyncio.gather(*pending_tasks, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as exc:
                logger.debug(f"清理 WebUI 线程事件循环时出现异常: {exc}")
            finally:
                loop.close()
                self._server = None
                self._loop = None
                self._stopped_event.set()

    def _maybe_register_reload_callback(self) -> None:
        if self._reload_callback_registered:
            return

        config_manager = _get_loaded_config_manager()
        if config_manager is None:
            return

        config_manager.register_reload_callback(self.reload_app)
        self._reload_callback_registered = True

    def _maybe_unregister_reload_callback(self) -> None:
        if not self._reload_callback_registered:
            return

        config_manager = _get_loaded_config_manager()
        if config_manager is None:
            return

        config_manager.unregister_reload_callback(self.reload_app)
        self._reload_callback_registered = False

    async def reload_app(self, changed_scopes: Sequence[str] | None = None) -> None:
        """在线程事件循环中重建 WebUI app。"""
        if changed_scopes and "bot" not in changed_scopes:
            return

        if not self._startup_event.is_set():
            await asyncio.to_thread(self._startup_event.wait, 5.0)
        if self._startup_error is not None:
            return

        loop = self._loop
        server = self._server
        if loop is None or server is None or not loop.is_running():
            return

        future = asyncio.run_coroutine_threadsafe(server.reload_app(), loop)
        await asyncio.wrap_future(future)

    async def shutdown(self, timeout: float = 5.0) -> None:
        """关闭 WebUI 专用线程。"""
        self._maybe_unregister_reload_callback()

        thread = self._thread
        if thread is None:
            return

        if not self._startup_event.is_set():
            await asyncio.to_thread(self._startup_event.wait, timeout)

        loop = self._loop
        server = self._server
        if loop is not None and server is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(server.shutdown(), loop)
            try:
                await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("WebUI 线程关闭等待超时")
                future.cancel()
            except Exception as exc:
                logger.warning(f"WebUI 线程关闭时出现异常: {exc}")

        if thread.is_alive():
            await asyncio.to_thread(thread.join, timeout)
        if thread.is_alive():
            logger.warning("WebUI 线程未能在超时时间内退出")
        else:
            logger.info("WebUI 线程已关闭")
            self._thread = None


# 全局 WebUI 服务器实例
_webui_server = None
_threaded_webui_server = None


def get_webui_server() -> WebUIServer:
    """获取全局 WebUI 服务器实例"""
    global _webui_server
    if _webui_server is None:
        from src.config.startup_bindings import resolve_webui_bind_address

        bind_address = resolve_webui_bind_address()
        _webui_server = WebUIServer(hosts=bind_address.hosts, port=bind_address.port)
    return _webui_server


def get_threaded_webui_server() -> ThreadedWebUIServer:
    """获取运行在专用线程中的 WebUI 服务器实例。"""
    global _threaded_webui_server
    if _threaded_webui_server is None:
        from src.config.startup_bindings import resolve_webui_bind_address

        bind_address = resolve_webui_bind_address()
        _threaded_webui_server = ThreadedWebUIServer(hosts=bind_address.hosts, port=bind_address.port)
    return _threaded_webui_server
