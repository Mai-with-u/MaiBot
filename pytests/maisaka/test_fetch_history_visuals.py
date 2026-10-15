"""历史召回的纯文本策略应贯穿构造、渲染和识图描述刷新。"""

from base64 import b64decode
from datetime import datetime
from types import SimpleNamespace
from typing import List, Tuple, cast
from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    StandardMessageComponents,
    TextComponent,
)
from src.llm_models.payload_content.context_item import (
    ContextImagePart,
    ContextTextPart,
    UserMessageItem,
    get_item_text,
)
from src.maisaka.context.message_adapter import build_visible_text_from_sequence
from src.maisaka.context.message_id_alias import to_display_message_id
from src.maisaka.context.messages import LLMContextMessage, SessionBackedMessage
from src.maisaka.focus.runtime_mixin import MaisakaFocusRuntimeMixin
from src.maisaka.reasoning_engine import MaisakaReasoningEngine


_PNG_BYTES = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY1cAAAAASUVORK5CYII=")


class _FetchHistoryRuntime(MaisakaFocusRuntimeMixin):
    """保留真实召回方法，仅提供构造历史所需的运行时状态。"""

    def __init__(self, messages: List[SessionMessage]) -> None:
        self.message_cache = messages
        self._chat_history: List[LLMContextMessage] = []
        self.chat_stream = SimpleNamespace(
            session_id="test-session",
            platform="test-platform",
            is_group_session=True,
            group_id="test-group",
            user_id="",
        )
        self.log_prefix = "[历史召回测试]"
        self._reasoning_engine = MaisakaReasoningEngine(self)

    def _is_focus_mode_active_for_current_chat(self) -> bool:
        return True


def _message(
    message_id: str,
    components: List[StandardMessageComponents],
    *,
    minute: int = 0,
) -> SessionMessage:
    """使用真实消息组件，避开接收链路的数据库和媒体处理。"""

    sequence = MessageSequence(components)
    return cast(
        SessionMessage,
        SimpleNamespace(
            message_id=message_id,
            session_id="test-session",
            timestamp=datetime(2026, 9, 22, 14, minute, 0),
            message_info=SimpleNamespace(
                user_info=SimpleNamespace(
                    user_id="test-user",
                    user_nickname="测试昵称",
                    user_cardname="测试群名片",
                ),
            ),
            is_notify=False,
            raw_message=sequence,
            processed_plain_text=build_visible_text_from_sequence(sequence),
            process=AsyncMock(),
        ),
    )


@pytest.fixture
def visual_loads(monkeypatch: pytest.MonkeyPatch) -> Tuple[List[str], List[str]]:
    """替换实际二进制加载，仍让引擎执行真实的水合和消息渲染。"""

    image_hashes: List[str] = []
    emoji_hashes: List[str] = []

    async def load_image(component: ImageComponent) -> None:
        image_hashes.append(component.binary_hash)
        component.binary_data = _PNG_BYTES

    async def load_emoji(component: EmojiComponent) -> None:
        emoji_hashes.append(component.binary_hash)
        component.binary_data = _PNG_BYTES

    monkeypatch.setattr("src.maisaka.reasoning_engine.resolve_enable_visual_planner", lambda: True)
    monkeypatch.setattr(ImageComponent, "load_image_binary", load_image)
    monkeypatch.setattr(EmojiComponent, "load_emoji_binary", load_emoji)
    return image_hashes, emoji_hashes


def _text_only_item(message: LLMContextMessage) -> UserMessageItem:
    item = message.to_context_item(enable_visual_message=True)
    assert isinstance(item, UserMessageItem)
    assert all(isinstance(part, ContextTextPart) for part in item.parts)
    return item


@pytest.mark.asyncio
@pytest.mark.parametrize("preloaded", [False, True])
async def test_fetch_history_keeps_text_metadata_and_order_without_visual_payload(
    preloaded: bool,
    visual_loads: Tuple[List[str], List[str]],
) -> None:
    binary_data = _PNG_BYTES if preloaded else b""
    older = _message(
        "older",
        [
            ReplyComponent("quoted-message"),
            TextComponent("以前的消息"),
            ImageComponent(binary_hash="image-old", content="[图片：小猫]", binary_data=binary_data),
            EmojiComponent(binary_hash="emoji-old", content="[表情包: 大笑]", binary_data=binary_data),
        ],
        minute=1,
    )
    newer = _message(
        "newer",
        [
            TextComponent("最新消息"),
            ImageComponent(binary_hash="image-new", binary_data=binary_data),
            EmojiComponent(binary_hash="emoji-new", binary_data=binary_data),
        ],
        minute=2,
    )
    existing = _message("existing", [TextComponent("已经进入上下文")])
    runtime = _FetchHistoryRuntime([older, existing, newer, newer])
    runtime._chat_history = [
        SessionBackedMessage.from_session_message(
            existing,
            raw_message=existing.raw_message,
            visible_text=existing.processed_plain_text,
        )
    ]
    original_components = [
        component
        for message in (older, newer)
        for component in message.raw_message.components
        if isinstance(component, (ImageComponent, EmojiComponent))
    ]
    original_binaries = [component.binary_data for component in original_components]

    content, structured, recalled = await runtime.build_focus_fetch_history_result(num=50)

    assert visual_loads == ([], [])
    assert [message.message_id for message in recalled] == ["newer", "older"]
    assert [message["message_id"] for message in structured["messages"]] == ["newer", "older"]
    assert structured["chat_id"] == "test-session"
    assert structured["messages"][1] == {
        "message_id": "older",
        "timestamp": "2026-09-22T14:01:00",
        "user_id": "test-user",
        "user_name": "测试群名片",
        "text": older.processed_plain_text,
    }
    assert "召回消息数: 2" in content
    assert all(isinstance(message, SessionBackedMessage) and not message.allow_visual for message in recalled)
    assert all(message.source == "user" for message in recalled)

    newer_item, older_item = [_text_only_item(message) for message in recalled]
    newer_text = get_item_text(newer_item)
    older_text = get_item_text(older_item)
    assert "最新消息" in newer_text
    assert "[图片，识别中.....]" in newer_text
    assert "[表情包]" in newer_text
    assert 'msg_id="older"' in older_text
    assert 'chat_id="test-session"' in older_text
    assert f'quote="{to_display_message_id("quoted-message")}"' in older_text
    assert 'time="14:01:00"' in older_text
    assert 'user="测试昵称"' in older_text
    assert 'group_card="测试群名片"' in older_text
    assert "以前的消息" in older_text
    assert "[图片：小猫]" in older_text
    assert "[表情包: 大笑]" in older_text
    assert older_item.meta.timestamp == older.timestamp
    # 仅文字策略不能通过清空源消息二进制实现，否则会破坏其他消息入口。
    assert [component.binary_data for component in original_components] == original_binaries


@pytest.mark.asyncio
async def test_default_history_path_still_loads_and_renders_visuals(
    visual_loads: Tuple[List[str], List[str]],
) -> None:
    message = _message(
        "normal-message",
        [
            ImageComponent(binary_hash="normal-image", content="[图片：小猫]"),
            EmojiComponent(binary_hash="normal-emoji", content="[表情包: 大笑]"),
        ],
    )
    runtime = _FetchHistoryRuntime([message])

    history = await runtime.build_session_messages_as_user_history([message])

    assert visual_loads == (["normal-image"], ["normal-emoji"])
    assert len(history) == 1
    assert isinstance(history[0], SessionBackedMessage)
    assert history[0].allow_visual is True
    item = history[0].to_context_item(enable_visual_message=True)
    assert isinstance(item, UserMessageItem)
    images = [part for part in item.parts if isinstance(part, ContextImagePart)]
    assert len(images) == 2
    assert all(b64decode(part.image_base64) == _PNG_BYTES for part in images)

    text_item = history[0].to_context_item(enable_visual_message=False)
    assert isinstance(text_item, UserMessageItem)
    assert all(isinstance(part, ContextTextPart) for part in text_item.parts)
    assert "[图片：小猫]" in get_item_text(text_item)
    assert "[表情包: 大笑]" in get_item_text(text_item)


@pytest.mark.asyncio
async def test_default_history_path_respects_disabled_visual_planner(
    monkeypatch: pytest.MonkeyPatch,
    visual_loads: Tuple[List[str], List[str]],
) -> None:
    monkeypatch.setattr("src.maisaka.reasoning_engine.resolve_enable_visual_planner", lambda: False)
    message = _message(
        "normal-text-message",
        [
            ImageComponent(binary_hash="text-image", content="[图片：小猫]"),
            EmojiComponent(binary_hash="text-emoji", content="[表情包: 大笑]"),
        ],
    )
    runtime = _FetchHistoryRuntime([message])

    history = await runtime.build_session_messages_as_user_history([message])

    assert visual_loads == ([], [])
    assert len(history) == 1
    text = get_item_text(_text_only_item(history[0]))
    assert "[图片：小猫]" in text
    assert "[表情包: 大笑]" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("use_runtime_history", [False, True])
async def test_description_refresh_preserves_text_only_policy_and_context_identity(
    use_runtime_history: bool,
    monkeypatch: pytest.MonkeyPatch,
    visual_loads: Tuple[List[str], List[str]],
) -> None:
    message = _message(
        "pending-message",
        [
            ImageComponent(binary_hash="pending-image"),
            EmojiComponent(binary_hash="pending-emoji"),
        ],
    )
    runtime = _FetchHistoryRuntime([message])
    _, _, recalled = await runtime.build_focus_fetch_history_result(num=1)
    runtime._chat_history.extend(recalled)
    # 回复器可传入独立的历史列表，刷新应修改调用方传入的列表。
    history = runtime._chat_history if use_runtime_history else list(recalled)
    original_item = _text_only_item(recalled[0])
    assert "[图片，识别中.....]" in get_item_text(original_item)

    image_description = Mock(return_value="小猫趴在窗台上")
    emoji_description = Mock(return_value="开心挥手")
    monkeypatch.setattr("src.maisaka.visual.chat_history_refresher._lookup_cached_image_description", image_description)
    monkeypatch.setattr("src.maisaka.visual.chat_history_refresher._lookup_cached_emoji_description", emoji_description)

    refreshed_count = await runtime._reasoning_engine._refresh_chat_history_visual_placeholders_once(history)

    assert refreshed_count == 1
    assert visual_loads == ([], [])
    image_description.assert_called_once_with("pending-image")
    emoji_description.assert_called_once_with("pending-emoji")
    cast(AsyncMock, message.process).assert_not_awaited()
    refreshed = history[0]
    assert isinstance(refreshed, SessionBackedMessage)
    assert refreshed.allow_visual is False
    assert refreshed.message_id == message.message_id
    assert refreshed.original_message is message
    assert refreshed.context_item_id == original_item.meta.item_id
    refreshed_item = _text_only_item(refreshed)
    assert refreshed_item.meta == original_item.meta
    assert "[图片：小猫趴在窗台上]" in get_item_text(refreshed_item)
    assert "[表情包: 开心挥手]" in get_item_text(refreshed_item)
    assert "[图片，识别中.....]" not in refreshed.visible_text
    assert "小猫趴在窗台上" in refreshed.visible_text


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_visual", [False, True])
async def test_partial_description_refresh_preserves_unrecognized_media(
    allow_visual: bool,
    monkeypatch: pytest.MonkeyPatch,
    visual_loads: Tuple[List[str], List[str]],
) -> None:
    """使用真实消息处理方法，确保部分描述命中时不会重新识别或清空其他媒体。"""
    ready_image = ImageComponent(binary_hash="ready-image")
    pending_image = ImageComponent(binary_hash="pending-image")
    pending_emoji = EmojiComponent(binary_hash="pending-emoji")
    # 模拟已加载的媒体，保留各自缓存键，避免相同测试图片在构造时得到相同哈希。
    for component in (ready_image, pending_image, pending_emoji):
        component.binary_data = _PNG_BYTES
    source = _message("partial-refresh", [ready_image, pending_image, pending_emoji])
    message = SessionMessage(source.message_id, source.timestamp, platform="test-platform")
    message.session_id = source.session_id
    message.message_info = source.message_info
    message.raw_message = source.raw_message
    message.processed_plain_text = source.processed_plain_text
    runtime = _FetchHistoryRuntime([message])
    history = await runtime.build_session_messages_as_user_history([message], allow_visual=allow_visual)
    original_item = history[0].to_context_item()
    assert isinstance(original_item, UserMessageItem)

    monkeypatch.setattr(
        "src.maisaka.visual.chat_history_refresher._lookup_cached_image_description",
        lambda image_hash: "小猫" if image_hash == "ready-image" else "",
    )
    monkeypatch.setattr("src.maisaka.visual.chat_history_refresher._lookup_cached_emoji_description", lambda _: "")
    image_recognition = AsyncMock(return_value=None)
    emoji_recognition = AsyncMock(return_value=None)
    monkeypatch.setattr("src.chat.image_system.image_manager.image_manager.get_image_description", image_recognition)
    monkeypatch.setattr("src.emoji_system.emoji_manager.emoji_manager.get_emoji_description", emoji_recognition)

    refreshed_count = await runtime._reasoning_engine._refresh_chat_history_visual_placeholders_once(history)

    image_recognition.assert_not_awaited()
    emoji_recognition.assert_not_awaited()
    assert refreshed_count == 1
    assert visual_loads == ([], [])
    assert ready_image.content == "[图片：小猫]"
    assert pending_image.content == pending_emoji.content == ""
    assert all(component.binary_data == _PNG_BYTES for component in (ready_image, pending_image, pending_emoji))
    refreshed = history[0]
    assert isinstance(refreshed, SessionBackedMessage)
    assert refreshed.allow_visual is allow_visual
    assert "[图片：小猫]" in refreshed.visible_text
    assert "[图片，识别中.....]" in refreshed.visible_text
    refreshed_item = refreshed.to_context_item()
    assert isinstance(refreshed_item, UserMessageItem)
    assert refreshed_item.meta == original_item.meta
    assert sum(isinstance(part, ContextImagePart) for part in refreshed_item.parts) == (3 if allow_visual else 0)
