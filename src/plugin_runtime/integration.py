"""插件运行时与主程序的集成层

提供 PluginRuntimeManager 单例，负责：
1. 管理双 PluginSupervisor 的生命周期（内置插件 / 第三方插件各一个子进程）
2. 将 EventType 桥接到运行时的 event dispatch
3. 触发跨 Supervisor 的命名 Hook 调用
4. 在运行时的 ComponentRegistry 中查找命令
5. 提供统一的能力实现注册接口，使插件可以调用主程序功能
"""

from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

import asyncio
import inspect
import shutil
import stat

import tomlkit

from src.common.logger import get_logger
from src.common.shutdown import is_shutdown_requested
from src.config.config import config_manager
from src.config.file_watcher import FileChange, FileWatcher
from src.platform_io import DeliveryBatch, InboundMessageEnvelope, get_platform_io_manager
from src.plugin_runtime.capabilities import (
    RuntimeComponentCapabilityMixin,
    RuntimeCoreCapabilityMixin,
    RuntimeDataCapabilityMixin,
    RuntimeRenderCapabilityMixin,
)
from src.plugin_runtime.capabilities.registry import register_capability_impls
from src.plugin_runtime.dependency_pipeline import PluginDependencyPipeline
from src.plugin_runtime.hook_catalog import register_builtin_hook_specs
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult, HookDispatcher
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.plugin_runtime.protocol.envelope import InspectPluginConfigResultPayload
from src.plugin_runtime.runner.manifest_validator import ManifestValidator, is_reserved_plugin_directory

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
    from src.plugin_runtime.host.message_utils import MessageDict
    from src.plugin_runtime.host.supervisor import PluginSupervisor

logger = get_logger("plugin_runtime.integration")

# 旧系统 EventType -> 新系统 event_type 字符串映射
_EVENT_TYPE_MAP: Dict[str, str] = {
    "on_start": "on_start",
    "on_stop": "on_stop",
    "on_message_pre_process": "on_message_pre_process",
    "on_message": "on_message",
    "on_plan": "on_plan",
    "post_llm": "post_llm",
    "after_llm": "after_llm",
    "post_send_pre_process": "post_send_pre_process",
    "post_send": "post_send",
    "after_send": "after_send",
}

_RUNTIME_GROUP_DESCRIPTIONS: Dict[str, str] = {
    "builtin": "核心插件（内置插件与适配器）",
    "third_party": "扩展插件（第三方扩展）",
}


@dataclass(frozen=True)
class DependencySyncState:
    """表示一次插件依赖同步后的状态。"""

    blocked_changed_plugin_ids: Set[str]
    environment_changed: bool


@dataclass(frozen=True)
class AdapterRuntimeTransitionResult:
    """适配器离线或上线操作的结构化结果。"""

    success: bool
    changed_plugin_ids: List[str]
    pending_plugin_ids: List[str]
    failed_plugins: Dict[str, str]


class PluginRuntimeManager(
    RuntimeCoreCapabilityMixin,
    RuntimeDataCapabilityMixin,
    RuntimeComponentCapabilityMixin,
    RuntimeRenderCapabilityMixin,
):
    """插件运行时管理器（单例）

    内置插件与第三方插件分别运行在各自的 Supervisor / Runner 子进程中。
    """

    def __init__(self) -> None:
        """初始化插件运行时管理器。"""
        self._builtin_supervisor: Optional[PluginSupervisor] = None
        self._third_party_supervisor: Optional[PluginSupervisor] = None
        self._started: bool = False
        self._plugin_file_watcher: Optional[FileWatcher] = None
        self._plugin_source_watcher_subscription_id: Optional[str] = None
        self._plugin_config_watcher_subscriptions: Dict[str, Tuple[Path, str]] = {}
        self._plugin_path_cache: Dict[str, Path] = {}
        self._manifest_validator: ManifestValidator = ManifestValidator(
            validate_python_package_dependencies=False,
            log_errors=False,
            log_compat_warnings=False,
        )
        self._plugin_dependency_pipeline: PluginDependencyPipeline = PluginDependencyPipeline()
        self._blocked_plugin_reasons: Dict[str, str] = {}
        self._config_reload_callback: Callable[[Sequence[str]], Awaitable[None]] = self._handle_main_config_reload
        self._config_reload_callback_registered: bool = False
        self._hook_spec_registry: HookSpecRegistry = HookSpecRegistry()
        self._builtin_hook_specs_registered: bool = False
        self._hook_dispatcher: HookDispatcher = HookDispatcher(
            lambda: self.supervisors,
            hook_spec_registry=self._hook_spec_registry,
        )
        self._adapter_transition_lock = asyncio.Lock()
        self._offline_adapter_plugin_ids: Set[str] = set()

    async def _dispatch_platform_inbound(self, envelope: InboundMessageEnvelope) -> None:
        """接收 Platform IO 审核后的入站消息并送入主消息链。

        Args:
            envelope: Platform IO 产出的入站封装。
        """
        session_message = envelope.session_message
        if session_message is None and envelope.payload is not None:
            from src.plugin_runtime.host.message_utils import PluginMessageUtils

            session_message = PluginMessageUtils._build_session_message_from_dict(dict(envelope.payload))
        if session_message is None:
            raise ValueError("Platform IO 入站封装缺少可用的 SessionMessage 或 payload")

        from src.chat.message_receive.bot import chat_bot

        await chat_bot.receive_message(session_message)

    # ─── 插件目录 ─────────────────────────────────────────────

    @staticmethod
    def _get_builtin_plugin_dirs() -> List[Path]:
        """内置插件目录：src/plugins/built_in/"""
        candidate = Path("src", "plugins", "built_in").resolve()
        return [candidate] if candidate.is_dir() else []

    @staticmethod
    def _get_third_party_plugin_dirs() -> List[Path]:
        """第三方插件目录：plugins/"""
        candidate = Path("plugins").resolve()
        return [candidate] if candidate.is_dir() else []

    @classmethod
    def _discover_plugin_dependency_map(cls, plugin_dirs: Iterable[Path]) -> Dict[str, List[str]]:
        """扫描指定插件目录集合，返回 ``plugin_id -> dependencies`` 映射。"""
        validator = ManifestValidator(
            validate_python_package_dependencies=False,
            log_errors=False,
            log_compat_warnings=False,
        )
        return validator.build_plugin_dependency_map(plugin_dirs)

    @classmethod
    def _discover_llm_provider_conflicts(cls, plugin_dirs: Iterable[Path]) -> Dict[str, str]:
        """扫描插件 Manifest，发现 LLM Provider client_type 冲突。

        Args:
            plugin_dirs: 需要扫描的插件根目录集合。

        Returns:
            Dict[str, str]: 需要阻止加载的插件 ID 与原因映射。
        """
        validator = ManifestValidator(
            validate_python_package_dependencies=False,
            log_errors=False,
            log_compat_warnings=False,
        )
        provider_owners: Dict[str, List[str]] = {}
        for _plugin_path, manifest in validator.iter_plugin_manifests(plugin_dirs, require_entrypoint=True):
            for client_type in manifest.llm_provider_client_types:
                provider_owners.setdefault(client_type, []).append(manifest.id)

        blocked_reasons: Dict[str, str] = {}
        for client_type, plugin_ids in provider_owners.items():
            unique_plugin_ids = sorted(set(plugin_ids))
            if len(unique_plugin_ids) <= 1:
                continue
            reason = (
                f"LLM Provider client_type 冲突: {client_type} 被以下插件重复声明: "
                f"{', '.join(unique_plugin_ids)}"
            )
            for plugin_id in unique_plugin_ids:
                blocked_reasons[plugin_id] = reason
        return blocked_reasons

    @classmethod
    def _discover_plugin_ids_by_type(cls, plugin_dirs: Iterable[Path], plugin_type: str) -> Set[str]:
        """扫描指定目录集合，返回匹配 manifest plugin_type 的插件 ID。"""

        validator = ManifestValidator(
            validate_python_package_dependencies=False,
            log_errors=False,
            log_compat_warnings=False,
        )
        normalized_plugin_type = str(plugin_type or "").strip().lower()
        plugin_ids: Set[str] = set()
        for _plugin_path, manifest in validator.iter_plugin_manifests(plugin_dirs, require_entrypoint=True):
            if str(manifest.plugin_type or "extension").strip().lower() == normalized_plugin_type:
                plugin_ids.add(manifest.id)
        return plugin_ids

    @classmethod
    def _get_group_dependency_flags(
        cls,
        builtin_dirs: Sequence[Path],
        third_party_dirs: Sequence[Path],
    ) -> tuple[bool, bool]:
        """返回内置组与第三方组之间的跨 Supervisor 依赖关系。"""

        builtin_dependencies = cls._discover_plugin_dependency_map(builtin_dirs)
        third_party_dependencies = cls._discover_plugin_dependency_map(third_party_dirs)
        adapter_plugin_ids = cls._discover_plugin_ids_by_type(third_party_dirs, "adapter")
        builtin_plugin_ids = set(builtin_dependencies)
        third_party_plugin_ids = set(third_party_dependencies)

        builtin_needs_third_party = any(
            dependency in third_party_plugin_ids
            and dependency not in adapter_plugin_ids
            for dependencies in builtin_dependencies.values()
            for dependency in dependencies
        )
        third_party_needs_builtin = any(
            dependency in builtin_plugin_ids or dependency in adapter_plugin_ids
            for dependencies in third_party_dependencies.values()
            for dependency in dependencies
        )

        return builtin_needs_third_party, third_party_needs_builtin

    @classmethod
    def _build_group_start_order(
        cls,
        builtin_dirs: Sequence[Path],
        third_party_dirs: Sequence[Path],
    ) -> List[str]:
        """根据跨 Supervisor 依赖关系决定 Runner 启动顺序。"""

        builtin_needs_third_party, third_party_needs_builtin = cls._get_group_dependency_flags(
            builtin_dirs,
            third_party_dirs,
        )

        if builtin_needs_third_party and third_party_needs_builtin:
            raise RuntimeError("检测到跨 Supervisor 循环依赖，当前无法安全启动独立 Runner")
        if builtin_needs_third_party:
            return ["third_party", "builtin"]
        return ["builtin", "third_party"]

    @staticmethod
    def _instantiate_supervisor(supervisor_cls: Any, **kwargs: Any) -> Any:
        """兼容不同构造签名地实例化 Supervisor。

        Args:
            supervisor_cls: 目标 Supervisor 类。
            **kwargs: 期望传入的构造参数。

        Returns:
            Any: 实例化后的 Supervisor。
        """

        signature = inspect.signature(supervisor_cls)
        accepts_var_keyword = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_var_keyword:
            return supervisor_cls(**kwargs)

        supported_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in signature.parameters
        }
        return supervisor_cls(**supported_kwargs)

    def _resolve_runtime_plugin_dirs(self) -> Tuple[List[Path], List[Path]]:
        """解析当前运行时应管理的插件根目录。

        Returns:
            Tuple[List[Path], List[Path]]: 内置插件目录列表与第三方插件目录列表。
        """

        return self._get_builtin_plugin_dirs(), self._get_third_party_plugin_dirs()

    @staticmethod
    def _resolve_supervisor_socket_paths() -> Tuple[Optional[str], Optional[str]]:
        """解析内置与第三方 Supervisor 的 IPC 地址。

        Returns:
            Tuple[Optional[str], Optional[str]]: 内置 Runner 与第三方 Runner 的 socket 地址。
        """

        runtime_config = config_manager.get_global_config().plugin_runtime
        socket_path_base = runtime_config.ipc_socket_path or None
        builtin_socket = f"{socket_path_base}-builtin" if socket_path_base else None
        third_party_socket = f"{socket_path_base}-third_party" if socket_path_base else None
        return builtin_socket, third_party_socket

    def _apply_blocked_plugin_reasons_to_supervisors(self) -> None:
        """将当前阻止加载插件列表同步到全部 Supervisor。"""

        for supervisor in self.supervisors:
            set_blocked_plugin_reasons = getattr(supervisor, "set_blocked_plugin_reasons", None)
            if callable(set_blocked_plugin_reasons):
                set_blocked_plugin_reasons(self._blocked_plugin_reasons)

    def _set_blocked_plugin_reasons(self, blocked_plugin_reasons: Dict[str, str]) -> Set[str]:
        """更新 Host 侧维护的阻止加载插件列表。

        Args:
            blocked_plugin_reasons: 最新的阻止加载插件及原因映射。

        Returns:
            Set[str]: 本次发生状态变化的插件 ID 集合。
        """

        normalized_reasons = {
            str(plugin_id or "").strip(): str(reason or "").strip()
            for plugin_id, reason in blocked_plugin_reasons.items()
            if str(plugin_id or "").strip() and str(reason or "").strip()
        }
        changed_plugin_ids = {
            plugin_id
            for plugin_id in set(self._blocked_plugin_reasons) | set(normalized_reasons)
            if self._blocked_plugin_reasons.get(plugin_id) != normalized_reasons.get(plugin_id)
        }
        self._blocked_plugin_reasons = normalized_reasons
        self._apply_blocked_plugin_reasons_to_supervisors()
        return changed_plugin_ids

    async def _sync_plugin_dependencies(self, plugin_dirs: Sequence[Path]) -> DependencySyncState:
        """执行插件依赖同步，并刷新阻止加载插件列表。

        Args:
            plugin_dirs: 当前需要参与分析的插件根目录列表。

        Returns:
            DependencySyncState: 同步后的环境变更状态与阻止列表变化集合。
        """

        result = await self._plugin_dependency_pipeline.execute(plugin_dirs)
        blocked_plugin_reasons = {
            **result.blocked_plugin_reasons,
            **self._discover_llm_provider_conflicts(plugin_dirs),
        }
        changed_plugin_ids = self._set_blocked_plugin_reasons(blocked_plugin_reasons)
        return DependencySyncState(
            blocked_changed_plugin_ids=changed_plugin_ids,
            environment_changed=result.environment_changed,
        )

    def _build_supervisors(self, builtin_dirs: Sequence[Path], third_party_dirs: Sequence[Path]) -> None:
        """根据目录列表创建当前运行时所需的 Supervisor。

        Args:
            builtin_dirs: 内置插件目录列表。
            third_party_dirs: 第三方插件目录列表。
        """

        from src.plugin_runtime.host.supervisor import PluginSupervisor

        builtin_socket, third_party_socket = self._resolve_supervisor_socket_paths()
        self._builtin_supervisor = None
        self._third_party_supervisor = None

        if builtin_dirs or third_party_dirs:
            builtin_supervisor = self._instantiate_supervisor(
                PluginSupervisor,
                plugin_dirs=list(builtin_dirs) + list(third_party_dirs),
                group_name="builtin",
                hook_spec_registry=self._hook_spec_registry,
                socket_path=builtin_socket,
                plugin_type_filter="trusted_or_adapter",
                trusted_plugin_dirs=list(builtin_dirs),
            )
            self._builtin_supervisor = builtin_supervisor
            self._register_capability_impls(builtin_supervisor)

        if third_party_dirs:
            third_party_supervisor = self._instantiate_supervisor(
                PluginSupervisor,
                plugin_dirs=list(third_party_dirs),
                group_name="third_party",
                hook_spec_registry=self._hook_spec_registry,
                socket_path=third_party_socket,
                plugin_type_filter="not_adapter",
            )
            self._third_party_supervisor = third_party_supervisor
            self._register_capability_impls(third_party_supervisor)

        self._apply_blocked_plugin_reasons_to_supervisors()

    @staticmethod
    def _is_plugin_load_residue_dir(plugin_path: Path) -> bool:
        """判断目录是否为插件加载前可安全清理的残留目录。"""
        if not plugin_path.exists() or not plugin_path.is_dir() or plugin_path.is_symlink():
            return False
        if is_reserved_plugin_directory(plugin_path):
            return False

        try:
            entries = list(plugin_path.iterdir())
        except OSError:
            return False

        if not entries:
            return False

        allowed_names = {".git", "__pycache__"}
        entry_names = {entry.name for entry in entries}
        if not entry_names.issubset(allowed_names):
            return False

        return all(entry.is_dir() and not entry.is_symlink() for entry in entries)

    @classmethod
    def _cleanup_plugin_load_residue_dirs(cls, plugin_dirs: Sequence[Path]) -> List[Path]:
        """清理插件系统加载前可判定的卸载残留目录。"""
        removed_paths: List[Path] = []

        def remove_readonly(func: Any, target_path: str, _: Any) -> None:
            Path(target_path).chmod(stat.S_IWRITE)
            func(target_path)

        for plugin_root in plugin_dirs:
            if not plugin_root.is_dir():
                continue
            for plugin_path in plugin_root.iterdir():
                if not cls._is_plugin_load_residue_dir(plugin_path):
                    continue
                try:
                    shutil.rmtree(plugin_path, onerror=remove_readonly)
                    removed_paths.append(plugin_path)
                except Exception as exc:
                    logger.warning(f"清理插件加载残留目录失败: {plugin_path}: {exc}")

        if removed_paths:
            cleaned_names = ", ".join(path.name for path in removed_paths)
            logger.info(f"插件系统加载前已清理 {len(removed_paths)} 个残留目录: {cleaned_names}")
        return removed_paths

    async def _start_supervisors(
        self,
        builtin_dirs: Sequence[Path],
        third_party_dirs: Sequence[Path],
    ) -> List["PluginSupervisor"]:
        """按依赖顺序启动当前已创建的 Supervisor。

        Args:
            builtin_dirs: 内置插件目录列表。
            third_party_dirs: 第三方插件目录列表。

        Returns:
            List[PluginSupervisor]: 成功启动的 Supervisor 列表。
        """

        started_supervisors: List["PluginSupervisor"] = []
        supervisor_groups: Dict[str, Optional["PluginSupervisor"]] = {
            "builtin": self._builtin_supervisor,
            "third_party": self._third_party_supervisor,
        }
        start_order = self._build_group_start_order(builtin_dirs, third_party_dirs)
        builtin_needs_third_party, third_party_needs_builtin = self._get_group_dependency_flags(
            builtin_dirs,
            third_party_dirs,
        )

        try:
            if not builtin_needs_third_party and not third_party_needs_builtin:
                independent_supervisors = [
                    supervisor
                    for group_name in start_order
                    if (supervisor := supervisor_groups.get(group_name)) is not None
                ]
                for supervisor in independent_supervisors:
                    supervisor.set_external_available_plugins({})
                    set_blocked_plugin_reasons = getattr(supervisor, "set_blocked_plugin_reasons", None)
                    if callable(set_blocked_plugin_reasons):
                        set_blocked_plugin_reasons(self._blocked_plugin_reasons)

                results = await asyncio.gather(
                    *(supervisor.start() for supervisor in independent_supervisors),
                    return_exceptions=True,
                )
                for supervisor, result in zip(independent_supervisors, results, strict=False):
                    if isinstance(result, Exception):
                        await asyncio.gather(
                            *(started_supervisor.stop() for started_supervisor in independent_supervisors),
                            return_exceptions=True,
                        )
                        raise result
                    started_supervisors.append(supervisor)
                return started_supervisors

            for group_name in start_order:
                supervisor = supervisor_groups.get(group_name)
                if supervisor is None:
                    continue

                external_plugin_versions = {
                    plugin_id: plugin_version
                    for started_supervisor in started_supervisors
                    for plugin_id, plugin_version in started_supervisor.get_loaded_plugin_versions().items()
                }
                supervisor.set_external_available_plugins(external_plugin_versions)
                set_blocked_plugin_reasons = getattr(supervisor, "set_blocked_plugin_reasons", None)
                if callable(set_blocked_plugin_reasons):
                    set_blocked_plugin_reasons(self._blocked_plugin_reasons)
                await supervisor.start()
                started_supervisors.append(supervisor)
        except Exception:
            await asyncio.gather(*(supervisor.stop() for supervisor in started_supervisors), return_exceptions=True)
            raise

        return started_supervisors

    async def _stop_supervisors(self) -> None:
        """停止当前全部 Supervisor。"""

        supervisors = self.supervisors
        if not supervisors:
            return

        await asyncio.gather(*(supervisor.stop() for supervisor in supervisors), return_exceptions=True)
        self._builtin_supervisor = None
        self._third_party_supervisor = None

    async def _restart_supervisors(self, reason: str) -> bool:
        """重启当前全部 Supervisor。

        Args:
            reason: 本次重启的原因。

        Returns:
            bool: 是否重启成功。
        """

        builtin_dirs, third_party_dirs = self._resolve_runtime_plugin_dirs()
        self._cleanup_plugin_load_residue_dirs(third_party_dirs)
        if duplicate_plugin_ids := self._find_duplicate_plugin_ids(builtin_dirs + third_party_dirs):
            details = "; ".join(
                f"{plugin_id}: {', '.join(str(path) for path in paths)}"
                for plugin_id, paths in sorted(duplicate_plugin_ids.items())
            )
            logger.error(f"检测到重复插件 ID，拒绝执行 Supervisor 重启: {details}")
            return False

        logger.info(f"开始重启插件运行时 Supervisor: {reason}")
        await self._stop_supervisors()
        self._build_supervisors(builtin_dirs, third_party_dirs)

        try:
            await self._start_supervisors(builtin_dirs, third_party_dirs)
        except Exception as exc:
            logger.error(f"重启插件运行时 Supervisor 失败: {exc}", exc_info=True)
            await self._stop_supervisors()
            return False

        self._refresh_plugin_config_watch_subscriptions()
        logger.info(f"插件运行时 Supervisor 已重启完成: {reason}")
        return True

    # ─── 生命周期 ─────────────────────────────────────────────

    async def start(self) -> None:
        """启动双子进程插件运行时"""
        if self._started:
            logger.warning("PluginRuntimeManager 已在运行中，跳过重复启动")
            return

        _cfg = config_manager.get_global_config().plugin_runtime
        if not _cfg.enabled:
            logger.info("插件运行时已在配置中禁用，跳过启动")
            return

        self._offline_adapter_plugin_ids.clear()

        builtin_dirs, third_party_dirs = self._resolve_runtime_plugin_dirs()
        self._cleanup_plugin_load_residue_dirs(third_party_dirs)

        if duplicate_plugin_ids := self._find_duplicate_plugin_ids(builtin_dirs + third_party_dirs):
            details = "; ".join(
                f"{plugin_id}: {', '.join(str(p) for p in paths)}"
                for plugin_id, paths in sorted(duplicate_plugin_ids.items())
            )
            logger.error(f"检测到重复插件 ID，拒绝启动插件运行时: {details}")
            return

        if not builtin_dirs and not third_party_dirs:
            logger.info("未找到任何插件目录，跳过插件运行时启动")
            return

        dependency_sync_state = await self._sync_plugin_dependencies(builtin_dirs + third_party_dirs)
        if dependency_sync_state.environment_changed:
            logger.info("插件依赖流水线已更新当前 Python 环境，启动时将直接加载最新环境")

        self.ensure_builtin_hook_specs_registered()
        platform_io_manager = get_platform_io_manager()
        self._build_supervisors(builtin_dirs, third_party_dirs)

        started_supervisors: List["PluginSupervisor"] = []
        try:
            platform_io_manager.set_inbound_dispatcher(self._dispatch_platform_inbound)
            await platform_io_manager.ensure_send_pipeline_ready()
            started_supervisors = await self._start_supervisors(builtin_dirs, third_party_dirs)

            await self._start_plugin_file_watcher()
            config_manager.register_reload_callback(self._config_reload_callback)
            self._config_reload_callback_registered = True
            self._started = True
            started_group_names = {supervisor.group_name for supervisor in started_supervisors}
            runtime_descriptions = [
                description
                for group_name, description in _RUNTIME_GROUP_DESCRIPTIONS.items()
                if group_name in started_group_names
            ]
            logger.info(
                f"已启动 {len(started_supervisors)} 个独立插件运行时：{'；'.join(runtime_descriptions)}"
            )
        except Exception as e:
            logger.error(f"插件运行时启动失败: {e}", exc_info=True)
            await self._stop_plugin_file_watcher()
            if self._config_reload_callback_registered:
                config_manager.unregister_reload_callback(self._config_reload_callback)
                self._config_reload_callback_registered = False
            await asyncio.gather(*(sv.stop() for sv in started_supervisors), return_exceptions=True)
            platform_io_manager.clear_inbound_dispatcher()
            try:
                await platform_io_manager.stop()
            except Exception as platform_io_exc:
                logger.warning(f"Platform IO 停止失败: {platform_io_exc}")
            await self._hook_dispatcher.stop()
            self._started = False
            self._builtin_supervisor = None
            self._third_party_supervisor = None

    async def stop(self) -> None:
        """停止所有插件运行时"""
        if not self._started:
            return

        platform_io_manager = get_platform_io_manager()
        await self._stop_plugin_file_watcher()
        if self._config_reload_callback_registered:
            config_manager.unregister_reload_callback(self._config_reload_callback)
            self._config_reload_callback_registered = False
        if is_shutdown_requested():
            await self._hook_dispatcher.stop()

        coroutines: List[Coroutine[Any, Any, None]] = []
        if self._builtin_supervisor:
            coroutines.append(self._builtin_supervisor.stop())
        if self._third_party_supervisor:
            coroutines.append(self._third_party_supervisor.stop())

        stop_errors: List[str] = []
        try:
            results = await asyncio.gather(*coroutines, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    stop_errors.append(str(result))

            platform_io_manager.clear_inbound_dispatcher()
            try:
                await platform_io_manager.stop()
            except Exception as exc:
                stop_errors.append(f"Platform IO: {exc}")

            if stop_errors:
                logger.error(f"插件运行时停止过程中存在错误: {'; '.join(stop_errors)}")
            else:
                logger.info("插件运行时已停止")
        finally:
            await self._hook_dispatcher.stop()
            self._started = False
            self._offline_adapter_plugin_ids.clear()
            self._builtin_supervisor = None
            self._third_party_supervisor = None
            self._plugin_path_cache.clear()

    @property
    def is_running(self) -> bool:
        """返回插件运行时是否处于启动状态。"""
        return self._started

    @property
    def hook_dispatcher(self) -> HookDispatcher:
        """返回跨 Supervisor 的命名 Hook 分发器。"""

        return self._hook_dispatcher

    @property
    def invoke_dispatcher(self) -> HookDispatcher:
        """返回命名 Hook 分发器的兼容别名。"""

        return self._hook_dispatcher

    @property
    def supervisors(self) -> List["PluginSupervisor"]:
        """获取所有活跃的 Supervisor"""
        return [s for s in (self._builtin_supervisor, self._third_party_supervisor) if s is not None]

    def register_hook_spec(self, spec: HookSpec) -> None:
        """注册单个命名 Hook 规格。

        Args:
            spec: 需要注册的 Hook 规格。
        """

        self.ensure_builtin_hook_specs_registered()
        self._hook_dispatcher.register_hook_spec(spec)

    def register_hook_specs(self, specs: Sequence[HookSpec]) -> None:
        """批量注册命名 Hook 规格。

        Args:
            specs: 需要注册的 Hook 规格序列。
        """

        self.ensure_builtin_hook_specs_registered()
        self._hook_dispatcher.register_hook_specs(specs)

    def unregister_hook_spec(self, hook_name: str) -> bool:
        """注销指定命名 Hook 规格。

        Args:
            hook_name: 目标 Hook 名称。

        Returns:
            bool: 是否成功注销。
        """

        self.ensure_builtin_hook_specs_registered()
        return self._hook_dispatcher.unregister_hook_spec(hook_name)

    def list_hook_specs(self) -> List[HookSpec]:
        """返回当前全部命名 Hook 规格。

        Returns:
            List[HookSpec]: 当前已注册的 Hook 规格列表。
        """

        self.ensure_builtin_hook_specs_registered()
        return self._hook_dispatcher.list_hook_specs()

    def ensure_builtin_hook_specs_registered(self) -> None:
        """确保内置 Hook 规格已经注册到共享中心表。"""

        if self._builtin_hook_specs_registered:
            return

        register_builtin_hook_specs(self._hook_spec_registry)
        self._builtin_hook_specs_registered = True

    def _build_registered_dependency_map(self) -> Dict[str, Set[str]]:
        """根据当前已注册插件构建全局依赖图。"""

        dependency_map: Dict[str, Set[str]] = {}
        for supervisor in self.supervisors:
            for plugin_id, registration in getattr(supervisor, "_registered_plugins", {}).items():
                dependency_map[plugin_id] = {
                    str(dependency or "").strip()
                    for dependency in getattr(registration, "dependencies", [])
                    if str(dependency or "").strip()
                }
        return dependency_map

    @staticmethod
    def _collect_reverse_dependents(
        plugin_ids: Set[str],
        dependency_map: Dict[str, Set[str]],
    ) -> Set[str]:
        """根据依赖图收集反向依赖闭包。"""

        impacted_plugins: Set[str] = set(plugin_ids)
        changed = True

        while changed:
            changed = False
            for registered_plugin_id, dependencies in dependency_map.items():
                if registered_plugin_id in impacted_plugins:
                    continue
                if dependencies & impacted_plugins:
                    impacted_plugins.add(registered_plugin_id)
                    changed = True

        return impacted_plugins

    def _build_registered_supervisor_map(self) -> Dict[str, "PluginSupervisor"]:
        """构建当前已注册插件到所属 Supervisor 的映射。"""

        return {
            plugin_id: supervisor for supervisor in self.supervisors for plugin_id in supervisor.get_loaded_plugin_ids()
        }

    def get_plugin_load_statuses(self) -> Dict[str, str]:
        """汇总所有 Supervisor 上报的插件加载状态。"""

        statuses: Dict[str, str] = {}
        for supervisor in self.supervisors:
            statuses.update(supervisor.get_plugin_load_statuses())
        return statuses

    def get_plugin_load_failure_reasons(self) -> Dict[str, str]:
        """汇总所有 Supervisor 上报的插件加载失败原因。"""

        reasons: Dict[str, str] = {}
        for supervisor in self.supervisors:
            get_reasons = getattr(supervisor, "get_plugin_load_failure_reasons", None)
            if callable(get_reasons):
                reasons.update(get_reasons())
        return reasons

    def get_plugin_circuit_statuses(self) -> Dict[str, Dict[str, Any]]:
        """返回当前插件熔断状态。"""

        from src.plugin_runtime.host.circuit_breaker import get_plugin_circuit_breaker

        return get_plugin_circuit_breaker().get_plugin_statuses()

    @property
    def is_loading(self) -> bool:
        """返回插件运行时是否仍有 Supervisor 处于加载阶段。"""

        return any(bool(getattr(supervisor, "is_loading", False)) for supervisor in self.supervisors)

    def _build_external_available_plugins_for_supervisor(self, target_supervisor: "PluginSupervisor") -> Dict[str, str]:
        """收集某个 Supervisor 可用的外部插件版本映射。"""

        external_plugin_versions: Dict[str, str] = {}
        for supervisor in self.supervisors:
            if supervisor is target_supervisor:
                continue
            external_plugin_versions.update(supervisor.get_loaded_plugin_versions())
        return external_plugin_versions

    def _find_supervisor_by_plugin_directory(self, plugin_id: str) -> Optional["PluginSupervisor"]:
        """根据插件目录推断应负责该插件重载的 Supervisor。"""

        for supervisor in self.supervisors:
            if self._get_plugin_path_for_supervisor(supervisor, plugin_id) is not None:
                return supervisor
        return None

    def _warn_skipped_cross_supervisor_reload(
        self,
        requested_loaded_plugin_ids: Set[str],
        dependency_map: Dict[str, Set[str]],
        supervisor_by_plugin: Dict[str, "PluginSupervisor"],
    ) -> None:
        """记录因跨 Supervisor 边界而未参与联动重载的插件。"""

        if not requested_loaded_plugin_ids:
            return

        handled_plugin_ids: Set[str] = set()
        for supervisor in self.supervisors:
            local_requested_plugin_ids = {
                plugin_id
                for plugin_id in requested_loaded_plugin_ids
                if supervisor_by_plugin.get(plugin_id) is supervisor
            }
            if not local_requested_plugin_ids:
                continue

            local_plugin_ids = set(supervisor.get_loaded_plugin_ids())
            local_dependency_map = {
                plugin_id: {
                    dependency for dependency in dependency_map.get(plugin_id, set()) if dependency in local_plugin_ids
                }
                for plugin_id in local_plugin_ids
            }
            handled_plugin_ids.update(
                self._collect_reverse_dependents(local_requested_plugin_ids, local_dependency_map)
            )

        impacted_plugin_ids = self._collect_reverse_dependents(requested_loaded_plugin_ids, dependency_map)
        skipped_plugin_ids = sorted(impacted_plugin_ids - handled_plugin_ids)
        if not skipped_plugin_ids:
            return

        logger.warning(
            f"插件 {', '.join(sorted(requested_loaded_plugin_ids))} 存在跨 Supervisor 依赖方未联动重载: "
            f"{', '.join(skipped_plugin_ids)}。当前仅在单个 Supervisor 内执行联动重载；"
            "跨 Supervisor API 调用仍然可用。如需联动重载，请将相关插件放在同一个 Supervisor 内。"
        )

    async def reload_plugins_globally(self, plugin_ids: Sequence[str], reason: str = "manual") -> bool:
        """按 Supervisor 分组执行精确重载。

        仅在单个 Supervisor 内执行依赖联动；跨 Supervisor 依赖方仅记录告警，
        不再自动参与本次热重载。
        """

        normalized_plugin_ids = [
            normalized_plugin_id for plugin_id in plugin_ids if (normalized_plugin_id := str(plugin_id or "").strip())
        ]
        if not normalized_plugin_ids:
            return True

        blocked_plugin_ids = [plugin_id for plugin_id in normalized_plugin_ids if plugin_id in self._blocked_plugin_reasons]
        if blocked_plugin_ids:
            logger.warning(
                "以下插件当前被依赖流水线阻止加载，已拒绝重载请求: "
                + ", ".join(
                    f"{plugin_id} ({self._blocked_plugin_reasons[plugin_id]})"
                    for plugin_id in sorted(blocked_plugin_ids)
                )
            )
            normalized_plugin_ids = [
                plugin_id for plugin_id in normalized_plugin_ids if plugin_id not in self._blocked_plugin_reasons
            ]
            if not normalized_plugin_ids:
                return False

        dependency_map = self._build_registered_dependency_map()
        supervisor_by_plugin = self._build_registered_supervisor_map()
        supervisor_roots: Dict["PluginSupervisor", List[str]] = {}
        requested_loaded_plugin_ids: Set[str] = set()
        missing_plugin_ids: List[str] = []

        for plugin_id in normalized_plugin_ids:
            supervisor = supervisor_by_plugin.get(plugin_id)
            if supervisor is not None:
                requested_loaded_plugin_ids.add(plugin_id)
            else:
                supervisor = self._find_supervisor_by_plugin_directory(plugin_id)

            if supervisor is None:
                missing_plugin_ids.append(plugin_id)
                continue

            if plugin_id not in supervisor_roots.setdefault(supervisor, []):
                supervisor_roots[supervisor].append(plugin_id)

        if missing_plugin_ids:
            logger.warning(f"以下插件未找到可重载的 Supervisor，已跳过: {', '.join(sorted(missing_plugin_ids))}")

        self._warn_skipped_cross_supervisor_reload(
            requested_loaded_plugin_ids=requested_loaded_plugin_ids,
            dependency_map=dependency_map,
            supervisor_by_plugin=supervisor_by_plugin,
        )

        success = True
        for supervisor, root_plugin_ids in supervisor_roots.items():
            if not root_plugin_ids:
                continue

            reloaded = await supervisor.reload_plugins(
                plugin_ids=root_plugin_ids,
                reason=reason,
                external_available_plugins=self._build_external_available_plugins_for_supervisor(supervisor),
            )
            success = success and reloaded

        return success and not missing_plugin_ids

    def _get_loaded_adapter_plugin_ids(self) -> Set[str]:
        """返回所有 Supervisor 当前加载的适配器插件 ID。"""

        return {
            plugin_id
            for supervisor in self.supervisors
            for plugin_id in supervisor.get_loaded_plugin_ids_by_type("adapter")
        }

    async def take_adapters_offline(self) -> AdapterRuntimeTransitionResult:
        """卸载当前所有适配器插件，并记录可恢复的插件集合。"""

        if not self._started:
            return AdapterRuntimeTransitionResult(
                success=False,
                changed_plugin_ids=[],
                pending_plugin_ids=sorted(self._offline_adapter_plugin_ids),
                failed_plugins={"plugin_runtime": "插件运行时尚未启动"},
            )

        async with self._adapter_transition_lock:
            unloaded_plugin_ids: Set[str] = set()
            failed_plugins: Dict[str, str] = {}

            for supervisor in self.supervisors:
                plugin_ids = supervisor.get_loaded_plugin_ids_by_type("adapter")
                if not plugin_ids:
                    continue
                try:
                    result = await supervisor.unload_plugins(
                        plugin_ids,
                        reason="local_operator_offline",
                    )
                except Exception as exc:
                    failed_plugins.update({plugin_id: str(exc) for plugin_id in plugin_ids})
                    continue
                unloaded_plugin_ids.update(result.unloaded_plugins)
                self._offline_adapter_plugin_ids.update(result.unloaded_plugins)
                failed_plugins.update(result.failed_plugins)

            return AdapterRuntimeTransitionResult(
                success=not failed_plugins,
                changed_plugin_ids=sorted(unloaded_plugin_ids),
                pending_plugin_ids=sorted(self._offline_adapter_plugin_ids),
                failed_plugins=failed_plugins,
            )

    async def bring_adapters_online(self) -> AdapterRuntimeTransitionResult:
        """重新加载由 ``take_adapters_offline`` 成功卸载的适配器插件。"""

        if not self._started:
            return AdapterRuntimeTransitionResult(
                success=False,
                changed_plugin_ids=[],
                pending_plugin_ids=sorted(self._offline_adapter_plugin_ids),
                failed_plugins={"plugin_runtime": "插件运行时尚未启动"},
            )

        async with self._adapter_transition_lock:
            requested_plugin_ids = set(self._offline_adapter_plugin_ids)
            if not requested_plugin_ids:
                return AdapterRuntimeTransitionResult(
                    success=True,
                    changed_plugin_ids=[],
                    pending_plugin_ids=[],
                    failed_plugins={},
                )

            loaded_adapter_plugin_ids = self._get_loaded_adapter_plugin_ids()
            plugin_ids_to_reload = requested_plugin_ids - loaded_adapter_plugin_ids
            if plugin_ids_to_reload:
                await self.reload_plugins_globally(
                    sorted(plugin_ids_to_reload),
                    reason="local_operator_online",
                )

            restored_plugin_ids = requested_plugin_ids & self._get_loaded_adapter_plugin_ids()
            self._offline_adapter_plugin_ids.difference_update(restored_plugin_ids)
            failed_plugins = {
                plugin_id: "适配器插件重新加载失败"
                for plugin_id in sorted(self._offline_adapter_plugin_ids)
            }
            return AdapterRuntimeTransitionResult(
                success=not failed_plugins,
                changed_plugin_ids=sorted(restored_plugin_ids),
                pending_plugin_ids=sorted(self._offline_adapter_plugin_ids),
                failed_plugins=failed_plugins,
            )

    async def notify_plugin_config_updated(
        self,
        plugin_id: str,
        config_data: Optional[Dict[str, Any]] = None,
        config_version: str = "",
        config_scope: str = "self",
    ) -> bool:
        """向拥有该插件的 Supervisor 推送配置更新事件。

        Args:
            plugin_id: 插件 ID
            config_data: 可选的配置数据（如果为 None 则由 Supervisor 从磁盘加载）
            config_version: 可选的配置版本字符串，供 Supervisor 进行版本控制
            config_scope: 配置变更范围。
        """
        if not self._started:
            return False

        try:
            sv = self._get_supervisor_for_plugin(plugin_id)
        except RuntimeError as exc:
            logger.error(f"推送插件配置更新失败: {exc}")
            return False

        if sv is None:
            return False

        config_payload = (
            config_data if config_data is not None else self._load_plugin_config_for_supervisor(sv, plugin_id)
        )
        return await sv.notify_plugin_config_updated(
            plugin_id=plugin_id,
            config_data=config_payload,
            config_version=config_version,
            config_scope=config_scope,
        )

    async def validate_plugin_config(self, plugin_id: str, config_data: Dict[str, Any]) -> Dict[str, Any] | None:
        """请求运行时按插件自身配置模型校验配置。

        Args:
            plugin_id: 目标插件 ID。
            config_data: 待校验的配置内容。

        Returns:
            Dict[str, Any] | None: 校验成功时返回规范化后的配置；若插件不存在、
            当前不可路由或运行时不可用，则返回 ``None`` 以便调用方回退到弱推断方案。

        Raises:
            ValueError: 插件已加载，但配置校验失败时抛出。
        """

        if not self._started:
            return None

        try:
            supervisor = self._get_supervisor_for_plugin(plugin_id)
        except RuntimeError as exc:
            logger.warning(f"插件 {plugin_id} 配置校验路由失败，将回退到静态 Schema: {exc}")
            return None

        if supervisor is None:
            supervisor = self._find_supervisor_by_plugin_directory(plugin_id)
        if supervisor is None:
            return None

        try:
            return await supervisor.validate_plugin_config(plugin_id, config_data)
        except ValueError:
            raise
        except Exception as exc:
            logger.warning(f"插件 {plugin_id} 运行时配置校验不可用，将回退到静态 Schema: {exc}")
            return None

    async def inspect_plugin_config(
        self,
        plugin_id: str,
        config_data: Optional[Dict[str, Any]] = None,
        *,
        use_provided_config: bool = False,
    ) -> InspectPluginConfigResultPayload | None:
        """请求运行时解析插件配置元数据。

        Args:
            plugin_id: 目标插件 ID。
            config_data: 可选的配置内容。
            use_provided_config: 是否优先使用传入的配置内容而不是磁盘配置。

        Returns:
            InspectPluginConfigResultPayload | None: 解析成功时返回结构化结果；若插件
            当前不可路由或运行时不可用，则返回 ``None``。

        Raises:
            ValueError: 插件存在，但运行时明确拒绝解析请求时抛出。
        """

        if not self._started:
            return None

        try:
            supervisor = self._get_supervisor_for_plugin(plugin_id)
        except RuntimeError as exc:
            logger.warning(f"插件 {plugin_id} 配置解析路由失败: {exc}")
            return None

        if supervisor is None:
            supervisor = self._find_supervisor_by_plugin_directory(plugin_id)
        if supervisor is None:
            return None

        try:
            return await supervisor.inspect_plugin_config(
                plugin_id=plugin_id,
                config_data=config_data,
                use_provided_config=use_provided_config,
            )
        except ValueError:
            raise
        except Exception as exc:
            logger.warning(f"插件 {plugin_id} 配置解析不可用: {exc}")
            return None

    @staticmethod
    def _normalize_config_reload_scopes(changed_scopes: Sequence[str]) -> tuple[str, ...]:
        """规范化配置热重载范围列表。

        Args:
            changed_scopes: 原始配置热重载范围列表。

        Returns:
            tuple[str, ...]: 去重后的有效配置范围元组。
        """

        normalized_scopes: list[str] = []
        for scope in changed_scopes:
            normalized_scope = str(scope or "").strip().lower()
            if normalized_scope not in {"bot", "model"}:
                continue
            if normalized_scope not in normalized_scopes:
                normalized_scopes.append(normalized_scope)
        return tuple(normalized_scopes)

    async def _broadcast_config_reload(self, scope: str, config_data: Dict[str, Any]) -> None:
        """向订阅指定范围的插件广播配置热重载。

        Args:
            scope: 配置变更范围，仅支持 ``bot`` 或 ``model``。
            config_data: 最新配置数据。
        """

        for supervisor in self.supervisors:
            for plugin_id in supervisor.get_config_reload_subscribers(scope):
                delivered = await supervisor.notify_plugin_config_updated(
                    plugin_id=plugin_id,
                    config_data=config_data,
                    config_version="",
                    config_scope=scope,
                )
                if not delivered:
                    logger.warning(f"向插件 {plugin_id} 广播 {scope} 配置热重载失败")

    async def _handle_main_config_reload(self, changed_scopes: Sequence[str]) -> None:
        """处理 bot/model 主配置热重载广播。

        Args:
            changed_scopes: 本次热重载命中的配置范围列表。
        """

        if not self._started:
            return

        normalized_scopes = self._normalize_config_reload_scopes(changed_scopes)
        if "bot" in normalized_scopes:
            await self._broadcast_config_reload("bot", config_manager.get_global_config().model_dump(mode="json"))
        if "model" in normalized_scopes:
            await self._broadcast_config_reload("model", config_manager.get_model_config().model_dump(mode="json"))

    # ─── 事件桥接 ──────────────────────────────────────────────

    async def bridge_event(
        self,
        event_type_value: str,
        message_dict: Optional["MessageDict"] = None,
        extra_args: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional["MessageDict"]]:
        """将事件分发到所有 Supervisor

        Returns:
            (continue_flag, modified_message_dict)
        """
        if not self._started:
            return True, None

        new_event_type: str = _EVENT_TYPE_MAP.get(event_type_value, event_type_value)

        modified: Optional["MessageDict"] = None
        plugin_message_utils: Any | None = None
        current_message: Optional["SessionMessage"] = None
        if message_dict is not None:
            from src.plugin_runtime.host.message_utils import PluginMessageUtils

            plugin_message_utils = PluginMessageUtils
            current_message = plugin_message_utils._build_session_message_from_dict(dict(message_dict))

        for sv in self.supervisors:
            try:
                cont, mod = await sv.dispatch_event(
                    event_type=new_event_type,
                    message=current_message,
                    extra_args=extra_args,
                )
                if mod is not None:
                    if plugin_message_utils is None:
                        from src.plugin_runtime.host.message_utils import PluginMessageUtils

                        plugin_message_utils = PluginMessageUtils
                    current_message = mod
                    modified = plugin_message_utils._session_message_to_dict(mod)
                if not cont:
                    return False, modified
            except Exception as e:
                logger.error(f"事件 {new_event_type} 分发失败: {e}", exc_info=True)

        return True, modified

    async def invoke_hook(self, hook_name: str, **kwargs: Any) -> HookDispatchResult:
        """触发一次跨 Supervisor 的命名 Hook 调用。

        Args:
            hook_name: 本次触发的 Hook 名称。
            **kwargs: 传递给 Hook 处理器的关键字参数。

        Returns:
            HookDispatchResult: 聚合后的 Hook 调用结果。
        """

        return await self._hook_dispatcher.invoke_hook(hook_name, **kwargs)

    # ─── 命令查找 ──────────────────────────────────────────────

    def find_command_by_text(self, text: str) -> Optional[Dict[str, Any]]:
        """在所有 Supervisor 的 ComponentRegistry 中查找命令"""
        if not self._started:
            return None

        for sv in self.supervisors:
            match_result = sv.component_registry.find_command_by_text(text)
            if match_result is not None:
                comp, matched_groups = match_result
                return {
                    "name": comp.name,
                    "full_name": comp.full_name,
                    "component_type": comp.component_type,
                    "plugin_id": comp.plugin_id,
                    "metadata": comp.metadata,
                    "enabled": comp.enabled,
                    "matched_groups": matched_groups,
                }
        return None

    async def invoke_plugin(
        self,
        method: str,
        plugin_id: str,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Any:
        """将插件调用路由到拥有该插件的 Supervisor"""
        sv = self._get_supervisor_for_plugin(plugin_id)
        if sv is None:
            raise RuntimeError(f"插件 {plugin_id} 未在任何 Supervisor 中注册")
        return await sv.invoke_plugin(
            method=method,
            plugin_id=plugin_id,
            component_name=component_name,
            args=args,
            timeout_ms=timeout_ms,
        )

    async def try_send_message_via_platform_io(
        self,
        message: "SessionMessage",
    ) -> Optional[DeliveryBatch]:
        """尝试通过 Platform IO 中间层发送消息。

        Args:
            message: 待发送的内部会话消息。

        Returns:
            Optional[DeliveryBatch]: 若当前消息命中了至少一条发送路由，则返回
            实际发送结果；若没有可用路由或 Platform IO 尚未启动，则返回 ``None``。
        """
        if not self._started:
            return None

        platform_io_manager = get_platform_io_manager()
        if not platform_io_manager.is_started:
            return None

        try:
            route_key = platform_io_manager.build_route_key_from_message(message)
        except Exception as exc:
            logger.warning(f"根据消息构造 Platform IO 路由键失败: {exc}")
            return None

        if not platform_io_manager.resolve_drivers(route_key):
            return None

        return await platform_io_manager.send_message(message, route_key)

    def _get_supervisors_for_plugin(self, plugin_id: str) -> List["PluginSupervisor"]:
        """返回当前持有指定插件的所有 Supervisor。

        该辅助函数主要用于检测插件是否被重复注册到多个运行时分组，
        供后续单路由选择和冲突检查使用。
        """
        return [supervisor for supervisor in self.supervisors if plugin_id in supervisor._registered_plugins]

    def _get_supervisor_for_plugin(self, plugin_id: str) -> Optional["PluginSupervisor"]:
        """返回负责指定插件的唯一 Supervisor。

        如果同一个插件同时出现在多个 Supervisor 中，说明运行时状态异常，
        此时直接抛出错误，避免把请求路由到错误的子进程。
        """
        matches = self._get_supervisors_for_plugin(plugin_id)
        if len(matches) > 1:
            raise RuntimeError(f"插件 {plugin_id} 同时存在于多个 Supervisor 中，无法安全路由")
        return matches[0] if matches else None

    async def load_plugin_globally(self, plugin_id: str, reason: str = "manual") -> bool:
        """加载或重载单个插件，并为其补齐跨 Supervisor 外部依赖。

        Args:
            plugin_id: 目标插件 ID。
            reason: 加载或重载原因。

        Returns:
            bool: 插件最终是否处于已加载状态。
        """

        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
            return False
        if normalized_plugin_id in self._blocked_plugin_reasons:
            logger.warning(
                f"插件 {normalized_plugin_id} 当前被依赖流水线阻止加载: "
                f"{self._blocked_plugin_reasons[normalized_plugin_id]}"
            )
            return False

        try:
            registered_supervisor = self._get_supervisor_for_plugin(normalized_plugin_id)
        except RuntimeError:
            return False

        if registered_supervisor is not None:
            return await self.reload_plugins_globally([normalized_plugin_id], reason=reason)

        supervisor = self._find_supervisor_by_plugin_directory(normalized_plugin_id)
        if supervisor is None:
            return False

        reloaded = await supervisor.reload_plugins(
            plugin_ids=[normalized_plugin_id],
            reason=reason,
            external_available_plugins=self._build_external_available_plugins_for_supervisor(supervisor),
        )
        return reloaded and normalized_plugin_id in supervisor.get_loaded_plugin_ids()

    @classmethod
    def _find_duplicate_plugin_ids(cls, plugin_dirs: List[Path]) -> Dict[str, List[Path]]:
        """扫描插件目录，找出被多个目录重复声明的插件 ID。"""
        plugin_locations: Dict[str, List[Path]] = {}
        validator = ManifestValidator(
            validate_python_package_dependencies=False,
            log_errors=False,
            log_compat_warnings=False,
        )
        for plugin_path, manifest in validator.iter_plugin_manifests(plugin_dirs):
            plugin_locations.setdefault(manifest.id, []).append(plugin_path)

        return {
            plugin_id: sorted(dict.fromkeys(paths), key=lambda p: str(p))
            for plugin_id, paths in plugin_locations.items()
            if len(set(paths)) > 1
        }

    async def _start_plugin_file_watcher(self) -> None:
        """启动插件文件监视器，并建立源码与配置两类订阅。"""
        if self._plugin_file_watcher is not None and self._plugin_file_watcher.running:
            return

        watch_paths = [path.resolve() for path in self._iter_plugin_dirs() if path.is_dir()]
        if not watch_paths:
            return

        watcher = FileWatcher(
            paths=watch_paths,
            debounce_ms=600,
            callback_timeout_s=15.0,
            callback_failure_threshold=3,
            callback_cooldown_s=30.0,
        )
        subscription_id = watcher.subscribe(self._handle_plugin_source_changes, paths=watch_paths)
        await watcher.start()
        self._plugin_file_watcher = watcher
        self._plugin_source_watcher_subscription_id = subscription_id
        self._refresh_plugin_config_watch_subscriptions()

    async def _stop_plugin_file_watcher(self) -> None:
        """停止插件文件监视器，并清理所有已注册订阅。"""
        if self._plugin_file_watcher is None:
            self._plugin_path_cache.clear()
            return
        for _plugin_id, (_config_path, subscription_id) in list(self._plugin_config_watcher_subscriptions.items()):
            self._plugin_file_watcher.unsubscribe(subscription_id)
        self._plugin_config_watcher_subscriptions.clear()
        if self._plugin_source_watcher_subscription_id is not None:
            self._plugin_file_watcher.unsubscribe(self._plugin_source_watcher_subscription_id)
            self._plugin_source_watcher_subscription_id = None
        await self._plugin_file_watcher.stop()
        self._plugin_file_watcher = None
        self._plugin_path_cache.clear()

    def _iter_plugin_dirs(self) -> Iterable[Path]:
        """迭代所有 Supervisor 当前管理的插件根目录。"""
        for supervisor in self.supervisors:
            yield from getattr(supervisor, "_plugin_dirs", [])

    @staticmethod
    def _iter_candidate_plugin_paths(plugin_dirs: Iterable[Path]) -> Iterable[Path]:
        """迭代所有可能的插件目录路径。

        Args:
            plugin_dirs: 一个或多个插件根目录。

        Yields:
            Path: 单个插件目录路径。
        """
        for plugin_dir in plugin_dirs:
            plugin_root = Path(plugin_dir).resolve()
            if not plugin_root.is_dir():
                continue
            for entry in plugin_root.iterdir():
                if entry.is_dir() and not is_reserved_plugin_directory(entry):
                    yield entry.resolve()

    def _read_plugin_id_from_plugin_path(self, plugin_path: Path) -> Optional[str]:
        """从单个插件目录中读取 manifest 声明的插件 ID。

        Args:
            plugin_path: 单个插件目录路径。

        Returns:
            Optional[str]: 解析成功时返回插件 ID，否则返回 ``None``。
        """
        return self._manifest_validator.read_plugin_id_from_plugin_path(plugin_path)

    def _iter_discovered_plugin_paths(self, plugin_dirs: Iterable[Path]) -> Iterable[Tuple[str, Path]]:
        """迭代目录中可解析到的插件 ID 与实际目录路径。

        Args:
            plugin_dirs: 一个或多个插件根目录。

        Yields:
            Tuple[str, Path]: ``(plugin_id, plugin_path)`` 二元组。
        """
        for plugin_path in self._iter_candidate_plugin_paths(plugin_dirs):
            if plugin_id := self._read_plugin_id_from_plugin_path(plugin_path):
                yield plugin_id, plugin_path

    def _get_plugin_path_for_supervisor(self, supervisor: Any, plugin_id: str) -> Optional[Path]:
        """为指定 Supervisor 定位某个插件的实际目录。

        Args:
            supervisor: 目标 Supervisor。
            plugin_id: 插件 ID。

        Returns:
            Optional[Path]: 插件目录路径；未找到时返回 ``None``。
        """
        cached_path = self._plugin_path_cache.get(plugin_id)
        if cached_path is not None:
            for plugin_dir in getattr(supervisor, "_plugin_dirs", []):
                if self._plugin_dir_matches(cached_path, Path(plugin_dir)) and self._supervisor_accepts_plugin_path(
                    supervisor,
                    cached_path,
                ):
                    return cached_path

        for candidate_plugin_id, plugin_path in self._iter_discovered_plugin_paths(
            getattr(supervisor, "_plugin_dirs", [])
        ):
            if candidate_plugin_id != plugin_id:
                continue
            if not self._supervisor_accepts_plugin_path(supervisor, plugin_path):
                continue
            self._plugin_path_cache[plugin_id] = plugin_path
            return plugin_path

        return None

    def _supervisor_accepts_plugin_path(self, supervisor: Any, plugin_path: Path) -> bool:
        """按 Supervisor 的 manifest 类型过滤配置判断插件目录是否归其管理。"""

        plugin_type_filter = str(getattr(supervisor, "_plugin_type_filter", "") or "").strip().lower()
        if not plugin_type_filter:
            return True

        resolved_plugin_path = plugin_path.resolve()
        trusted_plugin_dirs = [Path(path).resolve() for path in getattr(supervisor, "_trusted_plugin_dirs", [])]
        if plugin_type_filter == "trusted_or_adapter" and any(
            self._plugin_dir_matches(resolved_plugin_path, trusted_dir)
            for trusted_dir in trusted_plugin_dirs
        ):
            return True

        manifest = self._manifest_validator.load_from_plugin_path(resolved_plugin_path, require_entrypoint=True)
        if manifest is None:
            return False

        plugin_type = str(manifest.plugin_type or "extension").strip().lower() or "extension"
        if plugin_type_filter == "trusted_or_adapter":
            return plugin_type == "adapter"
        if plugin_type_filter == "not_adapter":
            return plugin_type != "adapter"
        return plugin_type == plugin_type_filter

    def _refresh_plugin_config_watch_subscriptions(self) -> None:
        """按当前可识别插件集合刷新 config.toml 的单插件订阅。

        当插件热重载后，插件集合或目录位置可能发生变化，因此需要重新对齐
        watcher 的订阅，确保每个插件配置变更只触发对应 plugin_id。
        这里不仅覆盖当前已注册插件，也覆盖已存在但暂未激活的合法插件。
        """
        if self._plugin_file_watcher is None:
            return

        desired_plugin_paths = dict(self._iter_watchable_plugin_paths())
        self._plugin_path_cache = desired_plugin_paths.copy()
        desired_config_paths = {
            plugin_id: self._resolve_plugin_config_path(plugin_id, plugin_path)
            for plugin_id, plugin_path in desired_plugin_paths.items()
        }

        for plugin_id, (_old_path, subscription_id) in list(self._plugin_config_watcher_subscriptions.items()):
            if desired_config_paths.get(plugin_id) == self._plugin_config_watcher_subscriptions[plugin_id][0]:
                continue
            self._plugin_file_watcher.unsubscribe(subscription_id)
            del self._plugin_config_watcher_subscriptions[plugin_id]

        for plugin_id, config_path in desired_config_paths.items():
            existing_subscription = self._plugin_config_watcher_subscriptions.get(plugin_id)
            if existing_subscription is not None and existing_subscription[0] == config_path:
                continue
            subscription_id = self._plugin_file_watcher.subscribe(
                self._build_plugin_config_change_callback(plugin_id),
                paths=[config_path],
            )
            self._plugin_config_watcher_subscriptions[plugin_id] = (config_path, subscription_id)

    def _build_plugin_config_change_callback(self, plugin_id: str) -> Callable[[Sequence[FileChange]], Awaitable[None]]:
        """为指定插件生成配置文件变更回调。"""

        async def _callback(changes: Sequence[FileChange]) -> None:
            """将 watcher 事件转发到指定插件的配置处理逻辑。

            Args:
                changes: 当前批次收集到的文件变更列表。
            """
            await self._handle_plugin_config_changes(plugin_id, changes)

        return _callback

    def _iter_registered_plugin_paths(self) -> Iterable[Tuple[str, Path]]:
        """迭代当前所有已注册插件的实际目录路径。"""
        for supervisor in self.supervisors:
            for plugin_id in getattr(supervisor, "_registered_plugins", {}).keys():
                if plugin_path := self._get_plugin_path_for_supervisor(supervisor, plugin_id):
                    yield plugin_id, plugin_path

    def _iter_watchable_plugin_paths(self) -> Iterable[Tuple[str, Path]]:
        """迭代应被配置监听器追踪的插件目录。

        Returns:
            Iterable[Tuple[str, Path]]: ``(plugin_id, plugin_path)`` 迭代器。
        """

        watchable_plugin_paths = dict(self._iter_discovered_plugin_paths(self._iter_plugin_dirs()))
        for plugin_id, plugin_path in self._iter_registered_plugin_paths():
            watchable_plugin_paths.setdefault(plugin_id, plugin_path)
        yield from watchable_plugin_paths.items()

    def _get_plugin_config_path_for_supervisor(self, supervisor: Any, plugin_id: str) -> Optional[Path]:
        """从指定 Supervisor 的插件目录中定位某个插件的 config.toml。"""
        plugin_path = self._get_plugin_path_for_supervisor(supervisor, plugin_id)
        return None if plugin_path is None else self._resolve_plugin_config_path(plugin_id, plugin_path)

    @staticmethod
    def _resolve_plugin_config_path(plugin_id: str, plugin_path: Path) -> Path:
        return plugin_path / "config.toml"

    async def _handle_plugin_config_changes(self, plugin_id: str, changes: Sequence[FileChange]) -> None:
        """处理单个插件配置文件变化，并定向派发自配置热更新。

        Args:
            plugin_id: 发生配置变更的插件 ID。
            changes: 当前批次收集到的配置文件变更列表。

        """
        if not self._started or not changes:
            return

        try:
            supervisor = self._get_supervisor_for_plugin(plugin_id)
        except RuntimeError as exc:
            logger.warning(f"插件 {plugin_id} 配置监听匹配失败: {exc}")
            return

        if supervisor is None:
            supervisor = self._find_supervisor_by_plugin_directory(plugin_id)
        if supervisor is None:
            return

        plugin_is_loaded = plugin_id in getattr(supervisor, "_registered_plugins", {})

        try:
            snapshot = await supervisor.inspect_plugin_config(plugin_id)
        except Exception as exc:
            logger.warning(f"插件 {plugin_id} 配置文件变更解析失败: {exc}")
            return

        try:
            if plugin_is_loaded and snapshot.enabled:
                delivered = await supervisor.notify_plugin_config_updated(
                    plugin_id=plugin_id,
                    config_data=dict(snapshot.normalized_config),
                    config_version="",
                    config_scope="self",
                )
                if not delivered:
                    logger.warning(f"插件 {plugin_id} 配置文件变更后通知失败")
                return

            if plugin_is_loaded and not snapshot.enabled:
                reloaded = await self.reload_plugins_globally([plugin_id], reason="config_disabled")
                if not reloaded:
                    logger.warning(f"插件 {plugin_id} 禁用配置已写入，但运行时卸载失败")
                return

            if not snapshot.enabled:
                logger.info(f"插件 {plugin_id} 当前处于禁用状态，跳过自动加载")
                return

            loaded = await self.load_plugin_globally(plugin_id, reason="config_enabled")
            if not loaded:
                logger.warning(f"插件 {plugin_id} 配置文件变更后自动加载失败")
        except Exception as exc:
            logger.warning(f"插件 {plugin_id} 配置文件变更处理失败: {exc}")

    async def _handle_plugin_source_changes(self, changes: Sequence[FileChange]) -> None:
        """处理插件源码相关变化。

        这里仅负责源码、清单等会影响插件装载状态的文件；配置文件的变化会由
        单独的 per-plugin watcher 处理，并定向派发给目标插件的
        ``on_config_update()``，避免放大成不必要的跨插件 reload。
        """
        if not self._started or not changes:
            return

        plugin_dirs = list(self._iter_plugin_dirs())
        if duplicate_plugin_ids := self._find_duplicate_plugin_ids(plugin_dirs):
            details = "; ".join(
                f"{plugin_id}: {', '.join(str(path) for path in paths)}"
                for plugin_id, paths in sorted(duplicate_plugin_ids.items())
            )
            logger.error(f"检测到重复插件 ID，跳过本次插件热重载: {details}")
            return

        relevant_source_changes = [
            change.path.resolve()
            for change in changes
            if change.path.name in {"plugin.py", "_manifest.json"} or change.path.suffix == ".py"
        ]
        if not relevant_source_changes:
            return

        dependency_sync_state = await self._sync_plugin_dependencies(plugin_dirs)
        restart_reason = "file_watcher"
        if dependency_sync_state.environment_changed:
            restart_reason = "file_watcher_dependency_install"
        elif dependency_sync_state.blocked_changed_plugin_ids:
            restart_reason = "file_watcher_blocklist_changed"

        restarted = await self._restart_supervisors(restart_reason)
        if not restarted:
            logger.warning(f"插件源码变更后重启 Supervisor 失败: {restart_reason}")

    @staticmethod
    def _plugin_dir_matches(path: Path, plugin_dir: Path) -> bool:
        """判断某个文件路径是否落在指定插件根目录内。"""
        plugin_root = plugin_dir.resolve()
        return path == plugin_root or path.is_relative_to(plugin_root)

    def _match_plugin_id_for_supervisor(self, supervisor: Any, path: Path) -> Optional[str]:
        """根据变更路径为指定 Supervisor 推断受影响的插件 ID。"""
        resolved_path = path.resolve()

        for plugin_id in getattr(supervisor, "_registered_plugins", {}).keys():
            plugin_path = self._get_plugin_path_for_supervisor(supervisor, plugin_id)
            if plugin_path is not None and (resolved_path == plugin_path or resolved_path.is_relative_to(plugin_path)):
                return plugin_id

        for plugin_id, plugin_path in self._plugin_path_cache.items():
            if not self._supervisor_accepts_plugin_path(supervisor, plugin_path):
                continue
            if not any(
                self._plugin_dir_matches(plugin_path, Path(plugin_dir))
                for plugin_dir in getattr(supervisor, "_plugin_dirs", [])
            ):
                continue
            if resolved_path == plugin_path or resolved_path.is_relative_to(plugin_path):
                return plugin_id

        for plugin_id, plugin_path in self._iter_discovered_plugin_paths(getattr(supervisor, "_plugin_dirs", [])):
            if not self._supervisor_accepts_plugin_path(supervisor, plugin_path):
                continue
            if resolved_path == plugin_path or resolved_path.is_relative_to(plugin_path):
                self._plugin_path_cache[plugin_id] = plugin_path
                return plugin_id

        return None

    def _load_plugin_config_for_supervisor(self, supervisor: Any, plugin_id: str) -> Dict[str, Any]:
        """从给定插件目录集合中读取目标插件的配置内容。"""
        plugin_path = self._get_plugin_path_for_supervisor(supervisor, plugin_id)
        if plugin_path is None:
            return {}

        config_path = self._resolve_plugin_config_path(plugin_id, plugin_path)
        if not config_path.exists():
            return {}

        with open(config_path, "r", encoding="utf-8") as handle:
            return tomlkit.load(handle).unwrap()

    # ─── 能力实现注册 ──────────────────────────────────────────

    def _register_capability_impls(self, supervisor: "PluginSupervisor") -> None:
        """向指定 Supervisor 注册主程序能力实现。

        Args:
            supervisor: 需要注册能力实现的目标 Supervisor。
        """
        register_capability_impls(self, supervisor)


# ─── 单例 ──────────────────────────────────────────────────

_manager: Optional[PluginRuntimeManager] = None


def get_plugin_runtime_manager() -> PluginRuntimeManager:
    """获取 PluginRuntimeManager 全局单例"""
    global _manager
    if _manager is None:
        _manager = PluginRuntimeManager()
    return _manager
