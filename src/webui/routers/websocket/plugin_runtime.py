"""插件运行状态变更通知，连接和重连后由客户端获取完整列表快照。"""

from typing import Callable, Dict, Optional, Tuple

import asyncio

from src.plugin_runtime.integration import get_plugin_runtime_manager

from .manager import websocket_manager

_subscriptions: Dict[str, Tuple[asyncio.Task[None], Callable[[], None]]] = {}


def stop_plugin_runtime_subscription(connection_id: str) -> None:
    subscription = _subscriptions.pop(connection_id, None)
    if subscription is not None:
        task, unsubscribe = subscription
        unsubscribe()
        task.cancel()


async def subscribe_plugin_runtime(connection_id: str, request_id: Optional[str]) -> None:
    stop_plugin_runtime_subscription(connection_id)
    manager = get_plugin_runtime_manager()
    loop = asyncio.get_running_loop()
    changed = asyncio.Event()
    closed = False

    def on_changed() -> None:
        # 主循环产生通知，WebUI 在独立线程的循环发送；不跨线程直接操作 Event。
        if not closed and not loop.is_closed():
            loop.call_soon_threadsafe(changed.set)

    unsubscribe = manager.subscribe_status_changes(on_changed)

    async def send_changes() -> None:
        nonlocal closed
        try:
            changed.set()
            while True:
                await changed.wait()
                changed.clear()
                await websocket_manager.send_event(
                    connection_id, domain="plugin_runtime", topic="main", event="changed", data={},
                )
        finally:
            closed = True
            unsubscribe()

    websocket_manager.subscribe(connection_id, domain="plugin_runtime", topic="main")
    _subscriptions[connection_id] = (asyncio.create_task(send_changes()), unsubscribe)
    await websocket_manager.send_response(
        connection_id, request_id=request_id, ok=True, data={"domain": "plugin_runtime", "topic": "main"},
    )
