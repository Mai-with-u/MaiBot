from os import getenv
from typing import Any, Dict
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from src.common.logger import get_logger
from src.config.config import global_config
from src.webui.dependencies import require_auth
from src.webui.utils.http_client import get_shared_ssl_context

logger = get_logger("webui.plugin_stats_proxy")

router = APIRouter(dependencies=[Depends(require_auth)])

PLUGIN_STATS_BASE_URL = getenv("MAIBOT_PLUGIN_STATS_BASE_URL", "http://hyybuth.xyz:10059").rstrip("/")
PLUGIN_STATS_TIMEOUT = float(getenv("MAIBOT_PLUGIN_STATS_TIMEOUT", "8"))


class VoteRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(..., min_length=1, max_length=300)


class RatingRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(..., min_length=1, max_length=300)
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = Field(None, max_length=500)
    username: str | None = Field(None, max_length=100)
    anonymous: bool = False

    @model_validator(mode="after")
    def validate_rating_or_comment(self) -> "RatingRequest":
        has_rating = "rating" in self.model_fields_set and self.rating is not None
        has_comment = "comment" in self.model_fields_set
        if not has_rating and not has_comment:
            raise ValueError("rating 和 comment 至少需要提供一个")
        if not self.anonymous and self.username is not None:
            self.username = self.username.strip()
            if not self.username:
                raise ValueError("非匿名评价的用户名不能为空")
        return self


class DownloadRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: str | None = Field(None, min_length=1, max_length=300)
    fingerprint: str | None = Field(None, min_length=1, max_length=300)


async def _request_stats_service(method: str, path: str, payload: Dict[str, Any] | None = None) -> JSONResponse:
    url = f"{PLUGIN_STATS_BASE_URL}{path}"
    try:
        # 复用共享 SSLContext：每次新建 AsyncClient 都会重新加载整套 CA 证书（实测约 5s/次），
        # 且该构造在 async 端点内是同步操作，会阻塞 WebUI 事件循环。
        async with httpx.AsyncClient(
            verify=get_shared_ssl_context(), timeout=PLUGIN_STATS_TIMEOUT
        ) as client:
            response = await client.request(method, url, json=payload)
    except httpx.HTTPError as exc:
        logger.warning(f"插件统计服务请求失败: {url} - {type(exc).__name__}: {exc!r}")
        raise HTTPException(status_code=502, detail="插件统计服务暂不可用") from exc

    try:
        data = response.json()
    except ValueError as exc:
        logger.warning(f"插件统计服务返回了非 JSON 响应: {url} - status={response.status_code}")
        raise HTTPException(status_code=502, detail="插件统计服务响应格式无效") from exc

    return JSONResponse(status_code=response.status_code, content=data)


@router.get("/stats-proxy/identity")
def get_review_identity() -> Dict[str, str]:
    """仅向本地 WebUI 提供评价的默认昵称。"""
    return {"username": global_config.bot.nickname}


@router.get("/stats-proxy/stats/user-state")
async def get_plugin_user_state(plugin_id: str, user_id: str) -> JSONResponse:
    query = f"plugin_id={quote(plugin_id, safe='')}&user_id={quote(user_id, safe='')}"
    return await _request_stats_service("GET", f"/stats/user-state?{query}")


@router.get("/stats-proxy/stats/user-states")
async def get_plugin_user_states(user_id: str) -> JSONResponse:
    return await _request_stats_service("GET", f"/stats/user-states?user_id={quote(user_id, safe='')}")


@router.get("/stats-proxy/stats/summary")
async def get_plugin_stats_summary() -> JSONResponse:
    return await _request_stats_service("GET", "/stats/summary")


@router.get("/stats-proxy/stats/{plugin_id}")
async def get_plugin_stats(plugin_id: str) -> JSONResponse:
    return await _request_stats_service("GET", f"/stats/{quote(plugin_id, safe='')}")


@router.post("/stats-proxy/stats/like")
async def like_plugin(request: VoteRequest) -> JSONResponse:
    return await _request_stats_service("POST", "/stats/like", request.model_dump())


@router.post("/stats-proxy/stats/dislike")
async def dislike_plugin(request: VoteRequest) -> JSONResponse:
    return await _request_stats_service("POST", "/stats/dislike", request.model_dump())


@router.post("/stats-proxy/stats/rate")
async def rate_plugin(request: RatingRequest) -> JSONResponse:
    payload = request.model_dump(exclude_unset=True)
    if payload.get("rating") is None:
        payload.pop("rating", None)
    # 匿名时即使客户端误带用户名，也不能转发到远端统计服务。
    if request.anonymous:
        payload.pop("username", None)
    return await _request_stats_service("POST", "/stats/rate", payload)


@router.post("/stats-proxy/stats/download")
async def record_plugin_download(request: DownloadRequest) -> JSONResponse:
    return await _request_stats_service("POST", "/stats/download", request.model_dump())
