"""MCP (Model Context Protocol) 客户端包。

业务运行时应通过 ``src.mcp_module.service.get_mcp_service`` 复用进程级连接；
``MCPManager`` 仅用于连接管理实现和一次性诊断场景。
"""

from .manager import MCPManager

__all__ = ["MCPManager"]
