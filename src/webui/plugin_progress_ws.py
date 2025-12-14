"""WebSocket 插件加载进度推送模块"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Set, Dict, Any, Optional
import json
import asyncio
from src.common.logger import get_logger
from src.webui.token_manager import get_token_manager
from src.webui.ws_auth import verify_ws_token

logger = get_logger("webui.plugin_progress")

# 创建路由器
router = APIRouter()

# 全局 WebSocket 连接池
active_connections: Set[WebSocket] = set()

# 当前加载进度状态
current_progress: Dict[str, Any] = {
    "operation": "idle",  # idle, fetch, install, uninstall, update
    "stage": "idle",  # idle, loading, success, error
    "progress": 0,  # 0-100
    "message": "",
    "error": None,
    "plugin_id": None,  # 当前操作的插件 ID
    "total_plugins": 0,
    "loaded_plugins": 0,
}


async def broadcast_progress(progress_data: Dict[str, Any]):
    """广播进度更新到所有连接的客户端"""
    global current_progress
    current_progress = progress_data.copy()

    if not active_connections:
        return

    message = json.dumps(progress_data, ensure_ascii=False)
    disconnected = set()

    for websocket in active_connections:
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error(f"发送进度更新失败: {e}")
            disconnected.add(websocket)

    # 移除断开的连接
    for websocket in disconnected:
        active_connections.discard(websocket)


async def update_progress(
    stage: str,
    progress: int,
    message: str,
    operation: str = "fetch",
    error: str = None,
    plugin_id: str = None,
    total_plugins: int = 0,
    loaded_plugins: int = 0,
):
    """更新并广播进度

    Args:
        stage: 阶段 (idle, loading, success, error)
        progress: 进度百分比 (0-100)
        message: 当前消息
        operation: 操作类型 (fetch, install, uninstall, update)
        error: 错误信息（可选）
        plugin_id: 当前操作的插件 ID
        total_plugins: 总插件数
        loaded_plugins: 已加载插件数
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

    await broadcast_progress(progress_data)
    logger.debug(f"进度更新: [{operation}] {stage} - {progress}% - {message}")


@router.websocket("/ws/plugin-progress")
async def websocket_plugin_progress(websocket: WebSocket, token: Optional[str] = Query(None)):
    """WebSocket 插件加载进度推送端点

    客户端连接后会立即收到当前进度状态
    支持三种认证方式（按优先级）：
    1. query 参数 token（推荐，通过 /api/webui/ws-token 获取临时 token）
    2. Cookie 中的 maibot_session
    3. 直接使用 session token（兼容）
    
    示例：ws://host/ws/plugin-progress?token=xxx
    """
    is_authenticated = False
    
    # 方式 1: 尝试验证临时 WebSocket token（推荐方式）
    if token and verify_ws_token(token):
        is_authenticated = True
        logger.debug("插件进度 WebSocket 使用临时 token 认证成功")
    
    # 方式 2: 尝试从 Cookie 获取 session token
    if not is_authenticated:
        cookie_token = websocket.cookies.get("maibot_session")
        if cookie_token:
            token_manager = get_token_manager()
            if token_manager.verify_token(cookie_token):
                is_authenticated = True
                logger.debug("插件进度 WebSocket 使用 Cookie 认证成功")
    
    # 方式 3: 尝试直接验证 query 参数作为 session token（兼容旧方式）
    if not is_authenticated and token:
        token_manager = get_token_manager()
        if token_manager.verify_token(token):
            is_authenticated = True
            logger.debug("插件进度 WebSocket 使用 session token 认证成功")
    
    if not is_authenticated:
        logger.warning("插件进度 WebSocket 连接被拒绝：认证失败")
        await websocket.close(code=4001, reason="认证失败，请重新登录")
        return
    
    await websocket.accept()
    active_connections.add(websocket)
    logger.info(f"📡 插件进度 WebSocket 客户端已连接（已认证），当前连接数: {len(active_connections)}")

    try:
        # 发送当前进度状态
        await websocket.send_text(json.dumps(current_progress, ensure_ascii=False))

        # 保持连接并处理客户端消息
        while True:
            try:
                data = await websocket.receive_text()

                # 处理客户端心跳
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
    """获取插件进度 WebSocket 路由器"""
    return router
