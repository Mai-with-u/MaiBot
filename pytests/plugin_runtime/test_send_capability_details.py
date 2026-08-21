from datetime import datetime
from typing import Dict
from unittest.mock import AsyncMock

import pytest

from src.chat.message_receive.message import SessionMessage
from src.plugin_runtime.capabilities.core import RuntimeCoreCapabilityMixin
from src.services import send_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "capability", "args", "send_function"),
    [
        ("_cap_send_text", "send.text", {"text": "你好", "stream_id": "chat-1"}, "text_to_stream_with_message"),
        (
            "_cap_send_emoji",
            "send.emoji",
            {"emoji_base64": "ZW1vamk=", "stream_id": "chat-1"},
            "emoji_to_stream_with_message",
        ),
        (
            "_cap_send_image",
            "send.image",
            {"image_base64": "aW1hZ2U=", "stream_id": "chat-1"},
            "image_to_stream_with_message",
        ),
        (
            "_cap_send_hybrid",
            "send.hybrid",
            {"segments": [{"type": "text", "content": "你好"}], "stream_id": "chat-1"},
            "custom_reply_set_to_stream_with_message",
        ),
        (
            "_cap_send_forward",
            "send.forward",
            {
                "messages": [{"segments": [{"type": "text", "content": "你好"}]}],
                "stream_id": "chat-1",
            },
            "custom_reply_set_to_stream_with_message",
        ),
        (
            "_cap_send_command",
            "send.command",
            {"command": "ping", "stream_id": "chat-1"},
            "custom_to_stream_with_message",
        ),
        (
            "_cap_send_custom",
            "send.custom",
            {"message_type": "notice", "content": {"value": 1}, "stream_id": "chat-1"},
            "custom_to_stream_with_message",
        ),
    ],
)
async def test_send_capability_optionally_returns_final_message_id(
    monkeypatch: pytest.MonkeyPatch,
    handler_name: str,
    capability: str,
    args: Dict[str, object],
    send_function: str,
) -> None:
    sent_message = SessionMessage(message_id="platform-message-1", timestamp=datetime.now(), platform="test")
    sent_message.platform_message_id = "platform-message-1"
    send_mock = AsyncMock(return_value=sent_message)
    monkeypatch.setattr(send_service, send_function, send_mock)
    manager = RuntimeCoreCapabilityMixin()
    handler = getattr(manager, handler_name)

    assert await handler("demo.plugin", capability, dict(args)) == {"success": True}
    assert await handler("demo.plugin", capability, {**args, "return_details": True}) == {
        "success": True,
        "sent": True,
        "message_id": "platform-message-1",
    }


@pytest.mark.asyncio
async def test_send_capability_detailed_failure_has_no_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(send_service, "text_to_stream_with_message", AsyncMock(return_value=None))
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_send_text(
        "demo.plugin",
        "send.text",
        {"text": "你好", "stream_id": "chat-1", "return_details": True},
    )

    assert result == {"success": False, "sent": False, "message_id": None}


@pytest.mark.asyncio
async def test_send_capability_does_not_return_internal_temporary_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_message = SessionMessage(message_id="host-temporary-id", timestamp=datetime.now(), platform="test")
    monkeypatch.setattr(send_service, "text_to_stream_with_message", AsyncMock(return_value=sent_message))
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_send_text(
        "demo.plugin",
        "send.text",
        {"text": "你好", "stream_id": "chat-1", "return_details": True},
    )

    assert result == {"success": True, "sent": True, "message_id": None}
