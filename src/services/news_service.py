"""首页资讯数据服务。

资讯内容由官方资讯服务维护（内容源是一个 Markdown 文件一条资讯），本模块负责拉取、
短期缓存与错误暴露。地址可用环境变量 ``MAIBOT_NEWS_SOURCE_URL`` 覆盖，便于自建部署。
"""

from os import getenv
from typing import Any

import asyncio
import time

import httpx

from src.common.logger import get_logger
from src.webui.schemas.news import NewsItem, NewsResponse

logger = get_logger("webui.news")

DEFAULT_NEWS_SOURCE_URL = "http://hyybuth.xyz:10059/news"
NEWS_SOURCE_URL = getenv("MAIBOT_NEWS_SOURCE_URL", DEFAULT_NEWS_SOURCE_URL).rstrip("/")
NEWS_REQUEST_TIMEOUT = float(getenv("MAIBOT_NEWS_REQUEST_TIMEOUT", "8"))
NEWS_ITEM_LIMIT = int(getenv("MAIBOT_NEWS_ITEM_LIMIT", "20"))
NEWS_CACHE_TTL = float(getenv("MAIBOT_NEWS_CACHE_TTL", "120"))

# 进程内缓存：资讯是低频内容，缓存可避免每次进首页都打远程接口
_cache: tuple[float, NewsResponse] | None = None
_cache_lock = asyncio.Lock()


class NewsSourceError(RuntimeError):
    """资讯服务不可用或返回了无效数据。"""


def _parse_items(payload: dict[str, Any]) -> NewsResponse:
    """把资讯服务返回的 JSON 解析成响应模型。"""

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise NewsSourceError("资讯服务返回格式无效：缺少 items 列表")

    items: list[NewsItem] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        title = str(raw_item.get("title") or "").strip()
        if not title:
            # 没有标题的条目无法展示，直接跳过而不是伪造占位文案
            logger.warning(f"跳过缺少标题的资讯条目: {raw_item.get('id')!r}")
            continue
        items.append(
            NewsItem(
                id=str(raw_item.get("id") or "").strip(),
                title=title,
                summary=str(raw_item.get("summary") or "").strip(),
                content=str(raw_item.get("content") or ""),
                url=str(raw_item.get("url") or "").strip(),
                source=str(raw_item.get("source") or "").strip(),
                published_at=str(raw_item.get("published_at") or "").strip(),
                pinned=bool(raw_item.get("pinned", False)),
            )
        )

    return NewsResponse(
        items=items,
        total=int(payload.get("total") or len(items)),
        updated_at=str(payload.get("updated_at") or "").strip(),
    )


async def _request_news() -> NewsResponse:
    url = f"{NEWS_SOURCE_URL}?limit={NEWS_ITEM_LIMIT}"
    try:
        async with httpx.AsyncClient(timeout=NEWS_REQUEST_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise NewsSourceError(f"资讯服务返回 HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise NewsSourceError(f"资讯服务请求失败：{type(exc).__name__}: {exc}") from exc
    except ValueError as exc:
        raise NewsSourceError("资讯服务返回了非 JSON 响应") from exc

    if not isinstance(payload, dict):
        raise NewsSourceError("资讯服务返回格式无效：顶层不是对象")

    return _parse_items(payload)


async def fetch_news(force: bool = False) -> NewsResponse:
    """获取首页资讯（带进程内缓存）。

    Args:
        force: 为 True 时跳过硬缓存，强制回源（供用户手动刷新使用）。

    Raises:
        NewsSourceError: 资讯服务不可用或数据无效。
    """

    global _cache

    cached = _cache
    if not force and cached is not None and time.monotonic() - cached[0] < NEWS_CACHE_TTL:
        return cached[1]

    async with _cache_lock:
        # 并发请求只回源一次，其余等待者复用同一份结果
        cached = _cache
        if not force and cached is not None and time.monotonic() - cached[0] < NEWS_CACHE_TTL:
            return cached[1]

        try:
            news = await _request_news()
        except NewsSourceError:
            # 回源失败时保留上一份可用数据，避免首页资讯整块变空
            if cached is not None:
                logger.warning("资讯服务暂时不可用，沿用上一次成功获取的内容")
                return cached[1]
            raise

        _cache = (time.monotonic(), news)
        return news
