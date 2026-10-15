from typing import Any, Dict, List

import asyncio
import json
import threading

import pytest
from starlette.websockets import WebSocketState

from src.maisaka.monitor import events as monitor_events
from src.webui.routers.websocket.manager import UnifiedWebSocketManager


class FakeWebSocket:
    """只实现管理器用到的接口：accept / send_text / close 与两个状态字段。"""

    def __init__(self, fail_on_send: bool = False) -> None:
        self.fail_on_send = fail_on_send
        self.sent: List[Dict[str, Any]] = []
        self.closed = False
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED

    async def accept(self) -> None:
        return None

    async def send_text(self, text: str) -> None:
        if self.fail_on_send:
            raise RuntimeError("transport broken")
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True
        self.application_state = WebSocketState.DISCONNECTED


async def _drain(manager: UnifiedWebSocketManager, connection_id: str) -> None:
    connection = manager.connections[connection_id]
    for _ in range(20):
        if connection.send_queue.empty():
            break
        await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_unserializable_message_is_dropped_without_killing_connection() -> None:
    manager = UnifiedWebSocketManager()
    websocket = FakeWebSocket()
    connection = await manager.connect("conn", websocket)  # type: ignore[arg-type]

    await manager.enqueue("conn", {"op": "event", "data": {"bad": object()}})
    await manager.enqueue("conn", {"op": "pong", "ts": 1})
    await _drain(manager, "conn")

    assert websocket.sent == [{"op": "pong", "ts": 1}]
    assert connection.sender_task is not None and not connection.sender_task.done()
    assert websocket.closed is False

    await manager.disconnect("conn")


@pytest.mark.asyncio
async def test_send_failure_closes_websocket_so_client_reconnects() -> None:
    manager = UnifiedWebSocketManager()
    websocket = FakeWebSocket(fail_on_send=True)
    connection = await manager.connect("conn", websocket)  # type: ignore[arg-type]

    await manager.enqueue("conn", {"op": "pong", "ts": 1})
    assert connection.sender_task is not None
    await asyncio.wait_for(connection.sender_task, timeout=1)

    assert websocket.closed is True

    await manager.disconnect("conn")


@pytest.mark.asyncio
async def test_monitor_broadcast_order_follows_event_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.maisaka.monitor import event_store
    from src.webui.routers.websocket import manager as manager_module

    next_event_id = 0
    id_lock = threading.Lock()
    first_recorded = threading.Event()

    def fake_record(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal next_event_id
        with id_lock:
            next_event_id += 1
            event_id = next_event_id
        if event_id == 1:
            # 第一条事件拿到 event_id 后写库较慢，并发的第二条若不加锁会先广播出去
            first_recorded.set()
            threading.Event().wait(0.05)
        return {**payload, "event_id": event_id}

    broadcast_ids: List[int] = []

    async def fake_broadcast_to_topic(domain: str, topic: str, event: str, data: Dict[str, Any]) -> None:
        broadcast_ids.append(int(data["event_id"]))

    monkeypatch.setattr(event_store, "record_monitor_event", fake_record)
    monkeypatch.setattr(manager_module.websocket_manager, "broadcast_to_topic", fake_broadcast_to_topic)
    monkeypatch.setattr(monitor_events, "_enrich_session_identity", lambda data: data)

    first = asyncio.create_task(monitor_events._broadcast("message.ingested", {"session_id": "s", "timestamp": 1}))
    await asyncio.to_thread(first_recorded.wait, 1)
    second = asyncio.create_task(monitor_events._broadcast("message.sent", {"session_id": "s", "timestamp": 2}))
    await asyncio.gather(first, second)

    assert broadcast_ids == [1, 2]
