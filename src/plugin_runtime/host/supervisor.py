from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING

import asyncio


from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_runtime.transport.factory import create_transport_server
from src.plugin_runtime.protocol.envelope import (
    BootstrapPluginPayload,
    ConfigUpdatedPayload,
    Envelope,
    HealthPayload,
    LogBatchPayload,
    RegisterPluginPayload,
    RunnerReadyPayload,
    ShutdownPayload,
)

from .authorization import AuthorizationManager
from .capability_service import CapabilityService
from .rpc_server import RPCServer
from .logger_bridge import RunnerLogBridge
from .component_registry import ComponentRegistry
from .event_dispatcher import EventDispatcher
from .hook_dispatcher import HookDispatcher
from .message_gateway import MessageGateway
from .message_utils import PluginMessageUtils

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

logger = get_logger("plugin_runtime.host.runner_manager")


class PluginRunnerSupervisor:
    """插件的Runner管理器，负责管理Runner的生命周期"""

    def __init__(
        self,
        plugin_dirs: Optional[List[Path]] = None,
        socket_path: Optional[str] = None,
        health_check_interval_sec: Optional[float] = None,
        max_restart_attempts: Optional[int] = None,
        runner_spawn_timeout_sec: Optional[float] = None,
    ):
        _cfg = global_config.plugin_runtime
        self._plugin_dirs: List[Path] = plugin_dirs or []
        self._health_interval = health_check_interval_sec or _cfg.health_check_interval_sec or 30.0
        self._runner_spawn_timeout = runner_spawn_timeout_sec or _cfg.runner_spawn_timeout_sec or 30.0

        # 基础设施
        self._transport = create_transport_server(socket_path=socket_path)
        self._authorization = AuthorizationManager()
        self._capability_service = CapabilityService(self._authorization)
        self._component_registry = ComponentRegistry()
        self._event_dispatcher = EventDispatcher(self._component_registry)
        self._hook_dispatcher = HookDispatcher(self._component_registry)
        self._message_gateway = MessageGateway(self._component_registry)

        # 编解码和服务器
        from src.plugin_runtime.protocol.codec import MsgPackCodec

        codec = MsgPackCodec()
        self._rpc_server = RPCServer(transport=self._transport, codec=codec)

        # Runner 子进程
        self._runner_process: Optional[asyncio.subprocess.Process] = None
        self._max_restart_attempts: int = max_restart_attempts or _cfg.max_restart_attempts or 3
        self._restart_count: int = 0

        # 已注册的插件组件信息
        self._registered_plugins: Dict[str, RegisterPluginPayload] = {}
        self._runner_ready_events: asyncio.Event = asyncio.Event()
        self._runner_ready_payloads: RunnerReadyPayload = RunnerReadyPayload()

        # 后台任务
        self._health_task: Optional[asyncio.Task] = None
        # Runner stderr 流排空任务（仅保留 stderr，用于 IPC 建立前的启动日志倒空、致命错误输出等场景）
        self._stderr_drain_task: Optional[asyncio.Task] = None
        self._running = False

        # Runner 日志桥（将 Runner 上报的批量日志重放到主进程 Logger）
        self._log_bridge: RunnerLogBridge = RunnerLogBridge()

        # 注册内部 RPC 方法
        self._register_internal_methods()  # TODO: 完成内部方法注册

    @property
    def authorization_manager(self) -> AuthorizationManager:
        return self._authorization

    @property
    def capability_service(self) -> CapabilityService:
        return self._capability_service

    @property
    def component_registry(self) -> ComponentRegistry:
        return self._component_registry

    @property
    def event_dispatcher(self) -> EventDispatcher:
        return self._event_dispatcher

    @property
    def hook_dispatcher(self) -> HookDispatcher:
        return self._hook_dispatcher

    @property
    def message_gateway(self) -> MessageGateway:
        return self._message_gateway

    @property
    def rpc_server(self) -> RPCServer:
        return self._rpc_server

    async def dispatch_event(
        self,
        event_type: str,
        message: Optional["SessionMessage"] = None,
        extra_args: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional["SessionMessage"]]:
        """分发事件到所有对应 handler 的快捷方法。"""
        return await self._event_dispatcher.dispatch_event(event_type, self, message, extra_args)

    async def dispatch_hook(self, stage: str, **kwargs):
        """分发Hook事件到所有对应 handler 的快捷方法。"""
        return await self._hook_dispatcher.hook_dispatch(stage, self, **kwargs)

    async def send_message_to_external(
        self,
        internal_message: "SessionMessage",
        *,
        enabled_only: bool = True,
        save_to_db: bool = True,
    ) -> bool:
        """发送系统内部消息到外部平台的快捷方法。"""
        return await self._message_gateway.send_message_to_external(
            internal_message, self, enabled_only=enabled_only, save_to_db=save_to_db
        )

    async def start(self) -> None:
        """启动 Supervisor

        1. 启动 RPC Server
        2. 拉起 Runner 子进程
        3. 启动健康检查
        """
        self._running = True

        # 启动 RPC Server
        await self._rpc_server.start()
        # 拉起 Runner 进程
        await self._spawn_runner()

        # 等待 Runner 完成连接和初始化，避免 start() 返回时 Runner 尚未就绪
        try:
            await self._wait_for_runner_connection(timeout_sec=self._runner_spawn_timeout)
            await self._wait_for_runner_ready(timeout_sec=self._runner_spawn_timeout)
        except TimeoutError:
            if not self._rpc_server.is_connected:
                logger.warning(f"Runner 未在 {self._runner_spawn_timeout}s 内完成连接，后续操作可能失败")
            else:
                logger.warning(f"Runner 未在 {self._runner_spawn_timeout}s 内完成初始化，后续操作可能失败")

        # 启动健康检查
        self._health_task = asyncio.create_task(self._health_check_loop())

        logger.info("PluginSupervisor 已启动")

    async def stop(self) -> None:
        """停止 Supervisor"""
        self._running = False

        # 停止组件
        await self._event_dispatcher.stop()
        await self._hook_dispatcher.stop()

        # 停止健康检查
        if self._health_task:
            self._health_task.cancel()
            self._health_task = None

        # 优雅关停 Runner
        await self._shutdown_runner()

        # 停止 RPC Server
        await self._rpc_server.stop()

        logger.info("PluginSupervisor 已停止")

    async def invoke_plugin(
        self,
        method: str,
        plugin_id: str,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Envelope:
        """调用插件组件

        由主进程业务逻辑调用，通过 RPC 转发给 Runner。
        """
        return await self._rpc_server.send_request(
            method,
            plugin_id,
            {"component_name": component_name, "args": args or {}},
            timeout_ms,
        )

    async def reload_plugin(self, plugin_id: str, reason: str = "manual") -> bool:
        raise NotImplementedError("等待SDK完成")  # TODO: 完成对应的调用和请求逻辑

    async def _wait_for_runner_connection(self, timeout_sec: float) -> None:
        """等待 Runner 连接上 RPC Server"""

        async def wait_for_connection():
            while self._running and not self._rpc_server.is_connected:
                await asyncio.sleep(0.1)

        try:
            await asyncio.wait_for(wait_for_connection(), timeout=timeout_sec)
            logger.info("Runner 已连接到 RPC Server")
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"等待 Runner 连接超时（{timeout_sec}s）") from e

    async def _wait_for_runner_ready(self, timeout_sec: float = 30.0) -> RunnerReadyPayload:
        """等待 Runner 完成初始化并上报就绪"""

        try:
            await asyncio.wait_for(self._runner_ready_events.wait(), timeout=timeout_sec)
            logger.info("Runner 已完成初始化并上报就绪")
            return self._runner_ready_payloads
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"等待 Runner 就绪超时（{timeout_sec}s）") from e
