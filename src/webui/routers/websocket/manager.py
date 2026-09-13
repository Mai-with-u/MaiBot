"""统一 WebSocket 连接管理器。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket
from starlette.websockets import WebSocketState

import asyncio

from src.common.logger import get_logger

logger = get_logger("webui.websocket")


# 单个连接出站发送队列的上限。慢速或僵死的客户端（TCP 已连但不读取）会导致
# 广播消息在其队列中无限积压，超过上限即判定连接异常并主动断开，
# 由客户端重连后重新拉取状态。
SEND_QUEUE_MAX_SIZE = 512


@dataclass
class WebSocketConnection:
    """统一 WebSocket 连接上下文。"""

    connection_id: str
    websocket: WebSocket
    subscriptions: Set[str] = field(default_factory=set)
    chat_sessions: Dict[str, str] = field(default_factory=dict)
    send_queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = field(
        default_factory=lambda: asyncio.Queue(maxsize=SEND_QUEUE_MAX_SIZE)
    )
    sender_task: Optional["asyncio.Task[None]"] = None


class UnifiedWebSocketManager:
    """统一 WebSocket 连接管理器。"""

    def __init__(self) -> None:
        """初始化统一 WebSocket 连接管理器。"""
        self.connections: Dict[str, WebSocketConnection] = {}

    def _build_subscription_key(self, domain: str, topic: str) -> str:
        """构建订阅索引键。

        Args:
            domain: 业务域名称。
            topic: 主题名称。

        Returns:
            str: 订阅索引键。
        """
        return f"{domain}:{topic}"

    async def _close_websocket(self, connection: WebSocketConnection) -> None:
        """显式关闭底层 WebSocket 连接。

        某些异常退出路径只会执行清理逻辑，但不会自动向客户端发送关闭帧。
        这里主动关闭底层连接，确保浏览器能够及时感知断线并触发重连。

        Args:
            connection: 目标连接上下文。
        """
        websocket = connection.websocket
        if (
            websocket.client_state == WebSocketState.DISCONNECTED
            or websocket.application_state == WebSocketState.DISCONNECTED
        ):
            return

        await websocket.close()

    async def _sender_loop(self, connection: WebSocketConnection) -> None:
        """串行发送指定连接的出站消息。

        Args:
            connection: 目标连接上下文。
        """
        try:
            while True:
                message = await connection.send_queue.get()
                if message is None:
                    return
                await connection.websocket.send_json(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"统一 WebSocket 发送失败: connection={connection.connection_id}, error={exc}")

    async def connect(self, connection_id: str, websocket: WebSocket) -> WebSocketConnection:
        """注册一个新的物理 WebSocket 连接。

        Args:
            connection_id: 连接 ID。
            websocket: FastAPI WebSocket 对象。

        Returns:
            WebSocketConnection: 新建的连接上下文。
        """
        await websocket.accept()
        connection = WebSocketConnection(connection_id=connection_id, websocket=websocket)
        connection.sender_task = asyncio.create_task(self._sender_loop(connection))
        self.connections[connection_id] = connection
        return connection

    async def disconnect(self, connection_id: str) -> None:
        """断开并清理指定连接。

        Args:
            connection_id: 连接 ID。
        """
        connection = self.connections.pop(connection_id, None)
        if connection is None:
            return

        try:
            await self._close_websocket(connection)
        except Exception as exc:
            logger.debug(f"关闭统一 WebSocket 底层连接时出现异常: connection={connection_id}, error={exc}")

        try:
            # 队列可能已被慢速客户端填满，此时无法再投递退出哨兵，
            # 直接取消发送协程以避免清理流程被阻塞。
            connection.send_queue.put_nowait(None)
        except asyncio.QueueFull:
            if connection.sender_task is not None:
                connection.sender_task.cancel()

        if connection.sender_task is not None:
            try:
                await connection.sender_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug(f"等待发送协程退出时出现异常: connection={connection_id}, error={exc}")

    def get_connection(self, connection_id: str) -> Optional[WebSocketConnection]:
        """获取指定连接上下文。

        Args:
            connection_id: 连接 ID。

        Returns:
            Optional[WebSocketConnection]: 找到时返回连接上下文。
        """
        return self.connections.get(connection_id)

    def register_chat_session(self, connection_id: str, client_session_id: str, session_id: str) -> None:
        """登记连接下的逻辑聊天会话。

        Args:
            connection_id: 连接 ID。
            client_session_id: 前端会话 ID。
            session_id: 内部会话 ID。
        """
        connection = self.connections.get(connection_id)
        if connection is None:
            return
        connection.chat_sessions[client_session_id] = session_id

    def unregister_chat_session(self, connection_id: str, client_session_id: str) -> None:
        """移除连接下的逻辑聊天会话登记。

        Args:
            connection_id: 连接 ID。
            client_session_id: 前端会话 ID。
        """
        connection = self.connections.get(connection_id)
        if connection is None:
            return
        connection.chat_sessions.pop(client_session_id, None)

    def get_chat_session_id(self, connection_id: str, client_session_id: str) -> Optional[str]:
        """查询连接下的内部聊天会话 ID。

        Args:
            connection_id: 连接 ID。
            client_session_id: 前端会话 ID。

        Returns:
            Optional[str]: 找到时返回内部会话 ID。
        """
        connection = self.connections.get(connection_id)
        if connection is None:
            return None
        return connection.chat_sessions.get(client_session_id)

    def subscribe(self, connection_id: str, domain: str, topic: str) -> None:
        """登记连接的主题订阅。

        Args:
            connection_id: 连接 ID。
            domain: 业务域名称。
            topic: 主题名称。
        """
        connection = self.connections.get(connection_id)
        if connection is None:
            return
        connection.subscriptions.add(self._build_subscription_key(domain, topic))

    def unsubscribe(self, connection_id: str, domain: str, topic: str) -> None:
        """移除连接的主题订阅。

        Args:
            connection_id: 连接 ID。
            domain: 业务域名称。
            topic: 主题名称。
        """
        connection = self.connections.get(connection_id)
        if connection is None:
            return
        connection.subscriptions.discard(self._build_subscription_key(domain, topic))

    def is_subscribed(self, connection_id: str, domain: str, topic: str) -> bool:
        """判断连接是否订阅了指定主题。

        Args:
            connection_id: 连接 ID。
            domain: 业务域名称。
            topic: 主题名称。

        Returns:
            bool: 已订阅时返回 ``True``。
        """
        connection = self.connections.get(connection_id)
        if connection is None:
            return False
        return self._build_subscription_key(domain, topic) in connection.subscriptions

    def has_topic_subscribers(self, domain: str, topic: str) -> bool:
        """判断是否存在任意连接订阅了指定主题。"""

        subscription_key = self._build_subscription_key(domain, topic)
        return any(
            subscription_key in connection.subscriptions for connection in self.connections.values()
        )

    @staticmethod
    def _try_put_nowait(connection: WebSocketConnection, message: Dict[str, Any]) -> bool:
        """尝试立即把消息放入发送队列，队列已满时返回 ``False``。"""

        try:
            connection.send_queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            return False

    async def enqueue(self, connection_id: str, message: Dict[str, Any]) -> None:
        """向指定连接的发送队列压入消息。

        队列有界：投递失败说明客户端消费过慢，将调度断开该连接。

        Args:
            connection_id: 连接 ID。
            message: 待发送的消息。
        """
        connection = self.connections.get(connection_id)
        if connection is None:
            return

        sender_task = connection.sender_task
        target_loop = sender_task.get_loop() if sender_task is not None else None
        if target_loop is None or target_loop.is_closed() or not target_loop.is_running():
            return

        current_loop = asyncio.get_running_loop()
        if target_loop is current_loop:
            if not self._try_put_nowait(connection, message):
                logger.warning(f"统一 WebSocket 发送队列已满，主动断开慢速连接: connection={connection_id}")
                await self.disconnect(connection_id)
            return

        # WebUI 运行在独立线程事件循环中，跨 loop 投递必须回到连接所属 loop 执行。
        async def _put_on_target_loop() -> bool:
            return self._try_put_nowait(connection, message)

        try:
            future = asyncio.run_coroutine_threadsafe(_put_on_target_loop(), target_loop)
        except RuntimeError as exc:
            logger.debug(f"统一 WebSocket 跨线程投递失败: connection={connection_id}, error={exc}")
            return

        try:
            delivered = await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as exc:
            logger.debug(f"统一 WebSocket 等待跨线程投递时出现异常: connection={connection_id}, error={exc}")
            return

        if not delivered:
            logger.warning(f"统一 WebSocket 发送队列已满，主动断开慢速连接: connection={connection_id}")
            # 断开清理必须回到连接所属的事件循环执行。
            try:
                disconnect_future = asyncio.run_coroutine_threadsafe(self.disconnect(connection_id), target_loop)
            except RuntimeError as exc:
                logger.debug(f"统一 WebSocket 调度断开慢速连接失败: connection={connection_id}, error={exc}")
                return
            disconnect_future.add_done_callback(
                lambda future: (
                    None
                    if future.exception() is None
                    else logger.debug(
                        f"统一 WebSocket 断开慢速连接时出现异常: connection={connection_id}, error={future.exception()}"
                    )
                )
            )

    async def send_response(
        self,
        connection_id: str,
        request_id: Optional[str],
        ok: bool,
        data: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        """发送统一响应消息。

        Args:
            connection_id: 连接 ID。
            request_id: 请求 ID。
            ok: 请求是否成功。
            data: 成功响应数据。
            error: 失败响应数据。
        """
        response_message: Dict[str, Any] = {
            "op": "response",
            "id": request_id,
            "ok": ok,
        }
        if data is not None:
            response_message["data"] = data
        if error is not None:
            response_message["error"] = error
        await self.enqueue(connection_id, response_message)

    async def send_event(
        self,
        connection_id: str,
        domain: str,
        event: str,
        data: Dict[str, Any],
        session: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> None:
        """发送统一事件消息。

        Args:
            connection_id: 连接 ID。
            domain: 业务域名称。
            event: 事件名称。
            data: 事件数据。
            session: 可选的逻辑会话 ID。
            topic: 可选的主题名称。
        """
        event_message: Dict[str, Any] = {
            "op": "event",
            "domain": domain,
            "event": event,
            "data": data,
        }
        if session is not None:
            event_message["session"] = session
        if topic is not None:
            event_message["topic"] = topic
        await self.enqueue(connection_id, event_message)

    async def send_pong(self, connection_id: str, timestamp: float) -> None:
        """发送心跳响应。

        Args:
            connection_id: 连接 ID。
            timestamp: 当前时间戳。
        """
        await self.enqueue(
            connection_id,
            {
                "op": "pong",
                "ts": timestamp,
            },
        )

    async def broadcast_to_topic(self, domain: str, topic: str, event: str, data: Dict[str, Any]) -> None:
        """向订阅指定主题的全部连接广播事件。

        Args:
            domain: 业务域名称。
            topic: 主题名称。
            event: 事件名称。
            data: 事件数据。
        """
        subscription_key = self._build_subscription_key(domain, topic)
        for connection in list(self.connections.values()):
            if subscription_key in connection.subscriptions:
                await self.send_event(
                    connection.connection_id,
                    domain=domain,
                    event=event,
                    data=data,
                    topic=topic,
                )


websocket_manager = UnifiedWebSocketManager()
