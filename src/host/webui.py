"""宿主的对外 HTTP：WebUI 静态资源、登录、Bot 管理与按 Bot 路由的业务代理。"""

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

import asyncio
import os
import re
import socket
import uvicorn

from .anti_crawler import AntiCrawlerMiddleware, create_robots_txt_response
from .authentication import HostCredentials
from .config import HostConfig
from .data_transfer import register_data_transfer
from .maintenance import HostMaintenance
from .maim import register_maim_gateway
from .proxy import DEVELOPMENT_ORIGINS, BotProxy, register_bot_proxy, same_webui_origin
from .sessions import HostSessions
from .settings import register_common_settings
from .static import report_static_status, resolve_static_path, static_response
from .supervisor import BotSupervisor
from .webui_auth import authenticate, register_host_auth


_DESKTOP_PET_SESSION_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>桌宠连接 · MaiBot</title></head>
<body><p>桌宠已连接，可返回桌宠窗口</p></body></html>"""


class SessionRequest(BaseModel):
    ticket: str = Field(min_length=1, max_length=256)


class CreateBotRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SelectionRequest(BaseModel):
    bot_id: Optional[str] = None


class UpdateRequest(BaseModel):
    target: str = Field(min_length=1, max_length=100)


def create_host_app(
    supervisor: BotSupervisor, maintenance: HostMaintenance, sessions: HostSessions, credentials: HostCredentials,
    stopping: asyncio.Event, *, config: HostConfig, static_path: Optional[Path] = None,
) -> FastAPI:
    """WebUI 关闭时只提供文件传输，不影响适配器下载文件。"""
    proxy = BotProxy(supervisor, sessions)
    workspace = supervisor.workspace
    management = config.webui_enabled

    app = FastAPI(title="MaiBot WebUI", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(DEVELOPMENT_ORIGINS), allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-MaiBot-Host", "Range", "Accept"],
        expose_headers=["Content-Disposition", "Content-Range"],
    )
    app.add_middleware(
        AntiCrawlerMiddleware, mode=config.anti_crawler_mode, allowed_ips=config.allowed_ips,
        trusted_proxies=config.trusted_proxies, trust_xff=config.trust_xff,
        is_authenticated=lambda request: sessions.valid(request.cookies.get(sessions.cookie_name)),
    )

    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt() -> Response:
        return create_robots_txt_response()

    @app.middleware("http")
    async def protect_requests(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/api/"):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                login_origin = sessions.login_origin(request.cookies.get(sessions.cookie_name))
                if not same_webui_origin(request.headers, login_origin):
                    return JSONResponse(status_code=403, content={"detail": "请从当前 WebUI 发起操作"})
                host_operation = request.url.path.startswith("/api/host/") and "/proxy/" not in request.url.path
                if host_operation and request.headers.get("x-maibot-host") != "1":
                    return JSONResponse(status_code=403, content={"detail": "缺少管理请求标识"})
            if stopping.is_set():
                return JSONResponse(status_code=503, content={"detail": "宿主正在关闭"})
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            # 静态资源由 static_response 决定缓存策略；接口响应一律不缓存。
            response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    register_maim_gateway(app, supervisor)
    if not management:
        register_bot_proxy(app, proxy, management=False)
        return app

    async def require_session(request: Request) -> None:
        # 仅内存查找，不把每次状态轮询分派到线程池。
        if not sessions.valid(request.cookies.get(sessions.cookie_name)):
            raise HTTPException(401, "请登录 WebUI")

    register_data_transfer(app, maintenance, require_session)
    register_bot_proxy(app, proxy, management=True)
    register_host_auth(app, sessions, credentials, require_session)
    register_common_settings(app, workspace, require_session)

    async def mode_payload(request: Request, authenticated: bool, bot_id: Optional[str] = None) -> Dict[str, Any]:
        rows = await supervisor.statuses() if authenticated else []
        requested = bot_id if bot_id is not None else request.cookies.get(sessions.selection_cookie)
        selected = next((row for row in rows if row["bot_id"] == requested), None)
        if requested is None and rows:
            selected = rows[0]
        return {
            "host": True, "authenticated": authenticated,
            "selected_bot_id": selected["bot_id"] if selected else None,
            "selected_bot_running": selected is not None and selected["status"] == "running",
        }

    @app.get("/api/host/mode")
    async def mode(request: Request, response: Response, bot_id: Optional[str] = None) -> Dict[str, Any]:
        return await mode_payload(request, authenticate(request, sessions), bot_id)

    @app.post("/api/host/selection", dependencies=[Depends(require_session)])
    async def select_bot(body: SelectionRequest, request: Request, response: Response) -> Dict[str, Any]:
        if body.bot_id is not None:
            try:
                await asyncio.to_thread(workspace.registry.get_bot, body.bot_id)
            except (KeyError, ValueError) as exc:
                raise HTTPException(404, "未找到 Bot") from exc
            response.set_cookie(sessions.selection_cookie, body.bot_id, httponly=True, samesite="lax", secure=sessions.secure_cookie)
        else:
            response.set_cookie(sessions.selection_cookie, "", httponly=True, samesite="lax", secure=sessions.secure_cookie)
        result = await mode_payload(request, True, body.bot_id)
        if body.bot_id is None:
            result.update(selected_bot_id=None, selected_bot_running=False)
        return result

    @app.post("/api/host/session")
    async def login(body: SessionRequest, request: Request, response: Response) -> Dict[str, bool]:
        session = await asyncio.to_thread(sessions.exchange, body.ticket, request.headers.get("origin"))
        sessions.set_cookie(response, session)
        return {"authenticated": True}

    @app.get("/api/host/session", dependencies=[Depends(require_session)])
    async def session_status() -> Dict[str, bool]:
        return {"authenticated": True}

    @app.get("/api/host/desktop-pet/session", response_class=HTMLResponse, dependencies=[Depends(require_session)])
    def desktop_pet_session(request: Request) -> HTMLResponse:
        # 原生客户端在认证后停留于此，不持续加载完整 WebUI；仅注入的客户端代码负责传输。
        # WebView 对 'self' 与 WS scheme 的匹配不同，显式允许同 Host 的 WS/WSS，兼容 TLS 反代。
        host = request.headers.get("host", "")
        login_origin = sessions.login_origin(request.cookies.get(sessions.cookie_name))
        if login_origin and login_origin not in DEVELOPMENT_ORIGINS:
            authority = urlsplit(login_origin).netloc
            if authority.lower().rpartition(":")[0] == host.lower():
                host = authority
        if re.fullmatch(r"(?:[A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?", host) is None:
            raise HTTPException(400, "无效的 WebUI 地址")
        try:
            parsed = urlsplit("http://" + host)
            if parsed.port == 0:
                raise ValueError("invalid port")
        except ValueError as exc:
            raise HTTPException(400, "无效的 WebUI 地址") from exc
        return HTMLResponse(_DESKTOP_PET_SESSION_HTML, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; "
                f"connect-src 'self' ipc: http://ipc.localhost ws://{host} wss://{host}; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            ),
        })

    @app.delete("/api/host/session", dependencies=[Depends(require_session)])
    async def logout(request: Request, response: Response) -> Dict[str, bool]:
        await asyncio.to_thread(sessions.revoke, request.cookies.get(sessions.cookie_name))
        response.delete_cookie(sessions.cookie_name, path="/", httponly=True, samesite="lax")
        return {"authenticated": False}

    @app.get("/api/host/bots", dependencies=[Depends(require_session)])
    async def list_bots() -> Dict[str, Any]:
        return {"bots": await supervisor.statuses(), "workspace": str(workspace.root)}

    @app.post("/api/host/bots", status_code=201, dependencies=[Depends(require_session)])
    async def create_bot(body: CreateBotRequest) -> Dict[str, Any]:
        try:
            bot = await maintenance.create_bot(body.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
        return await supervisor.status(bot.bot_id)

    @app.get("/api/host/maintenance/update", dependencies=[Depends(require_session)])
    def update_status() -> Dict[str, Any]:
        from src.common import self_update

        result = self_update.read_last_result(maintenance.update_root)
        return {"busy": maintenance.busy, "local": asdict(self_update.inspect_local(maintenance.update_root)),
                "last_result": asdict(result) if result is not None else None}

    @app.post("/api/host/maintenance/update/check", dependencies=[Depends(require_session)])
    async def check_update() -> Dict[str, Any]:
        from src.common.package_update import SelfUpdateError

        try:
            return await maintenance.check_update()
        except (SelfUpdateError, RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/host/maintenance/update", dependencies=[Depends(require_session)])
    async def apply_update(body: UpdateRequest, background: BackgroundTasks) -> Dict[str, Any]:
        from src.common.package_update import SelfUpdateError

        try:
            result = await maintenance.prepare_update(body.target)
        except (SelfUpdateError, RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        # 回执发送后再关闭 HTTP；更新过程与浏览器连接的存续无关。
        async def stop_for_update() -> None:
            stopping.set()

        background.add_task(stop_for_update)
        return result

    async def manage_bot(bot_id: str, action: str, name: str = "") -> Dict[str, Any]:
        try:
            return await maintenance.manage_bot(bot_id, action, name)
        except KeyError as exc:
            raise HTTPException(404, "未找到 Bot") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.patch("/api/host/bots/{bot_id}", dependencies=[Depends(require_session)])
    async def rename_bot(bot_id: str, body: CreateBotRequest) -> Dict[str, Any]:
        return await manage_bot(bot_id, "rename", body.name)

    @app.delete("/api/host/bots/{bot_id}", dependencies=[Depends(require_session)])
    async def archive_bot(bot_id: str) -> Dict[str, Any]:
        return await manage_bot(bot_id, "archive")

    @app.post("/api/host/bots/{bot_id}/clone", dependencies=[Depends(require_session)])
    async def clone_bot(bot_id: str, body: CreateBotRequest) -> Dict[str, Any]:
        return await manage_bot(bot_id, "clone", body.name)

    @app.get("/api/host/maintenance/plugin-dependencies", dependencies=[Depends(require_session)])
    async def plugin_dependencies() -> Dict[str, Any]:
        return {**await maintenance.analyze_plugin_dependencies(), "busy": maintenance.busy,
                "error": maintenance.dependency_error}

    @app.post("/api/host/maintenance/plugin-dependencies", dependencies=[Depends(require_session)])
    async def install_plugin_dependencies() -> Dict[str, Any]:
        try:
            return await maintenance.install_plugin_dependencies()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/host/bots/{bot_id}/{action}", dependencies=[Depends(require_session)])
    async def control_bot(bot_id: str, action: str) -> Dict[str, Any]:
        operations = {"start": supervisor.start, "stop": supervisor.stop, "restart": supervisor.restart}
        if action not in operations:
            raise HTTPException(404, "未知 Bot 操作")
        try:
            return await operations[action](bot_id)
        except KeyError as exc:
            raise HTTPException(404, "未找到 Bot") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.get("/{path:path}", include_in_schema=False)
    async def static_file(request: Request, path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "未找到接口")
        return await static_response(request, static_path, path)

    return app


class _HostHTTPServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # SIGINT/SIGTERM 统一由宿主主流程接管，避免 Uvicorn 覆盖宿主清理流程。
        yield


class HostWebUI:
    """宿主唯一的对外 HTTP 服务：WebUI、登录、Bot 代理与文件传输。"""

    def __init__(self, supervisor: BotSupervisor, maintenance: HostMaintenance, stopping: asyncio.Event) -> None:
        config = supervisor.workspace.config
        self.hosts = config.bind_hosts()
        self.port = config.port
        self.base_url = config.local_base_url()
        self.credentials = HostCredentials(supervisor.workspace.host_directory / "webui.json")
        self.sessions = HostSessions(
            supervisor.workspace.root, credentials=self.credentials, secure_cookie=config.secure_cookie,
        )
        self.management = config.webui_enabled
        self.static_path = resolve_static_path() if self.management else None
        self.app = create_host_app(
            supervisor, maintenance, self.sessions, self.credentials, stopping, config=config, static_path=self.static_path,
        )
        self._server: Optional[_HostHTTPServer] = None
        self.ssl_certfile = config.ssl_certfile
        self.ssl_keyfile = config.ssl_keyfile
        self._sockets: List[socket.socket] = []
        self.task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self.management:
            report_static_status(self.static_path)
        try:
            for host in self.hosts:
                sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
                self._sockets.append(sock)
                # POSIX 允许重用旧连接尚未收尾的监听地址，避免快速重启时 bind 失败。
                # Windows 的 SO_REUSEADDR 会允许抢占仍在监听的端口，沿用默认绑定行为。
                if os.name == "posix":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if sock.family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((host, self.port))
                sock.setblocking(False)
            self._server = _HostHTTPServer(uvicorn.Config(
                self.app, host=self.hosts[0], port=self.port, log_config=None, access_log=False,
                ws_max_size=104_857_600, proxy_headers=False, lifespan="off", timeout_graceful_shutdown=100,
                ssl_certfile=self.ssl_certfile or None, ssl_keyfile=self.ssl_keyfile or None,
            ))
            self.task = asyncio.create_task(self._server.serve(sockets=self._sockets))
            async with asyncio.timeout(10):
                while not self._server.started:
                    if self.task.done():
                        await self.task
                        raise RuntimeError("宿主 WebUI 启动失败")
                    await asyncio.sleep(0.01)
        except BaseException:
            await self.close()
            raise

    def open_url(self) -> str:
        return f"{self.base_url}/bots#ticket={self.sessions.issue_ticket()}"

    async def close(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        try:
            if self.task is not None:
                await self.task
        finally:
            for sock in self._sockets:
                sock.close()
