from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.emoji_system import emoji_manager as emoji_module
from src.plugin_runtime.capabilities.data import RuntimeDataCapabilityMixin


@pytest.mark.asyncio
@pytest.mark.parametrize("quiet", [False, True])
async def test_quiet_preserves_selected_emoji(monkeypatch, quiet):
    emoji = SimpleNamespace(file_name="example.png")
    manager = emoji_module.EmojiManager.__new__(emoji_module.EmojiManager)
    manager.emojis = [emoji]
    monkeypatch.setattr(manager, "_calculate_emotion_similarity_list", lambda label: [(emoji, 1.0)])
    monkeypatch.setattr(emoji_module, "_get_emoji_emotions", lambda value: ["开心"])
    log = Mock()
    monkeypatch.setattr(emoji_module, "logger", log)

    assert await manager.get_emoji_for_emotion("开心", quiet=quiet) is emoji
    assert log.info.call_count == (0 if quiet else 1)


@pytest.mark.asyncio
async def test_quiet_preserves_empty_library_warning(monkeypatch):
    manager = emoji_module.EmojiManager.__new__(emoji_module.EmojiManager)
    manager.emojis = []
    log = Mock()
    monkeypatch.setattr(emoji_module, "logger", log)

    assert await manager.get_emoji_for_emotion("开心", quiet=True) is None
    log.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("args, expected", [({}, False), ({"quiet": True}, True), ({"quiet": False}, False), ({"quiet": "false"}, False)])
async def test_capability_forwards_explicit_quiet_only(monkeypatch, args, expected):
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(emoji_module.emoji_manager, "get_emoji_for_emotion", fetch)

    result = await RuntimeDataCapabilityMixin()._cap_emoji_get_by_description(
        "test.plugin", "emoji.get_by_description", {"description": "开心", **args},
    )

    assert result == {"success": True, "emoji": None}
    fetch.assert_awaited_once_with("开心", quiet=expected)
