"""将会话 ToolProvider 适配到进程级 MCPService。"""

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

from .service import MCPService


class MCPToolProvider(ToolProvider):
    """会话侧 MCP 工具适配器。

    MCPService.close 会释放进程级连接，不能注册进会话 ToolRegistry。
    list_tools/invoke 适配到共享服务；close 不关闭 MCPService。
    """

    provider_name = "mcp"
    provider_type = "mcp"

    def __init__(self, service: MCPService) -> None:
        self._service = service

    async def list_tools(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> list[ToolSpec]:
        del context  # MCP 工具进程级共享，不按会话过滤。
        return await self._service.list_tools()

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        return await self._service.call_tool_invocation(invocation, context)

    async def close(self) -> None:
        """会话侧不得关闭进程级 MCP 连接。"""
