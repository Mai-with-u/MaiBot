"""NapCat 正向 WebSocket 传输层。"""

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional, Set, cast
from uuid import uuid4

import asyncio
import contextlib
import json

from napcat_adapter.config import NapCatServerConfig

if TYPE_CHECKING:
    from aiohttp import ClientWebSocketResponse as AiohttpClientWebSocketResponse

try:
    from aiohttp import ClientSession, ClientTimeout, WSMsgType

    AIOHTTP_AVAILABLE = True
except ImportError:
    ClientSession = cast(Any, None)
    ClientTimeout = cast(Any, None)
    WSMsgType = cast(Any, None)
    AIOHTTP_AVAILABLE = False

if not TYPE_CHECKING:
    AiohttpClientWebSocketResponse = Any


class NapCatTransportClient:
    """NapCat 正向 WebSocket 客户端。"""

    def __init__(
        self,
        logger: Any,
        on_connection_opened: Callable[[], Awaitable[None]],
        on_connection_closed: Callable[[], Awaitable[None]],
        on_payload: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        """初始化传输层客户端。

        Args:
            logger: 插件日志对象。
            on_connection_opened: 连接建立后的异步回调。
            on_connection_closed: 连接断开后的异步回调。
            on_payload: 收到非 echo 载荷后的异步回调。
        """
        self._logger = logger
        self._on_connection_opened = on_connection_opened
        self._on_connection_closed = on_connection_closed
        self._on_payload = on_payload
        self._server_config: Optional[NapCatServerConfig] = None
        self._connection_task: Optional[asyncio.Task[None]] = None
        self._pending_actions: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._background_tasks: Set[asyncio.Task[Any]] = set()
        self._send_lock = asyncio.Lock()
        self._ws: Optional[AiohttpClientWebSocketResponse] = None
        self._stop_requested: bool = False
        self._connection_active: bool = False

    @classmethod
    def is_available(cls) -> bool:
        """判断当前环境是否安装了传输层依赖。

        Returns:
            bool: 若已安装 ``aiohttp``，则返回 ``True``。
        """
        return AIOHTTP_AVAILABLE

    def configure(self, server_config: NapCatServerConfig) -> None:
        """更新当前传输层使用的 NapCat 服务端配置。

        Args:
            server_config: 最新生效的 NapCat 服务端配置。
        """
        self._server_config = server_config

    async def start(self) -> None:
        """启动 NapCat 正向 WebSocket 连接循环。

        Raises:
            RuntimeError: 当缺少配置或依赖时抛出。
        """
        if not self.is_available():
            raise RuntimeError("NapCat 适配器依赖 aiohttp，但当前环境未安装该依赖")
        if self._server_config is None:
            raise RuntimeError("NapCat 适配器尚未配置 napcat_server")
        if self._connection_task is not None and not self._connection_task.done():
            return

        self._stop_requested = False
        self._connection_task = asyncio.create_task(self._connection_loop(), name="napcat_adapter.connection")

    async def stop(self) -> None:
        """停止当前连接并清理所有后台任务。"""
        self._stop_requested = True
        connection_task = self._connection_task
        self._connection_task = None

        ws = self._ws
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        self._ws = None

        if connection_task is not None:
            connection_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await connection_task

        await self._cancel_background_tasks()
        await self._notify_connection_closed()
        self._fail_pending_actions("NapCat connection closed")

    async def call_action(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发送 OneBot 动作并等待对应的 echo 响应。

        Args:
            action_name: OneBot 动作名称。
            params: 动作参数。

        Returns:
            Dict[str, Any]: NapCat 返回的原始响应字典。

        Raises:
            RuntimeError: 当连接不可用时抛出。
        """
        ws = self._ws
        server_config = self._server_config
        if ws is None or ws.closed or server_config is None:
            raise RuntimeError("NapCat is not connected")

        echo_id = uuid4().hex
        loop = asyncio.get_running_loop()
        response_future: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending_actions[echo_id] = response_future

        request_payload = {"action": action_name, "params": params, "echo": echo_id}
        try:
            async with self._send_lock:
                await ws.send_str(json.dumps(request_payload, ensure_ascii=False))
            return await asyncio.wait_for(response_future, timeout=server_config.action_timeout_sec)
        finally:
            self._pending_actions.pop(echo_id, None)

    async def _connection_loop(self) -> None:
        """维护单个 WebSocket 连接，并在断开后按配置重连。"""
        assert ClientSession is not None
        assert ClientTimeout is not None

        while not self._stop_requested:
            server_config = self._server_config
            if server_config is None:
                return

            ws_url = server_config.build_ws_url()
            timeout = ClientTimeout(total=None, connect=10)

            try:
                async with ClientSession(headers=self._build_headers(server_config), timeout=timeout) as session:
                    async with session.ws_connect(ws_url, heartbeat=server_config.heartbeat_interval or None) as ws:
                        self._ws = ws
                        self._logger.info(f"NapCat 适配器已连接: {ws_url}")
                        await self._receive_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(f"NapCat 适配器连接失败: {exc}")
            finally:
                self._ws = None
                await self._notify_connection_closed()
                self._fail_pending_actions("NapCat connection interrupted")

            if self._stop_requested:
                break

            await asyncio.sleep(server_config.reconnect_delay_sec)

    async def _receive_loop(self, ws: AiohttpClientWebSocketResponse) -> None:
        """持续消费 WebSocket 消息并分发处理。

        Args:
            ws: 当前活跃的 WebSocket 连接对象。
        """
        assert WSMsgType is not None

        bootstrap_task = self._create_background_task(
            self._notify_connection_opened(),
            "napcat_adapter.bootstrap",
        )
        try:
            async for ws_message in ws:
                if ws_message.type != WSMsgType.TEXT:
                    if ws_message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                        break
                    continue

                payload = self._parse_json_message(ws_message.data)
                if payload is None:
                    continue

                if echo_id := str(payload.get("echo") or "").strip():
                    self._resolve_pending_action(echo_id, payload)
                    continue

                self._create_background_task(self._on_payload(payload), "napcat_adapter.payload")
        finally:
            if bootstrap_task is not None and not bootstrap_task.done():
                bootstrap_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bootstrap_task

    def _create_background_task(self, coroutine: Awaitable[Any], name: str) -> asyncio.Task[Any]:
        """创建并跟踪一个后台任务。

        Args:
            coroutine: 待执行的协程对象。
            name: 任务名。

        Returns:
            asyncio.Task[Any]: 已创建的后台任务。
        """
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._handle_background_task_completion)
        return task

    def _handle_background_task_completion(self, task: asyncio.Task[Any]) -> None:
        """处理后台任务结束后的清理与异常记录。

        Args:
            task: 已结束的后台任务。
        """
        self._background_tasks.discard(task)
        if task.cancelled():
            return

        exception = task.exception()
        if exception is not None:
            self._logger.error(f"NapCat 适配器后台任务异常: {exception}", exc_info=True)

    async def _cancel_background_tasks(self) -> None:
        """取消所有仍在运行的后台任务。"""
        background_tasks = list(self._background_tasks)
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*background_tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def _notify_connection_opened(self) -> None:
        """在连接建立后触发上层回调。"""
        if self._connection_active:
            return

        self._connection_active = True
        try:
            await self._on_connection_opened()
        except Exception as exc:
            self._logger.warning(f"NapCat 适配器连接建立回调失败: {exc}")

    async def _notify_connection_closed(self) -> None:
        """在连接断开后触发上层回调。"""
        if not self._connection_active:
            return

        self._connection_active = False
        try:
            await self._on_connection_closed()
        except Exception as exc:
            self._logger.warning(f"NapCat 适配器断连回调失败: {exc}")

    def _resolve_pending_action(self, echo_id: str, payload: Dict[str, Any]) -> None:
        """解析等待中的动作响应。

        Args:
            echo_id: 动作请求对应的 echo 标识。
            payload: NapCat 返回的响应载荷。
        """
        response_future = self._pending_actions.get(echo_id)
        if response_future is None or response_future.done():
            return
        response_future.set_result(payload)

    def _fail_pending_actions(self, error_message: str) -> None:
        """让所有等待中的动作以异常方式结束。

        Args:
            error_message: 写入异常中的错误信息。
        """
        for response_future in self._pending_actions.values():
            if not response_future.done():
                response_future.set_exception(RuntimeError(error_message))
        self._pending_actions.clear()

    def _build_headers(self, server_config: NapCatServerConfig) -> Dict[str, str]:
        """构造连接 NapCat 所需的请求头。

        Args:
            server_config: 当前生效的 NapCat 服务端配置。

        Returns:
            Dict[str, str]: WebSocket 握手请求头。
        """
        return {"Authorization": f"Bearer {server_config.token}"} if server_config.token else {}

    def _parse_json_message(self, data: Any) -> Optional[Dict[str, Any]]:
        """解析 WebSocket 文本消息中的 JSON 数据。

        Args:
            data: WebSocket 收到的原始文本数据。

        Returns:
            Optional[Dict[str, Any]]: 成功时返回字典，失败时返回 ``None``。
        """
        try:
            payload = json.loads(str(data))
        except Exception as exc:
            self._logger.warning(f"NapCat 适配器解析 JSON 载荷失败: {exc}")
            return None

        return payload if isinstance(payload, dict) else None
