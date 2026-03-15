import asyncio
import json

from typing import Any, Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.common.logger import get_logger
from src.webui.core import get_token_manager
from src.webui.routers.websocket.auth import verify_ws_token

logger = get_logger("webui.plugin_progress")

router = APIRouter()

active_connections: Set[WebSocket] = set()
current_progress: dict[str, Any] = {
    "operation": "idle",
    "stage": "idle",
    "progress": 0,
    "message": "",
    "error": None,
    "plugin_id": None,
    "total_plugins": 0,
    "loaded_plugins": 0,
}


async def broadcast_progress(progress_data: dict[str, Any]) -> None:
    global current_progress
    current_progress = progress_data.copy()

    if not active_connections:
        return

    message = json.dumps(progress_data, ensure_ascii=False)
    disconnected: set[WebSocket] = set()

    for websocket in active_connections:
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error(f"发送进度更新失败: {e}")
            disconnected.add(websocket)

    for websocket in disconnected:
        active_connections.discard(websocket)


async def update_progress(
    stage: str,
    progress: int,
    message: str,
    operation: str = "fetch",
    error: Optional[str] = None,
    plugin_id: Optional[str] = None,
    total_plugins: int = 0,
    loaded_plugins: int = 0,
) -> None:
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

    await broadcast_progress(progress_data)
    logger.debug(f"进度更新: [{operation}] {stage} - {progress}% - {message}")


@router.websocket("/ws/plugin-progress")
async def websocket_plugin_progress(websocket: WebSocket, token: Optional[str] = Query(None)) -> None:
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
            except Exception as e:
                logger.error(f"处理客户端消息时出错: {e}")
                break

    except WebSocketDisconnect:
        active_connections.discard(websocket)
        logger.info(f"📡 插件进度 WebSocket 客户端已断开，当前连接数: {len(active_connections)}")
    except Exception as e:
        logger.error(f"❌ WebSocket 错误: {e}")
        active_connections.discard(websocket)


def get_progress_router() -> APIRouter:
    return router