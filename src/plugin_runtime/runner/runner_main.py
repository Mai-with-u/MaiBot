"""Runner 主循环

作为独立子进程运行，负责：
1. 从环境变量读取 IPC 地址和会话令牌
2. 连接 Host 并完成握手
3. 加载所有插件
4. 注册组件到 Host
5. 处理 Host 的调用请求
6. 转发插件的能力调用到 Host
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple, cast

import asyncio
import contextlib
import inspect
import json
import logging as stdlib_logging
import os
import signal
import sys
import time
import tomllib

import tomlkit

from src.plugin_runtime.local_sdk import activate_local_sdk_import_path

activate_local_sdk_import_path()

from src.common.logger import get_console_handler, get_logger, initialize_logging
from src.config.config_utils import compare_versions
from src.plugin_runtime import (
    ENV_BLOCKED_PLUGIN_REASONS,
    ENV_EXTERNAL_PLUGIN_IDS,
    ENV_HOST_VERSION,
    ENV_IPC_ADDRESS,
    ENV_PLUGIN_DIRS,
    ENV_PLUGIN_TYPE_FILTER,
    ENV_RUNNER_GROUP,
    ENV_SESSION_TOKEN,
    ENV_TRUSTED_PLUGIN_DIRS,
)
from src.plugin_runtime.protocol.envelope import (
    BootstrapPluginPayload,
    ComponentDeclaration,
    ConfigUpdatedPayload,
    Envelope,
    HealthPayload,
    InspectPluginConfigPayload,
    InspectPluginConfigResultPayload,
    InvokePayload,
    InvokeResultPayload,
    LLMProviderDeclaration,
    LLMProviderInvokePayload,
    RegisterPluginPayload,
    ReloadPluginPayload,
    ReloadPluginResultPayload,
    ReloadPluginsPayload,
    ReloadPluginsResultPayload,
    RunnerReadyPayload,
    UnloadPluginsPayload,
    UnloadPluginsResultPayload,
    UnregisterPluginPayload,
    ValidatePluginConfigPayload,
    ValidatePluginConfigResultPayload,
)
from src.plugin_runtime.protocol.errors import ErrorCode
from src.plugin_runtime.runner.log_handler import RunnerIPCLogHandler
from src.plugin_runtime.runner.plugin_paths import PluginPaths, build_plugin_paths
from src.plugin_runtime.runner.plugin_loader import PluginCandidate, PluginLoader, PluginMeta
from src.plugin_runtime.runner.rpc_client import RPCClient

logger = get_logger("plugin_runtime.runner.main")

_PLUGIN_ALLOWED_RAW_HOST_METHODS = frozenset(
    {
        "cap.call",
        "host.route_message",
        "host.update_message_gateway_state",
    }
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_RUNNER_DEBUG_FILE_PATH = _PROJECT_ROOT / "logs" / "plugin_runtime_debug" / "runner_rpc_debug.jsonl"


class _ContextAwarePlugin(Protocol):
    """支持注入运行时上下文的插件协议。

    该协议用于描述 Runner 在激活插件时依赖的最小接口。
    只要插件实例实现了 ``_set_context`` 方法，就可以被 Runner
    注入 ``PluginContext`` 或兼容层上下文对象。
    """

    def _set_context(self, context: Any) -> None:
        """为插件实例注入运行时上下文。

        Args:
            context: 由 Runner 构造的上下文对象。
        """


class _ConfigAwarePlugin(Protocol):
    """支持声明式插件配置能力的插件协议。"""

    def normalize_plugin_config(self, config_data: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, Any], bool]:
        """对插件配置进行归一化与补齐。

        Args:
            config_data: 原始配置数据。

        Returns:
            Tuple[Dict[str, Any], bool]: 归一化后的配置，以及是否发生自动变更。
        """

        ...

    def set_plugin_config(self, config: Dict[str, Any]) -> None:
        """注入插件当前配置。

        Args:
            config: 当前最新插件配置。
        """

        ...

    def get_default_config(self) -> Dict[str, Any]:
        """返回插件默认配置。

        Returns:
            Dict[str, Any]: 默认配置字典。
        """

        ...

    def get_webui_config_schema(
        self,
        *,
        plugin_id: str = "",
        plugin_name: str = "",
        plugin_version: str = "",
        plugin_description: str = "",
        plugin_author: str = "",
    ) -> Dict[str, Any]:
        """返回插件配置 Schema。

        Args:
            plugin_id: 插件 ID。
            plugin_name: 插件名称。
            plugin_version: 插件版本。
            plugin_description: 插件描述。
            plugin_author: 插件作者。

        Returns:
            Dict[str, Any]: WebUI 配置 Schema。
        """

        ...


class PluginActivationStatus(str, Enum):
    """描述插件激活结果。"""

    LOADED = "loaded"
    INACTIVE = "inactive"
    FAILED = "failed"


@dataclass(slots=True)
class InFlightRPC:
    """记录 Runner 内当前正在执行的 RPC 调用。"""

    request_id: int
    method: str
    plugin_id: str
    component_name: str
    timeout_ms: int
    started_at_epoch: float
    started_at_monotonic: float
    payload_summary: Dict[str, Any]


@dataclass(frozen=True)
class PluginConfigNormalizationResult:
    """描述插件配置归一化结果。"""

    normalized_config: Dict[str, Any]
    changed: bool
    should_persist: bool


class PluginConfigVersionError(ValueError):
    """插件配置版本不合法时抛出的异常。"""


def _deep_copy_plugin_config_value(value: Any) -> Any:
    """递归复制插件配置值。

    Args:
        value: 待复制的任意配置值。

    Returns:
        Any: 深复制后的配置值。
    """

    if isinstance(value, Mapping):
        return _deep_copy_plugin_config_mapping(value)
    if isinstance(value, list):
        return [_deep_copy_plugin_config_value(item) for item in value]
    return value


def _deep_copy_plugin_config_mapping(value: Mapping[str, Any]) -> Dict[str, Any]:
    """递归复制插件配置字典。

    Args:
        value: 待复制的插件配置映射。

    Returns:
        Dict[str, Any]: 深复制后的插件配置字典。
    """

    return {str(key): _deep_copy_plugin_config_value(item) for key, item in value.items()}


def _is_plugin_config_value_type_compatible(target_value: Any, source_value: Any) -> bool:
    """判断旧配置值是否可回填到新默认配置字段。"""

    if target_value is None:
        return True
    if isinstance(target_value, bool):
        return isinstance(source_value, bool)
    if isinstance(target_value, str):
        return isinstance(source_value, str)
    if isinstance(target_value, list):
        return isinstance(source_value, list)
    if isinstance(target_value, int):
        return isinstance(source_value, int) and not isinstance(source_value, bool)
    if isinstance(target_value, float):
        return isinstance(source_value, (int, float)) and not isinstance(source_value, bool)
    return isinstance(source_value, type(target_value))


def _overlay_plugin_config_fields(target: Dict[str, Any], source: Mapping[str, Any]) -> None:
    """将旧配置中类型匹配的已有字段覆盖到新配置骨架中。

    Args:
        target: 以最新默认配置构造出的目标配置字典。
        source: 旧版本配置字典。
    """

    for key, source_value in source.items():
        if key not in target:
            continue
        if key == "config_version":
            continue

        target_value = target[key]
        if isinstance(target_value, dict) and isinstance(source_value, Mapping):
            _overlay_plugin_config_fields(target_value, source_value)
            continue

        if not _is_plugin_config_value_type_compatible(target_value, source_value):
            continue

        target[key] = _deep_copy_plugin_config_value(source_value)


def extract_plugin_config_version(config_data: Mapping[str, Any]) -> str:
    """提取插件配置中的版本号。

    Args:
        config_data: 插件配置字典。

    Returns:
        str: ``plugin.config_version`` 的规范化字符串值。

    Raises:
        PluginConfigVersionError: 当缺少 ``[plugin]`` 配置节或 ``config_version``
            字段为空时抛出。
    """

    plugin_section = config_data.get("plugin")
    if not isinstance(plugin_section, Mapping):
        raise PluginConfigVersionError(
            "插件配置文件缺少 [plugin] 配置节，且必须提供 plugin.config_version 版本号"
        )

    version_value = plugin_section.get("config_version")
    normalized_version = str(version_value or "").strip()
    if not normalized_version:
        raise PluginConfigVersionError(
            "插件配置文件缺少 plugin.config_version 版本号，当前版本策略不再兼容无版本配置"
        )
    return normalized_version


def rebuild_plugin_config_data(
    default_config: Mapping[str, Any],
    current_config: Mapping[str, Any],
) -> Dict[str, Any]:
    """基于默认结构重建插件配置。

    该方法用于版本升级场景：以最新默认配置为骨架，仅迁移仍然存在且类型匹配的旧字段值，
    从而达到“补齐新增字段、移除废弃字段、保留用户已有值”的效果。

    Args:
        default_config: 最新默认配置内容。
        current_config: 旧版本配置内容。

    Returns:
        Dict[str, Any]: 按最新结构重建后的配置字典。
    """

    rebuilt_config = _deep_copy_plugin_config_mapping(default_config)
    _overlay_plugin_config_fields(rebuilt_config, current_config)
    return rebuilt_config


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
        external_available_plugins: Optional[Dict[str, str]] = None,
        blocked_plugin_reasons: Optional[Dict[str, str]] = None,
        runner_group: str = "unknown",
        plugin_type_filter: str = "",
        trusted_plugin_dirs: Optional[List[str]] = None,
    ) -> None:
        """初始化 Runner。

        Args:
            host_address: Host 的 IPC 地址。
            session_token: 握手用会话令牌。
            plugin_dirs: 当前 Runner 负责扫描的插件目录列表。
            external_available_plugins: 视为已满足的外部依赖插件版本映射。
            blocked_plugin_reasons: 需要拒绝加载的插件及原因映射。
            runner_group: 当前 Runner 所属运行时分组名称。
            plugin_type_filter: manifest plugin_type 过滤模式。
            trusted_plugin_dirs: 过滤模式下始终信任的插件根目录。
        """
        self._host_address: str = host_address
        self._session_token: str = session_token
        self._plugin_dirs: List[str] = plugin_dirs
        self._runner_group: str = str(runner_group or "unknown").strip() or "unknown"
        self._external_available_plugins: Dict[str, str] = {
            str(plugin_id or "").strip(): str(plugin_version or "").strip()
            for plugin_id, plugin_version in (external_available_plugins or {}).items()
            if str(plugin_id or "").strip() and str(plugin_version or "").strip()
        }
        self._blocked_plugin_reasons: Dict[str, str] = {
            str(plugin_id or "").strip(): str(reason or "").strip()
            for plugin_id, reason in (blocked_plugin_reasons or {}).items()
            if str(plugin_id or "").strip() and str(reason or "").strip()
        }

        self._rpc_client: RPCClient = RPCClient(host_address, session_token)
        self._loader: PluginLoader = PluginLoader(
            host_version=os.getenv(ENV_HOST_VERSION, ""),
            plugin_type_filter=plugin_type_filter,
            trusted_plugin_dirs=trusted_plugin_dirs or [],
        )
        self._loader.set_blocked_plugin_reasons(self._blocked_plugin_reasons)
        self._start_time: float = time.monotonic()
        self._shutting_down: bool = False
        self._reload_lock: asyncio.Lock = asyncio.Lock()
        self._inflight_rpcs: Dict[int, InFlightRPC] = {}

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
        plugins = self._loader.discover_and_load(
            self._plugin_dirs,
            extra_available=self._external_available_plugins,
        )
        logger.debug(f"已加载 {len(plugins)} 个插件")

        # 4. 注入 PluginContext + 调用 on_load 生命周期钩子
        failed_plugin_reasons: Dict[str, str] = dict(self._loader.failed_plugins)
        failed_plugins: Set[str] = set(failed_plugin_reasons)
        inactive_plugins: Set[str] = set()
        available_plugin_versions: Dict[str, str] = dict(self._external_available_plugins)
        for meta in plugins:
            unsatisfied_dependencies = [
                dependency.id
                for dependency in meta.manifest.plugin_dependencies
                if dependency.id not in available_plugin_versions
                or not self._loader.manifest_validator.is_plugin_dependency_satisfied(
                    dependency,
                    available_plugin_versions[dependency.id],
                )
            ]
            if unsatisfied_dependencies:
                if any(dependency_id in inactive_plugins for dependency_id in unsatisfied_dependencies):
                    logger.info(
                        f"插件 {meta.plugin_id} 依赖的插件当前未激活，跳过本次启动: {', '.join(unsatisfied_dependencies)}"
                    )
                    inactive_plugins.add(meta.plugin_id)
                    continue
                failed_plugins.add(meta.plugin_id)
                failed_plugin_reasons[meta.plugin_id] = f"依赖未满足: {', '.join(unsatisfied_dependencies)}"
                continue

            activation_status = await self._activate_plugin(meta)
            if activation_status == PluginActivationStatus.LOADED:
                available_plugin_versions[meta.plugin_id] = meta.version
                continue
            if activation_status == PluginActivationStatus.INACTIVE:
                inactive_plugins.add(meta.plugin_id)
                continue
            failed_plugins.add(meta.plugin_id)
            failed_plugin_reasons[meta.plugin_id] = "插件初始化失败"

        successful_plugins = [
            meta.plugin_id
            for meta in plugins
            if meta.plugin_id not in failed_plugins and meta.plugin_id not in inactive_plugins
        ]
        await self._notify_ready(
            successful_plugins,
            sorted(failed_plugins),
            sorted(inactive_plugins),
            failed_plugin_reasons,
        )

        # 5. 等待直到收到关停信号
        with contextlib.suppress(asyncio.CancelledError):
            while not self._shutting_down:
                await asyncio.sleep(1.0)

        # 6. 卸载 IPC 日志 Handler 并刷空剩余缓冲，然后断开连接
        logger.debug("Runner 开始关停")
        await self._uninstall_log_handler()
        await self._rpc_client.disconnect()
        logger.debug("Runner 已退出")

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

    @staticmethod
    def _debug_file_path() -> Path:
        """返回独立的 Runner RPC 诊断文件路径。"""

        return _RUNNER_DEBUG_FILE_PATH.resolve()

    @staticmethod
    def _summarize_envelope_payload(payload: Any) -> Dict[str, Any]:
        """提取 RPC payload 的轻量摘要，避免把完整消息内容写入诊断文件。"""

        if not isinstance(payload, dict):
            return {"payload_type": type(payload).__name__}

        summary: Dict[str, Any] = {"payload_keys": sorted(str(key) for key in payload.keys())}
        component_name = str(payload.get("component_name") or "").strip()
        if component_name:
            summary["component_name"] = component_name

        args = payload.get("args")
        if isinstance(args, dict):
            summary["args_keys"] = sorted(str(key) for key in args.keys())
            summary["args_count"] = len(args)
        if client_type := str(payload.get("client_type") or "").strip():
            summary["client_type"] = client_type
        if operation := str(payload.get("operation") or "").strip():
            summary["operation"] = operation
        return summary

    def _write_debug_event_sync(self, event: str, payload: Dict[str, Any]) -> None:
        """把 Runner 诊断事件追加到独立 JSONL 文件。"""

        record = {
            "event": event,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "timestamp_epoch": time.time(),
            "pid": os.getpid(),
            "runner_group": self._runner_group,
            "plugin_dirs": list(self._plugin_dirs),
            **payload,
        }
        try:
            debug_file = self._debug_file_path()
            debug_file.parent.mkdir(parents=True, exist_ok=True)
            with open(debug_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception as exc:
            logger.warning(f"写入 Runner RPC 诊断文件失败: {exc}")

    async def _write_debug_event(self, event: str, payload: Dict[str, Any]) -> None:
        """异步追加 Runner 诊断事件，避免诊断文件 IO 阻塞事件循环。"""

        await asyncio.to_thread(self._write_debug_event_sync, event, payload)

    def _schedule_debug_event(self, event: str, payload: Dict[str, Any]) -> None:
        """调度一次诊断事件写入。"""

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._write_debug_event(event, payload))
        except RuntimeError:
            self._write_debug_event_sync(event, payload)

    def _start_inflight_rpc(self, envelope: Envelope, component_name: str = "") -> InFlightRPC:
        """登记一个正在执行的 Host -> Runner RPC。"""

        record = InFlightRPC(
            request_id=envelope.request_id,
            method=str(envelope.method or ""),
            plugin_id=str(envelope.plugin_id or ""),
            component_name=str(component_name or "").strip(),
            timeout_ms=int(envelope.timeout_ms or 0),
            started_at_epoch=time.time(),
            started_at_monotonic=time.monotonic(),
            payload_summary=self._summarize_envelope_payload(envelope.payload),
        )
        self._inflight_rpcs[record.request_id] = record
        return record

    def _finish_inflight_rpc(self, record: InFlightRPC) -> None:
        """注销已完成 RPC，并在超出 RPC timeout 时写诊断记录。"""

        self._inflight_rpcs.pop(record.request_id, None)
        duration_ms = int((time.monotonic() - record.started_at_monotonic) * 1000)
        timeout_ms = max(record.timeout_ms, 1)
        if duration_ms <= timeout_ms:
            return

        self._schedule_debug_event(
            "rpc_completed_after_timeout",
            {
                "request": self._inflight_record_to_dict(record),
                "duration_ms": duration_ms,
            },
        )

    def _inflight_record_to_dict(self, record: InFlightRPC) -> Dict[str, Any]:
        """序列化单个 in-flight RPC 记录。"""

        return {
            "request_id": record.request_id,
            "method": record.method,
            "plugin_id": record.plugin_id,
            "component_name": record.component_name,
            "timeout_ms": record.timeout_ms,
            "started_at_epoch": record.started_at_epoch,
            "duration_ms": int((time.monotonic() - record.started_at_monotonic) * 1000),
            "payload_summary": dict(record.payload_summary),
        }

    def _inflight_snapshot(self, min_duration_ms: int = 0) -> List[Dict[str, Any]]:
        """返回当前未完成 RPC 调用快照。"""

        records = [
            self._inflight_record_to_dict(record)
            for record in self._inflight_rpcs.values()
            if int((time.monotonic() - record.started_at_monotonic) * 1000) >= min_duration_ms
        ]
        records.sort(key=lambda item: int(item.get("duration_ms", 0)), reverse=True)
        return records

    async def _dump_inflight_debug(self, reason: str, *, min_duration_ms: int = 0) -> None:
        """将当前未完成 RPC 快照写入独立诊断文件。"""

        snapshot = self._inflight_snapshot(min_duration_ms=min_duration_ms)
        if not snapshot:
            return
        await self._write_debug_event(
            "inflight_rpc_snapshot",
            {
                "reason": reason,
                "inflight_count": len(snapshot),
                "inflight": snapshot,
            },
        )

    async def _invoke_plugin_callable(self, handler_method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """调用插件函数；同步函数放到线程中执行，避免阻塞 Runner 事件循环。"""

        if inspect.iscoroutinefunction(handler_method):
            return await handler_method(*args, **kwargs)

        result = await asyncio.to_thread(handler_method, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _inject_context(self, plugin_id: str, instance: object, plugin_dir: Optional[str] = None) -> None:
        """为插件实例创建并注入 PluginContext。

        对新版 MaiBotPlugin（具有 _set_context 方法）：创建 PluginContext 并注入。
        对旧版 LegacyPluginAdapter（具有 _set_context 方法，由兼容代理封装）：同上。
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
            timeout_ms: Optional[int] = None,
        ) -> Any:
            """桥接 PluginContext 的原始 RPC 调用到 Host。

            无论调用方传入何种 plugin_id，实际发往 Host 的 plugin_id
            始终绑定为当前插件实例，避免伪造其他插件身份申请能力。
            """
            if plugin_id and plugin_id != bound_plugin_id:
                logger.warning(f"插件 {bound_plugin_id} 尝试以 {plugin_id} 身份发起 RPC，已强制绑定回自身身份")
            normalized_method = str(method or "").strip()
            if normalized_method not in _PLUGIN_ALLOWED_RAW_HOST_METHODS:
                raise PermissionError(
                    f"插件 {bound_plugin_id} 不允许直接调用 Host 原始 RPC 方法: {normalized_method or '<empty>'}"
                )
            request_payload = dict(payload or {})
            if timeout_ms is None and normalized_method == "cap.call":
                capability_name = str(request_payload.get("capability") or "").strip()
                cap_args = request_payload.get("args")
                if isinstance(cap_args, dict):
                    if "rpc_timeout_ms" in cap_args:
                        timeout_ms = cap_args.pop("rpc_timeout_ms")
                    elif "_timeout_ms" in cap_args:
                        timeout_ms = cap_args.pop("_timeout_ms")
                    # 旧版 render.html2png 使用 timeout_ms 表示渲染业务超时，不能按 RPC 超时移除。
                    elif capability_name != "render.html2png" and "timeout_ms" in cap_args:
                        timeout_ms = cap_args.pop("timeout_ms")

            request_timeout_ms = 30000
            if timeout_ms is not None:
                try:
                    request_timeout_ms = int(timeout_ms)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"RPC timeout_ms 无效: {timeout_ms}") from exc
                if request_timeout_ms <= 0:
                    raise ValueError(f"RPC timeout_ms 必须大于 0: {timeout_ms}")

            resp = await rpc_client.send_request(
                method=normalized_method,
                plugin_id=bound_plugin_id,
                payload=request_payload,
                timeout_ms=request_timeout_ms,
            )
            if resp.error:
                raise RuntimeError(resp.error.get("message", "能力调用失败"))
            if normalized_method == "cap.call" and isinstance(resp.payload, dict) and "result" in resp.payload:
                return resp.payload.get("result")
            return resp.payload

        plugin_paths = build_plugin_paths(plugin_id, _PROJECT_ROOT)
        ctx = self._create_plugin_context(
            PluginContext,
            plugin_id=plugin_id,
            rpc_call=_rpc_call,
            plugin_paths=plugin_paths,
        )
        self._warn_legacy_plugin_data_dir(plugin_id, plugin_dir, plugin_paths.data_dir)
        self._ensure_context_llm_helpers(ctx)
        cast(_ContextAwarePlugin, instance)._set_context(ctx)
        logger.debug(f"已为插件 {plugin_id} 注入 PluginContext")

    @staticmethod
    def _create_plugin_context(
        context_class: Any,
        *,
        plugin_id: str,
        rpc_call: Callable[..., Any],
        plugin_paths: PluginPaths,
    ) -> Any:
        """创建 PluginContext，并兼容尚未正式支持 paths 参数的 SDK。"""

        context_signature = inspect.signature(context_class)
        if "paths" in context_signature.parameters:
            return context_class(plugin_id=plugin_id, rpc_call=rpc_call, paths=plugin_paths)

        ctx = context_class(plugin_id=plugin_id, rpc_call=rpc_call)
        ctx.paths = plugin_paths
        return ctx

    @staticmethod
    def _warn_legacy_plugin_data_dir(plugin_id: str, plugin_dir: Optional[str], data_dir: Path) -> None:
        """提示插件作者迁移旧式源码目录 data。"""

        if not plugin_dir:
            return

        legacy_data_dir = Path(plugin_dir) / "data"
        if not legacy_data_dir.exists():
            return

        logger.warning(
            f"插件 {plugin_id} 检测到旧式数据目录 {legacy_data_dir}，"
            f"建议迁移到统一持久化目录 {data_dir}"
        )

    @staticmethod
    def _ensure_context_llm_helpers(ctx: Any) -> None:
        """为旧版 SDK 的 LLM 代理补齐新增便捷方法。"""

        llm_proxy = getattr(ctx, "llm", None)
        if llm_proxy is None:
            return

        if not hasattr(llm_proxy, "embed"):
            async def _embed(
                text: str | None = None,
                texts: List[str] | None = None,
                task_name: str = "",
                model: str = "",
                model_name: str = "",
                max_concurrent: int | None = None,
                **kwargs: Any,
            ) -> Any:
                payload: Dict[str, Any] = dict(kwargs)
                if text is not None:
                    payload["text"] = text
                if texts is not None:
                    payload["texts"] = texts
                if task_name:
                    payload["task_name"] = task_name
                if model:
                    payload["model"] = model
                if model_name:
                    payload["model_name"] = model_name
                if max_concurrent is not None:
                    payload["max_concurrent"] = max_concurrent
                return await ctx.call_capability("llm.embed", **payload)

            llm_proxy.embed = _embed

        if hasattr(llm_proxy, "transcribe_audio"):
            return

        async def _transcribe_audio(
            audio_base64: str = "",
            voice_base64: str = "",
            task_name: str = "voice",
            model: str = "",
            model_name: str = "",
            **kwargs: Any,
        ) -> Any:
            payload: Dict[str, Any] = dict(kwargs)
            if audio_base64:
                payload["audio_base64"] = audio_base64
            if voice_base64:
                payload["voice_base64"] = voice_base64
            if task_name:
                payload["task_name"] = task_name
            if model:
                payload["model"] = model
            if model_name:
                payload["model_name"] = model_name
            return await ctx.call_capability("llm.transcribe_audio", **payload)

        llm_proxy.transcribe_audio = _transcribe_audio

    def _apply_plugin_config(self, meta: PluginMeta, config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """在 Runner 侧为插件实例注入当前插件配置。

        Args:
            meta: 插件元数据。
            config_data: 可选的配置数据；留空时自动从插件目录读取。

        Returns:
            Dict[str, Any]: 归一化后的当前插件配置。
        """
        instance = meta.instance
        default_config = self._get_plugin_default_config(instance)
        raw_config = config_data if config_data is not None else self._load_plugin_config(meta.plugin_dir, meta.plugin_id)
        normalization_result = self._normalize_plugin_config(
            instance,
            raw_config,
            default_config=default_config,
            suppress_errors=False,
            enforce_version=True,
        )
        plugin_config = normalization_result.normalized_config
        config_path = Path(meta.plugin_dir) / "config.toml"
        should_initialize_file = not config_path.exists() and bool(default_config)
        if normalization_result.should_persist or should_initialize_file:
            self._save_plugin_config(meta.plugin_dir, plugin_config)
        if hasattr(instance, "set_plugin_config"):
            try:
                cast(_ConfigAwarePlugin, instance).set_plugin_config(plugin_config)
            except Exception as exc:
                logger.warning(f"插件 {meta.plugin_id} 配置注入失败: {exc}")
        return plugin_config

    def _normalize_plugin_config(
        self,
        instance: object,
        config_data: Optional[Dict[str, Any]],
        *,
        default_config: Optional[Dict[str, Any]] = None,
        suppress_errors: bool = True,
        enforce_version: bool = True,
    ) -> PluginConfigNormalizationResult:
        """对插件配置做统一归一化处理。

        Args:
            instance: 插件实例。
            config_data: 原始配置数据。
            default_config: 插件声明的默认配置。
            suppress_errors: 是否在归一化失败时吞掉异常并回退原始配置。
            enforce_version: 是否强制执行 ``plugin.config_version`` 版本检查。

        Returns:
            PluginConfigNormalizationResult: 归一化结果、是否发生变更以及是否应写回文件。
        """

        raw_config = dict(config_data or {})
        latest_default_config = default_config if default_config is not None else self._get_plugin_default_config(instance)
        config_for_normalize = rebuild_plugin_config_data(raw_config, {})
        should_persist = False

        try:
            if latest_default_config:
                if enforce_version:
                    config_for_normalize, should_persist = self._prepare_plugin_config_for_version_update(
                        raw_config=raw_config,
                        default_config=latest_default_config,
                    )
                elif not raw_config:
                    config_for_normalize = rebuild_plugin_config_data(latest_default_config, {})
        except Exception as exc:
            if not suppress_errors:
                raise
            logger.warning(f"插件配置版本检查失败，将回退为原始配置: {exc}")
            return PluginConfigNormalizationResult(
                normalized_config=raw_config,
                changed=False,
                should_persist=False,
            )

        if not hasattr(instance, "normalize_plugin_config"):
            return PluginConfigNormalizationResult(
                normalized_config=config_for_normalize,
                changed=config_for_normalize != raw_config,
                should_persist=should_persist,
            )

        try:
            normalized_config, normalized_changed = cast(_ConfigAwarePlugin, instance).normalize_plugin_config(
                config_for_normalize
            )
        except Exception as exc:
            if not suppress_errors:
                raise
            logger.warning(f"插件配置归一化失败，将回退为原始配置: {exc}")
            return PluginConfigNormalizationResult(
                normalized_config=raw_config,
                changed=False,
                should_persist=False,
            )

        return PluginConfigNormalizationResult(
            normalized_config=normalized_config,
            changed=normalized_changed or normalized_config != raw_config,
            should_persist=should_persist,
        )

    @staticmethod
    def _prepare_plugin_config_for_version_update(
        raw_config: Mapping[str, Any],
        default_config: Mapping[str, Any],
    ) -> Tuple[Dict[str, Any], bool]:
        """基于配置版本决定是否需要重建插件配置。

        Args:
            raw_config: 当前磁盘上的插件配置。
            default_config: 插件最新默认配置。

        Returns:
            Tuple[Dict[str, Any], bool]: 用于后续归一化的配置副本，以及是否需要写回文件。

        Raises:
            PluginConfigVersionError: 当默认配置或当前配置缺少版本号时抛出。
        """

        if not default_config:
            return rebuild_plugin_config_data(raw_config, {}), False

        latest_version = extract_plugin_config_version(default_config)
        if not raw_config:
            return rebuild_plugin_config_data(default_config, {}), False

        current_version = extract_plugin_config_version(raw_config)
        if compare_versions(current_version, latest_version):
            logger.info(f"检测到插件配置版本升级: {current_version} -> {latest_version}")
            return rebuild_plugin_config_data(default_config, raw_config), True

        return rebuild_plugin_config_data(raw_config, {}), False

    @staticmethod
    def _merge_plugin_config_document(target: Any, source: Any) -> None:
        """递归更新现有 TOML 文档，尽量保留原注释与格式。

        这里采用“更新已有键、补充缺失键”的策略，而不是直接整体重写，
        这样插件启动时因补齐默认配置触发落盘时，可以尽量保留用户手写的注释。

        Args:
            target: 现有的 TOML 文档或表对象。
            source: 最新的配置字典。
        """

        if isinstance(source, list) or not isinstance(source, dict) or not isinstance(target, dict):
            return

        for key, value in source.items():
            if key in target:
                target_value = target[key]
                if isinstance(value, dict) and isinstance(target_value, dict):
                    PluginRunner._merge_plugin_config_document(target_value, value)
                else:
                    try:
                        target[key] = tomlkit.item(value)
                    except (TypeError, ValueError):
                        target[key] = value
            else:
                try:
                    target[key] = tomlkit.item(value)
                except (TypeError, ValueError):
                    target[key] = value

    @staticmethod
    def _has_extra_config_keys(existing_config: Any, latest_config: Any) -> bool:
        """判断现有配置中是否包含新配置不存在的键。

        如果插件归一化后的结果删除了某些旧键，就需要回退到完整重写，
        否则仅做增量合并会把旧键残留在文件里。

        Args:
            existing_config: 现有配置字典。
            latest_config: 最新配置字典。

        Returns:
            bool: 是否存在需要通过整文件重写才能删除的旧键。
        """

        if not isinstance(existing_config, dict) or not isinstance(latest_config, dict):
            return False

        for key, existing_value in existing_config.items():
            if key not in latest_config:
                return True
            if PluginRunner._has_extra_config_keys(existing_value, latest_config[key]):
                return True
        return False

    @staticmethod
    def _is_plugin_enabled(config_data: Optional[Mapping[str, Any]]) -> bool:
        """根据配置内容判断插件是否应被视为启用。

        Args:
            config_data: 当前插件配置。

        Returns:
            bool: 插件是否启用。
        """

        if not isinstance(config_data, Mapping):
            return True

        plugin_section = config_data.get("plugin")
        if not isinstance(plugin_section, Mapping):
            return True

        enabled_value = plugin_section.get("enabled", True)
        if isinstance(enabled_value, str):
            normalized_value = enabled_value.strip().lower()
            if normalized_value in {"0", "false", "no", "off"}:
                return False
            if normalized_value in {"1", "true", "yes", "on"}:
                return True
        return bool(enabled_value)

    @staticmethod
    def _save_plugin_config(plugin_dir: str, config_data: Dict[str, Any]) -> None:
        """将插件配置写回到 ``config.toml``。

        Args:
            plugin_dir: 插件目录。
            config_data: 需要写回的配置字典。
        """

        config_path = Path(plugin_dir) / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            try:
                with config_path.open("r", encoding="utf-8") as handle:
                    existing_document = tomlkit.load(handle)
                existing_config = existing_document.unwrap()
                if not PluginRunner._has_extra_config_keys(existing_config, config_data):
                    PluginRunner._merge_plugin_config_document(existing_document, config_data)
                    with config_path.open("w", encoding="utf-8") as handle:
                        handle.write(tomlkit.dumps(existing_document))
                    return
            except Exception as exc:
                logger.warning(f"保留插件配置注释失败，将回退为整文件重写: {config_path}: {exc}")

        with config_path.open("w", encoding="utf-8") as handle:
            handle.write(tomlkit.dumps(config_data))

    @staticmethod
    def _load_plugin_config(plugin_dir: str, plugin_id: str = "") -> Dict[str, Any]:
        """从插件目录读取 config.toml。"""
        _ = plugin_id
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

    def _resolve_plugin_candidate(self, plugin_id: str) -> Tuple[Optional[PluginCandidate], Optional[str]]:
        """解析指定插件的候选目录。

        Args:
            plugin_id: 目标插件 ID。

        Returns:
            Tuple[Optional[PluginCandidate], Optional[str]]: 候选插件与错误信息。
        """

        candidates, duplicate_candidates = self._loader.discover_candidates(self._plugin_dirs)
        if plugin_id in duplicate_candidates:
            conflict_paths = ", ".join(str(path) for path in duplicate_candidates[plugin_id])
            return None, f"检测到重复插件 ID: {conflict_paths}"

        candidate = candidates.get(plugin_id)
        if candidate is None:
            return None, f"未找到插件: {plugin_id}"
        return candidate, None

    def _resolve_plugin_meta_for_config_request(
        self,
        plugin_id: str,
    ) -> Tuple[Optional[PluginMeta], bool, Optional[str]]:
        """为配置相关请求解析插件元数据。

        Args:
            plugin_id: 目标插件 ID。

        Returns:
            Tuple[Optional[PluginMeta], bool, Optional[str]]: 依次为插件元数据、
            是否为临时冷加载实例、以及错误信息。
        """

        loaded_meta = self._loader.get_plugin(plugin_id)
        if loaded_meta is not None:
            return loaded_meta, False, None

        candidate, error_message = self._resolve_plugin_candidate(plugin_id)
        if candidate is None:
            return None, False, error_message

        try:
            meta = self._loader.load_candidate(plugin_id, candidate)
        except Exception as exc:
            return None, False, str(exc)
        if meta is None:
            return None, False, "插件模块加载失败"
        return meta, True, None

    def _inspect_plugin_config(
        self,
        meta: PluginMeta,
        *,
        config_data: Optional[Dict[str, Any]] = None,
        use_provided_config: bool = False,
        suppress_errors: bool = True,
        enforce_version: bool = True,
    ) -> InspectPluginConfigResultPayload:
        """解析插件代码定义的配置元数据。

        Args:
            meta: 插件元数据。
            config_data: 可选的配置内容。
            use_provided_config: 是否优先使用传入的配置内容。
            suppress_errors: 是否在归一化失败时回退原始配置。
            enforce_version: 是否强制校验 ``plugin.config_version``。

        Returns:
            InspectPluginConfigResultPayload: 结构化解析结果。
        """

        raw_config = config_data if use_provided_config else self._load_plugin_config(meta.plugin_dir)
        if use_provided_config and config_data is None:
            raw_config = {}

        default_config = self._get_plugin_default_config(meta.instance)
        normalization_result = self._normalize_plugin_config(
            meta.instance,
            raw_config,
            default_config=default_config,
            suppress_errors=suppress_errors,
            enforce_version=enforce_version,
        )
        normalized_config = normalization_result.normalized_config
        changed = normalization_result.changed
        if not normalized_config and not raw_config and default_config:
            normalized_config = rebuild_plugin_config_data(default_config, {})
            changed = True

        return InspectPluginConfigResultPayload(
            success=True,
            default_config=default_config,
            config_schema=self._get_plugin_config_schema(meta),
            normalized_config=normalized_config,
            changed=changed,
            enabled=self._is_plugin_enabled(normalized_config),
        )

    def _register_handlers(self) -> None:
        """注册 Host -> Runner 的方法处理器。"""
        self._rpc_client.register_method("plugin.invoke_command", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_action", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_api", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_tool", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_message_gateway", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_llm_provider", self._handle_llm_provider_invoke)
        self._rpc_client.register_method("plugin.emit_event", self._handle_event_invoke)
        self._rpc_client.register_method("plugin.invoke_hook", self._handle_hook_invoke)
        self._rpc_client.register_method("plugin.health", self._handle_health)
        self._rpc_client.register_method("plugin.prepare_shutdown", self._handle_prepare_shutdown)
        self._rpc_client.register_method("plugin.shutdown", self._handle_shutdown)
        self._rpc_client.register_method("plugin.config_updated", self._handle_config_updated)
        self._rpc_client.register_method("plugin.inspect_config", self._handle_inspect_plugin_config)
        self._rpc_client.register_method("plugin.validate_config", self._handle_validate_plugin_config)
        self._rpc_client.register_method("plugin.reload", self._handle_reload_plugin)
        self._rpc_client.register_method("plugin.reload_batch", self._handle_reload_plugins)
        self._rpc_client.register_method("plugin.unload_batch", self._handle_unload_plugins)

    @staticmethod
    def _resolve_component_handler_name(meta: PluginMeta, component_name: str) -> str:
        """解析组件名对应的真实处理函数名。

        Args:
            meta: 已加载插件的元数据。
            component_name: Host 侧请求中的组件声明名。

        Returns:
            str: 实际应在插件实例上查找的方法名。
        """
        return str(meta.component_handlers.get(component_name, component_name) or component_name)

    def _resolve_component_handler(self, meta: PluginMeta, component_name: str) -> Any:
        """根据组件声明名解析插件实例上的可调用处理函数。

        Args:
            meta: 已加载插件的元数据。
            component_name: Host 侧请求中的组件声明名。

        Returns:
            Any: 解析到的可调用对象；未找到时返回 ``None``。
        """
        instance = meta.instance
        handler_name = self._resolve_component_handler_name(meta, component_name)
        handler_method = getattr(instance, handler_name, None)
        if handler_method is not None:
            return handler_method

        if handler_name != component_name:
            legacy_style_handler = getattr(instance, f"handle_{component_name}", None)
            if legacy_style_handler is not None:
                return legacy_style_handler

        prefixed_handler = getattr(instance, f"handle_{component_name}", None)
        if prefixed_handler is not None:
            return prefixed_handler
        return getattr(instance, component_name, None)

    async def _bootstrap_plugin(self, meta: PluginMeta, capabilities_required: Optional[List[str]] = None) -> bool:
        """向 Host 同步插件 bootstrap 能力令牌。"""
        payload = BootstrapPluginPayload(
            plugin_id=meta.plugin_id,
            plugin_version=meta.version,
            plugin_type=meta.plugin_type if capabilities_required is None or capabilities_required else "extension",
            capabilities_required=capabilities_required
            if capabilities_required is not None
            else list(meta.capabilities_required or []),
        )

        try:
            response = await self._rpc_client.send_request(
                "plugin.bootstrap",
                plugin_id=meta.plugin_id,
                payload=payload.model_dump(),
                timeout_ms=10000,
            )
            if response.error:
                raise RuntimeError(response.error.get("message", "插件 bootstrap 失败"))
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
        llm_providers: List[LLMProviderDeclaration] = []
        config_reload_subscriptions: List[str] = []
        instance = meta.instance

        # 从插件实例获取组件声明（SDK 插件须实现 get_components 方法）
        if hasattr(instance, "get_components"):
            meta.component_handlers.clear()
            for comp_info in instance.get_components():
                if not isinstance(comp_info, dict):
                    continue

                component_name = str(comp_info.get("name", "") or "").strip()
                raw_metadata = comp_info.get("metadata", {})
                component_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

                if component_name:
                    handler_name = str(component_metadata.get("handler_name", component_name) or component_name).strip()
                    meta.component_handlers[component_name] = handler_name or component_name

                components.append(
                    ComponentDeclaration(
                        name=component_name,
                        component_type=str(comp_info.get("type", "") or "").strip(),
                        plugin_id=meta.plugin_id,
                        chat_scope=str(comp_info.get("chat_scope", "all") or "all").strip(),
                        allowed_session=[
                            str(item).strip()
                            for item in comp_info.get("allowed_session", [])
                            if str(item).strip()
                        ]
                        if isinstance(comp_info.get("allowed_session"), list)
                        else [],
                        metadata=component_metadata,
                    )
                )
        if hasattr(instance, "get_config_reload_subscriptions"):
            config_reload_subscriptions = list(instance.get_config_reload_subscriptions())
        if hasattr(instance, "get_llm_providers"):
            meta.llm_provider_handlers.clear()
            for provider_info in instance.get_llm_providers():
                if not isinstance(provider_info, dict):
                    continue

                client_type = str(provider_info.get("client_type", "") or "").strip()
                raw_metadata = provider_info.get("metadata", {})
                provider_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                if client_type:
                    handler_name = str(provider_metadata.get("handler_name", client_type) or client_type).strip()
                    meta.llm_provider_handlers[client_type] = handler_name or client_type

                llm_providers.append(
                    LLMProviderDeclaration(
                        client_type=client_type,
                        name=str(provider_info.get("name", "") or "").strip(),
                        description=str(provider_info.get("description", "") or "").strip(),
                        version=str(provider_info.get("version", "1.0.0") or "1.0.0").strip() or "1.0.0",
                        metadata=provider_metadata,
                    )
                )

        declared_client_types = sorted(meta.manifest.llm_provider_client_types)
        registered_client_types = sorted(provider.client_type for provider in llm_providers)
        if declared_client_types != registered_client_types:
            logger.error(
                f"插件 {meta.plugin_id} LLM Provider 声明不一致: "
                f"manifest={declared_client_types}, code={registered_client_types}"
            )
            return False

        reg_payload = RegisterPluginPayload(
            plugin_id=meta.plugin_id,
            plugin_version=meta.version,
            plugin_type=meta.plugin_type,
            components=components,
            llm_providers=llm_providers,
            capabilities_required=meta.capabilities_required,
            dependencies=meta.dependencies,
            config_reload_subscriptions=config_reload_subscriptions,
            default_config=self._get_plugin_default_config(instance),
            config_schema=self._get_plugin_config_schema(meta),
        )

        try:
            response = await self._rpc_client.send_request(
                "plugin.register_components",
                plugin_id=meta.plugin_id,
                payload=reg_payload.model_dump(),
                timeout_ms=10000,
            )
            if response.error:
                raise RuntimeError(response.error.get("message", "插件注册失败"))
            response_payload = response.payload if isinstance(response.payload, dict) else {}
            if not bool(response_payload.get("accepted", True)):
                raise RuntimeError(str(response_payload.get("reason", "插件注册失败")))
            logger.debug(f"插件 {meta.plugin_id} 注册完成")
            return True
        except Exception as e:
            logger.error(f"插件 {meta.plugin_id} 注册失败: {e}")
            return False

    @staticmethod
    def _get_plugin_default_config(instance: object) -> Dict[str, Any]:
        """获取插件默认配置。

        Args:
            instance: 插件实例。

        Returns:
            Dict[str, Any]: 默认配置；插件未声明时返回空字典。
        """

        if not hasattr(instance, "get_default_config"):
            return {}
        try:
            default_config = cast(_ConfigAwarePlugin, instance).get_default_config()
        except Exception as exc:
            logger.warning(f"读取插件默认配置失败: {exc}")
            return {}
        return default_config if isinstance(default_config, dict) else {}

    @staticmethod
    def _get_plugin_config_schema(meta: PluginMeta) -> Dict[str, Any]:
        """获取插件 WebUI 配置 Schema。

        Args:
            meta: 插件元数据。

        Returns:
            Dict[str, Any]: 插件配置 Schema；插件未声明时返回空字典。
        """

        instance = meta.instance
        if not hasattr(instance, "get_webui_config_schema"):
            return {}
        try:
            schema = cast(_ConfigAwarePlugin, instance).get_webui_config_schema(
                plugin_id=meta.plugin_id,
                plugin_name=meta.manifest.name,
                plugin_version=meta.version,
                plugin_description=meta.manifest.description,
                plugin_author=meta.manifest.author.name,
            )
        except Exception as exc:
            logger.warning(f"构造插件配置 Schema 失败: {exc}")
            return {}
        return schema if isinstance(schema, dict) else {}

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
            await self._invoke_plugin_callable(instance.on_load)
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
            await self._invoke_plugin_callable(instance.on_unload)
        except Exception as exc:
            logger.error(f"插件 {meta.plugin_id} on_unload 失败: {exc}", exc_info=True)

    async def _activate_plugin(self, meta: PluginMeta) -> PluginActivationStatus:
        """完成插件注入、授权、生命周期和组件注册。

        Args:
            meta: 待激活的插件元数据。

        Returns:
            PluginActivationStatus: 插件激活结果。
        """
        self._inject_context(meta.plugin_id, meta.instance, meta.plugin_dir)
        try:
            plugin_config = self._apply_plugin_config(meta)
        except PluginConfigVersionError as exc:
            logger.error(f"插件 {meta.plugin_id} 配置版本非法: {exc}")
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return PluginActivationStatus.FAILED
        except Exception as exc:
            logger.error(f"插件 {meta.plugin_id} 配置加载失败: {exc}", exc_info=True)
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return PluginActivationStatus.FAILED
        if not self._is_plugin_enabled(plugin_config):
            logger.debug(f"插件 {meta.plugin_id} 已在配置中禁用，跳过激活")
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return PluginActivationStatus.INACTIVE

        if not await self._bootstrap_plugin(meta):
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return PluginActivationStatus.FAILED

        if not await self._register_plugin(meta):
            await self._invoke_plugin_on_unload(meta)
            await self._deactivate_plugin(meta)
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return PluginActivationStatus.FAILED

        if not await self._invoke_plugin_on_load(meta):
            await self._unregister_plugin(meta.plugin_id, reason="on_load_failed")
            await self._deactivate_plugin(meta)
            self._loader.purge_plugin_modules(meta.plugin_id, meta.plugin_dir)
            return PluginActivationStatus.FAILED

        self._loader.set_loaded_plugin(meta)
        return PluginActivationStatus.LOADED

    async def _unload_plugin(self, meta: PluginMeta, reason: str, *, purge_modules: bool = True) -> None:
        """卸载单个插件并清理 Host/Runner 两侧状态。

        Args:
            meta: 待卸载的插件元数据。
            reason: 卸载原因。
            purge_modules: 是否在卸载完成后清理插件模块缓存。
        """
        await self._invoke_plugin_on_unload(meta)
        await self._unregister_plugin(meta.plugin_id, reason)
        await self._deactivate_plugin(meta)
        self._loader.remove_loaded_plugin(meta.plugin_id)
        if purge_modules:
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

    def _collect_reverse_dependents_for_roots(self, plugin_ids: Set[str]) -> Set[str]:
        """收集多个根插件对应的反向依赖并集。

        Args:
            plugin_ids: 根插件 ID 集合。

        Returns:
            Set[str]: 所有根插件及其反向依赖并集。
        """

        impacted_plugins: Set[str] = set()
        for plugin_id in sorted(plugin_ids):
            impacted_plugins.update(self._collect_reverse_dependents(plugin_id))
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

        indegree: Dict[str, int] = {
            plugin_id: len(dependencies) for plugin_id, dependencies in dependency_graph.items()
        }
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

    @staticmethod
    def _normalize_requested_plugin_ids(plugin_ids: List[str]) -> List[str]:
        """规范化批量重载请求中的插件 ID 列表。"""

        normalized_plugin_ids: List[str] = []
        seen_plugin_ids: Set[str] = set()
        for plugin_id in plugin_ids:
            normalized_plugin_id = str(plugin_id or "").strip()
            if not normalized_plugin_id or normalized_plugin_id in seen_plugin_ids:
                continue
            seen_plugin_ids.add(normalized_plugin_id)
            normalized_plugin_ids.append(normalized_plugin_id)
        return normalized_plugin_ids

    @staticmethod
    def _finalize_failed_reload_messages(
        failed_plugins: Dict[str, str],
        rollback_failures: Dict[str, str],
    ) -> Dict[str, str]:
        """在重载失败后补充回滚结果说明。"""

        finalized_failures: Dict[str, str] = {}
        for failed_plugin_id, failure_reason in failed_plugins.items():
            rollback_failure = rollback_failures.get(failed_plugin_id)
            if rollback_failure:
                finalized_failures[failed_plugin_id] = f"{failure_reason}；且旧版本恢复失败: {rollback_failure}"
            else:
                finalized_failures[failed_plugin_id] = f"{failure_reason}（已恢复旧版本）"

        for failed_plugin_id, rollback_failure in rollback_failures.items():
            if failed_plugin_id not in finalized_failures:
                finalized_failures[failed_plugin_id] = f"旧版本恢复失败: {rollback_failure}"

        return finalized_failures

    async def _reload_plugin_by_id(
        self,
        plugin_id: str,
        reason: str,
        external_available_plugins: Optional[Dict[str, str]] = None,
    ) -> ReloadPluginResultPayload:
        """按插件 ID 在 Runner 进程内执行精确重载。

        Args:
            plugin_id: 目标插件 ID。
            reason: 重载原因。
            external_available_plugins: 视为已满足的外部依赖插件版本映射。

        Returns:
            ReloadPluginResultPayload: 结构化重载结果。
        """
        batch_result = await self._reload_plugins_by_ids(
            [plugin_id],
            reason,
            external_available_plugins=external_available_plugins,
        )
        return ReloadPluginResultPayload(
            success=batch_result.success,
            requested_plugin_id=plugin_id,
            reloaded_plugins=batch_result.reloaded_plugins,
            unloaded_plugins=batch_result.unloaded_plugins,
            inactive_plugins=batch_result.inactive_plugins,
            failed_plugins=batch_result.failed_plugins,
        )

    async def _unload_plugins_by_ids(
        self,
        plugin_ids: List[str],
        reason: str,
    ) -> UnloadPluginsResultPayload:
        """按依赖安全顺序批量卸载当前已加载的插件。"""

        normalized_plugin_ids = self._normalize_requested_plugin_ids(plugin_ids)
        loaded_plugin_ids = set(self._loader.list_plugins())
        failed_plugins = {
            plugin_id: "插件当前未加载"
            for plugin_id in normalized_plugin_ids
            if plugin_id not in loaded_plugin_ids
        }
        unload_order = self._build_unload_order(set(normalized_plugin_ids) & loaded_plugin_ids)
        unloaded_plugins: List[str] = []

        for plugin_id in unload_order:
            meta = self._loader.get_plugin(plugin_id)
            if meta is None:
                failed_plugins[plugin_id] = "无法获取已加载插件元数据"
                continue
            try:
                await self._unload_plugin(meta, reason=reason)
            except Exception as exc:
                failed_plugins[plugin_id] = str(exc)
                logger.error(f"卸载插件 {plugin_id} 失败: {exc}", exc_info=True)
                continue
            unloaded_plugins.append(plugin_id)

        return UnloadPluginsResultPayload(
            success=not failed_plugins and len(unloaded_plugins) == len(normalized_plugin_ids),
            requested_plugin_ids=normalized_plugin_ids,
            unloaded_plugins=unloaded_plugins,
            failed_plugins=failed_plugins,
        )

    async def _reload_plugins_by_ids(
        self,
        plugin_ids: List[str],
        reason: str,
        external_available_plugins: Optional[Dict[str, str]] = None,
    ) -> ReloadPluginsResultPayload:
        """按插件 ID 列表在 Runner 进程内执行一次批量重载。"""

        normalized_plugin_ids = self._normalize_requested_plugin_ids(plugin_ids)
        if not normalized_plugin_ids:
            return ReloadPluginsResultPayload(success=True, requested_plugin_ids=[])

        candidates, duplicate_candidates = self._loader.discover_candidates(self._plugin_dirs)
        failed_plugins: Dict[str, str] = {}
        normalized_external_available = {
            str(candidate_plugin_id or "").strip(): str(candidate_plugin_version or "").strip()
            for candidate_plugin_id, candidate_plugin_version in (external_available_plugins or {}).items()
            if str(candidate_plugin_id or "").strip() and str(candidate_plugin_version or "").strip()
        }

        loaded_plugin_ids = set(self._loader.list_plugins())
        reload_root_ids: Set[str] = set()
        for plugin_id in normalized_plugin_ids:
            if plugin_id in duplicate_candidates:
                conflict_paths = ", ".join(str(path) for path in duplicate_candidates[plugin_id])
                failed_plugins[plugin_id] = f"检测到重复插件 ID: {conflict_paths}"
                continue

            plugin_is_loaded = plugin_id in loaded_plugin_ids
            plugin_has_candidate = plugin_id in candidates
            if not plugin_is_loaded and not plugin_has_candidate:
                failed_plugins[plugin_id] = "插件不存在或未找到合法的 manifest/plugin.py"
                continue

            reload_root_ids.add(plugin_id)

        if not reload_root_ids:
            return ReloadPluginsResultPayload(
                success=False,
                requested_plugin_ids=normalized_plugin_ids,
                failed_plugins=failed_plugins,
            )

        target_plugin_ids: Set[str] = {plugin_id for plugin_id in reload_root_ids if plugin_id not in loaded_plugin_ids}
        if loaded_root_plugin_ids := reload_root_ids & loaded_plugin_ids:
            target_plugin_ids.update(self._collect_reverse_dependents_for_roots(loaded_root_plugin_ids))

        unload_order = self._build_unload_order(target_plugin_ids & loaded_plugin_ids)
        unloaded_plugins: List[str] = []
        retained_plugin_ids = loaded_plugin_ids - set(unload_order)
        rollback_metas: Dict[str, PluginMeta] = {}

        for unload_plugin_id in unload_order:
            meta = self._loader.get_plugin(unload_plugin_id)
            if meta is None:
                continue
            rollback_metas[unload_plugin_id] = meta
            await self._unload_plugin(meta, reason=reason, purge_modules=False)
            self._loader.purge_plugin_modules(unload_plugin_id, meta.plugin_dir)
            unloaded_plugins.append(unload_plugin_id)

        reload_candidates: Dict[str, PluginCandidate] = {}
        for target_plugin_id in target_plugin_ids:
            candidate = candidates.get(target_plugin_id)
            if candidate is None:
                failed_plugins[target_plugin_id] = "插件目录已不存在"
                continue
            reload_candidates[target_plugin_id] = candidate

        load_order, dependency_failures = self._loader.resolve_dependencies(
            reload_candidates,
            extra_available={
                **normalized_external_available,
                **{
                    retained_plugin_id: retained_meta.version
                    for retained_plugin_id in retained_plugin_ids
                    if (retained_meta := self._loader.get_plugin(retained_plugin_id)) is not None
                },
            },
        )
        failed_plugins.update(dependency_failures)

        available_plugins = {
            **normalized_external_available,
            **{
                retained_plugin_id: retained_meta.version
                for retained_plugin_id in retained_plugin_ids
                if (retained_meta := self._loader.get_plugin(retained_plugin_id)) is not None
            },
        }
        reloaded_plugins: List[str] = []
        inactive_plugins: List[str] = []
        inactive_plugin_ids: Set[str] = set()

        for load_plugin_id in load_order:
            if load_plugin_id in failed_plugins:
                continue

            candidate = reload_candidates.get(load_plugin_id)
            if candidate is None:
                continue

            _, manifest, _ = candidate
            unsatisfied_dependency_ids = [
                dependency.id
                for dependency in manifest.plugin_dependencies
                if dependency.id not in available_plugins
                or not self._loader.manifest_validator.is_plugin_dependency_satisfied(
                    dependency,
                    available_plugins[dependency.id],
                )
            ]
            if unsatisfied_dependencies := self._loader.manifest_validator.get_unsatisfied_plugin_dependencies(
                manifest,
                available_plugin_versions=available_plugins,
            ):
                if load_plugin_id not in reload_root_ids and any(
                    dependency_id in inactive_plugin_ids for dependency_id in unsatisfied_dependency_ids
                ):
                    logger.info(
                        f"插件 {load_plugin_id} 的依赖当前未激活，保留为未激活状态: {', '.join(unsatisfied_dependencies)}"
                    )
                    inactive_plugin_ids.add(load_plugin_id)
                    inactive_plugins.append(load_plugin_id)
                    continue
                failed_plugins[load_plugin_id] = f"依赖未满足: {', '.join(unsatisfied_dependencies)}"
                continue

            meta = self._loader.load_candidate(load_plugin_id, candidate)
            if meta is None:
                failed_plugins[load_plugin_id] = "插件模块加载失败"
                continue

            activated = await self._activate_plugin(meta)
            if activated == PluginActivationStatus.FAILED:
                failed_plugins[load_plugin_id] = "插件初始化失败"
                continue
            if activated == PluginActivationStatus.INACTIVE:
                inactive_plugin_ids.add(load_plugin_id)
                inactive_plugins.append(load_plugin_id)
                continue

            available_plugins[load_plugin_id] = meta.version
            reloaded_plugins.append(load_plugin_id)

        if failed_plugins:
            rollback_failures: Dict[str, str] = {}

            for reloaded_plugin_id in reversed(reloaded_plugins):
                reloaded_meta = self._loader.get_plugin(reloaded_plugin_id)
                if reloaded_meta is None:
                    continue

                try:
                    await self._unload_plugin(
                        reloaded_meta,
                        reason=f"{reason}_rollback_cleanup",
                        purge_modules=False,
                    )
                except Exception as exc:
                    rollback_failures[reloaded_plugin_id] = f"清理失败: {exc}"
                finally:
                    self._loader.purge_plugin_modules(reloaded_plugin_id, reloaded_meta.plugin_dir)

            for rollback_plugin_id in reversed(unload_order):
                rollback_meta = rollback_metas.get(rollback_plugin_id)
                if rollback_meta is None:
                    continue

                try:
                    restored = await self._activate_plugin(rollback_meta)
                except Exception as exc:
                    rollback_failures[rollback_plugin_id] = str(exc)
                    continue

                if restored != PluginActivationStatus.LOADED:
                    rollback_failures[rollback_plugin_id] = "无法重新激活旧版本"

            return ReloadPluginsResultPayload(
                success=False,
                requested_plugin_ids=normalized_plugin_ids,
                reloaded_plugins=[],
                unloaded_plugins=unloaded_plugins,
                inactive_plugins=[],
                failed_plugins=self._finalize_failed_reload_messages(failed_plugins, rollback_failures),
            )

        requested_plugin_success = all(
            plugin_id in reloaded_plugins or plugin_id in inactive_plugins for plugin_id in reload_root_ids
        )

        return ReloadPluginsResultPayload(
            success=requested_plugin_success and not failed_plugins,
            requested_plugin_ids=normalized_plugin_ids,
            reloaded_plugins=reloaded_plugins,
            unloaded_plugins=unloaded_plugins,
            inactive_plugins=inactive_plugins,
            failed_plugins=failed_plugins,
        )

    async def _notify_ready(
        self,
        loaded_plugins: List[str],
        failed_plugins: List[str],
        inactive_plugins: List[str],
        failed_plugin_reasons: Dict[str, str] | None = None,
    ) -> None:
        """通知 Host 当前 Runner 已完成插件初始化。

        Args:
            loaded_plugins: 成功初始化的插件列表。
            failed_plugins: 初始化失败的插件列表。
            inactive_plugins: 因禁用或依赖不可用而未激活的插件列表。
            failed_plugin_reasons: 初始化失败的插件及原因。
        """
        payload = RunnerReadyPayload(
            loaded_plugins=loaded_plugins,
            failed_plugins=failed_plugins,
            failed_plugin_reasons=failed_plugin_reasons or {},
            inactive_plugins=inactive_plugins,
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

        component_name = invoke.component_name
        handler_method = self._resolve_component_handler(meta, component_name)
        inflight = self._start_inflight_rpc(envelope, component_name)

        try:
            # 回退: 旧版 LegacyPluginAdapter 通过 invoke_component 统一桥接
            if (handler_method is None or not callable(handler_method)) and hasattr(meta.instance, "invoke_component"):
                try:
                    result = await self._invoke_plugin_callable(
                        meta.instance.invoke_component,
                        component_name,
                        **invoke.args,
                    )
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
                result = await self._invoke_plugin_callable(handler_method, **invoke.args)
                resp_payload = InvokeResultPayload(success=True, result=result)
                return envelope.make_response(payload=resp_payload.model_dump())
            except Exception as e:
                logger.error(f"插件 {plugin_id} 组件 {component_name} 执行异常: {e}", exc_info=True)
                resp_payload = InvokeResultPayload(success=False, result=str(e))
                return envelope.make_response(payload=resp_payload.model_dump())
        finally:
            self._finish_inflight_rpc(inflight)

    async def _handle_llm_provider_invoke(self, envelope: Envelope) -> Envelope:
        """处理 LLM Provider 调用请求。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: 标准化后的 Provider 调用结果。
        """
        try:
            invoke = LLMProviderInvokePayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        plugin_id = envelope.plugin_id
        meta = self._loader.get_plugin(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                f"插件 {plugin_id} 未加载",
            )

        handler_name = meta.llm_provider_handlers.get(invoke.client_type, "")
        handler_method = getattr(meta.instance, handler_name, None) if handler_name else None
        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 未注册 LLM Provider: {invoke.client_type}",
            )

        inflight = self._start_inflight_rpc(envelope, f"llm_provider:{invoke.client_type}")
        try:
            result = await self._invoke_plugin_callable(
                handler_method,
                operation=invoke.operation,
                request=invoke.request,
            )
            resp_payload = InvokeResultPayload(success=True, result=result)
            return envelope.make_response(payload=resp_payload.model_dump())
        except Exception as exc:
            logger.error(
                f"插件 {plugin_id} LLM Provider {invoke.client_type} 执行异常: {exc}",
                exc_info=True,
            )
            resp_payload = InvokeResultPayload(success=False, result=str(exc))
            return envelope.make_response(payload=resp_payload.model_dump())
        finally:
            self._finish_inflight_rpc(inflight)

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

        component_name = invoke.component_name
        handler_method = self._resolve_component_handler(meta, component_name)

        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 无组件: {component_name}",
            )

        inflight = self._start_inflight_rpc(envelope, component_name)
        try:
            raw = await self._invoke_plugin_callable(handler_method, **invoke.args)

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
        finally:
            self._finish_inflight_rpc(inflight)

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

        component_name = invoke.component_name
        handler_method = self._resolve_component_handler(meta, component_name)
        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 无组件: {component_name}",
            )

        inflight = self._start_inflight_rpc(envelope, component_name)
        try:
            try:
                raw = await self._invoke_plugin_callable(handler_method, **invoke.args)
            except Exception as exc:
                logger.error(f"插件 {plugin_id} hook_handler {component_name} 执行异常: {exc}", exc_info=True)
                return envelope.make_response(
                    payload={
                        "success": False,
                        "action": "continue",
                        "error_message": str(exc),
                    }
                )

            if raw is None:
                result = {"success": True, "action": "continue"}
            elif isinstance(raw, dict):
                result = {
                    "success": True,
                    "action": str(raw.get("action", "continue") or "continue").strip().lower() or "continue",
                    "modified_kwargs": raw.get("modified_kwargs"),
                    "custom_result": raw.get("custom_result"),
                }
            else:
                result = {"success": True, "action": "continue", "custom_result": raw}

            return envelope.make_response(payload=result)
        finally:
            self._finish_inflight_rpc(inflight)

    async def _handle_health(self, envelope: Envelope) -> Envelope:
        """处理健康检查"""
        inflight = self._inflight_snapshot()
        await self._write_debug_event(
            "runner_health_received",
            {
                "request_id": envelope.request_id,
                "inflight_count": len(inflight),
                "max_inflight_duration_ms": int(inflight[0].get("duration_ms", 0)) if inflight else 0,
            },
        )
        await self._dump_inflight_debug("health_check", min_duration_ms=5000)
        uptime_ms = int((time.monotonic() - self._start_time) * 1000)
        health = HealthPayload(
            healthy=True,
            loaded_plugins=self._loader.list_plugins(),
            uptime_ms=uptime_ms,
        )
        return envelope.make_response(payload=health.model_dump())

    async def _handle_prepare_shutdown(self, envelope: Envelope) -> Envelope:
        """处理准备关停"""
        logger.debug("收到 prepare_shutdown 信号")
        await self._dump_inflight_debug("prepare_shutdown")
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_shutdown(self, envelope: Envelope) -> Envelope:
        """处理关停 — 调用所有插件的 on_unload 后退出"""
        logger.debug("收到 shutdown 信号，开始调用 on_unload")
        await self._dump_inflight_debug("shutdown")
        for plugin_id in list(self._loader.list_plugins()):
            meta = self._loader.get_plugin(plugin_id)
            if meta is not None:
                await self._unload_plugin(meta, reason="runner_shutdown")
        self._shutting_down = True
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_config_updated(self, envelope: Envelope) -> Envelope:
        """处理配置更新事件。"""
        try:
            payload = ConfigUpdatedPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        plugin_id = envelope.plugin_id
        if meta := self._loader.get_plugin(plugin_id):
            inflight = self._start_inflight_rpc(envelope, "on_config_update")
            try:
                config_scope = payload.config_scope.value
                if config_scope == "self":
                    self._apply_plugin_config(meta, config_data=payload.config_data)
                if not hasattr(meta.instance, "on_config_update"):
                    raise AttributeError("插件缺少 on_config_update() 实现")

                await self._invoke_plugin_callable(
                    meta.instance.on_config_update,
                    config_scope,
                    payload.config_data,
                    payload.config_version,
                )
            except Exception as e:
                logger.error(f"插件 {plugin_id} 配置更新失败: {e}")
                return envelope.make_error_response(ErrorCode.E_UNKNOWN.value, str(e))
            finally:
                self._finish_inflight_rpc(inflight)
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_inspect_plugin_config(self, envelope: Envelope) -> Envelope:
        """处理插件配置元数据解析请求。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: RPC 响应信封。
        """

        try:
            payload = InspectPluginConfigPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        plugin_id = envelope.plugin_id
        meta, is_temporary_meta, error_message = self._resolve_plugin_meta_for_config_request(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                error_message or f"未找到插件: {plugin_id}",
            )

        try:
            result = self._inspect_plugin_config(
                meta,
                config_data=payload.config_data,
                use_provided_config=payload.use_provided_config,
                suppress_errors=payload.use_provided_config,
                enforce_version=not payload.use_provided_config,
            )
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))
        finally:
            if is_temporary_meta:
                self._loader.purge_plugin_modules(plugin_id, meta.plugin_dir)

        return envelope.make_response(payload=result.model_dump())

    async def _handle_validate_plugin_config(self, envelope: Envelope) -> Envelope:
        """处理插件配置校验请求。

        Args:
            envelope: RPC 请求信封。

        Returns:
            Envelope: RPC 响应信封。
        """

        try:
            payload = ValidatePluginConfigPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        plugin_id = envelope.plugin_id
        meta, is_temporary_meta, error_message = self._resolve_plugin_meta_for_config_request(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                error_message or f"未找到插件: {plugin_id}",
            )

        try:
            inspection_result = self._inspect_plugin_config(
                meta,
                config_data=payload.config_data,
                use_provided_config=True,
                suppress_errors=False,
                enforce_version=True,
            )
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))
        finally:
            if is_temporary_meta:
                self._loader.purge_plugin_modules(plugin_id, meta.plugin_dir)

        result = ValidatePluginConfigResultPayload(
            success=True,
            normalized_config=inspection_result.normalized_config,
            changed=inspection_result.changed,
        )
        return envelope.make_response(payload=result.model_dump())

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
            result = await self._reload_plugin_by_id(
                payload.plugin_id,
                payload.reason,
                external_available_plugins=dict(payload.external_available_plugins),
            )
            return envelope.make_response(payload=result.model_dump())

    async def _handle_unload_plugins(self, envelope: Envelope) -> Envelope:
        """处理批量插件卸载请求。"""

        try:
            payload = UnloadPluginsPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        if self._reload_lock.locked():
            requested_plugin_ids = ", ".join(self._normalize_requested_plugin_ids(payload.plugin_ids)) or "<empty>"
            return envelope.make_error_response(
                ErrorCode.E_RELOAD_IN_PROGRESS.value,
                f"插件 {requested_plugin_ids} 批量卸载请求被拒绝：已有重载或卸载任务正在执行",
            )

        async with self._reload_lock:
            result = await self._unload_plugins_by_ids(list(payload.plugin_ids), payload.reason)
            return envelope.make_response(payload=result.model_dump())

    async def _handle_reload_plugins(self, envelope: Envelope) -> Envelope:
        """处理批量插件重载请求。"""

        try:
            payload = ReloadPluginsPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        if self._reload_lock.locked():
            requested_plugin_ids = ", ".join(self._normalize_requested_plugin_ids(payload.plugin_ids)) or "<empty>"
            return envelope.make_error_response(
                ErrorCode.E_RELOAD_IN_PROGRESS.value,
                f"插件 {requested_plugin_ids} 批量重载请求被拒绝：已有重载任务正在执行",
            )

        async with self._reload_lock:
            result = await self._reload_plugins_by_ids(
                list(payload.plugin_ids),
                payload.reason,
                external_available_plugins=dict(payload.external_available_plugins),
            )
            return envelope.make_response(payload=result.model_dump())

    def request_capability(self) -> RPCClient:
        """获取 RPC 客户端（供 SDK 使用，发起能力调用）"""
        return self._rpc_client


# ─── 进程入口 ──────────────────────────────────────────────


async def _async_main() -> None:
    """异步主入口"""
    blocked_plugin_reasons_raw = os.environ.get(ENV_BLOCKED_PLUGIN_REASONS, "")
    host_address = os.environ.pop(ENV_IPC_ADDRESS, "")
    external_plugin_ids_raw = os.environ.get(ENV_EXTERNAL_PLUGIN_IDS, "")
    session_token = os.environ.pop(ENV_SESSION_TOKEN, "")
    plugin_dirs_str = os.environ.get(ENV_PLUGIN_DIRS, "")
    plugin_type_filter = os.environ.get(ENV_PLUGIN_TYPE_FILTER, "")
    runner_group = os.environ.get(ENV_RUNNER_GROUP, "unknown")
    trusted_plugin_dirs_str = os.environ.get(ENV_TRUSTED_PLUGIN_DIRS, "")

    if not host_address or not session_token:
        logger.error(f"缺少必要的环境变量: {ENV_IPC_ADDRESS}, {ENV_SESSION_TOKEN}")
        sys.exit(1)

    plugin_dirs = [d for d in plugin_dirs_str.split(os.pathsep) if d]
    trusted_plugin_dirs = [d for d in trusted_plugin_dirs_str.split(os.pathsep) if d]
    try:
        external_plugin_ids = json.loads(external_plugin_ids_raw) if external_plugin_ids_raw else {}
    except json.JSONDecodeError:
        logger.warning("解析外部依赖插件版本映射失败，已回退为空映射")
        external_plugin_ids = {}
    if not isinstance(external_plugin_ids, dict):
        logger.warning("外部依赖插件版本映射格式非法，已回退为空映射")
        external_plugin_ids = {}

    try:
        blocked_plugin_reasons = json.loads(blocked_plugin_reasons_raw) if blocked_plugin_reasons_raw else {}
    except json.JSONDecodeError:
        logger.warning("解析阻止加载插件原因映射失败，已回退为空映射")
        blocked_plugin_reasons = {}
    if not isinstance(blocked_plugin_reasons, dict):
        logger.warning("阻止加载插件原因映射格式非法，已回退为空映射")
        blocked_plugin_reasons = {}

    runner_kwargs: Dict[str, Any] = {
        "external_available_plugins": {
            str(plugin_id): str(plugin_version) for plugin_id, plugin_version in external_plugin_ids.items()
        }
    }
    if blocked_plugin_reasons:
        runner_kwargs["blocked_plugin_reasons"] = {
            str(plugin_id): str(reason) for plugin_id, reason in blocked_plugin_reasons.items()
        }

    runner = PluginRunner(
        host_address,
        session_token,
        plugin_dirs,
        runner_group=runner_group,
        plugin_type_filter=plugin_type_filter,
        trusted_plugin_dirs=trusted_plugin_dirs,
        **runner_kwargs,
    )
    await runner._write_debug_event(
        "runner_started",
        {
            "debug_file": str(runner._debug_file_path()),
        },
    )

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
