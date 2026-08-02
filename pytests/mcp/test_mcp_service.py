"""进程级 MCP 共享服务回归测试。"""

from typing import Any

import pytest

from src.config.official_configs import MCPConfig, MCPServerItemConfig
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.mcp_module.manager import MCPManager
from src.mcp_module.service import MCPService


class _FakeManager:
    """提供共享服务测试所需的最小管理器接口。"""

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


@pytest.mark.asyncio
async def test_service_reuses_unchanged_manager_and_preserves_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相同配置只建一次连接，工具结果仍携带当前聊天上下文。"""

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
    config = MCPConfig(servers=[MCPServerItemConfig(name="local", command="server")])

    await service.reload(config)
    await service.reload(config.model_copy(deep=True))
    await service.reload(
        MCPConfig(
            servers=[
                MCPServerItemConfig(name="local", command="server"),
                MCPServerItemConfig(enabled=False, name="draft", command="not-used"),
            ]
        )
    )

    tools = await service.list_tools()
    result = await service.call_tool_invocation(
        ToolInvocation(tool_name="local_tool"),
        ToolExecutionContext(session_id="session-1", stream_id="stream-1"),
    )

    assert len(created_managers) == 1
    assert [tool.name for tool in tools] == ["local_tool"]
    assert result.metadata["chat_session_id"] == "session-1"
    assert result.metadata["chat_stream_id"] == "stream-1"
    assert service.get_status_snapshot()["server_count"] == 1

    await service.close()
    assert created_managers[0].close_count == 1
    assert service.get_status_snapshot()["initialized"] is False


@pytest.mark.asyncio
async def test_service_closes_old_manager_after_configuration_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置变化后应原子替换并释放旧连接。"""

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

    await service.reload(MCPConfig(servers=[MCPServerItemConfig(name="first", command="server")]))
    await service.reload(MCPConfig(servers=[MCPServerItemConfig(name="second", command="server")]))

    assert len(created_managers) == 2
    assert created_managers[0].close_count == 1
    assert service.get_status_snapshot()["servers"][0]["name"] == "second"

    await service.close()
    assert created_managers[1].close_count == 1
