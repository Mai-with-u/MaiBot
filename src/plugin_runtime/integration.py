"""插件运行时与主程序的集成层

提供 PluginRuntimeManager 单例，负责：
1. 管理双 PluginSupervisor 的生命周期（内置插件 / 第三方插件各一个子进程）
2. 将 EventType 桥接到运行时的 event dispatch
3. 在运行时的 ComponentRegistry 中查找命令
4. 提供统一的能力实现注册接口，使插件可以调用主程序功能
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import asyncio

import tomlkit

from src.common.logger import get_logger
from src.config.config import config_manager
from src.config.file_watcher import FileChange, FileWatcher
from src.platform_io import DeliveryBatch, InboundMessageEnvelope, get_platform_io_manager
from src.plugin_runtime.capabilities import (
    RuntimeComponentCapabilityMixin,
    RuntimeCoreCapabilityMixin,
    RuntimeDataCapabilityMixin,
)
from src.plugin_runtime.capabilities.registry import register_capability_impls
from src.plugin_runtime.host.message_utils import MessageDict, PluginMessageUtils
from src.plugin_runtime.runner.manifest_validator import ManifestValidator

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
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


class PluginRuntimeManager(
    RuntimeCoreCapabilityMixin,
    RuntimeDataCapabilityMixin,
    RuntimeComponentCapabilityMixin,
):
    """插件运行时管理器（单例）

    内置插件与第三方插件分别运行在各自的 Supervisor / Runner 子进程中。
    """

    def __init__(self) -> None:
        """初始化插件运行时管理器。"""
        from src.plugin_runtime.host.supervisor import PluginSupervisor

        self._builtin_supervisor: Optional[PluginSupervisor] = None
        self._third_party_supervisor: Optional[PluginSupervisor] = None
        self._started: bool = False
        self._plugin_file_watcher: Optional[FileWatcher] = None
        self._plugin_source_watcher_subscription_id: Optional[str] = None
        self._plugin_config_watcher_subscriptions: Dict[str, Tuple[Path, str]] = {}
        self._plugin_path_cache: Dict[str, Path] = {}
        self._manifest_validator: ManifestValidator = ManifestValidator()
        self._config_reload_callback: Callable[[Sequence[str]], Awaitable[None]] = self._handle_main_config_reload
        self._config_reload_callback_registered: bool = False

    async def _dispatch_platform_inbound(self, envelope: InboundMessageEnvelope) -> None:
        """接收 Platform IO 审核后的入站消息并送入主消息链。

        Args:
            envelope: Platform IO 产出的入站封装。
        """
        session_message = envelope.session_message
        if session_message is None and envelope.payload is not None:
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
        validator = ManifestValidator()
        return validator.build_plugin_dependency_map(plugin_dirs)

    @classmethod
    def _build_group_start_order(
        cls,
        builtin_dirs: Sequence[Path],
        third_party_dirs: Sequence[Path],
    ) -> List[str]:
        """根据跨 Supervisor 依赖关系决定 Runner 启动顺序。"""

        builtin_dependencies = cls._discover_plugin_dependency_map(builtin_dirs)
        third_party_dependencies = cls._discover_plugin_dependency_map(third_party_dirs)
        builtin_plugin_ids = set(builtin_dependencies)
        third_party_plugin_ids = set(third_party_dependencies)

        builtin_needs_third_party = any(
            dependency in third_party_plugin_ids
            for dependencies in builtin_dependencies.values()
            for dependency in dependencies
        )
        third_party_needs_builtin = any(
            dependency in builtin_plugin_ids
            for dependencies in third_party_dependencies.values()
            for dependency in dependencies
        )

        if builtin_needs_third_party and third_party_needs_builtin:
            raise RuntimeError("检测到跨 Supervisor 循环依赖，当前无法安全启动独立 Runner")
        if builtin_needs_third_party:
            return ["third_party", "builtin"]
        return ["builtin", "third_party"]

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

        from src.plugin_runtime.host.supervisor import PluginSupervisor

        builtin_dirs = self._get_builtin_plugin_dirs()
        third_party_dirs = self._get_third_party_plugin_dirs()

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

        platform_io_manager = get_platform_io_manager()

        # 从配置读取自定义 IPC socket 路径（留空则自动生成）
        socket_path_base = _cfg.ipc_socket_path or None

        # 当用户指定了自定义路径时，为两个 Supervisor 添加后缀以避免 UDS 冲突
        builtin_socket = f"{socket_path_base}-builtin" if socket_path_base else None
        third_party_socket = f"{socket_path_base}-third_party" if socket_path_base else None

        # 创建两个 Supervisor，各自拥有独立的 socket / Runner 子进程
        if builtin_dirs:
            self._builtin_supervisor = PluginSupervisor(
                plugin_dirs=builtin_dirs,
                socket_path=builtin_socket,
            )
            self._register_capability_impls(self._builtin_supervisor)

        if third_party_dirs:
            self._third_party_supervisor = PluginSupervisor(
                plugin_dirs=third_party_dirs,
                socket_path=third_party_socket,
            )
            self._register_capability_impls(self._third_party_supervisor)

        started_supervisors: List[PluginSupervisor] = []
        try:
            platform_io_manager.set_inbound_dispatcher(self._dispatch_platform_inbound)
            await platform_io_manager.ensure_send_pipeline_ready()

            supervisor_groups: Dict[str, Optional[PluginSupervisor]] = {
                "builtin": self._builtin_supervisor,
                "third_party": self._third_party_supervisor,
            }
            start_order = self._build_group_start_order(builtin_dirs, third_party_dirs)

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
                await supervisor.start()
                started_supervisors.append(supervisor)

            await self._start_plugin_file_watcher()
            config_manager.register_reload_callback(self._config_reload_callback)
            self._config_reload_callback_registered = True
            self._started = True
            logger.info(f"插件运行时已启动 — 内置: {builtin_dirs or '无'}, 第三方: {third_party_dirs or '无'}")
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
            self._started = False
            self._builtin_supervisor = None
            self._third_party_supervisor = None
            self._plugin_path_cache.clear()

    @property
    def is_running(self) -> bool:
        """返回插件运行时是否处于启动状态。"""
        return self._started

    @property
    def supervisors(self) -> List["PluginSupervisor"]:
        """获取所有活跃的 Supervisor"""
        return [s for s in (self._builtin_supervisor, self._third_party_supervisor) if s is not None]

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
            plugin_id: supervisor
            for supervisor in self.supervisors
            for plugin_id in supervisor.get_loaded_plugin_ids()
        }

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
                    dependency
                    for dependency in dependency_map.get(plugin_id, set())
                    if dependency in local_plugin_ids
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
            normalized_plugin_id
            for plugin_id in plugin_ids
            if (normalized_plugin_id := str(plugin_id or "").strip())
        ]
        if not normalized_plugin_ids:
            return True

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
            config_data
            if config_data is not None
            else self._load_plugin_config_for_supervisor(sv, plugin_id)
        )
        return await sv.notify_plugin_config_updated(
            plugin_id=plugin_id,
            config_data=config_payload,
            config_version=config_version,
            config_scope=config_scope,
        )

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
            await self._broadcast_config_reload("bot", config_manager.get_global_config().model_dump())
        if "model" in normalized_scopes:
            await self._broadcast_config_reload("model", config_manager.get_model_config().model_dump())

    # ─── 事件桥接 ──────────────────────────────────────────────

    async def bridge_event(
        self,
        event_type_value: str,
        message_dict: Optional[MessageDict] = None,
        extra_args: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[MessageDict]]:
        """将事件分发到所有 Supervisor

        Returns:
            (continue_flag, modified_message_dict)
        """
        if not self._started:
            return True, None

        new_event_type: str = _EVENT_TYPE_MAP.get(event_type_value, event_type_value)
        modified: Optional[MessageDict] = None
        current_message: Optional["SessionMessage"] = (
            PluginMessageUtils._build_session_message_from_dict(dict(message_dict))
            if message_dict is not None
            else None
        )

        for sv in self.supervisors:
            try:
                cont, mod = await sv.dispatch_event(
                    event_type=new_event_type,
                    message=current_message,
                    extra_args=extra_args,
                )
                if mod is not None:
                    current_message = mod
                    modified = PluginMessageUtils._session_message_to_dict(mod)
                if not cont:
                    return False, modified
            except Exception as e:
                logger.error(f"事件 {new_event_type} 分发失败: {e}", exc_info=True)

        return True, modified

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
        """加载或重载单个插件，并为其补齐跨 Supervisor 外部依赖。"""

        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
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

        return await supervisor.reload_plugins(
            plugin_ids=[normalized_plugin_id],
            reason=reason,
            external_available_plugins=self._build_external_available_plugins_for_supervisor(supervisor),
        )

    @classmethod
    def _find_duplicate_plugin_ids(cls, plugin_dirs: List[Path]) -> Dict[str, List[Path]]:
        """扫描插件目录，找出被多个目录重复声明的插件 ID。"""
        plugin_locations: Dict[str, List[Path]] = {}
        validator = ManifestValidator()
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
                if entry.is_dir():
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
                if self._plugin_dir_matches(cached_path, Path(plugin_dir)):
                    return cached_path

        for candidate_plugin_id, plugin_path in self._iter_discovered_plugin_paths(getattr(supervisor, "_plugin_dirs", [])):
            if candidate_plugin_id != plugin_id:
                continue
            self._plugin_path_cache[plugin_id] = plugin_path
            return plugin_path

        return None

    def _refresh_plugin_config_watch_subscriptions(self) -> None:
        """按当前已注册插件集合刷新 config.toml 的单插件订阅。

        当插件热重载后，插件集合或目录位置可能发生变化，因此需要重新对齐
        watcher 的订阅，确保每个插件配置变更只触发对应 plugin_id。
        """
        if self._plugin_file_watcher is None:
            return

        desired_plugin_paths = dict(self._iter_registered_plugin_paths())
        self._plugin_path_cache = desired_plugin_paths.copy()
        desired_config_paths = {
            plugin_id: plugin_path / "config.toml" for plugin_id, plugin_path in desired_plugin_paths.items()
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

    def _build_plugin_config_change_callback(
        self, plugin_id: str
    ) -> Callable[[Sequence[FileChange]], Awaitable[None]]:
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

    def _get_plugin_config_path_for_supervisor(self, supervisor: Any, plugin_id: str) -> Optional[Path]:
        """从指定 Supervisor 的插件目录中定位某个插件的 config.toml。"""
        plugin_path = self._get_plugin_path_for_supervisor(supervisor, plugin_id)
        return None if plugin_path is None else plugin_path / "config.toml"

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
            return

        try:
            config_payload = self._load_plugin_config_for_supervisor(supervisor, plugin_id)
            delivered = await supervisor.notify_plugin_config_updated(
                plugin_id=plugin_id,
                config_data=config_payload,
                config_version="",
                config_scope="self",
            )
            if not delivered:
                logger.warning(f"插件 {plugin_id} 配置文件变更后通知失败")
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

        if duplicate_plugin_ids := self._find_duplicate_plugin_ids(list(self._iter_plugin_dirs())):
            details = "; ".join(
                f"{plugin_id}: {', '.join(str(path) for path in paths)}"
                for plugin_id, paths in sorted(duplicate_plugin_ids.items())
            )
            logger.error(f"检测到重复插件 ID，跳过本次插件热重载: {details}")
            return

        changed_plugin_ids: List[str] = []
        changed_paths = [change.path.resolve() for change in changes]

        for supervisor in self.supervisors:
            for path in changed_paths:
                plugin_id = self._match_plugin_id_for_supervisor(supervisor, path)
                if plugin_id is None:
                    continue
                if path.name in {"plugin.py", "_manifest.json"} or path.suffix == ".py":
                    if plugin_id not in changed_plugin_ids:
                        changed_plugin_ids.append(plugin_id)

        if changed_plugin_ids:
            await self.reload_plugins_globally(changed_plugin_ids, reason="file_watcher")
            self._refresh_plugin_config_watch_subscriptions()

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
            if not any(self._plugin_dir_matches(plugin_path, Path(plugin_dir)) for plugin_dir in getattr(supervisor, "_plugin_dirs", [])):
                continue
            if resolved_path == plugin_path or resolved_path.is_relative_to(plugin_path):
                return plugin_id

        for plugin_id, plugin_path in self._iter_discovered_plugin_paths(getattr(supervisor, "_plugin_dirs", [])):
            if resolved_path == plugin_path or resolved_path.is_relative_to(plugin_path):
                self._plugin_path_cache[plugin_id] = plugin_path
                return plugin_id

        return None

    def _load_plugin_config_for_supervisor(self, supervisor: Any, plugin_id: str) -> Dict[str, Any]:
        """从给定插件目录集合中读取目标插件的配置内容。"""
        plugin_path = self._get_plugin_path_for_supervisor(supervisor, plugin_id)
        if plugin_path is None:
            return {}

        config_path = plugin_path / "config.toml"
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
