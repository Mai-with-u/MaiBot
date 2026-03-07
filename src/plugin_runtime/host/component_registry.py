"""Host-side ComponentRegistry

对齐旧系统 component_registry.py 的核心能力：
- 按类型注册组件（action / command / tool / event_handler / workflow_step）
- 命名空间 (plugin_id.component_name)
- 命令正则匹配
- 组件启用/禁用
- 多维度查询（按名称、类型、插件）
- 注册统计
"""

from typing import Any

from src.common.logger import get_logger
import re

logger = get_logger("plugin_runtime.host.component_registry")


class RegisteredComponent:
    """已注册的组件条目"""

    __slots__ = (
        "name", "full_name", "component_type", "plugin_id",
        "metadata", "enabled", "_compiled_pattern",
    )

    def __init__(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: dict[str, Any],
    ):
        self.name = name
        self.full_name = f"{plugin_id}.{name}"
        self.component_type = component_type
        self.plugin_id = plugin_id
        self.metadata = metadata
        self.enabled = metadata.get("enabled", True)

        # 预编译命令正则（仅 command 类型）
        self._compiled_pattern: re.Pattern | None = None
        if component_type == "command":
            if pattern := metadata.get("command_pattern", ""):
                try:
                    self._compiled_pattern = re.compile(pattern)
                except re.error as e:
                    logger.warning(f"命令 {self.full_name} 正则编译失败: {e}")


class ComponentRegistry:
    """Host-side 组件注册表

    由 Supervisor 在收到 plugin.register_components 时调用。
    供业务层查询可用组件、匹配命令、调度 action/event 等。
    """

    def __init__(self):
        # 全量索引
        self._components: dict[str, RegisteredComponent] = {}  # full_name -> comp

        # 按类型索引
        self._by_type: dict[str, dict[str, RegisteredComponent]] = {
            "action": {},
            "command": {},
            "tool": {},
            "event_handler": {},
            "workflow_step": {},
        }

        # 按插件索引
        self._by_plugin: dict[str, list[RegisteredComponent]] = {}

    # ──── 注册 / 注销 ─────────────────────────────────────────

    def register_component(
        self,
        name: str,
        component_type: str,
        plugin_id: str,
        metadata: dict[str, Any],
    ) -> bool:
        """注册单个组件。"""
        comp = RegisteredComponent(name, component_type, plugin_id, metadata)
        if comp.full_name in self._components:
            logger.warning(f"组件 {comp.full_name} 已存在，覆盖")

        self._components[comp.full_name] = comp

        if component_type not in self._by_type:
            self._by_type[component_type] = {}
        self._by_type[component_type][comp.full_name] = comp

        self._by_plugin.setdefault(plugin_id, []).append(comp)

        return True

    def register_plugin_components(
        self,
        plugin_id: str,
        components: list[dict[str, Any]],
    ) -> int:
        """批量注册一个插件的所有组件，返回成功注册数。"""
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
        """移除某个插件的所有组件，返回移除数量。"""
        comps = self._by_plugin.pop(plugin_id, [])
        for comp in comps:
            self._components.pop(comp.full_name, None)
            if type_dict := self._by_type.get(comp.component_type):
                type_dict.pop(comp.full_name, None)
        return len(comps)

    # ──── 启用 / 禁用 ─────────────────────────────────────────

    def set_component_enabled(self, full_name: str, enabled: bool) -> bool:
        """启用或禁用指定组件。"""
        comp = self._components.get(full_name)
        if comp is None:
            return False
        comp.enabled = enabled
        return True

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> int:
        """批量启用或禁用某插件的所有组件。"""
        comps = self._by_plugin.get(plugin_id, [])
        for comp in comps:
            comp.enabled = enabled
        return len(comps)

    # ──── 查询方法 ─────────────────────────────────────────────

    def get_component(self, full_name: str) -> RegisteredComponent | None:
        """按全名查询。"""
        return self._components.get(full_name)

    def get_components_by_type(
        self, component_type: str, *, enabled_only: bool = True
    ) -> list[RegisteredComponent]:
        """按类型查询。"""
        type_dict = self._by_type.get(component_type, {})
        if enabled_only:
            return [c for c in type_dict.values() if c.enabled]
        return list(type_dict.values())

    def get_components_by_plugin(
        self, plugin_id: str, *, enabled_only: bool = True
    ) -> list[RegisteredComponent]:
        """按插件查询。"""
        comps = self._by_plugin.get(plugin_id, [])
        return [c for c in comps if c.enabled] if enabled_only else list(comps)

    def find_command_by_text(self, text: str) -> RegisteredComponent | None:
        """通过文本匹配命令正则，返回第一个匹配的 command 组件。"""
        for comp in self._by_type.get("command", {}).values():
            if not comp.enabled:
                continue
            if comp._compiled_pattern and comp._compiled_pattern.search(text):
                return comp
            # 别名匹配
            aliases = comp.metadata.get("aliases", [])
            for alias in aliases:
                if text.startswith(alias):
                    return comp
        return None

    def get_event_handlers(
        self, event_type: str, *, enabled_only: bool = True
    ) -> list[RegisteredComponent]:
        """获取特定事件类型的所有 event_handler，按 weight 降序排列。"""
        handlers = []
        for comp in self._by_type.get("event_handler", {}).values():
            if enabled_only and not comp.enabled:
                continue
            if comp.metadata.get("event_type") == event_type:
                handlers.append(comp)
        handlers.sort(key=lambda c: c.metadata.get("weight", 0), reverse=True)
        return handlers

    def get_workflow_steps(
        self, stage: str, *, enabled_only: bool = True
    ) -> list[RegisteredComponent]:
        """获取特定 workflow 阶段的所有步骤，按 priority 降序。"""
        steps = []
        for comp in self._by_type.get("workflow_step", {}).values():
            if enabled_only and not comp.enabled:
                continue
            if comp.metadata.get("stage") == stage:
                steps.append(comp)
        steps.sort(key=lambda c: c.metadata.get("priority", 0), reverse=True)
        return steps

    def get_tools_for_llm(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        """获取可供 LLM 使用的工具列表（openai function-calling 格式预览）。"""
        result = []
        for comp in self.get_components_by_type("tool", enabled_only=enabled_only):
            tool_def: dict[str, Any] = {
                "name": comp.full_name,
                "description": comp.metadata.get("description", ""),
            }
            # 从结构化参数或原始参数构建 parameters
            params = comp.metadata.get("parameters", [])
            params_raw = comp.metadata.get("parameters_raw", {})
            if params:
                tool_def["parameters"] = params
            elif params_raw:
                tool_def["parameters"] = params_raw
            result.append(tool_def)
        return result

    # ──── 统计 ─────────────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        """获取注册统计。"""
        stats: dict[str, int] = {"total": len(self._components)}
        for comp_type, type_dict in self._by_type.items():
            stats[comp_type] = len(type_dict)
        stats["plugins"] = len(self._by_plugin)
        return stats
