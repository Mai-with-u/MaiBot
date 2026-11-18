"""开发者服务的公开评论读取，与统计和用户互动写入接口独立。"""

from pydantic import BaseModel, Field
from typing import List, Optional
from urllib.parse import quote
import httpx

from src.common.http_client import get_shared_ssl_context_async

from .errors import MarketError


class DeveloperReply(BaseModel):
    body: str
    login: str
    updated: int


class PublicReview(BaseModel):
    id: str
    comment: str
    rating: Optional[int] = None
    username: Optional[str] = None
    created_at: str
    updated_at: str
    reply: Optional[DeveloperReply] = None


class FeedbackPage(BaseModel):
    plugin_id: str
    snapshot_at: int
    items: List[PublicReview]
    total: int = Field(ge=0)
    next_offset: Optional[int] = Field(default=None, ge=0)


async def get_feedback(plugin_id: str, offset: int = 0) -> FeedbackPage:
    """直接读取配对好的评论和回复，不按昵称、正文等非唯一字段拼接。"""
    ssl_context = await get_shared_ssl_context_async()
    url = f"https://plugin.maibot.chat/api/feedback/{quote(plugin_id, safe='')}"
    try:
        async with httpx.AsyncClient(verify=ssl_context, timeout=10.0) as client:
            response = await client.get(
                url,
                params={"offset": offset, "limit": 20},
                headers={"User-Agent": "MaiBot-WebUI/1.0 (+https://plugin.maibot.chat)"},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise MarketError(404, "该插件暂未收录到评论与回复服务") from exc
        raise MarketError(502, "评论与开发者回复服务暂不可用，请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise MarketError(502, "无法连接评论与开发者回复服务，请稍后重试") from exc
    try:
        return FeedbackPage.model_validate(response.json())
    except ValueError as exc:
        raise MarketError(502, "评论与开发者回复服务返回的数据格式无效") from exc
