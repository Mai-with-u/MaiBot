"""
核心组件注册表

面向最终架构的组件管理：
- Action：注册 ActionInfo + 执行器（本地 callable 或 IPC 路由）
- Command：注册正则模式 + 执行器
- Tool：注册工具定义 + 执行器

不依赖任何插件基类，组件执行器是纯 async callable。
"""

import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Pattern, Tuple, Union

from src.common.logger import get_logger
from src.core.types import (
    ActionActivationType,
    ActionInfo,
    CommandInfo,
    ComponentInfo,
    ComponentType,
    ToolInfo,
)

logger = get_logger("component_registry")

# 执行器类型
ActionExecutor = Callable[..., Awaitable[Any]]
CommandExecutor = Callable[..., Awaitable[Tuple[bool, Optional[str], bool]]]
ToolExecutor = Callable[..., Awaitable[Any]]


class ComponentRegistry:
    """核心组件注册表

    管理 action、command、tool 三类组件。
    每个组件由「元信息 + 执行器」构成，执行器是 async callable，
    不需要继承任何基类。
    """

    def __init__(self):
        # Action 注册
        self._actions: Dict[str, ActionInfo] = {}
        self._action_executors: Dict[str, ActionExecutor] = {}
        self._default_actions: Dict[str, ActionInfo] = {}

        # Command 注册
        self._commands: Dict[str, CommandInfo] = {}
        self._command_executors: Dict[str, CommandExecutor] = {}
        self._command_patterns: Dict[Pattern, str] = {}

        # Tool 注册
        self._tools: Dict[str, ToolInfo] = {}
        self._tool_executors: Dict[str, ToolExecutor] = {}
        self._llm_available_tools: Dict[str, ToolInfo] = {}

        # 插件配置（plugin_name -> config dict）
        self._plugin_configs: Dict[str, dict] = {}

        logger.info("核心组件注册表初始化完成")

    # ========== Action ==========

    def register_action(
        self,
        info: ActionInfo,
        executor: ActionExecutor,
    ) -> bool:
        """注册 action

        Args:
            info: action 元信息
            executor: 执行器，async callable
        """
        name = info.name
        if name in self._actions:
            logger.warning(f"Action {name} 已存在，跳过注册")
            return False

        self._actions[name] = info
        self._action_executors[name] = executor

        if info.enabled:
            self._default_actions[name] = info

        logger.debug(f"注册 Action: {name}")
        return True

    def get_action_info(self, name: str) -> Optional[ActionInfo]:
        return self._actions.get(name)

    def get_action_executor(self, name: str) -> Optional[ActionExecutor]:
        return self._action_executors.get(name)

    def get_default_actions(self) -> Dict[str, ActionInfo]:
        return self._default_actions.copy()

    def get_all_actions(self) -> Dict[str, ActionInfo]:
        return self._actions.copy()

    def remove_action(self, name: str) -> bool:
        if name not in self._actions:
            return False
        del self._actions[name]
        self._action_executors.pop(name, None)
        self._default_actions.pop(name, None)
        logger.debug(f"移除 Action: {name}")
        return True

    # ========== Command ==========

    def register_command(
        self,
        info: CommandInfo,
        executor: CommandExecutor,
    ) -> bool:
        """注册 command"""
        name = info.name
        if name in self._commands:
            logger.warning(f"Command {name} 已存在，跳过注册")
            return False

        self._commands[name] = info
        self._command_executors[name] = executor

        if info.enabled and info.command_pattern:
            pattern = re.compile(info.command_pattern, re.IGNORECASE | re.DOTALL)
            self._command_patterns[pattern] = name

        logger.debug(f"注册 Command: {name}")
        return True

    def find_command_by_text(
        self, text: str
    ) -> Optional[Tuple[CommandExecutor, dict, CommandInfo]]:
        """根据文本查找匹配的命令

        Returns:
            (executor, matched_groups, command_info) 或 None
        """
        candidates = [p for p in self._command_patterns if p.match(text)]
        if not candidates:
            return None
        if len(candidates) > 1:
            logger.warning(f"文本 '{text[:50]}' 匹配到多个命令模式，使用第一个")
        pattern = candidates[0]
        name = self._command_patterns[pattern]
        return (
            self._command_executors[name],
            pattern.match(text).groupdict(),  # type: ignore
            self._commands[name],
        )

    def remove_command(self, name: str) -> bool:
        if name not in self._commands:
            return False
        del self._commands[name]
        self._command_executors.pop(name, None)
        self._command_patterns = {k: v for k, v in self._command_patterns.items() if v != name}
        logger.debug(f"移除 Command: {name}")
        return True

    # ========== Tool ==========

    def register_tool(
        self,
        info: ToolInfo,
        executor: ToolExecutor,
    ) -> bool:
        """注册 tool"""
        name = info.name
        if name in self._tools:
            logger.warning(f"Tool {name} 已存在，跳过注册")
            return False

        self._tools[name] = info
        self._tool_executors[name] = executor

        if info.enabled:
            self._llm_available_tools[name] = info

        logger.debug(f"注册 Tool: {name}")
        return True

    def get_tool_info(self, name: str) -> Optional[ToolInfo]:
        return self._tools.get(name)

    def get_tool_executor(self, name: str) -> Optional[ToolExecutor]:
        return self._tool_executors.get(name)

    def get_llm_available_tools(self) -> Dict[str, ToolInfo]:
        return self._llm_available_tools.copy()

    def get_all_tools(self) -> Dict[str, ToolInfo]:
        return self._tools.copy()

    def remove_tool(self, name: str) -> bool:
        if name not in self._tools:
            return False
        del self._tools[name]
        self._tool_executors.pop(name, None)
        self._llm_available_tools.pop(name, None)
        logger.debug(f"移除 Tool: {name}")
        return True

    # ========== 通用查询 ==========

    def get_component_info(self, name: str, component_type: ComponentType) -> Optional[ComponentInfo]:
        """获取组件元信息"""
        match component_type:
            case ComponentType.ACTION:
                return self._actions.get(name)
            case ComponentType.COMMAND:
                return self._commands.get(name)
            case ComponentType.TOOL:
                return self._tools.get(name)
            case _:
                return None

    def get_components_by_type(self, component_type: ComponentType) -> Dict[str, ComponentInfo]:
        """获取某类型的所有组件"""
        match component_type:
            case ComponentType.ACTION:
                return dict(self._actions)
            case ComponentType.COMMAND:
                return dict(self._commands)
            case ComponentType.TOOL:
                return dict(self._tools)
            case _:
                return {}

    # ========== 插件配置 ==========

    def set_plugin_config(self, plugin_name: str, config: dict) -> None:
        self._plugin_configs[plugin_name] = config

    def get_plugin_config(self, plugin_name: str) -> Optional[dict]:
        return self._plugin_configs.get(plugin_name)


# 全局单例
component_registry = ComponentRegistry()
