"""Regression coverage for messages that already carry an emoji description."""

from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from unittest.mock import AsyncMock, Mock

from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select

import pytest

from src.chat.message_receive import message as message_module
from src.chat.message_receive.message import SessionMessage
from src.common.database import database
from src.common.database.database_model import Images, ImageType
from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ForwardComponent,
    ForwardNodeComponent,
    MessageSequence,
    StandardMessageComponents,
    TextComponent,
)
from src.common.utils import image_path
from src.emoji_system import emoji_manager as emoji_module


DESCRIPTION = "[表情包: 开心]"
MIB = 1024 * 1024


def make_message(*components: StandardMessageComponents) -> SessionMessage:
    """Build a real session message without starting a bot."""
    message = SessionMessage(message_id="fixture-message", timestamp=datetime.now(), platform="qq")
    message.session_id = "fixture-session"
    message.raw_message = MessageSequence(list(components))
    return message


@pytest.fixture(autouse=True)
def emoji_settings(monkeypatch):
    """Set a known collection limit and prevent any real recognition call."""
    monkeypatch.setattr(emoji_module.global_config.emoji, "max_emoji_size_mb", 1.0)
    describe = AsyncMock()
    monkeypatch.setattr(emoji_module.emoji_manager, "get_emoji_description", describe)
    yield
    describe.assert_not_awaited()


@pytest.mark.asyncio
async def test_preannotated_emoji_can_be_reloaded_by_hash(tmp_path, monkeypatch):
    """Exercise the real message, cache write, database lookup and file read."""
    emoji_dir = tmp_path / "emoji"
    emoji_dir.mkdir()
    engine = create_engine(f"sqlite:///{(tmp_path / 'emoji.db').as_posix()}")
    SQLModel.metadata.create_all(engine, tables=[Images.__table__])

    @contextmanager
    def get_test_session():
        """Use a fresh SQLite session for each cache operation."""
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    monkeypatch.setattr(database, "get_db_session", get_test_session)
    monkeypatch.setattr(emoji_module, "get_db_session", get_test_session)
    monkeypatch.setattr(emoji_module, "EMOJI_DIR", emoji_dir)
    monkeypatch.setattr(image_path, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(emoji_module.global_config.emoji, "steal_emoji", False)
    with BytesIO() as buffer:
        Image.new("RGB", (2, 2), (25, 50, 75)).save(buffer, format="PNG")
        original = buffer.getvalue()
    emoji = EmojiComponent(binary_hash="", content=f"  {DESCRIPTION}  ", binary_data=original)
    message = make_message(emoji)

    try:
        await message.process(enable_heavy_media_analysis=False)
        assert message.processed_plain_text == DESCRIPTION
        assert emoji.content == DESCRIPTION
        assert emoji.binary_data == b""

        with get_test_session() as session:
            record = session.exec(
                select(Images).filter_by(image_hash=emoji.binary_hash, image_type=ImageType.EMOJI)
            ).one()
            assert not record.is_registered
        restored = EmojiComponent(binary_hash=emoji.binary_hash)
        await restored.load_emoji_binary()
        assert restored.binary_data == original
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("forwarded", [False, True])
async def test_cache_failure_preserves_description_and_logs_error(monkeypatch, forwarded):
    """Cover both top-level messages and the actual merged-forward path."""
    original = b"fixture-emoji"
    emoji = EmojiComponent(binary_hash="", content=f"  {DESCRIPTION}  ", binary_data=original)
    error = OSError("fixture disk write failure")
    save = AsyncMock(side_effect=error)
    logger = Mock()
    monkeypatch.setattr(emoji_module.emoji_manager, "ensure_emoji_saved", save)
    monkeypatch.setattr(message_module, "logger", logger)
    component = ForwardNodeComponent([ForwardComponent("测试用户", "forward-node", [emoji])]) if forwarded else emoji
    message = make_message(TextComponent("前文"), component, TextComponent("后文"))

    await message.process(enable_heavy_media_analysis=False)

    expected = f"【合并转发消息: \n-- 【测试用户】: {DESCRIPTION}\n】" if forwarded else DESCRIPTION
    assert message.processed_plain_text == f"前文 {expected} 后文"
    assert emoji.binary_data == original
    save.assert_awaited_once_with(original)
    logger.error.assert_called_once_with(f"处理消息组件时发生错误: {error}")


@pytest.mark.asyncio
async def test_direct_emoji_processing_surfaces_save_error(monkeypatch):
    """Direct callers still receive the actual cache error and original bytes."""
    original = b"fixture-emoji"
    emoji = EmojiComponent(binary_hash="", content=DESCRIPTION, binary_data=original)
    error = ValueError("fixture invalid emoji format")
    monkeypatch.setattr(emoji_module.emoji_manager, "ensure_emoji_saved", AsyncMock(side_effect=error))

    with pytest.raises(ValueError) as raised:
        await make_message(emoji).process_emoji_component(emoji)

    assert raised.value is error
    assert emoji.binary_data == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "limit", "saved"),
    [(0, 1.0, False), (MIB, 1.0, True), (MIB + 1, 1.0, False), (6, 5.5 / MIB, False), (MIB + 1, 0.0, True)],
)
async def test_preannotated_emoji_size_policy(monkeypatch, size, limit, saved):
    """Respect the existing byte limit, including exact and unlimited bounds."""
    monkeypatch.setattr(emoji_module.global_config.emoji, "max_emoji_size_mb", limit)
    save = AsyncMock()
    monkeypatch.setattr(emoji_module.emoji_manager, "ensure_emoji_saved", save)
    original = b"x" * size
    emoji = EmojiComponent(binary_hash="", content=f"  {DESCRIPTION}  ", binary_data=original)
    message = make_message(emoji)

    await message.process(enable_heavy_media_analysis=False)

    assert message.processed_plain_text == DESCRIPTION
    assert emoji.binary_data == (b"" if saved else original)
    if saved:
        save.assert_awaited_once_with(original)
    else:
        save.assert_not_awaited()
