"""插件运行时工具 Provider。"""

from __future__ import annotations

from typing import Optional

from src.core.tooling import (
    ToolAvailabilityContext,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolInvocation,
    ToolProvider,
    ToolSpec,
)

from .component_query import component_query_service


class PluginToolProvider(ToolProvider):
    """将插件 Tool 暴露为统一工具 Provider。"""

    provider_name = "plugin_runtime"
    provider_type = "plugin"

    async def list_tools(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> list[ToolSpec]:
        """列出插件运行时当前可用的工具声明。"""

        return list(component_query_service.get_llm_available_tool_specs(context=context).values())

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        """执行插件工具 的工具调用。

        Args:
            invocation: 工具调用请求。
            context: 执行上下文。

        Returns:
            ToolExecutionResult: 工具执行结果。
        """

        return await component_query_service.invoke_tool_as_tool(
            invocation=invocation,
            context=context,
        )

    async def close(self) -> None:
        """关闭 Provider。

        插件运行时工具 Provider 不持有独立资源。
        """

