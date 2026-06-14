"""插件进度实时推送支持。"""

from typing import Any, Dict, Optional, Set
import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.common.logger import get_logger
from src.webui.core import get_token_manager
from src.webui.routers.websocket.auth import verify_ws_token
from src.webui.routers.websocket.manager import websocket_manager

logger = get_logger("webui.plugin_progress")

router = APIRouter()

active_connections: Set[WebSocket] = set()
current_progress: Dict[str, Any] = {
    "operation": "idle",
    "stage": "idle",
    "progress": 0,
    "message": "",
    "error": None,
    "plugin_id": None,
    "total_plugins": 0,
    "loaded_plugins": 0,
}


def get_current_progress() -> Dict[str, Any]:
    """获取当前插件进度快照。

    Returns:
        Dict[str, Any]: 当前插件进度数据副本。
    """
    return current_progress.copy()


async def broadcast_progress(progress_data: Dict[str, Any]) -> None:
    """向统一连接层广播插件进度更新。

    Args:
        progress_data: 插件进度数据。
    """
    global current_progress
    current_progress = progress_data.copy()
    await websocket_manager.broadcast_to_topic(
        domain="plugin_progress",
        topic="main",
        event="update",
        data={"progress": progress_data},
    )


async def update_progress(
    stage: str,
    progress: int,
    message: str,
    operation: str = "fetch",
    error: Optional[str] = None,
    plugin_id: Optional[str] = None,
    total_plugins: int = 0,
    loaded_plugins: int = 0,
    mirror_id: Optional[str] = None,
    mirror_name: Optional[str] = None,
    mirror_index: Optional[int] = None,
    total_mirrors: Optional[int] = None,
    attempt: Optional[int] = None,
    max_attempts: Optional[int] = None,
) -> None:
    """更新当前插件进度并广播。

    Args:
        stage: 当前阶段。
        progress: 当前进度百分比。
        message: 进度说明消息。
        operation: 当前操作类型。
        error: 可选的错误信息。
        plugin_id: 当前处理的插件 ID。
        total_plugins: 总插件数量。
        loaded_plugins: 已处理插件数量。
        mirror_id: 当前尝试的镜像源 ID。
        mirror_name: 当前尝试的镜像源名称。
        mirror_index: 当前镜像源序号（从 1 开始）。
        total_mirrors: 本次操作将尝试的镜像源总数。
        attempt: 当前镜像源下的尝试次数（从 1 开始）。
        max_attempts: 当前镜像源最大尝试次数。
    """
    progress_data = {
        "operation": operation,
        "stage": stage,
        "progress": progress,
        "message": message,
        "error": error,
        "plugin_id": plugin_id,
        "total_plugins": total_plugins,
        "loaded_plugins": loaded_plugins,
        "timestamp": asyncio.get_event_loop().time(),
    }
    optional_fields = {
        "mirror_id": mirror_id,
        "mirror_name": mirror_name,
        "mirror_index": mirror_index,
        "total_mirrors": total_mirrors,
        "attempt": attempt,
        "max_attempts": max_attempts,
    }
    progress_data.update({key: value for key, value in optional_fields.items() if value is not None})

    await broadcast_progress(progress_data)
    logger.debug(f"进度更新: [{operation}] {stage} - {progress}% - {message}")


@router.websocket("/ws/plugin-progress")
async def websocket_plugin_progress(websocket: WebSocket, token: Optional[str] = Query(None)) -> None:
    """旧版插件进度 WebSocket 入口。

    Args:
        websocket: FastAPI WebSocket 对象。
        token: 可选的一次性握手 Token。
    """
    is_authenticated = False

    if token and verify_ws_token(token):
        is_authenticated = True
        logger.debug("插件进度 WebSocket 使用临时 token 认证成功")

    if not is_authenticated:
        cookie_token = websocket.cookies.get("maibot_session")
        if cookie_token:
            token_manager = get_token_manager()
            if token_manager.verify_token(cookie_token):
                is_authenticated = True
                logger.debug("插件进度 WebSocket 使用 Cookie 认证成功")

    if not is_authenticated:
        logger.warning("插件进度 WebSocket 连接被拒绝：认证失败")
        await websocket.close(code=4001, reason="认证失败，请重新登录")
        return

    await websocket.accept()
    active_connections.add(websocket)
    logger.info(f"📡 插件进度 WebSocket 客户端已连接（已认证），当前连接数: {len(active_connections)}")

    try:
        await websocket.send_text(json.dumps(current_progress, ensure_ascii=False))

        while True:
            try:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
            except Exception as exc:
                logger.error(f"处理客户端消息时出错: {exc}")
                break

    except WebSocketDisconnect:
        active_connections.discard(websocket)
        logger.info(f"📡 插件进度 WebSocket 客户端已断开，当前连接数: {len(active_connections)}")
    except Exception as exc:
        logger.error(f"❌ WebSocket 错误: {exc}")
        active_connections.discard(websocket)


def get_progress_router() -> APIRouter:
    """获取旧版插件进度路由对象。

    Returns:
        APIRouter: 插件进度路由对象。
    """
    return router
