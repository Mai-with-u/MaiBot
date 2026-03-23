from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Protocol, Sequence

from src.common.logger import get_logger

logger = get_logger("plugin_runtime.integration")

if TYPE_CHECKING:
    from src.plugin_runtime.host.api_registry import APIEntry
    from src.plugin_runtime.host.component_registry import ComponentEntry
    from src.plugin_runtime.host.supervisor import PluginSupervisor


class _RuntimeComponentManagerProtocol(Protocol):
    @property
    def supervisors(self) -> List["PluginSupervisor"]: ...

    def _normalize_component_type(self, component_type: str) -> str: ...

    def _is_api_component_type(self, component_type: str) -> bool: ...

    def _serialize_api_entry(self, entry: "APIEntry") -> Dict[str, Any]: ...

    def _serialize_api_component_entry(self, entry: "APIEntry") -> Dict[str, Any]: ...

    def _is_api_visible_to_plugin(self, entry: "APIEntry", caller_plugin_id: str) -> bool: ...

    def _normalize_api_reference(self, api_name: str, version: str = "") -> tuple[str, str]: ...

    def _build_api_unavailable_error(self, entry: "APIEntry") -> str: ...

    def _get_supervisor_for_plugin(self, plugin_id: str) -> Optional["PluginSupervisor"]: ...

    def _resolve_api_target(
        self,
        caller_plugin_id: str,
        api_name: str,
        version: str = "",
    ) -> tuple[Optional["PluginSupervisor"], Optional["APIEntry"], Optional[str]]: ...

    def _resolve_api_toggle_target(
        self,
        name: str,
        version: str = "",
    ) -> tuple[Optional["PluginSupervisor"], Optional["APIEntry"], Optional[str]]: ...

    def _resolve_component_toggle_target(
        self, name: str, component_type: str
    ) -> tuple[Optional["ComponentEntry"], Optional[str]]: ...

    def _find_duplicate_plugin_ids(self, plugin_dirs: List[Path]) -> Dict[str, List[Path]]: ...

    def _iter_plugin_dirs(self) -> Iterable[Path]: ...

    async def load_plugin_globally(self, plugin_id: str, reason: str = "manual") -> bool: ...

    async def reload_plugins_globally(self, plugin_ids: Sequence[str], reason: str = "manual") -> bool: ...


class RuntimeComponentCapabilityMixin:
    @staticmethod
    def _normalize_component_type(component_type: str) -> str:
        """规范化组件类型名称。

        Args:
            component_type: 原始组件类型。

        Returns:
            str: 统一转为大写后的组件类型名。
        """

        return str(component_type or "").strip().upper()

    @classmethod
    def _is_api_component_type(cls, component_type: str) -> bool:
        """判断组件类型是否为 API。

        Args:
            component_type: 原始组件类型。

        Returns:
            bool: 是否为 API 组件类型。
        """

        return cls._normalize_component_type(component_type) == "API"

    @staticmethod
    def _serialize_api_entry(entry: "APIEntry") -> Dict[str, Any]:
        """将 API 组件条目序列化为能力返回值。

        Args:
            entry: API 组件条目。

        Returns:
            Dict[str, Any]: 适合通过能力层返回给插件的 API 元信息。
        """

        return {
            "name": entry.name,
            "full_name": entry.full_name,
            "plugin_id": entry.plugin_id,
            "description": entry.description,
            "version": entry.version,
            "public": entry.public,
            "enabled": entry.enabled,
            "dynamic": entry.dynamic,
            "offline_reason": entry.offline_reason,
            "metadata": dict(entry.metadata),
        }

    @classmethod
    def _serialize_api_component_entry(cls, entry: "APIEntry") -> Dict[str, Any]:
        """将 API 条目序列化为通用组件视图。

        Args:
            entry: API 组件条目。

        Returns:
            Dict[str, Any]: 适合 ``component.get_all_plugins`` 返回的组件结构。
        """

        serialized_entry = cls._serialize_api_entry(entry)
        return {
            "name": serialized_entry["name"],
            "full_name": serialized_entry["full_name"],
            "type": "API",
            "enabled": serialized_entry["enabled"],
            "metadata": serialized_entry["metadata"],
        }

    @staticmethod
    def _is_api_visible_to_plugin(entry: "APIEntry", caller_plugin_id: str) -> bool:
        """判断某个 API 是否对调用方可见。

        Args:
            entry: 目标 API 组件条目。
            caller_plugin_id: 调用方插件 ID。

        Returns:
            bool: 是否允许当前插件可见并调用。
        """

        return entry.plugin_id == caller_plugin_id or entry.public

    @staticmethod
    def _normalize_api_reference(api_name: str, version: str = "") -> tuple[str, str]:
        """规范化 API 名称与版本参数。

        支持在 ``api_name`` 中直接携带 ``@version`` 后缀。
        """

        normalized_api_name = str(api_name or "").strip()
        normalized_version = str(version or "").strip()
        if normalized_api_name and not normalized_version and "@" in normalized_api_name:
            candidate_name, candidate_version = normalized_api_name.rsplit("@", 1)
            candidate_name = candidate_name.strip()
            candidate_version = candidate_version.strip()
            if candidate_name and candidate_version:
                normalized_api_name = candidate_name
                normalized_version = candidate_version
        return normalized_api_name, normalized_version

    @staticmethod
    def _build_api_unavailable_error(entry: "APIEntry") -> str:
        """构造 API 当前不可用时的错误信息。"""

        if entry.offline_reason:
            return entry.offline_reason
        return f"API {entry.registry_key} 当前不可用"

    def _resolve_api_target(
        self: _RuntimeComponentManagerProtocol,
        caller_plugin_id: str,
        api_name: str,
        version: str = "",
    ) -> tuple[Optional["PluginSupervisor"], Optional["APIEntry"], Optional[str]]:
        """解析 API 名称到唯一可调用的目标组件。

        Args:
            caller_plugin_id: 调用方插件 ID。
            api_name: API 名称，支持 ``plugin_id.api_name`` 或唯一短名。
            version: 可选的 API 版本。

        Returns:
            tuple[Optional[PluginSupervisor], Optional[APIEntry], Optional[str]]:
                解析成功时返回 ``(监督器, API 条目, None)``，失败时返回错误信息。
        """

        normalized_api_name, normalized_version = self._normalize_api_reference(api_name, version)
        if not normalized_api_name:
            return None, None, "缺少必要参数 api_name"

        if "." in normalized_api_name:
            target_plugin_id, target_api_name = normalized_api_name.rsplit(".", 1)
            try:
                supervisor = self._get_supervisor_for_plugin(target_plugin_id)
            except RuntimeError as exc:
                return None, None, str(exc)

            if supervisor is None:
                return None, None, f"未找到 API 提供方插件: {target_plugin_id}"

            entries = supervisor.api_registry.get_apis(
                plugin_id=target_plugin_id,
                name=target_api_name,
                version=normalized_version,
                enabled_only=False,
            )
            visible_enabled_entries = [
                entry
                for entry in entries
                if self._is_api_visible_to_plugin(entry, caller_plugin_id) and entry.enabled
            ]
            visible_disabled_entries = [
                entry
                for entry in entries
                if self._is_api_visible_to_plugin(entry, caller_plugin_id) and not entry.enabled
            ]
            if len(visible_enabled_entries) == 1:
                return supervisor, visible_enabled_entries[0], None
            if len(visible_enabled_entries) > 1:
                return None, None, f"API {normalized_api_name} 存在多个版本，请显式指定 version"
            if visible_disabled_entries:
                if len(visible_disabled_entries) == 1:
                    return None, None, self._build_api_unavailable_error(visible_disabled_entries[0])
                return None, None, f"API {normalized_api_name} 存在多个已下线版本，请显式指定 version"
            if any(not self._is_api_visible_to_plugin(entry, caller_plugin_id) for entry in entries):
                return None, None, f"API {normalized_api_name} 未公开，禁止跨插件调用"
            if normalized_version:
                return None, None, f"未找到版本为 {normalized_version} 的 API: {normalized_api_name}"
            return None, None, f"未找到 API: {normalized_api_name}"

        visible_enabled_matches: List[tuple["PluginSupervisor", "APIEntry"]] = []
        visible_disabled_matches: List[tuple["PluginSupervisor", "APIEntry"]] = []
        hidden_match_exists = False
        for supervisor in self.supervisors:
            for entry in supervisor.api_registry.get_apis(
                name=normalized_api_name,
                version=normalized_version,
                enabled_only=False,
            ):
                if self._is_api_visible_to_plugin(entry, caller_plugin_id):
                    if entry.enabled:
                        visible_enabled_matches.append((supervisor, entry))
                    else:
                        visible_disabled_matches.append((supervisor, entry))
                else:
                    hidden_match_exists = True

        if len(visible_enabled_matches) == 1:
            return visible_enabled_matches[0][0], visible_enabled_matches[0][1], None
        if len(visible_enabled_matches) > 1:
            return None, None, f"API 名称不唯一: {normalized_api_name}，请使用 plugin_id.api_name 或显式指定 version"
        if visible_disabled_matches:
            if len(visible_disabled_matches) == 1:
                return None, None, self._build_api_unavailable_error(visible_disabled_matches[0][1])
            return None, None, f"API {normalized_api_name} 存在多个已下线版本，请使用 plugin_id.api_name@version"
        if hidden_match_exists:
            return None, None, f"API {normalized_api_name} 未公开，禁止跨插件调用"
        if normalized_version:
            return None, None, f"未找到版本为 {normalized_version} 的 API: {normalized_api_name}"
        return None, None, f"未找到 API: {normalized_api_name}"

    def _resolve_api_toggle_target(
        self: _RuntimeComponentManagerProtocol,
        name: str,
        version: str = "",
    ) -> tuple[Optional["PluginSupervisor"], Optional["APIEntry"], Optional[str]]:
        """解析需要启用或禁用的 API 组件。

        Args:
            name: API 名称，支持 ``plugin_id.api_name`` 或唯一短名。
            version: 可选的 API 版本。

        Returns:
            tuple[Optional[PluginSupervisor], Optional[APIEntry], Optional[str]]:
                解析成功时返回 ``(监督器, API 条目, None)``，失败时返回错误信息。
        """

        normalized_name, normalized_version = self._normalize_api_reference(name, version)
        if not normalized_name:
            return None, None, "缺少必要参数 name"

        if "." in normalized_name:
            plugin_id, api_name = normalized_name.rsplit(".", 1)
            try:
                supervisor = self._get_supervisor_for_plugin(plugin_id)
            except RuntimeError as exc:
                return None, None, str(exc)

            if supervisor is None:
                return None, None, f"未找到 API 提供方插件: {plugin_id}"

            entries = supervisor.api_registry.get_apis(
                plugin_id=plugin_id,
                name=api_name,
                version=normalized_version,
                enabled_only=False,
            )
            if len(entries) == 1:
                return supervisor, entries[0], None
            if entries:
                return None, None, f"API {normalized_name} 存在多个版本，请显式指定 version"
            return None, None, f"未找到 API: {normalized_name}"

        matches: List[tuple["PluginSupervisor", "APIEntry"]] = []
        for supervisor in self.supervisors:
            matches.extend(
                (supervisor, entry)
                for entry in supervisor.api_registry.get_apis(
                    name=normalized_name,
                    version=normalized_version,
                    enabled_only=False,
                )
            )

        if len(matches) == 1:
            return matches[0][0], matches[0][1], None
        if len(matches) > 1:
            return None, None, f"API 名称不唯一: {normalized_name}，请使用 plugin_id.api_name 或显式指定 version"
        return None, None, f"未找到 API: {normalized_name}"

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
                components_list.extend(
                    self._serialize_api_component_entry(entry)
                    for entry in sv.api_registry.get_apis(plugin_id=pid, enabled_only=False)
                )
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
    ) -> tuple[Optional["ComponentEntry"], Optional[str]]:
        normalized_component_type = self._normalize_component_type(component_type)
        short_name_matches: List["ComponentEntry"] = []
        for sv in self.supervisors:
            comp = sv.component_registry.get_component(name)
            if comp is not None and comp.component_type == normalized_component_type:
                return comp, None

            short_name_matches.extend(
                candidate
                for candidate in sv.component_registry.get_components_by_type(
                    normalized_component_type,
                    enabled_only=False,
                )
                if candidate.name == name
            )

        if len(short_name_matches) == 1:
            return short_name_matches[0], None
        if len(short_name_matches) > 1:
            return None, f"组件名不唯一: {name} ({normalized_component_type})，请使用完整名 plugin_id.component_name"
        return None, f"未找到组件: {name} ({normalized_component_type})"

    async def _cap_component_enable(
        self: _RuntimeComponentManagerProtocol, plugin_id: str, capability: str, args: Dict[str, Any]
    ) -> Any:
        name: str = args.get("name", "")
        component_type: str = args.get("component_type", "")
        version: str = args.get("version", "")
        scope: str = args.get("scope", "global")
        stream_id: str = args.get("stream_id", "")
        if not name or not component_type:
            return {"success": False, "error": "缺少必要参数 name 或 component_type"}
        if scope != "global" or stream_id:
            return {"success": False, "error": "当前仅支持全局组件启用，不支持 scope/stream_id 定位"}

        if self._is_api_component_type(component_type):
            supervisor, api_entry, error = self._resolve_api_toggle_target(name, version)
            if supervisor is None or api_entry is None:
                return {"success": False, "error": error or f"未找到 API: {name}"}
            supervisor.api_registry.toggle_api_status(api_entry.registry_key, True)
            return {"success": True}

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
        version: str = args.get("version", "")
        scope: str = args.get("scope", "global")
        stream_id: str = args.get("stream_id", "")
        if not name or not component_type:
            return {"success": False, "error": "缺少必要参数 name 或 component_type"}
        if scope != "global" or stream_id:
            return {"success": False, "error": "当前仅支持全局组件禁用，不支持 scope/stream_id 定位"}

        if self._is_api_component_type(component_type):
            supervisor, api_entry, error = self._resolve_api_toggle_target(name, version)
            if supervisor is None or api_entry is None:
                return {"success": False, "error": error or f"未找到 API: {name}"}
            supervisor.api_registry.toggle_api_status(api_entry.registry_key, False)
            return {"success": True}

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
            loaded = await self.load_plugin_globally(plugin_name, reason=f"load {plugin_name}")
        except Exception as e:
            logger.error(f"[cap.component.load_plugin] 热重载失败: {e}")
            return {"success": False, "error": str(e)}

        if loaded:
            return {"success": True, "count": 1}
        return {"success": False, "error": f"插件 {plugin_name} 热重载失败"}

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
            reloaded = await self.reload_plugins_globally([plugin_name], reason=f"reload {plugin_name}")
        except Exception as e:
            logger.error(f"[cap.component.reload_plugin] 热重载失败: {e}")
            return {"success": False, "error": str(e)}

        if reloaded:
            return {"success": True}
        return {"success": False, "error": f"插件 {plugin_name} 热重载失败"}

    async def _cap_api_call(
        self: _RuntimeComponentManagerProtocol,
        plugin_id: str,
        capability: str,
        args: Dict[str, Any],
    ) -> Any:
        """调用其他插件公开的 API。

        Args:
            plugin_id: 当前调用方插件 ID。
            capability: 能力名称。
            args: 能力参数。

        Returns:
            Any: API 调用结果。
        """

        del capability
        api_name = str(args.get("api_name", "") or "").strip()
        version = str(args.get("version", "") or "").strip()
        api_args = args.get("args", {})
        if not isinstance(api_args, dict):
            return {"success": False, "error": "参数 args 必须为字典"}

        supervisor, entry, error = self._resolve_api_target(plugin_id, api_name, version)
        if supervisor is None or entry is None:
            return {"success": False, "error": error or "API 解析失败"}

        invoke_args = dict(api_args)
        if entry.dynamic:
            invoke_args.setdefault("__maibot_api_name__", entry.name)
            invoke_args.setdefault("__maibot_api_full_name__", entry.full_name)
            invoke_args.setdefault("__maibot_api_version__", entry.version)

        try:
            response = await supervisor.invoke_api(
                plugin_id=entry.plugin_id,
                component_name=entry.handler_name,
                args=invoke_args,
                timeout_ms=30000,
            )
        except Exception as exc:
            logger.error(f"[cap.api.call] 调用 API {entry.full_name} 失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

        if response.error:
            return {"success": False, "error": response.error.get("message", "API 调用失败")}

        payload = response.payload if isinstance(response.payload, dict) else {}
        if not bool(payload.get("success", False)):
            result = payload.get("result")
            return {"success": False, "error": "" if result is None else str(result)}
        return {"success": True, "result": payload.get("result")}

    async def _cap_api_get(
        self: _RuntimeComponentManagerProtocol,
        plugin_id: str,
        capability: str,
        args: Dict[str, Any],
    ) -> Any:
        """获取当前插件可见的单个 API 元信息。

        Args:
            plugin_id: 当前调用方插件 ID。
            capability: 能力名称。
            args: 能力参数。

        Returns:
            Any: API 元信息或 ``None``。
        """

        del capability
        api_name = str(args.get("api_name", "") or "").strip()
        version = str(args.get("version", "") or "").strip()
        if not api_name:
            return {"success": False, "error": "缺少必要参数 api_name"}

        supervisor, entry, _error = self._resolve_api_target(plugin_id, api_name, version)
        if supervisor is None or entry is None:
            return {"success": True, "api": None}
        return {"success": True, "api": self._serialize_api_entry(entry)}

    async def _cap_api_list(
        self: _RuntimeComponentManagerProtocol,
        plugin_id: str,
        capability: str,
        args: Dict[str, Any],
    ) -> Any:
        """列出当前插件可见的 API 列表。

        Args:
            plugin_id: 当前调用方插件 ID。
            capability: 能力名称。
            args: 能力参数。

        Returns:
            Any: API 元信息列表。
        """

        del capability
        target_plugin_id = str(args.get("plugin_id", "") or "").strip()
        api_name, version = self._normalize_api_reference(
            str(args.get("api_name", args.get("name", "")) or ""),
            str(args.get("version", "") or ""),
        )
        apis: List[Dict[str, Any]] = []
        for supervisor in self.supervisors:
            apis.extend(
                self._serialize_api_entry(entry)
                for entry in supervisor.api_registry.get_apis(
                    plugin_id=target_plugin_id or None,
                    name=api_name,
                    version=version,
                    enabled_only=True,
                )
                if self._is_api_visible_to_plugin(entry, plugin_id)
            )

        apis.sort(key=lambda item: (str(item["plugin_id"]), str(item["name"]), str(item["version"])))
        return {"success": True, "apis": apis}

    async def _cap_api_replace_dynamic(
        self: _RuntimeComponentManagerProtocol,
        plugin_id: str,
        capability: str,
        args: Dict[str, Any],
    ) -> Any:
        """替换插件自行维护的动态 API 列表。"""

        del capability
        raw_apis = args.get("apis", [])
        offline_reason = str(args.get("offline_reason", "") or "").strip() or "动态 API 已下线"
        if not isinstance(raw_apis, list):
            return {"success": False, "error": "参数 apis 必须为列表"}

        try:
            supervisor = self._get_supervisor_for_plugin(plugin_id)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        if supervisor is None:
            return {"success": False, "error": f"未找到插件: {plugin_id}"}

        normalized_components: List[Dict[str, Any]] = []
        seen_registry_keys: set[str] = set()
        for index, raw_api in enumerate(raw_apis):
            if not isinstance(raw_api, dict):
                return {"success": False, "error": f"apis[{index}] 必须为字典"}

            api_name = str(raw_api.get("name", "") or "").strip()
            component_type = str(raw_api.get("component_type", raw_api.get("type", "API")) or "").strip()
            if not api_name:
                return {"success": False, "error": f"apis[{index}] 缺少 name"}
            if not self._is_api_component_type(component_type):
                return {"success": False, "error": f"apis[{index}] 不是 API 组件"}

            metadata = raw_api.get("metadata", {}) if isinstance(raw_api.get("metadata"), dict) else {}
            normalized_metadata = dict(metadata)
            normalized_metadata["dynamic"] = True
            version = str(normalized_metadata.get("version", "1") or "1").strip() or "1"
            registry_key = supervisor.api_registry.build_registry_key(plugin_id, api_name, version)
            if registry_key in seen_registry_keys:
                return {"success": False, "error": f"动态 API 重复声明: {registry_key}"}
            seen_registry_keys.add(registry_key)

            existing_entry = supervisor.api_registry.get_api(
                plugin_id,
                api_name,
                version=version,
                enabled_only=False,
            )
            if existing_entry is not None and not existing_entry.dynamic:
                return {"success": False, "error": f"动态 API 不能覆盖静态 API: {registry_key}"}

            normalized_components.append(
                {
                    "name": api_name,
                    "component_type": "API",
                    "metadata": normalized_metadata,
                }
            )

        registered_count, offlined_count = supervisor.api_registry.replace_plugin_dynamic_apis(
            plugin_id,
            normalized_components,
            offline_reason=offline_reason,
        )
        return {
            "success": True,
            "count": registered_count,
            "offlined": offlined_count,
        }
