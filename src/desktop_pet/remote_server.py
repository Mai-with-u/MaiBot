"""远端桌宠传输：WebUI 循环拥有网络连接，业务始终在 Bot 主循环执行。"""

from io import BytesIO
from typing import Any, Awaitable, Callable, Dict, Tuple, TypeVar

from fastapi import WebSocket, WebSocketDisconnect
from PIL import Image

import asyncio
import base64
import binascii
import hashlib
import json
import math
import re
import secrets
import time

from src.common.runtime_loop import get_main_loop, run_on_main_loop

from .attachments import MAX_IMAGE_BYTES

PROTOCOL_VERSION = 1
HEARTBEAT_INTERVAL = 20
HEARTBEAT_TIMEOUT = 60
MAX_EVENT_BYTES = 24 * 1024 * 1024
MAX_CONTROL_BYTES = 64 * 1024
REQUEST_TTL = 30 * 60
MAX_RECENT_REQUESTS = 256
T = TypeVar("T")


async def on_main_loop(operation: Callable[[], Awaitable[T]]) -> T:
    """不允许运行时停止后退回 WebUI 循环继续修改桌宠状态。"""
    loop = get_main_loop()
    if loop is None or loop.is_closed() or not loop.is_running():
        raise RuntimeError("Bot 主循环尚未就绪")
    return await run_on_main_loop(operation())


def decode_object(raw: str) -> Dict[str, Any]:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"JSON 不支持 {value}")

    value = json.loads(raw, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("请求必须是 JSON 对象")
    return value


def validate_request_id(request_id: str) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
        raise ValueError("请求 ID 必须为 32 位十六进制 UUID")


def validate_hello(event: Dict[str, Any]) -> Tuple[str, Dict[str, bool]]:
    if event.get("type") != "hello" or type(event.get("version")) is not int or event["version"] != PROTOCOL_VERSION:
        raise ValueError("远端桌宠协议版本不兼容，需要版本 1")
    client_id = event.get("client_id")
    if not isinstance(client_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", client_id) is None:
        raise ValueError("客户端 ID 必须为 1～64 位字母、数字、下划线或短横线")
    capabilities = event.get("capabilities")
    if not isinstance(capabilities, dict) or type(capabilities.get("screen_read")) is not bool:
        raise ValueError("客户端必须明确声明 screen_read 能力")
    return client_id, {"screen_read": capabilities["screen_read"]}


def _text_field(payload: Dict[str, Any], name: str, *, limit: int, empty: bool = False) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"{name} 必须为{'最多' if empty else '1～'} {limit} 字的文本")
    return value


def validate_event(event: Dict[str, Any]) -> None:
    """先检查网络事件字段，再交给原本信任本机窗口的事件分发器。"""
    kind = event["type"]
    if kind in {"ready", "clicked", "buttons_refresh"}:
        return
    if kind == "ack":
        _text_field(event, "id", limit=128)
        if "error" in event and not isinstance(event["error"], str):
            raise ValueError("ack.error 必须为文本")
        return
    if kind == "button_click":
        _text_field(event, "plugin_id", limit=200)
        _text_field(event, "button_id", limit=64)
        if event.get("action_id") is not None:
            _text_field(event, "action_id", limit=64)
        return
    if kind in {"drag_started", "drag_ended"}:
        _text_field(event, "gesture_id", limit=128)
        if kind == "drag_started":
            for name in ("offset_x", "offset_y", "scale"):
                value = event.get(name)
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f"{name} 必须为有限数值")
            if not 0 < event["scale"] <= 20:
                raise ValueError("scale 必须在 0～20 范围内")
        return
    if kind == "screen_action":
        if _text_field(event, "action", limit=10) not in {"read", "start", "stop"}:
            raise ValueError("未知桌面读取操作")
        return
    if kind == "exploration_tasks":
        _text_field(event, "task_id", limit=64, empty=True)
        _text_field(event, "request_id", limit=64)
        return
    if kind == "explore":
        _text_field(event, "goal", limit=2000)
        return
    raise ValueError(f"未知的桌宠事件: {kind}")


def validate_todo_payload(action: str, payload: Dict[str, Any]) -> None:
    if action == "list":
        if type(payload.get("include_completed", False)) is not bool:
            raise ValueError("include_completed 必须为布尔值")
        return
    if action in {"update", "complete", "delete"}:
        _text_field(payload, "todo_id", limit=128)
    if action == "add":
        _text_field(payload, "text", limit=200)
    elif action == "update" and payload.get("text") is not None:
        _text_field(payload, "text", limit=200)
    options = payload.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("options 必须为 JSON 对象")
    for name in ("condition_type", "condition", "kind"):
        if name in options and not isinstance(options[name], str):
            raise ValueError(f"{name} 必须为文本")


def validate_chat_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验实际图片内容；在工作线程执行解码和图片检查。"""
    text = payload.get("text")
    images = payload.get("images")
    if not isinstance(text, str) or not isinstance(images, list):
        raise ValueError("图文请求必须包含 text 字符串和 images 列表")
    text = text.strip()
    if len(text) > 10000 or len(images) > 4 or not (text or images):
        raise ValueError("最多 10000 字、4 张图片，消息不能为空")
    normalized = []
    for item in images:
        if not isinstance(item, dict) or not isinstance(item.get("base64"), str):
            raise ValueError("图片必须包含 base64 数据")
        encoded = item["base64"]
        if len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
            raise ValueError("每张图片不能超过 8 MiB")
        try:
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("每张图片不能超过 8 MiB")
            with Image.open(BytesIO(raw)) as picture:
                if picture.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise ValueError("仅支持 PNG、JPEG、WebP 或 GIF 图片")
                if picture.width * picture.height > 25_000_000:
                    raise ValueError("图片不能超过 2500 万像素")
                mime = Image.MIME[picture.format]
                picture.verify()
        except (binascii.Error, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("图片数据无效") from exc
        normalized.append({"base64": encoded, "mime_type": mime})
    return {"text": text, "images": normalized}


class RemotePeer:
    """只有所属 WebUI 循环接触 socket；主循环可安全 await send/close。"""

    def __init__(self, websocket: WebSocket, client_id: str, capabilities: Dict[str, bool]) -> None:
        self.client_id = client_id
        self.capabilities = capabilities
        self.connection_id = secrets.token_urlsafe(32)
        self._websocket = websocket
        self._loop = asyncio.get_running_loop()
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._requests = 0
        self._image_requests = 0

    async def _on_socket_loop(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._loop.is_closed() or not self._loop.is_running():
            raise ConnectionError("远端桌宠连接已关闭")
        if asyncio.get_running_loop() is self._loop:
            return await operation()
        future = asyncio.run_coroutine_threadsafe(operation(), self._loop)
        return await asyncio.wrap_future(future)

    async def send(self, command: Dict[str, Any]) -> None:
        await self._on_socket_loop(lambda: self._send(command))

    async def _send(self, command: Dict[str, Any]) -> None:
        if self._closed:
            raise ConnectionError("远端桌宠连接已关闭")
        # 皮肤及媒体命令可能较大，序列化不能占住任一事件循环。
        encoded = await asyncio.to_thread(json.dumps, command, ensure_ascii=False, allow_nan=False)
        async with self._write_lock:
            if self._closed:
                raise ConnectionError("远端桌宠连接已关闭")
            await asyncio.wait_for(self._websocket.send_text(encoded), timeout=15)

    async def close(self, reason: str) -> None:
        await self._on_socket_loop(lambda: self._close(reason))

    async def _close(self, reason: str) -> None:
        async with self._write_lock:
            if self._closed:
                return
            self._closed = True
            try:
                await asyncio.wait_for(self._websocket.close(code=1008, reason=reason[:35]), timeout=5)
            except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
                pass  # 连接另一端已经关闭，无需再次发送 close 帧。

    def mark_disconnected(self) -> None:
        self._closed = True

    def begin_request(self) -> None:
        if self._closed:
            raise ConnectionError("远端桌宠连接已关闭")
        if self._requests >= 2:
            raise RuntimeError("远端桌宠请求过多，请稍后重试")
        self._requests += 1

    def end_request(self) -> None:
        self._requests -= 1

    def begin_image_request(self) -> None:
        if self._closed:
            raise ConnectionError("远端桌宠连接已关闭")
        if self._image_requests >= 8:
            raise RuntimeError("图片读取请求过多，请稍后重试")
        self._image_requests += 1

    def end_image_request(self) -> None:
        self._image_requests -= 1


class RecentRequests:
    """保留已投递请求的结果，HTTP 断线不取消已开始的业务，也不重复执行。"""

    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, str, str], Tuple[str, float, asyncio.Task[Any]]] = {}

    async def execute(
        self, client_id: str, domain: str, request_id: str, payload: Dict[str, Any],
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        validate_request_id(request_id)
        digest = await asyncio.to_thread(self._digest, payload)
        now = time.monotonic()
        for key, (_, created, task) in list(self._entries.items()):
            if task.done() and created + REQUEST_TTL <= now:
                del self._entries[key]
        key = (client_id, domain, request_id)
        entry = self._entries.get(key)
        if entry is not None:
            if entry[0] != digest:
                raise ValueError("同一个请求 ID 不能用于不同内容")
            return await asyncio.shield(entry[2])
        if len(self._entries) >= MAX_RECENT_REQUESTS:
            raise RuntimeError("近期桌宠请求过多，请稍后重试")
        task = asyncio.create_task(operation())
        # HTTP 客户端离线后仍需消费任务异常；下次相同请求会得到原来的失败结果。
        task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
        self._entries[key] = (digest, now, task)
        return await asyncio.shield(task)

    @staticmethod
    def _digest(payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def receive_event(websocket: WebSocket, *, timeout: float, limit: int = MAX_EVENT_BYTES) -> Dict[str, Any]:
    message = await asyncio.wait_for(websocket.receive(), timeout=timeout)
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text")
    if not isinstance(raw, str):
        raise ValueError("桌宠 WebSocket 仅接受 JSON 文本消息")
    size = len(raw.encode("utf-8"))
    if size > limit:
        raise ValueError("远端桌宠消息过大；图文消息请使用 HTTP 上传")
    event = await asyncio.to_thread(decode_object, raw)
    if not isinstance(event.get("type"), str):
        raise ValueError("桌宠事件缺少 type")
    if event["type"] != "ack" and size > MAX_CONTROL_BYTES:
        raise ValueError("桌宠控制消息不能超过 64 KiB")
    return event
