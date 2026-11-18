"""统一 WebUI 的认证协议，与现有登录/设置页面兼容。"""

from typing import TYPE_CHECKING, Any, Callable, Dict

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

import asyncio

from .authentication import HostCredentials, LoginAttempts

if TYPE_CHECKING:
    from .sessions import HostSessions


class VerifyRequest(BaseModel):
    token: str = Field(max_length=4096)


class UpdateRequest(BaseModel):
    new_token: str = Field(min_length=10, max_length=4096)


class ResetRequest(BaseModel):
    reset_password: bool = False


def authenticate(request: Request, sessions: "HostSessions") -> bool:
    return sessions.valid(request.cookies.get(sessions.cookie_name))


def register_host_auth(
    app: FastAPI, sessions: "HostSessions", credentials: HostCredentials, require_session: Callable,
    get_client_ip: Callable[[Request], str],
) -> None:
    attempts = LoginAttempts()

    @app.get("/api/webui/auth/check")
    async def check(request: Request) -> Dict[str, Any]:
        authenticated = authenticate(request, sessions)
        return {"authenticated": authenticated, **(credentials.setup_status() if authenticated else {})}

    @app.post("/api/webui/auth/verify")
    async def verify(body: VerifyRequest, request: Request, response: Response) -> Dict[str, Any]:
        address = get_client_ip(request)
        if attempts.blocked(address):
            raise HTTPException(429, "认证失败次数过多，请稍后再试")
        valid = credentials.verify(body.token)
        try:
            attempts.record(address, valid)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        if not valid:
            return {"valid": False, "message": "Token 无效或已过期"}
        sessions.set_cookie(response, await asyncio.to_thread(sessions.create_session, request.headers.get("origin")))
        return {"valid": True, "message": "登录成功", **credentials.setup_status()}

    @app.post("/api/webui/auth/logout")
    async def logout(request: Request, response: Response) -> Dict[str, Any]:
        await asyncio.to_thread(sessions.revoke, request.cookies.get(sessions.cookie_name))
        response.delete_cookie(sessions.cookie_name, path="/")
        return {"success": True, "message": "已登出"}

    @app.post("/api/webui/auth/update", dependencies=[Depends(require_session)])
    async def update(body: UpdateRequest, request: Request, response: Response) -> Dict[str, Any]:
        try:
            session = await asyncio.to_thread(
                sessions.update_password, request.cookies.get(sessions.cookie_name), body.new_token,
            )
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        if session is not None:
            sessions.set_cookie(response, session)
            return {"success": True, "message": "密码已设置"}
        response.delete_cookie(sessions.cookie_name, path="/")
        return {"success": True, "message": "密码已更新，请重新登录"}

    @app.post("/api/webui/auth/regenerate", dependencies=[Depends(require_session)])
    async def regenerate(response: Response) -> Dict[str, Any]:
        token = await asyncio.to_thread(credentials.regenerate)
        await asyncio.to_thread(sessions.clear_all)
        response.delete_cookie(sessions.cookie_name, path="/")
        return {"success": True, "token": token, "message": "密码已重新生成"}

    @app.get("/api/webui/setup/status", dependencies=[Depends(require_session)])
    async def setup_status() -> Dict[str, Any]:
        return {**credentials.setup_status(), "message": "统一 WebUI 配置状态"}

    @app.post("/api/webui/setup/complete", dependencies=[Depends(require_session)])
    async def complete() -> Dict[str, Any]:
        try:
            await asyncio.to_thread(credentials.complete_setup)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"success": True, "message": "配置完成"}

    @app.post("/api/webui/setup/reset", dependencies=[Depends(require_session)])
    async def reset(body: ResetRequest, request: Request, response: Response) -> Dict[str, Any]:
        await asyncio.to_thread(credentials.reset_setup, body.reset_password)
        if body.reset_password:
            await asyncio.to_thread(sessions.clear_all)
            sessions.set_cookie(response, await asyncio.to_thread(sessions.create_session, request.headers.get("origin")))
        return {"success": True, "message": "配置状态已重置"}
