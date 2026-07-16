"""主程序更新后的插件兼容性提醒。"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

import asyncio
import json
import time

from src.common.logger import get_logger
from src.common.version import PROJECT_ROOT
from src.plugin_runtime.runner.manifest_validator import VersionComparator, is_reserved_plugin_directory


logger = get_logger("plugin_update_compatibility")

UpdateCheckStatus = Literal["checking", "available", "unavailable", "not_found", "check_failed"]
MarketplaceLoader = Callable[[], Awaitable[List[Dict[str, Any]]]]

_PLUGIN_REPO_OWNER = "Mai-with-u"
_PLUGIN_REPO_NAME = "plugin-repo"
_PLUGIN_REPO_BRANCH = "main"
_PLUGIN_DETAILS_FILE = "plugin_details.json"
_MARKETPLACE_CACHE_TTL_SECONDS = 5 * 60
_MARKETPLACE_FETCH_TIMEOUT_SECONDS = 10
_marketplace_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None


@dataclass(frozen=True)
class IncompatiblePluginNotice:
    """一次主程序更新导致的不兼容插件。"""

    plugin_id: str
    name: str
    installed_version: str
    host_min_version: str
    host_max_version: str
    update_status: UpdateCheckStatus = "checking"
    update_version: Optional[str] = None


@dataclass(frozen=True)
class _PluginDescriptor:
    plugin_id: str
    name: str
    version: str
    host_min_version: str
    host_max_version: str


def _is_host_compatible(host_version: str, min_version: str, max_version: str) -> bool:
    """使用与插件运行时一致的 Host 兼容规则。"""

    in_range, _ = VersionComparator.is_in_range(host_version, min_version, max_version)
    if in_range:
        return True
    return VersionComparator.is_same_major_minor_higher_version(host_version, max_version)


def _parse_plugin_descriptor(
    manifest: Dict[str, Any],
    *,
    fallback_id: str = "",
    source: str,
) -> Optional[_PluginDescriptor]:
    plugin_id = str(manifest.get("id") or fallback_id).strip()
    name = str(manifest.get("name") or plugin_id).strip()
    version = str(manifest.get("version") or "").strip()
    host_application = manifest.get("host_application")
    if not isinstance(host_application, dict):
        logger.warning(f"跳过缺少 host_application 的插件清单: {source}")
        return None

    min_version = str(host_application.get("min_version") or "").strip()
    max_version = str(host_application.get("max_version") or "").strip()
    required_versions = (version, min_version, max_version)
    if (
        not plugin_id
        or not name
        or not all(VersionComparator.is_valid_semver(item) for item in required_versions)
    ):
        logger.warning(f"跳过插件 ID 或版本范围无效的插件清单: {source}")
        return None
    if VersionComparator.compare(min_version, max_version) > 0:
        logger.warning(f"跳过 Host 版本范围颠倒的插件清单: {source}")
        return None

    return _PluginDescriptor(
        plugin_id=plugin_id,
        name=name,
        version=version,
        host_min_version=min_version,
        host_max_version=max_version,
    )


def find_plugins_made_incompatible(
    from_version: str,
    current_version: str,
    plugins_dir: Path = PROJECT_ROOT / "plugins",
) -> List[IncompatiblePluginNotice]:
    """查找旧版兼容、升级到当前版本后不再兼容的已安装插件。"""

    if not plugins_dir.is_dir():
        return []

    try:
        plugin_paths = sorted(plugins_dir.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        logger.error(f"扫描已安装插件失败: {plugins_dir}, error={exc}")
        return []

    incompatible_plugins: List[IncompatiblePluginNotice] = []
    for plugin_path in plugin_paths:
        if not plugin_path.is_dir() or plugin_path.is_symlink() or is_reserved_plugin_directory(plugin_path):
            continue

        manifest_path = plugin_path / "_manifest.json"
        try:
            with manifest_path.open("r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"读取插件清单失败，已跳过: {manifest_path}, error={exc}")
            continue

        if not isinstance(manifest, dict):
            logger.warning(f"插件清单顶层不是对象，已跳过: {manifest_path}")
            continue
        descriptor = _parse_plugin_descriptor(manifest, source=str(manifest_path))
        if descriptor is None:
            continue

        was_compatible = _is_host_compatible(
            from_version,
            descriptor.host_min_version,
            descriptor.host_max_version,
        )
        is_compatible = _is_host_compatible(
            current_version,
            descriptor.host_min_version,
            descriptor.host_max_version,
        )
        if not was_compatible or is_compatible:
            continue

        incompatible_plugins.append(
            IncompatiblePluginNotice(
                plugin_id=descriptor.plugin_id,
                name=descriptor.name,
                installed_version=descriptor.version,
                host_min_version=descriptor.host_min_version,
                host_max_version=descriptor.host_max_version,
            )
        )

    return incompatible_plugins


def mark_compatible_marketplace_updates(
    incompatible_plugins: List[IncompatiblePluginNotice],
    marketplace_plugins: List[Dict[str, Any]],
    current_version: str,
) -> List[IncompatiblePluginNotice]:
    """根据插件市场当前清单标记是否存在更高且兼容的插件版本。"""

    marketplace_by_id: Dict[str, _PluginDescriptor] = {}
    for item in marketplace_plugins:
        if not isinstance(item, dict):
            continue
        manifest = item.get("manifest")
        if not isinstance(manifest, dict):
            continue
        descriptor = _parse_plugin_descriptor(
            manifest,
            fallback_id=str(item.get("id") or "").strip(),
            source=f"插件市场:{item.get('id') or manifest.get('id') or 'unknown'}",
        )
        if descriptor is None:
            continue
        marketplace_by_id[descriptor.plugin_id.casefold()] = descriptor

    results: List[IncompatiblePluginNotice] = []
    for plugin in incompatible_plugins:
        marketplace_plugin = marketplace_by_id.get(plugin.plugin_id.casefold())
        if marketplace_plugin is None:
            results.append(replace(plugin, update_status="not_found"))
            continue

        has_newer_version = (
            VersionComparator.compare(marketplace_plugin.version, plugin.installed_version) > 0
        )
        update_is_compatible = _is_host_compatible(
            current_version,
            marketplace_plugin.host_min_version,
            marketplace_plugin.host_max_version,
        )
        if has_newer_version and update_is_compatible:
            results.append(
                replace(
                    plugin,
                    update_status="available",
                    update_version=marketplace_plugin.version,
                )
            )
            continue
        results.append(replace(plugin, update_status="unavailable"))

    return results


async def _fetch_marketplace_plugins() -> List[Dict[str, Any]]:
    global _marketplace_cache

    now = time.monotonic()
    if _marketplace_cache is not None and now - _marketplace_cache[0] < _MARKETPLACE_CACHE_TTL_SECONDS:
        return _marketplace_cache[1]

    # 复用插件管理页的镜像源配置，避免启动检查与手动更新使用不同的数据源。
    from src.webui.services.git_mirror_service import get_git_mirror_service

    service = get_git_mirror_service()
    try:
        result = await asyncio.wait_for(
            service.fetch_raw_file(
                owner=_PLUGIN_REPO_OWNER,
                repo=_PLUGIN_REPO_NAME,
                branch=_PLUGIN_REPO_BRANCH,
                file_path=_PLUGIN_DETAILS_FILE,
                report_progress=False,
            ),
            timeout=_MARKETPLACE_FETCH_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise RuntimeError("获取插件市场清单超时") from exc

    if not result.get("success"):
        raise RuntimeError(str(result.get("error") or "获取插件市场清单失败"))

    raw_data = result.get("data")
    if not isinstance(raw_data, str):
        raise ValueError("插件市场清单响应缺少 data 字段")
    marketplace_plugins = json.loads(raw_data)
    if not isinstance(marketplace_plugins, list):
        raise ValueError("插件市场清单顶层必须为数组")

    normalized_plugins = [item for item in marketplace_plugins if isinstance(item, dict)]
    _marketplace_cache = (time.monotonic(), normalized_plugins)
    return normalized_plugins


async def collect_update_incompatible_plugins(
    from_version: str,
    current_version: str,
    plugins_dir: Path = PROJECT_ROOT / "plugins",
    marketplace_loader: Optional[MarketplaceLoader] = None,
) -> List[IncompatiblePluginNotice]:
    """收集更新导致的不兼容插件，并检查插件市场中的兼容更新。"""

    incompatible_plugins = find_plugins_made_incompatible(from_version, current_version, plugins_dir)
    if not incompatible_plugins:
        return []

    logger.info(f"检测到 {len(incompatible_plugins)} 个插件因主程序更新不再兼容，正在检查兼容更新")
    loader = marketplace_loader or _fetch_marketplace_plugins
    try:
        marketplace_plugins = await loader()
    except Exception as exc:
        logger.warning(f"检查插件兼容更新失败: {exc}")
        return [replace(plugin, update_status="check_failed") for plugin in incompatible_plugins]

    return mark_compatible_marketplace_updates(incompatible_plugins, marketplace_plugins, current_version)


def format_terminal_compatibility_notice(
    from_version: str,
    current_version: str,
    plugins: List[IncompatiblePluginNotice],
) -> str:
    """生成终端插件兼容性提醒。"""

    lines = [
        "插件兼容性提醒",
        "=" * 48,
        f"以下插件因 MaiBot 从 v{from_version} 更新到 v{current_version} 后不再兼容：",
    ]
    for plugin in plugins:
        if plugin.update_status == "available":
            update_text = f"可更新至 v{plugin.update_version}（兼容当前版本）"
        elif plugin.update_status == "check_failed":
            update_text = "兼容更新检查失败"
        elif plugin.update_status == "not_found":
            update_text = "插件市场中未找到该插件"
        else:
            update_text = "暂无兼容更新"
        lines.append(
            f"- {plugin.name} ({plugin.plugin_id}) v{plugin.installed_version}："
            f"支持 MaiBot v{plugin.host_min_version} - v{plugin.host_max_version}；{update_text}"
        )
    lines.append("=" * 48)
    return "\n".join(lines)
