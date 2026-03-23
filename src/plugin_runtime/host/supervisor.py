from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import asyncio
import contextlib
import json
import os
import sys

from src.common.logger import get_logger
from src.config.config import config_manager, global_config
from src.platform_io import DriverKind, InboundMessageEnvelope, RouteBinding, RouteKey, get_platform_io_manager
from src.platform_io.drivers import PluginPlatformDriver
from src.platform_io.route_key_factory import RouteKeyFactory
from src.plugin_runtime import (
    ENV_EXTERNAL_PLUGIN_IDS,
    ENV_GLOBAL_CONFIG_SNAPSHOT,
    ENV_HOST_VERSION,
    ENV_IPC_ADDRESS,
    ENV_PLUGIN_DIRS,
    ENV_SESSION_TOKEN,
)
from src.plugin_runtime.protocol.envelope import (
    BootstrapPluginPayload,
    ConfigReloadScope,
    ConfigUpdatedPayload,
    Envelope,
    HealthPayload,
    MessageGatewayStateUpdatePayload,
    MessageGatewayStateUpdateResultPayload,
    PROTOCOL_VERSION,
    ReceiveExternalMessageResultPayload,
    RegisterPluginPayload,
    ReloadPluginResultPayload,
    RouteMessagePayload,
    RunnerReadyPayload,
    ShutdownPayload,
    UnregisterPluginPayload,
)
from src.plugin_runtime.protocol.codec import MsgPackCodec
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.transport.factory import create_transport_server

from .authorization import AuthorizationManager
from .api_registry import APIRegistry
from .capability_service import CapabilityService
from .component_registry import ComponentRegistry
from .event_dispatcher import EventDispatcher
from .hook_dispatcher import HookDispatcher
from .logger_bridge import RunnerLogBridge
from .message_gateway import MessageGateway
from .rpc_server import RPCServer

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

logger = get_logger("plugin_runtime.host.runner_manager")

@dataclass(slots=True)
class _MessageGatewayRuntimeState:
    """保存消息网关当前的运行时连接状态。"""

    ready: bool = False
    platform: Optional[str] = None
    account_id: Optional[str] = None
    scope: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class PluginRunnerSupervisor:
    """插件 Runner 监督器。

    负责 Host 侧与单个 Runner 子进程之间的生命周期、内部 RPC、
    健康检查和插件级重载协调。
    """

    def __init__(
        self,
        plugin_dirs: Optional[List[Path]] = None,
        socket_path: Optional[str] = None,
        health_check_interval_sec: Optional[float] = None,
        max_restart_attempts: Optional[int] = None,
        runner_spawn_timeout_sec: Optional[float] = None,
    ) -> None:
        """初始化 Supervisor。

        Args:
            plugin_dirs: 由当前 Runner 负责加载的插件目录列表。
            socket_path: 自定义 IPC 地址；留空时由传输层自动生成。
            health_check_interval_sec: 健康检查间隔，单位秒。
            max_restart_attempts: 自动重启 Runner 的最大次数。
            runner_spawn_timeout_sec: 等待 Runner 建连并就绪的超时时间，单位秒。
        """
        runtime_config = global_config.plugin_runtime
        self._plugin_dirs: List[Path] = plugin_dirs or []
        self._health_interval: float = health_check_interval_sec or runtime_config.health_check_interval_sec or 30.0
        self._runner_spawn_timeout: float = (
            runner_spawn_timeout_sec or runtime_config.runner_spawn_timeout_sec or 30.0
        )
        self._max_restart_attempts: int = max_restart_attempts or runtime_config.max_restart_attempts or 3

        self._transport = create_transport_server(socket_path=socket_path)
        self._authorization = AuthorizationManager()
        self._capability_service = CapabilityService(self._authorization)
        self._api_registry = APIRegistry()
        self._component_registry = ComponentRegistry()
        self._event_dispatcher = EventDispatcher(self._component_registry)
        self._hook_dispatcher = HookDispatcher(self._component_registry)
        self._message_gateway = MessageGateway(self._component_registry)
        self._log_bridge = RunnerLogBridge()

        codec = MsgPackCodec()
        self._rpc_server = RPCServer(transport=self._transport, codec=codec)

        self._runner_process: Optional[asyncio.subprocess.Process] = None
        self._registered_plugins: Dict[str, RegisterPluginPayload] = {}
        self._message_gateway_states: Dict[str, Dict[str, _MessageGatewayRuntimeState]] = {}
        self._external_available_plugins: Dict[str, str] = {}
        self._runner_ready_events: asyncio.Event = asyncio.Event()
        self._runner_ready_payloads: RunnerReadyPayload = RunnerReadyPayload()
        self._health_task: Optional[asyncio.Task[None]] = None
        self._stderr_drain_task: Optional[asyncio.Task[None]] = None
        self._restart_count: int = 0
        self._running: bool = False

        self._register_internal_methods()

    @property
    def authorization_manager(self) -> AuthorizationManager:
        """返回授权管理器。"""
        return self._authorization

    @property
    def capability_service(self) -> CapabilityService:
        """返回能力服务。"""
        return self._capability_service

    @property
    def api_registry(self) -> APIRegistry:
        """返回 API 专用注册表。"""
        return self._api_registry

    @property
    def component_registry(self) -> ComponentRegistry:
        """返回组件注册表。"""
        return self._component_registry

    @property
    def event_dispatcher(self) -> EventDispatcher:
        """返回事件分发器。"""
        return self._event_dispatcher

    @property
    def hook_dispatcher(self) -> HookDispatcher:
        """返回 Hook 分发器。"""
        return self._hook_dispatcher

    @property
    def message_gateway(self) -> MessageGateway:
        """返回消息网关。"""
        return self._message_gateway

    @property
    def rpc_server(self) -> RPCServer:
        """返回底层 RPC 服务端。"""
        return self._rpc_server

    def set_external_available_plugins(self, plugin_versions: Dict[str, str]) -> None:
        """设置当前 Runner 启动/重载时可视为已满足的外部依赖版本映射。

        Args:
            plugin_versions: 外部插件版本映射，键为插件 ID，值为插件版本。
        """
        self._external_available_plugins = {
            str(plugin_id or "").strip(): str(plugin_version or "").strip()
            for plugin_id, plugin_version in plugin_versions.items()
            if str(plugin_id or "").strip() and str(plugin_version or "").strip()
        }

    def get_loaded_plugin_ids(self) -> List[str]:
        """返回当前 Supervisor 已注册的插件 ID 列表。"""

        return sorted(self._registered_plugins.keys())

    def get_loaded_plugin_versions(self) -> Dict[str, str]:
        """返回当前 Supervisor 已注册插件的版本映射。

        Returns:
            Dict[str, str]: 已注册插件版本映射，键为插件 ID，值为插件版本。
        """
        return {
            plugin_id: registration.plugin_version
            for plugin_id, registration in self._registered_plugins.items()
        }

    async def dispatch_event(
        self,
        event_type: str,
        message: Optional["SessionMessage"] = None,
        extra_args: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional["SessionMessage"]]:
        """分发事件到已注册的事件处理器。

        Args:
            event_type: 事件类型。
            message: 可选的消息对象。
            extra_args: 附加参数。

        Returns:
            Tuple[bool, Optional[SessionMessage]]: 是否继续处理，以及插件可能修改后的消息。
        """
        return await self._event_dispatcher.dispatch_event(event_type, self, message, extra_args)

    async def dispatch_hook(self, stage: str, **kwargs: Any) -> Dict[str, Any]:
        """分发 Hook 到已注册的 Hook 处理器。

        Args:
            stage: Hook 阶段名称。
            **kwargs: 传递给 Hook 的关键字参数。

        Returns:
            Dict[str, Any]: 经 Hook 修改后的参数字典。
        """
        return await self._hook_dispatcher.hook_dispatch(stage, self, **kwargs)

    async def send_message_to_external(
        self,
        internal_message: "SessionMessage",
        *,
        enabled_only: bool = True,
        save_to_db: bool = True,
    ) -> bool:
        """通过插件消息网关发送外部消息。

        Args:
            internal_message: 系统内部消息对象。
            enabled_only: 是否仅使用启用的网关组件。
            save_to_db: 发送成功后是否写入数据库。

        Returns:
            bool: 是否发送成功。
        """
        return await self._message_gateway.send_message_to_external(
            internal_message,
            self,
            enabled_only=enabled_only,
            save_to_db=save_to_db,
        )

    async def start(self) -> None:
        """启动 Supervisor。"""
        if self._running:
            logger.warning("PluginRunnerSupervisor 已在运行，跳过重复启动")
            return

        self._running = True
        self._restart_count = 0
        self._clear_runner_state()

        try:
            await self._rpc_server.start()
            await self._spawn_runner()

            try:
                await self._wait_for_runner_connection(timeout_sec=self._runner_spawn_timeout)
                await self._wait_for_runner_ready(timeout_sec=self._runner_spawn_timeout)
            except TimeoutError:
                if not self._rpc_server.is_connected:
                    logger.warning("Runner 未在限定时间内完成连接，后续操作可能失败")
                else:
                    logger.warning("Runner 未在限定时间内完成初始化，后续操作可能失败")
        except Exception:
            await self._shutdown_runner(reason="startup_failed")
            await self._rpc_server.stop()
            self._clear_runner_state()
            self._running = False
            raise

        self._health_task = asyncio.create_task(self._health_check_loop(), name="PluginRunnerSupervisor.health")
        logger.info("PluginRunnerSupervisor 已启动")

    async def stop(self) -> None:
        """停止 Supervisor。"""
        if not self._running:
            return

        self._running = False

        if self._health_task is not None:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
            self._health_task = None

        await self._event_dispatcher.stop()
        await self._hook_dispatcher.stop()
        await self._shutdown_runner(reason="host_stop")
        await self._rpc_server.stop()
        self._clear_runner_state()

        logger.info("PluginRunnerSupervisor 已停止")

    async def invoke_plugin(
        self,
        method: str,
        plugin_id: str,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Envelope:
        """调用 Runner 内的插件组件。

        Args:
            method: RPC 方法名。
            plugin_id: 目标插件 ID。
            component_name: 组件名。
            args: 调用参数。
            timeout_ms: RPC 超时时间，单位毫秒。

        Returns:
            Envelope: RPC 响应信封。
        """
        return await self._rpc_server.send_request(
            method,
            plugin_id,
            {"component_name": component_name, "args": args or {}},
            timeout_ms,
        )

    async def invoke_message_gateway(
        self,
        plugin_id: str,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Envelope:
        """调用插件声明的消息网关方法。

        Args:
            plugin_id: 目标插件 ID。
            component_name: 消息网关组件名称。
            args: 传递给网关方法的关键字参数。
            timeout_ms: RPC 超时时间，单位毫秒。

        Returns:
            Envelope: Runner 返回的响应信封。
        """

        return await self.invoke_plugin(
            method="plugin.invoke_message_gateway",
            plugin_id=plugin_id,
            component_name=component_name,
            args=args,
            timeout_ms=timeout_ms,
        )

    async def invoke_api(
        self,
        plugin_id: str,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Envelope:
        """调用插件声明的 API 方法。

        Args:
            plugin_id: 目标插件 ID。
            component_name: API 组件名称。
            args: 传递给 API 方法的关键字参数。
            timeout_ms: RPC 超时时间，单位毫秒。

        Returns:
            Envelope: Runner 返回的响应信封。
        """

        return await self.invoke_plugin(
            method="plugin.invoke_api",
            plugin_id=plugin_id,
            component_name=component_name,
            args=args,
            timeout_ms=timeout_ms,
        )

    async def reload_plugin(
        self,
        plugin_id: str,
        reason: str = "manual",
        external_available_plugins: Optional[Dict[str, str]] = None,
    ) -> bool:
        """按插件 ID 触发精确重载。

        Args:
            plugin_id: 目标插件 ID。
            reason: 重载原因。
            external_available_plugins: 视为已满足的外部依赖插件版本映射。

        Returns:
            bool: 是否重载成功。
        """
        try:
            response = await self._rpc_server.send_request(
                "plugin.reload",
                plugin_id=plugin_id,
                payload={
                    "plugin_id": plugin_id,
                    "reason": reason,
                    "external_available_plugins": external_available_plugins or self._external_available_plugins,
                },
                timeout_ms=max(int(self._runner_spawn_timeout * 1000), 10000),
            )
        except Exception as exc:
            logger.error(f"插件 {plugin_id} 重载请求失败: {exc}")
            return False

        result = ReloadPluginResultPayload.model_validate(response.payload)
        if not result.success:
            logger.warning(f"插件 {plugin_id} 重载失败: {result.failed_plugins}")
        return result.success

    async def reload_plugins(
        self,
        plugin_ids: Optional[List[str]] = None,
        reason: str = "manual",
        external_available_plugins: Optional[Dict[str, str]] = None,
    ) -> bool:
        """批量重载插件。

        Args:
            plugin_ids: 目标插件 ID 列表；为空时重载当前已注册的全部插件。
            reason: 重载原因。
            external_available_plugins: 视为已满足的外部依赖插件版本映射。

        Returns:
            bool: 是否全部重载成功。
        """
        target_plugin_ids = plugin_ids or list(self._registered_plugins.keys())
        ordered_plugin_ids = list(dict.fromkeys(target_plugin_ids))
        success = True

        for plugin_id in ordered_plugin_ids:
            reloaded = await self.reload_plugin(
                plugin_id=plugin_id,
                reason=reason,
                external_available_plugins=external_available_plugins,
            )
            success = success and reloaded

        return success

    async def notify_plugin_config_updated(
        self,
        plugin_id: str,
        config_data: Optional[Dict[str, Any]] = None,
        config_version: str = "",
        config_scope: str | ConfigReloadScope = "self",
    ) -> bool:
        """向 Runner 推送插件配置更新。

        Args:
            plugin_id: 目标插件 ID。
            config_data: 配置内容。
            config_version: 配置版本号。
            config_scope: 配置变更范围。

        Returns:
            bool: 请求是否成功送达并被 Runner 接受。
        """
        try:
            normalized_scope = ConfigReloadScope(config_scope)
        except ValueError:
            logger.warning(f"插件 {plugin_id} 配置更新通知失败: 非法的 config_scope={config_scope}")
            return False

        payload = ConfigUpdatedPayload(
            plugin_id=plugin_id,
            config_scope=normalized_scope,
            config_version=config_version,
            config_data=config_data or {},
        )
        try:
            response = await self._rpc_server.send_request(
                "plugin.config_updated",
                plugin_id=plugin_id,
                payload=payload.model_dump(),
                timeout_ms=10000,
            )
        except Exception as exc:
            logger.warning(f"插件 {plugin_id} 配置更新通知失败: {exc}")
            return False

        return bool(response.payload.get("acknowledged", False))

    def get_config_reload_subscribers(self, scope: str) -> List[str]:
        """返回订阅指定全局配置广播的插件列表。

        Args:
            scope: 配置变更范围，仅支持 ``bot`` 或 ``model``。

        Returns:
            List[str]: 已声明订阅该范围的插件 ID 列表。
        """

        return [
            plugin_id
            for plugin_id, registration in self._registered_plugins.items()
            if scope in registration.config_reload_subscriptions
        ]

    async def _wait_for_runner_connection(self, timeout_sec: float) -> None:
        """等待 Runner 建立 RPC 连接。

        Args:
            timeout_sec: 超时时间，单位秒。

        Raises:
            TimeoutError: 在超时时间内 Runner 未完成连接。
        """

        async def wait_for_connection() -> None:
            """轮询等待 RPC 连接建立。"""
            while True:
                if self._rpc_server.is_connected:
                    return

                if not self._running:
                    raise RuntimeError("Supervisor 已停止，等待 Runner 连接已取消")

                if failure_reason := self._get_runner_startup_failure_reason():
                    raise RuntimeError(f"等待 Runner 连接失败: {failure_reason}")

                await asyncio.sleep(0.1)

        try:
            await asyncio.wait_for(wait_for_connection(), timeout=timeout_sec)
            logger.info("Runner 已连接到 RPC Server")
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"等待 Runner 连接超时（{timeout_sec}s）") from exc

    async def _wait_for_runner_ready(self, timeout_sec: float = 30.0) -> RunnerReadyPayload:
        """等待 Runner 完成启动初始化。

        Args:
            timeout_sec: 超时时间，单位秒。

        Returns:
            RunnerReadyPayload: Runner 上报的就绪信息。

        Raises:
            TimeoutError: 在超时时间内 Runner 未完成初始化。
        """
        async def wait_for_ready() -> RunnerReadyPayload:
            """轮询等待 Runner 上报就绪。"""
            while True:
                if self._runner_ready_events.is_set():
                    return self._runner_ready_payloads

                if not self._running:
                    raise RuntimeError("Supervisor 已停止，等待 Runner 就绪已取消")

                if failure_reason := self._get_runner_startup_failure_reason():
                    raise RuntimeError(f"等待 Runner 就绪失败: {failure_reason}")

                if not self._rpc_server.is_connected:
                    raise RuntimeError("等待 Runner 就绪失败: Runner RPC 连接已断开")

                await asyncio.sleep(0.1)

        try:
            payload = await asyncio.wait_for(wait_for_ready(), timeout=timeout_sec)
            logger.info("Runner 已完成初始化并上报就绪")
            return payload
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"等待 Runner 就绪超时（{timeout_sec}s）") from exc

    def _register_internal_methods(self) -> None:
        """注册 Host 侧内部 RPC 方法。"""
        self._rpc_server.register_method("cap.call", self._capability_service.handle_capability_request)
        self._rpc_server.register_method("host.route_message", self._handle_route_message)
        self._rpc_server.register_method("host.update_message_gateway_state", self._handle_update_message_gateway_state)
        self._rpc_server.register_method("plugin.bootstrap", self._handle_bootstrap_plugin)
        self._rpc_server.register_method("plugin.register_components", self._handle_register_plugin)
        self._rpc_server.register_method("plugin.register_plugin", self._handle_register_plugin)
        self._rpc_server.register_method("plugin.unregister", self._handle_unregister_plugin)
        self._rpc_server.register_method("runner.log_batch", self._log_bridge.handle_log_batch)
        self._rpc_server.register_method("runner.ready", self._handle_runner_ready)

    async def _handle_bootstrap_plugin(self, envelope: Envelope) -> Envelope:
        """处理插件 bootstrap 请求。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: RPC 响应信封。
        """
        try:
            payload = BootstrapPluginPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        if payload.capabilities_required:
            self._authorization.register_plugin(payload.plugin_id, payload.capabilities_required)
        else:
            self._authorization.revoke_permission_token(payload.plugin_id)

        return envelope.make_response(payload={"accepted": True, "plugin_id": payload.plugin_id})

    async def _handle_register_plugin(self, envelope: Envelope) -> Envelope:
        """处理插件组件注册请求。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: RPC 响应信封。
        """
        try:
            payload = RegisterPluginPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        component_declarations = [component.model_dump() for component in payload.components]
        runtime_components, api_components = self._split_component_declarations(component_declarations)
        self._component_registry.remove_components_by_plugin(payload.plugin_id)
        self._api_registry.remove_apis_by_plugin(payload.plugin_id)
        await self._unregister_all_message_gateway_drivers_for_plugin(payload.plugin_id)

        registered_count = self._component_registry.register_plugin_components(
            payload.plugin_id,
            runtime_components,
        )
        registered_api_count = self._api_registry.register_plugin_apis(payload.plugin_id, api_components)
        self._registered_plugins[payload.plugin_id] = payload
        self._message_gateway_states[payload.plugin_id] = {}

        return envelope.make_response(
            payload={
                "accepted": True,
                "plugin_id": payload.plugin_id,
                "registered_components": registered_count,
                "registered_apis": registered_api_count,
                "message_gateways": len(
                    self._component_registry.get_message_gateways(plugin_id=payload.plugin_id, enabled_only=False)
                ),
            }
        )

    async def _handle_unregister_plugin(self, envelope: Envelope) -> Envelope:
        """处理插件注销请求。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: RPC 响应信封。
        """
        try:
            payload = UnregisterPluginPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        removed_components = self._component_registry.remove_components_by_plugin(payload.plugin_id)
        removed_apis = self._api_registry.remove_apis_by_plugin(payload.plugin_id)
        self._authorization.revoke_permission_token(payload.plugin_id)
        removed_registration = self._registered_plugins.pop(payload.plugin_id, None) is not None
        await self._unregister_all_message_gateway_drivers_for_plugin(payload.plugin_id)
        self._message_gateway_states.pop(payload.plugin_id, None)

        return envelope.make_response(
            payload={
                "accepted": True,
                "plugin_id": payload.plugin_id,
                "reason": payload.reason,
                "removed_components": removed_components,
                "removed_apis": removed_apis,
                "removed_registration": removed_registration,
            }
        )

    @staticmethod
    def _is_api_component(component: Dict[str, Any]) -> bool:
        """判断组件声明是否属于 API。

        Args:
            component: 原始组件声明字典。

        Returns:
            bool: 是否为 API 组件。
        """

        return str(component.get("component_type", "") or "").strip().upper() == "API"

    def _split_component_declarations(
        self,
        components: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """拆分通用组件声明和 API 声明。

        Args:
            components: Runner 上报的原始组件声明列表。

        Returns:
            Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
                第一个列表为需要进入通用组件表的声明，
                第二个列表为需要进入 API 专用表的声明。
        """

        runtime_components: List[Dict[str, Any]] = []
        api_components: List[Dict[str, Any]] = []
        for component in components:
            if self._is_api_component(component):
                api_components.append(component)
            else:
                runtime_components.append(component)
        return runtime_components, api_components

    @staticmethod
    def _build_message_gateway_driver_id(plugin_id: str, gateway_name: str) -> str:
        """构造消息网关驱动 ID。

        Args:
            plugin_id: 插件 ID。
            gateway_name: 网关组件名称。

        Returns:
            str: 对应 Platform IO 中的驱动 ID。
        """

        return f"gateway:{plugin_id}:{gateway_name}"

    @staticmethod
    def _normalize_runtime_route_value(value: str) -> Optional[str]:
        """规范化运行时路由字段。

        Args:
            value: 待规范化的原始字符串。

        Returns:
            Optional[str]: 规范化后非空则返回字符串，否则返回 ``None``。
        """

        normalized_value = str(value or "").strip()
        return normalized_value or None

    def _resolve_message_gateway_entry(
        self,
        plugin_id: str,
        gateway_name: str,
    ) -> Optional[Any]:
        """解析指定插件的消息网关组件。

        Args:
            plugin_id: 插件 ID。
            gateway_name: 网关组件名称；为空时按兼容规则推断。

        Returns:
            Optional[Any]: 匹配到的消息网关组件条目。
        """

        if gateway_name:
            return self._component_registry.get_message_gateway(
                plugin_id=plugin_id,
                name=gateway_name,
                enabled_only=False,
            )

        gateways = self._component_registry.get_message_gateways(plugin_id=plugin_id, enabled_only=False)
        return gateways[0] if len(gateways) == 1 else None

    async def _register_message_gateway_driver(
        self,
        plugin_id: str,
        gateway_entry: Any,
        route_key: RouteKey,
    ) -> None:
        """为消息网关注册驱动并绑定发送/接收路由。

        Args:
            plugin_id: 插件 ID。
            gateway_entry: 消息网关组件条目。
            route_key: 当前链路对应的路由键。
        """

        await self._unregister_message_gateway_driver(plugin_id, gateway_entry.name)

        platform_io_manager = get_platform_io_manager()
        driver = PluginPlatformDriver(
            driver_id=self._build_message_gateway_driver_id(plugin_id, gateway_entry.name),
            platform=route_key.platform,
            account_id=route_key.account_id,
            scope=route_key.scope,
            plugin_id=plugin_id,
            component_name=gateway_entry.name,
            supports_send=bool(gateway_entry.supports_send),
            supervisor=self,
            metadata={
                "protocol": gateway_entry.protocol,
                "route_type": gateway_entry.route_type,
                **gateway_entry.metadata,
            },
        )

        try:
            if platform_io_manager.is_started:
                await platform_io_manager.add_driver(driver)
            else:
                platform_io_manager.register_driver(driver)
        except Exception:
            with contextlib.suppress(Exception):
                if platform_io_manager.is_started:
                    await platform_io_manager.remove_driver(driver.driver_id)
                else:
                    platform_io_manager.unregister_driver(driver.driver_id)
            raise

        binding_metadata = {
            "plugin_id": plugin_id,
            "gateway_name": gateway_entry.name,
            "protocol": gateway_entry.protocol,
            "route_type": gateway_entry.route_type,
            **gateway_entry.metadata,
        }
        binding = RouteBinding(
            route_key=route_key,
            driver_id=driver.driver_id,
            driver_kind=DriverKind.PLUGIN,
            metadata=binding_metadata,
        )
        if gateway_entry.supports_send:
            platform_io_manager.bind_send_route(binding)
        if gateway_entry.supports_receive:
            platform_io_manager.bind_receive_route(binding)

    async def _unregister_message_gateway_driver(self, plugin_id: str, gateway_name: str) -> None:
        """从 Platform IO 注销单个消息网关驱动。

        Args:
            plugin_id: 插件 ID。
            gateway_name: 网关组件名称。
        """

        platform_io_manager = get_platform_io_manager()
        driver_id = self._build_message_gateway_driver_id(plugin_id, gateway_name)
        platform_io_manager.send_route_table.remove_bindings_by_driver(driver_id)
        platform_io_manager.receive_route_table.remove_bindings_by_driver(driver_id)

        with contextlib.suppress(Exception):
            if platform_io_manager.is_started:
                await platform_io_manager.remove_driver(driver_id)
            else:
                platform_io_manager.unregister_driver(driver_id)

    async def _unregister_all_message_gateway_drivers_for_plugin(self, plugin_id: str) -> None:
        """注销指定插件的全部消息网关驱动。

        Args:
            plugin_id: 插件 ID。
        """

        gateway_names = list(self._message_gateway_states.get(plugin_id, {}).keys())
        for gateway_name in gateway_names:
            await self._unregister_message_gateway_driver(plugin_id, gateway_name)

    def _build_message_gateway_route_key(
        self,
        gateway_entry: Any,
        payload: MessageGatewayStateUpdatePayload,
    ) -> RouteKey:
        """根据消息网关运行时状态构造路由键。

        Args:
            gateway_entry: 消息网关组件条目。
            payload: 网关上报的运行时状态。

        Returns:
            RouteKey: 当前链路对应的路由键。

        Raises:
            ValueError: 当平台信息缺失时抛出。
        """

        if not (platform := str(payload.platform or gateway_entry.platform or "").strip()):
            raise ValueError(f"消息网关 {gateway_entry.full_name} 未提供有效的平台名称")

        return RouteKey(
            platform=platform,
            account_id=self._normalize_runtime_route_value(payload.account_id) or gateway_entry.account_id or None,
            scope=self._normalize_runtime_route_value(payload.scope) or gateway_entry.scope or None,
        )

    def _apply_message_gateway_state(
        self,
        plugin_id: str,
        gateway_entry: Any,
        payload: MessageGatewayStateUpdatePayload,
    ) -> Tuple[_MessageGatewayRuntimeState, Dict[str, Any]]:
        """应用消息网关运行时状态，并同步 Platform IO 路由。

        Args:
            plugin_id: 插件 ID。
            gateway_entry: 消息网关组件条目。
            payload: 网关上报的运行时状态。

        Returns:
            Tuple[_MessageGatewayRuntimeState, Dict[str, Any]]: 更新后的状态与路由键字典。
        """

        plugin_states = self._message_gateway_states.setdefault(plugin_id, {})
        if not payload.ready:
            runtime_state = _MessageGatewayRuntimeState(
                ready=False,
                platform=self._normalize_runtime_route_value(payload.platform) or gateway_entry.platform or None,
                account_id=self._normalize_runtime_route_value(payload.account_id) or gateway_entry.account_id or None,
                scope=self._normalize_runtime_route_value(payload.scope) or gateway_entry.scope or None,
                metadata=dict(payload.metadata),
            )
            plugin_states[gateway_entry.name] = runtime_state
            return runtime_state, {}

        route_key = self._build_message_gateway_route_key(gateway_entry, payload)
        runtime_state = _MessageGatewayRuntimeState(
            ready=True,
            platform=route_key.platform,
            account_id=route_key.account_id,
            scope=route_key.scope,
            metadata=dict(payload.metadata),
        )
        plugin_states[gateway_entry.name] = runtime_state
        return runtime_state, {
            "platform": route_key.platform,
            "account_id": route_key.account_id,
            "scope": route_key.scope,
        }

    @staticmethod
    def _attach_inbound_route_metadata(
        session_message: "SessionMessage",
        route_key: RouteKey,
        route_metadata: Dict[str, Any],
    ) -> None:
        """将入站路由信息写回消息的 ``additional_config``。

        Args:
            session_message: 已构造好的内部消息对象。
            route_key: Host 为该消息解析出的标准路由键。
            route_metadata: 插件通过 RPC 补充的原始路由辅助元数据。
        """

        additional_config = session_message.message_info.additional_config
        if not isinstance(additional_config, dict):
            additional_config = {}
            session_message.message_info.additional_config = additional_config

        for key, value in route_metadata.items():
            if value is None:
                continue
            normalized_value = str(value).strip()
            if normalized_value:
                additional_config[key] = value

        if route_key.account_id:
            additional_config.setdefault("platform_io_account_id", route_key.account_id)
        if route_key.scope:
            additional_config.setdefault("platform_io_scope", route_key.scope)

    def _build_inbound_route_key(
        self,
        gateway_entry: Any,
        runtime_state: _MessageGatewayRuntimeState,
        message: Dict[str, Any],
        route_metadata: Dict[str, Any],
    ) -> RouteKey:
        """为入站消息构造归一路由键。

        Args:
            gateway_entry: 接收消息的网关组件条目。
            runtime_state: 当前网关的运行时状态。
            message: 标准消息字典。
            route_metadata: 插件补充的路由辅助元数据。

        Returns:
            RouteKey: 供 Platform IO 使用的规范化路由键。
        """

        platform = str(
            message.get("platform")
            or route_metadata.get("platform")
            or runtime_state.platform
            or gateway_entry.platform
            or ""
        ).strip()
        if not platform:
            raise ValueError(f"消息网关 {gateway_entry.full_name} 的入站消息缺少平台信息")

        try:
            route_key = RouteKeyFactory.from_message_dict(message)
        except Exception:
            route_key = RouteKey(platform=platform)

        route_account_id, route_scope = RouteKeyFactory.extract_components(route_metadata)
        account_id = route_key.account_id or route_account_id or runtime_state.account_id or gateway_entry.account_id or None
        scope = route_key.scope or route_scope or runtime_state.scope or gateway_entry.scope or None
        return RouteKey(
            platform=platform,
            account_id=account_id,
            scope=scope,
        )

    async def _handle_update_message_gateway_state(self, envelope: Envelope) -> Envelope:
        """处理消息网关上报的运行时状态更新。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: 状态更新处理结果。
        """

        try:
            payload = MessageGatewayStateUpdatePayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        gateway_entry = self._resolve_message_gateway_entry(envelope.plugin_id, payload.gateway_name)
        if gateway_entry is None:
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {envelope.plugin_id} 未声明消息网关 {payload.gateway_name or '<auto>'}",
            )

        try:
            if payload.ready:
                route_key = self._build_message_gateway_route_key(gateway_entry, payload)
                await self._register_message_gateway_driver(envelope.plugin_id, gateway_entry, route_key)
            else:
                await self._unregister_message_gateway_driver(envelope.plugin_id, gateway_entry.name)
            runtime_state, route_key_dict = self._apply_message_gateway_state(
                plugin_id=envelope.plugin_id,
                gateway_entry=gateway_entry,
                payload=payload,
            )
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        response = MessageGatewayStateUpdateResultPayload(
            accepted=True,
            ready=runtime_state.ready,
            route_key=route_key_dict,
        )
        return envelope.make_response(payload=response.model_dump())

    async def _handle_route_message(self, envelope: Envelope) -> Envelope:
        """处理消息网关上报的外部入站消息。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: 注入结果响应。
        """

        try:
            payload = RouteMessagePayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        gateway_entry = self._resolve_message_gateway_entry(envelope.plugin_id, payload.gateway_name)
        if gateway_entry is None or not bool(gateway_entry.supports_receive):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {envelope.plugin_id} 未声明可接收的消息网关 {payload.gateway_name}",
            )

        runtime_state = self._message_gateway_states.get(envelope.plugin_id, {}).get(
            gateway_entry.name,
            _MessageGatewayRuntimeState(),
        )
        if not runtime_state.ready:
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"消息网关 {gateway_entry.full_name} 尚未就绪，不能注入外部消息",
            )

        try:
            route_key = self._build_inbound_route_key(
                gateway_entry=gateway_entry,
                runtime_state=runtime_state,
                message=payload.message,
                route_metadata=payload.route_metadata,
            )
            session_message = self._message_gateway.build_session_message(payload.message)
            self._attach_inbound_route_metadata(session_message, route_key, payload.route_metadata)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        platform_io_manager = get_platform_io_manager()
        accepted = await platform_io_manager.accept_inbound(
            InboundMessageEnvelope(
                route_key=route_key,
                driver_id=self._build_message_gateway_driver_id(envelope.plugin_id, gateway_entry.name),
                driver_kind=DriverKind.PLUGIN,
                external_message_id=payload.external_message_id or str(payload.message.get("message_id") or "") or None,
                dedupe_key=payload.dedupe_key or None,
                session_message=session_message,
                payload=payload.message,
                metadata={
                    "plugin_id": envelope.plugin_id,
                    "gateway_name": gateway_entry.name,
                    "protocol": gateway_entry.protocol,
                    **payload.route_metadata,
                },
            )
        )
        response = ReceiveExternalMessageResultPayload(
            accepted=accepted,
            route_key={
                "platform": route_key.platform,
                "account_id": route_key.account_id,
                "scope": route_key.scope,
            },
        )
        return envelope.make_response(payload=response.model_dump())

    async def _handle_runner_ready(self, envelope: Envelope) -> Envelope:
        """处理 Runner 就绪通知。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: RPC 响应信封。
        """
        try:
            payload = RunnerReadyPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        self._runner_ready_payloads = payload
        self._runner_ready_events.set()
        return envelope.make_response(payload={"accepted": True})

    def _build_runner_environment(self) -> Dict[str, str]:
        """构建拉起 Runner 所需的环境变量。

        Returns:
            Dict[str, str]: 传递给 Runner 进程的环境变量映射。
        """
        global_config_snapshot = config_manager.get_global_config().model_dump()
        global_config_snapshot["model"] = config_manager.get_model_config().model_dump()
        return {
            ENV_EXTERNAL_PLUGIN_IDS: json.dumps(self._external_available_plugins, ensure_ascii=False),
            ENV_GLOBAL_CONFIG_SNAPSHOT: json.dumps(global_config_snapshot, ensure_ascii=False),
            ENV_HOST_VERSION: PROTOCOL_VERSION,
            ENV_IPC_ADDRESS: self._transport.get_address(),
            ENV_PLUGIN_DIRS: os.pathsep.join(str(path) for path in self._plugin_dirs),
            ENV_SESSION_TOKEN: self._rpc_server.session_token,
        }

    async def _spawn_runner(self) -> None:
        """拉起 Runner 子进程。"""
        if self._runner_process is not None and self._runner_process.returncode is None:
            logger.warning("Runner 已在运行，跳过重复拉起")
            return

        self._clear_runner_state()

        env = os.environ.copy()
        env.update(self._build_runner_environment())

        self._runner_process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "src.plugin_runtime.runner.runner_main",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        if self._runner_process.stderr is not None:
            self._stderr_drain_task = asyncio.create_task(
                self._drain_runner_stderr(self._runner_process.stderr),
                name="PluginRunnerSupervisor.stderr",
            )

        logger.info(f"Runner 已拉起，pid={self._runner_process.pid}")

    async def _drain_runner_stderr(self, stream: asyncio.StreamReader) -> None:
        """持续排空 Runner 的 stderr。

        Args:
            stream: Runner 的 stderr 流。
        """
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                if message := line.decode("utf-8", errors="replace").rstrip():
                    logger.warning(f"[runner-stderr] {message}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"排空 Runner stderr 失败: {exc}")

    async def _shutdown_runner(self, reason: str = "normal") -> None:
        """优雅关闭 Runner 子进程。

        Args:
            reason: 关停原因。
        """
        process = self._runner_process
        if process is None:
            return

        payload = ShutdownPayload(reason=reason)

        if process.returncode is None and self._rpc_server.is_connected:
            with contextlib.suppress(Exception):
                await self._rpc_server.send_request(
                    "plugin.prepare_shutdown",
                    payload=payload.model_dump(),
                    timeout_ms=payload.drain_timeout_ms,
                )
            with contextlib.suppress(Exception):
                await self._rpc_server.send_request(
                    "plugin.shutdown",
                    payload=payload.model_dump(),
                    timeout_ms=payload.drain_timeout_ms,
                )

        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=max(payload.drain_timeout_ms / 1000.0, 1.0))
            except asyncio.TimeoutError:
                logger.warning("Runner 优雅退出超时，尝试 terminate")
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Runner terminate 超时，尝试 kill")
                    process.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(process.wait(), timeout=5.0)

        self._runner_process = None

        if self._stderr_drain_task is not None:
            self._stderr_drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_drain_task
            self._stderr_drain_task = None

        for plugin_id in list(self._message_gateway_states.keys()):
            await self._unregister_all_message_gateway_drivers_for_plugin(plugin_id)
        self._clear_runner_state()

    async def _health_check_loop(self) -> None:
        """周期性检查 Runner 健康状态，并在必要时重启。"""
        timeout_ms = max(int(self._health_interval * 1000), 1000)

        while self._running:
            try:
                await asyncio.sleep(self._health_interval)
            except asyncio.CancelledError:
                return

            if not self._running:
                return

            process = self._runner_process
            if process is None or process.returncode is not None:
                reason = "runner_process_exited" if process is not None else "runner_process_missing"
                restarted = await self._restart_runner(reason=reason)
                if not restarted:
                    return
                continue

            try:
                response = await self._rpc_server.send_request("plugin.health", timeout_ms=timeout_ms)
                health = HealthPayload.model_validate(response.payload)
                if not health.healthy:
                    restarted = await self._restart_runner(reason="health_check_unhealthy")
                    if not restarted:
                        return
            except asyncio.CancelledError:
                return
            except (RPCError, Exception) as exc:
                logger.warning(f"Runner 健康检查失败: {exc}")
                restarted = await self._restart_runner(reason="health_check_failed")
                if not restarted:
                    return

    async def _restart_runner(self, reason: str) -> bool:
        """在 Runner 异常时执行整进程级重启。

        Args:
            reason: 触发重启的原因。

        Returns:
            bool: 是否重启成功。
        """
        if not self._running:
            return False

        if self._restart_count >= self._max_restart_attempts:
            logger.error(f"Runner 自动重启次数已达上限，停止重启。reason={reason}")
            return False

        self._restart_count += 1
        logger.warning(f"准备重启 Runner，第 {self._restart_count} 次，reason={reason}")

        await self._shutdown_runner(reason=reason)

        try:
            await self._spawn_runner()
            await self._wait_for_runner_connection(timeout_sec=self._runner_spawn_timeout)
            await self._wait_for_runner_ready(timeout_sec=self._runner_spawn_timeout)
        except Exception as exc:
            await self._shutdown_runner(reason="restart_failed")
            logger.error(f"Runner 重启失败: {exc}", exc_info=True)
            return False

        self._restart_count = 0
        logger.info("Runner 已成功重启")
        return True

    def _clear_runner_state(self) -> None:
        """清理当前 Runner 对应的 Host 侧注册状态。"""
        self._authorization.clear()
        self._api_registry.clear()
        self._component_registry.clear()
        self._registered_plugins.clear()
        self._message_gateway_states.clear()
        self._runner_ready_events = asyncio.Event()
        self._runner_ready_payloads = RunnerReadyPayload()
        self._rpc_server.clear_handshake_state()

    def _get_runner_startup_failure_reason(self) -> Optional[str]:
        """获取 Runner 在启动阶段已经暴露出的失败原因。

        Returns:
            Optional[str]: 若已检测到失败则返回失败原因，否则返回 ``None``。
        """
        if handshake_reason := self._rpc_server.last_handshake_rejection_reason:
            return f"握手被拒绝: {handshake_reason}"

        process = self._runner_process
        if process is None:
            return "Runner 进程不存在"

        if process.returncode is not None:
            return f"Runner 进程已退出，退出码 {process.returncode}"

        return None


PluginSupervisor = PluginRunnerSupervisor
