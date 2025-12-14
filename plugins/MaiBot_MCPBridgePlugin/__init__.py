"""
MCP 桥接插件
将 MCP (Model Context Protocol) 服务器的工具桥接到 MaiBot

v1.1.0 新增功能:
- 心跳检测和自动重连
- 调用统计（次数、成功率、耗时）
- 更好的错误处理

v1.2.0 新增功能:
- Resources 支持（资源读取）
- Prompts 支持（提示模板）
"""

from .plugin import MCPBridgePlugin, mcp_tool_registry, MCPStartupHandler, MCPStopHandler
from .mcp_client import (
    mcp_manager,
    MCPClientManager,
    MCPServerConfig,
    TransportType,
    MCPCallResult,
    MCPToolInfo,
    MCPResourceInfo,
    MCPPromptInfo,
    ToolCallStats,
    ServerStats,
)

__all__ = [
    "MCPBridgePlugin",
    "mcp_tool_registry",
    "mcp_manager",
    "MCPClientManager",
    "MCPServerConfig",
    "TransportType",
    "MCPCallResult",
    "MCPToolInfo",
    "MCPResourceInfo",
    "MCPPromptInfo",
    "ToolCallStats",
    "ServerStats",
    "MCPStartupHandler",
    "MCPStopHandler",
]
