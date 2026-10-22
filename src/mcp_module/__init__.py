"""MCP (Model Context Protocol) 客户端包。

业务运行时应通过 ``src.mcp_module.service.get_mcp_service`` 复用进程级连接；
``MCPManager`` 仅用于连接管理实现和一次性诊断场景。
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager import MCPManager

__all__ = ["MCPManager"]


def __getattr__(name: str) -> Any:
    """保留 Manager 导出，但导入配置或 Provider 时不提前加载 MCP SDK。"""
    if name == "MCPManager":
        from .manager import MCPManager

        globals()[name] = MCPManager
        return MCPManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
