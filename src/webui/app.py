"""FastAPI 应用工厂 - 创建和配置 WebUI 应用实例"""

from importlib import import_module
from os import getenv
from pathlib import Path
from typing import Any, Dict, Tuple

import asyncio
import gzip
import mimetypes

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from src.common.paths import SOURCE_ROOT, get_instance_root
from src.common.i18n import t
from src.common.logger import get_logger
from src.webui.dependencies import require_auth
from src.webui.middleware.compression import TextGZipMiddleware, accepts_gzip
from src.webui.version_compatibility import (
    get_webui_version_compatibility,
    read_installed_webui_version,
    read_local_webui_version,
)

logger = get_logger("webui.app")

_DASHBOARD_PACKAGE_NAME = "maibot-dashboard"
_LOCAL_DASHBOARD_ENV = "MAIBOT_WEBUI_USE_LOCAL_DASHBOARD"
_STATISTICS_REPORT_PATH_ENV = "MAIBOT_STATISTICS_REPORT_PATH"
_DEFAULT_STATISTICS_REPORT_PATH = "maibot_statistics.html"
_MANUAL_INSTALL_COMMAND = f"pip install {_DASHBOARD_PACKAGE_NAME}"

# 同步路由端点由 Starlette 交给 anyio 线程池执行，anyio 默认上限是 40；
# 底层 SQLite 连接池只有 5 条常驻 + 10 条溢出连接，并发过高会让线程排队等连接，
# 因此把同步端点的并发数限制在连接池容量以内。
MAX_CONCURRENT_SYNC_ENDPOINTS = 8

# 前端构建产物里可压缩的文本类文件；woff2、图片等本身已压缩，不再处理
_GZIP_STATIC_SUFFIXES = frozenset({".css", ".html", ".js", ".json", ".map", ".mjs", ".svg", ".ttf", ".txt"})
# 静态文件 gzip 结果缓存：路径 -> (修改时间, 文件大小, 压缩后内容)，文件变化后自动重新压缩
_gzip_static_cache: Dict[Path, Tuple[int, int, bytes]] = {}


def limit_sync_endpoint_concurrency() -> int:
    """把同步路由端点的并发数限制在 SQLite 连接池容量以内，并返回生效值。

    必须在目标事件循环内调用：anyio 的线程池上限是按事件循环保存的，在循环外设置
    不会作用到 WebUI 自己的循环上。
    """

    import anyio.to_thread

    limiter = anyio.to_thread.current_default_thread_limiter()
    if limiter.total_tokens > MAX_CONCURRENT_SYNC_ENDPOINTS:
        limiter.total_tokens = MAX_CONCURRENT_SYNC_ENDPOINTS
    return int(limiter.total_tokens)


def _resolve_safe_static_file_path(static_path: Path, full_path: str) -> Path | None:
    static_root = static_path.resolve()

    try:
        candidate_path = (static_root / full_path).resolve()
        candidate_path.relative_to(static_root)
    except (OSError, RuntimeError, ValueError):
        logger.warning(t("startup.webui_path_traversal_detected", full_path=full_path))
        return None

    return candidate_path


def _load_gzipped_static_file(file_path: Path) -> bytes:
    """读取静态文件的 gzip 内容，按修改时间和大小缓存。包含文件读写，需在线程中调用。"""

    file_stat = file_path.stat()
    cached = _gzip_static_cache.get(file_path)
    if cached is not None and cached[0] == file_stat.st_mtime_ns and cached[1] == file_stat.st_size:
        return cached[2]

    compressed = gzip.compress(file_path.read_bytes(), compresslevel=9, mtime=0)
    _gzip_static_cache[file_path] = (file_stat.st_mtime_ns, file_stat.st_size, compressed)
    return compressed


def _resolve_static_cache_control(static_path: Path, file_path: Path) -> str | None:
    """按文件位置决定缓存策略。"""

    relative_parts = file_path.resolve().relative_to(static_path.resolve()).parts
    if relative_parts[0] == "assets":
        # 构建产物文件名带内容哈希，内容变化时文件名必然变化，可以长期缓存
        return "public, max-age=31536000, immutable"
    if file_path.suffix == ".html":
        # 入口页每次都要校验，保证更新 WebUI 后立即加载到新的资源文件名
        return "no-cache"
    return None


async def _build_static_file_response(request: Request, static_path: Path, file_path: Path) -> Response:
    """构造静态文件响应：文本类文件在客户端支持时返回 gzip 内容，并附带缓存策略。"""

    media_type = mimetypes.guess_type(str(file_path))[0]
    response: Response
    if file_path.suffix in _GZIP_STATIC_SUFFIXES and accepts_gzip(request.headers.get("Accept-Encoding", "")):
        compressed = await asyncio.to_thread(_load_gzipped_static_file, file_path)
        # 猜不出类型时与 FileResponse 的默认值保持一致
        response = Response(content=compressed, media_type=media_type or "text/plain")
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Vary"] = "Accept-Encoding"
    else:
        response = FileResponse(file_path, media_type=media_type)

    cache_control = _resolve_static_cache_control(static_path, file_path)
    if cache_control is not None:
        response.headers["Cache-Control"] = cache_control
    if file_path.suffix == ".html":
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def _get_project_root() -> Path:
    return SOURCE_ROOT


def _resolve_statistics_report_path() -> Path:
    configured_path = getenv(_STATISTICS_REPORT_PATH_ENV, "").strip()
    report_path = Path(configured_path or _DEFAULT_STATISTICS_REPORT_PATH)
    if report_path.is_absolute():
        return report_path.resolve()

    return (get_instance_root() / report_path).resolve()


def _is_local_dashboard_enabled() -> bool:
    return getenv(_LOCAL_DASHBOARD_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_static_path(static_path: Path | None) -> Tuple[str, Dict[str, Any]] | None:
    if static_path is None:
        return "startup.webui_static_dir_missing", {}

    if not static_path.exists():
        return "startup.webui_static_dir_missing_with_path", {"static_path": static_path}

    index_path = static_path / "index.html"
    if not index_path.exists():
        return "startup.webui_index_missing", {"index_path": index_path}

    return None


def _ensure_static_path_ready() -> Path | None:
    static_path = _resolve_static_path()
    validation_error = _validate_static_path(static_path)
    if validation_error is None:
        return static_path

    logger.warning(t("startup.webui_static_assets_unavailable"))
    error_key, error_kwargs = validation_error
    logger.warning(t(error_key, **error_kwargs))
    logger.warning(t("startup.webui_dashboard_package_hint", command=_MANUAL_INSTALL_COMMAND))
    return None


def create_app(
    host: str = "0.0.0.0",
    port: int = 8001,
    enable_static: bool = True,
) -> FastAPI:
    """
    创建 WebUI FastAPI 应用实例

    Args:
        host: 服务器主机地址
        port: 服务器端口
        enable_static: 是否启用静态文件服务
    """
    app = FastAPI(title="MaiBot WebUI")

    _setup_anti_crawler(app)
    _setup_cors(app, port)
    app.add_middleware(TextGZipMiddleware)
    _register_api_routes(app)
    _setup_robots_txt(app)

    if enable_static:
        _setup_static_files(app)

    return app


def _setup_cors(app: FastAPI, port: int):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:7999",
            "http://127.0.0.1:7999",
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Accept",
            "Origin",
            "X-Requested-With",
        ],
        expose_headers=["Content-Length", "Content-Type"],
    )
    logger.debug(t("startup.webui_cors_configured"))


def _setup_anti_crawler(app: FastAPI):
    try:
        from src.config.config import global_config
        from src.webui.middleware import AntiCrawlerMiddleware

        anti_crawler_mode = global_config.webui.anti_crawler_mode
        app.add_middleware(AntiCrawlerMiddleware, mode=anti_crawler_mode)

        mode_descriptions = {
            "false": t("startup.webui_anti_crawler_mode_disabled"),
            "strict": t("startup.webui_anti_crawler_mode_strict"),
            "loose": t("startup.webui_anti_crawler_mode_loose"),
            "basic": t("startup.webui_anti_crawler_mode_basic"),
        }
        mode_desc = mode_descriptions.get(anti_crawler_mode, t("startup.webui_anti_crawler_mode_basic"))
        logger.debug(t("startup.webui_anti_crawler_configured", mode_desc=mode_desc))
    except Exception as e:
        logger.error(t("startup.webui_anti_crawler_config_failed", error=e), exc_info=True)


def _setup_robots_txt(app: FastAPI):
    try:
        from src.webui.middleware import create_robots_txt_response

        @app.get("/robots.txt", include_in_schema=False)
        async def robots_txt():
            return create_robots_txt_response()

        logger.debug(t("startup.webui_robots_route_registered"))
    except Exception as e:
        logger.error(t("startup.webui_robots_route_register_failed", error=e), exc_info=True)


def _register_api_routes(app: FastAPI):
    try:
        from src.webui.routers import get_all_routers

        for router in get_all_routers():
            app.include_router(router)

        logger.debug(t("startup.webui_api_routes_registered"))
    except Exception as e:
        logger.error(t("startup.webui_api_routes_register_failed", error=e), exc_info=True)


def _setup_static_files(app: FastAPI):
    mimetypes.init()
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("application/json", ".json")

    static_path = _ensure_static_path_ready()
    if static_path is None:
        return

    _log_webui_version_compatibility(static_path)

    if not static_path.exists():
        logger.warning(t("startup.webui_static_dir_missing_with_path", static_path=static_path))
        logger.warning(t("startup.webui_dashboard_package_hint", command=_MANUAL_INSTALL_COMMAND))
        return

    if not (static_path / "index.html").exists():
        logger.warning(t("startup.webui_index_missing", index_path=static_path / "index.html"))
        logger.warning(t("startup.webui_dashboard_package_hint", command=_MANUAL_INSTALL_COMMAND))
        return

    @app.get("/maibot_statistics.html", include_in_schema=False, dependencies=[Depends(require_auth)])
    async def serve_statistics_report():
        report_path = _resolve_statistics_report_path()
        if not report_path.exists() or not report_path.is_file():
            raise HTTPException(status_code=404, detail=t("core.not_found"))

        response = FileResponse(report_path, media_type="text/html")
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(request: Request, full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail=t("core.not_found"))

        if not full_path or full_path == "/":
            return await _build_static_file_response(request, static_path, static_path / "index.html")

        file_path = _resolve_safe_static_file_path(static_path, full_path)
        if file_path is None:
            raise HTTPException(status_code=404, detail=t("core.not_found"))

        if file_path.exists() and file_path.is_file():
            return await _build_static_file_response(request, static_path, file_path)

        return await _build_static_file_response(request, static_path, static_path / "index.html")

    logger.debug(t("startup.webui_static_files_configured", static_path=static_path))


def _log_webui_version_compatibility(static_path: Path) -> None:
    """在控制台提示当前加载的 WebUI 与主程序版本是否匹配。"""

    local_static_path = (_get_project_root() / "dashboard" / "dist").resolve()
    if static_path.resolve() == local_static_path:
        webui_version = read_local_webui_version(_get_project_root())
    else:
        webui_version = read_installed_webui_version()

    compatibility = get_webui_version_compatibility(webui_version, _get_project_root())
    if compatibility.status == "webui_outdated":
        logger.warning(
            t(
                "startup.webui_version_outdated",
                current_version=compatibility.webui_version,
                required_version=compatibility.required_webui_version,
            )
        )
    elif compatibility.status == "main_program_outdated":
        logger.warning(
            t(
                "startup.main_program_version_outdated_for_webui",
                main_version=compatibility.main_program_version,
                current_version=compatibility.webui_version,
                required_version=compatibility.required_webui_version,
            )
        )


def _resolve_static_path() -> Path | None:
    if _is_local_dashboard_enabled():
        static_path = _get_project_root() / "dashboard" / "dist"
        if static_path.is_dir() and (static_path / "index.html").exists():
            return static_path

    try:
        module = import_module("maibot_dashboard")
        get_dist_path = getattr(module, "get_dist_path", None)
        if callable(get_dist_path):
            package_path = get_dist_path()
            if isinstance(package_path, Path) and package_path.exists():
                return package_path
    except Exception:
        pass

    return None


def show_access_token():
    """显示 WebUI Access Token（供启动时调用）"""
    try:
        from src.webui.core import get_token_manager

        token_manager = get_token_manager()
        current_token = token_manager.get_token()
        if token_manager.should_show_startup_token():
            logger.info(t("startup.webui_access_token", token=current_token))
            logger.info(t("startup.webui_access_token_login_hint"))
    except Exception as e:
        logger.error(t("startup.webui_access_token_failed", error=e))
