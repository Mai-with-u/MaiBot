"""统一 WebSocket 路由。"""

from typing import Any, Dict, Optional, Set, cast

import asyncio
import time
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.common.logger import get_logger
from src.webui.core import get_token_manager
from src.webui.logs_ws import load_recent_logs
from src.webui.routers.chat.service import (
    chat_manager,
    dispatch_chat_event,
    normalize_chat_client_info,
    normalize_webui_user_id,
    resolve_initial_virtual_identity,
    send_initial_chat_state,
)
from src.webui.routers.plugin.progress import get_current_progress
from src.webui.routers.websocket.auth import verify_ws_token
from src.webui.routers.websocket.manager import websocket_manager

logger = get_logger("webui.unified_ws")
router = APIRouter()
_background_tasks: Set["asyncio.Task[None]"] = set()


def _build_error(code: str, message: str) -> Dict[str, Any]:
    """构建统一错误响应体。

    Args:
        code: 错误码。
        message: 错误描述。

    Returns:
        Dict[str, Any]: 统一错误对象。
    """
    return {
        "code": code,
        "message": message,
    }


def _get_request_data(message: Dict[str, Any]) -> Dict[str, Any]:
    """从客户端消息中提取数据字段。

    Args:
        message: 客户端消息。

    Returns:
        Dict[str, Any]: 标准化后的数据字典。
    """
    data = message.get("data", {})
    if isinstance(data, dict):
        return cast(Dict[str, Any], data)
    return {}


def _track_background_task(task: "asyncio.Task[None]") -> None:
    """登记后台任务并在完成后自动清理。

    Args:
        task: 后台协程任务。
    """
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def authenticate_websocket_connection(websocket: WebSocket, token: Optional[str]) -> bool:
    """校验统一 WebSocket 连接的认证状态。

    Args:
        websocket: FastAPI WebSocket 对象。
        token: 可选的一次性握手 Token。

    Returns:
        bool: 认证通过时返回 ``True``。
    """
    if token and verify_ws_token(token):
        logger.debug("统一 WebSocket 使用临时 token 认证成功")
        return True

    cookie_token = websocket.cookies.get("maibot_session")
    if cookie_token:
        token_manager = get_token_manager()
        if token_manager.verify_token(cookie_token):
            logger.debug("统一 WebSocket 使用 Cookie 认证成功")
            return True

    return False


async def _handle_logs_subscribe(connection_id: str, request_id: Optional[str], data: Dict[str, Any]) -> None:
    """处理日志域订阅请求。

    Args:
        connection_id: 连接 ID。
        request_id: 请求 ID。
        data: 订阅参数。
    """
    replay_limit = int(data.get("replay", 100) or 100)
    replay_limit = max(0, min(replay_limit, 500))
    websocket_manager.subscribe(connection_id, domain="logs", topic="main")
    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=True,
        data={"domain": "logs", "topic": "main"},
    )
    await websocket_manager.send_event(
        connection_id,
        domain="logs",
        event="snapshot",
        topic="main",
        data={"entries": load_recent_logs(limit=replay_limit)},
    )


async def _handle_plugin_progress_subscribe(connection_id: str, request_id: Optional[str]) -> None:
    """处理插件进度域订阅请求。

    Args:
        connection_id: 连接 ID。
        request_id: 请求 ID。
    """
    websocket_manager.subscribe(connection_id, domain="plugin_progress", topic="main")
    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=True,
        data={"domain": "plugin_progress", "topic": "main"},
    )
    await websocket_manager.send_event(
        connection_id,
        domain="plugin_progress",
        event="snapshot",
        topic="main",
        data={"progress": get_current_progress()},
    )


async def _handle_maisaka_monitor_subscribe(
    connection_id: str,
    request_id: Optional[str],
    data: Dict[str, Any],
) -> None:
    """处理 MaiSaka 监控域订阅请求。

    Args:
        connection_id: 连接 ID。
        request_id: 请求 ID。
        data: 订阅参数。
    """
    logger.info(
        f"MaiSaka 监控订阅请求: connection_id={connection_id} "
        f"manager_id={id(websocket_manager)}"
    )
    since_event_id = _coerce_non_negative_int(data.get("since_event_id"))
    replay_limit = _coerce_non_negative_int(data.get("replay_limit"), default=1000)
    replay_limit = max(1, min(replay_limit, 10000))
    websocket_manager.subscribe(connection_id, domain="maisaka_monitor", topic="main")
    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=True,
        data={
            "domain": "maisaka_monitor",
            "topic": "main",
            "since_event_id": since_event_id,
            "replay_limit": replay_limit,
        },
    )
    from src.maisaka.monitor.event_store import replay_monitor_events

    replay_events = await asyncio.to_thread(
        replay_monitor_events,
        since_event_id=since_event_id,
        limit=replay_limit,
    )
    for replay_event in replay_events:
        await websocket_manager.send_event(
            connection_id,
            domain="maisaka_monitor",
            event=str(replay_event["event"]),
            topic="main",
            data=cast(Dict[str, Any], replay_event["data"]),
        )

    from src.maisaka.display.stage_status_board import get_stage_status_snapshot

    await websocket_manager.send_event(
        connection_id,
        domain="maisaka_monitor",
        event="stage.snapshot",
        topic="main",
        data={"entries": get_stage_status_snapshot(), "timestamp": time.time()},
    )


def _coerce_non_negative_int(value: Any, *, default: int = 0) -> int:
    """将订阅参数解析为非负整数。"""

    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        normalized_value = value.strip()
        if normalized_value:
            try:
                return max(0, int(normalized_value))
            except ValueError:
                return default
    return default


async def _handle_subscribe(connection_id: str, message: Dict[str, Any]) -> None:
    """处理主题订阅请求。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    request_id = cast(Optional[str], message.get("id"))
    domain = str(message.get("domain") or "").strip()
    topic = str(message.get("topic") or "").strip()
    data = _get_request_data(message)

    if domain == "logs" and topic == "main":
        await _handle_logs_subscribe(connection_id, request_id, data)
        return

    if domain == "plugin_progress" and topic == "main":
        await _handle_plugin_progress_subscribe(connection_id, request_id)
        return

    if domain == "maisaka_monitor" and topic == "main":
        await _handle_maisaka_monitor_subscribe(connection_id, request_id, data)
        return

    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=False,
        error=_build_error("unsupported_subscription", f"不支持的订阅目标: {domain}:{topic}"),
    )


async def _handle_unsubscribe(connection_id: str, message: Dict[str, Any]) -> None:
    """处理主题退订请求。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    request_id = cast(Optional[str], message.get("id"))
    domain = str(message.get("domain") or "").strip()
    topic = str(message.get("topic") or "").strip()

    if not domain or not topic:
        await websocket_manager.send_response(
            connection_id,
            request_id=request_id,
            ok=False,
            error=_build_error("invalid_unsubscribe", "退订请求缺少 domain 或 topic"),
        )
        return

    websocket_manager.unsubscribe(connection_id, domain=domain, topic=topic)
    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=True,
        data={"domain": domain, "topic": topic},
    )


async def _open_chat_session(connection_id: str, message: Dict[str, Any]) -> None:
    """打开一个逻辑聊天会话。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    request_id = cast(Optional[str], message.get("id"))
    client_session_id = str(message.get("session") or "").strip()
    if not client_session_id:
        await websocket_manager.send_response(
            connection_id,
            request_id=request_id,
            ok=False,
            error=_build_error("missing_session", "聊天会话打开请求缺少 session"),
        )
        return

    data = _get_request_data(message)
    normalized_user_id = normalize_webui_user_id(cast(Optional[str], data.get("user_id")))
    current_user_name = str(data.get("user_name") or "WebUI用户")
    client_info = normalize_chat_client_info(cast(Optional[Dict[str, Any]], data.get("client")))
    current_virtual_config = resolve_initial_virtual_identity(
        platform=cast(Optional[str], data.get("platform")),
        person_id=cast(Optional[str], data.get("person_id")),
        group_name=cast(Optional[str], data.get("group_name")),
        group_id=cast(Optional[str], data.get("group_id")),
    )
    restore = bool(data.get("restore"))
    session_id = f"{connection_id}:{client_session_id}"

    async def send_chat_event(chat_message: Dict[str, Any]) -> None:
        """将聊天消息封装为统一事件并发送。

        Args:
            chat_message: 聊天消息体。
        """
        event_name = str(chat_message.get("type") or "message")
        await websocket_manager.send_event(
            connection_id,
            domain="chat",
            event=event_name,
            session=client_session_id,
            data=chat_message,
        )

    await chat_manager.connect(
        session_id=session_id,
        connection_id=connection_id,
        client_session_id=client_session_id,
        user_id=normalized_user_id,
        user_name=current_user_name,
        virtual_config=current_virtual_config,
        client_info=client_info,
        sender=send_chat_event,
    )
    websocket_manager.register_chat_session(connection_id, client_session_id, session_id)
    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=True,
        data={
            "session": client_session_id,
            "session_id": session_id,
            "client_mode": client_info["type"],
            "client": client_info,
        },
    )
    await send_initial_chat_state(
        session_id=session_id,
        user_id=normalized_user_id,
        user_name=current_user_name,
        virtual_config=current_virtual_config,
        client_info=client_info,
        include_welcome=not restore,
    )


async def _close_chat_session(connection_id: str, message: Dict[str, Any]) -> None:
    """关闭一个逻辑聊天会话。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    request_id = cast(Optional[str], message.get("id"))
    client_session_id = str(message.get("session") or "").strip()
    session_id = websocket_manager.get_chat_session_id(connection_id, client_session_id)
    if session_id is None:
        await websocket_manager.send_response(
            connection_id,
            request_id=request_id,
            ok=False,
            error=_build_error("session_not_found", f"找不到聊天会话: {client_session_id}"),
        )
        return

    chat_manager.disconnect(session_id)
    websocket_manager.unregister_chat_session(connection_id, client_session_id)
    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=True,
        data={"session": client_session_id},
    )


async def _process_chat_message(connection_id: str, client_session_id: str, data: Dict[str, Any]) -> None:
    """在后台处理聊天消息事件。

    Args:
        connection_id: 连接 ID。
        client_session_id: 前端会话 ID。
        data: 客户端提交的消息数据。
    """
    session_id = websocket_manager.get_chat_session_id(connection_id, client_session_id)
    if session_id is None:
        return

    session_state = chat_manager.get_session(session_id)
    if session_state is None:
        return

    next_user_name, next_virtual_config = await dispatch_chat_event(
        session_id=session_id,
        session_id_prefix=session_id[:8],
        data=data,
        current_user_name=session_state.user_name,
        normalized_user_id=session_state.user_id,
        current_virtual_config=session_state.virtual_config,
    )
    chat_manager.update_session_context(
        session_id=session_id,
        user_name=next_user_name,
        virtual_config=next_virtual_config,
    )


async def _handle_chat_message_send(connection_id: str, message: Dict[str, Any]) -> None:
    """处理聊天消息发送请求。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    request_id = cast(Optional[str], message.get("id"))
    client_session_id = str(message.get("session") or "").strip()
    session_id = websocket_manager.get_chat_session_id(connection_id, client_session_id)
    if session_id is None:
        await websocket_manager.send_response(
            connection_id,
            request_id=request_id,
            ok=False,
            error=_build_error("session_not_found", f"找不到聊天会话: {client_session_id}"),
        )
        return

    data = _get_request_data(message)
    payload = {
        "type": "message",
        "content": data.get("content", ""),
        "images": data.get("images", []),
        "emojis": data.get("emojis", []),
        "files": data.get("files", []),
        "voices": data.get("voices", []),
        "user_name": data.get("user_name", ""),
    }
    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=True,
        data={"accepted": True, "session": client_session_id},
    )
    _track_background_task(asyncio.create_task(_process_chat_message(connection_id, client_session_id, payload)))


async def _handle_chat_nickname_update(connection_id: str, message: Dict[str, Any]) -> None:
    """处理聊天昵称更新请求。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    request_id = cast(Optional[str], message.get("id"))
    client_session_id = str(message.get("session") or "").strip()
    session_id = websocket_manager.get_chat_session_id(connection_id, client_session_id)
    if session_id is None:
        await websocket_manager.send_response(
            connection_id,
            request_id=request_id,
            ok=False,
            error=_build_error("session_not_found", f"找不到聊天会话: {client_session_id}"),
        )
        return

    data = _get_request_data(message)
    session_state = chat_manager.get_session(session_id)
    if session_state is None:
        await websocket_manager.send_response(
            connection_id,
            request_id=request_id,
            ok=False,
            error=_build_error("session_not_found", f"找不到聊天会话: {client_session_id}"),
        )
        return

    next_user_name, next_virtual_config = await dispatch_chat_event(
        session_id=session_id,
        session_id_prefix=session_id[:8],
        data={
            "type": "update_nickname",
            "user_name": data.get("user_name", ""),
        },
        current_user_name=session_state.user_name,
        normalized_user_id=session_state.user_id,
        current_virtual_config=session_state.virtual_config,
    )
    chat_manager.update_session_context(
        session_id=session_id,
        user_name=next_user_name,
        virtual_config=next_virtual_config,
    )
    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=True,
        data={"session": client_session_id, "user_name": next_user_name},
    )


async def _handle_chat_call(connection_id: str, message: Dict[str, Any]) -> None:
    """处理聊天域调用请求。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    request_id = cast(Optional[str], message.get("id"))
    method = str(message.get("method") or "").strip()

    if method == "session.open":
        await _open_chat_session(connection_id, message)
        return

    if method == "session.close":
        await _close_chat_session(connection_id, message)
        return

    if method == "message.send":
        await _handle_chat_message_send(connection_id, message)
        return

    if method == "session.update_nickname":
        await _handle_chat_nickname_update(connection_id, message)
        return

    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=False,
        error=_build_error("unsupported_method", f"不支持的聊天方法: {method}"),
    )


async def _handle_call(connection_id: str, message: Dict[str, Any]) -> None:
    """处理统一调用请求。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    request_id = cast(Optional[str], message.get("id"))
    domain = str(message.get("domain") or "").strip()
    if domain == "chat":
        await _handle_chat_call(connection_id, message)
        return

    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=False,
        error=_build_error("unsupported_domain", f"不支持的调用域: {domain}"),
    )


async def handle_client_message(connection_id: str, message: Dict[str, Any]) -> None:
    """处理统一 WebSocket 客户端消息。

    Args:
        connection_id: 连接 ID。
        message: 客户端消息。
    """
    operation = str(message.get("op") or "").strip()
    request_id = cast(Optional[str], message.get("id"))

    if operation == "ping":
        await websocket_manager.send_pong(connection_id, time.time())
        return

    if operation == "subscribe":
        await _handle_subscribe(connection_id, message)
        return

    if operation == "unsubscribe":
        await _handle_unsubscribe(connection_id, message)
        return

    if operation == "call":
        await _handle_call(connection_id, message)
        return

    await websocket_manager.send_response(
        connection_id,
        request_id=request_id,
        ok=False,
        error=_build_error("unsupported_operation", f"不支持的操作: {operation}"),
    )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = Query(None)) -> None:
    """统一 WebSocket 入口。

    Args:
        websocket: FastAPI WebSocket 对象。
        token: 可选的一次性握手 Token。
    """
    if not await authenticate_websocket_connection(websocket, token):
        logger.warning("统一 WebSocket 连接被拒绝：认证失败")
        await websocket.close(code=4001, reason="认证失败，请重新登录")
        return

    connection_id = uuid.uuid4().hex
    await websocket_manager.connect(connection_id=connection_id, websocket=websocket)
    logger.info(f"统一 WebSocket 客户端已连接: connection={connection_id}")
    await websocket_manager.send_event(
        connection_id,
        domain="system",
        event="ready",
        data={"connection_id": connection_id, "timestamp": time.time()},
    )

    try:
        while True:
            raw_message = await websocket.receive_json()
            if not isinstance(raw_message, dict):
                await websocket_manager.send_response(
                    connection_id,
                    request_id=None,
                    ok=False,
                    error=_build_error("invalid_message", "消息必须是 JSON 对象"),
                )
                continue
            await handle_client_message(connection_id, cast(Dict[str, Any], raw_message))
    except WebSocketDisconnect:
        logger.info(f"统一 WebSocket 客户端已断开: connection={connection_id}")
    except asyncio.CancelledError:
        logger.warning(f"统一 WebSocket 连接处理被取消: connection={connection_id}")
        raise
    except Exception as exc:
        logger.error(f"统一 WebSocket 处理失败: connection={connection_id}, error={exc}", exc_info=True)
    finally:
        chat_manager.disconnect_connection(connection_id)
        await websocket_manager.disconnect(connection_id)
        logger.info(
            f"统一 WebSocket 连接清理完成: connection={connection_id}, 剩余连接={len(websocket_manager.connections)}",
        )
