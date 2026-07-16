import pytest

from src.chat.message_receive.chat_manager import BotChatSession
from src.chat.utils.utils import WEBUI_BOT_USER_ID, get_all_bot_accounts, get_bot_account, is_bot_self
from src.common.data_models.message_component_data_model import MessageSequence
from src.config.config import global_config
from src.services import send_service


def test_webui_bot_identity_does_not_depend_on_qq_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.bot, "qq_account", "")

    assert get_bot_account("webui") == WEBUI_BOT_USER_ID
    assert get_all_bot_accounts()["webui"] == WEBUI_BOT_USER_ID
    assert is_bot_self("webui", WEBUI_BOT_USER_ID)


def test_webui_bot_identity_does_not_reuse_configured_qq_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.bot, "qq_account", "123456789")

    assert get_bot_account("webui") == WEBUI_BOT_USER_ID
    assert get_bot_account("qq") == "123456789"
    assert not is_bot_self("webui", "123456789")
    assert is_bot_self("qq", "123456789")


def test_send_service_builds_webui_message_without_qq_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.bot, "qq_account", "")
    webui_session = BotChatSession(
        session_id="webui-session",
        platform="webui",
        user_id="webui-user",
    )
    monkeypatch.setattr(
        send_service._chat_manager,
        "get_session_by_session_id",
        lambda session_id: webui_session if session_id == webui_session.session_id else None,
    )

    message = send_service._build_outbound_session_message(
        MessageSequence([]).text("测试回复"),
        webui_session.session_id,
    )

    assert message is not None
    assert message.platform == "webui"
    assert message.message_info.user_info.user_id == WEBUI_BOT_USER_ID
