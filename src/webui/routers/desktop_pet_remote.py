"""远端桌宠业务入口；宿主代理统一验证登录，Bot 子应用仅由本机 IPC 访问。"""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

import asyncio

from src.common.logger import get_logger
from src.desktop_pet.attachments import MAX_REQUEST_BYTES
from src.desktop_pet.remote_server import (
    HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT, MAX_CONTROL_BYTES, PROTOCOL_VERSION,
    RecentRequests, RemotePeer, decode_object, on_main_loop, receive_event, validate_chat_request,
    validate_event, validate_hello, validate_request_id, validate_todo_payload,
)

router = APIRouter(prefix="/desktop-pet/remote", tags=["desktop-pet"])
logger = get_logger("desktop_pet")
_connections: Dict[str, RemotePeer] = {}
_recent_requests = RecentRequests()


def _service():
    from src.desktop_pet.service import desktop_pet_service

    return desktop_pet_service


def _peer(request: Request) -> RemotePeer:
    connection_id = request.headers.get("x-desktop-pet-connection", "")
    peer = _connections.get(connection_id)
    if peer is None:
        raise HTTPException(409, "桌宠连接已失效，请先连接远端桌宠")
    return peer


async def _body(request: Request) -> Dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise HTTPException(400, "Content-Length 无效") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise HTTPException(413, "桌宠请求不能超过 48 MiB")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "桌宠请求不能超过 48 MiB")
        body.extend(chunk)
    try:
        return await asyncio.to_thread(lambda: decode_object(body.decode("utf-8")))
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.websocket("/ws")
async def remote_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    peer = None
    attached = False
    try:
        hello = await receive_event(websocket, timeout=10, limit=MAX_CONTROL_BYTES)
        client_id, capabilities = validate_hello(hello)
        peer = RemotePeer(websocket, client_id, capabilities)
        service = _service()
        await on_main_loop(lambda: service.attach_remote(peer))
        attached = True
        _connections[peer.connection_id] = peer
        await peer.send({
            "type": "hello", "version": PROTOCOL_VERSION, "connection_id": peer.connection_id,
            "heartbeat_interval": HEARTBEAT_INTERVAL, "heartbeat_timeout": HEARTBEAT_TIMEOUT,
        })
        while True:
            event = await receive_event(websocket, timeout=HEARTBEAT_TIMEOUT)
            if event["type"] == "ping":
                await peer.send({"type": "pong"})
            elif event["type"] == "pong":
                continue
            elif event["type"] in {"hello", "chat"}:
                raise ValueError("重复握手或未通过 HTTP 上传的聊天请求")
            else:
                validate_event(event)
                await on_main_loop(lambda event=event: service.receive_remote_event(peer, event))
    except WebSocketDisconnect:
        pass
    except (ValueError, RuntimeError, TimeoutError, ConnectionError) as exc:
        try:
            if peer is None:
                await websocket.send_json({"type": "error", "error": str(exc)})
                await websocket.close(code=1008, reason="远端桌宠连接结束")
            else:
                await peer.send({"type": "error", "error": str(exc)})
                await peer.close("远端桌宠连接结束")
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
    finally:
        if peer is not None:
            _connections.pop(peer.connection_id, None)
            peer.mark_disconnected()
            if attached:
                try:
                    await on_main_loop(lambda: service.detach_remote(peer))
                except RuntimeError as exc:
                    logger.debug("远端桌宠断线清理: %s", exc)


@router.post("/chat/{request_id}")
async def remote_chat(request_id: str, request: Request) -> Dict[str, Any]:
    peer = _peer(request)
    started = False
    try:
        validate_request_id(request_id)
        peer.begin_request()
        started = True
        body = await _body(request)
        payload = await asyncio.to_thread(validate_chat_request, body)
        service = _service()
        return await _recent_requests.execute(
            peer.client_id, "chat", request_id, payload,
            lambda: on_main_loop(lambda: service.remote_chat(peer, request_id, payload)),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, ConnectionError) as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        if started:
            peer.end_request()


@router.post("/todos")
async def remote_todos(request: Request) -> Dict[str, Any]:
    peer = _peer(request)
    started = False
    try:
        peer.begin_request()
        started = True
        body = await _body(request)
        action = body.get("action")
        if not isinstance(action, str) or action not in {"list", "add", "update", "complete", "delete"}:
            raise ValueError("不支持的待办操作")
        payload = {key: value for key, value in body.items() if key not in {"action", "request_id"}}
        validate_todo_payload(action, payload)
        service = _service()
        if action == "list":
            return await on_main_loop(lambda: service.remote_todo(peer, action, payload))
        request_id = body.get("request_id")
        if not isinstance(request_id, str):
            raise ValueError("修改待办必须提供 request_id")
        return await _recent_requests.execute(
            peer.client_id, "todo", request_id, {"action": action, **payload},
            lambda: on_main_loop(lambda: service.remote_todo(peer, action, payload)),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, ConnectionError) as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        if started:
            peer.end_request()


@router.get("/todos/images/{image_id}")
async def remote_todo_image(image_id: str, request: Request) -> Dict[str, Any]:
    peer = _peer(request)
    started = False
    try:
        peer.begin_image_request()
        started = True
        return await on_main_loop(lambda: _service().remote_todo(peer, "image", {"image_id": image_id}))
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except (RuntimeError, ConnectionError) as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        if started:
            peer.end_image_request()
