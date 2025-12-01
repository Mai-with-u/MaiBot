"""独立的 WebUI 服务器 - 运行在 0.0.0.0:8001"""

import os
import asyncio
import mimetypes
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from uvicorn import Config, Server as UvicornServer
from src.common.logger import get_logger

logger = get_logger("webui_server")


class WebUIServer:
    """独立的 WebUI 服务器"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8001):
        self.host = host
        self.port = port
        self.app = FastAPI(title="MaiBot WebUI")
        self._server = None

        # 配置 CORS（支持开发环境跨域请求）
        self._setup_cors()

        # 显示 Access Token
        self._show_access_token()

        # 重要：先注册 API 路由，再设置静态文件
        self._register_api_routes()
        self._setup_static_files()

    def _setup_cors(self):
        """配置 CORS 中间件"""
        # 开发环境需要允许前端开发服务器的跨域请求
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:5173",  # Vite 开发服务器
                "http://127.0.0.1:5173",
                "http://localhost:8001",  # 生产环境
                "http://127.0.0.1:8001",
            ],
            allow_credentials=True,  # 允许携带 Cookie
            allow_methods=["*"],
            allow_headers=["*"],
        )
        logger.debug("✅ CORS 中间件已配置")

    def _show_access_token(self):
        """显示 WebUI Access Token"""
        try:
            from src.webui.token_manager import get_token_manager

            token_manager = get_token_manager()
            current_token = token_manager.get_token()
            logger.info(f"🔑 WebUI Access Token: {current_token}")
            logger.info("💡 请使用此 Token 登录 WebUI")
        except Exception as e:
            logger.error(f"❌ 获取 Access Token 失败: {e}")

    def _setup_static_files(self):
        """设置静态文件服务"""
        # 确保正确的 MIME 类型映射
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

        # 处理 SPA 路由 - 注意：这个路由优先级最低
        @self.app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            """服务单页应用 - 只处理非 API 请求"""
            # 如果是根路径，直接返回 index.html
            if not full_path or full_path == "/":
                return FileResponse(static_path / "index.html", media_type="text/html")

            # 检查是否是静态文件
            file_path = static_path / full_path
            if file_path.is_file() and file_path.exists():
                # 自动检测 MIME 类型
                media_type = mimetypes.guess_type(str(file_path))[0]
                return FileResponse(file_path, media_type=media_type)

            # 其他路径返回 index.html（SPA 路由）
            return FileResponse(static_path / "index.html", media_type="text/html")

        logger.info(f"✅ WebUI 静态文件服务已配置: {static_path}")

    def _register_api_routes(self):
        """注册所有 WebUI API 路由"""
        try:
            # 导入所有 WebUI 路由
            from src.webui.routes import router as webui_router
            from src.webui.logs_ws import router as logs_router

            logger.info("开始导入 knowledge_routes...")
            from src.webui.knowledge_routes import router as knowledge_router

            logger.info("knowledge_routes 导入成功")

            # 导入本地聊天室路由
            from src.webui.chat_routes import router as chat_router

            logger.info("chat_routes 导入成功")

            # 注册路由
            self.app.include_router(webui_router)
            self.app.include_router(logs_router)
            self.app.include_router(knowledge_router)
            self.app.include_router(chat_router)
            logger.info(f"knowledge_router 路由前缀: {knowledge_router.prefix}")

            logger.info("✅ WebUI API 路由已注册")
        except Exception as e:
            logger.error(f"❌ 注册 WebUI API 路由失败: {e}", exc_info=True)

    async def start(self):
        """启动服务器"""
        config = Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_config=None,
            access_log=False,
        )
        self._server = UvicornServer(config=config)

        logger.info("🌐 WebUI 服务器启动中...")
        logger.info(f"🌐 访问地址: http://{self.host}:{self.port}")
        if self.host == "0.0.0.0":
            logger.info(f"本机访问请使用 http://localhost:{self.port}")

        try:
            await self._server.serve()
        except Exception as e:
            logger.error(f"❌ WebUI 服务器运行错误: {e}")
            raise

    async def shutdown(self):
        """关闭服务器"""
        if self._server:
            logger.info("正在关闭 WebUI 服务器...")
            self._server.should_exit = True
            try:
                await asyncio.wait_for(self._server.shutdown(), timeout=3.0)
                logger.info("✅ WebUI 服务器已关闭")
            except asyncio.TimeoutError:
                logger.warning("⚠️ WebUI 服务器关闭超时")
            except Exception as e:
                logger.error(f"❌ WebUI 服务器关闭失败: {e}")
            finally:
                self._server = None


# 全局 WebUI 服务器实例
_webui_server = None


def get_webui_server() -> WebUIServer:
    """获取全局 WebUI 服务器实例"""
    global _webui_server
    if _webui_server is None:
        # 从环境变量读取配置
        host = os.getenv("WEBUI_HOST", "0.0.0.0")
        port = int(os.getenv("WEBUI_PORT", "8001"))
        _webui_server = WebUIServer(host=host, port=port)
    return _webui_server
