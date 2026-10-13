"""发布版本索引与兼容版本选择；安装端和展示端使用相同的判断。"""

from time import monotonic
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Cookie, HTTPException
from packaging.version import Version
from pydantic import BaseModel, Field, ValidationError
import asyncio
import json

import httpx

from src.common.logger import get_logger
from src.config.config import MMC_VERSION
from src.plugin_runtime.runner.manifest_validator import ManifestValidator
from src.webui.services.git_mirror_service import get_git_mirror_service
from src.webui.services.plugin_market_service import request_market_detail, use_github_market_data

from .support import require_plugin_token

logger = get_logger("webui.plugin_releases")

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


def validate_release_entry(entry: PluginReleaseEntry) -> None:
    """校验单条版本索引条目的字段一致性。

    Args:
        entry: 版本索引中的插件条目。

    Raises:
        ValueError: 安装模式与版本列表不匹配、版本重复、Tag 或 manifest ID 不匹配。
    """
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


def parse_release_index(payload: str) -> Tuple[List[PluginReleaseEntry], List[str]]:
    """解析版本索引原始文本，逐条隔离条目级错误。

    Args:
        payload: 插件版本索引 JSON 文本。

    Returns:
        Tuple[List[PluginReleaseEntry], List[str]]: 可用条目，以及被跳过条目的说明。

    Raises:
        ValueError: 顶层结构无效（JSON 无法解析、schema_version 不是 1、plugins 不是数组）。
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：{exc}") from exc
    schema_version = data.get("schema_version") if isinstance(data, dict) else None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if isinstance(schema_version, bool) or schema_version != 1 or not isinstance(plugins, list):
        raise ValueError('顶层结构无效：需要 {"schema_version": 1, "plugins": [...]}')

    entries: List[PluginReleaseEntry] = []
    notices: List[str] = []
    for raw in plugins:
        try:
            entry = PluginReleaseEntry.model_validate(raw)
            validate_release_entry(entry)
        except (ValidationError, ValueError, KeyError) as exc:
            notices.append(f"跳过无效的插件版本索引条目：{exc}")
            continue
        entries.append(entry)
    return entries, notices


def deduplicate_release_entries(
    entries: List[PluginReleaseEntry],
) -> Tuple[List[PluginReleaseEntry], List[str]]:
    """按插件 ID 与 manifest ID 去重，保证每个别名只解析到一条可用记录。

    同一插件出现多条记录时优先保留没有 sync_error 的记录。若候选记录同时与多条已有记录冲突
    （其中存在可用记录），则直接跳过候选记录且**不做任何删除**，避免已有记录被移除后候选又被跳过、
    导致插件从索引里消失。

    Args:
        entries: 已通过字段校验的索引条目。

    Returns:
        Tuple[List[PluginReleaseEntry], List[str]]: 去重后的条目，以及被跳过或被替换记录的说明。
    """
    kept: Dict[str, PluginReleaseEntry] = {}
    alias_owner: Dict[str, str] = {}
    notices: List[str] = []

    def _release_aliases(entry_id: str) -> None:
        for key, owner in list(alias_owner.items()):
            if owner == entry_id:
                alias_owner.pop(key, None)

    for entry in entries:
        keys = {alias.casefold() for alias in (entry.id, entry.manifest_id) if alias}
        previous_entries: List[PluginReleaseEntry] = []
        seen_owners: set = set()
        for key in keys:
            owner = alias_owner.get(key)
            if owner is None or owner in seen_owners:
                continue
            seen_owners.add(owner)
            previous = kept.get(owner)
            if previous is not None:
                previous_entries.append(previous)
        if any(previous.sync_error is None for previous in previous_entries):
            notices.append(f"跳过插件版本索引条目（插件 ID 冲突：{'/'.join(sorted(keys))}）：{entry.id}")
            continue
        for previous in previous_entries:
            kept.pop(previous.id, None)
            _release_aliases(previous.id)
        if previous_entries:
            replaced = "/".join(sorted(previous.id for previous in previous_entries))
            notices.append(f"版本索引同名记录以可用记录替换同步失败记录：{replaced} → {entry.id}")
        kept[entry.id] = entry
        for key in keys:
            alias_owner[key] = entry.id
    return list(kept.values()), notices


async def load_release_index() -> PluginReleaseIndex:
    """加载插件版本索引，单条脏数据不影响其余条目。

    Returns:
        PluginReleaseIndex: 已剔除无效与冲突条目的版本索引。
    """
    global _cache, _cache_time
    if _cache is not None and monotonic() - _cache_time < 5 * 60:
        return _cache
    response = await get_git_mirror_service().fetch_raw_file(
        owner="Mai-with-u", repo="plugin-repo", branch="main", file_path="plugin_versions.json"
    )
    if not response.get("success"):
        raise HTTPException(status_code=502, detail=f"获取插件版本索引失败：{response.get('error', '未知错误')}")
    try:
        entries, notices = parse_release_index(response["data"])
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"插件版本索引无效：{exc}") from exc
    entries, dedup_notices = deduplicate_release_entries(entries)
    for notice in notices + dedup_notices:
        logger.warning(notice)
    if notices or dedup_notices:
        logger.warning(f"插件版本索引共忽略 {len(notices) + len(dedup_notices)} 条记录，其余 {len(entries)} 条已加载")
    index = PluginReleaseIndex(schema_version=1, plugins=entries)
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
    validate_release_entry(entry)
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
