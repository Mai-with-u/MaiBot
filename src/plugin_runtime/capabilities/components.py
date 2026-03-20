from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Protocol

from src.common.logger import get_logger

logger = get_logger("plugin_runtime.integration")

if TYPE_CHECKING:
    from src.plugin_runtime.host.component_registry import RegisteredComponent
    from src.plugin_runtime.host.supervisor import PluginSupervisor


class _RuntimeComponentManagerProtocol(Protocol):
    @property
    def supervisors(self) -> List["PluginSupervisor"]: ...

    def _get_supervisor_for_plugin(self, plugin_id: str) -> Optional["PluginSupervisor"]: ...

    def _resolve_component_toggle_target(
        self, name: str, component_type: str
    ) -> tuple[Optional["RegisteredComponent"], Optional[str]]: ...

    def _find_duplicate_plugin_ids(self, plugin_dirs: List[Path]) -> Dict[str, List[Path]]: ...

    def _iter_plugin_dirs(self) -> Iterable[Path]: ...


class RuntimeComponentCapabilityMixin:
    async def _cap_component_get_all_plugins(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        result: Dict[str, Any] = {}
        for sv in self.supervisors:
            for pid, reg in sv._registered_plugins.items():
                if pid in result:
                    logger.error(f"检测到重复插件 ID {pid}，component.get_all_plugins 结果已拒绝聚合")
                    return {"success": False, "error": f"检测到重复插件 ID: {pid}"}
                comps = sv.component_registry.get_components_by_plugin(pid, enabled_only=False)
                components_list = [
                    {
                        "name": component.name,
                        "full_name": component.full_name,
                        "type": component.component_type,
                        "enabled": component.enabled,
                        "metadata": component.metadata,
                    }
                    for component in comps
                ]
                result[pid] = {
                    "name": pid,
                    "version": reg.plugin_version,
                    "description": "",
                    "author": "",
                    "enabled": True,
                    "components": components_list,
                }
        return {"success": True, "plugins": result}

    async def _cap_component_get_plugin_info(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        plugin_name: str = args.get("plugin_name", plugin_id)
        try:
            sv = self._get_supervisor_for_plugin(plugin_name)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        if sv is not None and (reg := sv._registered_plugins.get(plugin_name)) is not None:
            return {
                "success": True,
                "plugin": {
                    "name": plugin_name,
                    "version": reg.plugin_version,
                    "description": "",
                    "author": "",
                    "enabled": True,
                },
            }
        return {"success": False, "error": f"未找到插件: {plugin_name}"}

    async def _cap_component_list_loaded_plugins(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        plugins: List[str] = []
        for sv in self.supervisors:
            plugins.extend(sv._registered_plugins.keys())
        return {"success": True, "plugins": plugins}

    async def _cap_component_list_registered_plugins(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        plugins: List[str] = []
        for sv in self.supervisors:
            plugins.extend(sv._registered_plugins.keys())
        return {"success": True, "plugins": plugins}

    def _resolve_component_toggle_target(
        self: _RuntimeComponentManagerProtocol, name: str, component_type: str
    ) -> tuple[Optional["RegisteredComponent"], Optional[str]]:
        short_name_matches: List["RegisteredComponent"] = []
        for sv in self.supervisors:
            comp = sv.component_registry.get_component(name)
            if comp is not None and comp.component_type == component_type:
                return comp, None

            short_name_matches.extend(
                candidate
                for candidate in sv.component_registry.get_components_by_type(component_type, enabled_only=False)
                if candidate.name == name
            )

        if len(short_name_matches) == 1:
            return short_name_matches[0], None
        if len(short_name_matches) > 1:
            return None, f"组件名不唯一: {name} ({component_type})，请使用完整名 plugin_id.component_name"
        return None, f"未找到组件: {name} ({component_type})"

    async def _cap_component_enable(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        name: str = args.get("name", "")
        component_type: str = args.get("component_type", "")
        scope: str = args.get("scope", "global")
        stream_id: str = args.get("stream_id", "")
        if not name or not component_type:
            return {"success": False, "error": "缺少必要参数 name 或 component_type"}
        if scope != "global" or stream_id:
            return {"success": False, "error": "当前仅支持全局组件启用，不支持 scope/stream_id 定位"}

        comp, error = self._resolve_component_toggle_target(name, component_type)
        if comp is None:
            return {"success": False, "error": error or f"未找到组件: {name} ({component_type})"}

        comp.enabled = True
        return {"success": True}

    async def _cap_component_disable(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        name: str = args.get("name", "")
        component_type: str = args.get("component_type", "")
        scope: str = args.get("scope", "global")
        stream_id: str = args.get("stream_id", "")
        if not name or not component_type:
            return {"success": False, "error": "缺少必要参数 name 或 component_type"}
        if scope != "global" or stream_id:
            return {"success": False, "error": "当前仅支持全局组件禁用，不支持 scope/stream_id 定位"}

        comp, error = self._resolve_component_toggle_target(name, component_type)
        if comp is None:
            return {"success": False, "error": error or f"未找到组件: {name} ({component_type})"}

        comp.enabled = False
        return {"success": True}

    async def _cap_component_load_plugin(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        plugin_name: str = args.get("plugin_name", "")
        if not plugin_name:
            return {"success": False, "error": "缺少必要参数 plugin_name"}

        if duplicate_plugin_ids := self._find_duplicate_plugin_ids(list(self._iter_plugin_dirs())):
            details = "; ".join(
                f"{conflict_plugin_id}: {', '.join(str(path) for path in paths)}"
                for conflict_plugin_id, paths in sorted(duplicate_plugin_ids.items())
            )
            return {"success": False, "error": f"检测到重复插件 ID，拒绝热重载: {details}"}

        try:
            registered_supervisor = self._get_supervisor_for_plugin(plugin_name)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        if registered_supervisor is not None:
            try:
                reloaded = await registered_supervisor.reload_plugins(
                    plugin_ids=[plugin_name],
                    reason=f"load {plugin_name}",
                )
                if reloaded:
                    return {"success": True, "count": 1}
                return {"success": False, "error": f"插件 {plugin_name} 热重载失败，已回滚"}
            except Exception as e:
                logger.error(f"[cap.component.load_plugin] 热重载失败: {e}")
                return {"success": False, "error": str(e)}

        for sv in self.supervisors:
            for pdir in sv._plugin_dirs:
                if (pdir / plugin_name).is_dir():
                    try:
                        reloaded = await sv.reload_plugins(
                            plugin_ids=[plugin_name],
                            reason=f"load {plugin_name}",
                        )
                        if reloaded:
                            return {"success": True, "count": 1}
                        return {"success": False, "error": f"插件 {plugin_name} 热重载失败，已回滚"}
                    except Exception as e:
                        logger.error(f"[cap.component.load_plugin] 热重载失败: {e}")
                        return {"success": False, "error": str(e)}

        return {"success": False, "error": f"未找到插件: {plugin_name}"}

    async def _cap_component_unload_plugin(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        return {"success": False, "error": "新运行时不支持单独卸载插件，请使用 reload"}

    async def _cap_component_reload_plugin(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        plugin_name: str = args.get("plugin_name", "")
        if not plugin_name:
            return {"success": False, "error": "缺少必要参数 plugin_name"}

        if duplicate_plugin_ids := self._find_duplicate_plugin_ids(list(self._iter_plugin_dirs())):
            details = "; ".join(
                f"{conflict_plugin_id}: {', '.join(str(path) for path in paths)}"
                for conflict_plugin_id, paths in sorted(duplicate_plugin_ids.items())
            )
            return {"success": False, "error": f"检测到重复插件 ID，拒绝热重载: {details}"}

        try:
            sv = self._get_supervisor_for_plugin(plugin_name)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        if sv is not None:
            try:
                reloaded = await sv.reload_plugins(
                    plugin_ids=[plugin_name],
                    reason=f"reload {plugin_name}",
                )
                if reloaded:
                    return {"success": True}
                return {"success": False, "error": f"插件 {plugin_name} 热重载失败，已回滚"}
            except Exception as e:
                logger.error(f"[cap.component.reload_plugin] 热重载失败: {e}")
                return {"success": False, "error": str(e)}
        return {"success": False, "error": f"未找到插件: {plugin_name}"}
