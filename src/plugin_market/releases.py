"""插件市场的发布清单模型与兼容版本判断，不依赖 WebUI 路由。"""

from typing import Any, Dict, List, Literal, Optional

from packaging.version import Version
from pydantic import BaseModel, Field

from src.plugin_runtime.runner.manifest_validator import ManifestValidator


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


def manifest_compatibility(manifest: Dict[str, Any]) -> List[str]:
    validator = ManifestValidator(
        validate_python_package_dependencies=False, log_errors=False, log_compat_warnings=False
    )
    validator.parse_manifest(manifest)
    return list(validator.errors)


def release_compatibility(release: PluginRelease) -> List[str]:
    reasons = manifest_compatibility(release.manifest)
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
    _validate_release_entry(entry)
    return entry


def parse_release_index(content: str) -> PluginReleaseIndex:
    index = PluginReleaseIndex.model_validate_json(content)
    aliases: Dict[str, str] = {}
    for entry in index.plugins:
        for alias in {entry.id, entry.manifest_id} - {None}:
            if entry.sync_error:
                continue
            key = alias.casefold()
            if key in aliases and aliases[key] != entry.id:
                raise ValueError(f"版本索引插件 ID 冲突：{alias}")
            aliases[key] = entry.id
        _validate_release_entry(entry)
    return index


def _validate_release_entry(entry: PluginReleaseEntry) -> None:
    # Release 待审核时两个列表都可为空，但不能因此退回分支安装。
    if entry.mode == "branch" and (entry.versions or entry.rejected_releases):
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
