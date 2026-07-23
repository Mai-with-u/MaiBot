"""FastAPI 应用工厂 - 创建和配置 WebUI 应用实例"""

from importlib import import_module
from os import getenv
from pathlib import Path
from typing import Any, Dict, Tuple

import mimetypes

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.common.i18n import t
from src.common.logger import get_logger
from src.webui.dependencies import require_auth
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


def _resolve_safe_static_file_path(static_path: Path, full_path: str) -> Path | None:
    static_root = static_path.resolve()

    try:
        candidate_path = (static_root / full_path).resolve()
        candidate_path.relative_to(static_root)
    except (OSError, RuntimeError, ValueError):
        logger.warning(t("startup.webui_path_traversal_detected", full_path=full_path))
        return None

    return candidate_path


def _get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_statistics_report_path() -> Path:
    configured_path = getenv(_STATISTICS_REPORT_PATH_ENV, "").strip()
    report_path = Path(configured_path or _DEFAULT_STATISTICS_REPORT_PATH)
    if report_path.is_absolute():
        return report_path.resolve()

    return (_get_project_root() / report_path).resolve()


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
    async def serve_spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail=t("core.not_found"))

        if not full_path or full_path == "/":
            response = FileResponse(static_path / "index.html", media_type="text/html")
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

        file_path = _resolve_safe_static_file_path(static_path, full_path)
        if file_path is None:
            raise HTTPException(status_code=404, detail=t("core.not_found"))

        if file_path.exists() and file_path.is_file():
            media_type = mimetypes.guess_type(str(file_path))[0]
            response = FileResponse(file_path, media_type=media_type)
            if str(file_path).endswith(".html"):
                response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

        response = FileResponse(static_path / "index.html", media_type="text/html")
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

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
