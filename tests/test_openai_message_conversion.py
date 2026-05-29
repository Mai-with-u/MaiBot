from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm_models.model_client.openai_client import _convert_messages
from src.llm_models.payload_content.message import Message, RoleType, TextMessagePart


def test_openai_message_conversion_replaces_blank_text_string() -> None:
    messages = [Message(role=RoleType.User, parts=[TextMessagePart("  \n")])]

    converted_messages = _convert_messages(messages)

    assert converted_messages[0]["content"] == "[空白消息]"


def test_openai_message_conversion_skips_blank_text_blocks_in_mixed_content() -> None:
    messages = [
        Message(
            role=RoleType.User,
            parts=[TextMessagePart("开头"), TextMessagePart(" \t"), TextMessagePart("结尾")],
        )
    ]

    converted_messages = _convert_messages(messages)

    assert converted_messages[0]["content"] == [
        {"type": "text", "text": "开头"},
        {"type": "text", "text": "结尾"},
    ]
