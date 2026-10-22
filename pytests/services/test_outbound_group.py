from datetime import datetime
from typing import Optional

import pytest

from src.chat.message_receive.chat_manager import BotChatSession
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.services import send_service


@pytest.mark.parametrize(
    "stored_name,context_name,expected_name",
    [
        (None, None, ""),
        ("", "", ""),
        ("  ", "", ""),
        ("", "上下文群名", "上下文群名"),
        ("已保存群名", "", "已保存群名"),
    ],
)
def test_outbound_group_preserves_target_without_name(
    monkeypatch: pytest.MonkeyPatch,
    stored_name: Optional[str],
    context_name: Optional[str],
    expected_name: str,
) -> None:
    session = BotChatSession(
        session_id="group-session", platform="qq", group_id="648947612", group_name=stored_name, account_id="bot"
    )
    if context_name is not None:
        inbound = SessionMessage(message_id="incoming", timestamp=datetime.now(), platform="qq")
        inbound.message_info = MessageInfo(
            user_info=UserInfo(user_id="member", user_nickname="成员"),
            group_info=GroupInfo(group_id=session.group_id, group_name=context_name),
        )
        session.set_context(inbound)
    monkeypatch.setitem(send_service._chat_manager.sessions, session.session_id, session)
    monkeypatch.setattr(send_service, "get_bot_account", lambda platform, account_id: account_id)

    message = send_service._build_outbound_session_message(MessageSequence([TextComponent("你好")]), session.session_id)

    assert message is not None
    assert message.message_info.group_info is not None
    assert message.message_info.group_info.group_id == "648947612"
    assert message.message_info.group_info.group_name == expected_name
