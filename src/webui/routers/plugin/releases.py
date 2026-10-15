"""发布版本索引与兼容版本选择；安装端和展示端使用相同的判断。"""

from time import monotonic
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Cookie, HTTPException
from packaging.version import Version
from pydantic import BaseModel, Field, ValidationError
import asyncio

import httpx

from src.config.config import MMC_VERSION
from src.plugin_runtime.runner.manifest_validator import ManifestValidator
from src.webui.services.git_mirror_service import get_git_mirror_service
from src.webui.services.plugin_market_service import request_market_detail, use_github_market_data

from .support import require_plugin_token

router = APIRouter()


class PluginRelease(BaseModel):
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    tag: str = Field(min_length=1)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    prerelease: bool
    yanked: bool = False
    manifest: Dict[str, Any]
    published_at: Optional[str] = None
    release_url: Optional[str] = None
    release_notes: str = ""


class PluginReleaseEntry(BaseModel):
    id: str
    manifest_id: Optional[str] = None
    repositoryUrl: str = Field(pattern=r"^https://github\.com/[\w.-]+/[\w.-]+/?$")
    mode: Literal["releases", "branch"]
    versions: List[PluginRelease]
    sync_error: Optional[str] = None
    rejected_releases: List[Dict[str, str]] = Field(default_factory=list)


class PluginReleaseIndex(BaseModel):
    schema_version: Literal[1]
    plugins: List[PluginReleaseEntry]


_cache: Optional[PluginReleaseIndex] = None
_cache_time = 0.0


async def load_release_index() -> PluginReleaseIndex:
    global _cache, _cache_time
    if _cache is not None and monotonic() - _cache_time < 5 * 60:
        return _cache
    response = await get_git_mirror_service().fetch_raw_file(
        owner="Mai-with-u", repo="plugin-repo", branch="main", file_path="plugin_versions.json"
    )
    if not response.get("success"):
        raise HTTPException(status_code=502, detail=f"获取插件版本索引失败：{response.get('error', '未知错误')}")
    try:
        index = PluginReleaseIndex.model_validate_json(response["data"])
        aliases: Dict[str, str] = {}
        for entry in index.plugins:
            for alias in {entry.id, entry.manifest_id} - {None}:
                if entry.sync_error:
                    continue
                key = alias.casefold()
                if key in aliases and aliases[key] != entry.id:
                    raise ValueError(f"版本索引插件 ID 冲突：{alias}")
                aliases[key] = entry.id
            seen = set()
            for release in entry.versions:
                if release.version in seen or release.manifest.get("version") != release.version:
                    raise ValueError(f"版本索引版本重复或 manifest 不匹配：{entry.id}")
                if release.tag not in (release.version, f"v{release.version}"):
                    raise ValueError(f"版本索引 Tag 不匹配：{entry.id}")
                if release.manifest.get("manifest_version") == 2 and release.manifest.get("id") != entry.manifest_id:
                    raise ValueError(f"版本索引 manifest ID 不匹配：{entry.id}")
                seen.add(release.version)
            if bool(entry.versions or entry.rejected_releases) != (entry.mode == "releases"):
                raise ValueError(f"版本索引安装模式不匹配：{entry.id}")
    except (ValidationError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"插件版本索引无效：{exc}") from exc
    _cache, _cache_time = index, monotonic()
    return index


def release_compatibility(release: PluginRelease) -> List[str]:
    validator = ManifestValidator(
        validate_python_package_dependencies=False, log_errors=False, log_compat_warnings=False
    )
    validator.parse_manifest(release.manifest)
    reasons = list(validator.errors)
    if release.yanked:
        reasons.append("该版本已撤回")
    return reasons


def describe_entry(entry: PluginReleaseEntry) -> Dict[str, Any]:
    versions = []
    recommended = None
    for release in sorted(entry.versions, key=lambda item: Version(item.version), reverse=True):
        reasons = release_compatibility(release)
        if entry.sync_error:
            reasons.append(f"版本同步失败：{entry.sync_error}")
        versions.append({**release.model_dump(), "compatible": not reasons, "reasons": reasons})
        if recommended is None and not reasons and not release.prerelease:
            recommended = release.version
    return {**entry.model_dump(exclude={"versions"}), "versions": versions, "recommended_version": recommended}


def parse_market_release_entry(data: Dict[str, Any], plugin_id: Optional[str] = None) -> PluginReleaseEntry:
    entry = PluginReleaseEntry.model_validate({
        "id": data["marketplace_id"], "manifest_id": data["id"],
        "repositoryUrl": data["repository_url"], "mode": data["install_mode"],
        "versions": data["versions"], "sync_error": data.get("sync_error"),
        "rejected_releases": data.get("rejected_releases", []),
    })
    if plugin_id is not None and plugin_id.casefold() not in {entry.id.casefold(), (entry.manifest_id or "").casefold()}:
        raise ValueError("插件中心返回的插件 ID 与请求不一致")
    if bool(entry.versions or entry.rejected_releases) != (entry.mode == "releases"):
        raise ValueError(f"版本索引安装模式不匹配：{entry.id}")
    seen = set()
    for release in entry.versions:
        if release.version in seen or release.manifest.get("version") != release.version:
            raise ValueError(f"版本索引版本重复或 manifest 不匹配：{entry.id}")
        if release.tag not in (release.version, f"v{release.version}"):
            raise ValueError(f"版本索引 Tag 不匹配：{entry.id}")
        if release.manifest.get("manifest_version") == 2 and release.manifest.get("id") != entry.manifest_id:
            raise ValueError(f"版本索引 manifest ID 不匹配：{entry.id}")
        seen.add(release.version)
    return entry


async def load_plugin_release_entry(plugin_id: str) -> Optional[PluginReleaseEntry]:
    if await asyncio.to_thread(use_github_market_data):
        index = await load_release_index()
        return next(
            (item for item in index.plugins if plugin_id.casefold() in {item.id.casefold(), (item.manifest_id or "").casefold()}),
            None,
        )
    try:
        data = await request_market_detail(plugin_id, MMC_VERSION)
        return await asyncio.to_thread(parse_market_release_entry, data, plugin_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise HTTPException(status_code=502, detail=f"获取插件中心版本失败：{exc}") from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"插件中心版本数据无效：{exc}") from exc


async def resolve_release(plugin_id: str, version: str) -> Tuple[PluginReleaseEntry, Optional[PluginRelease]]:
    entry = await load_plugin_release_entry(plugin_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="插件尚未收录到版本索引，请先同步插件中心")
    if entry.sync_error:
        raise HTTPException(status_code=409, detail=f"插件版本同步失败：{entry.sync_error}")
    if entry.mode == "branch":
        if version != "latest":
            raise HTTPException(status_code=400, detail="该插件尚未发布 Release，只支持分支安装")
        return entry, None
    description = await asyncio.to_thread(describe_entry, entry)
    target = description["recommended_version"] if version == "latest" else version
    release = next((item for item in entry.versions if item.version == target), None)
    if release is None:
        raise HTTPException(status_code=400, detail="没有可安装的兼容稳定版本" if version == "latest" else "发布版本不存在")
    reasons = await asyncio.to_thread(release_compatibility, release)
    if reasons:
        raise HTTPException(status_code=400, detail="；".join(reasons))
    return entry, release


@router.get("/releases")
async def get_plugin_releases(maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    index = await load_release_index()
    return {"plugins": [describe_entry(entry) for entry in index.plugins]}
