"""外接 Agent 工具的聊天白名单。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest

from src.agent.config import AgentConfig
from src.agent.provider import AgentToolProvider
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolInvocation


@pytest.mark.parametrize(
    ("users", "groups", "context", "allowed"),
    [
        ([], [], ToolAvailabilityContext(), True),
        ([" QQ:UserA "], [], ToolAvailabilityContext(platform="qq", user_id="UserA"), True),
        (["qq:UserA"], [], ToolAvailabilityContext(platform="qq", user_id="usera"), False),
        (["qq:123"], [], ToolAvailabilityContext(platform="qq", user_id="123", is_group_chat=True), False),
        ([], ["qq:456"], ToolAvailabilityContext(platform="qq", group_id="456", is_group_chat=True), True),
        ([], ["qq:456"], ToolAvailabilityContext(platform="qq", group_id="789", is_group_chat=True), False),
        (["  "], [], ToolAvailabilityContext(platform="qq", user_id="123"), False),
        (["qq:123"], [], None, False),
    ],
)
def test_tool_visibility(users, groups, context, allowed):
    service = SimpleNamespace(config=AgentConfig(enabled=True, allow_users=users, allow_groups=groups), agent_id="agent")
    tools = asyncio.run(AgentToolProvider(service).list_tools(context))
    assert {tool.name for tool in tools} == ({"agent_talk", "agent_status"} if allowed else set())


def test_agent_session_remains_trusted():
    service = SimpleNamespace(config=AgentConfig(enabled=True, allow_users=["qq:123"]), agent_id="agent")
    tools = asyncio.run(AgentToolProvider(service).list_tools(ToolAvailabilityContext(session_id="agent")))
    assert {tool.name for tool in tools} == {"agent_talk", "agent_status", "agent_chats", "agent_recent"}


@pytest.mark.parametrize("user_id,allowed", [("123", True), ("999", False)])
def test_direct_invocation_checks_whitelist(monkeypatch, user_id, allowed):
    service = SimpleNamespace(config=AgentConfig(enabled=True, allow_users=["qq:123"]), agent_id="agent", require_agent=lambda: None)
    tell = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("src.agent.provider.messaging.tell", tell)
    context = ToolExecutionContext(session_id="chat", platform="qq", user_id=user_id, is_group_chat=False)
    result = asyncio.run(AgentToolProvider(service).invoke(ToolInvocation("agent_talk", {"text": "hi"}), context))
    assert result.success is allowed
    assert tell.await_count == int(allowed)
    if not allowed:
        assert "权限" in result.error_message
