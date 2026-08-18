from types import SimpleNamespace

from src.chat.message_receive.bot import ChatBot
from src.cli.bot_console import BotConsole
from src.plugin_runtime.component_query import component_query_service


def test_registered_plugin_command_is_filter_exempt_candidate(monkeypatch) -> None:
    message = BotConsole._build_message("/demo 是这样的")
    message.processed_plain_text = "/demo 是这样的"
    message.session_id = "console-session"
    command_info = SimpleNamespace(name="demo")

    monkeypatch.setattr(
        component_query_service,
        "find_command_by_text",
        lambda _text: (object(), {}, command_info),
    )

    assert ChatBot._is_command_candidate(message) is True


def test_unregistered_slash_message_is_not_filter_exempt(monkeypatch) -> None:
    message = BotConsole._build_message("/unknown 是这样的")
    message.processed_plain_text = "/unknown 是这样的"
    message.session_id = "console-session"

    monkeypatch.setattr(component_query_service, "find_command_by_text", lambda _text: None)

    assert ChatBot._is_command_candidate(message) is False


def test_local_console_clear_with_target_is_filter_exempt(monkeypatch) -> None:
    message = BotConsole._build_message("/clear 是这样的群")
    message.processed_plain_text = "/clear 是这样的群"
    message.session_id = "console-session"

    monkeypatch.setattr(component_query_service, "find_command_by_text", lambda _text: None)

    assert ChatBot._is_command_candidate(message) is True
