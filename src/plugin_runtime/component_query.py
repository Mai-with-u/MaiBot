"""插件运行时统一组件查询服务。

该模块统一从插件运行时的 Host ComponentRegistry 中聚合只读视图，
供 HFC、ToolExecutor 和运行时能力层查询与调用。
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional, Tuple, cast

from src.common.logger import get_logger
from src.core.local_operator import is_local_operator
from src.core.tooling import (
    ToolContentItem,
    ToolAvailabilityContext,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.core.types import ActionActivationType, ActionInfo, CommandInfo, ComponentInfo, ComponentType, ToolInfo
from src.llm_models.payload_content.tool_option import normalize_tool_option
from src.plugin_runtime.host.component_timeout import resolve_component_rpc_timeout_ms
from src.plugin_runtime.host.message_utils import PluginMessageUtils

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
        context: Optional[ToolAvailabilityContext] = None,
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

        session_id = context.session_id if context is not None else None
        is_group_chat = context.is_group_chat if context is not None else None
        group_id = context.group_id if context is not None else None
        platform = context.platform if context is not None else None
        collected_entries: list[tuple["PluginSupervisor", "ComponentEntry"]] = []
        for supervisor in self._iter_supervisors():
            for component in supervisor.component_registry.get_components_by_type(
                host_component_type,
                enabled_only=enabled_only,
                session_id=session_id,
                is_group_chat=is_group_chat,
                group_id=group_id,
                platform=platform,
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
            {str(param_name): str(param_description) for param_name, param_description in raw_action_parameters.items()}
            if isinstance(raw_action_parameters, dict)
            else {}
        )
        action_require = [
            str(item) for item in (metadata.get("action_require") or []) if item is not None and str(item).strip()
        ]
        associated_types = [
            str(item) for item in (metadata.get("associated_types") or []) if item is not None and str(item).strip()
        ]
        activation_keywords = [
            str(item) for item in (metadata.get("activation_keywords") or []) if item is not None and str(item).strip()
        ]

        return ActionInfo(
            name=entry.name,
            description=str(metadata.get("description", "") or ""),
            enabled=bool(entry.enabled),
            plugin_name=entry.plugin_id,
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
            description=str(metadata.get("description", "") or ""),
            enabled=bool(entry.enabled),
            plugin_name=entry.plugin_id,
        )

    @staticmethod
    def _build_tool_definition(entry: "ToolEntry") -> dict[str, Any]:
        """将运行时 Tool 条目转换为原始工具定义字典。

        Args:
            entry: 插件运行时中的 Tool 条目。

        Returns:
            dict[str, Any]: 可交给 `normalize_tool_option()` 的原始工具定义。
        """
        raw_definition: dict[str, Any] = {
            "name": entry.name,
            "description": entry.description,
        }
        if isinstance(entry.parameters_raw, dict) and entry.parameters_raw:
            raw_definition["parameters_schema"] = entry.parameters_raw
            return raw_definition
        if isinstance(entry.parameters, list) and entry.parameters:
            raw_definition["parameters"] = entry.parameters
            return raw_definition
        if isinstance(entry.parameters_raw, list) and entry.parameters_raw:
            raw_definition["parameters"] = entry.parameters_raw
            return raw_definition
        return raw_definition

    @staticmethod
    def _build_tool_parameters_schema(entry: "ToolEntry") -> dict[str, Any] | None:
        """将运行时 Tool 条目转换为对象级参数 Schema。

        Args:
            entry: 插件运行时中的 Tool 条目。

        Returns:
            dict[str, Any] | None: 规范化后的对象级参数 Schema。
        """
        normalized_option = normalize_tool_option(ComponentQueryService._build_tool_definition(entry))
        return normalized_option.parameters_schema

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
            description=entry.description,
            enabled=bool(entry.enabled),
            plugin_name=entry.plugin_id,
            parameters_schema=ComponentQueryService._build_tool_parameters_schema(entry),
        )

    @staticmethod
    def _get_tool_visibility(entry: "ToolEntry") -> str:
        """读取插件工具面向 LLM 的可见性声明。"""

        raw_visibility = str(entry.metadata.get("visibility") or "").strip().lower()
        if raw_visibility in {"visible", "deferred", "hidden"}:
            return raw_visibility
        if bool(entry.metadata.get("core_tool", False)):
            return "visible"
        return "deferred"

    @staticmethod
    def _build_tool_spec(entry: "ToolEntry") -> ToolSpec:
        """将运行时 Tool 条目转换为统一工具声明。

        Args:
            entry: 插件运行时中的 Tool 条目。

        Returns:
            ToolSpec: 统一工具声明。
        """

        parameters_schema = ComponentQueryService._build_tool_parameters_schema(entry)
        visibility = ComponentQueryService._get_tool_visibility(entry)
        return ToolSpec(
            name=entry.name,
            description=entry.description or f"工具 {entry.name}",
            parameters_schema=parameters_schema,
            provider_name=entry.plugin_id,
            provider_type="plugin",
            enabled=visibility != "hidden",
            metadata={
                "plugin_id": entry.plugin_id,
                "invoke_method": entry.invoke_method,
                "legacy_component_type": entry.legacy_component_type,
                "visibility": visibility,
            },
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
        *,
        context: Optional[ToolAvailabilityContext] = None,
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
            for supervisor, entry in self._iter_component_entries(component_type, context=context)
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
    def _build_action_executor(
        supervisor: "PluginSupervisor",
        plugin_id: str,
        component_name: str,
        timeout_ms: int = 0,
    ) -> ActionExecutor:
        """构造动作执行 RPC 闭包。

        Args:
            supervisor: 负责该组件的监督器。
            plugin_id: 插件 ID。
            component_name: 组件名称。

        Returns:
            ActionExecutor: 兼容旧 Planner 的异步执行器。
        """

        rpc_timeout_ms = resolve_component_rpc_timeout_ms(timeout_ms)

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
                    timeout_ms=rpc_timeout_ms,
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
        timeout_ms: int = 0,
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

        rpc_timeout_ms = resolve_component_rpc_timeout_ms(timeout_ms)

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
            message_info = getattr(message, "message_info", None)
            group_info = getattr(message_info, "group_info", None)
            user_info = getattr(message_info, "user_info", None)
            message_is_local_operator = False
            if message is not None:
                message_is_local_operator = is_local_operator(
                    message.platform,
                    message.message_info.additional_config,
                )
            invoke_args: Dict[str, Any] = {
                "text": str(getattr(message, "processed_plain_text", "") or ""),
                "stream_id": str(getattr(message, "session_id", "") or ""),
                "group_id": str(getattr(group_info, "group_id", "") or ""),
                "platform": str(getattr(message, "platform", "") or ""),
                "user_id": str(getattr(user_info, "user_id", "") or ""),
                "is_local_operator": message_is_local_operator,
                "matched_groups": matched_groups if isinstance(matched_groups, dict) else {},
            }
            if message is not None:
                invoke_args["message"] = dict(
                    PluginMessageUtils._session_message_to_dict(message, include_binary_data=False)
                )
            if isinstance(plugin_config, dict):
                invoke_args["plugin_config"] = plugin_config

            try:
                response = await supervisor.invoke_plugin(
                    method="plugin.invoke_command",
                    plugin_id=plugin_id,
                    component_name=component_name,
                    args=invoke_args,
                    timeout_ms=rpc_timeout_ms,
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
    def _build_tool_executor(
        supervisor: "PluginSupervisor",
        plugin_id: str,
        component_name: str,
        invoke_method: str = "plugin.invoke_tool",
        timeout_ms: int = 0,
    ) -> ToolExecutor:
        """构造工具执行 RPC 闭包。

        Args:
            supervisor: 负责该组件的监督器。
            plugin_id: 插件 ID。
            component_name: 组件名称。

        Returns:
            ToolExecutor: 兼容旧 ToolExecutor 的异步执行器。
        """

        rpc_timeout_ms = resolve_component_rpc_timeout_ms(timeout_ms)

        async def _executor(function_args: Dict[str, Any]) -> Any:
            """将核心工具调用桥接到插件运行时。

            Args:
                function_args: 工具调用参数。

            Returns:
                Any: 插件工具返回结果；若结果不是字典，则会包装为 ``{"content": ...}``。
            """

            try:
                response = await supervisor.invoke_plugin(
                    method=invoke_method,
                    plugin_id=plugin_id,
                    component_name=component_name,
                    args=function_args,
                    timeout_ms=rpc_timeout_ms,
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
        return self._build_action_executor(supervisor, entry.plugin_id, entry.name, entry.timeout_ms)

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
                entry.timeout_ms,
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
        tool_entry = cast("ToolEntry", entry)
        return self._build_tool_executor(
            supervisor,
            tool_entry.plugin_id,
            tool_entry.name,
            tool_entry.invoke_method,
            tool_entry.timeout_ms,
        )

    def get_llm_available_tool_specs(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> Dict[str, ToolSpec]:
        """获取当前可供 LLM 使用的统一工具声明集合。

        Returns:
            Dict[str, ToolSpec]: 工具名到工具声明的映射。
        """

        collected_specs: Dict[str, ToolSpec] = {}
        for _supervisor, entry in self._iter_component_entries(ComponentType.TOOL, context=context):
            if entry.name in collected_specs:
                self._log_duplicate_component(ComponentType.TOOL, entry.name)
                continue
            collected_specs[entry.name] = self._build_tool_spec(entry)  # type: ignore[arg-type]
        return collected_specs

    @staticmethod
    def _build_tool_context_payload(context: Optional[ToolExecutionContext]) -> Dict[str, Any]:
        """提取插件工具可复用的会话上下文字段。"""

        if context is None:
            return {}

        payload: Dict[str, Any] = {}
        stream_id = str(context.stream_id or context.session_id or "").strip()
        if stream_id:
            payload["stream_id"] = stream_id
            payload["chat_id"] = stream_id

        group_id = str(context.group_id or "").strip()
        user_id = str(context.user_id or "").strip()
        platform = str(context.platform or "").strip()
        if group_id:
            payload["group_id"] = group_id
        if user_id:
            payload["user_id"] = user_id
        if platform:
            payload["platform"] = platform

        anchor_message = context.metadata.get("anchor_message")
        message_info = getattr(anchor_message, "message_info", None)
        group_info = getattr(message_info, "group_info", None)
        user_info = getattr(message_info, "user_info", None)

        anchor_group_id = str(getattr(group_info, "group_id", "") or "").strip()
        anchor_user_id = str(getattr(user_info, "user_id", "") or "").strip()
        if anchor_group_id:
            payload["group_id"] = anchor_group_id
        if anchor_user_id:
            payload["user_id"] = anchor_user_id
        return payload

    @staticmethod
    def _build_tool_availability_context(
        context: Optional[ToolExecutionContext],
    ) -> Optional[ToolAvailabilityContext]:
        """从执行上下文还原工具可用性判断所需字段。"""

        if context is None:
            return None
        return ToolAvailabilityContext(
            session_id=context.session_id,
            stream_id=context.stream_id,
            is_group_chat=context.is_group_chat,
            group_id=context.group_id,
            user_id=context.user_id,
            platform=context.platform,
        )

    @staticmethod
    def _build_tool_invocation_payload(
        entry: "ToolEntry",
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext],
    ) -> Dict[str, Any]:
        """构造插件工具执行时发送给 Runner 的参数。

        Args:
            entry: 目标工具条目。
            invocation: 统一工具调用请求。
            context: 统一工具执行上下文。

        Returns:
            Dict[str, Any]: 发往 Runner 的参数字典。
        """

        payload = dict(invocation.arguments)
        context_payload = ComponentQueryService._build_tool_context_payload(context)
        if entry.invoke_method == "plugin.invoke_action":
            stream_id = str(
                context_payload.get("stream_id")
                or (context.stream_id if context is not None else invocation.stream_id)
                or invocation.stream_id
            ).strip()
            reasoning = context.reasoning if context is not None else invocation.reasoning
            payload = {
                **payload,
                **{key: value for key, value in context_payload.items() if key not in payload or not payload.get(key)},
                "stream_id": stream_id,
                "chat_id": stream_id,
                "reasoning": reasoning,
                "action_data": dict(invocation.arguments),
            }
            return payload

        for key, value in context_payload.items():
            if key not in payload or not payload.get(key):
                payload[key] = value
        return payload

    @staticmethod
    def _parse_tool_invoke_result(
        entry: "ToolEntry",
        result: Any,
    ) -> ToolExecutionResult:
        """将插件组件返回值转换为统一工具执行结果。

        Args:
            entry: 目标工具条目。
            result: 插件组件原始返回值。

        Returns:
            ToolExecutionResult: 统一执行结果。
        """

        if isinstance(result, dict):
            success = bool(result.get("success", True))
            content = str(result.get("content", result.get("message", "")) or "").strip()
            content_items = ComponentQueryService._parse_tool_content_items(result.get("content_items"))
            error_message = ""
            if not success:
                error_message = str(result.get("error", result.get("message", "插件工具执行失败")) or "").strip()
            return ToolExecutionResult(
                tool_name=entry.name,
                success=success,
                content=content,
                error_message=error_message,
                structured_content=result,
                content_items=content_items,
                metadata={"plugin_id": entry.plugin_id},
            )

        if isinstance(result, (list, tuple)) and result:
            if isinstance(result[0], bool):
                success = bool(result[0])
                message = "" if len(result) < 2 or result[1] is None else str(result[1]).strip()
                return ToolExecutionResult(
                    tool_name=entry.name,
                    success=success,
                    content=message if success else "",
                    error_message="" if success else message,
                    structured_content=list(result),
                    metadata={"plugin_id": entry.plugin_id},
                )

        normalized_content = "" if result is None else str(result).strip()
        return ToolExecutionResult(
            tool_name=entry.name,
            success=True,
            content=normalized_content,
            structured_content=result,
            metadata={"plugin_id": entry.plugin_id},
        )

    @staticmethod
    def _parse_tool_content_items(raw_items: Any) -> list[ToolContentItem]:
        """从插件工具返回值中解析结构化内容项。"""

        if not isinstance(raw_items, list):
            return []

        content_items: list[ToolContentItem] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue

            raw_content_type = str(raw_item.get("content_type") or raw_item.get("type") or "unknown").strip()
            if raw_content_type not in {"text", "image", "audio", "resource_link", "resource", "binary", "unknown"}:
                raw_content_type = "unknown"

            metadata = raw_item.get("metadata")
            content_items.append(
                ToolContentItem(
                    content_type=cast(Any, raw_content_type),
                    text=str(raw_item.get("text") or ""),
                    data=str(raw_item.get("data") or raw_item.get("base64") or ""),
                    mime_type=str(raw_item.get("mime_type") or ""),
                    uri=str(raw_item.get("uri") or ""),
                    name=str(raw_item.get("name") or ""),
                    description=str(raw_item.get("description") or ""),
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
            )
        return content_items

    async def invoke_tool_as_tool(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        """按统一工具语义执行插件工具。

        Args:
            invocation: 统一工具调用请求。
            context: 执行上下文。

        Returns:
            ToolExecutionResult: 统一工具执行结果。
        """

        availability_context = self._build_tool_availability_context(context)
        matched_entry = self._get_unique_component_entry(
            ComponentType.TOOL,
            invocation.tool_name,
            context=availability_context,
        )
        if matched_entry is None:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"未找到插件工具：{invocation.tool_name}",
            )

        supervisor, entry = matched_entry
        tool_entry = cast("ToolEntry", entry)
        invoke_payload = self._build_tool_invocation_payload(tool_entry, invocation, context)

        try:
            response = await supervisor.invoke_plugin(
                method=tool_entry.invoke_method,
                plugin_id=tool_entry.plugin_id,
                component_name=tool_entry.name,
                args=invoke_payload,
                timeout_ms=resolve_component_rpc_timeout_ms(tool_entry.timeout_ms),
            )
        except Exception as exc:
            logger.error(f"运行时工具 {tool_entry.plugin_id}.{tool_entry.name} 执行失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=tool_entry.name,
                success=False,
                error_message=str(exc),
                metadata={"plugin_id": tool_entry.plugin_id},
            )

        payload = response.payload if isinstance(response.payload, dict) else {}
        transport_success = bool(payload.get("success", False))
        result = payload.get("result")
        if not transport_success:
            return ToolExecutionResult(
                tool_name=tool_entry.name,
                success=False,
                error_message="" if result is None else str(result),
                structured_content=result,
                metadata={"plugin_id": tool_entry.plugin_id},
            )
        return self._parse_tool_invoke_result(tool_entry, result)

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

    def get_plugin_default_config(self, plugin_name: str) -> Optional[dict]:
        """获取指定插件注册时上报的默认配置。

        Args:
            plugin_name: 插件名称。

        Returns:
            Optional[dict]: 默认配置字典；未找到时返回 ``None``。
        """

        runtime_manager = self._get_runtime_manager()
        try:
            supervisor = runtime_manager._get_supervisor_for_plugin(plugin_name)
        except RuntimeError as exc:
            logger.error(f"读取插件默认配置失败: {exc}")
            return None

        if supervisor is None:
            return None

        registration = supervisor._registered_plugins.get(plugin_name)
        if registration is None:
            return None
        return dict(registration.default_config)

    def get_plugin_config_schema(self, plugin_name: str) -> Optional[dict]:
        """获取指定插件注册时上报的配置 Schema。

        Args:
            plugin_name: 插件名称。

        Returns:
            Optional[dict]: 配置 Schema；未找到时返回 ``None``。
        """

        runtime_manager = self._get_runtime_manager()
        try:
            supervisor = runtime_manager._get_supervisor_for_plugin(plugin_name)
        except RuntimeError as exc:
            logger.error(f"读取插件配置 Schema 失败: {exc}")
            return None

        if supervisor is None:
            return None

        registration = supervisor._registered_plugins.get(plugin_name)
        if registration is None:
            return None
        return dict(registration.config_schema)

    def list_hook_specs(self) -> list[dict[str, Any]]:
        """返回当前运行时公开的 Hook 规格清单。

        Returns:
            list[dict[str, Any]]: 可直接序列化给 WebUI 的 Hook 规格列表。
        """

        runtime_manager = self._get_runtime_manager()
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters_schema": deepcopy(spec.parameters_schema),
                "default_timeout_ms": spec.default_timeout_ms,
                "allow_blocking": spec.allow_blocking,
                "allow_observe": spec.allow_observe,
                "allow_abort": spec.allow_abort,
                "allow_kwargs_mutation": spec.allow_kwargs_mutation,
            }
            for spec in runtime_manager.list_hook_specs()
        ]


component_query_service = ComponentQueryService()
