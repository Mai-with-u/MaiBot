from datetime import datetime

import pytest

from src.chat.message_receive.message import SessionMessage
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import FileComponent, MessageSequence, TextComponent
from src.maisaka.context.messages import SessionBackedMessage


@pytest.mark.parametrize("caption", ["", "看看这个文件："])
def test_replyer_preserves_file_in_history_and_reply_target(caption: str) -> None:
    timestamp = datetime(2026, 11, 16, 9, 23, 14)
    original = SessionMessage(message_id="file-message", timestamp=timestamp, platform="qq")
    original.message_info = MessageInfo(user_info=UserInfo(user_id="123", user_nickname="千石可乐"))
    original.raw_message = MessageSequence([
        TextComponent(caption),
        FileComponent(name="P2990443.JPG", size=12880091, file_ref="attachment-ref"),
    ])
    message = SessionBackedMessage(
        raw_message=original.raw_message,
        visible_text="",
        timestamp=timestamp,
        message_id=original.message_id,
        original_message=original,
    )
    generator = object.__new__(BaseMaisakaReplyGenerator)

    history_line = generator._render_reply_history_line(message)
    target_content = generator._build_target_message_content(original)

    assert "[09:23:14] 千石可乐说：" in history_line
    for content in (history_line, target_content):
        assert "[文件] P2990443.JPG" in content
        assert "大小: 12880091" in content
        assert "file_ref: attachment-ref" in content
        if caption:
            assert caption in content
