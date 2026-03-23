"""插件运行时统一组件查询服务。

该模块统一从插件运行时的 Host ComponentRegistry 中聚合只读视图，
供 HFC/PFC、Planner、ToolExecutor 和运行时能力层查询与调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional, Tuple

from src.common.logger import get_logger
from src.core.types import ActionActivationType, ActionInfo, CommandInfo, ComponentInfo, ComponentType, ToolInfo
from src.llm_models.payload_content.tool_option import ToolParamType

if TYPE_CHECKING:
    from src.plugin_runtime.host.component_registry import ActionEntry, CommandEntry, ComponentEntry, ToolEntry
    from src.plugin_runtime.host.supervisor import PluginSupervisor
    from src.plugin_runtime.integration import PluginRuntimeManager

logger = get_logger("plugin_runtime.component_query")

ActionExecutor = Callable[..., Awaitable[Any]]
CommandExecutor = Callable[..., Awaitable[Tuple[bool, Optional[str], bool]]]
ToolExecutor = Callable[..., Awaitable[Any]]

_HOST_COMPONENT_TYPE_MAP: Dict[ComponentType, str] = {
    ComponentType.ACTION: "ACTION",
    ComponentType.COMMAND: "COMMAND",
    ComponentType.TOOL: "TOOL",
}
_TOOL_PARAM_TYPE_MAP: Dict[str, ToolParamType] = {
    "string": ToolParamType.STRING,
    "integer": ToolParamType.INTEGER,
    "float": ToolParamType.FLOAT,
    "boolean": ToolParamType.BOOLEAN,
    "bool": ToolParamType.BOOLEAN,
}


class ComponentQueryService:
    """插件运行时统一组件查询服务。

    该对象不维护独立状态，只读取插件系统中的注册结果。
    所有注册、删除、配置写入等写操作都被显式禁用。
    """

    @staticmethod
    def _get_runtime_manager() -> "PluginRuntimeManager":
        """获取插件运行时管理器单例。

        Returns:
            PluginRuntimeManager: 当前全局插件运行时管理器。
        """

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager()

    def _iter_supervisors(self) -> list["PluginSupervisor"]:
        """获取当前所有活跃的插件运行时监督器。

        Returns:
            list[PluginSupervisor]: 当前运行中的监督器列表。
        """

        runtime_manager = self._get_runtime_manager()
        return list(runtime_manager.supervisors)

    def _iter_component_entries(
        self,
        component_type: ComponentType,
        *,
        enabled_only: bool = True,
    ) -> list[tuple["PluginSupervisor", "ComponentEntry"]]:
        """遍历指定类型的全部组件条目。

        Args:
            component_type: 目标组件类型。
            enabled_only: 是否仅返回启用状态的组件。

        Returns:
            list[tuple[PluginSupervisor, ComponentEntry]]: ``(监督器, 组件条目)`` 列表。
        """

        host_component_type = _HOST_COMPONENT_TYPE_MAP.get(component_type)
        if host_component_type is None:
            return []

        collected_entries: list[tuple["PluginSupervisor", "ComponentEntry"]] = []
        for supervisor in self._iter_supervisors():
            for component in supervisor.component_registry.get_components_by_type(
                host_component_type,
                enabled_only=enabled_only,
            ):
                collected_entries.append((supervisor, component))
        return collected_entries

    @staticmethod
    def _coerce_action_activation_type(raw_value: Any) -> ActionActivationType:
        """规范化动作激活类型。

        Args:
            raw_value: 原始激活类型值。

        Returns:
            ActionActivationType: 规范化后的激活类型枚举。
        """

        normalized_value = str(raw_value or "").strip().lower()
        if normalized_value == ActionActivationType.NEVER.value:
            return ActionActivationType.NEVER
        if normalized_value == ActionActivationType.RANDOM.value:
            return ActionActivationType.RANDOM
        if normalized_value == ActionActivationType.KEYWORD.value:
            return ActionActivationType.KEYWORD
        return ActionActivationType.ALWAYS

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        """将任意值安全转换为浮点数。

        Args:
            value: 待转换的输入值。
            default: 转换失败时返回的默认值。

        Returns:
            float: 转换后的浮点结果。
        """

        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _build_action_info(entry: "ActionEntry") -> ActionInfo:
        """将运行时 Action 条目转换为核心动作信息。

        Args:
            entry: 插件运行时中的 Action 条目。

        Returns:
            ActionInfo: 供核心 Planner 使用的动作信息。
        """

        metadata = dict(entry.metadata)
        raw_action_parameters = metadata.get("action_parameters")
        action_parameters = (
            {
                str(param_name): str(param_description)
                for param_name, param_description in raw_action_parameters.items()
            }
            if isinstance(raw_action_parameters, dict)
            else {}
        )
        action_require = [
            str(item)
            for item in (metadata.get("action_require") or [])
            if item is not None and str(item).strip()
        ]
        associated_types = [
            str(item)
            for item in (metadata.get("associated_types") or [])
            if item is not None and str(item).strip()
        ]
        activation_keywords = [
            str(item)
            for item in (metadata.get("activation_keywords") or [])
            if item is not None and str(item).strip()
        ]

        return ActionInfo(
            name=entry.name,
            component_type=ComponentType.ACTION,
            description=str(metadata.get("description", "") or ""),
            enabled=bool(entry.enabled),
            plugin_name=entry.plugin_id,
            metadata=metadata,
            action_parameters=action_parameters,
            action_require=action_require,
            associated_types=associated_types,
            activation_type=ComponentQueryService._coerce_action_activation_type(metadata.get("activation_type")),
            random_activation_probability=ComponentQueryService._coerce_float(
                metadata.get("activation_probability"),
                0.0,
            ),
            activation_keywords=activation_keywords,
            parallel_action=bool(metadata.get("parallel_action", False)),
        )

    @staticmethod
    def _build_command_info(entry: "CommandEntry") -> CommandInfo:
        """将运行时 Command 条目转换为核心命令信息。

        Args:
            entry: 插件运行时中的 Command 条目。

        Returns:
            CommandInfo: 供核心命令链使用的命令信息。
        """

        metadata = dict(entry.metadata)
        return CommandInfo(
            name=entry.name,
            component_type=ComponentType.COMMAND,
            description=str(metadata.get("description", "") or ""),
            enabled=bool(entry.enabled),
            plugin_name=entry.plugin_id,
            metadata=metadata,
            command_pattern=str(metadata.get("command_pattern", "") or ""),
        )

    @staticmethod
    def _coerce_tool_param_type(raw_value: Any) -> ToolParamType:
        """规范化工具参数类型。

        Args:
            raw_value: 原始工具参数类型值。

        Returns:
            ToolParamType: 规范化后的工具参数类型。
        """

        normalized_value = str(raw_value or "").strip().lower()
        return _TOOL_PARAM_TYPE_MAP.get(normalized_value, ToolParamType.STRING)

    @staticmethod
    def _build_tool_parameters(entry: "ToolEntry") -> list[tuple[str, ToolParamType, str, bool, list[str] | None]]:
        """将运行时工具参数元数据转换为核心 ToolInfo 参数列表。

        Args:
            entry: 插件运行时中的 Tool 条目。

        Returns:
            list[tuple[str, ToolParamType, str, bool, list[str] | None]]: 转换后的参数列表。
        """

        structured_parameters = entry.parameters if isinstance(entry.parameters, list) else []
        if not structured_parameters and isinstance(entry.parameters_raw, dict):
            structured_parameters = [
                {"name": key, **value}
                for key, value in entry.parameters_raw.items()
                if isinstance(value, dict)
            ]

        normalized_parameters: list[tuple[str, ToolParamType, str, bool, list[str] | None]] = []
        for parameter in structured_parameters:
            if not isinstance(parameter, dict):
                continue

            parameter_name = str(parameter.get("name", "") or "").strip()
            if not parameter_name:
                continue

            enum_values = parameter.get("enum")
            normalized_enum_values = (
                [str(item) for item in enum_values if item is not None]
                if isinstance(enum_values, list)
                else None
            )
            normalized_parameters.append(
                (
                    parameter_name,
                    ComponentQueryService._coerce_tool_param_type(parameter.get("param_type") or parameter.get("type")),
                    str(parameter.get("description", "") or ""),
                    bool(parameter.get("required", True)),
                    normalized_enum_values,
                )
            )
        return normalized_parameters

    @staticmethod
    def _build_tool_info(entry: "ToolEntry") -> ToolInfo:
        """将运行时 Tool 条目转换为核心工具信息。

        Args:
            entry: 插件运行时中的 Tool 条目。

        Returns:
            ToolInfo: 供 ToolExecutor 与能力层使用的工具信息。
        """

        return ToolInfo(
            name=entry.name,
            component_type=ComponentType.TOOL,
            description=entry.description,
            enabled=bool(entry.enabled),
            plugin_name=entry.plugin_id,
            metadata=dict(entry.metadata),
            tool_parameters=ComponentQueryService._build_tool_parameters(entry),
            tool_description=entry.description,
        )

    @staticmethod
    def _log_duplicate_component(component_type: ComponentType, component_name: str) -> None:
        """记录重复组件名称冲突。

        Args:
            component_type: 组件类型。
            component_name: 发生冲突的组件名称。
        """

        logger.warning(f"检测到重复{component_type.value}名称 {component_name}，将只保留首个匹配项")

    def _get_unique_component_entry(
        self,
        component_type: ComponentType,
        name: str,
    ) -> Optional[tuple["PluginSupervisor", "ComponentEntry"]]:
        """按组件短名解析唯一条目。

        Args:
            component_type: 目标组件类型。
            name: 组件短名。

        Returns:
            Optional[tuple[PluginSupervisor, ComponentEntry]]: 唯一命中的组件条目。
        """

        matched_entries = [
            (supervisor, entry)
            for supervisor, entry in self._iter_component_entries(component_type)
            if entry.name == name
        ]
        if not matched_entries:
            return None
        if len(matched_entries) > 1:
            self._log_duplicate_component(component_type, name)
        return matched_entries[0]

    def _collect_unique_component_infos(
        self,
        component_type: ComponentType,
    ) -> Dict[str, ComponentInfo]:
        """收集某类组件的唯一信息视图。

        Args:
            component_type: 目标组件类型。

        Returns:
            Dict[str, ComponentInfo]: 组件名到核心组件信息的映射。
        """

        collected_components: Dict[str, ComponentInfo] = {}
        for _supervisor, entry in self._iter_component_entries(component_type):
            if entry.name in collected_components:
                self._log_duplicate_component(component_type, entry.name)
                continue

            if component_type == ComponentType.ACTION:
                collected_components[entry.name] = self._build_action_info(entry)  # type: ignore[arg-type]
            elif component_type == ComponentType.COMMAND:
                collected_components[entry.name] = self._build_command_info(entry)  # type: ignore[arg-type]
            elif component_type == ComponentType.TOOL:
                collected_components[entry.name] = self._build_tool_info(entry)  # type: ignore[arg-type]
        return collected_components

    @staticmethod
    def _extract_stream_id_from_action_kwargs(kwargs: Dict[str, Any]) -> str:
        """从旧 ActionManager 参数中提取聊天流 ID。

        Args:
            kwargs: 旧动作执行器收到的关键字参数。

        Returns:
            str: 提取出的 ``stream_id``。
        """

        chat_stream = kwargs.get("chat_stream")
        if chat_stream is not None:
            try:
                return str(chat_stream.session_id)
            except AttributeError:
                pass

        return str(kwargs.get("stream_id", "") or "")

    @staticmethod
    def _build_action_executor(supervisor: "PluginSupervisor", plugin_id: str, component_name: str) -> ActionExecutor:
        """构造动作执行 RPC 闭包。

        Args:
            supervisor: 负责该组件的监督器。
            plugin_id: 插件 ID。
            component_name: 组件名称。

        Returns:
            ActionExecutor: 兼容旧 Planner 的异步执行器。
        """

        async def _executor(**kwargs: Any) -> tuple[bool, str]:
            """将核心动作调用桥接到插件运行时。

            Args:
                **kwargs: 旧 ActionManager 传入的上下文参数。

            Returns:
                tuple[bool, str]: ``(是否成功, 结果说明)``。
            """

            invoke_args: Dict[str, Any] = {}
            action_data = kwargs.get("action_data")
            if isinstance(action_data, dict):
                invoke_args.update(action_data)

            stream_id = ComponentQueryService._extract_stream_id_from_action_kwargs(kwargs)
            invoke_args["action_data"] = action_data if isinstance(action_data, dict) else {}
            invoke_args["stream_id"] = stream_id
            invoke_args["chat_id"] = stream_id
            invoke_args["reasoning"] = str(kwargs.get("action_reasoning", "") or "")

            if (thinking_id := kwargs.get("thinking_id")) is not None:
                invoke_args["thinking_id"] = str(thinking_id)
            if isinstance(kwargs.get("cycle_timers"), dict):
                invoke_args["cycle_timers"] = kwargs["cycle_timers"]
            if isinstance(kwargs.get("plugin_config"), dict):
                invoke_args["plugin_config"] = kwargs["plugin_config"]
            if isinstance(kwargs.get("log_prefix"), str):
                invoke_args["log_prefix"] = kwargs["log_prefix"]
            if isinstance(kwargs.get("shutting_down"), bool):
                invoke_args["shutting_down"] = kwargs["shutting_down"]

            try:
                response = await supervisor.invoke_plugin(
                    method="plugin.invoke_action",
                    plugin_id=plugin_id,
                    component_name=component_name,
                    args=invoke_args,
                    timeout_ms=30000,
                )
            except Exception as exc:
                logger.error(f"运行时 Action {plugin_id}.{component_name} 执行失败: {exc}", exc_info=True)
                return False, str(exc)

            payload = response.payload if isinstance(response.payload, dict) else {}
            success = bool(payload.get("success", False))
            result = payload.get("result")
            if isinstance(result, (list, tuple)):
                if len(result) >= 2:
                    return bool(result[0]), "" if result[1] is None else str(result[1])
                if len(result) == 1:
                    return bool(result[0]), ""
            if success:
                return True, "" if result is None else str(result)
            return False, "" if result is None else str(result)

        return _executor

    @staticmethod
    def _build_command_executor(
        supervisor: "PluginSupervisor",
        plugin_id: str,
        component_name: str,
        metadata: Dict[str, Any],
    ) -> CommandExecutor:
        """构造命令执行 RPC 闭包。

        Args:
            supervisor: 负责该组件的监督器。
            plugin_id: 插件 ID。
            component_name: 组件名称。
            metadata: 命令组件元数据。

        Returns:
            CommandExecutor: 兼容旧消息命令链的执行器。
        """

        async def _executor(**kwargs: Any) -> tuple[bool, Optional[str], bool]:
            """将核心命令调用桥接到插件运行时。

            Args:
                **kwargs: 命令执行上下文参数。

            Returns:
                tuple[bool, Optional[str], bool]: ``(是否成功, 返回文本, 是否拦截后续消息)``。
            """

            message = kwargs.get("message")
            matched_groups = kwargs.get("matched_groups")
            plugin_config = kwargs.get("plugin_config")
            invoke_args: Dict[str, Any] = {
                "text": str(getattr(message, "processed_plain_text", "") or ""),
                "stream_id": str(getattr(message, "session_id", "") or ""),
                "matched_groups": matched_groups if isinstance(matched_groups, dict) else {},
            }
            if isinstance(plugin_config, dict):
                invoke_args["plugin_config"] = plugin_config

            try:
                response = await supervisor.invoke_plugin(
                    method="plugin.invoke_command",
                    plugin_id=plugin_id,
                    component_name=component_name,
                    args=invoke_args,
                    timeout_ms=30000,
                )
            except Exception as exc:
                logger.error(f"运行时 Command {plugin_id}.{component_name} 执行失败: {exc}", exc_info=True)
                return False, str(exc), True

            payload = response.payload if isinstance(response.payload, dict) else {}
            success = bool(payload.get("success", False))
            result = payload.get("result")
            intercept = bool(metadata.get("intercept_message_level", 0))
            response_text: Optional[str]

            if isinstance(result, (list, tuple)) and len(result) >= 3:
                response_text = None if result[1] is None else str(result[1])
                intercept = bool(result[2])
            else:
                response_text = None if result is None else str(result)

            return success, response_text, intercept

        return _executor

    @staticmethod
    def _build_tool_executor(supervisor: "PluginSupervisor", plugin_id: str, component_name: str) -> ToolExecutor:
        """构造工具执行 RPC 闭包。

        Args:
            supervisor: 负责该组件的监督器。
            plugin_id: 插件 ID。
            component_name: 组件名称。

        Returns:
            ToolExecutor: 兼容旧 ToolExecutor 的异步执行器。
        """

        async def _executor(function_args: Dict[str, Any]) -> Any:
            """将核心工具调用桥接到插件运行时。

            Args:
                function_args: 工具调用参数。

            Returns:
                Any: 插件工具返回结果；若结果不是字典，则会包装为 ``{"content": ...}``。
            """

            try:
                response = await supervisor.invoke_plugin(
                    method="plugin.invoke_tool",
                    plugin_id=plugin_id,
                    component_name=component_name,
                    args=function_args,
                    timeout_ms=30000,
                )
            except Exception as exc:
                logger.error(f"运行时 Tool {plugin_id}.{component_name} 执行失败: {exc}", exc_info=True)
                return {"content": f"工具 {component_name} 执行失败: {exc}"}

            payload = response.payload if isinstance(response.payload, dict) else {}
            result = payload.get("result")
            if isinstance(result, dict):
                return result
            return {"content": "" if result is None else str(result)}

        return _executor

    def get_action_info(self, name: str) -> Optional[ActionInfo]:
        """获取指定动作的信息。

        Args:
            name: 动作名称。

        Returns:
            Optional[ActionInfo]: 匹配到的动作信息。
        """

        matched_entry = self._get_unique_component_entry(ComponentType.ACTION, name)
        if matched_entry is None:
            return None
        _supervisor, entry = matched_entry
        return self._build_action_info(entry)  # type: ignore[arg-type]

    def get_action_executor(self, name: str) -> Optional[ActionExecutor]:
        """获取指定动作的执行器。

        Args:
            name: 动作名称。

        Returns:
            Optional[ActionExecutor]: 运行时 RPC 执行闭包。
        """

        matched_entry = self._get_unique_component_entry(ComponentType.ACTION, name)
        if matched_entry is None:
            return None
        supervisor, entry = matched_entry
        return self._build_action_executor(supervisor, entry.plugin_id, entry.name)

    def get_default_actions(self) -> Dict[str, ActionInfo]:
        """获取当前默认启用的动作集合。

        Returns:
            Dict[str, ActionInfo]: 动作名到动作信息的映射。
        """

        action_infos = self._collect_unique_component_infos(ComponentType.ACTION)
        return {name: info for name, info in action_infos.items() if isinstance(info, ActionInfo) and info.enabled}

    def find_command_by_text(self, text: str) -> Optional[Tuple[CommandExecutor, dict, CommandInfo]]:
        """根据文本查找匹配的命令。

        Args:
            text: 待匹配的文本内容。

        Returns:
            Optional[Tuple[CommandExecutor, dict, CommandInfo]]: 匹配结果。
        """

        for supervisor in self._iter_supervisors():
            match_result = supervisor.component_registry.find_command_by_text(text)
            if match_result is None:
                continue

            entry, matched_groups = match_result
            command_info = self._build_command_info(entry)  # type: ignore[arg-type]
            command_executor = self._build_command_executor(
                supervisor,
                entry.plugin_id,
                entry.name,
                dict(entry.metadata),
            )
            return command_executor, matched_groups, command_info
        return None

    def get_tool_info(self, name: str) -> Optional[ToolInfo]:
        """获取指定工具的信息。

        Args:
            name: 工具名称。

        Returns:
            Optional[ToolInfo]: 匹配到的工具信息。
        """

        matched_entry = self._get_unique_component_entry(ComponentType.TOOL, name)
        if matched_entry is None:
            return None
        _supervisor, entry = matched_entry
        return self._build_tool_info(entry)  # type: ignore[arg-type]

    def get_tool_executor(self, name: str) -> Optional[ToolExecutor]:
        """获取指定工具的执行器。

        Args:
            name: 工具名称。

        Returns:
            Optional[ToolExecutor]: 运行时 RPC 执行闭包。
        """

        matched_entry = self._get_unique_component_entry(ComponentType.TOOL, name)
        if matched_entry is None:
            return None
        supervisor, entry = matched_entry
        return self._build_tool_executor(supervisor, entry.plugin_id, entry.name)

    def get_llm_available_tools(self) -> Dict[str, ToolInfo]:
        """获取当前可供 LLM 选择的工具集合。

        Returns:
            Dict[str, ToolInfo]: 工具名到工具信息的映射。
        """

        tool_infos = self._collect_unique_component_infos(ComponentType.TOOL)
        return {name: info for name, info in tool_infos.items() if isinstance(info, ToolInfo) and info.enabled}

    def get_components_by_type(self, component_type: ComponentType) -> Dict[str, ComponentInfo]:
        """获取某类组件的全部信息。

        Args:
            component_type: 组件类型。

        Returns:
            Dict[str, ComponentInfo]: 组件名到组件信息的映射。
        """

        return self._collect_unique_component_infos(component_type)

    def get_plugin_config(self, plugin_name: str) -> Optional[dict]:
        """读取指定插件的配置文件内容。

        Args:
            plugin_name: 插件名称。

        Returns:
            Optional[dict]: 读取成功时返回配置字典；未找到时返回 ``None``。
        """

        runtime_manager = self._get_runtime_manager()
        try:
            supervisor = runtime_manager._get_supervisor_for_plugin(plugin_name)
        except RuntimeError as exc:
            logger.error(f"读取插件配置失败: {exc}")
            return None

        if supervisor is None:
            return None

        try:
            return runtime_manager._load_plugin_config_for_supervisor(supervisor, plugin_name)
        except Exception as exc:
            logger.error(f"读取插件 {plugin_name} 配置失败: {exc}", exc_info=True)
            return None


component_query_service = ComponentQueryService()
