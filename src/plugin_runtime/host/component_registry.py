"""Host-side ComponentRegistry

对齐旧系统 component_registry.py 的核心能力：
- 按类型注册组件（action / command / tool / event_handler / workflow_handler / message_gateway）
- 命名空间 (plugin_id.component_name)
- 命令正则匹配
- 组件启用/禁用
- 多维度查询（按名称、类型、插件）
- 注册统计
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Set, TypedDict, Tuple

import contextlib
import re

from src.common.logger import get_logger

logger = get_logger("plugin_runtime.host.component_registry")


class ComponentTypes(str, Enum):
    ACTION = "ACTION"
    COMMAND = "COMMAND"
    TOOL = "TOOL"
    EVENT_HANDLER = "EVENT_HANDLER"
    WORKFLOW_HANDLER = "WORKFLOW_HANDLER"
    MESSAGE_GATEWAY = "MESSAGE_GATEWAY"


class StatusDict(TypedDict):
    total: int
    ACTION: int
    COMMAND: int
    TOOL: int
    EVENT_HANDLER: int
    WORKFLOW_HANDLER: int
    MESSAGE_GATEWAY: int
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
        "compiled_pattern",
        "disabled_session",
    )

    def __init__(self, name: str, component_type: str, plugin_id: str, metadata: Dict[str, Any]) -> None:
        self.name: str = name
        self.full_name: str = f"{plugin_id}.{name}"
        self.component_type: ComponentTypes = ComponentTypes(component_type)
        self.plugin_id: str = plugin_id
        self.metadata: Dict[str, Any] = metadata
        self.enabled: bool = metadata.get("enabled", True)
        self.disabled_session: Set[str] = set()


class ActionEntry(ComponentEntry):
    """Action 组件条目"""

    def __init__(self, name: str, component_type: str, plugin_id: str, metadata: Dict[str, Any]) -> None:
        super().__init__(name, component_type, plugin_id, metadata)


class CommandEntry(ComponentEntry):
    """Command 组件条目"""

    def __init__(self, name: str, component_type: str, plugin_id: str, metadata: Dict[str, Any]) -> None:
        self.compiled_pattern: Optional[re.Pattern] = None
        self.aliases: List[str] = metadata.get("aliases", [])
        if pattern := metadata.get("command_pattern", ""):
            try:
                self.compiled_pattern = re.compile(pattern)
            except re.error as e:
                logger.warning(f"命令 {self.full_name} 正则编译失败: {e}")
        super().__init__(name, component_type, plugin_id, metadata)


class ToolEntry(ComponentEntry):
    """Tool 组件条目"""

    def __init__(self, name: str, component_type: str, plugin_id: str, metadata: Dict[str, Any]) -> None:
        self.description: str = metadata.get("description", "")
        self.parameters: List[Dict[str, Any]] = metadata.get("parameters", [])
        self.parameters_raw: List[Dict[str, Any]] = metadata.get("parameters_raw", [])
        super().__init__(name, component_type, plugin_id, metadata)


class EventHandlerEntry(ComponentEntry):
    """EventHandler 组件条目"""

    def __init__(self, name: str, component_type: str, plugin_id: str, metadata: Dict[str, Any]) -> None:
        self.event_type: str = metadata.get("event_type", "")
        self.weight: int = metadata.get("weight", 0)
        self.intercept_message: bool = metadata.get("intercept_message", False)
        super().__init__(name, component_type, plugin_id, metadata)


class WorkflowHandlerEntry(ComponentEntry):
    """WorkflowHandler 组件条目"""

    def __init__(self, name: str, component_type: str, plugin_id: str, metadata: Dict[str, Any]) -> None:
        self.stage: str = metadata.get("stage", "")
        self.priority: int = metadata.get("priority", 0)
        super().__init__(name, component_type, plugin_id, metadata)


class MessageGatewayEntry(ComponentEntry):
    """MessageGateway 组件条目"""

    def __init__(self, name: str, component_type: str, plugin_id: str, metadata: Dict[str, Any]) -> None:
        platform = metadata.get("platform")
        if not platform or not isinstance(platform, str):
            raise ValueError(f"MessageGateway 组件 {plugin_id}.{name} 缺少有效的 platform 字段")
        self.platform: str = platform
        super().__init__(name, component_type, plugin_id, metadata)


class ComponentRegistry:
    """Host-side 组件注册表

    由 Supervisor 在收到 plugin.register_components 时调用。
    供业务层查询可用组件、匹配命令、调度 action/event 等。
    """

    def __init__(self) -> None:
        # 全量索引
        self._components: Dict[str, ComponentEntry] = {}  # full_name -> comp

        # 按类型索引
        self._by_type: Dict[ComponentTypes, Dict[str, ComponentEntry]] = {
            comp_type: {} for comp_type in ComponentTypes
        }  # component_type -> (full_name -> comp)

        # 按插件索引
        self._by_plugin: Dict[str, List[ComponentEntry]] = {}

    def clear(self) -> None:
        """清空全部组件注册状态。"""
        self._components.clear()
        for type_dict in self._by_type.values():
            type_dict.clear()
        self._by_plugin.clear()

    # ====== 注册 / 注销 ======
    def register_component(self, name: str, component_type: str, plugin_id: str, metadata: Dict[str, Any]) -> bool:
        """注册单个组件

        Args:
            name: 组件名称（不含插件id前缀）
            component_type: 组件类型（如 `ACTION`、`COMMAND` 等）
            plugin_id: 插件id
            metadata: 组件元数据
        Returns:
            success (bool): 是否成功注册（失败原因通常是组件类型无效）
        """
        try:
            if component_type == ComponentTypes.ACTION:
                comp = ActionEntry(name, component_type, plugin_id, metadata)
            elif component_type == ComponentTypes.COMMAND:
                comp = CommandEntry(name, component_type, plugin_id, metadata)
            elif component_type == ComponentTypes.TOOL:
                comp = ToolEntry(name, component_type, plugin_id, metadata)
            elif component_type == ComponentTypes.EVENT_HANDLER:
                comp = EventHandlerEntry(name, component_type, plugin_id, metadata)
            elif component_type == ComponentTypes.WORKFLOW_HANDLER:
                comp = WorkflowHandlerEntry(name, component_type, plugin_id, metadata)
            elif component_type == ComponentTypes.MESSAGE_GATEWAY:
                comp = MessageGatewayEntry(name, component_type, plugin_id, metadata)
            else:
                raise ValueError(f"组件类型 {component_type} 不存在")
        except ValueError:
            logger.error(f"组件类型 {component_type} 不存在")
            return False

        if comp.full_name in self._components:
            logger.warning(f"组件 {comp.full_name} 已存在，覆盖")
            old_comp = self._components[comp.full_name]
            # 从 _by_plugin 列表中移除旧条目，防止幽灵组件堆积
            old_list = self._by_plugin.get(old_comp.plugin_id)
            if old_list is not None:
                with contextlib.suppress(ValueError):
                    old_list.remove(old_comp)
            # 从旧类型索引中移除，防止类型变更时幽灵残留
            if old_type_dict := self._by_type.get(old_comp.component_type):
                old_type_dict.pop(comp.full_name, None)

        self._components[comp.full_name] = comp
        self._by_type[comp.component_type][comp.full_name] = comp
        self._by_plugin.setdefault(plugin_id, []).append(comp)

        return True

    def register_plugin_components(self, plugin_id: str, components: List[Dict[str, Any]]) -> int:
        """批量注册一个插件的所有组件，返回成功注册数。
        Args:
            plugin_id (str): 插件id
            components (List[Dict[str, Any]]): 组件字典列表，每个组件包含 name, component_type, metadata 等字段
        Returns:
            count (int): 成功注册的组件数量
        """
        count = 0
        for comp_data in components:
            ok = self.register_component(
                name=comp_data.get("name", ""),
                component_type=comp_data.get("component_type", ""),
                plugin_id=plugin_id,
                metadata=comp_data.get("metadata", {}),
            )
            if ok:
                count += 1
        return count

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
    def check_component_enabled(self, component: ComponentEntry, session_id: Optional[str] = None):
        if session_id and session_id in component.disabled_session:
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
        self, component_type: str, *, enabled_only: bool = True, session_id: Optional[str] = None
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
            comp_type = ComponentTypes(component_type)
        except ValueError:
            logger.error(f"组件类型 {component_type} 不存在")
            raise
        type_dict = self._by_type.get(comp_type, {})
        if enabled_only:
            return [c for c in type_dict.values() if self.check_component_enabled(c, session_id)]
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

    def get_workflow_handlers(
        self, stage: str, *, enabled_only: bool = True, session_id: Optional[str] = None
    ) -> List[WorkflowHandlerEntry]:
        """获取特定 workflow 阶段的所有步骤，按 priority 降序。

        Args:
            stage: workflow 阶段名称
            enabled_only: 是否仅返回启用的组件
            session_id: 可选的会话ID，若提供则考虑会话禁用状态
        Returns:
            handlers (List[WorkflowHandlerEntry]): 符合条件的 WorkflowHandler 组件列表，按 priority 降序排序
        """
        handlers: List[WorkflowHandlerEntry] = []
        for comp in self._by_type.get(ComponentTypes.WORKFLOW_HANDLER, {}).values():
            if enabled_only and not self.check_component_enabled(comp, session_id):
                continue
            if not isinstance(comp, WorkflowHandlerEntry):
                continue
            if comp.stage == stage:
                handlers.append(comp)
        handlers.sort(key=lambda c: c.priority, reverse=True)
        return handlers

    def get_message_gateways(
        self, platform: str, *, enabled_only: bool = True, session_id: Optional[str] = None
    ) -> Optional[MessageGatewayEntry]:
        """查询消息网关组件。

        Args:
            platform (str): 平台名称
            enabled_only (bool): 是否仅返回启用的组件
            session_id (Optional[str]): 可选的会话ID，若提供则考虑会话禁用状态
        Returns:
            gateway (Optional[MessageGatewayEntry]): 符合条件的 MessageGateway 组件，可能不存在
        """

        for comp in self._by_type.get(ComponentTypes.MESSAGE_GATEWAY, {}).values():
            if not isinstance(comp, MessageGatewayEntry):
                continue
            if enabled_only and not self.check_component_enabled(comp, session_id):
                continue
            if comp.platform == platform:
                return comp # 返回第一个

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

    # ====== 统计信息 ======
    def get_stats(self) -> StatusDict:
        """获取注册统计。

        Returns:
            stats (StatusDict): 组件统计信息，包括总数、各类型数量、插件数量等
        """
        stats: StatusDict = {"total": len(self._components)}  # type: ignore
        for comp_type, type_dict in self._by_type.items():
            stats[comp_type.value] = len(type_dict)
        stats["plugins"] = len(self._by_plugin)
        return stats
