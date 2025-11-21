"""
工具注册系统
提供统一的工具注册和管理接口
"""

from typing import List, Dict, Any, Optional, Callable, Awaitable
from src.common.logger import get_logger
from src.llm_models.payload_content.tool_option import ToolParamType

logger = get_logger("memory_retrieval_tools")


class MemoryRetrievalTool:
    """记忆检索工具基类"""

    def __init__(
        self, name: str, description: str, parameters: List[Dict[str, Any]], execute_func: Callable[..., Awaitable[str]]
    ):
        """
        初始化工具

        Args:
            name: 工具名称
            description: 工具描述
            parameters: 参数定义列表，格式：[{"name": "param_name", "type": "string", "description": "参数描述", "required": True}]
            execute_func: 执行函数，必须是异步函数
        """
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute_func = execute_func

    def get_tool_description(self) -> str:
        """获取工具的文本描述，用于prompt"""
        param_descriptions = []
        for param in self.parameters:
            param_name = param.get("name", "")
            param_type = param.get("type", "string")
            param_desc = param.get("description", "")
            required = param.get("required", True)
            required_str = "必填" if required else "可选"
            param_descriptions.append(f"   - {param_name} ({param_type}, {required_str}): {param_desc}")

        params_str = "\n".join(param_descriptions) if param_descriptions else "   无参数"
        return f"{self.name}({', '.join([p['name'] for p in self.parameters])}): {self.description}\n{params_str}"

    async def execute(self, **kwargs) -> str:
        """执行工具"""
        return await self.execute_func(**kwargs)

    def get_tool_definition(self) -> Dict[str, Any]:
        """获取工具定义，用于LLM function calling

        Returns:
            Dict[str, Any]: 工具定义字典，格式与BaseTool一致
            格式: {"name": str, "description": str, "parameters": List[Tuple]}
        """
        # 转换参数格式为元组列表，格式与BaseTool一致
        # 格式: [("param_name", ToolParamType, "description", required, enum_values)]
        param_tuples = []

        for param in self.parameters:
            param_name = param.get("name", "")
            param_type_str = param.get("type", "string").lower()
            param_desc = param.get("description", "")
            is_required = param.get("required", False)
            enum_values = param.get("enum", None)

            # 转换类型字符串到ToolParamType
            type_mapping = {
                "string": ToolParamType.STRING,
                "integer": ToolParamType.INTEGER,
                "int": ToolParamType.INTEGER,
                "float": ToolParamType.FLOAT,
                "boolean": ToolParamType.BOOLEAN,
                "bool": ToolParamType.BOOLEAN,
            }
            param_type = type_mapping.get(param_type_str, ToolParamType.STRING)

            # 构建参数元组
            param_tuple = (param_name, param_type, param_desc, is_required, enum_values)
            param_tuples.append(param_tuple)

        # 构建工具定义，格式与BaseTool.get_tool_definition()一致
        tool_def = {"name": self.name, "description": self.description, "parameters": param_tuples}

        return tool_def


class MemoryRetrievalToolRegistry:
    """工具注册器"""

    def __init__(self):
        self.tools: Dict[str, MemoryRetrievalTool] = {}

    def register_tool(self, tool: MemoryRetrievalTool) -> None:
        """注册工具"""
        if tool.name in self.tools:
            logger.debug(f"记忆检索工具 {tool.name} 已存在，跳过重复注册")
            return
        self.tools[tool.name] = tool
        logger.info(f"注册记忆检索工具: {tool.name}")

    def get_tool(self, name: str) -> Optional[MemoryRetrievalTool]:
        """获取工具"""
        return self.tools.get(name)

    def get_all_tools(self) -> Dict[str, MemoryRetrievalTool]:
        """获取所有工具"""
        return self.tools.copy()

    def get_tools_description(self) -> str:
        """获取所有工具的描述，用于prompt"""
        descriptions = []
        for i, tool in enumerate(self.tools.values(), 1):
            descriptions.append(f"{i}. {tool.get_tool_description()}")
        return "\n".join(descriptions)

    def get_action_types_list(self) -> str:
        """获取所有动作类型的列表，用于prompt（已废弃，保留用于兼容）"""
        action_types = [tool.name for tool in self.tools.values()]
        action_types.append("final_answer")
        action_types.append("no_answer")
        return " 或 ".join([f'"{at}"' for at in action_types])

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """获取所有工具的定义列表，用于LLM function calling

        Returns:
            List[Dict[str, Any]]: 工具定义列表，每个元素是一个工具定义字典
        """
        return [tool.get_tool_definition() for tool in self.tools.values()]


# 全局工具注册器实例
_tool_registry = MemoryRetrievalToolRegistry()


def register_memory_retrieval_tool(
    name: str, description: str, parameters: List[Dict[str, Any]], execute_func: Callable[..., Awaitable[str]]
) -> None:
    """注册记忆检索工具的便捷函数

    Args:
        name: 工具名称
        description: 工具描述
        parameters: 参数定义列表
        execute_func: 执行函数
    """
    tool = MemoryRetrievalTool(name, description, parameters, execute_func)
    _tool_registry.register_tool(tool)


def get_tool_registry() -> MemoryRetrievalToolRegistry:
    """获取工具注册器实例"""
    return _tool_registry
