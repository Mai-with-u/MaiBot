from datetime import datetime
from types import SimpleNamespace

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.reply_generation_data_models import (
    LLMCompletionResult,
    ReplyGenerationResult,
)
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import AtComponent, TextComponent
from src.config.config import global_config
from src.core.tooling import ToolAvailabilityContext, ToolInvocation

import src.maisaka.turn_scheduler as turn_scheduler_module
from src.maisaka.builtin_tool import reply as reply_tool_module
from src.maisaka.builtin_tool import get_builtin_tools
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.builtin_tool.wait import handle_tool as handle_wait_tool
from src.maisaka.mode_policy import is_idle_cycle_reason, is_reply_necessity_trigger_enabled
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.turn_scheduler import MessageTurnScheduler


def _tool_names(tool_definitions: list[dict]) -> set[str]:
    return {
        str(tool_definition.get("name") or "").strip()
        for tool_definition in tool_definitions
        if str(tool_definition.get("name") or "").strip()
    }


def _availability_context() -> ToolAvailabilityContext:
    return ToolAvailabilityContext(
        session_id="session-1",
        stream_id="session-1",
        is_group_chat=True,
    )


def test_planner_exposes_wait_without_no_action_or_finish() -> None:
    tool_names = _tool_names(get_builtin_tools(_availability_context()))

    assert "wait" in tool_names
    assert "finish" not in tool_names
    assert "no_action" not in tool_names


def test_rich_reply_hides_standalone_media_tools(monkeypatch) -> None:
    monkeypatch.setattr(global_config.experimental, "enable_rich_reply", True, raising=False)

    tool_names = _tool_names(get_builtin_tools(_availability_context()))

    assert "reply" in tool_names
    assert "send_image" not in tool_names
    assert "send_emoji" not in tool_names


def test_rich_reply_adds_reply_attachment_parameters(monkeypatch) -> None:
    monkeypatch.setattr(global_config.experimental, "enable_rich_reply", True, raising=False)

    reply_tool = next(tool for tool in get_builtin_tools(_availability_context()) if tool["name"] == "reply")
    properties = reply_tool["parameters_schema"]["properties"]

    assert "attach_pic" in properties
    assert "attach_emoji" in properties
    assert "attach_at" in properties


def test_reply_attachment_parameters_are_hidden_when_rich_reply_disabled(monkeypatch) -> None:
    monkeypatch.setattr(global_config.experimental, "enable_rich_reply", False, raising=False)

    reply_tool = next(tool for tool in get_builtin_tools(_availability_context()) if tool["name"] == "reply")
    properties = reply_tool["parameters_schema"]["properties"]

    assert "attach_pic" not in properties
    assert "attach_emoji" not in properties
    assert "attach_at" not in properties


@pytest.mark.asyncio
async def test_rich_reply_output_expands_at_and_text() -> None:
    target_message = SessionMessage(message_id="msg-1", timestamp=datetime.now(), platform="qq")
    target_message.message_info = MessageInfo(
        user_info=UserInfo(
            user_id="user-1",
            user_nickname="用户",
            user_cardname="群名片",
        ),
        additional_config={},
    )

    class DummyRuntime:
        @staticmethod
        def find_source_message_by_id(message_id: str):
            return target_message if message_id == "msg-1" else None

        @staticmethod
        def _update_stage_status(stage: str, detail: str) -> None:
            del stage, detail

    tool_ctx = BuiltinToolRuntimeContext.__new__(BuiltinToolRuntimeContext)
    tool_ctx.runtime = DummyRuntime()

    sequences = await tool_ctx.post_process_rich_reply_message_sequences_async("你好", {"attach_at": ["msg-1"]})

    assert len(sequences) == 1
    components = sequences[0].components
    assert isinstance(components[0], AtComponent)
    assert components[0].target_user_id == "user-1"
    assert components[0].target_user_cardname == "群名片"
    assert isinstance(components[1], TextComponent)
    assert components[1].text == "你好"


@pytest.mark.asyncio
async def test_rich_reply_uses_action_parameters_without_checker(monkeypatch) -> None:
    monkeypatch.setattr(global_config.experimental, "enable_rich_reply", True, raising=False)

    target_message = SessionMessage(message_id="msg-1", timestamp=datetime.now(), platform="qq")
    target_message.message_info = MessageInfo(
        user_info=UserInfo(
            user_id="user-1",
            user_nickname="用户",
            user_cardname="群名片",
        ),
        additional_config={},
    )

    class DummyReplyer:
        async def generate_reply_with_context(self, **kwargs):
            assert "attach_at" not in kwargs["reply_tool_args"]
            return True, ReplyGenerationResult(
                success=True,
                completion=LLMCompletionResult(response_text="啥基米弓前端是真好用吧"),
            )

    class DummyRuntime:
        session_id = "session-1"
        chat_stream = SimpleNamespace(platform="qq", is_group_session=True)
        log_prefix = "[test]"

        def __init__(self) -> None:
            self._chat_history = []

        @staticmethod
        def find_source_message_by_id(message_id: str):
            return target_message if message_id == "msg-1" else None

        @staticmethod
        def _update_stage_status(stage: str, detail: str) -> None:
            del stage, detail

    sent_segments: list[str] = []
    sent_sequences: list[object] = []

    async def fake_send_to_target_with_message(**kwargs):
        sent_segments.append(kwargs["processed_plain_text"])
        sent_sequences.append(kwargs["message_sequence"])
        return SimpleNamespace(message_id="sent-1")

    monkeypatch.setattr(reply_tool_module.replyer_manager, "get_replyer", lambda **kwargs: DummyReplyer())
    monkeypatch.setattr(reply_tool_module.send_service, "_send_to_target_with_message", fake_send_to_target_with_message)

    tool_ctx = BuiltinToolRuntimeContext.__new__(BuiltinToolRuntimeContext)
    tool_ctx.runtime = DummyRuntime()
    invocation = ToolInvocation(
        tool_name="reply",
        arguments={"msg_id": "msg-1", "attach_at": ["msg-1"]},
        call_id="reply-1",
    )

    result = await reply_tool_module.handle_tool(tool_ctx, invocation)

    assert result.success is True
    assert sent_segments == ["@群名片啥基米弓前端是真好用吧"]
    assert isinstance(sent_sequences[0].components[0], AtComponent)
    monitor_detail = result.metadata["monitor_detail"]
    assert monitor_detail["output_text"] == "@群名片啥基米弓前端是真好用吧"
    assert "extra_sections" not in monitor_detail


def test_planner_no_tool_ends_cycle() -> None:
    class DummyRuntime:
        log_prefix = "[test]"

        def __init__(self) -> None:
            self._chat_history = []
            self.ended = False
            self.stopped = False
            self.wait_reset_reason = ""

        def _end_planner_continuation(self) -> None:
            self.ended = True

        def _reset_consecutive_wait_count(self, reason: str) -> None:
            self.wait_reset_reason = reason

        def _enter_stop_state(self) -> None:
            self.stopped = True

    runtime = DummyRuntime()
    engine = MaisakaReasoningEngine.__new__(MaisakaReasoningEngine)
    engine._runtime = runtime
    planner_extra_lines: list[str] = []

    count, cycle_end, should_end = engine._handle_planner_no_tool_retry(
        0,
        planner_extra_lines,
    )

    assert count == 1
    assert cycle_end.reason == "planner_no_tool_end"
    assert is_idle_cycle_reason(cycle_end.reason)
    assert "结束" in cycle_end.detail
    assert planner_extra_lines == ["状态：未调用工具，已结束本轮思考"]
    assert should_end is True
    assert runtime.ended is True
    assert runtime.stopped is True
    assert runtime.wait_reset_reason == "planner_no_tool_end"


def test_reply_necessity_trigger_is_optional(monkeypatch) -> None:
    monkeypatch.setattr(global_config.chat.reply_timing, "reply_trigger_mode", "frequency")

    assert is_reply_necessity_trigger_enabled() is False

    monkeypatch.setattr(global_config.chat.reply_timing, "reply_trigger_mode", "reply_necessity")

    assert is_reply_necessity_trigger_enabled() is True


def test_wait_completed_message_includes_elapsed_seconds() -> None:
    class DummyRuntime:
        def _consume_pending_wait_state(self):
            return "wait-1", 3.2, 10.0

    engine = MaisakaReasoningEngine.__new__(MaisakaReasoningEngine)
    engine._runtime = DummyRuntime()

    message = engine._build_wait_completed_message(has_new_messages=True)

    assert message.tool_call_id == "wait-1"
    assert message.tool_name == "wait"
    assert "实际等待 3.2 秒" in message.content
    assert "原计划等待 10.0 秒" in message.content


def test_private_chat_message_breaks_wait(monkeypatch) -> None:
    monkeypatch.setattr(turn_scheduler_module.focus_mode_manager, "can_decide", lambda *args, **kwargs: True)
    monkeypatch.setattr(turn_scheduler_module, "is_reply_necessity_trigger_enabled", lambda: False)

    class DummyIdleBackoff:
        @staticmethod
        def should_delay(pending_count: int) -> bool:
            del pending_count
            return False

    class DummyRuntime:
        _STATE_WAIT = "wait"
        _STATE_RUNNING = "running"

        def __init__(self, *, is_group_session: bool) -> None:
            self.session_id = "session-1"
            self.chat_stream = SimpleNamespace(is_group_session=is_group_session)
            self.log_prefix = "[test]"
            self._agent_state = self._STATE_WAIT
            self._message_turn_scheduled = False
            self._idle_backoff = DummyIdleBackoff()
            self.enqueued = False

        @staticmethod
        def _is_reply_frequency_silent() -> bool:
            return False

        def _enter_running_state(self) -> None:
            self._agent_state = self._STATE_RUNNING

        @staticmethod
        def _get_pending_message_count() -> int:
            return 1

        @staticmethod
        def _get_effective_reply_frequency() -> float:
            return 1.0

        @staticmethod
        def _format_reply_frequency_for_display(value: float) -> str:
            return str(value)

        @staticmethod
        def _has_forced_turn_trigger() -> bool:
            return False

        @staticmethod
        def _get_message_trigger_threshold() -> int:
            return 1

        def _enqueue_message_turn(self) -> None:
            self.enqueued = True
            self._message_turn_scheduled = True

    private_runtime = DummyRuntime(is_group_session=False)
    MessageTurnScheduler(private_runtime).schedule_message_turn()

    assert private_runtime._agent_state == private_runtime._STATE_RUNNING
    assert private_runtime.enqueued is True

    group_runtime = DummyRuntime(is_group_session=True)
    MessageTurnScheduler(group_runtime).schedule_message_turn()

    assert group_runtime._agent_state == group_runtime._STATE_WAIT
    assert group_runtime.enqueued is False


@pytest.mark.asyncio
async def test_wait_tool_rejects_after_consecutive_limit(monkeypatch) -> None:
    monkeypatch.setattr(global_config.chat.reply_timing, "max_consecutive_wait_count", 5, raising=False)

    class DummyRuntime:
        def __init__(self) -> None:
            self.count = 0

        def _try_enter_wait_state(self, seconds=None, tool_call_id=None):
            del seconds, tool_call_id
            max_count = int(global_config.chat.reply_timing.max_consecutive_wait_count)
            if self.count >= max_count:
                return False, self.count, max_count
            self.count += 1
            return True, self.count, max_count

    tool_ctx = BuiltinToolRuntimeContext.__new__(BuiltinToolRuntimeContext)
    tool_ctx.runtime = DummyRuntime()
    invocation = ToolInvocation(tool_name="wait", arguments={"seconds": 1}, call_id="wait-1")

    for _ in range(5):
        result = await handle_wait_tool(tool_ctx, invocation)
        assert result.success is True
        assert result.metadata.get("pause_execution") is True

    result = await handle_wait_tool(tool_ctx, invocation)

    assert result.success is True
    assert result.metadata.get("pause_execution") is True
    assert result.metadata.get("wait_limit_reached") is True
    assert result.metadata.get("wait_rest") is True
    assert result.metadata.get("consecutive_wait_count") == 5
