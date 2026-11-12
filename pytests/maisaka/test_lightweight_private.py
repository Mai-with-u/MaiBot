"""轻量私聊的工具边界、模式隔离与回复收尾。"""

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, Mock

import pytest

from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.config.official_configs import TargetItem
from src.core.tooling import ToolExecutionResult, ToolInvocation
from src.maisaka.builtin_tool.query_memory import get_tool_spec as memory_spec
from src.maisaka.builtin_tool.query_person_profile import get_tool_spec as profile_spec
from src.maisaka.builtin_tool.stop import get_tool_spec as stop_spec
from src.maisaka.builtin_tool.tool_search import get_tool_spec as search_spec
from src.maisaka.builtin_tool.view_forward_message import get_tool_spec as forward_spec
from src.maisaka.builtin_tool.wait import get_tool_spec as wait_spec
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.lightweight import build_lightweight_tools, is_lightweight_private_chat
from src.maisaka.reasoning_engine import CycleRuntimeState, MaisakaReasoningEngine
from src.maisaka.scenes.private.behavior import PrivateChatScene


def test_lightweight_tools_shrink_schema_without_mutating_registry() -> None:
    memory = memory_spec()
    plugin = memory_spec()
    plugin.provider_name = "plugin"
    disabled = profile_spec(enabled=False)
    definitions = build_lightweight_tools([
        memory, profile_spec(), forward_spec(), stop_spec(), wait_spec(), search_spec(), plugin, disabled,
    ])
    assert [item["name"] for item in definitions] == [
        "query_memory", "query_person_profile", "view_forward_message", "stop", "tool_search",
    ]
    assert set(definitions[0]["parameters_schema"]["properties"]) == {"query"}
    assert definitions[0]["parameters_schema"]["required"] == ["query"]
    assert "mode" in memory.parameters_schema["properties"]


@pytest.mark.parametrize("group,registered,enabled", [(False, True, True), (True, True, False), (False, False, False)])
def test_mode_requires_registered_private_session(
    monkeypatch: pytest.MonkeyPatch, group: bool, registered: bool, enabled: bool,
) -> None:
    session = SimpleNamespace(is_group_session=group, platform="qq", user_id="123")
    monkeypatch.setattr(global_config.experimental, "lightweight_private_chats", [
        TargetItem(platform="qq", item_id="123", rule_type="private"),
    ])
    monkeypatch.setattr("src.maisaka.lightweight.chat_manager.get_session_by_session_id", lambda _: session if registered else None)
    assert is_lightweight_private_chat("real-session") is enabled


@pytest.mark.asyncio
async def test_builtin_tools_remain_available_across_rounds() -> None:
    runtime = SimpleNamespace(
        _tool_registry=SimpleNamespace(list_tools=AsyncMock(return_value=[memory_spec(), wait_spec(), search_spec()])),
        update_deferred_tool_specs=Mock(), set_current_action_tool_names=Mock(), _max_internal_rounds=30,
        _chat_loop_service=SimpleNamespace(select_llm_context_messages=lambda history, **kwargs: (history, "")),
        _chat_history=[], _max_context_size=20, chat_stream=SimpleNamespace(is_group_session=False),
        sync_discovered_deferred_tools_with_context=Mock(),
        get_discovered_deferred_tool_specs=Mock(return_value=[]), build_deferred_tools_reminder=Mock(return_value=""),
    )
    engine = MaisakaReasoningEngine(runtime)
    engine._turn_lightweight_private = True
    engine._build_tool_availability_context = Mock()
    definitions, reminder = await engine._build_action_tool_definitions()
    assert [item["name"] for item in definitions] == ["query_memory", "tool_search"]
    assert reminder == ""
    for _ in range(3):
        assert await engine._build_action_tool_definitions() == (definitions, "")
    runtime.set_current_action_tool_names.assert_called_with(["query_memory", "tool_search"])
    runtime.update_deferred_tool_specs.assert_called_with([])
    assert runtime._tool_registry.list_tools.await_count == 4


@pytest.mark.asyncio
async def test_plain_guidance_runs_supplement_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = object()
    runtime = SimpleNamespace(
        session_id="real-session", log_prefix="test", _chat_history=[],
        _chat_loop_service=SimpleNamespace(planner_request_snapshot=snapshot),
        chat_stream=SimpleNamespace(is_group_session=False),
        _update_stage_status=Mock(), _end_planner_continuation=Mock(),
        _reset_consecutive_wait_count=Mock(), _enter_stop_state=Mock(),
    )
    engine = MaisakaReasoningEngine(runtime)
    engine._turn_lightweight_private = True
    engine._turn_passive_replyer = True
    engine._emit_planner_progress = AsyncMock()
    engine._record_tool_execution_effects = AsyncMock()
    engine._get_planner_content = Mock(return_value="回应用户的问候")
    supplement = AsyncMock(return_value=object())
    monkeypatch.setattr("src.maisaka.reasoning_engine.generate_reply_supplement", supplement)

    async def run_reply(*args: Any, **kwargs: Any) -> ToolExecutionResult:
        assert await kwargs["supplement_runner"]() is supplement.return_value
        return ToolExecutionResult(tool_name="reply", success=True)

    execute = AsyncMock(side_effect=run_reply)
    monkeypatch.setattr("src.maisaka.reasoning_engine.execute_reply", execute)
    response = SimpleNamespace(tool_calls=[], raw_messages=[], prompt_html_uri="")
    target = SimpleNamespace(message_id="message-1")
    state = CycleRuntimeState(reply_target_message=target, response=response)
    assert await engine._handle_planner_response_actions(
        response=response, cycle_detail=SimpleNamespace(time_records={}), state=state, planner_extra_lines=[],
    )
    supplement.assert_awaited_once_with(snapshot, "回应用户的问候", "real-session", False)
    assert execute.await_args.kwargs["target_message"] is target
    assert state.cycle_end.reason == "planner_reply_end"
    runtime._enter_stop_state.assert_called_once()


def test_lightweight_prompt_snapshot_and_focus_exclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.maisaka.chat_loop_service.is_lightweight_private_chat", lambda _: False)
    service = MaisakaChatLoopService(session_id="real-session", is_group_chat=False)
    service.lightweight_private_override = True
    assert service._get_chat_prompt_name() == "maisaka_chat_lightweight"
    assert service._build_planner_final_user_reminder() == ""
    monkeypatch.setattr(global_config.experimental, "focus_on_private", True)
    monkeypatch.setattr("src.maisaka.scenes.private.behavior.is_lightweight_private_chat", lambda _: True)
    assert not PrivateChatScene().allows_focus("real-session")


@pytest.mark.asyncio
async def test_stop_ends_lightweight_turn_without_reply() -> None:
    runtime = SimpleNamespace(
        _has_pending_messages=lambda: False, _chat_history=[], _enter_stop_state=Mock(),
        _end_planner_continuation=Mock(), _reset_consecutive_wait_count=Mock(),
    )
    engine = MaisakaReasoningEngine(runtime)
    engine._turn_lightweight_private = True
    engine._turn_passive_replyer = True
    engine._get_planner_content = Mock(return_value="")
    engine._should_replace_reasoning = Mock(return_value=False)
    engine._emit_planner_progress = AsyncMock()
    engine._handle_tool_calls = AsyncMock(return_value=(False, "", [], []))
    response = SimpleNamespace(tool_calls=[SimpleNamespace(func_name="stop", call_id="stop-1")], raw_messages=[])
    state = CycleRuntimeState()
    assert await engine._handle_planner_response_actions(
        response=response, cycle_detail=SimpleNamespace(time_records={}), state=state, planner_extra_lines=[],
    )
    engine._handle_tool_calls.assert_not_awaited()
    runtime._enter_stop_state.assert_called_once()
    assert state.cycle_end == engine._cycle_end_for_pause_tool("stop")


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_lightweight_prompt_renders_in_all_supported_languages(locale: str) -> None:
    text = load_prompt(
        "maisaka_chat_lightweight", locale=locale, bot_name="麦麦", bot_alias_notice="",
        personality="友善", behavior_style="简洁", bot_nickname_notice="",
    )
    assert "麦麦" in text
    assert "{bot_name}" not in text
    assert "query_memory" in text
    assert "tool_search" in text
    assert "stop()" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("names,executed", [
    (["wait"], []), (["query_memory", "query_memory"], ["query_memory", "query_memory"]),
])
async def test_execution_rejects_unavailable_but_allows_multiple_calls(names: List[str], executed: List[str]) -> None:
    invoke = AsyncMock(return_value=ToolExecutionResult(tool_name="query_memory", success=True))
    runtime = SimpleNamespace(
        _tool_registry=SimpleNamespace(list_tools=AsyncMock(return_value=[memory_spec(), wait_spec()]), invoke=invoke),
        is_action_tool_currently_available=lambda name: name == "query_memory",
        deferred_tool_specs_by_name={},
        _update_stage_status=Mock(), _reset_consecutive_wait_count=Mock(),
    )
    engine = MaisakaReasoningEngine(runtime)
    engine._turn_lightweight_private = True
    engine._turn_passive_replyer = True
    engine._build_tool_execution_context = Mock()
    engine._build_tool_availability_context = Mock()
    engine._log_tool_call_source = Mock()
    engine._build_tool_invocation = lambda call, _: ToolInvocation(tool_name=call.func_name)
    engine._record_tool_execution_effects = AsyncMock()
    engine._append_tool_execution_result = Mock()
    engine._append_tool_post_history_messages = Mock()
    engine._emit_planner_progress = AsyncMock()

    async def record_display(**kwargs: Any) -> None:
        kwargs["tool_monitor_results"].append({"success": kwargs["result"].success})

    engine._append_tool_display_results = record_display
    calls = [SimpleNamespace(func_name=name, call_id=str(index)) for index, name in enumerate(names)]
    pause, _, _, results = await engine._handle_tool_calls(calls, "", None, CycleRuntimeState())
    assert not pause
    assert [call.args[0].tool_name for call in invoke.await_args_list] == executed
    assert [result["success"] for result in results] == [name == "query_memory" for name in names]
