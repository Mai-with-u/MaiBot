"""Host 侧组件注册表。

对齐旧系统 component_registry.py 的核心能力：
- 按类型注册组件（action / command / tool / event_handler / hook_handler / message_gateway）
- 命名空间 (plugin_id.component_name)
- 命令正则匹配
- 组件启用/禁用
- 多维度查询（按名称、类型、插件）
- 注册统计
"""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, TypedDict

import contextlib
import re

from src.common.logger import get_logger
from .component_timeout import normalize_component_timeout_ms
from .hook_spec_registry import HookSpecRegistry

logger = get_logger("plugin_runtime.host.component_registry")


class ComponentRegistrationError(ValueError):
    """组件注册失败异常。"""

    def __init__(
        self,
        message: str,
        *,
        component_name: str = "",
        component_type: str = "",
        plugin_id: str = "",
    ) -> None:
        """初始化组件注册失败异常。

        Args:
            message: 原始错误信息。
            component_name: 组件名称。
            component_type: 组件类型。
            plugin_id: 插件 ID。
        """

        self.component_name = str(component_name or "").strip()
        self.component_type = str(component_type or "").strip()
        self.plugin_id = str(plugin_id or "").strip()
        super().__init__(message)


class ComponentTypes(str, Enum):
    ACTION = "ACTION"
    COMMAND = "COMMAND"
    TOOL = "TOOL"
    EVENT_HANDLER = "EVENT_HANDLER"
    HOOK_HANDLER = "HOOK_HANDLER"
    MESSAGE_GATEWAY = "MESSAGE_GATEWAY"
    HOME_CARD = "HOME_CARD"


ComponentChatScope = Literal["all", "group", "private"]


def _normalize_chat_scope(raw_value: Any) -> ComponentChatScope:
    """规范化组件聊天类型适用范围。"""

    normalized_value = str(raw_value or "all").strip().lower()
    if normalized_value == "group":
        return "group"
    if normalized_value == "private":
        return "private"
    return "all"


class StatusDict(TypedDict):
    total: int
    action: int
    command: int
    tool: int
    event_handler: int
    hook_handler: int
    message_gateway: int
    plugins: int


class ComponentEntry:
    """组件条目"""

    __slots__ = (
        "name",
        "full_name",
        "component_type",
        "plugin_id",
        "metadata",
        "enabled",
        "timeout_ms",
        "compiled_pattern",
        "disabled_session",
        "chat_scope",
        "allowed_session",
    )

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> None:
        self.name: str = name
        self.full_name: str = f"{plugin_id}.{name}"
        self.component_type: ComponentTypes = ComponentTypes(component_type)
        self.plugin_id: str = plugin_id
        self.metadata: Dict[str, Any] = metadata
        self.enabled: bool = metadata.get("enabled", True)
        self.timeout_ms: int = normalize_component_timeout_ms(metadata.get("timeout_ms", 0))
        self.disabled_session: Set[str] = set()
        self.chat_scope: ComponentChatScope = _normalize_chat_scope(chat_scope)
        self.allowed_session: Set[str] = {
            str(session_id).strip()
            for session_id in (allowed_session or [])
            if str(session_id).strip()
        }

class ActionEntry(ComponentEntry):
    """Action 组件条目"""

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> None:
        super().__init__(name, component_type, plugin_id, metadata, chat_scope, allowed_session)


class CommandEntry(ComponentEntry):
    """Command 组件条目"""

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> None:
        super().__init__(name, component_type, plugin_id, metadata, chat_scope, allowed_session)
        self.aliases: List[str] = metadata.get("aliases", [])
        self.compiled_pattern: Optional[re.Pattern] = None
        if pattern := metadata.get("command_pattern", ""):
            try:
                self.compiled_pattern = re.compile(pattern)
            except (re.error, TypeError) as e:
                logger.warning(f"命令 {self.full_name} 正则编译失败: {e}")


class ToolEntry(ComponentEntry):
    """Tool 组件条目"""

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> None:
        self.description: str = str(
            metadata.get("description", "") or metadata.get("brief_description", "") or f"工具 {name}"
        ).strip()
        self.parameters: List[Dict[str, Any]] = metadata.get("parameters", [])
        self.parameters_raw: Dict[str, Any] | List[Dict[str, Any]] = metadata.get("parameters_raw", {})
        self.invoke_method: str = str(metadata.get("invoke_method", "plugin.invoke_tool") or "plugin.invoke_tool").strip()
        self.legacy_component_type: str = str(metadata.get("legacy_component_type", "") or "").strip()
        super().__init__(name, component_type, plugin_id, metadata, chat_scope, allowed_session)

    def _get_parameters_schema(self) -> Dict[str, Any] | None:
        """获取当前工具条目的对象级参数 Schema。

        Returns:
            Dict[str, Any] | None: 归一化后的参数 Schema。
        """

        if isinstance(self.parameters_raw, dict) and self.parameters_raw:
            if self.parameters_raw.get("type") == "object" or "properties" in self.parameters_raw:
                return dict(self.parameters_raw)

            required_names: List[str] = []
            normalized_properties: Dict[str, Any] = {}
            for property_name, property_schema in self.parameters_raw.items():
                if not isinstance(property_schema, dict):
                    continue
                property_schema_copy = dict(property_schema)
                if bool(property_schema_copy.pop("required", False)):
                    required_names.append(str(property_name))
                normalized_properties[str(property_name)] = property_schema_copy

            schema: Dict[str, Any] = {
                "type": "object",
                "properties": normalized_properties,
            }
            if required_names:
                schema["required"] = required_names
            return schema

        if isinstance(self.parameters, list) and self.parameters:
            properties: Dict[str, Any] = {}
            required_names: List[str] = []
            for parameter in self.parameters:
                if not isinstance(parameter, dict):
                    continue
                parameter_name = str(parameter.get("name", "") or "").strip()
                if not parameter_name:
                    continue
                if bool(parameter.get("required", False)):
                    required_names.append(parameter_name)
                properties[parameter_name] = {
                    key: value
                    for key, value in parameter.items()
                    if key not in {"name", "required", "param_type"}
                }
                properties[parameter_name]["type"] = str(
                    parameter.get("type", parameter.get("param_type", "string")) or "string"
                )

            schema = {
                "type": "object",
                "properties": properties,
            }
            if required_names:
                schema["required"] = required_names
            return schema

        return None


class EventHandlerEntry(ComponentEntry):
    """EventHandler 组件条目"""

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> None:
        self.event_type: str = metadata.get("event_type", "")
        self.weight: int = metadata.get("weight", 0)
        self.intercept_message: bool = metadata.get("intercept_message", False)
        super().__init__(name, component_type, plugin_id, metadata, chat_scope, allowed_session)


class HookHandlerEntry(ComponentEntry):
    """HookHandler 组件条目。"""

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> None:
        self.hook: str = self._normalize_hook_name(metadata.get("hook", ""))
        self.mode: str = self._normalize_mode(metadata.get("mode", "blocking"))
        self.order: str = self._normalize_order(metadata.get("order", "normal"))
        self.error_policy: str = self._normalize_error_policy(metadata.get("error_policy", "skip"))
        super().__init__(name, component_type, plugin_id, metadata, chat_scope, allowed_session)

    @staticmethod
    def _normalize_error_policy(raw_value: Any) -> str:
        """规范化 Hook 异常处理策略。

        Args:
            raw_value: 原始异常处理策略值。

        Returns:
            str: 规范化后的异常处理策略。

        Raises:
            ValueError: 当异常处理策略不受支持时抛出。
        """

        normalized_source = getattr(raw_value, "value", raw_value)
        normalized_value = str(normalized_source or "").strip().lower() or "skip"
        if normalized_value not in {"abort", "skip", "log"}:
            raise ValueError(f"HookHandler 异常处理策略不合法: {raw_value}")
        return normalized_value

    @staticmethod
    def _normalize_hook_name(raw_value: Any) -> str:
        """规范化命名 Hook 名称。

        Args:
            raw_value: 原始 Hook 名称。

        Returns:
            str: 去空白后的 Hook 名称。

        Raises:
            ValueError: 当 Hook 名称为空时抛出。
        """

        normalized_source = getattr(raw_value, "value", raw_value)
        if not (normalized_value := str(normalized_source or "").strip()):
            raise ValueError("HookHandler 的 hook 名称不能为空")
        return normalized_value

    @staticmethod
    def _normalize_mode(raw_value: Any) -> str:
        """规范化 Hook 处理模式。

        Args:
            raw_value: 原始模式值。

        Returns:
            str: 规范化后的模式。

        Raises:
            ValueError: 当模式不受支持时抛出。
        """

        normalized_source = getattr(raw_value, "value", raw_value)
        normalized_value = str(normalized_source or "").strip().lower() or "blocking"
        if normalized_value not in {"blocking", "observe"}:
            raise ValueError(f"HookHandler 模式不合法: {raw_value}")
        return normalized_value

    @staticmethod
    def _normalize_order(raw_value: Any) -> str:
        """规范化 Hook 顺序槽位。

        Args:
            raw_value: 原始顺序值。

        Returns:
            str: 规范化后的顺序槽位。

        Raises:
            ValueError: 当顺序值不受支持时抛出。
        """

        normalized_source = getattr(raw_value, "value", raw_value)
        normalized_value = str(normalized_source or "").strip().lower() or "normal"
        if normalized_value not in {"early", "normal", "late"}:
            raise ValueError(f"HookHandler 顺序槽位不合法: {raw_value}")
        return normalized_value

    @staticmethod
    def _normalize_timeout_ms(raw_value: Any) -> int:
        """规范化 Hook 超时配置。

        Args:
            raw_value: 原始超时值。

        Returns:
            int: 规范化后的超时毫秒数。

        Raises:
            ValueError: 当超时值为负数或无法转换为整数时抛出。
        """

        try:
            timeout_ms = int(raw_value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"HookHandler 超时配置不合法: {raw_value}") from exc
        if timeout_ms < 0:
            raise ValueError(f"HookHandler 超时配置不能为负数: {raw_value}")
        return timeout_ms

    @property
    def is_blocking(self) -> bool:
        """返回当前 Hook 是否为阻塞模式。"""

        return self.mode == "blocking"

    @property
    def is_observe(self) -> bool:
        """返回当前 Hook 是否为观察模式。"""

        return self.mode == "observe"


class MessageGatewayEntry(ComponentEntry):
    """MessageGateway 组件条目"""

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> None:
        self.route_type: str = self._normalize_route_type(metadata.get("route_type", ""))
        self.platform: str = str(metadata.get("platform", "") or "").strip()
        self.protocol: str = str(metadata.get("protocol", "") or "").strip()
        self.account_id: str = str(metadata.get("account_id", "") or "").strip()
        self.scope: str = str(metadata.get("scope", "") or "").strip()
        super().__init__(name, component_type, plugin_id, metadata, chat_scope, allowed_session)

    @staticmethod
    def _normalize_route_type(raw_value: Any) -> str:
        """规范化消息网关路由类型。

        Args:
            raw_value: 原始路由类型值。

        Returns:
            str: 规范化后的路由类型。

        Raises:
            ValueError: 当路由类型不受支持时抛出。
        """

        normalized_value = str(raw_value or "").strip().lower()
        route_type_aliases = {
            "send": "send",
            "receive": "receive",
            "recv": "receive",
            "recive": "receive",
            "duplex": "duplex",
        }
        route_type = route_type_aliases.get(normalized_value)
        if route_type is None:
            raise ValueError(f"MessageGateway 路由类型不合法: {raw_value}")
        return route_type

    @property
    def supports_send(self) -> bool:
        """返回当前网关是否支持出站。"""

        return self.route_type in {"send", "duplex"}

    @property
    def supports_receive(self) -> bool:
        """返回当前网关是否支持入站。"""

        return self.route_type in {"receive", "duplex"}


class HomeCardEntry(ComponentEntry):
    """WebUI 首页卡片组件条目。"""

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> None:
        self.title: str = str(metadata.get("title", "") or name).strip()
        self.description: str = str(metadata.get("description", "") or "").strip()
        self.width: str = self._normalize_width(metadata.get("width", "medium"))
        self.order: int = self._normalize_order(metadata.get("order", 1000))
        super().__init__(name, component_type, plugin_id, metadata, chat_scope, allowed_session)

    @staticmethod
    def _normalize_width(raw_value: Any) -> str:
        """规范化首页卡片宽度。"""

        normalized_value = str(raw_value or "medium").strip().lower()
        if normalized_value in {"small", "medium", "large", "wide", "full"}:
            return normalized_value
        return "medium"

    @staticmethod
    def _normalize_order(raw_value: Any) -> int:
        """规范化首页卡片默认排序。"""

        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return 1000


class ComponentRegistry:
    """Host 侧组件注册表。

    由 Supervisor 在收到 plugin.register_components 时调用。
    供业务层查询可用组件、匹配命令、调度 action/event 等。
    """

    def __init__(self, hook_spec_registry: Optional[HookSpecRegistry] = None) -> None:
        """初始化组件注册表。

        Args:
            hook_spec_registry: 可选的 Hook 规格注册中心；提供后会在注册
                HookHandler 时执行规格校验。
        """

        # 全量索引
        self._components: Dict[str, ComponentEntry] = {}  # full_name -> comp

        # 按类型索引
        self._by_type: Dict[ComponentTypes, Dict[str, ComponentEntry]] = {
            comp_type: {} for comp_type in ComponentTypes
        }  # component_type -> (full_name -> comp)

        # 按插件索引
        self._by_plugin: Dict[str, List[ComponentEntry]] = {}
        self._hook_spec_registry = hook_spec_registry

    @staticmethod
    def _convert_action_metadata_to_tool_metadata(
        name: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """将旧 Action 元数据转换为统一 Tool 元数据。

        Args:
            name: 组件名称。
            metadata: Action 原始元数据。

        Returns:
            Dict[str, Any]: 转换后的 Tool 元数据。
        """

        action_parameters = metadata.get("action_parameters")
        parameters_schema: Dict[str, Any] | None = None
        if isinstance(action_parameters, dict) and action_parameters:
            properties: Dict[str, Any] = {}
            for parameter_name, parameter_description in action_parameters.items():
                normalized_name = str(parameter_name or "").strip()
                if not normalized_name:
                    continue
                properties[normalized_name] = {
                    "type": "string",
                    "description": str(parameter_description or "").strip() or "兼容旧 Action 参数",
                }
            if properties:
                parameters_schema = {
                    "type": "object",
                    "properties": properties,
                }

        description = str(metadata.get("description", "") or metadata.get("brief_description", "") or f"工具 {name}").strip()
        return {
            **metadata,
            "description": description,
            "parameters_raw": parameters_schema or {},
            "invoke_method": "plugin.invoke_action",
            "legacy_action": True,
            "legacy_component_type": "ACTION",
        }

    @staticmethod
    def _normalize_component_type(component_type: str) -> ComponentTypes:
        """规范化组件类型输入。

        Args:
            component_type: 原始组件类型字符串。

        Returns:
            ComponentTypes: 规范化后的组件类型枚举。

        Raises:
            ValueError: 当组件类型不受支持时抛出。
        """

        normalized_value = str(component_type or "").strip().upper()
        return ComponentTypes(normalized_value)

    def clear(self) -> None:
        """清空全部组件注册状态。"""
        self._components.clear()
        for type_dict in self._by_type.values():
            type_dict.clear()
        self._by_plugin.clear()

    @staticmethod
    def _is_legacy_action_component(component: ComponentEntry) -> bool:
        """判断组件是否为兼容旧 Action 的 Tool 条目。

        Args:
            component: 待判断的组件条目。

        Returns:
            bool: 是否为兼容旧 Action 组件。
        """

        if not isinstance(component, ToolEntry):
            return False
        return str(component.metadata.get("legacy_component_type", "") or "").strip().upper() == "ACTION"

    def _validate_hook_handler_entry(self, component: HookHandlerEntry) -> None:
        """校验 HookHandler 是否满足已注册的 Hook 规格。

        Args:
            component: 待校验的 HookHandler 条目。

        Raises:
            ComponentRegistrationError: HookHandler 声明不合法时抛出。
        """

        if self._hook_spec_registry is None:
            return

        hook_spec = self._hook_spec_registry.get_hook_spec(component.hook)
        if hook_spec is None:
            raise ComponentRegistrationError(
                f"HookHandler {component.full_name} 声明了未注册的 Hook: {component.hook}",
                component_name=component.name,
                component_type=component.component_type.value,
                plugin_id=component.plugin_id,
            )

        if component.is_blocking and not hook_spec.allow_blocking:
            raise ComponentRegistrationError(
                f"HookHandler {component.full_name} 不能注册为 blocking：Hook {component.hook} 不允许 blocking 处理器",
                component_name=component.name,
                component_type=component.component_type.value,
                plugin_id=component.plugin_id,
            )

        if component.is_observe and not hook_spec.allow_observe:
            raise ComponentRegistrationError(
                f"HookHandler {component.full_name} 不能注册为 observe：Hook {component.hook} 不允许 observe 处理器",
                component_name=component.name,
                component_type=component.component_type.value,
                plugin_id=component.plugin_id,
            )

        if component.error_policy == "abort" and not hook_spec.allow_abort:
            raise ComponentRegistrationError(
                f"HookHandler {component.full_name} 不能使用 error_policy=abort：Hook {component.hook} 不允许 abort",
                component_name=component.name,
                component_type=component.component_type.value,
                plugin_id=component.plugin_id,
            )

    def _build_component_entry(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> ComponentEntry:
        """根据声明构造组件条目。

        Args:
            name: 组件名称。
            component_type: 组件类型。
            plugin_id: 插件 ID。
            metadata: 组件元数据。

        Returns:
            ComponentEntry: 已构造并完成校验的组件条目。

        Raises:
            ComponentRegistrationError: 组件声明不合法时抛出。
        """

        try:
            normalized_type = self._normalize_component_type(component_type)
            normalized_metadata = dict(metadata)
            if normalized_type == ComponentTypes.ACTION:
                normalized_metadata = self._convert_action_metadata_to_tool_metadata(name, normalized_metadata)
                component = ToolEntry(
                    name,
                    ComponentTypes.TOOL.value,
                    plugin_id,
                    normalized_metadata,
                    chat_scope,
                    allowed_session,
                )
            elif normalized_type == ComponentTypes.COMMAND:
                component = CommandEntry(
                    name,
                    normalized_type.value,
                    plugin_id,
                    normalized_metadata,
                    chat_scope,
                    allowed_session,
                )
            elif normalized_type == ComponentTypes.TOOL:
                component = ToolEntry(
                    name,
                    normalized_type.value,
                    plugin_id,
                    normalized_metadata,
                    chat_scope,
                    allowed_session,
                )
            elif normalized_type == ComponentTypes.EVENT_HANDLER:
                component = EventHandlerEntry(
                    name,
                    normalized_type.value,
                    plugin_id,
                    normalized_metadata,
                    chat_scope,
                    allowed_session,
                )
            elif normalized_type == ComponentTypes.HOOK_HANDLER:
                component = HookHandlerEntry(
                    name,
                    normalized_type.value,
                    plugin_id,
                    normalized_metadata,
                    chat_scope,
                    allowed_session,
                )
                self._validate_hook_handler_entry(component)
            elif normalized_type == ComponentTypes.MESSAGE_GATEWAY:
                component = MessageGatewayEntry(
                    name,
                    normalized_type.value,
                    plugin_id,
                    normalized_metadata,
                    chat_scope,
                    allowed_session,
                )
            elif normalized_type == ComponentTypes.HOME_CARD:
                component = HomeCardEntry(
                    name,
                    normalized_type.value,
                    plugin_id,
                    normalized_metadata,
                    chat_scope,
                    allowed_session,
                )
            else:
                raise ComponentRegistrationError(
                    f"组件类型 {component_type} 不存在",
                    component_name=name,
                    component_type=component_type,
                    plugin_id=plugin_id,
                )
        except ComponentRegistrationError:
            raise
        except Exception as exc:
            raise ComponentRegistrationError(
                str(exc),
                component_name=name,
                component_type=component_type,
                plugin_id=plugin_id,
            ) from exc

        return component

    def _remove_existing_component_entry(self, component: ComponentEntry) -> None:
        """移除同名旧组件条目。

        Args:
            component: 即将写入的新组件条目。
        """

        if component.full_name not in self._components:
            return

        logger.warning(f"组件 {component.full_name} 已存在，覆盖")
        old_component = self._components[component.full_name]
        old_list = self._by_plugin.get(old_component.plugin_id)
        if old_list is not None:
            with contextlib.suppress(ValueError):
                old_list.remove(old_component)
        if old_type_dict := self._by_type.get(old_component.component_type):
            old_type_dict.pop(component.full_name, None)

    def _add_component_entry(self, component: ComponentEntry) -> None:
        """写入单个组件条目到全部索引。

        Args:
            component: 待写入的组件条目。
        """

        self._remove_existing_component_entry(component)
        self._components[component.full_name] = component
        self._by_type[component.component_type][component.full_name] = component
        self._by_plugin.setdefault(component.plugin_id, []).append(component)

    # ====== 注册 / 注销 ======
    def register_component(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: Dict[str, Any],
        chat_scope: str = "all",
        allowed_session: Optional[List[str]] = None,
    ) -> bool:
        """注册单个组件。

        Args:
            name: 组件名称（不含插件 ID 前缀）。
            component_type: 组件类型（如 ``ACTION``、``COMMAND`` 等）。
            plugin_id: 插件 ID。
            metadata: 组件元数据。

        Returns:
            bool: 注册成功时恒为 ``True``。

        Raises:
            ComponentRegistrationError: 组件声明不合法时抛出。
        """

        component = self._build_component_entry(
            name,
            component_type,
            plugin_id,
            metadata,
            chat_scope,
            allowed_session,
        )
        self._add_component_entry(component)
        return True

    def register_plugin_components(self, plugin_id: str, components: List[Dict[str, Any]]) -> int:
        """批量替换一个插件的组件集合。

        该方法会先完整校验所有组件声明，只有全部通过后才会替换旧组件，
        从而避免插件进入半注册状态。

        Args:
            plugin_id: 插件 ID。
            components: 组件声明字典列表。

        Returns:
            int: 实际注册的组件数量。

        Raises:
            ComponentRegistrationError: 任一组件声明不合法时抛出。
        """

        prepared_components: List[ComponentEntry] = []
        for component_data in components:
            raw_metadata = (
                dict(component_data.get("metadata", {}))
                if isinstance(component_data.get("metadata"), dict)
                else {}
            )
            chat_scope = str(component_data.get("chat_scope", raw_metadata.pop("chat_scope", "all")) or "all")
            raw_allowed_session = component_data.get("allowed_session", raw_metadata.pop("allowed_session", []))
            allowed_session = (
                [str(item).strip() for item in raw_allowed_session if str(item).strip()]
                if isinstance(raw_allowed_session, list)
                else []
            )
            prepared_components.append(
                self._build_component_entry(
                    name=str(component_data.get("name", "") or ""),
                    component_type=str(component_data.get("component_type", "") or ""),
                    plugin_id=plugin_id,
                    metadata=raw_metadata,
                    chat_scope=chat_scope,
                    allowed_session=allowed_session,
                )
            )

        self.remove_components_by_plugin(plugin_id)
        for component in prepared_components:
            self._add_component_entry(component)
        return len(prepared_components)

    def remove_components_by_plugin(self, plugin_id: str) -> int:
        """移除某个插件的所有组件，返回移除数量。

        Args:
            plugin_id (str): 插件id
        Returns:
            count (int): 移除的组件数量
        """
        comps = self._by_plugin.pop(plugin_id, [])
        for comp in comps:
            self._components.pop(comp.full_name, None)
            if type_dict := self._by_type.get(comp.component_type):
                type_dict.pop(comp.full_name, None)
        return len(comps)

    # ====== 启用 / 禁用 ======
    def check_component_enabled(
        self,
        component: ComponentEntry,
        session_id: Optional[str] = None,
        is_group_chat: Optional[bool] = None,
        group_id: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        if session_id and session_id in component.disabled_session:
            return False
        if is_group_chat is not None:
            if component.chat_scope == "group" and is_group_chat is not True:
                return False
            if component.chat_scope == "private" and is_group_chat is not False:
                return False
        if component.allowed_session:
            allowed_candidates = {str(session_id or "").strip(), str(group_id or "").strip()}
            if platform and group_id:
                allowed_candidates.add(f"{platform}:{group_id}")
            allowed_candidates.discard("")
            if component.allowed_session.isdisjoint(allowed_candidates):
                return False
        return component.enabled

    def toggle_component_status(self, full_name: str, enabled: bool, session_id: Optional[str] = None) -> bool:
        """启用或禁用指定组件。

        Args:
            full_name (str): 组件全名
            enabled (bool): 使能情况
            session_id (Optional[str]): 可选的会话ID，仅对该会话禁用（如果提供）
        Returns:
            success (bool): 是否成功设置（失败原因通常是组件不存在）
        """
        comp = self._components.get(full_name)
        if comp is None:
            return False
        if session_id:
            if enabled:
                comp.disabled_session.discard(session_id)
            else:
                comp.disabled_session.add(session_id)
        else:
            comp.enabled = enabled
        return True

    def set_component_enabled(self, full_name: str, enabled: bool, session_id: Optional[str] = None) -> bool:
        """设置指定组件的启用状态。

        Args:
            full_name: 组件全名。
            enabled: 目标启用状态。
            session_id: 可选的会话 ID，仅对该会话生效。

        Returns:
            bool: 是否设置成功。
        """

        return self.toggle_component_status(full_name, enabled, session_id=session_id)

    def toggle_plugin_status(self, plugin_id: str, enabled: bool, session_id: Optional[str] = None) -> int:
        """批量启用或禁用某插件的所有组件。

        Args:
            plugin_id (str): 插件id
            enabled (bool): 使能情况
            session_id (Optional[str]): 可选的会话ID，仅对该会话禁用（如果提供）
        Returns:
            count (int): 成功设置的组件数量（失败原因通常是插件不存在）
        """
        comps = self._by_plugin.get(plugin_id, [])
        for comp in comps:
            if session_id:
                if enabled:
                    comp.disabled_session.discard(session_id)
                else:
                    comp.disabled_session.add(session_id)
            else:
                comp.enabled = enabled
        return len(comps)

    def get_component(self, full_name: str) -> Optional[ComponentEntry]:
        """按全名查询。

        Args:
            full_name (str): 组件全名
        Returns:
            component (Optional[ComponentEntry]): 组件条目，未找到时为 None
        """
        return self._components.get(full_name)

    def get_components_by_type(
        self,
        component_type: str,
        *,
        enabled_only: bool = True,
        session_id: Optional[str] = None,
        is_group_chat: Optional[bool] = None,
        group_id: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> List[ComponentEntry]:
        """按类型查询组件

        Args:
            component_type (str): 组件类型（如 `ACTION`、`COMMAND` 等）
            enabled_only (bool): 是否仅返回启用的组件
            session_id (Optional[str]): 可选的会话ID，若提供则考虑会话禁用状态
        Returns:
            components (List[ComponentEntry]): 组件条目列表
        """
        try:
            comp_type = self._normalize_component_type(component_type)
        except ValueError:
            logger.error(f"组件类型 {component_type} 不存在")
            raise

        if comp_type == ComponentTypes.ACTION:
            action_components = [
                component
                for component in self._by_type.get(ComponentTypes.TOOL, {}).values()
                if self._is_legacy_action_component(component)
            ]
            if enabled_only:
                return [
                    component
                    for component in action_components
                    if self.check_component_enabled(component, session_id, is_group_chat, group_id, platform)
                ]
            return action_components

        type_dict = self._by_type.get(comp_type, {})
        if enabled_only:
            return [
                c
                for c in type_dict.values()
                if self.check_component_enabled(c, session_id, is_group_chat, group_id, platform)
            ]
        return list(type_dict.values())

    def get_components_by_plugin(
        self, plugin_id: str, *, enabled_only: bool = True, session_id: Optional[str] = None
    ) -> List[ComponentEntry]:
        """按插件查询组件。

        Args:
            plugin_id (str): 插件ID
            enabled_only (bool): 是否仅返回启用的组件
            session_id (Optional[str]): 可选的会话ID，若提供则考虑会话禁用状态
        Returns:
            components (List[ComponentEntry]): 组件条目列表
        """
        comps = self._by_plugin.get(plugin_id, [])
        return [c for c in comps if self.check_component_enabled(c, session_id)] if enabled_only else list(comps)

    def find_command_by_text(
        self, text: str, session_id: Optional[str] = None
    ) -> Optional[Tuple[ComponentEntry, Dict[str, Any]]]:
        """通过文本匹配命令正则，返回 (组件, matched_groups) 元组。

        matched_groups 为正则命名捕获组 dict，别名匹配时为空 dict。
        Args:
            text (str): 待匹配文本
            session_id (Optional[str]): 可选的会话ID，若提供则考虑会话禁用状态
        Returns:
            result (Optional[tuple[ComponentEntry, Dict[str, Any]]]): 匹配到的组件及正则捕获组，未找到时为 None
        """
        for comp in self._by_type.get(ComponentTypes.COMMAND, {}).values():
            if not self.check_component_enabled(comp, session_id):
                continue
            if not isinstance(comp, CommandEntry):
                continue
            if comp.compiled_pattern:
                if m := comp.compiled_pattern.search(text):
                    return comp, m.groupdict()
            # 别名匹配
            for alias in comp.aliases:
                if text.startswith(alias):
                    return comp, {}
        return None

    def get_event_handlers(
        self, event_type: str, *, enabled_only: bool = True, session_id: Optional[str] = None
    ) -> List[EventHandlerEntry]:
        """查询指定事件类型的事件处理器组件。

        Args:
            event_type (str): 事件类型
            enabled_only (bool): 是否仅返回启用的组件
            session_id (Optional[str]): 可选的会话ID，若提供则考虑会话禁用状态
        Returns:
            handlers (List[EventHandlerEntry]): 符合条件的 EventHandler 组件列表，按 weight 降序排序
        """
        handlers: List[EventHandlerEntry] = []
        for comp in self._by_type.get(ComponentTypes.EVENT_HANDLER, {}).values():
            if enabled_only and not self.check_component_enabled(comp, session_id):
                continue
            if not isinstance(comp, EventHandlerEntry):
                continue
            if comp.event_type == event_type:
                handlers.append(comp)
        handlers.sort(key=lambda c: c.weight, reverse=True)
        return handlers

    def get_hook_handlers(
        self, hook_name: str, *, enabled_only: bool = True, session_id: Optional[str] = None
    ) -> List[HookHandlerEntry]:
        """获取订阅指定命名 Hook 的全部处理器。

        Args:
            hook_name: 目标 Hook 名称。
            enabled_only: 是否仅返回启用的组件。
            session_id: 可选的会话 ID，若提供则考虑会话禁用状态。

        Returns:
            List[HookHandlerEntry]: 符合条件的 HookHandler 组件列表。
        """
        handlers: List[HookHandlerEntry] = []
        for comp in self._by_type.get(ComponentTypes.HOOK_HANDLER, {}).values():
            if enabled_only and not self.check_component_enabled(comp, session_id):
                continue
            if not isinstance(comp, HookHandlerEntry):
                continue
            if comp.hook == hook_name:
                handlers.append(comp)
        handlers.sort(key=lambda comp: (self._get_hook_mode_rank(comp.mode), self._get_hook_order_rank(comp.order), comp.plugin_id, comp.name))
        return handlers

    @staticmethod
    def _get_hook_mode_rank(mode: str) -> int:
        """返回 Hook 模式的排序权重。

        Args:
            mode: Hook 模式字符串。

        Returns:
            int: 越小表示越靠前。
        """

        return {"blocking": 0, "observe": 1}.get(mode, 99)

    @staticmethod
    def _get_hook_order_rank(order: str) -> int:
        """返回 Hook 顺序槽位的排序权重。

        Args:
            order: Hook 顺序槽位字符串。

        Returns:
            int: 越小表示越靠前。
        """

        return {"early": 0, "normal": 1, "late": 2}.get(order, 99)

    def get_message_gateway(
        self,
        plugin_id: str,
        name: str,
        *,
        enabled_only: bool = True,
        session_id: Optional[str] = None,
    ) -> Optional[MessageGatewayEntry]:
        """按插件和组件名获取单个消息网关。

        Args:
            plugin_id: 插件 ID。
            name: 网关组件名称。
            enabled_only: 是否仅返回启用的组件。
            session_id: 可选的会话 ID。

        Returns:
            Optional[MessageGatewayEntry]: 若存在则返回消息网关条目。
        """

        component = self._components.get(f"{plugin_id}.{name}")
        if not isinstance(component, MessageGatewayEntry):
            return None
        if enabled_only and not self.check_component_enabled(component, session_id):
            return None
        return component

    def get_message_gateways(
        self,
        *,
        plugin_id: Optional[str] = None,
        platform: str = "",
        route_type: str = "",
        enabled_only: bool = True,
        session_id: Optional[str] = None,
    ) -> List[MessageGatewayEntry]:
        """查询消息网关组件列表。

        Args:
            plugin_id: 可选的插件 ID 过滤条件。
            platform: 可选的平台过滤条件。
            route_type: 可选的路由类型过滤条件。
            enabled_only: 是否仅返回启用的组件。
            session_id: 可选的会话 ID。

        Returns:
            List[MessageGatewayEntry]: 符合条件的消息网关组件列表。
        """

        normalized_platform = str(platform or "").strip()
        normalized_route_type = str(route_type or "").strip().lower()
        gateways: List[MessageGatewayEntry] = []
        for comp in self._by_type.get(ComponentTypes.MESSAGE_GATEWAY, {}).values():
            if not isinstance(comp, MessageGatewayEntry):
                continue
            if plugin_id and comp.plugin_id != plugin_id:
                continue
            if enabled_only and not self.check_component_enabled(comp, session_id):
                continue
            if normalized_platform and comp.platform != normalized_platform:
                continue
            if normalized_route_type and comp.route_type != normalized_route_type:
                continue
            gateways.append(comp)
        return gateways

    def get_tools(self, *, enabled_only: bool = True, session_id: Optional[str] = None) -> List[ToolEntry]:
        """查询所有工具组件。

        Args:
            enabled_only (bool): 是否仅返回启用的组件
            session_id (Optional[str]): 可选的会话ID，若提供则考虑会话禁用状态
        Returns:
            tools (List[ToolEntry]): 符合条件的 Tool 组件列表
        """
        tools: List[ToolEntry] = []
        for comp in self._by_type.get(ComponentTypes.TOOL, {}).values():
            if enabled_only and not self.check_component_enabled(comp, session_id):
                continue
            if isinstance(comp, ToolEntry):
                tools.append(comp)
        return tools

    def get_tools_for_llm(self, *, enabled_only: bool = True, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """兼容旧接口，返回可供 LLM 使用的工具条目列表。

        Args:
            enabled_only: 是否仅返回启用的组件。
            session_id: 可选的会话 ID，若提供则考虑会话禁用状态。

        Returns:
            List[Dict[str, Any]]: 兼容旧结构的工具组件字典列表。
        """

        return [
            {
                "name": tool.full_name,
                "description": tool.description,
                "parameters": (
                    dict(tool.parameters_raw)
                    if isinstance(tool.parameters_raw, dict) and tool.parameters_raw
                    else tool._get_parameters_schema() or {}
                ),
                "parameters_raw": tool.parameters_raw,
                "enabled": tool.enabled,
                "plugin_id": tool.plugin_id,
            }
            for tool in self.get_tools(enabled_only=enabled_only, session_id=session_id)
            if not self._is_legacy_action_component(tool)
        ]

    # ====== 统计信息 ======
    def get_stats(self) -> StatusDict:
        """获取注册统计。

        Returns:
            stats (StatusDict): 组件统计信息，包括总数、各类型数量、插件数量等
        """
        return StatusDict(
            total=len(self._components),
            action=len(
                [
                    component
                    for component in self._by_type.get(ComponentTypes.TOOL, {}).values()
                    if self._is_legacy_action_component(component)
                ]
            ),
            command=len(self._by_type[ComponentTypes.COMMAND]),
            tool=len(
                [
                    component
                    for component in self._by_type.get(ComponentTypes.TOOL, {}).values()
                    if not self._is_legacy_action_component(component)
                ]
            ),
            event_handler=len(self._by_type[ComponentTypes.EVENT_HANDLER]),
            hook_handler=len(self._by_type[ComponentTypes.HOOK_HANDLER]),
            message_gateway=len(self._by_type[ComponentTypes.MESSAGE_GATEWAY]),
            plugins=len(self._by_plugin),
        )
