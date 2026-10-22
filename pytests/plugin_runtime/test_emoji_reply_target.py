from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.emoji_system.maisaka_tool import MaisakaEmojiSendResult
from src.maisaka.builtin_tool import send_emoji


@pytest.mark.asyncio
@pytest.mark.parametrize("target_id", ["browse:123", "browse:123:comment:8", "", "unknown"])
async def test_native_emoji_tool_preserves_explicit_community_target(monkeypatch, target_id):
    target = object()
    runtime = SimpleNamespace(
        _chat_history=[],
        session_id="session",
        log_prefix="test",
        chat_stream=SimpleNamespace(platform="xiaoheihe"),
        find_source_message_by_id=lambda value: target if value.startswith("browse:") else None,
    )
    ctx = SimpleNamespace(
        runtime=runtime,
        build_failure_result=lambda *args, **kwargs: {"success": False},
        build_success_result=lambda *args, **kwargs: {"success": True},
    )
    sender = AsyncMock(return_value=MaisakaEmojiSendResult(True, "已发送"))
    monkeypatch.setattr(send_emoji, "send_emoji_for_maisaka", sender)
    result = await send_emoji.handle_tool(
        ctx,
        SimpleNamespace(
            arguments={"msg_id": target_id},
            tool_name="send_emoji",
            reasoning="参与讨论",
        ),
    )
    if target_id.startswith("browse:"):
        assert result["success"]
        assert sender.call_args.kwargs["reply_message"] is target
    else:
        assert not result["success"]
        sender.assert_not_called()
