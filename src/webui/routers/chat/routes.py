"""兼容旧导入路径：`from src.webui.routers.chat.routes import router`。"""

from src.webui.routers.chat import router

__all__ = ["router"]
