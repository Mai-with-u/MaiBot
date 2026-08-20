"""插件 WebUI 页面清单和静态资源路由。"""

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.webui.dependencies import require_auth
from src.webui.routers.plugin.support import validate_plugin_id
from src.webui.services.plugin_page_registry import PluginPageRecord

router = APIRouter(tags=["插件页面"])

_ALLOWED_ASSET_MEDIA_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".mjs": "application/javascript",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def discover_runtime_plugin_pages() -> List[PluginPageRecord]:
    """返回当前运行时插件页面。

    Phase 1 先保留 Host 侧发现接口；运行时目录和加载状态接线在 Task 4 完成。
    """
    return []


def _get_page_records() -> List[PluginPageRecord]:
    """获取当前页面记录，独立函数便于测试和后续缓存接入。"""
    return discover_runtime_plugin_pages()


def _find_plugin_page(plugin_id: str) -> PluginPageRecord:
    """从当前页面记录中查找插件，用于解析其资源根目录。"""
    normalized_plugin_id = validate_plugin_id(plugin_id)
    for page in _get_page_records():
        if page.plugin_id == normalized_plugin_id:
            return page
    raise HTTPException(status_code=404, detail="插件页面不存在")


def _resolve_asset_path(plugin_path: Path, asset_path: str) -> Path:
    """解析插件资源路径并限制在插件的 WebUI 构建目录内。"""
    if (
        not asset_path
        or "\x00" in asset_path
        or asset_path.startswith(("/", "\\"))
        or any(part == ".." for part in Path(asset_path).parts)
    ):
        raise HTTPException(status_code=400, detail="插件资源路径包含非法字符")

    plugin_root = plugin_path.resolve()
    asset_root = (plugin_root / "webui" / "dist").resolve()
    candidate_path = plugin_root / asset_path
    if candidate_path.exists() and candidate_path.is_symlink():
        raise HTTPException(status_code=400, detail="插件资源不能是符号链接")

    try:
        resolved_candidate_path = candidate_path.resolve()
        resolved_candidate_path.relative_to(asset_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="插件资源路径超出允许范围") from exc

    if not resolved_candidate_path.exists() or not resolved_candidate_path.is_file():
        raise HTTPException(status_code=404, detail="插件资源不存在")
    if resolved_candidate_path.suffix.lower() not in _ALLOWED_ASSET_MEDIA_TYPES:
        raise HTTPException(status_code=403, detail="插件资源类型不受支持")
    return resolved_candidate_path


@router.get("/pages", dependencies=[Depends(require_auth)])
async def list_plugin_pages() -> Dict[str, Any]:
    """返回当前已加载插件声明的 WebUI 页面。"""
    try:
        pages = _get_page_records()
    except ValueError as exc:
        return {"success": True, "pages": [], "warnings": [str(exc)]}

    return {
        "success": True,
        "pages": [page.to_response_dict() for page in pages],
        "warnings": [],
    }


@router.get("/{plugin_id}/assets/{asset_path:path}", dependencies=[Depends(require_auth)])
async def serve_plugin_asset(plugin_id: str, asset_path: str) -> FileResponse:
    """返回页面声明插件的 WebUI 构建资源。"""
    page = _find_plugin_page(plugin_id)
    resolved_asset_path = _resolve_asset_path(page.plugin_path, asset_path)
    media_type = _ALLOWED_ASSET_MEDIA_TYPES[resolved_asset_path.suffix.lower()]
    response = FileResponse(resolved_asset_path, media_type=media_type)
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return response
