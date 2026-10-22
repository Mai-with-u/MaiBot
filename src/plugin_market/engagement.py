"""公共市场互动服务；统计、评论和用户状态共用同一市场地址。"""

from dataclasses import dataclass
from pydantic import BaseModel, Field, model_validator
from typing import Any, Dict, Optional
from urllib.parse import quote
import httpx

from src.common.http_client import get_shared_ssl_context_async

from . import sources
from .cache import market_cache
from .errors import MarketError


@dataclass
class MarketReply:
    status_code: int
    data: Dict[str, Any]


class VoteRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(..., min_length=1, max_length=300)


class RatingRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(..., min_length=1, max_length=300)
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=500)
    username: Optional[str] = Field(None, max_length=100)
    anonymous: bool = False

    @model_validator(mode="after")
    def validate_rating_or_comment(self) -> "RatingRequest":
        has_rating = "rating" in self.model_fields_set and self.rating is not None
        if not has_rating and "comment" not in self.model_fields_set:
            raise ValueError("rating 和 comment 至少需要提供一个")
        if not self.anonymous and self.username is not None:
            self.username = self.username.strip()
            if not self.username:
                raise ValueError("非匿名评价的用户名不能为空")
        return self


class DownloadRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: Optional[str] = Field(None, min_length=1, max_length=300)
    fingerprint: Optional[str] = Field(None, min_length=1, max_length=300)


async def _request(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> MarketReply:
    url = f"{sources.MARKET_BASE_URL}{path}"
    try:
        # SSLContext 的首次创建也在线程池执行，避免占住任意一个事件循环。
        ssl_context = await get_shared_ssl_context_async()
        async with httpx.AsyncClient(verify=ssl_context, timeout=sources.REQUEST_TIMEOUT) as client:
            response = await client.request(method, url, json=payload)
    except httpx.HTTPError as exc:
        raise MarketError(502, f"插件市场互动请求失败：{url} - {exc}") from exc
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("插件市场互动响应必须为对象")
    if method != "GET" and response.is_success and data.get("success") is not False:
        market_cache.invalidate(f"{sources.MARKET_BASE_URL}/api/v2/market/")
    return MarketReply(response.status_code, data)


async def get_reviews(plugin_id: str) -> Dict[str, Any]:
    reply = await _request("GET", f"/stats/{quote(plugin_id, safe='')}")
    if reply.status_code >= 400 or reply.data.get("success") is False:
        raise MarketError(reply.status_code if reply.status_code >= 400 else 502,
                          str(reply.data.get("error", "获取插件评论失败")))
    stats = reply.data.get("stats", reply.data)
    if not isinstance(stats, dict):
        raise ValueError("插件评论响应格式无效")
    return {"recent_ratings": stats.get("recent_ratings", [])}


async def get_user_state(plugin_id: str, user_id: str) -> MarketReply:
    return await _request("GET", f"/stats/user-state?plugin_id={quote(plugin_id, safe='')}&user_id={quote(user_id, safe='')}")


async def get_user_states(user_id: str) -> MarketReply:
    return await _request("GET", f"/stats/user-states?user_id={quote(user_id, safe='')}")


async def vote(request: VoteRequest, dislike: bool = False) -> MarketReply:
    return await _request("POST", "/stats/dislike" if dislike else "/stats/like", request.model_dump())


async def rate(request: RatingRequest) -> MarketReply:
    payload = request.model_dump(exclude_unset=True)
    if payload.get("rating") is None:
        payload.pop("rating", None)
    # 匿名评价不转发用户名，避免客户端传入的隐私字段进入远端服务。
    if request.anonymous:
        payload.pop("username", None)
    return await _request("POST", "/stats/rate", payload)


async def record_download(request: DownloadRequest) -> MarketReply:
    return await _request("POST", "/stats/download", request.model_dump())
