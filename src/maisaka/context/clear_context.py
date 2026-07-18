"""Maisaka 短期上下文清理指令辅助函数。"""

from typing import Sequence, TypeVar

from src.chat.message_receive.message import SessionMessage

CLEAR_CONTEXT_COMMAND = "/clear"
CLEAR_CONTEXT_MARKER_KEY = "maisaka_context_cleared"
CLEAR_CONTEXT_INTERCEPT_LEVEL = 1

MessageT = TypeVar("MessageT", bound=SessionMessage)


def is_clear_context_command(text: str | None) -> bool:
    """判断消息文本是否是完整的 ``/clear`` 指令。"""

    return text is not None and text.strip() == CLEAR_CONTEXT_COMMAND


def mark_clear_context_command(message: SessionMessage) -> None:
    """把消息标记为 Maisaka 上下文清理边界。"""

    message.is_command = True
    message.message_info.additional_config[CLEAR_CONTEXT_MARKER_KEY] = True
    message.message_info.additional_config["intercept_message_level"] = CLEAR_CONTEXT_INTERCEPT_LEVEL


def is_clear_context_marker(message: SessionMessage) -> bool:
    """判断数据库消息是否是已经执行过的上下文清理边界。"""

    return message.is_command and message.message_info.additional_config.get(CLEAR_CONTEXT_MARKER_KEY) is True


def select_messages_after_latest_clear_marker(messages: Sequence[MessageT]) -> list[MessageT]:
    """仅保留最后一次清理之后可恢复到 Maisaka 的非指令消息。"""

    restore_start_index = 0
    for index, message in enumerate(messages):
        if is_clear_context_marker(message):
            restore_start_index = index + 1

    return [message for message in messages[restore_start_index:] if not message.is_command]
