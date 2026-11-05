from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import hashlib

from PIL import Image
import pytest

from src.common.utils import image_path
from src.emoji_system import emoji_manager


@pytest.fixture
def cache_environment(tmp_path, monkeypatch):
    records = []
    session = SimpleNamespace(
        exec=lambda statement: SimpleNamespace(first=lambda: None, all=lambda: []),
        add=records.append,
    )

    @contextmanager
    def get_session():
        yield session

    monkeypatch.setattr(emoji_manager, "EMOJI_DIR", tmp_path)
    monkeypatch.setattr(emoji_manager, "get_db_session", get_session)
    monkeypatch.setattr(image_path, "PROJECT_ROOT", tmp_path)
    return tmp_path, records


@pytest.mark.asyncio
async def test_invalid_emoji_is_removed_without_database_write(cache_environment):
    directory, records = cache_environment
    manager = emoji_manager.EmojiManager.__new__(emoji_manager.EmojiManager)
    data = b"not an image"
    image_hash = hashlib.sha256(data).hexdigest()

    with pytest.raises(ValueError, match="表情包格式识别失败"):
        await manager._ensure_emoji_saved_locked(data, image_hash)

    assert list(directory.iterdir()) == []
    assert records == []


@pytest.mark.asyncio
async def test_valid_emoji_is_saved_with_actual_extension(cache_environment):
    directory, records = cache_environment
    manager = emoji_manager.EmojiManager.__new__(emoji_manager.EmojiManager)
    output = BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="PNG")
    data = output.getvalue()
    image_hash = hashlib.sha256(data).hexdigest()

    emoji = await manager._ensure_emoji_saved_locked(data, image_hash)

    assert emoji.full_path == (directory / f"{image_hash}.png").resolve()
    assert emoji.full_path.read_bytes() == data
    assert not list(directory.glob("*.tmp"))
    assert len(records) == 1
    assert records[0].image_hash == image_hash


@pytest.mark.asyncio
async def test_maintenance_skips_temporary_files(cache_environment, monkeypatch):
    directory, _ = cache_environment
    (directory / "incomplete.tmp").write_bytes(b"incomplete")
    (directory / "old.TMP").write_bytes(b"invalid")
    image_path = directory / "manual.png"
    image_path.write_bytes(b"manually imported image")
    manager = emoji_manager.EmojiManager.__new__(emoji_manager.EmojiManager)
    manager._emoji_num = 0
    manager._maintenance_wakeup_event = SimpleNamespace(wait=AsyncMock(), clear=lambda: None)
    manager.register_emoji_by_filename = AsyncMock(return_value="registered")
    manager.check_emoji_file_integrity = lambda: None
    monkeypatch.setattr(emoji_manager, "_ensure_directories", lambda: None)
    monkeypatch.setattr(
        emoji_manager, "global_config",
        SimpleNamespace(emoji=SimpleNamespace(check_interval=1, steal_emoji=True, max_reg_num=10)),
    )
    monkeypatch.setattr(emoji_manager, "_is_collected_emoji_size_allowed", lambda size: True)
    waits = 0

    async def wait_once_then_cancel(awaitable, timeout):
        nonlocal waits
        await awaitable
        waits += 1
        if waits == 1:
            raise asyncio.TimeoutError
        raise asyncio.CancelledError

    monkeypatch.setattr(emoji_manager.asyncio, "wait_for", wait_once_then_cancel)

    with pytest.raises(asyncio.CancelledError):
        await manager.periodic_emoji_maintenance()

    manager.register_emoji_by_filename.assert_awaited_once_with(image_path)
