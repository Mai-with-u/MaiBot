"""插件市场只读 Host 能力。授权由统一 CapabilityService 校验。"""

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from src.plugin_market import service


class MarketDetailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str = Field(min_length=1, max_length=500)
    force_refresh: bool = False


class MarketReadmeRequest(MarketDetailRequest):
    max_chars: int = Field(default=8000, ge=500, le=20000)
    prefer_local: bool = True
    branch: str = Field(default="main", min_length=1, max_length=200)


async def search(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, "result": await service.search(service.MarketSearch.model_validate(args))}


async def get_detail(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    request = MarketDetailRequest.model_validate(args)
    return {"success": True, "result": await service.get_detail(**request.model_dump())}


async def get_readme(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    request = MarketReadmeRequest.model_validate(args)
    return {"success": True, "result": await service.get_readme(**request.model_dump())}
