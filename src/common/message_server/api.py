"""maim_message 协议仅挂载到宿主 IPC；不创建任何 TCP 监听器。"""

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket
from maim_message import MessageConverter, MessageServer
from maim_message.server import ServerConfig, WebSocketServer
from maim_message.server_ws_connection import ServerNetworkDriver

import asyncio

from src.common.logger import adopt_library_logger, get_logger

adopt_library_logger("maim_message", handler_names={"maim_message_default_handler"})
message_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class MountedNetworkDriver(ServerNetworkDriver):
    """复用 0.6.8 的协议处理和回执，网络生命周期由宿主 IPC 接管。"""

    def __init__(self, allowed_keys: List[str], **kwargs: Any) -> None:
        self.allowed_keys = allowed_keys
        super().__init__(**kwargs)

    def _setup_routes(self) -> None:
        # 不使用库中打印 API Key 的调试路由，协议处理仍交给原驱动。
        @self.app.websocket(self.path)
        async def connect(websocket: WebSocket, api_key: Optional[str] = None, platform: Optional[str] = None):
            key = api_key or websocket.headers.get("x-apikey", "")
            if not key or key not in self.allowed_keys:
                await websocket.close(code=1008, reason="API Key 无效")
                return
            await self._handle_connection(websocket, query_api_key=api_key, query_platform=platform)

    async def start(self, event_queue: asyncio.Queue) -> None:
        self.event_queue = event_queue
        self.main_loop = asyncio.get_running_loop()
        self.running = True

    async def stop(self) -> None:
        self.running = False
        for connection_id in list(self.active_connections):
            await self._cleanup_connection(connection_id)
        if self.message_cache is not None:
            await self.message_cache.stop()
        self.event_queue = None
        self.main_loop = None


class HostedMessageServer(MessageServer):
    def __init__(self) -> None:
        from src.config.config import global_config

        config = global_config.maim_message
        super().__init__(app=message_app, path="/ws", enable_token=True, custom_logger=get_logger("maim_message"))
        for token in config.auth_token:
            if token.strip():
                self.add_valid_token(token)
        self.platform_map: Dict[str, str] = {}
        self.extra_server: Optional[WebSocketServer] = None
        if config.enable_api_server:
            # 新协议仍使用独立 Key 白名单；空名单不能开放到宿主公开端口。
            async def authenticate(metadata: Dict[str, Any]) -> bool:
                key = metadata.get("api_key")
                return bool(key and key in config.api_server_allowed_api_keys)

            async def receive(message, metadata: Dict[str, Any]) -> None:
                # 保留新协议到正式消息处理链的转换，并按认证身份登记回复路由。
                legacy = MessageConverter.from_api_receive(message).to_dict()
                platform = legacy["message_info"]["platform"]
                self.platform_map[platform] = metadata["api_key"]
                legacy.setdefault("raw_message", None)
                await self.process_message(legacy)

            async def echo(payload: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
                # message_id_echo 回到 main 注册的自定义消息处理器。
                for handler in self.custom_message_handlers.get("message_id_echo", []):
                    await handler(payload)

            server_config = ServerConfig(path="/api", custom_logger=get_logger("maim_message_api_server"))
            server_config.on_auth = authenticate
            server_config.on_message = receive
            server_config.register_custom_handler("message_id_echo", echo)
            self.extra_server = WebSocketServer(config=server_config)
            self.extra_server.network_driver = MountedNetworkDriver(
                allowed_keys=config.api_server_allowed_api_keys,
                host="127.0.0.1", port=1, path="/api", custom_logger=server_config.custom_logger,
            )
            message_app.include_router(self.extra_server.network_driver.app.router)

    async def run(self) -> None:
        if self.extra_server is not None:
            await self.extra_server.start()
        # 传入外部 app 后库仅激活协议，不调用 Uvicorn TCP 监听。
        await super().run()

    async def stop(self) -> None:
        if self.extra_server is not None:
            await self.extra_server.stop()
        await super().stop()


global_api: Optional[HostedMessageServer] = None


def get_global_api() -> HostedMessageServer:
    from src.config.config import global_config

    if not global_config.maim_message.enabled:
        raise RuntimeError("maim_message 服务已关闭，修改配置后需重启")
    global global_api
    if global_api is None:
        global_api = HostedMessageServer()
    return global_api
