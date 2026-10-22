"""公共市场服务的 WebUI HTTP 适配层，不实现选源、合并或版本判断。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import Response
from typing import Any, Awaitable, Callable, Dict
from urllib.error import HTTPError

from src.config.config import global_config
from src.plugin_market import engagement, service
from src.plugin_market.errors import MarketError
from src.plugin_market.sources import PluginSourceSettings, load_source_settings, save_source_settings
from src.webui.dependencies import require_auth


class MarketRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except (HTTPException, RequestValidationError):
                raise
            except MarketError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
            except HTTPError as exc:
                raise HTTPException(status_code=404 if exc.code == 404 else 502, detail=str(exc)) from exc
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise HTTPException(status_code=502, detail=f"插件市场请求失败：{exc}") from exc

        return handle


router = APIRouter(prefix="/marketplace", route_class=MarketRoute, dependencies=[Depends(require_auth)])


def _reply(reply: engagement.MarketReply) -> JSONResponse:
    return JSONResponse(status_code=reply.status_code, content=reply.data)


@router.get("/source", response_model=PluginSourceSettings)
def get_market_source() -> PluginSourceSettings:
    return load_source_settings()


@router.put("/source", response_model=PluginSourceSettings)
def update_market_source(request: PluginSourceSettings) -> PluginSourceSettings:
    try:
        save_source_settings(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return load_source_settings()


@router.get("")
async def get_marketplace(compatible_only: bool = False, force_refresh: bool = False) -> Dict[str, Any]:
    return await service.get_catalog(compatible_only, force_refresh)


@router.get("/review-identity")
def get_review_identity() -> Dict[str, str]:
    """默认评论昵称仅从本地读取，不发送给市场。"""
    return {"username": global_config.bot.nickname}


@router.get("/user-states")
async def get_user_states(user_id: str = Query(min_length=1, max_length=300)) -> JSONResponse:
    return _reply(await engagement.get_user_states(user_id))


@router.post("/like")
async def like_plugin(request: engagement.VoteRequest) -> JSONResponse:
    return _reply(await engagement.vote(request))


@router.post("/dislike")
async def dislike_plugin(request: engagement.VoteRequest) -> JSONResponse:
    return _reply(await engagement.vote(request, dislike=True))


@router.post("/rate")
async def rate_plugin(request: engagement.RatingRequest) -> JSONResponse:
    return _reply(await engagement.rate(request))


@router.post("/download")
async def record_download(request: engagement.DownloadRequest) -> JSONResponse:
    return _reply(await engagement.record_download(request))


@router.get("/{plugin_id}/user-state")
async def get_user_state(plugin_id: str, user_id: str = Query(min_length=1, max_length=300)) -> JSONResponse:
    return _reply(await engagement.get_user_state(plugin_id, user_id))


@router.get("/{plugin_id}/stats")
async def get_stats(plugin_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    return await service.get_stats(plugin_id, force_refresh)


@router.get("/{plugin_id}/readme")
async def get_readme(
    plugin_id: str, prefer_local: bool = True, branch: str = "main", force_refresh: bool = False,
) -> Dict[str, Any]:
    return await service.get_readme(plugin_id, 200000, prefer_local, branch, force_refresh)


@router.get("/{plugin_id}/changelog")
async def get_changelog(
    plugin_id: str, prefer_local: bool = True, branch: str = "main", force_refresh: bool = False,
) -> Dict[str, Any]:
    return await service.get_changelog(plugin_id, 200000, prefer_local, branch, force_refresh)


@router.get("/{plugin_id}")
async def get_marketplace_detail(plugin_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    return await service.get_detail(plugin_id, force_refresh)
