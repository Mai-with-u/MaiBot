"""MCP 连接超时配置回归测试。"""

import pytest

from src.mcp_module.config import MCPClientRuntimeConfig, MCPServerRuntimeConfig
from src.mcp_module.connection import MCPConnection


@pytest.mark.asyncio
async def test_http_client_uses_session_read_timeout_for_response_body() -> None:
    """长耗时工具的 HTTP 读取应使用会话读取超时，而非连接超时。"""

    connection = MCPConnection(
        MCPServerRuntimeConfig(
            name="remote",
            transport="streamable_http",
            url="https://example.test/mcp",
            http_timeout_seconds=30.0,
            read_timeout_seconds=300.0,
        ),
        MCPClientRuntimeConfig(),
    )
    client = connection._build_http_client()

    try:
        assert client.timeout.connect == 30.0
        assert client.timeout.read == 300.0
        assert client.timeout.write == 30.0
        assert client.timeout.pool == 30.0
    finally:
        await client.aclose()
