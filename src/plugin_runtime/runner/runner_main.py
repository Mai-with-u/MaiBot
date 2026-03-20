"""Runner 主循环

作为独立子进程运行，负责：
1. 从环境变量读取 IPC 地址和会话令牌
2. 连接 Host 并完成握手
3. 加载所有插件
4. 注册组件到 Host
5. 处理 Host 的调用请求
6. 转发插件的能力调用到 Host
"""

from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple, cast

from pathlib import Path

import asyncio
import contextlib
import inspect
import logging as stdlib_logging
import os
import signal
import sys
import time
import tomllib

from src.common.logger import get_console_handler, get_logger, initialize_logging
from src.plugin_runtime import ENV_HOST_VERSION, ENV_IPC_ADDRESS, ENV_PLUGIN_DIRS, ENV_SESSION_TOKEN
from src.plugin_runtime.protocol.envelope import (
    BootstrapPluginPayload,
    ComponentDeclaration,
    Envelope,
    HealthPayload,
    InvokePayload,
    InvokeResultPayload,
    RegisterPluginPayload,
    ReloadPluginPayload,
    ReloadPluginResultPayload,
    RunnerReadyPayload,
    UnregisterPluginPayload,
)
from src.plugin_runtime.protocol.errors import ErrorCode
from src.plugin_runtime.runner.log_handler import RunnerIPCLogHandler
from src.plugin_runtime.runner.plugin_loader import PluginLoader, PluginMeta
from src.plugin_runtime.runner.rpc_client import RPCClient

logger = get_logger("plugin_runtime.runner.main")


class _ContextAwarePlugin(Protocol):
    def _set_context(self, context: Any) -> None:
        """为插件注入上下文对象。"""


def _install_shutdown_signal_handlers(
    mark_runner_shutting_down: Callable[[], None],
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
    """为 Runner 注册关停信号处理器。

    Windows 默认事件循环不支持 add_signal_handler，且当前 Runner 在 Windows
    下由 Host 直接 terminate/kill，不依赖进程内信号回调进行优雅收尾。
    """
    if sys.platform == "win32":
        return

    target_loop = loop or asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            target_loop.add_signal_handler(sig, mark_runner_shutting_down)
        except Exception as exc:
            if not isinstance(exc, (NotImplementedError, RuntimeError)):
                raise
            logger.debug(f"当前事件循环不支持注册 Runner 信号处理器: {exc}")
            return


def _disable_runner_console_logging() -> None:
    """关闭 Runner 的控制台日志输出，避免被 Host 从 stderr 二次包装。"""
    root_logger = stdlib_logging.getLogger()
    console_handler = get_console_handler()
    if console_handler in root_logger.handlers:
        root_logger.removeHandler(console_handler)


class PluginRunner:
    """插件 Runner

    运行在独立子进程中，管理所有插件的执行。
    """

    def __init__(
        self,
        host_address: str,
        session_token: str,
        plugin_dirs: List[str],
    ) -> None:
        """初始化 Runner。

        Args:
            host_address: Host 的 IPC 地址。
            session_token: 握手用会话令牌。
            plugin_dirs: 当前 Runner 负责扫描的插件目录列表。
        """
        self._host_address: str = host_address
        self._session_token: str = session_token
        self._plugin_dirs: List[str] = plugin_dirs

        self._rpc_client: RPCClient = RPCClient(host_address, session_token)
        self._loader: PluginLoader = PluginLoader(host_version=os.getenv(ENV_HOST_VERSION, ""))
        self._start_time: float = time.monotonic()
        self._shutting_down: bool = False
        self._reload_lock: asyncio.Lock = asyncio.Lock()

        # IPC 日志 Handler：握手成功后安装，将所有 stdlib logging 转发到 Host
        self._log_handler: Optional[RunnerIPCLogHandler] = None
        self._suspended_console_handlers: List[stdlib_logging.Handler] = []

    async def run(self) -> None:
        """运行 Runner 主循环。"""
        # 1. 连接 Host
        logger.info(f"Runner 启动，连接 Host: {self._host_address}")
        ok = await self._rpc_client.connect_and_handshake()
        if not ok:
            logger.error("握手失败，退出")
            return

        # 2. 握手成功后立即安装 IPC 日志 Handler，接管所有 Runner 端日志
        self._install_log_handler()

        # 3. 注册方法处理器
        self._register_handlers()

        # 3. 加载插件
        plugins = self._loader.discover_and_load(self._plugin_dirs)
        logger.info(f"已加载 {len(plugins)} 个插件")

        # 4. 注入 PluginContext + 调用 on_load 生命周期钩子
        failed_plugins: Set[str] = set(self._loader.failed_plugins.keys())
        for meta in plugins:
            ok = await self._activate_plugin(meta)
            if not ok:
                failed_plugins.add(meta.plugin_id)

        successful_plugins = [meta.plugin_id for meta in plugins if meta.plugin_id not in failed_plugins]
        await self._notify_ready(successful_plugins, sorted(failed_plugins))

        # 5. 等待直到收到关停信号
        with contextlib.suppress(asyncio.CancelledError):
            while not self._shutting_down:
                await asyncio.sleep(1.0)

        # 6. 卸载 IPC 日志 Handler 并刷空剩余缓冲，然后断开连接
        logger.info("Runner 开始关停")
        await self._uninstall_log_handler()
        await self._rpc_client.disconnect()
        logger.info("Runner 已退出")

    def _install_log_handler(self) -> None:
        """握手完成后将 RunnerIPCLogHandler 安装到 logging.root。

        安装后，Runner 进程内所有 stdlib logging 调用（含 structlog 透传的）
        均会通过 IPC 转发到 Host，由 Host 的 RunnerLogBridge 重放到主进程 Logger。
        """
        loop = asyncio.get_running_loop()
        handler = RunnerIPCLogHandler()
        handler.start(self._rpc_client, loop)
        self._suspend_console_handlers()
        stdlib_logging.root.addHandler(handler)
        self._log_handler = handler
        logger.debug(
            "RunnerIPCLogHandler \u5df2\u5b89\u88c3\uff0c\u63d2\u4ef6\u65e5\u5fd7\u5c06\u901a\u8fc7 IPC \u8f6c\u53d1\u5230\u4e3b\u8fdb\u7a0b"
        )

    async def _uninstall_log_handler(self) -> None:
        """关停前从 logging.root 移除 Handler 并刷空缓冲。

        必须在 disconnect() 之前调用，确保最后一批日志能正常发送。
        """
        if self._log_handler is None:
            return
        stdlib_logging.root.removeHandler(self._log_handler)
        await self._log_handler.stop()
        self._log_handler = None
        self._restore_console_handlers()
        logger.debug("RunnerIPCLogHandler \u5df2\u5378\u8f7d")

    def _suspend_console_handlers(self) -> None:
        """暂停 Runner 的控制台输出，避免与 IPC 转发重复。"""
        if self._suspended_console_handlers:
            return

        for handler in list(stdlib_logging.root.handlers):
            if isinstance(handler, stdlib_logging.StreamHandler):
                stdlib_logging.root.removeHandler(handler)
                self._suspended_console_handlers.append(handler)

    def _restore_console_handlers(self) -> None:
        """恢复此前暂停的控制台输出。"""
        if not self._suspended_console_handlers:
            return

        for handler in self._suspended_console_handlers:
            if handler not in stdlib_logging.root.handlers:
                stdlib_logging.root.addHandler(handler)
        self._suspended_console_handlers.clear()

    def _inject_context(self, plugin_id: str, instance: object) -> None:
        """为插件实例创建并注入 PluginContext。

        对新版 MaiBotPlugin（具有 _set_context 方法）：创建 PluginContext 并注入。
        对旧版 LegacyPluginAdapter（具有 _set_context 方法，由适配器代理）：同上。
        """
        if not hasattr(instance, "_set_context"):
            return

        try:
            from maibot_sdk.context import PluginContext
        except ImportError:
            logger.warning(f"maibot_sdk 不可用，无法为插件 {plugin_id} 创建 PluginContext")
            return

        rpc_client = self._rpc_client
        bound_plugin_id = plugin_id

        async def _rpc_call(
            method: str,
            plugin_id: str = "",
            payload: Optional[Dict[str, Any]] = None,
        ) -> Any:
            """桥接 PluginContext.call_capability → RPCClient.send_request。

            无论调用方传入何种 plugin_id，实际发往 Host 的 plugin_id
            始终绑定为当前插件实例，避免伪造其他插件身份申请能力。
            """
            if plugin_id and plugin_id != bound_plugin_id:
                logger.warning(
                    f"插件 {bound_plugin_id} 尝试以 {plugin_id} 身份发起 RPC，已强制绑定回自身身份"
                )
            resp = await rpc_client.send_request(
                method=method,
                plugin_id=bound_plugin_id,
                payload=payload or {},
            )
            # 从响应信封中提取业务结果
            if resp.error:
                raise RuntimeError(resp.error.get("message", "能力调用失败"))
            return resp.payload.get("result")

        ctx = PluginContext(plugin_id=plugin_id, rpc_call=_rpc_call)
        cast(_ContextAwarePlugin, instance)._set_context(ctx)
        logger.debug(f"已为插件 {plugin_id} 注入 PluginContext")

    def _apply_plugin_config(self, meta: PluginMeta, config_data: Optional[Dict[str, Any]] = None) -> None:
        """在 Runner 侧为插件实例注入当前插件配置。"""
        instance = meta.instance
        if not hasattr(instance, "set_plugin_config"):
            return

        plugin_config = config_data if config_data is not None else self._load_plugin_config(meta.plugin_dir)
        try:
            instance.set_plugin_config(plugin_config)
        except Exception as exc:
            logger.warning(f"插件 {meta.plugin_id} 配置注入失败: {exc}")

    @staticmethod
    def _load_plugin_config(plugin_dir: str) -> Dict[str, Any]:
        """从插件目录读取 config.toml。"""
        config_path = Path(plugin_dir) / "config.toml"
        if not config_path.exists():
            return {}

        try:
            with config_path.open("rb") as handle:
                loaded = tomllib.load(handle)
        except Exception as exc:
            logger.warning(f"读取插件配置失败 {config_path}: {exc}")
            return {}

        return loaded if isinstance(loaded, dict) else {}

    def _register_handlers(self) -> None:
        """注册 Host -> Runner 的方法处理器。"""
        self._rpc_client.register_method("plugin.invoke_command", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_action", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_tool", self._handle_invoke)
        self._rpc_client.register_method("plugin.emit_event", self._handle_event_invoke)
        self._rpc_client.register_method("plugin.invoke_hook", self._handle_hook_invoke)
        self._rpc_client.register_method("plugin.invoke_workflow_step", self._handle_workflow_step)
        self._rpc_client.register_method("plugin.health", self._handle_health)
        self._rpc_client.register_method("plugin.prepare_shutdown", self._handle_prepare_shutdown)
        self._rpc_client.register_method("plugin.shutdown", self._handle_shutdown)
        self._rpc_client.register_method("plugin.config_updated", self._handle_config_updated)
        self._rpc_client.register_method("plugin.reload", self._handle_reload_plugin)

    async def _bootstrap_plugin(self, meta: PluginMeta, capabilities_required: Optional[List[str]] = None) -> bool:
        """向 Host 同步插件 bootstrap 能力令牌。"""
        payload = BootstrapPluginPayload(
            plugin_id=meta.plugin_id,
            plugin_version=meta.version,
            capabilities_required=capabilities_required
            if capabilities_required is not None
            else list(meta.capabilities_required or []),
        )

        try:
            await self._rpc_client.send_request(
                "plugin.bootstrap",
                plugin_id=meta.plugin_id,
                payload=payload.model_dump(),
                timeout_ms=10000,
            )
            return True
        except Exception as e:
            logger.error(f"插件 {meta.plugin_id} bootstrap 失败: {e}")
            return False

    async def _deactivate_plugin(self, meta: PluginMeta) -> None:
        """撤销 bootstrap 期间为插件签发的能力令牌。"""
        await self._bootstrap_plugin(meta, capabilities_required=[])

    async def _register_plugin(self, meta: PluginMeta) -> bool:
        """向 Host 注册单个插件。

        Args:
            meta: 待注册的插件元数据。

        Returns:
            bool: 是否注册成功。
        """
        # 收集插件组件声明
        components: List[ComponentDeclaration] = []
        instance = meta.instance

        # 从插件实例获取组件声明（SDK 插件须实现 get_components 方法）
        if hasattr(instance, "get_components"):
            components.extend(
                ComponentDeclaration(
                    name=comp_info.get("name", ""),
                    component_type=comp_info.get("type", ""),
                    plugin_id=meta.plugin_id,
                    metadata=comp_info.get("metadata", {}),
                )
                for comp_info in instance.get_components()
            )

        reg_payload = RegisterPluginPayload(
            plugin_id=meta.plugin_id,
            plugin_version=meta.version,
            components=components,
            capabilities_required=meta.capabilities_required,
        )

        try:
            _resp = await self._rpc_client.send_request(
                "plugin.register_components",
                plugin_id=meta.plugin_id,
                payload=reg_payload.model_dump(),
                timeout_ms=10000,
            )
            logger.info(f"插件 {meta.plugin_id} 注册完成")
            return True
        except Exception as e:
            logger.error(f"插件 {meta.plugin_id} 注册失败: {e}")
            return False

    async def _unregister_plugin(self, plugin_id: str, reason: str) -> None:
        """通知 Host 注销指定插件。

        Args:
            plugin_id: 目标插件 ID。
            reason: 注销原因。
        """
        payload = UnregisterPluginPayload(plugin_id=plugin_id, reason=reason)
        try:
            await self._rpc_client.send_request(
                "plugin.unregister",
                plugin_id=plugin_id,
                payload=payload.model_dump(),
                timeout_ms=10000,
            )
        except Exception as exc:
            logger.warning(f"插件 {plugin_id} 注销通知失败: {exc}")

    async def _invoke_plugin_on_load(self, meta: PluginMeta) -> bool:
        """执行插件的 ``on_load`` 生命周期。

        Args:
            meta: 待初始化的插件元数据。

        Returns:
            bool: 生命周期是否执行成功。
        """
        instance = meta.instance
        if not hasattr(instance, "on_load"):
            return True

        try:
            result = instance.on_load()
            if asyncio.iscoroutine(result):
                await result
            return True
        except Exception as exc:
            logger.error(f"插件 {meta.plugin_id} on_load 失败: {exc}", exc_info=True)
            return False

    async def _invoke_plugin_on_unload(self, meta: PluginMeta) -> None:
        """执行插件的 ``on_unload`` 生命周期。

        Args:
            meta: 待卸载的插件元数据。
        """
        instance = meta.instance
        if not hasattr(instance, "on_unload"):
            return

        try:
            result = instance.on_unload()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.error(f"插件 {meta.plugin_id} on_unload 失败: {exc}", exc_info=True)

    async def _activate_plugin(self, meta: PluginMeta) -> bool:
        """完成插件注入、授权、生命周期和组件注册。

        Args:
            meta: 待激活的插件元数据。

        Returns:
            bool: 是否激活成功。
        """
        self._inject_context(meta.plugin_id, meta.instance)
        self._apply_plugin_config(meta)

        if not await self._bootstrap_plugin(meta):
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return False

        if not await self._invoke_plugin_on_load(meta):
            await self._deactivate_plugin(meta)
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return False

        if not await self._register_plugin(meta):
            await self._invoke_plugin_on_unload(meta)
            await self._deactivate_plugin(meta)
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return False

        self._loader.set_loaded_plugin(meta)
        return True

    async def _unload_plugin(self, meta: PluginMeta, reason: str) -> None:
        """卸载单个插件并清理 Host/Runner 两侧状态。

        Args:
            meta: 待卸载的插件元数据。
            reason: 卸载原因。
        """
        await self._invoke_plugin_on_unload(meta)
        await self._unregister_plugin(meta.plugin_id, reason)
        await self._deactivate_plugin(meta)
        self._loader.remove_loaded_plugin(meta.plugin_id)
        self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)

    def _collect_reverse_dependents(self, plugin_id: str) -> Set[str]:
        """收集依赖指定插件的所有已加载插件。

        Args:
            plugin_id: 根插件 ID。

        Returns:
            Set[str]: 目标插件及其所有反向依赖插件集合。
        """
        impacted_plugins: Set[str] = {plugin_id}
        changed = True

        while changed:
            changed = False
            for loaded_plugin_id in self._loader.list_plugins():
                if loaded_plugin_id in impacted_plugins:
                    continue

                meta = self._loader.get_plugin(loaded_plugin_id)
                if meta is None:
                    continue

                if any(dependency in impacted_plugins for dependency in meta.dependencies):
                    impacted_plugins.add(loaded_plugin_id)
                    changed = True

        return impacted_plugins

    def _build_unload_order(self, plugin_ids: Set[str]) -> List[str]:
        """构建受影响插件的卸载顺序。

        Args:
            plugin_ids: 需要卸载的插件集合。

        Returns:
            List[str]: 依赖方优先的卸载顺序。
        """
        dependency_graph: Dict[str, Set[str]] = {}
        for plugin_id in plugin_ids:
            meta = self._loader.get_plugin(plugin_id)
            if meta is None:
                dependency_graph[plugin_id] = set()
                continue
            dependency_graph[plugin_id] = {dependency for dependency in meta.dependencies if dependency in plugin_ids}

        indegree: Dict[str, int] = {plugin_id: len(dependencies) for plugin_id, dependencies in dependency_graph.items()}
        reverse_graph: Dict[str, Set[str]] = {plugin_id: set() for plugin_id in dependency_graph}

        for plugin_id, dependencies in dependency_graph.items():
            for dependency in dependencies:
                reverse_graph.setdefault(dependency, set()).add(plugin_id)

        queue: List[str] = sorted(plugin_id for plugin_id, degree in indegree.items() if degree == 0)
        load_order: List[str] = []

        while queue:
            current_plugin_id = queue.pop(0)
            load_order.append(current_plugin_id)
            for dependent_plugin_id in sorted(reverse_graph.get(current_plugin_id, set())):
                indegree[dependent_plugin_id] -= 1
                if indegree[dependent_plugin_id] == 0:
                    queue.append(dependent_plugin_id)
            queue.sort()

        return list(reversed(load_order))

    async def _reload_plugin_by_id(self, plugin_id: str, reason: str) -> ReloadPluginResultPayload:
        """按插件 ID 在 Runner 进程内执行精确重载。

        Args:
            plugin_id: 目标插件 ID。
            reason: 重载原因。

        Returns:
            ReloadPluginResultPayload: 结构化重载结果。
        """
        candidates, duplicate_candidates = self._loader.discover_candidates(self._plugin_dirs)
        failed_plugins: Dict[str, str] = {}

        if plugin_id in duplicate_candidates:
            conflict_paths = ", ".join(str(path) for path in duplicate_candidates[plugin_id])
            return ReloadPluginResultPayload(
                success=False,
                requested_plugin_id=plugin_id,
                failed_plugins={plugin_id: f"检测到重复插件 ID: {conflict_paths}"},
            )

        loaded_plugin_ids = set(self._loader.list_plugins())
        plugin_is_loaded = plugin_id in loaded_plugin_ids
        plugin_has_candidate = plugin_id in candidates

        if not plugin_is_loaded and not plugin_has_candidate:
            return ReloadPluginResultPayload(
                success=False,
                requested_plugin_id=plugin_id,
                failed_plugins={plugin_id: "插件不存在或未找到合法的 manifest/plugin.py"},
            )

        target_plugin_ids: Set[str] = {plugin_id}
        if plugin_is_loaded:
            target_plugin_ids = self._collect_reverse_dependents(plugin_id)

        unload_order = self._build_unload_order(target_plugin_ids & loaded_plugin_ids)
        unloaded_plugins: List[str] = []
        retained_plugin_ids = loaded_plugin_ids - set(unload_order)

        for unload_plugin_id in unload_order:
            meta = self._loader.get_plugin(unload_plugin_id)
            if meta is None:
                continue
            await self._unload_plugin(meta, reason=reason)
            unloaded_plugins.append(unload_plugin_id)

        reload_candidates: Dict[str, Tuple[Path, Dict[str, Any], Path]] = {}
        for target_plugin_id in target_plugin_ids:
            candidate = candidates.get(target_plugin_id)
            if candidate is None:
                failed_plugins[target_plugin_id] = "插件目录已不存在，已保持卸载状态"
                continue
            reload_candidates[target_plugin_id] = candidate

        load_order, dependency_failures = self._loader.resolve_dependencies(
            reload_candidates,
            extra_available=retained_plugin_ids,
        )
        failed_plugins.update(dependency_failures)

        available_plugins = set(retained_plugin_ids)
        reloaded_plugins: List[str] = []

        for load_plugin_id in load_order:
            if load_plugin_id in failed_plugins:
                continue

            candidate = reload_candidates.get(load_plugin_id)
            if candidate is None:
                continue

            _, manifest, _ = candidate
            dependencies = PluginMeta._extract_dependencies(manifest)
            missing_dependencies = [dependency for dependency in dependencies if dependency not in available_plugins]
            if missing_dependencies:
                failed_plugins[load_plugin_id] = f"依赖未满足: {', '.join(missing_dependencies)}"
                continue

            meta = self._loader.load_candidate(load_plugin_id, candidate)
            if meta is None:
                failed_plugins[load_plugin_id] = "插件模块加载失败"
                continue

            activated = await self._activate_plugin(meta)
            if not activated:
                failed_plugins[load_plugin_id] = "插件初始化失败"
                continue

            available_plugins.add(load_plugin_id)
            reloaded_plugins.append(load_plugin_id)

        requested_plugin_success = plugin_id in reloaded_plugins and not failed_plugins

        return ReloadPluginResultPayload(
            success=requested_plugin_success,
            requested_plugin_id=plugin_id,
            reloaded_plugins=reloaded_plugins,
            unloaded_plugins=unloaded_plugins,
            failed_plugins=failed_plugins,
        )

    async def _notify_ready(self, loaded_plugins: List[str], failed_plugins: List[str]) -> None:
        """通知 Host 当前 Runner 已完成插件初始化。

        Args:
            loaded_plugins: 成功初始化的插件列表。
            failed_plugins: 初始化失败的插件列表。
        """
        payload = RunnerReadyPayload(
            loaded_plugins=loaded_plugins,
            failed_plugins=failed_plugins,
        )
        await self._rpc_client.send_request(
            "runner.ready",
            payload=payload.model_dump(),
            timeout_ms=10000,
        )

    async def _handle_invoke(self, envelope: Envelope) -> Envelope:
        """处理组件调用请求"""
        try:
            invoke = InvokePayload.model_validate(envelope.payload)
        except Exception as e:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(e))

        plugin_id = envelope.plugin_id
        meta = self._loader.get_plugin(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                f"插件 {plugin_id} 未加载",
            )

        # 调用插件实例的组件方法
        instance = meta.instance
        component_name = invoke.component_name

        # 优先查找 handle_<name> 或直接 <name> 方法（新版 SDK 插件）
        handler_method = getattr(instance, f"handle_{component_name}", None)
        if handler_method is None:
            handler_method = getattr(instance, component_name, None)

        # 回退: 旧版 LegacyPluginAdapter 通过 invoke_component 统一桥接
        if (handler_method is None or not callable(handler_method)) and hasattr(instance, "invoke_component"):
            try:
                result = await instance.invoke_component(component_name, **invoke.args)
                resp_payload = InvokeResultPayload(success=True, result=result)
                return envelope.make_response(payload=resp_payload.model_dump())
            except Exception as e:
                logger.error(f"插件 {plugin_id} 组件 {component_name} (legacy) 执行异常: {e}", exc_info=True)
                resp_payload = InvokeResultPayload(success=False, result=str(e))
                return envelope.make_response(payload=resp_payload.model_dump())

        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 无组件: {component_name}",
            )

        try:
            result = (
                await handler_method(**invoke.args)
                if inspect.iscoroutinefunction(handler_method)
                else handler_method(**invoke.args)
            )
            resp_payload = InvokeResultPayload(success=True, result=result)
            return envelope.make_response(payload=resp_payload.model_dump())
        except Exception as e:
            logger.error(f"插件 {plugin_id} 组件 {component_name} 执行异常: {e}", exc_info=True)
            resp_payload = InvokeResultPayload(success=False, result=str(e))
            return envelope.make_response(payload=resp_payload.model_dump())

    async def _handle_event_invoke(self, envelope: Envelope) -> Envelope:
        """处理 EventHandler 调用请求

        与通用 invoke 不同，会将返回值规范化为
        {success, continue_processing, modified_message, custom_result} 格式，
        使 EventDispatcher 可直接从 payload 顶层读取这些字段。
        """
        try:
            invoke = InvokePayload.model_validate(envelope.payload)
        except Exception as e:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(e))

        plugin_id = envelope.plugin_id
        meta = self._loader.get_plugin(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                f"插件 {plugin_id} 未加载",
            )

        instance = meta.instance
        component_name = invoke.component_name
        handler_method = getattr(instance, f"handle_{component_name}", None)
        if handler_method is None:
            handler_method = getattr(instance, component_name, None)

        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 无组件: {component_name}",
            )

        try:
            raw = (
                await handler_method(**invoke.args)
                if inspect.iscoroutinefunction(handler_method)
                else handler_method(**invoke.args)
            )

            # 规范化返回值：将 EventHandler 返回展平到 payload 顶层
            if raw is None:
                result = {"success": True, "continue_processing": True}
            elif isinstance(raw, dict):
                result = {
                    "success": True,
                    # 兼容 guide.md 中文档的 {"blocked": True} 写法
                    "continue_processing": not raw.get("blocked", False)
                    if "blocked" in raw
                    else raw.get("continue_processing", True),
                    "modified_message": raw.get("modified_message"),
                    "custom_result": raw.get("custom_result"),
                }
            else:
                result = {"success": True, "continue_processing": True, "custom_result": raw}

            return envelope.make_response(payload=result)
        except Exception as e:
            logger.error(f"插件 {plugin_id} event_handler {component_name} 执行异常: {e}", exc_info=True)
            return envelope.make_response(payload={"success": False, "continue_processing": True})

    async def _handle_hook_invoke(self, envelope: Envelope) -> Envelope:
        """处理 HookHandler 调用请求。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: 标准化后的 Hook 调用结果。
        """
        try:
            invoke = InvokePayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        plugin_id = envelope.plugin_id
        meta = self._loader.get_plugin(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                f"插件 {plugin_id} 未加载",
            )

        instance = meta.instance
        component_name = invoke.component_name
        handler_method = getattr(instance, f"handle_{component_name}", None) or getattr(instance, component_name, None)
        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 无组件: {component_name}",
            )

        try:
            raw = (
                await handler_method(**invoke.args)
                if inspect.iscoroutinefunction(handler_method)
                else handler_method(**invoke.args)
            )
        except Exception as exc:
            logger.error(f"插件 {plugin_id} hook_handler {component_name} 执行异常: {exc}", exc_info=True)
            return envelope.make_response(payload={"success": False, "continue_processing": True})

        if raw is None:
            result = {"success": True, "continue_processing": True}
        elif isinstance(raw, dict):
            result = {
                "success": True,
                "continue_processing": raw.get("continue_processing", True),
                "modified_kwargs": raw.get("modified_kwargs"),
                "custom_result": raw.get("custom_result"),
            }
        else:
            result = {"success": True, "continue_processing": True, "custom_result": raw}

        return envelope.make_response(payload=result)

    async def _handle_workflow_step(self, envelope: Envelope) -> Envelope:
        """处理 WorkflowStep 调用请求

        与通用 invoke 不同，会将返回值规范化为
        {hook_result, modified_message, stage_output} 格式。
        """
        try:
            invoke = InvokePayload.model_validate(envelope.payload)
        except Exception as e:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(e))

        plugin_id = envelope.plugin_id
        meta = self._loader.get_plugin(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                f"插件 {plugin_id} 未加载",
            )

        instance = meta.instance
        component_name = invoke.component_name
        handler_method = getattr(instance, f"handle_{component_name}", None) or getattr(instance, component_name, None)

        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 无组件: {component_name}",
            )

        try:
            raw = (
                await handler_method(**invoke.args)
                if inspect.iscoroutinefunction(handler_method)
                else handler_method(**invoke.args)
            )

            # 规范化返回值
            if isinstance(raw, str):
                result = {"hook_result": raw}
            elif isinstance(raw, dict):
                result = raw
                result.setdefault("hook_result", "continue")
            else:
                result = {"hook_result": "continue"}

            resp_payload = InvokeResultPayload(success=True, result=result)
            return envelope.make_response(payload=resp_payload.model_dump())
        except Exception as e:
            logger.error(f"插件 {plugin_id} workflow_step {component_name} 执行异常: {e}", exc_info=True)
            resp_payload = InvokeResultPayload(success=False, result=str(e))
            return envelope.make_response(payload=resp_payload.model_dump())

    async def _handle_health(self, envelope: Envelope) -> Envelope:
        """处理健康检查"""
        uptime_ms = int((time.monotonic() - self._start_time) * 1000)
        health = HealthPayload(
            healthy=True,
            loaded_plugins=self._loader.list_plugins(),
            uptime_ms=uptime_ms,
        )
        return envelope.make_response(payload=health.model_dump())

    async def _handle_prepare_shutdown(self, envelope: Envelope) -> Envelope:
        """处理准备关停"""
        logger.info("收到 prepare_shutdown 信号")
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_shutdown(self, envelope: Envelope) -> Envelope:
        """处理关停 — 调用所有插件的 on_unload 后退出"""
        logger.info("收到 shutdown 信号，开始调用 on_unload")
        for plugin_id in list(self._loader.list_plugins()):
            meta = self._loader.get_plugin(plugin_id)
            if meta is not None:
                await self._unload_plugin(meta, reason="runner_shutdown")
        self._shutting_down = True
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_config_updated(self, envelope: Envelope) -> Envelope:
        """处理配置更新事件"""
        plugin_id = envelope.plugin_id
        if meta := self._loader.get_plugin(plugin_id):
            try:
                config_data = envelope.payload.get("config_data", {})
                config_version = envelope.payload.get("config_version", "")
                self._apply_plugin_config(meta, config_data=config_data)
                if hasattr(meta.instance, "on_config_update"):
                    ret = meta.instance.on_config_update(config_data, config_version)
                    # 兼容同步和异步的 on_config_update 实现
                    if asyncio.iscoroutine(ret):
                        await ret
            except Exception as e:
                logger.error(f"插件 {plugin_id} 配置更新失败: {e}")
                return envelope.make_error_response(ErrorCode.E_UNKNOWN.value, str(e))
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_reload_plugin(self, envelope: Envelope) -> Envelope:
        """处理按插件 ID 的精确重载请求。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: 结构化重载结果。
        """
        try:
            payload = ReloadPluginPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        if self._reload_lock.locked():
            return envelope.make_error_response(
                ErrorCode.E_RELOAD_IN_PROGRESS.value,
                f"插件 {payload.plugin_id} 重载请求被拒绝：已有重载任务正在执行",
            )

        async with self._reload_lock:
            result = await self._reload_plugin_by_id(payload.plugin_id, payload.reason)
            return envelope.make_response(payload=result.model_dump())

    def request_capability(self) -> RPCClient:
        """获取 RPC 客户端（供 SDK 使用，发起能力调用）"""
        return self._rpc_client


# ─── sys.path 隔离 ────────────────────────────────────────


def _isolate_sys_path(plugin_dirs: List[str]) -> None:
    """清理 sys.path，限制 Runner 子进程只能访问标准库、SDK 和插件目录。

    防止插件代码 import 主程序模块读取运行时数据。
    """
    import importlib.abc
    import sysconfig

    # 保留: 标准库路径 + site-packages（含 SDK 和依赖）
    stdlib_paths = set()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        if path := sysconfig.get_path(key):
            stdlib_paths.add(os.path.normpath(path))

    runtime_paths = set(stdlib_paths)
    if os.name == "nt":
        # Windows 的部分平台扩展模块和依赖会通过 <prefix>/DLLs 暴露在 sys.path 中。
        for prefix in {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix}:
            if prefix:
                runtime_paths.add(os.path.normpath(os.path.join(prefix, "DLLs")))

    allowed = set()
    for p in sys.path:
        norm = os.path.normpath(p)
        # 保留标准库和 site-packages
        if any(norm.startswith(runtime_path) for runtime_path in runtime_paths):
            allowed.add(p)
        # 保留 site-packages（第三方库 + SDK）
        if "site-packages" in norm or "dist-packages" in norm:
            allowed.add(p)

    # 添加插件目录
    plugin_dir_paths = [os.path.normpath(d) for d in plugin_dirs]
    for d in plugin_dir_paths:
        allowed.add(d)

    # 添加项目根目录（使得 src.plugin_runtime / src.common 可导入）
    runtime_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    allowed.add(runtime_root)

    preserved_paths = [p for p in sys.path if p in allowed]
    for extra_path in [*plugin_dir_paths, runtime_root]:
        if extra_path not in preserved_paths:
            preserved_paths.append(extra_path)
    sys.path[:] = preserved_paths

    # 安装 import 钩子，阻止插件导入主程序核心模块
    # 仅允许 src.plugin_runtime 和 src.common，拒绝其他 src.* 子包
    class _PluginImportBlocker(importlib.abc.MetaPathFinder):
        """阻止 Runner 子进程导入主程序核心模块。

        只放行 src.plugin_runtime 和 src.common，
        拒绝 src.chat_module / src.services 等主程序内部包。
        """

        _ALLOWED_SRC_PREFIXES = ("src.plugin_runtime", "src.common")

        def find_module(self, fullname: str, path: Any = None) -> Any:
            """决定是否拦截指定模块导入。"""
            return self if self._should_block(fullname) else None

        def load_module(self, fullname: str) -> None:
            """阻止被拦截模块继续导入。"""
            raise ImportError(f"Runner 子进程不允许导入主程序模块: {fullname}")

        def _should_block(self, fullname: str) -> bool:
            """判断给定模块名是否应被阻止导入。"""
            # 放行非 src.* 的导入、以及 "src" 本身
            if not fullname.startswith("src.") or fullname == "src":
                return False
            # 放行白名单前缀
            return not any(
                fullname == prefix or fullname.startswith(f"{prefix}.") for prefix in self._ALLOWED_SRC_PREFIXES
            )

    sys.meta_path.insert(0, _PluginImportBlocker())


# ─── 进程入口 ──────────────────────────────────────────────


async def _async_main() -> None:
    """异步主入口"""
    host_address = os.environ.get(ENV_IPC_ADDRESS, "")
    session_token = os.environ.get(ENV_SESSION_TOKEN, "")
    plugin_dirs_str = os.environ.get(ENV_PLUGIN_DIRS, "")

    if not host_address or not session_token:
        logger.error(f"缺少必要的环境变量: {ENV_IPC_ADDRESS}, {ENV_SESSION_TOKEN}")
        sys.exit(1)

    plugin_dirs = [d for d in plugin_dirs_str.split(os.pathsep) if d]

    # sys.path 隔离: 只保留标准库、SDK 包、插件目录
    _isolate_sys_path(plugin_dirs)

    runner = PluginRunner(host_address, session_token, plugin_dirs)

    # 注册信号处理
    def _mark_runner_shutting_down() -> None:
        """标记 Runner 即将进入关停流程。"""
        runner._shutting_down = True

    _install_shutdown_signal_handlers(_mark_runner_shutting_down)

    await runner.run()


def main() -> None:
    """进程入口（python -m src.plugin_runtime.runner.runner_main）"""
    initialize_logging(verbose=False)
    _disable_runner_console_logging()
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
