"""WebUI 前端静态资源：所有 Bot 共用宿主提供的同一份页面。"""

from importlib import import_module
from pathlib import Path
from typing import Dict, Optional, Tuple

import asyncio
import gzip
import mimetypes
import os
import sys

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response

from src.common.paths import SOURCE_ROOT
from src.webui.middleware.compression import accepts_gzip
from src.webui.version_compatibility import (
    get_webui_version_compatibility,
    read_installed_webui_version,
    read_local_webui_version,
)


_LOCAL_DASHBOARD_ENV = "MAIBOT_WEBUI_USE_LOCAL_DASHBOARD"
_MANUAL_INSTALL_COMMAND = "pip install maibot-dashboard"
# 前端构建产物里可压缩的文本类文件；woff2、图片等本身已压缩，不再处理
_GZIP_STATIC_SUFFIXES = frozenset({".css", ".html", ".js", ".json", ".map", ".mjs", ".svg", ".ttf", ".txt"})
# Windows 注册表可能把 .js 登记为 text/plain；模块脚本必须提供标准 MIME，不能依赖系统猜测。
_MEDIA_TYPES = {".css": "text/css", ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json"}
# 静态文件 gzip 结果缓存：路径 -> (修改时间, 文件大小, 压缩后内容)，文件变化后自动重新压缩
_gzip_cache: Dict[Path, Tuple[int, int, bytes]] = {}


def _local_dashboard_enabled() -> bool:
    return os.environ.get(_LOCAL_DASHBOARD_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_static_path() -> Optional[Path]:
    """开发时可用本地构建产物；否则使用安装的 maibot-dashboard 资源包。"""
    if _local_dashboard_enabled():
        local_path = SOURCE_ROOT / "dashboard" / "dist"
        if (local_path / "index.html").is_file():
            return local_path
    try:
        module = import_module("maibot_dashboard")
    except ModuleNotFoundError as exc:
        if exc.name != "maibot_dashboard":
            raise
        return None
    package_path = Path(module.get_dist_path())
    return package_path if (package_path / "index.html").is_file() else None


def report_static_status(static_path: Optional[Path]) -> None:
    """启动时提示资源缺失或前后端版本不匹配。"""
    if static_path is None:
        print(f"WebUI 前端资源不可用，请执行 {_MANUAL_INSTALL_COMMAND} 或构建 dashboard", file=sys.stderr, flush=True)
        return
    local_path = (SOURCE_ROOT / "dashboard" / "dist").resolve()
    webui_version = read_local_webui_version(SOURCE_ROOT) if static_path.resolve() == local_path else read_installed_webui_version()
    compatibility = get_webui_version_compatibility(webui_version, SOURCE_ROOT)
    if compatibility.status == "webui_outdated":
        print(
            f"WebUI 版本过旧：当前 {compatibility.webui_version}，需要 {compatibility.required_webui_version}",
            file=sys.stderr, flush=True,
        )
    elif compatibility.status == "main_program_outdated":
        print(
            f"主程序版本 {compatibility.main_program_version} 低于 WebUI {compatibility.webui_version} "
            f"所需的 {compatibility.required_webui_version}",
            file=sys.stderr, flush=True,
        )


def _load_gzipped(file_path: Path) -> bytes:
    """读取静态文件的 gzip 内容，按修改时间和大小缓存。包含文件读写，需在线程中调用。"""
    file_stat = file_path.stat()
    cached = _gzip_cache.get(file_path)
    if cached is not None and cached[0] == file_stat.st_mtime_ns and cached[1] == file_stat.st_size:
        return cached[2]
    compressed = gzip.compress(file_path.read_bytes(), compresslevel=9, mtime=0)
    _gzip_cache[file_path] = (file_stat.st_mtime_ns, file_stat.st_size, compressed)
    return compressed


def _cache_control(static_path: Path, file_path: Path) -> Optional[str]:
    if file_path.relative_to(static_path).parts[0] == "assets":
        # 构建产物文件名带内容哈希，内容变化时文件名必然变化，可以长期缓存
        return "public, max-age=31536000, immutable"
    if file_path.suffix == ".html":
        # 入口页每次都要校验，保证更新 WebUI 后立即加载到新的资源文件名
        return "no-cache"
    return None


async def static_response(request: Request, static_path: Optional[Path], path: str) -> Response:
    """返回 SPA 资源；未知的无后缀路径交给前端路由。"""
    if static_path is None:
        raise HTTPException(503, "当前 WebUI 资源不可用，请安装新版资源或构建 dashboard")
    root = static_path.resolve()
    file_path = (root / path).resolve()
    if not file_path.is_relative_to(root):
        raise HTTPException(404, "未找到页面资源")
    if not file_path.is_file():
        if Path(path).suffix:
            raise HTTPException(404, "未找到页面资源")
        file_path = root / "index.html"
    media_type = _MEDIA_TYPES.get(file_path.suffix.lower()) or mimetypes.guess_type(str(file_path))[0]
    response: Response
    if file_path.suffix in _GZIP_STATIC_SUFFIXES and accepts_gzip(request.headers.get("Accept-Encoding", "")):
        compressed = await asyncio.to_thread(_load_gzipped, file_path)
        # 猜不出类型时与 FileResponse 的默认值保持一致
        response = Response(content=compressed, media_type=media_type or "text/plain")
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Vary"] = "Accept-Encoding"
    else:
        response = FileResponse(file_path, media_type=media_type)
    cache_control = _cache_control(root, file_path)
    if cache_control is not None:
        response.headers["Cache-Control"] = cache_control
    if file_path.suffix == ".html":
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response
