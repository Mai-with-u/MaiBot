"""验证工具定义只在历史末尾加载，搜索不改变后续请求的顶层声明。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import json

import pytest

from src.config.model_configs import ModelInfo
from src.config.config import global_config
from src.core.tooling import ToolExecutionResult, ToolInvocation, ToolSpec
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.model_client.openai_client import _convert_messages
from src.llm_models.model_client.openai_responses_client import _convert_context_items
from src.llm_models.payload_content.context_item import (
    PROVIDER_REPLAY_SCHEMA_VERSION,
    ContextItemMeta,
    ContextToolCall,
    FunctionCallItem,
    ProviderReplayFragment,
    ProviderScope,
    build_provider_endpoint_fingerprint,
)
from src.llm_models.payload_content.context_snapshot import deserialize_context_items_snapshot, serialize_context_items_snapshot
from src.llm_models.payload_content.tool_option import normalize_tool_options
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.builtin_tool.tool_search import get_tool_spec, handle_tool
from src.maisaka.context.messages import ModelOutputContextMessage, ToolResultMessage
from src.maisaka.focus.tools import FocusToolRouter
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting
from src.maisaka.tool_discovery import TOOL_LOADED_INSTRUCTION
from src.maisaka.tool_result_condenser import should_condense_tool_result


@pytest.fixture(autouse=True)
def tail_loading_enabled(monkeypatch):
    monkeypatch.setattr(global_config.experimental, "tool_search_tail_loading", True)


def plugin_spec():
    return ToolSpec(name="plugin_query", description="查询插件", provider_name="plugin", provider_type="plugin", parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})


def runtime(spec=None):
    chat = object.__new__(MaisakaHeartFlowChatting)
    chat.session_id = "real-session"
    chat.chat_stream = SimpleNamespace(is_group_session=False, group_id="", user_id="owner", platform="internal")
    chat._chat_history = []
    chat.deferred_tool_specs_by_name = {}
    chat.discovered_tool_names = set()
    chat._tool_search_tail_loading = True
    chat._current_action_tool_names = set()
    search = get_tool_spec()
    search.metadata.update(builtin_stage="action", visibility="visible")
    definitions = [search, spec or plugin_spec()]
    chat._tool_registry = SimpleNamespace(list_tools=AsyncMock(return_value=definitions))
    chat._chat_loop_service = SimpleNamespace(select_llm_context_messages=lambda history, **kwargs: (history, "test"))
    return chat


def append_search(chat, result):
    call = FunctionCallItem(meta=ContextItemMeta.create(logical_turn_id="turn"), tool_call=ContextToolCall(call_id="search-1", func_name="tool_search", args_json=b'{"query":"plugin_query"}'))
    message = ToolResultMessage(content=result.get_history_content(), timestamp=datetime.now(), tool_call_id="search-1", logical_turn_id="turn", tool_name="tool_search", success=result.success)
    chat._chat_history.extend([ModelOutputContextMessage(call), message])
    return call, message


@pytest.mark.asyncio
async def test_search_keeps_top_level_tools_and_directory_stable_across_rounds():
    chat = runtime()
    engine = MaisakaReasoningEngine(chat)
    engine._turn_passive_replyer = False
    before, directory = await engine._build_action_tool_definitions()
    assert [tool["name"] for tool in before] == ["tool_search"]
    assert "plugin_query" in directory
    result = await handle_tool(BuiltinToolRuntimeContext(engine, chat), ToolInvocation(tool_name="tool_search", arguments={"query": "plugin_query"}))
    append_search(chat, result)
    after, next_directory = await engine._build_action_tool_definitions()
    assert before == after
    assert directory == next_directory
    assert chat.is_action_tool_currently_available("plugin_query")
    payload = json.loads(result.get_history_content())
    assert payload["status"] == "loaded"
    assert payload["instruction"] == TOOL_LOADED_INSTRUCTION
    assert payload["tools"][0]["parameters"]["required"] == ["query"]
    assert "next_action" not in payload
    # 删除加载历史后执行状态随上下文清除，但顶层声明和目录仍保持稳定。
    chat._chat_history.clear()
    trimmed, trimmed_directory = await engine._build_action_tool_definitions()
    assert trimmed == before
    assert trimmed_directory == directory
    assert not chat.is_action_tool_currently_available("plugin_query")


@pytest.mark.asyncio
async def test_search_definition_is_frozen_and_restores_in_both_protocols():
    spec = plugin_spec()
    chat = runtime(spec)
    chat.update_deferred_tool_specs([spec])
    result = await handle_tool(BuiltinToolRuntimeContext(None, chat), ToolInvocation(tool_name="tool_search", arguments={"query": "plugin_query"}))
    call, message = append_search(chat, result)
    saved = serialize_context_items_snapshot([call, message.to_context_item()])
    spec.parameters_schema["properties"]["query"]["type"] = "integer"
    restored = deserialize_context_items_snapshot(saved)
    payload = json.loads(restored[-1].output)
    assert payload["tools"][0]["parameters"]["properties"]["query"]["type"] == "string"
    assert payload["status"] == "loaded"
    chat._chat_history = [ModelOutputContextMessage(restored[0]), ToolResultMessage(content=restored[1].output, timestamp=datetime.now(), tool_call_id="search-1", logical_turn_id="turn", tool_name="tool_search")]
    chat.sync_discovered_deferred_tools_with_context(chat._chat_history)
    assert chat.discovered_tool_names == {"plugin_query"}
    request = ResponseRequest(model_info=ModelInfo(name="model", model_type="llm", model_identifier="any-model", api_provider="provider"), context_items=restored, tool_options=normalize_tool_options([get_tool_spec().to_llm_definition()]))
    responses = _convert_context_items(restored, request, "provider", "https://example.test")
    assert responses[-1]["type"] == "function_call_output"
    assert json.loads(responses[-1]["output"]) == payload
    messages = _convert_messages(restored)
    assert messages[-1]["role"] == "tool"
    assert json.loads(messages[-1]["content"]) == payload


@pytest.mark.asyncio
async def test_empty_search_does_not_claim_tools_are_loaded():
    chat = runtime()
    result = await handle_tool(BuiltinToolRuntimeContext(None, chat), ToolInvocation(tool_name="tool_search", arguments={"query": "missing"}))
    assert result.success
    payload = json.loads(result.get_history_content())
    assert payload["status"] == "not_found"
    assert payload["tools"] == []
    assert payload["instruction"] != TOOL_LOADED_INSTRUCTION


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"query": ""}, {"query": "plugin_query", "limit": True}, {"query": "plugin_query", "limit": 0}, {"query": "plugin_query", "limit": "5"}])
async def test_invalid_search_arguments_fail_explicitly(arguments):
    result = await handle_tool(BuiltinToolRuntimeContext(None, runtime()), ToolInvocation(tool_name="tool_search", arguments=arguments))
    assert not result.success
    assert result.error_message


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_focus_search_routes_target_in_both_modes(monkeypatch, enabled):
    monkeypatch.setattr(global_config.experimental, "tool_search_tail_loading", enabled)
    spec = plugin_spec()
    chat = runtime(spec)
    chat.expand_message_id_aliases = lambda arguments: arguments
    originals = {tool.name: tool for tool in chat._tool_registry.list_tools.return_value}
    chat._tool_registry.get_tool_spec = AsyncMock(side_effect=lambda name, context: originals[name])

    async def invoke(call, context):
        if call.tool_name == "tool_search":
            return await handle_tool(BuiltinToolRuntimeContext(None, chat), call, context)
        return ToolExecutionResult(tool_name=call.tool_name, success=True, content="done")

    chat._tool_registry.invoke = AsyncMock(side_effect=invoke)
    group = SimpleNamespace(members={chat.session_id: chat}, is_member_valid=lambda member: member is chat, scope_id="scope")
    router = FocusToolRouter(group)
    await router.refresh()
    before = deepcopy(router.definitions())
    assert [tool["name"] for tool in before] == ["tool_search"]
    notice = router.deferred_notice()
    _, result, _, _ = await router.invoke("tool_search", {"query": "plugin_query", "session_id": chat.session_id}, "search", "")
    if enabled:
        schema = json.loads(result.get_history_content())["tools"][0]["parameters"]
        assert schema["properties"]["session_id"]["enum"] == [chat.session_id]
    else:
        assert "tools" not in result.structured_content
    await router.refresh()
    if enabled:
        assert router.definitions() == before
    else:
        assert [tool["name"] for tool in router.definitions()] == ["tool_search", "plugin_query"]
    assert router.deferred_notice() == notice
    invocation, executed, target, _ = await router.invoke("plugin_query", {"query": "games", "session_id": chat.session_id}, "call", "")
    assert executed.success and target is chat
    assert invocation.arguments == {"query": "games"}
    assert chat._tool_registry.invoke.call_args.args[0].arguments == {"query": "games"}
    count = chat._tool_registry.invoke.await_count
    _, rejected, _, _ = await router.invoke("plugin_query", {"query": "games", "session_id": "other-session"}, "bad", "")
    assert not rejected.success
    assert chat._tool_registry.invoke.await_count == count


def test_search_results_are_never_condensed():
    assert not should_condense_tool_result("tool_search", "x" * 100_000, success=True)


def test_old_native_search_history_is_only_replayed_as_plain_function():
    call = FunctionCallItem(meta=ContextItemMeta.create(logical_turn_id="turn"), tool_call=ContextToolCall(call_id="old-search", func_name="tool_search", args_json=b'{"query":"plugin_query"}'))
    scope = ProviderScope(schema_version=PROVIDER_REPLAY_SCHEMA_VERSION, client_type="openai_responses", provider_name="provider", endpoint_fingerprint=build_provider_endpoint_fingerprint("openai_responses", "https://example.test"), model_identifier="model")
    call = replace(call, replay=ProviderReplayFragment.from_payload(scope, {"type": "tool_search_call", "call_id": "old-search", "execution": "client", "arguments": {"query": "plugin_query"}}))
    request = ResponseRequest(model_info=ModelInfo(name="model", model_type="llm", model_identifier="model", api_provider="provider"), context_items=[call])
    wire = _convert_context_items([call], request, "provider", "https://example.test")
    assert wire == [{"type": "function_call", "call_id": "old-search", "name": "tool_search", "arguments": '{"query":"plugin_query"}'}]


@pytest.mark.asyncio
async def test_disabling_experiment_restores_legacy_activation(monkeypatch):
    monkeypatch.setattr(global_config.experimental, "tool_search_tail_loading", False)
    chat = runtime()
    engine = MaisakaReasoningEngine(chat)
    engine._turn_passive_replyer = False
    before, directory = await engine._build_action_tool_definitions()
    result = await handle_tool(BuiltinToolRuntimeContext(engine, chat), ToolInvocation(tool_name="tool_search", arguments={"query": "plugin_query"}))
    assert "status" not in result.structured_content
    assert "tools" not in result.structured_content
    assert "加入可用工具列表" in result.content
    append_search(chat, result)
    after, next_directory = await engine._build_action_tool_definitions()
    assert [tool["name"] for tool in before] == ["tool_search"]
    assert [tool["name"] for tool in after] == ["tool_search", "plugin_query"]
    assert "plugin_query" in directory and next_directory == ""


@pytest.mark.asyncio
async def test_experiment_switch_takes_effect_at_next_tool_decision(monkeypatch):
    chat = runtime()
    engine = MaisakaReasoningEngine(chat)
    engine._turn_passive_replyer = False
    await engine._build_action_tool_definitions()
    monkeypatch.setattr(global_config.experimental, "tool_search_tail_loading", False)
    result = await handle_tool(BuiltinToolRuntimeContext(engine, chat), ToolInvocation(tool_name="tool_search", arguments={"query": "plugin_query"}))
    assert result.structured_content["status"] == "loaded"
    append_search(chat, result)
    after, _ = await engine._build_action_tool_definitions()
    assert [tool["name"] for tool in after] == ["tool_search", "plugin_query"]
