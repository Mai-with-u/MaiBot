"""FastAPI 应用工厂 - 创建 Bot 的 WebUI 接口子应用。

前端页面、登录与公开流量防护由宿主提供；这里只有经宿主转发的业务接口。
"""

from os import getenv
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from src.common.paths import get_instance_root
from src.common.i18n import t
from src.common.logger import get_logger
from src.webui.middleware.compression import TextGZipMiddleware

logger = get_logger("webui.app")

_STATISTICS_REPORT_PATH_ENV = "MAIBOT_STATISTICS_REPORT_PATH"
_DEFAULT_STATISTICS_REPORT_PATH = "maibot_statistics.html"


def _resolve_statistics_report_path() -> Path:
    configured_path = getenv(_STATISTICS_REPORT_PATH_ENV, "").strip()
    report_path = Path(configured_path or _DEFAULT_STATISTICS_REPORT_PATH)
    if report_path.is_absolute():
        return report_path.resolve()

    return (get_instance_root() / report_path).resolve()


def create_app() -> FastAPI:
    """创建 WebUI 接口子应用。"""
    app = FastAPI(title="MaiBot WebUI")
    app.add_middleware(TextGZipMiddleware)
    _register_api_routes(app)

    @app.get("/maibot_statistics.html", include_in_schema=False)
    def serve_statistics_report():
        report_path = _resolve_statistics_report_path()
        if not report_path.exists() or not report_path.is_file():
            raise HTTPException(status_code=404, detail=t("core.not_found"))

        response = FileResponse(report_path, media_type="text/html")
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    return app


def _register_api_routes(app: FastAPI):
    from src.webui.routers import get_all_routers

    for router in get_all_routers():
        app.include_router(router)

    logger.debug(t("startup.webui_api_routes_registered"))
