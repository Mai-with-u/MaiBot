"""WebUI MCP 辅助接口测试。"""

from types import SimpleNamespace
from typing import Any

import pytest

from src.config.official_configs import MCPServerItemConfig
from src.webui.routers import mcp as mcp_router


@pytest.mark.asyncio
async def test_connection_endpoint_returns_tool_preview_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一次性连接测试应返回工具风险摘要并可靠释放连接。"""

    connections: list[Any] = []

    class FakeConnection:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.protocol_version = "2025-06-18"
            self.last_error = ""
            self.closed = False
            self.tools = [
                SimpleNamespace(
                    name="read_data",
                    title="读取数据",
                    description="读取远程数据",
                    annotations=SimpleNamespace(
                        title="",
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                        openWorldHint=True,
                        audience=None,
                        priority=None,
                        meta=None,
                    ),
                )
            ]
            connections.append(self)

        async def connect(self) -> bool:
            return True

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(mcp_router, "MCPConnection", FakeConnection)

    response = await mcp_router.test_mcp_connection(
        MCPServerItemConfig(name="local", command="server")
    )

    assert response.success is True
    assert response.protocol_version == "2025-06-18"
    assert response.tools[0].name == "read_data"
    assert response.tools[0].read_only is True
    assert response.tools[0].destructive is False
    assert connections[0].closed is True
