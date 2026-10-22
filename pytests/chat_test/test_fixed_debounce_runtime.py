from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio
import time

import pytest

from src.chat.debounce import MessageDebouncer
from src.chat.debounce.message_adapter import get_message_shape
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    ForwardComponent,
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.maisaka import runtime as runtime_module
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting


@pytest.fixture
def runtime(monkeypatch):
    config = SimpleNamespace(
        chat=SimpleNamespace(
            reply_timing=SimpleNamespace(message_debounce_mode="natural")
        )
    )
    monkeypatch.setattr(runtime_module, "global_config", config)
    instance = object.__new__(MaisakaHeartFlowChatting)
    instance._running = True
    instance.session_id = "test"
    instance.log_prefix = "[test]"
    instance._fixed_message_debouncer = MessageDebouncer()
    instance._message_debounce_was_enabled = False
    instance._last_message_received_at = time.time()
    instance.message_cache = []
    instance._last_processed_index = 0
    return instance


def _message():
    message = SessionMessage("test-message", datetime.now(), "test", received_at=datetime.now())
    message.message_info = MessageInfo(UserInfo("alice", "Alice"))
    message.raw_message = MessageSequence(
        [TextComponent("看这个"), ImageComponent(binary_hash="test", content="识图描述")]
    )
    return message


def test_shape_uses_original_text_and_prioritizes_image():
    message = _message()
    message.processed_plain_text = "很长的图片识别描述" * 100
    assert get_message_shape(message) == (1, 3)


def test_forward_is_not_treated_as_ordinary_text():
    message = _message()
    node = ForwardComponent("Alice", "forwarded-message", [TextComponent("原文")])
    message.raw_message = MessageSequence([ForwardNodeComponent([node])])
    assert get_message_shape(message) == (3, 0)


def test_notification_and_command_do_not_extend_debounce(runtime):
    message = _message()
    message.is_notify = True
    runtime._observe_message_debounce(message, time.time())
    assert runtime._fixed_message_debouncer.deadline is None
    message.is_notify = False
    message.is_command = True
    runtime._observe_message_debounce(message, time.time())
    assert runtime._fixed_message_debouncer.deadline is None


@pytest.mark.asyncio
async def test_initial_turn_wait_can_be_shortened_by_new_message(runtime, monkeypatch):
    message = _message()
    runtime._observe_message_debounce(message, time.time())
    waiter = asyncio.create_task(runtime._wait_for_message_quiet_period())
    await asyncio.sleep(0)
    assert not waiter.done()  # 初次判断也需要等待，不依赖“打断后再等”的旧标记。
    message.is_at = True
    runtime._observe_message_debounce(message, time.time() - 1.1)
    await asyncio.wait_for(waiter, 0.5)


@pytest.mark.asyncio
async def test_disabling_switch_releases_an_existing_wait(runtime, monkeypatch):
    runtime._observe_message_debounce(_message(), time.time())
    waiter = asyncio.create_task(runtime._wait_for_message_quiet_period())
    await asyncio.sleep(0)
    runtime_module.global_config.chat.reply_timing.message_debounce_mode = "off"
    await asyncio.wait_for(waiter, 1)


@pytest.mark.asyncio
async def test_stopping_cancels_debounce_without_leaking_tasks(runtime, monkeypatch):
    runtime._observe_message_debounce(_message(), time.time())
    waiter = asyncio.create_task(runtime._wait_for_message_quiet_period())
    await asyncio.sleep(0)
    runtime._running = False
    runtime._fixed_message_debouncer.consume()
    await asyncio.wait_for(waiter, 0.5)


@pytest.mark.asyncio
async def test_disabled_switch_does_not_wait(runtime, monkeypatch):
    runtime_module.global_config.chat.reply_timing.message_debounce_mode = "off"
    runtime._observe_message_debounce(_message(), time.time())
    assert runtime._get_message_quiet_period_remaining_seconds() == 0.0
    await asyncio.wait_for(runtime._wait_for_message_quiet_period(), 0.1)


def test_consuming_messages_resets_batch_without_dropping_messages(runtime, monkeypatch):
    message = _message()
    runtime._observe_message_debounce(message, time.time())
    runtime.message_cache = [message, message]
    runtime.session_id = "test"
    assert runtime._collect_pending_messages() == [message, message]
    assert runtime._last_processed_index == 2
    assert runtime._fixed_message_debouncer.deadline is None


@pytest.mark.asyncio
async def test_ingest_path_waits_before_consuming_messages(runtime, monkeypatch):
    message = _message()
    runtime.message_cache = [message]
    runtime.log_prefix = "[test]"
    runtime._cycle_counter = 1
    runtime._wait_for_message_quiet_period = AsyncMock()
    engine = MaisakaReasoningEngine(runtime)
    engine._ingest_messages = AsyncMock()
    engine._emit_flow_step = AsyncMock()
    assert await engine._ingest_pending_messages() == [message]
    runtime._wait_for_message_quiet_period.assert_awaited_once()
    engine._ingest_messages.assert_awaited_once_with([message])


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["message", "timeout", "proactive"])
async def test_all_turn_start_paths_wait_before_collecting(runtime, monkeypatch, trigger):
    message = _message()
    runtime.message_cache = [message]
    runtime._agent_state = runtime._STATE_STOP
    runtime._is_reply_frequency_silent = Mock(return_value=False)
    runtime._enter_running_state = Mock()
    runtime._update_stage_status = Mock()
    runtime._has_pending_wait_tool_call = Mock(return_value=False)
    runtime._wait_for_message_quiet_period = AsyncMock()
    engine = MaisakaReasoningEngine(runtime)
    engine._drain_ready_turn_triggers = Mock(
        return_value=(trigger == "message", trigger == "timeout", trigger == "proactive")
    )
    engine._ingest_messages = AsyncMock()
    result = await engine._prepare_turn_start_context(trigger)
    runtime._wait_for_message_quiet_period.assert_awaited_once()
    assert result.cached_messages == [message]
