"""插件市场条目规范化、筛选与统计排序。"""

from typing import Any, Dict, List, Optional
import re

MIN_PLUGIN_MANIFEST_VERSION = 2
PLUGIN_STATS_SORTS = (
    "downloads", "likes", "rating", "downloads_growth_7d", "likes_growth_7d",
    "downloads_growth_30d", "likes_growth_30d",
)


def _safe_string(value: Any) -> str:
    """把外部接口返回值转换成稳定字符串。"""

    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: Any, default: int = 0) -> int:
    """把外部接口返回值转换成整数。"""

    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> List[str]:
    """把插件元信息中的列表字段规范化为字符串列表。"""

    if not isinstance(value, list):
        return []
    return [_safe_string(item) for item in value if _safe_string(item)]


def _plugin_manifest_repository_url(manifest: Dict[str, Any]) -> str:
    """读取插件仓库地址，兼容 dashboard 处理的两种字段。"""

    urls = manifest.get("urls")
    repository_from_urls = urls.get("repository") if isinstance(urls, dict) else ""
    return _safe_string(manifest.get("repository_url") or repository_from_urls)


def _plugin_manifest_homepage_url(manifest: Dict[str, Any]) -> str:
    """读取插件主页地址，兼容 dashboard 处理的两种字段。"""

    urls = manifest.get("urls")
    homepage_from_urls = urls.get("homepage") if isinstance(urls, dict) else ""
    return _safe_string(manifest.get("homepage_url") or homepage_from_urls)


def _plugin_manifest_url(manifest: Dict[str, Any], key: str) -> str:
    """读取插件 urls 中的指定地址。"""

    urls = manifest.get("urls")
    return _safe_string(urls.get(key)) if isinstance(urls, dict) else ""


def _normalize_plugin_catalog_item(item: Dict[str, Any], include_manifest: bool = False) -> Optional[Dict[str, Any]]:
    """按 dashboard 的展示口径整理插件市场条目。"""

    manifest = item.get("manifest")
    if not isinstance(manifest, dict):
        return None

    manifest_version = _safe_int(manifest.get("manifest_version"), 0)
    if manifest_version < MIN_PLUGIN_MANIFEST_VERSION:
        return None

    manifest_id = _safe_string(manifest.get("id"))
    marketplace_id = _safe_string(item.get("id"))
    plugin_id = manifest_id or marketplace_id
    name = _safe_string(manifest.get("name"))
    version = _safe_string(manifest.get("version"))
    if not plugin_id or not name or not version:
        return None

    author = manifest.get("author")
    author_info = author if isinstance(author, dict) else {}
    normalized = {
        "id": plugin_id,
        "marketplace_id": marketplace_id,
        "manifest_version": manifest_version,
        "name": name,
        "version": version,
        "description": _safe_string(manifest.get("description")),
        "author": {
            "name": _safe_string(author_info.get("name")) or "Unknown",
            "url": _safe_string(author_info.get("url")),
        },
        "license": _safe_string(manifest.get("license")) or "Unknown",
        "host_application": manifest.get("host_application") if isinstance(manifest.get("host_application"), dict) else {},
        "sdk": manifest.get("sdk") if isinstance(manifest.get("sdk"), dict) else {},
        "repository_url": _plugin_manifest_repository_url(manifest),
        "homepage_url": _plugin_manifest_homepage_url(manifest),
        "documentation_url": _plugin_manifest_url(manifest, "documentation"),
        "issues_url": _plugin_manifest_url(manifest, "issues"),
        "keywords": _string_list(manifest.get("keywords")),
        "categories": _string_list(manifest.get("categories")),
        "capabilities": _string_list(manifest.get("capabilities")),
        "dependencies": manifest.get("dependencies") if isinstance(manifest.get("dependencies"), list) else [],
        "default_locale": _safe_string(manifest.get("i18n", {}).get("default_locale")) or "zh-CN",
        "locales_path": _safe_string(manifest.get("i18n", {}).get("locales_path")),
        "source": "market",
    }
    if include_manifest:
        normalized["manifest"] = manifest
    return normalized


def _plugin_search_text(plugin: Dict[str, Any]) -> str:
    """构建插件搜索文本。"""

    parts = [
        _safe_string(plugin.get("id")),
        _safe_string(plugin.get("marketplace_id")),
        _safe_string(plugin.get("name")),
        _safe_string(plugin.get("description")),
        _safe_string(plugin.get("repository_url")),
        _safe_string(plugin.get("homepage_url")),
    ]
    author = plugin.get("author")
    if isinstance(author, dict):
        parts.append(_safe_string(author.get("name")))
    parts.extend(_string_list(plugin.get("keywords")))
    parts.extend(_string_list(plugin.get("categories")))
    return " ".join(part for part in parts if part).casefold()


def _filter_plugins(
    plugins: List[Dict[str, Any]],
    query: str,
    category: str,
) -> List[Dict[str, Any]]:
    """按关键词和分类筛选插件列表。"""

    query_terms = [term.casefold() for term in re.split(r"\s+", query.strip()) if term.strip()]
    normalized_category = category.strip().casefold()
    filtered: List[Dict[str, Any]] = []
    for plugin in plugins:
        categories = [item.casefold() for item in _string_list(plugin.get("categories"))]
        if normalized_category and not any(normalized_category in item for item in categories):
            continue

        search_text = _plugin_search_text(plugin)
        if query_terms and not all(term in search_text for term in query_terms):
            continue
        filtered.append(plugin)
    return filtered


def _sort_stat_plugins(plugins: List[Dict[str, Any]], sort_by: str) -> List[Dict[str, Any]]:
    """按总量或服务端全市场飙升位次排序；飙升查询只返回上榜插件。"""

    if sort_by not in PLUGIN_STATS_SORTS:
        raise ValueError(f"sort_by 必须是以下值之一：{', '.join(PLUGIN_STATS_SORTS)}")
    growth = "_growth_" in sort_by
    field = sort_by.replace("_growth_", "_growth_rank_") if growth else sort_by
    if growth and plugins and not any(field in plugin.get("stats", {}) for plugin in plugins):
        raise ValueError(f"统计接口未提供 {field}，无法查询该飙升榜")
    # 缺失指标不能参与排序；null 飙升位次表示未上榜，不自行计算替代排名。
    candidates = [
        plugin for plugin in plugins
        if isinstance(plugin.get("stats"), dict)
        and isinstance(plugin["stats"].get(field), (int, float))
        and not isinstance(plugin["stats"][field], bool)
        and (not growth or plugin["stats"][field] > 0)
    ]
    return sorted(candidates, key=lambda plugin: (
        plugin["stats"][field] if growth else -plugin["stats"][field], plugin["id"],
    ))


