"""WebUI 路由聚合模块 - 提供统一的路由注册接口"""

from typing import List

from fastapi import APIRouter


def get_api_router() -> APIRouter:
    """获取主 API 路由器（包含所有子路由）"""
    from src.webui.routes import router as main_router

    return main_router


def get_all_routers() -> List[APIRouter]:
    """获取所有需要独立注册的路由器列表"""
    from src.webui.api.planner import router as planner_router
    from src.webui.api.replier import router as replier_router
    from src.webui.routers.chat import router as chat_router
    from src.webui.routers.memory import compat_router as memory_compat_router
    from src.webui.routers.websocket.logs import router as logs_router
    from src.webui.routes import router as main_router

    return [
        main_router,
        memory_compat_router,
        logs_router,
        chat_router,
        planner_router,
        replier_router,
    ]


__all__ = [
    "get_api_router",
    "get_all_routers",
]
