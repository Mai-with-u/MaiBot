"""WebSocket 日志推送模块"""

from pathlib import Path
from typing import Dict, List, Optional, Set

import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.common.logger import get_logger
from src.common.logger_color_and_mapping import MODULE_ALIASES, MODULE_COLORS
from src.webui.core import get_token_manager
from src.webui.routers.websocket.auth import verify_ws_token
from src.webui.routers.websocket.manager import websocket_manager

logger = get_logger("webui.logs_ws")
router = APIRouter()

# 全局 WebSocket 连接池
active_connections: Set[WebSocket] = set()


def _resolve_module_style(module_name: str) -> Dict:
    """解析日志模块在 WebUI 中使用的颜色信息。"""
    module_name = module_name.strip()
    if not module_name:
        return {}

    parts = module_name.split(".")
    for index in range(len(parts), 0, -1):
        candidate = ".".join(parts[:index])
        module_style = MODULE_COLORS.get(candidate)
        if module_style is None:
            continue

        foreground_color, background_color, bold = module_style
        style = {
            "moduleColor": foreground_color,
            "moduleBold": bold,
        }
        if background_color:
            style["moduleBackgroundColor"] = background_color
        return style

    return {}


def _format_log_entry(raw_entry: Dict, log_id: str) -> Dict:
    """转换为前端日志查看器使用的数据结构。"""
    module_name = str(raw_entry.get("logger_name", "") or raw_entry.get("module", ""))
    formatted_log = {
        "id": log_id,
        "timestamp": raw_entry.get("timestamp", ""),
        "level": str(raw_entry.get("level", "INFO")).upper(),
        "module": module_name,
        "message": raw_entry.get("event", ""),
    }
    formatted_log.update(_resolve_module_style(module_name))
    # 颜色解析必须使用原始模块名（MODULE_COLORS 按 "." 分段匹配原始 key），
    # 因此别名转换放在颜色解析之后，仅影响前端显示的模块名。
    formatted_log["module"] = MODULE_ALIASES.get(module_name, module_name)
    return formatted_log


def load_recent_logs(limit: int = 100) -> List[Dict]:
    """从日志文件中加载最近的日志

    Args:
        limit: 返回的最大日志条数

    Returns:
        日志列表
    """
    logs = []
    log_dir = Path("logs")

    if not log_dir.exists():
        return logs

    # 获取所有日志文件,按修改时间排序
    log_files = sorted(log_dir.glob("app_*.log.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)

    # 用于生成唯一 ID 的计数器
    log_counter = 0

    # 从最新的文件开始读取
    for log_file in log_files:
        if len(logs) >= limit:
            break

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # 从文件末尾开始读取
                for line in reversed(lines):
                    if len(logs) >= limit:
                        break
                    try:
                        log_entry = json.loads(line.strip())
                        # 转换为前端期望的格式
                        # 使用时间戳 + 计数器生成唯一 ID
                        timestamp_id = (
                            log_entry.get("timestamp", "0").replace("-", "").replace(" ", "").replace(":", "")
                        )
                        formatted_log = _format_log_entry(log_entry, f"{timestamp_id}_{log_counter}")
                        logs.append(formatted_log)
                        log_counter += 1
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            logger.error(f"读取日志文件失败 {log_file}: {e}")
            continue

    # 反转列表，使其按时间顺序排列（旧到新）
    return list(reversed(logs))


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket, token: Optional[str] = Query(None)):
    """WebSocket 日志推送端点

    客户端连接后会持续接收服务器端的日志消息
    支持三种认证方式（按优先级）：
    1. query 参数 token（推荐，通过 /api/webui/ws-token 获取临时 token）
    2. Cookie 中的 maibot_session

    示例：ws://host/ws/logs?token=xxx
    """
    is_authenticated = False

    # 方式 1: 尝试验证临时 WebSocket token（推荐方式）
    if token and verify_ws_token(token):
        is_authenticated = True
        logger.debug("WebSocket 使用临时 token 认证成功")

    # 方式 2: 尝试从 Cookie 获取 session token
    if not is_authenticated:
        cookie_token = websocket.cookies.get("maibot_session")
        if cookie_token:
            token_manager = get_token_manager()
            if token_manager.verify_token(cookie_token):
                is_authenticated = True
                logger.debug("WebSocket 使用 Cookie 认证成功")

    if not is_authenticated:
        logger.warning("WebSocket 连接被拒绝：认证失败")
        await websocket.close(code=4001, reason="认证失败，请重新登录")
        return

    await websocket.accept()
    active_connections.add(websocket)
    logger.info(f"📡 WebSocket 客户端已连接（已认证），当前连接数: {len(active_connections)}")

    # 连接建立后，立即发送历史日志
    try:
        recent_logs = load_recent_logs(limit=100)
        logger.info(f"发送 {len(recent_logs)} 条历史日志到客户端")

        for log_entry in recent_logs:
            await websocket.send_text(json.dumps(log_entry, ensure_ascii=False))
    except Exception as e:
        logger.error(f"发送历史日志失败: {e}")

    try:
        # 保持连接，等待客户端消息或断开
        while True:
            # 接收客户端消息（用于心跳或控制指令）
            data = await websocket.receive_text()

            # 可以处理客户端的控制消息，例如：
            # - "ping" -> 心跳检测
            # - {"filter": "ERROR"} -> 设置日志级别过滤
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        active_connections.discard(websocket)
        logger.info(f"📡 WebSocket 客户端已断开，当前连接数: {len(active_connections)}")
    except Exception as e:
        logger.error(f"❌ WebSocket 错误: {e}")
        active_connections.discard(websocket)


async def broadcast_log(log_data: Dict):
    """广播日志到所有连接的 WebSocket 客户端

    Args:
        log_data: 日志数据字典
    """
    module_name = str(log_data.get("module", ""))
    enriched_log_data = dict(log_data)
    enriched_log_data.update(_resolve_module_style(module_name))
    # 颜色解析必须使用原始模块名，别名转换放在其后，仅影响前端显示。
    enriched_log_data["module"] = MODULE_ALIASES.get(module_name, module_name)
    await websocket_manager.broadcast_to_topic(
        domain="logs",
        topic="main",
        event="entry",
        data={"entry": enriched_log_data},
    )
