"""插件 WebUI 页面声明的 Host 侧注册表。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Collection, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from src.common.logger import get_logger
from src.plugin_runtime.runner.manifest_validator import ManifestValidator, ManifestWebUiPage, PluginManifest

logger = get_logger("webui.plugin_page_registry")

_PLUGIN_PAGE_ROUTE_PREFIX = "/plugin-pages"
_PLUGIN_API_PREFIX = "/api/webui/plugins"
_WEBUI_DIST_DIRECTORY = "webui/dist"


@dataclass(frozen=True, slots=True)
class PluginPageRecord:
    """页面声明解析后的 Host 内部记录。"""

    plugin_id: str
    page_id: str
    title: str
    route: str
    entry_url: str
    component: str
    icon: Optional[str]
    order: int
    permissions: Tuple[str, ...]
    api: Dict[str, str]
    api_base: str
    plugin_path: Path
    entry_path: Path
    plugin_version: str

    def to_response_dict(self) -> Dict[str, object]:
        """转换为不暴露本地路径的 WebUI 响应结构。"""
        return {
            "plugin_id": self.plugin_id,
            "page_id": self.page_id,
            "title": self.title,
            "route": self.route,
            "entry": self.entry_url,
            "component": self.component,
            "icon": self.icon,
            "order": self.order,
            "permissions": list(self.permissions),
            "api_base": self.api_base,
        }


def _resolve_page_entry_path(plugin_path: Path, entry: str) -> Path:
    """解析页面入口并确保其位于插件的 WebUI 构建目录。"""
    resolved_plugin_path = plugin_path.resolve()
    if plugin_path.is_symlink() or not resolved_plugin_path.is_dir():
        raise ValueError("插件目录不能是符号链接或不存在")

    webui_dist_path = (resolved_plugin_path / _WEBUI_DIST_DIRECTORY).resolve()
    try:
        webui_dist_path.relative_to(resolved_plugin_path)
    except ValueError as exc:
        raise ValueError("webui/dist 目录超出插件根目录") from exc

    candidate_path = resolved_plugin_path / entry
    if candidate_path.exists() and candidate_path.is_symlink():
        raise ValueError(f"页面入口不能是符号链接: {entry}")

    try:
        resolved_candidate_path = candidate_path.resolve()
        resolved_candidate_path.relative_to(webui_dist_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"页面入口超出 webui/dist 目录: {entry}") from exc

    if not resolved_candidate_path.exists() or not resolved_candidate_path.is_file():
        raise ValueError(f"入口文件不存在: {entry}")
    return resolved_candidate_path


def _build_page_record(plugin_path: Path, manifest: PluginManifest, page: ManifestWebUiPage) -> PluginPageRecord:
    """根据强类型 Manifest 页面生成 Host 页面记录。"""
    plugin_id = manifest.id
    page_id = page.id
    entry_path = _resolve_page_entry_path(plugin_path, page.entry)
    encoded_plugin_id = quote(plugin_id, safe="")
    encoded_route = quote(page.route, safe="")
    encoded_page_id = quote(page_id, safe="")
    encoded_entry = quote(page.entry, safe="/")

    return PluginPageRecord(
        plugin_id=plugin_id,
        page_id=page_id,
        title=page.title,
        route=f"{_PLUGIN_PAGE_ROUTE_PREFIX}/{encoded_plugin_id}/{encoded_route}",
        entry_url=(
            f"{_PLUGIN_API_PREFIX}/{encoded_plugin_id}/assets/{encoded_entry}"
            f"?v={quote(manifest.version, safe='')}"
        ),
        component=page.component,
        icon=page.icon,
        order=page.order,
        permissions=tuple(page.permissions),
        api=dict(page.api),
        api_base=f"{_PLUGIN_API_PREFIX}/{encoded_plugin_id}/pages/{encoded_page_id}/api",
        plugin_path=plugin_path.resolve(),
        entry_path=entry_path,
        plugin_version=manifest.version,
    )


def discover_plugin_pages(
    plugin_paths: Iterable[Path],
    loaded_plugin_ids: Collection[str],
) -> Tuple[List[PluginPageRecord], List[str]]:
    """扫描已加载插件的 WebUI 页面声明并生成 Host 页面记录。

    Manifest 校验保护声明契约，文件系统解析保护实际资源边界；两层校验都必须保留。

    返回 (有效页面列表, 警告列表)；单个页面声明损坏只跳过该页并记录警告，不影响其余页面。
    """
    # 延迟导入插件路由支持函数，避免页面注册表与插件路由包初始化互相导入。
    from src.webui.routers.plugin.support import load_manifest_json

    loaded_ids = {str(plugin_id).strip() for plugin_id in loaded_plugin_ids if str(plugin_id).strip()}
    validator = ManifestValidator(
        validate_python_package_dependencies=False,
        log_errors=False,
        log_compat_warnings=False,
    )
    pages: List[PluginPageRecord] = []
    warnings: List[str] = []

    for plugin_path in plugin_paths:
        candidate_path = Path(plugin_path)
        manifest_data = load_manifest_json(candidate_path / "_manifest.json")
        if not manifest_data:
            continue

        manifest = validator.parse_manifest(manifest_data, source=str(candidate_path))
        if manifest is None:
            logger.warning(f"跳过 Manifest 无效的插件 WebUI 页面: {candidate_path}")
            continue
        if manifest.id not in loaded_ids or manifest.extensions is None:
            continue

        for page in manifest.extensions.webui_pages:
            try:
                pages.append(_build_page_record(candidate_path, manifest, page))
            except ValueError as exc:
                logger.warning(f"插件 {manifest.id} 页面 {page.id} 声明无效，已跳过: {exc}")
                warnings.append(f"插件 {manifest.id} 页面 {page.id} 声明无效，已跳过: {exc}")

    pages.sort(key=lambda page: (page.order, page.plugin_id, page.page_id))
    return pages, warnings
