"""FastAPI 应用工厂 - 创建和配置 WebUI 应用实例"""

import mimetypes
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from src.common.logger import get_logger

logger = get_logger("webui.app")


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
            "Authorization",
            "Accept",
            "Origin",
            "X-Requested-With",
        ],
        expose_headers=["Content-Length", "Content-Type"],
    )
    logger.debug("✅ CORS 中间件已配置")


def _setup_anti_crawler(app: FastAPI):
    try:
        from src.webui.middleware import AntiCrawlerMiddleware
        from src.config.config import global_config

        anti_crawler_mode = global_config.webui.anti_crawler_mode
        app.add_middleware(AntiCrawlerMiddleware, mode=anti_crawler_mode)

        mode_descriptions = {
            "false": "已禁用",
            "strict": "严格模式",
            "loose": "宽松模式",
            "basic": "基础模式",
        }
        mode_desc = mode_descriptions.get(anti_crawler_mode, "基础模式")
        logger.info(f"🛡️ 防爬虫中间件已配置: {mode_desc}")
    except Exception as e:
        logger.error(f"❌ 配置防爬虫中间件失败: {e}", exc_info=True)


def _setup_robots_txt(app: FastAPI):
    try:
        from src.webui.middleware import create_robots_txt_response

        @app.get("/robots.txt", include_in_schema=False)
        async def robots_txt():
            return create_robots_txt_response()

        logger.debug("✅ robots.txt 路由已注册")
    except Exception as e:
        logger.error(f"❌ 注册robots.txt路由失败: {e}", exc_info=True)


def _register_api_routes(app: FastAPI):
    try:
        from src.webui.routers import get_all_routers

        for router in get_all_routers():
            app.include_router(router)

        logger.info("✅ WebUI API 路由已注册")
    except Exception as e:
        logger.error(f"❌ 注册 WebUI API 路由失败: {e}", exc_info=True)


def _setup_static_files(app: FastAPI):
    mimetypes.init()
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("application/json", ".json")

    base_dir = Path(__file__).parent.parent.parent
    static_path = base_dir / "webui" / "dist"

    if not static_path.exists():
        logger.warning(f"❌ WebUI 静态文件目录不存在: {static_path}")
        logger.warning("💡 请先构建前端: cd webui && npm run build")
        return

    if not (static_path / "index.html").exists():
        logger.warning(f"❌ 未找到 index.html: {static_path / 'index.html'}")
        logger.warning("💡 请确认前端已正确构建")
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if not full_path or full_path == "/":
            response = FileResponse(static_path / "index.html", media_type="text/html")
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

        file_path = static_path / full_path
        if file_path.is_file() and file_path.exists():
            media_type = mimetypes.guess_type(str(file_path))[0]
            response = FileResponse(file_path, media_type=media_type)
            if str(file_path).endswith(".html"):
                response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

        response = FileResponse(static_path / "index.html", media_type="text/html")
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    logger.info(f"✅ WebUI 静态文件服务已配置: {static_path}")


def show_access_token():
    """显示 WebUI Access Token（供启动时调用）"""
    try:
        from src.webui.core import get_token_manager

        token_manager = get_token_manager()
        current_token = token_manager.get_token()
        logger.info(f"🔑 WebUI Access Token: {current_token}")
        logger.info("💡 请使用此 Token 登录 WebUI")
    except Exception as e:
        logger.error(f"❌ 获取 Access Token 失败: {e}")
