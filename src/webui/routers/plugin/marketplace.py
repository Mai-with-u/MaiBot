"""插件中心 v2 代理与市场数据源设置。"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel
import asyncio

import httpx

from src.config.config import MMC_VERSION
from src.webui.services.plugin_market_service import (
    request_market,
    request_market_detail,
    save_market_source,
    use_github_market_data,
)

from .releases import PluginReleaseEntry, describe_entry, parse_market_release_entry
from .support import require_plugin_token

router = APIRouter()


class MarketSourceSettings(BaseModel):
    use_github: bool


@router.get("/marketplace/source", response_model=MarketSourceSettings)
def get_market_source(maibot_session: Optional[str] = Cookie(None)) -> MarketSourceSettings:
    require_plugin_token(maibot_session)
    return MarketSourceSettings(use_github=use_github_market_data())


@router.put("/marketplace/source", response_model=MarketSourceSettings)
def update_market_source(
    request: MarketSourceSettings, maibot_session: Optional[str] = Cookie(None)
) -> MarketSourceSettings:
    require_plugin_token(maibot_session)
    save_market_source(request.use_github)
    return request


def market_entry(data: Dict[str, Any]) -> PluginReleaseEntry:
    """服务端选中的版本仍由宿主校验 SDK、清单格式与依赖声明。"""
    return PluginReleaseEntry.model_validate(
        {
            "id": data["marketplace_id"],
            "manifest_id": data["id"],
            "repositoryUrl": data["repository_url"],
            "mode": data["install_mode"],
            "versions": [data["release"]] if data.get("release") else [],
            "sync_error": data.get("sync_error"),
        }
    )


def describe_market_list(data: Dict[str, Any]) -> Dict[str, Any]:
    details = []
    catalog = []
    stats = {}
    for item in data["plugins"]:
        entry = market_entry(item)
        details.append(
            {
                "id": entry.id,
                "manifest": item["manifest"],
                "assets": {"icon_64": item["icon_url"]} if item.get("icon_url") else None,
            }
        )
        description = describe_entry(entry)
        # 未提供可推荐发布版本时不能让空版本列表被误判成分支插件。
        if entry.mode == "releases" and not entry.versions:
            description["recommended_version"] = None
        catalog.append(description)
        stats[item["id"]] = item["stats"]
    return {
        "source": "service",
        "details": details,
        "catalog": {"plugins": catalog},
        "stats": stats,
        "stats_as_of": data.get("stats_as_of"),
        "growth_ranking": data.get("growth_ranking"),
    }


@router.get("/marketplace")
async def get_marketplace(
    compatible_only: bool = False, maibot_session: Optional[str] = Cookie(None)
) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    if await asyncio.to_thread(use_github_market_data):
        return {"source": "github"}
    try:
        data = await request_market(
            "plugins/summary", {"maibot_version": MMC_VERSION, "compatible_only": str(compatible_only).lower()}
        )
        return await asyncio.to_thread(describe_market_list, data)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"获取插件中心列表失败：{exc}") from exc


@router.get("/marketplace/{plugin_id}")
async def get_marketplace_detail(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    try:
        data = await request_market_detail(plugin_id, MMC_VERSION)
        entry = await asyncio.to_thread(parse_market_release_entry, data, plugin_id)
        catalog = await asyncio.to_thread(describe_entry, entry)
        return {"manifest": data["manifest"], "releases": catalog}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="插件中心未找到该插件") from exc
        raise HTTPException(status_code=502, detail=f"获取插件中心详情失败：{exc}") from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"获取插件中心详情失败：{exc}") from exc
