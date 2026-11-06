"""验证核心鉴权覆盖公开命令、工具发现和执行，并拒绝模型参数中的伪造身份。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.config.official_configs import PluginUsagePermissionConfig
from src.core.plugin_usage import has_plugin_usage_permission
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolInvocation
from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import ComponentRegistry


@pytest.fixture
def runtime(monkeypatch):
    from src.config import config
    from src.files import service as files

    rules = {"test": PluginUsagePermissionConfig(enabled=True, allow_users=["QQ:Owner"], allow_chats=["allowed"])}
    monkeypatch.setattr(config, "global_config", SimpleNamespace(plugin=SimpleNamespace(usage_permissions=rules)))
    monkeypatch.setattr(files, "get_file_service", lambda: SimpleNamespace(enabled=False, revoke=Mock()))
    registry = ComponentRegistry()
    registry.register_component("tool", "TOOL", "test", {})
    supervisor = SimpleNamespace(
        component_registry=registry,
        invoke_plugin=AsyncMock(return_value=SimpleNamespace(payload={"success": True, "result": {"success": True}})),
    )
    query = ComponentQueryService()
    monkeypatch.setattr(query, "_iter_supervisors", lambda: [supervisor])
    return query, supervisor, rules


@pytest.mark.parametrize("platform,user,session,expected", [
    ("qq", "Owner", "other", True),
    ("QQ", "Owner", "other", True),
    ("qq", "owner", "other", False),
    ("telegram", "Owner", "other", False),
    ("qq", "visitor", "allowed", True),
    ("qq", "visitor", "other", False),
    ("", "", "", False),
])
def test_user_or_chat_scope(runtime, platform, user, session, expected):
    _, _, rules = runtime
    assert has_plugin_usage_permission(
        "test", rules, platform=platform, user_id=user, session_id=session,
    ) is expected


def test_default_and_empty_allowlist(runtime):
    _, _, rules = runtime
    assert has_plugin_usage_permission("unconfigured", rules)
    rules["test"].enabled = False
    assert has_plugin_usage_permission("test", rules)
    rules["test"] = PluginUsagePermissionConfig(enabled=True)
    assert not has_plugin_usage_permission("test", rules, platform="qq", user_id="Owner", session_id="allowed")


@pytest.mark.asyncio
@pytest.mark.parametrize("session,user,is_group,expected", [
    ("other", "Owner", False, True),
    ("other", "Owner", True, False),
    ("other", "Owner", None, False),
    ("allowed", "visitor", True, True),
    ("other", "visitor", False, False),
])
async def test_tool_discovery_and_execution(runtime, session, user, is_group, expected):
    query, supervisor, _ = runtime
    identity = dict(session_id=session, platform="qq", user_id=user, is_group_chat=is_group)
    assert ("tool" in query.get_llm_available_tool_specs(ToolAvailabilityContext(**identity))) is expected
    result = await query.invoke_tool_as_tool(ToolInvocation("tool"), ToolExecutionContext(**identity))
    assert result.success is expected
    assert supervisor.invoke_plugin.await_count == int(expected)


@pytest.mark.asyncio
async def test_missing_context_and_forged_arguments_do_not_authorize(runtime):
    query, supervisor, _ = runtime
    assert not query.get_llm_available_tool_specs()
    forged = {"session_id": "allowed", "stream_id": "allowed", "platform": "qq", "user_id": "Owner"}
    result = await query.invoke_tool_as_tool(ToolInvocation("tool", forged))
    assert not result.success
    executor = query.get_tool_executor("tool")
    assert executor is not None
    assert (await executor(forged))["success"] is False
    supervisor.invoke_plugin.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_changes_apply_to_existing_executor(runtime):
    query, supervisor, rules = runtime
    rules["test"].enabled = False
    executor = query.get_tool_executor("tool")
    assert executor is not None
    await executor({})
    supervisor.invoke_plugin.assert_awaited_once()
    rules["test"].enabled = True
    assert (await executor({}))["success"] is False
    supervisor.invoke_plugin.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("user,session,expected", [("Owner", "other", True), ("visitor", "allowed", True), ("visitor", "other", False)])
async def test_public_command_executor_enforces_plugin_scope(runtime, user, session, expected):
    query, supervisor, _ = runtime
    executor = query._build_command_executor(supervisor, "test", "public", {"permission": "public"})
    # 模拟核心消息，不依赖插件声明权限。拒绝路径必须在序列化和 RPC 之前结束。
    message = SimpleNamespace(
        platform="qq", session_id=session,
        message_info=SimpleNamespace(user_info=SimpleNamespace(user_id=user), additional_config={}),
    )
    if expected:
        # 允许路径由真实消息序列化负责；这里只断言权限检查已经放行至该边界。
        from unittest.mock import patch
        with patch("src.plugin_runtime.component_query.PluginMessageUtils._session_message_to_dict", return_value={}):
            result = await executor(message=message)
    else:
        result = await executor(message=message)
    assert result[0] is expected
    assert supervisor.invoke_plugin.await_count == int(expected)
