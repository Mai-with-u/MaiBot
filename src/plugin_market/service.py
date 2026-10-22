"""插件市场只读查询；不依赖 WebUI 服务启动或登录状态。"""

from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.error import HTTPError
import asyncio
import json

from src.common.git_source import GitSource
from src.config.config import MMC_VERSION

from .catalog import _filter_plugins, _normalize_plugin_catalog_item, _sort_stat_plugins
from .documents import get_document
from .errors import MarketError
from .releases import (
    PluginRelease,
    PluginReleaseEntry,
    PluginReleaseIndex,
    describe_entry,
    manifest_compatibility,
    parse_market_release_entry,
    parse_release_index,
    release_compatibility,
)
from .sources import fetch_information_file, load_source_settings, request_market, request_market_detail

MarketSort = Literal[
    "name", "downloads", "likes", "rating", "downloads_growth_7d", "likes_growth_7d",
    "downloads_growth_30d", "likes_growth_30d",
]


class MarketSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=500)
    category: str = Field(default="", max_length=100)
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=50)
    sort_by: MarketSort = "name"
    compatible_only: bool = False
    include_manifest: bool = False
    include_stats: bool = False
    force_refresh: bool = False


def _release_description(entry: PluginReleaseEntry, manifest: Dict[str, Any]) -> Dict[str, Any]:
    description = describe_entry(entry)
    if entry.mode == "branch":
        reasons = manifest_compatibility(manifest)
        if entry.sync_error:
            reasons.append(f"版本同步失败：{entry.sync_error}")
        description.update(branch_compatible=not reasons, branch_reasons=reasons)
    return description


def _catalog(
    items: List[Dict[str, Any]], source: str, index: Optional[PluginReleaseIndex] = None,
) -> List[Dict[str, Any]]:
    """所有消费者共用清单合并、ID、图标和版本兼容性处理。"""
    records = {item["id"]: item for item in items} if source != "official" else {}
    entries = {entry.id: entry for entry in index.plugins} if index is not None else {}
    # Tag 中的有效清单也必须保留，不能因分支清单失效而丢失发布版本入口。
    if index is not None:
        for entry in index.plugins:
            record = records.get(entry.id)
            if record is None or not record.get("manifest") or record["manifest"].get("manifest_version", 1) < 2:
                description = describe_entry(entry)
                release = next((r for r in entry.versions if r.version == description["recommended_version"]), None)
                if release is None and entry.versions:
                    release = max(entry.versions, key=lambda r: Version(r.version))
                if release is not None:
                    records[entry.id] = {**(record if record is not None else {}),
                                         "id": entry.id, "manifest": release.manifest}
        items = list(records.values())
    plugins = []
    for item in items:
        if source == "official":
            entry = parse_market_release_entry({**item, "versions": [item["release"]] if item.get("release") else []})
            raw = {"id": item["marketplace_id"], "manifest": item["manifest"]}
        else:
            entry = entries.get(item["id"])
            raw = item
        if entry is not None:
            description = describe_entry(entry)
            recommended = next((r for r in entry.versions if r.version == description["recommended_version"]), None)
            if recommended is not None:
                raw = {**raw, "manifest": recommended.manifest}
        plugin = _normalize_plugin_catalog_item(raw, include_manifest=True)
        if plugin is None:
            continue
        stats = item.get("stats") if source == "official" else None
        if stats is not None and not isinstance(stats, dict):
            raise ValueError(f"插件 {plugin['id']} 的统计数据格式无效")
        plugin["stats"] = {**stats, "plugin_id": plugin["id"]} if stats is not None else None
        if entry is None:
            entry = PluginReleaseEntry(
                id=plugin["marketplace_id"], manifest_id=plugin["id"], repositoryUrl=plugin["repository_url"],
                mode="releases", versions=[], sync_error="插件尚未收录到版本索引，请先同步插件中心",
            )
        plugin["releases"] = _release_description(entry, plugin["manifest"])
        assets = item.get("assets") or {}
        icon = item.get("icon_url") if source == "official" else assets.get("icon_64")
        if icon and not icon.startswith(("https://", "http://")):
            icon = GitSource.select("github" if source == "official" else source).raw_url(
                "Mai-with-u", "plugin-repo", "main", icon,
            )
        plugin["assets"] = {"icon_64": icon} if icon else None
        for field in ("changelog", "published_at", "updated_at"):
            plugin[field] = item.get(field)
        plugins.append(plugin)
    return plugins


async def _snapshot(force_refresh: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    source = (await asyncio.to_thread(load_source_settings)).information_source
    if source == "official":
        data = await request_market(
            "plugins/summary", {"maibot_version": MMC_VERSION, "compatible_only": "false"}, force_refresh,
        )
        plugins = await asyncio.to_thread(_catalog, data["plugins"], source)
        return plugins, {
            "source": source, "source_url": data["source_url"], "cache": data["cache"],
            "stats_as_of": data.get("stats_as_of"), "growth_ranking": data.get("growth_ranking"),
            "features": {"stats": True, "releases": True},
        }
    details, index = await asyncio.gather(
        fetch_information_file("plugin_details.json", source, force_refresh), _git_releases(source, force_refresh),
    )

    def parse() -> List[Dict[str, Any]]:
        items = json.loads(details["data"])
        if not isinstance(items, list):
            raise ValueError("插件市场列表响应格式不是列表")
        return _catalog(items, source, index)

    return await asyncio.to_thread(parse), {
        "source": source, "source_url": details["url"], "cache": details["cache"],
        "features": {"stats": False, "releases": True},
    }


def _is_compatible(plugin: Dict[str, Any]) -> bool:
    releases = plugin["releases"]
    return bool(releases["branch_compatible"] if releases["mode"] == "branch" else releases["recommended_version"])


async def get_catalog(compatible_only: bool = False, force_refresh: bool = False) -> Dict[str, Any]:
    plugins, metadata = await _snapshot(force_refresh)
    if compatible_only:
        plugins = await asyncio.to_thread(lambda: [plugin for plugin in plugins if _is_compatible(plugin)])
    return {"success": True, **metadata, "plugins": plugins}


def _resolve(reference: str, plugins: List[Dict[str, Any]]) -> Dict[str, Any]:
    reference = reference.strip()
    if not reference:
        raise ValueError("plugin_id 不能为空")
    matches = [plugin for plugin in plugins if any(
        plugin[field].casefold() == reference.casefold()
        for field in ("id", "marketplace_id", "name", "repository_url")
    )]
    if not matches:
        matches = _filter_plugins(plugins, reference, "")
    # 名称或仓库模糊匹配必须唯一，不能把第一个候选误当成目标插件。
    if len(matches) != 1:
        candidates = ", ".join(plugin["id"] for plugin in matches[:5])
        raise MarketError(409 if matches else 404, f"未找到插件或匹配多个插件，请指定准确 ID。候选：{candidates}")
    return matches[0]


async def _git_releases(source: str, force_refresh: bool) -> PluginReleaseIndex:
    response = await fetch_information_file("plugin_versions.json", source, force_refresh)
    return await asyncio.to_thread(parse_release_index, response["data"])


async def search(request: MarketSearch) -> Dict[str, Any]:
    plugins, metadata = await _snapshot(request.force_refresh)
    if (request.include_stats or request.sort_by != "name") and not metadata["features"]["stats"]:
        raise ValueError(f"当前信息源 {metadata['source']} 不支持插件统计或排行榜，请在市场设置中选择官方源")

    def select() -> Dict[str, Any]:
        candidates = plugins
        if request.compatible_only:
            candidates = [plugin for plugin in candidates if _is_compatible(plugin)]
        if request.sort_by != "name":
            candidates = _sort_stat_plugins(candidates, request.sort_by)
        else:
            candidates = sorted(candidates, key=lambda plugin: (plugin["name"].casefold(), plugin["id"]))
        filtered = _filter_plugins(candidates, request.query, request.category)
        start = (request.page - 1) * request.limit
        pages = (len(filtered) + request.limit - 1) // request.limit
        fields = {"releases"}
        if not request.include_manifest:
            fields.add("manifest")
        if not request.include_stats and request.sort_by == "name":
            fields.add("stats")
        return {
            "success": True, **metadata, "query": request.query, "category": request.category,
            "sort_by": request.sort_by, "total": len(plugins), "matched": len(filtered),
            "page": request.page, "limit": request.limit,
            "pagination": {
                "total_pages": pages, "has_next": request.page < pages,
                "has_previous": request.page > 1 and pages > 0,
            },
            "available_categories": sorted({category for plugin in plugins for category in plugin["categories"]}),
            "plugins": [{key: value for key, value in plugin.items() if key not in fields}
                        for plugin in filtered[start:start + request.limit]],
        }

    return await asyncio.to_thread(select)


async def get_detail(plugin_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    plugins, metadata = await _snapshot(force_refresh)
    plugin = await asyncio.to_thread(_resolve, plugin_id, plugins)
    if metadata["source"] == "official":
        data = await request_market_detail(plugin["id"], MMC_VERSION, force_refresh)
        entry = await asyncio.to_thread(parse_market_release_entry, data, plugin["id"])
        if data["manifest"]["id"] != plugin["id"]:
            raise ValueError("插件中心返回的 manifest ID 与请求不一致")
        metadata.update(cache=data["cache"], source_url=data["source_url"])
        detail = await asyncio.to_thread(
            _normalize_plugin_catalog_item, {"id": data["marketplace_id"], "manifest": data["manifest"]}, True,
        )
        if detail is None:
            raise ValueError("插件中心返回了无效的插件清单")
        plugin = {**detail, "stats": data.get("stats", plugin["stats"])}
    else:
        index = await _git_releases(metadata["source"], force_refresh)
        entry = next((item for item in index.plugins if item.id == plugin["marketplace_id"]), None)
        if entry is None:
            raise ValueError(f"版本索引中没有插件 {plugin['marketplace_id']}")
    releases = await asyncio.to_thread(_release_description, entry, plugin["manifest"])
    plugin.pop("releases", None)
    return {"success": True, **metadata, "plugin": plugin, "releases": releases}


async def _document_plugin(plugin_id: str, force_refresh: bool) -> Tuple[Dict[str, Any], str]:
    plugins, metadata = await _snapshot(force_refresh)
    return await asyncio.to_thread(_resolve, plugin_id, plugins), metadata["source"]


async def get_readme(
    plugin_id: str, max_chars: int = 8000, prefer_local: bool = True,
    branch: str = "main", force_refresh: bool = False,
) -> Dict[str, Any]:
    return await get_document(plugin_id, "readme", _document_plugin, max_chars, prefer_local, branch, force_refresh)


async def get_changelog(
    plugin_id: str, max_chars: int = 200000, prefer_local: bool = True,
    branch: str = "main", force_refresh: bool = False,
) -> Dict[str, Any]:
    return await get_document(plugin_id, "changelog", _document_plugin, max_chars, prefer_local, branch, force_refresh)


async def get_stats(plugin_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    from .engagement import get_reviews

    plugins, metadata = await _snapshot(force_refresh)
    plugin = await asyncio.to_thread(_resolve, plugin_id, plugins)
    if not metadata["features"]["stats"] or plugin["stats"] is None:
        return {"success": True, "stats": None, **metadata}
    reviews = await get_reviews(plugin["id"])
    return {"success": True, **metadata, "stats": {**plugin["stats"], **reviews, "plugin_id": plugin["id"]}}


async def load_release_index(force_refresh: bool = False) -> PluginReleaseIndex:
    source = (await asyncio.to_thread(load_source_settings)).information_source
    return await _git_releases(source, force_refresh)


async def load_plugin_release_entry(plugin_id: str, force_refresh: bool = False) -> Optional[PluginReleaseEntry]:
    if (await asyncio.to_thread(load_source_settings)).information_source != "official":
        index = await load_release_index(force_refresh)
        return next(
            (item for item in index.plugins if plugin_id.casefold() in {item.id.casefold(), (item.manifest_id or "").casefold()}),
            None,
        )
    try:
        data = await request_market_detail(plugin_id, MMC_VERSION, force_refresh)
        return await asyncio.to_thread(parse_market_release_entry, data, plugin_id)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise MarketError(502, f"获取插件中心版本失败：{exc}") from exc


async def resolve_release(plugin_id: str, version: str) -> Tuple[PluginReleaseEntry, Optional[PluginRelease]]:
    # 安装前重新校验远端发布状态，不能使用仍在 TTL 内的旧撤回/兼容信息。
    entry = await load_plugin_release_entry(plugin_id, force_refresh=True)
    if entry is None:
        raise MarketError(404, "插件尚未收录到版本索引，请先同步插件中心")
    if entry.sync_error:
        raise MarketError(409, f"插件版本同步失败：{entry.sync_error}")
    if entry.mode == "branch":
        if version != "latest":
            raise MarketError(400, "该插件尚未发布 Release，只支持分支安装")
        return entry, None
    if not entry.versions:
        raise MarketError(400, "暂无可安装版本")
    description = await asyncio.to_thread(describe_entry, entry)
    target = description["recommended_version"] if version == "latest" else version
    release = next((item for item in entry.versions if item.version == target), None)
    if release is None:
        raise MarketError(400, "没有可安装的兼容稳定版本" if version == "latest" else "发布版本不存在")
    reasons = await asyncio.to_thread(release_compatibility, release)
    if reasons:
        raise MarketError(400, "；".join(reasons))
    return entry, release


