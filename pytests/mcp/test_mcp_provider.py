"""MCP 会话 ToolProvider 适配器回归测试。"""

from typing import Any

import pytest

from src.config.official_configs import MCPConfig, MCPServerItemConfig
from src.core.tooling import (
    ToolAvailabilityContext,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.mcp_module.manager import MCPManager
from src.mcp_module.provider import MCPToolProvider
from src.mcp_module.service import MCPService


class _FakeManager:
    """提供适配器测试所需的最小管理器接口。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.server_count = 1
        self.tool_count = 1
        self.close_count = 0

    def get_tool_specs(self) -> list[ToolSpec]:
        return [ToolSpec(name=f"{self.name}_tool")]

    def get_status_snapshot(self) -> dict[str, Any]:
        return {
            "initialized": True,
            "server_count": 1,
            "tool_count": 1,
            "servers": [
                {
                    "name": self.name,
                    "transport": "stdio",
                    "connected": True,
                    "protocol_version": "2025-06-18",
                    "tool_count": 1,
                    "error": "",
                }
            ],
        }

    async def call_tool_invocation(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            content="ok",
        )

    async def close(self) -> None:
        self.close_count += 1


async def _reload_service_with_fake_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MCPService, list[_FakeManager]]:
    created_managers: list[_FakeManager] = []

    async def fake_from_app_config(
        cls: type[MCPManager],
        mcp_config: MCPConfig,
        *_args: Any,
        **_kwargs: Any,
    ) -> _FakeManager:
        del cls
        manager = _FakeManager(mcp_config.servers[0].name)
        created_managers.append(manager)
        return manager

    monkeypatch.setattr(MCPManager, "from_app_config", classmethod(fake_from_app_config))
    service = MCPService()
    await service.reload(MCPConfig(servers=[MCPServerItemConfig(name="local", command="server")]))
    return service, created_managers


@pytest.mark.asyncio
async def test_provider_lists_shared_tools_and_preserves_chat_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """适配器列出共享工具，invoke 结果仍携带聊天元数据。"""

    service, created_managers = await _reload_service_with_fake_manager(monkeypatch)
    provider = MCPToolProvider(service)

    tools = await provider.list_tools(ToolAvailabilityContext(session_id="session-1"))
    result = await provider.invoke(
        ToolInvocation(tool_name="local_tool"),
        ToolExecutionContext(session_id="session-1", stream_id="stream-1"),
    )

    assert [tool.name for tool in tools] == ["local_tool"]
    assert result.success is True
    assert result.content == "ok"
    assert result.metadata["chat_session_id"] == "session-1"
    assert result.metadata["chat_stream_id"] == "stream-1"
    assert created_managers[0].close_count == 0

    await service.close()


@pytest.mark.asyncio
async def test_provider_invoke_without_manager_keeps_original_metadata() -> None:
    """未连接服务器时，invoke 失败结果仍不写入聊天元数据。"""

    provider = MCPToolProvider(MCPService())
    result = await provider.invoke(
        ToolInvocation(tool_name="missing"),
        ToolExecutionContext(session_id="session-1", stream_id="stream-1"),
    )

    assert result.success is False
    assert "chat_session_id" not in result.metadata
    assert "chat_stream_id" not in result.metadata


@pytest.mark.asyncio
async def test_provider_close_does_not_release_shared_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话 Provider.close 不得关闭进程级 MCP 连接。"""

    service, created_managers = await _reload_service_with_fake_manager(monkeypatch)
    provider = MCPToolProvider(service)

    await provider.close()
    tools = await provider.list_tools()
    result = await provider.invoke(ToolInvocation(tool_name="local_tool"))

    assert [tool.name for tool in tools] == ["local_tool"]
    assert result.success is True
    assert "chat_session_id" not in result.metadata
    assert "chat_stream_id" not in result.metadata
    assert created_managers[0].close_count == 0

    await service.close()
    assert created_managers[0].close_count == 1
