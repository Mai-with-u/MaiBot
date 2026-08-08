import pytest

from src.chat.utils import utils as chat_utils


def test_regular_user_on_configured_platform_does_not_emit_unconfigured_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warning = []
    monkeypatch.setattr(chat_utils, "get_bot_accounts", lambda platform: {"bot-account"})
    monkeypatch.setattr(chat_utils.logger, "warning", warning.append)
    chat_utils._warned_unconfigured_platforms.clear()

    assert not chat_utils.is_bot_self("qq", "regular-user")
    assert warning == []


def test_unconfigured_platform_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    warning = []
    monkeypatch.setattr(chat_utils, "get_bot_accounts", lambda platform: set())
    monkeypatch.setattr(chat_utils.logger, "warning", warning.append)
    chat_utils._warned_unconfigured_platforms.clear()

    assert not chat_utils.is_bot_self("unknown", "regular-user")
    assert not chat_utils.is_bot_self("unknown", "another-user")
    assert len(warning) == 1
