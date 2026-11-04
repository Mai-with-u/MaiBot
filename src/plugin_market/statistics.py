"""官方统计数据读取，与插件清单和下载源的选择无关。"""

from typing import Any, Dict, Tuple
from urllib.parse import quote

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from src.config.config import MMC_VERSION

from . import sources
from .errors import MarketError


class PluginStatistics(BaseModel):
    """统计边界校验；缺失读数不能被当成零，额外的榜单字段原样保留。"""

    model_config = ConfigDict(extra="allow")

    plugin_id: str = Field(strict=True, min_length=1)
    downloads: int = Field(strict=True, ge=0)
    likes: int = Field(strict=True, ge=0)
    dislikes: int = Field(strict=True, ge=0)
    rating: float = Field(strict=True, ge=0, le=5)
    rating_count: int = Field(strict=True, ge=0)
    comment_count: int = Field(strict=True, ge=0)


def normalize_statistics(data: Dict[str, Any]) -> Dict[str, Any]:
    """按官方记录原样验证，不补造 ID 或统计读数。"""
    return PluginStatistics.model_validate(data).model_dump()


def _parse_snapshot(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for item in data["plugins"]:
        if item["manifest"].get("manifest_version") != 2:
            continue
        record = item.get("stats")
        if record is None:
            continue
        validated = normalize_statistics(record)
        plugin_id = validated["plugin_id"]
        if plugin_id in stats:
            raise ValueError(f"官方统计快照中的插件 ID 重复：{plugin_id}")
        stats[plugin_id] = validated
    return stats


async def _snapshot(force_refresh: bool = False) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    # 官方统计摘要包含飙升榜；与官方清单复用同一缓存和在途请求。
    response = await sources.request_market(
        "plugins/summary",
        {"maibot_version": MMC_VERSION, "compatible_only": "false"},
        force_refresh,
    )
    try:
        snapshot = await asyncio.to_thread(_parse_snapshot, response)
    except (KeyError, TypeError, ValueError):
        sources.market_cache.invalidate(response["source_url"])
        raise
    return snapshot, {"source": "official", "source_url": response["source_url"], "cache": response["cache"]}


async def get_summary(force_refresh: bool = False) -> Dict[str, Any]:
    snapshot, metadata = await _snapshot(force_refresh)
    return {"success": True, "stats": snapshot, **metadata}


async def get_plugin(plugin_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    if not plugin_id.strip():
        raise ValueError("plugin_id 不能为空")
    url = f"{sources.MARKET_BASE_URL}/stats/{quote(plugin_id, safe='')}"

    def load() -> Dict[str, Any]:
        data = sources.GitSource.read_json(url, sources.REQUEST_TIMEOUT, sources.get_shared_ssl_context())
        if data.get("success") is not True:
            raise MarketError(502, f"官方统计接口返回失败：{data}")
        record = data["stats"]
        if record["plugin_id"] != plugin_id:
            raise ValueError(f"官方统计返回了其他插件的记录：{plugin_id} / {record['plugin_id']}")
        return normalize_statistics(record)

    # 独立缓存单插件评论和计数；只有验证成功的数据才能进入缓存。
    stats, cache = await asyncio.to_thread(
        sources.market_cache.get,
        url,
        load,
        sources.MARKET_TTL,
        force_refresh,
    )
    return {
        "success": True,
        "stats": stats,
        "source": "official",
        "source_url": url,
        "cache": cache,
    }
