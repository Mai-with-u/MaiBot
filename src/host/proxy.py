"""按显式 Bot ID 经进程间通道转发业务 HTTP / WebSocket；浏览器只登录宿主。"""

from typing import Any, AsyncIterator, Dict, Mapping, Protocol, Set
from urllib.parse import quote, urljoin, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, WSMsgType
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from yarl import URL

import asyncio
import re

from src.common.ipc import http_connector

from .supervisor import BotSupervisor


_PREFIX = "/api/host/bots/{bot_id}/proxy"
_BOT_ID = re.compile(r"[0-9a-f]{32}")
# 进程间通道没有主机名；固定的占位主机只用于构造 URL 与校验跳转目标。
_UPSTREAM_ORIGIN = "http://bot"
_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade",
})
_REQUEST_PRIVATE_HEADERS = frozenset({"host", "cookie", "authorization", "origin", "forwarded"})


class SessionValidator(Protocol):
    cookie_name: str

    def valid(self, session: str | None) -> bool: ...


def _allowed_path(path: str) -> bool:
    # 只转发应用业务路径；登录与初始化由宿主自己处理，不能被转发到 Bot。
    if "\\" in path or any(part in {".", ".."} for part in path.split("/")):
        return False
    if not path.startswith("api/"):
        return False
    return not any(path == prefix or path.startswith(prefix + "/") for prefix in (
        "api/host", "api/webui/auth", "api/webui/setup",
    ))


def _connection_headers(headers: Mapping[str, str]) -> Set[str]:
    return {value.strip().lower() for value in headers.get("connection", "").split(",") if value.strip()}


def _request_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    prohibited = _HOP_HEADERS | _REQUEST_PRIVATE_HEADERS | _connection_headers(headers)
    return {
        name: value for name, value in headers.items()
        if name.lower() not in prohibited and not name.lower().startswith(("x-forwarded-", "sec-websocket-"))
    }


def _upstream_url(path: str, query: str) -> URL:
    # path 来自解码后的路由参数，重新编码后交给 aiohttp。
    address = f"{_UPSTREAM_ORIGIN}/{quote(path, safe='/')}"
    if query:
        address += "?" + query
    return URL(address, encoded=True)


def _response_headers(headers: Mapping[str, str], bot_id: str) -> Dict[str, str]:
    prohibited = _HOP_HEADERS | _connection_headers(headers) | {"set-cookie", "content-length", "location"}
    result = {
        name: value for name, value in headers.items()
        if name.lower() not in prohibited and not name.lower().startswith("access-control-")
    }
    location = headers.get("location")
    if location is not None:
        resolved = urlsplit(urljoin(_UPSTREAM_ORIGIN + "/", location))
        if f"{resolved.scheme}://{resolved.netloc}" != _UPSTREAM_ORIGIN or not _allowed_path(resolved.path.lstrip("/")):
            raise HTTPException(502, "Bot 返回了不支持的跳转地址")
        result["Location"] = _PREFIX.format(bot_id=bot_id) + resolved.path
        if resolved.query:
            result["Location"] += "?" + resolved.query
        if resolved.fragment:
            result["Location"] += "#" + resolved.fragment
    return result


class BotProxy:
    """每次转发使用独立的客户端：通道地址随 Bot 的每次运行变化，不跨运行复用连接。"""

    def __init__(self, supervisor: BotSupervisor, sessions: SessionValidator | None) -> None:
        self.supervisor = supervisor
        self.sessions = sessions

    def is_authenticated(self, connection: Request | WebSocket) -> bool:
        sessions = self.sessions
        return sessions is not None and sessions.valid(connection.cookies.get(sessions.cookie_name))

    def _require_session(self, connection: Request | WebSocket) -> None:
        if not self.is_authenticated(connection):
            raise HTTPException(401, "请先登录 WebUI")

    def _client(self, bot_id: str) -> ClientSession:
        if _BOT_ID.fullmatch(bot_id) is None:
            raise HTTPException(422, "Bot ID 无效")
        try:
            endpoint = self.supervisor.proxy_endpoint(bot_id)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return ClientSession(
            connector=http_connector(endpoint), timeout=ClientTimeout(total=None, sock_connect=5, sock_read=None),
            auto_decompress=False, cookie_jar=DummyCookieJar(),
        )

    async def http(self, request: Request, bot_id: str, path: str) -> StreamingResponse:
        self._require_session(request)
        if not _allowed_path(path):
            raise HTTPException(404, "此路径不属于 Bot 业务接口")
        return await self._forward_http(request, bot_id, path)

    async def transfer(self, request: Request, bot_id: str, token: str) -> StreamingResponse:
        # 文件传输由 Bot 的限时凭证鉴权，不依赖浏览器登录。
        if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token) is None:
            raise HTTPException(404, "无效的文件传输凭证")
        return await self._forward_http(request, bot_id, f"api/files/transfers/{token}")

    async def _forward_http(self, request: Request, bot_id: str, path: str) -> StreamingResponse:
        client = self._client(bot_id)
        target = _upstream_url(path, request.scope["query_string"].decode("ascii"))
        # 保留上传流，避免导入压缩包或文件时把整个请求读入内存。
        body = request.stream() if request.method not in {"GET", "HEAD"} or request.headers.get("content-length") else None
        try:
            upstream = await client.request(
                request.method, target, headers=_request_headers(request.headers), data=body, allow_redirects=False,
            )
            response_headers = _response_headers(upstream.headers, bot_id)
        except (ClientError, TimeoutError) as exc:
            await client.close()
            raise HTTPException(502, "无法连接此 Bot，请检查启动状态和日志") from exc
        except BaseException:
            await client.close()
            raise

        async def response_body() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.content.iter_any():
                    yield chunk
            finally:
                upstream.close()
                await client.close()

        return StreamingResponse(response_body(), status_code=upstream.status, headers=response_headers)

    async def websocket(self, websocket: WebSocket, bot_id: str, path: str) -> None:
        try:
            self._require_session(websocket)
            if not _allowed_path(path):
                raise HTTPException(404, "此路径不属于 Bot WebSocket 接口")
            client = self._client(bot_id)
        except HTTPException as exc:
            # 接受后用应用错误码关闭，前端可区分未登录和 Bot 停止，避免无意义的重试。
            await websocket.accept()
            await websocket.close(code=4001 if exc.status_code == 401 else 4400 + exc.status_code % 100, reason=str(exc.detail))
            return
        try:
            try:
                upstream = await client.ws_connect(
                    _upstream_url(path, websocket.scope["query_string"].decode("ascii")),
                    headers=_request_headers(websocket.headers), autoping=True, max_msg_size=0,
                )
            except (ClientError, TimeoutError):
                await websocket.accept()
                await websocket.close(code=1013, reason="Bot WebSocket 尚未就绪")
                return
            await websocket.accept()
            await self._relay(websocket, upstream)
        finally:
            await client.close()

    async def _relay(self, websocket: WebSocket, upstream: Any) -> None:
        revoked = False

        async def session_is_current() -> bool:
            nonlocal revoked
            if self.is_authenticated(websocket):
                return True
            # 改密码、登出和会话到期也必须作用于已有长连接；不能等下一次握手才停止转发。
            if not revoked:
                revoked = True
                await websocket.close(code=4001, reason="WebUI 登录已失效，请重新登录")
            return False

        async def browser_to_bot() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if not await session_is_current():
                    return
                if message.get("text") is not None:
                    await upstream.send_str(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send_bytes(message["bytes"])

        async def bot_to_browser() -> None:
            async for message in upstream:
                if not await session_is_current():
                    return
                if message.type == WSMsgType.TEXT:
                    await websocket.send_text(message.data)
                elif message.type == WSMsgType.BINARY:
                    await websocket.send_bytes(message.data)
                elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    return

        relays = {asyncio.create_task(browser_to_bot()), asyncio.create_task(bot_to_browser())}
        try:
            done, _ = await asyncio.wait(relays, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    await task
                except (ClientError, WebSocketDisconnect, OSError):
                    pass  # 任一端断开即结束本次转发，不改接另一个 Bot。
        finally:
            for task in relays:
                task.cancel()
            await asyncio.gather(*relays, return_exceptions=True)
            close_code = 1008 if upstream.close_code == 1008 else 1001
            await upstream.close()
            try:
                await websocket.close(code=close_code)
            except (RuntimeError, WebSocketDisconnect):
                pass  # 浏览器已经发送 disconnect。


def register_bot_proxy(app: FastAPI, proxy: BotProxy, *, management: bool) -> None:
    """必须在静态 SPA 的 catch-all 路由之前注册；关闭 WebUI 时只保留文件传输。"""
    app.add_api_route("/api/files/bots/{bot_id}/transfers/{token}", proxy.transfer, methods=["GET", "HEAD", "PUT"])
    if management:
        path = _PREFIX + "/{path:path}"
        app.add_api_route(path, proxy.http, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
        app.add_api_websocket_route(path, proxy.websocket)
