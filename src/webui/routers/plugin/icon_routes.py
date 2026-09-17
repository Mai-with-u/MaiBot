from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import FileResponse
import mimetypes

from .support import find_plugin_path_by_id, load_manifest_json, require_plugin_token, resolve_plugin_file_path

router = APIRouter()

_LOCAL_ICON_SUFFIXES = {".jpg", ".jpeg", ".png", ".svg", ".webp"}
_MAX_ICON_BYTES = 512 * 1024


def _get_local_icon_path(manifest: Dict[str, Any]) -> Optional[str]:
    display = manifest.get("display")
    if not isinstance(display, dict):
        return None

    icon = display.get("icon")
    if not isinstance(icon, dict):
        return None

    if str(icon.get("type", "")).strip() != "local":
        return None

    icon_path = str(icon.get("value", "")).strip()
    return icon_path or None


def _validate_local_icon_path(icon_path: str) -> None:
    path = Path(icon_path)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise HTTPException(status_code=400, detail="图标路径必须是插件目录内的相对路径")
    if "\x00" in icon_path or icon_path.startswith(("/", "\\")):
        raise HTTPException(status_code=400, detail="图标路径包含非法字符")
    if path.suffix.lower() not in _LOCAL_ICON_SUFFIXES:
        raise HTTPException(status_code=400, detail="不支持的图标文件类型")


@router.get("/icon/{plugin_id}")
def get_plugin_icon(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> FileResponse:
    """读取已安装插件在 manifest 中声明的本地图标。"""
    require_plugin_token(maibot_session)

    plugin_path = find_plugin_path_by_id(plugin_id)
    if plugin_path is None:
        raise HTTPException(status_code=404, detail="插件未安装")

    manifest = load_manifest_json(resolve_plugin_file_path(plugin_path, "_manifest.json"))
    if manifest is None:
        raise HTTPException(status_code=404, detail="插件 manifest 不存在或无法读取")

    icon_path = _get_local_icon_path(manifest)
    if icon_path is None:
        raise HTTPException(status_code=404, detail="插件未声明本地图标")

    _validate_local_icon_path(icon_path)
    resolved_icon_path = resolve_plugin_file_path(plugin_path, icon_path, allow_missing=False)
    if not resolved_icon_path.is_file():
        raise HTTPException(status_code=404, detail="插件图标不存在")
    if resolved_icon_path.stat().st_size > _MAX_ICON_BYTES:
        raise HTTPException(status_code=400, detail="插件图标文件过大")

    media_type, _encoding = mimetypes.guess_type(resolved_icon_path.name)
    return FileResponse(
        resolved_icon_path,
        media_type=media_type or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=3600"},
    )
