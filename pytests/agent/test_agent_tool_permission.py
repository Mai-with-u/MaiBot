"""外接 Agent 工具的白名单鉴权。"""

from types import SimpleNamespace

import asyncio

from src.agent.config import AgentConfig
import src.agent.provider as provider_module
from src.agent.provider import AgentToolProvider
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolInvocation, ToolExecutionResult

AGENT_ID = "agent-session"


def build_provider(**kwargs) -> AgentToolProvider:
    service = SimpleNamespace(config=AgentConfig(enabled=True, **kwargs), agent_id=AGENT_ID)
    return AgentToolProvider(service)


def build_context(*, session_id: str = "chat-1", platform: str = "qq", group_id: str = "", user_id: str = "") -> ToolAvailabilityContext:
    return ToolAvailabilityContext(
        session_id=session_id, stream_id=session_id, is_group_chat=bool(group_id),
        group_id=group_id, user_id=user_id, platform=platform,
    )


def list_tool_names(provider: AgentToolProvider, context: ToolAvailabilityContext):
    async def scenario():
        return [spec.name for spec in await provider.list_tools(context)]

    return asyncio.run(scenario())


def test_empty_allowlist_keeps_all_chats_open():
    provider = build_provider()
    assert "agent_talk" in list_tool_names(provider, build_context(user_id="111"))
    assert "agent_talk" in list_tool_names(provider, build_context(group_id="222"))


def test_allow_users_limits_private_chat():
    provider = build_provider(allow_users=["qq:111"])
    assert "agent_talk" in list_tool_names(provider, build_context(user_id="111"))
    assert list_tool_names(provider, build_context(user_id="999")) == []


def test_allow_users_closes_groups_until_group_allowed():
    provider = build_provider(allow_users=["qq:111"])
    assert list_tool_names(provider, build_context(group_id="222")) == []


def test_allow_groups_limits_group_chat():
    provider = build_provider(allow_groups=["qq:222"])
    assert "agent_talk" in list_tool_names(provider, build_context(group_id="222"))
    assert list_tool_names(provider, build_context(group_id="333")) == []


def test_agent_own_session_stays_trusted():
    provider = build_provider(allow_users=["qq:111"])
    names = set(list_tool_names(provider, build_context(session_id=AGENT_ID)))
    assert {"agent_talk", "agent_status", "agent_chats", "agent_recent"} <= names


def test_invoke_rejects_unauthorized_chat():
    provider = build_provider(allow_users=["qq:111"])
    context = ToolExecutionContext(session_id="chat-1", stream_id="chat-1", is_group_chat=False,
                                   user_id="999", platform="qq")

    async def scenario():
        return await provider.invoke(ToolInvocation("agent_talk", {"text": "hi"}), context)

    result = asyncio.run(scenario())
    assert result.success is False
    assert "权限" in (result.error_message or "")


def test_blank_entries_fail_closed():
    provider = build_provider(allow_users=["   "])
    assert list_tool_names(provider, build_context(user_id="111")) == []


def test_invoke_allows_whitelisted_chat(monkeypatch):
    provider = build_provider(allow_users=["qq:111"])
    provider.service.require_agent = lambda: None
    calls = {}

    async def fake_tell(service, caller, text, target="", images=None):
        calls.update(caller=caller, text=text, target=target)
        return {"delivered": True}

    monkeypatch.setattr(provider_module.messaging, "tell", fake_tell)
    context = ToolExecutionContext(session_id="chat-1", stream_id="chat-1", is_group_chat=False,
                                   user_id="111", platform="qq")

    async def scenario():
        return await provider.invoke(ToolInvocation("agent_talk", {"text": "hi"}), context)

    result = asyncio.run(scenario())
    assert isinstance(result, ToolExecutionResult)
    assert result.success is True
    assert calls == {"caller": "chat-1", "text": "hi", "target": ""}
