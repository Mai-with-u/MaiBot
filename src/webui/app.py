"""FastAPI 应用工厂 - 创建 Bot 的 WebUI 接口子应用。

前端页面、登录与公开流量防护由宿主提供；这里只有经宿主转发的业务接口。
"""

from fastapi import FastAPI

from src.common.i18n import t
from src.common.logger import get_logger
from src.webui.middleware.compression import TextGZipMiddleware

logger = get_logger("webui.app")


def create_app() -> FastAPI:
    """创建 WebUI 接口子应用。"""
    app = FastAPI(title="MaiBot WebUI")
    app.add_middleware(TextGZipMiddleware)
    _register_api_routes(app)

    return app


def _register_api_routes(app: FastAPI):
    from src.webui.routers import get_all_routers

    for router in get_all_routers():
        app.include_router(router)

    logger.debug(t("startup.webui_api_routes_registered"))
