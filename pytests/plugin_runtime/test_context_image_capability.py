from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import base64

import pytest

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.common.data_models.message_component_data_model import ImageComponent, MessageSequence
from src.maisaka.context.message_id_alias import to_display_message_id
from src.maisaka.context.messages import SessionBackedMessage
from src.plugin_runtime.capabilities.core import RuntimeCoreCapabilityMixin


@pytest.fixture
def image_runtime(monkeypatch):
    source = SessionBackedMessage(
        message_id="tool_result:call_x:1", timestamp=datetime.now(), visible_text="图片",
        raw_message=MessageSequence([
            ImageComponent(binary_hash="", binary_data=b"first"),
            ImageComponent(binary_hash="", binary_data=b"second"),
        ]),
    )
    runtime = SimpleNamespace(_chat_history=[source], find_source_message_by_id=lambda source_id: None)
    monkeypatch.setattr(heartflow_manager, "heartflow_chat_list", {"current": runtime})
    monkeypatch.setattr(heartflow_manager, "get_or_create_heartflow_chat", AsyncMock())
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("source_id", ["tool_result:call_x:1", to_display_message_id("tool_result:call_x:1")])
async def test_context_image_resolves_media_and_alias(image_runtime, source_id):
    result = await RuntimeCoreCapabilityMixin()._cap_maisaka_context_resolve_image(
        "test.plugin", "maisaka.context.resolve_image",
        {"stream_id": "current", "source_id": source_id, "index": 1},
    )
    assert result["success"]
    assert base64.b64decode(result["image"]["binary_data_base64"]) == b"second"
    assert result["image"]["binary_hash"] == image_runtime._chat_history[0].raw_message.components[1].binary_hash
    heartflow_manager.get_or_create_heartflow_chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("args,error", [
    ({"stream_id": "other", "source_id": "tool_result:call_x:1"}, "没有现存"),
    ({"stream_id": "current", "source_id": "missing"}, "没有找到消息"),
    ({"stream_id": "current", "source_id": "tool_result:call_x:1", "index": 2}, "超出范围"),
    ({"stream_id": "current", "source_id": "tool_result:call_x:1", "index": True}, "非负整数"),
    ({"stream_id": "current", "source_id": "tool_result:call_x:1", "index": -1}, "非负整数"),
    ({"stream_id": "current"}, "source_id"),
])
async def test_context_image_errors_do_not_create_runtime(image_runtime, args, error):
    result = await RuntimeCoreCapabilityMixin()._cap_maisaka_context_resolve_image(
        "test.plugin", "maisaka.context.resolve_image", args,
    )
    assert result["success"] is False and error in result["error"]
    heartflow_manager.get_or_create_heartflow_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_image_respects_rpc_frame_size(image_runtime, monkeypatch):
    from src.plugin_runtime.transport import base

    monkeypatch.setattr(base, "MAX_FRAME_SIZE", 65536)
    result = await RuntimeCoreCapabilityMixin()._cap_maisaka_context_resolve_image(
        "test.plugin", "maisaka.context.resolve_image",
        {"stream_id": "current", "source_id": "tool_result:call_x:1"},
    )
    assert result["success"] is False and "RPC" in result["error"]
