"""插件提供的外部材料可以作为独立用户消息被回复工具定位。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.common.data_models.message_component_data_model import ImageComponent
from src.llm_models.payload_content.context_item import ContextImagePart
from src.maisaka.context.messages import InternalContextMessage, SessionBackedMessage
from src.maisaka.runtime import MaisakaHeartFlowChatting
from src.plugin_runtime.capabilities.core import RuntimeCoreCapabilityMixin


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    runtime = SimpleNamespace(
        chat_stream=SimpleNamespace(
            platform="xiaoheihe", account_id="100", scope=None,
            group_id="post:123:thread:browse", group_name="帖子讨论",
        ),
        _chat_history=[],
    )
    monkeypatch.setattr(heartflow_manager, "get_or_create_heartflow_chat", AsyncMock(return_value=runtime))
    return runtime


@pytest.mark.asyncio
async def test_external_materials_are_distinct_replyable_user_messages(runtime: SimpleNamespace) -> None:
    manager = RuntimeCoreCapabilityMixin()
    for message_id, uid, nickname, text in (
        ("browse:123", "200", "作者", "帖子正文"),
        ("browse:123:comment:8", "300", "评论者", "评论正文"),
    ):
        result = await manager._cap_maisaka_context_append("test.plugin", "maisaka.context.append", {
            "stream_id": "real-session", "message_id": message_id, "replyable": True,
            "user_info": {"user_id": uid, "user_nickname": nickname},
            "segments": [{"type": "text", "data": text}], "source_kind": "xiaoheihe_browse",
        })
        assert result["success"] and result["replyable"]
        context = runtime._chat_history[-1]
        assert isinstance(context, SessionBackedMessage)
        assert context.role == "user"
        assert nickname in context.render_visible_text()
        assert context.to_context_item() is not None
        source = MaisakaHeartFlowChatting.find_source_message_by_id(runtime, message_id)
        assert source is context.original_message
        assert source.message_info.user_info.user_id == uid
        assert source.session_id == "real-session"
        assert source.account_id == "100"
        assert source.message_info.group_info.group_id == "post:123:thread:browse"
    assert len(runtime._chat_history) == 2


@pytest.mark.asyncio
async def test_default_append_remains_internal_and_invalid_authors_are_rejected(runtime: SimpleNamespace) -> None:
    manager = RuntimeCoreCapabilityMixin()
    args = {"stream_id": "real-session", "segments": [{"type": "text", "data": "背景"}]}
    result = await manager._cap_maisaka_context_append("test.plugin", "maisaka.context.append", args)
    assert result["success"] and not result["replyable"]
    assert isinstance(runtime._chat_history[0], InternalContextMessage)
    result = await manager._cap_maisaka_context_append("test.plugin", "maisaka.context.append", {
        **args, "message_id": "browse:123", "replyable": True,
    })
    assert not result["success"]
    assert "user_info" in result["error"]
    assert len(runtime._chat_history) == 1


@pytest.mark.asyncio
async def test_replyable_user_message_preserves_its_image(runtime: SimpleNamespace) -> None:
    result = await RuntimeCoreCapabilityMixin()._cap_maisaka_context_append("test.plugin", "maisaka.context.append", {
        "stream_id": "real-session", "message_id": "browse:123", "replyable": True,
        "user_info": {"user_id": "200", "user_nickname": "作者"},
        "segments": [
            {"type": "text", "data": "配图正文"},
            {
                "type": "image", "data": "[帖子图片]", "format": "png",
                "binary_data_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=",
            },
        ],
    })
    assert result["success"]
    context = runtime._chat_history[0]
    assert any(isinstance(component, ImageComponent) for component in context.original_message.raw_message.components)
    item = context.to_context_item(enable_visual_message=True)
    assert any(isinstance(part, ContextImagePart) for part in item.parts)
