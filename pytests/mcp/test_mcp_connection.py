"""MCP 连接超时配置与工具调用错误信息回归测试。"""

from typing import Any

import anyio
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


@pytest.mark.asyncio
async def test_call_tool_error_message_names_exception_without_text() -> None:
    """底层流已关闭等没有消息文本的异常，工具错误信息应给出异常类名而不是空白。"""

    class ClosedStreamSession:
        """模拟底层流已关闭的会话。"""

        async def call_tool(self, *args: Any, **kwargs: Any) -> Any:
            raise anyio.ClosedResourceError

    connection = MCPConnection(
        MCPServerRuntimeConfig(name="local", command="unused"),
        MCPClientRuntimeConfig(),
    )
    connection.session = ClosedStreamSession()

    result = await connection.call_tool("echo", {"text": "ping"})

    assert result.success is False
    assert result.error_message == "MCP 工具 'echo' 执行失败: ClosedResourceError"
