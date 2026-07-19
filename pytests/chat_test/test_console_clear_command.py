from typing import List, Tuple

import pytest

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.chat.message_receive.bot import ChatBot
from src.chat.message_receive.chat_manager import chat_manager
from src.chat.message_receive.message import SessionMessage
from src.cli.bot_console import BotConsole
from src.common.utils.utils_message import MessageUtils
from src.maisaka.context.clear_context import is_clear_context_marker
from src.services import send_service


@pytest.mark.asyncio
async def test_console_clear_targets_selected_real_chat(monkeypatch) -> None:
    message = BotConsole._build_message("/clear 测试群")
    message.processed_plain_text = "/clear 测试群"
    message.session_id = "console-session"
    stored_messages: List[SessionMessage] = []
    cleared_session_ids: List[str] = []
    sent_messages: List[Tuple[str, str]] = []

    monkeypatch.setattr(
        chat_manager,
        "get_named_session_options",
        lambda excluded_platforms=None: {"测试群": "real-group-session"},
    )

    async def store_message(stored_message: SessionMessage) -> None:
        stored_messages.append(stored_message)

    async def clear_context(session_id: str) -> bool:
        cleared_session_ids.append(session_id)
        return True

    async def send_text(
        text: str,
        stream_id: str,
        storage_message: bool = True,
    ) -> bool:
        sent_messages.append((text, stream_id))
        return True

    monkeypatch.setattr(MessageUtils, "store_message_to_db_async", store_message)
    monkeypatch.setattr(heartflow_manager, "clear_chat_history_context", clear_context)
    monkeypatch.setattr(send_service, "text_to_stream", send_text)

    handled = await ChatBot()._process_clear_context_command(message)

    assert handled is True
    assert cleared_session_ids == ["real-group-session"]
    assert len(stored_messages) == 1
    assert stored_messages[0].session_id == "real-group-session"
    assert is_clear_context_marker(stored_messages[0]) is True
    assert sent_messages == [("已清空“测试群”的 Maisaka 历史上下文。", "console-session")]


@pytest.mark.asyncio
async def test_console_clear_without_chat_name_only_shows_usage(monkeypatch) -> None:
    message = BotConsole._build_message("/clear")
    message.processed_plain_text = "/clear"
    message.session_id = "console-session"
    cleared_session_ids: List[str] = []
    sent_messages: List[Tuple[str, str]] = []

    async def store_message(_stored_message: SessionMessage) -> None:
        return None

    async def clear_context(session_id: str) -> bool:
        cleared_session_ids.append(session_id)
        return True

    async def send_text(
        text: str,
        stream_id: str,
        storage_message: bool = True,
    ) -> bool:
        sent_messages.append((text, stream_id))
        return True

    monkeypatch.setattr(MessageUtils, "store_message_to_db_async", store_message)
    monkeypatch.setattr(heartflow_manager, "clear_chat_history_context", clear_context)
    monkeypatch.setattr(send_service, "text_to_stream", send_text)

    handled = await ChatBot()._process_clear_context_command(message)

    assert handled is True
    assert cleared_session_ids == []
    assert sent_messages == [
        ("控制台中的 /clear 必须指定聊天名，请输入 /clear 后按 Tab 选择。", "console-session")
    ]
