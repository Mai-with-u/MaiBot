from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import pytest

from src.common.data_models.message_component_data_model import EmojiComponent, MessageSequence, TextComponent
from src.emoji_system import semantic_match
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.builtin_tool.reply import get_tool_spec
from src.maisaka.context.message_id_alias import to_display_message_id
from src.maisaka.context.messages import ToolResultMediaMessage
from src.maisaka.reply_supplement import ReplySupplementPlan, SupplementResources, resolve_supplement


@pytest.mark.asyncio
async def test_context_emoji_uses_sticker_index_and_keeps_body_on_invalid_reference(monkeypatch):
    source = "tool_result:call_emoji:1"
    original = EmojiComponent(binary_hash="example", binary_data=b"emoji")
    message = ToolResultMediaMessage(
        raw_message=MessageSequence([TextComponent("caption"), original]),
        visible_text="caption", timestamp=datetime.now(), message_id=source, tool_call_id="call_emoji",
    )
    context = BuiltinToolRuntimeContext(None, SimpleNamespace(_chat_history=[message]))
    for index, expected_size in [(0, 2), (1, 1)]:
        items = await context.post_process_reply_message_items_async(
            "正文", {"attach_emoji": {"msg_id": to_display_message_id(source), "index": index}},
            skip_post_process=True,
        )
        assert len(items) == expected_size
        assert items[0].sequence.components[0].text == "正文"
        if index == 0:
            assert items[1].sequence.components[0].binary_data == b"emoji"


@pytest.mark.asyncio
async def test_attach_emoji_routes_structured_content_to_the_content_index(monkeypatch):
    emoji = EmojiComponent(binary_hash="example", binary_data=b"emoji")
    load = AsyncMock(return_value=emoji)
    monkeypatch.setattr(BuiltinToolRuntimeContext, "load_emoji_by_description", load)
    context = BuiltinToolRuntimeContext(None, SimpleNamespace(session_id="session"))

    items = await context.post_process_reply_message_items_async(
        "正文", {"attach_emoji": {"content": "猫在捂脸"}}, skip_post_process=True,
    )

    load.assert_awaited_once_with("", "session", content="猫在捂脸")
    assert items[1].sequence.components == [emoji]


@pytest.mark.asyncio
async def test_removed_collage_index_cannot_attach_an_emoji():
    spec = get_tool_spec()
    emoji_schema = spec.parameters_schema["properties"]["attach_emoji"]
    assert all(option["type"] != "integer" for option in emoji_schema["anyOf"])

    context = BuiltinToolRuntimeContext(None, SimpleNamespace())
    items = await context.post_process_reply_message_items_async(
        "正文", {"attach_emoji": 1}, skip_post_process=True,
    )
    assert [item.sequence.components[0].text for item in items] == ["正文"]


@pytest.mark.asyncio
async def test_supplement_exposes_reply_target_while_attachment_is_pending(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    target = SimpleNamespace(message_id="target")
    emoji = EmojiComponent(binary_hash="example", binary_data=b"emoji")

    async def load(*args, **kwargs):
        started.set()
        await release.wait()
        return emoji

    monkeypatch.setattr(BuiltinToolRuntimeContext, "load_emoji_by_description", load)
    plan = ReplySupplementPlan.model_validate({
        "msg_id": "target", "parts": [{"type": "reply"}, {"type": "emoji", "emotion": "开心"}],
    })
    resources = SupplementResources(
        messages={"target": target}, pictures={}, allow_at=False, allow_quote=True,
        context_emojis={}, session_id="session",
    )
    supplement = resolve_supplement(plan, resources, {})
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert supplement.target_message is target
        assert not supplement.attachments.done()
    finally:
        release.set()
        assert await supplement.attachments == [None, emoji]


@pytest.mark.asyncio
async def test_backfill_persists_vectors_and_matching_only_embeds_the_query(monkeypatch, tmp_path):
    monkeypatch.setattr(semantic_match, "_index_path", tmp_path / "emoji.sqlite3")
    monkeypatch.setattr(semantic_match, "_config_key", lambda: "config")
    monkeypatch.setattr(semantic_match, "_model_key", lambda _: "test")
    monkeypatch.setattr(semantic_match, "_backfill_cursor", 0)
    monkeypatch.setattr(semantic_match, "_BACKFILL_BATCH_SIZE", 1)
    monkeypatch.setattr(semantic_match, "_is_vlm_task_configured", lambda: True)
    emojis = [
        SimpleNamespace(file_hash="a", description="安慰"),
        SimpleNamespace(file_hash="b", description="自嘲"),
    ]
    analyze = AsyncMock(side_effect=[
        '{"emotion_tags":["安慰"],"content_tags":["猫","拥抱"]}',
        '{"emotion_tags":["自嘲"],"content_tags":["猫","捂脸"]}',
    ])
    monkeypatch.setattr(semantic_match, "emoji_manager", SimpleNamespace(
        emojis=emojis, analyze_emoji_image=analyze, get_emoji_by_hash_from_db=lambda _: emojis[1],
    ))

    async def embed(label):
        return SimpleNamespace(embedding={"安慰": [0.6, 0.8], "猫、捂脸": [0, 1]}.get(label, [1, 0]))

    client = SimpleNamespace(embed_text=AsyncMock(side_effect=embed))
    monkeypatch.setattr(semantic_match, "EmbeddingServiceClient", lambda **_: client)
    with pytest.raises(ValueError, match="没有完成所需维度向量索引"):
        await semantic_match.match_emoji_description("轻松地自嘲", "session")
    client.embed_text.assert_not_awaited()

    await semantic_match.backfill_emoji_index()
    assert [call.args[0] for call in client.embed_text.await_args_list] == ["猫、拥抱", "安慰"]
    assert semantic_match.get_emoji_tags("a").content_tags == ["猫", "拥抱"]

    # 模拟注册提交后排队，第二张图无需等下一轮定时维护。
    semantic_match.schedule_emoji_index("b")
    await asyncio.sleep(0)
    await semantic_match._pending["b"]
    assert semantic_match.get_emoji_tags("b").emotion_tags == ["自嘲"]
    client.embed_text.reset_mock()
    await semantic_match.backfill_emoji_index()
    assert await semantic_match.match_emoji_description("轻松地自嘲", "session") == "b"
    client.embed_text.assert_awaited_once_with("轻松地自嘲")
    client.embed_text.reset_mock()
    assert await semantic_match.match_emoji_description("", "session", content="猫抱在一起") == "a"
    client.embed_text.assert_awaited_once_with("猫抱在一起")
    client.embed_text.reset_mock()
    both_started = asyncio.Event()
    started = set()

    async def embed_together(text):
        started.add(text)
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        return await embed(text)

    client.embed_text.side_effect = embed_together
    assert await asyncio.wait_for(
        semantic_match.match_emoji_description("轻松地自嘲", "session", content="猫抱在一起"), timeout=2,
    ) == "a"
    assert [call.args[0] for call in client.embed_text.await_args_list] == ["轻松地自嘲", "猫抱在一起"]
    assert analyze.await_count == 2
