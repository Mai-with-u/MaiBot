from src.chat.message_receive.chat_manager import BotChatSession, ChatManager
from src.core.local_operator import BOT_CONSOLE_PLATFORM


def test_named_session_options_use_chat_names_and_exclude_console() -> None:
    manager = ChatManager()
    manager.sessions = {
        "group-session": BotChatSession(
            session_id="group-session",
            platform="qq",
            group_id="1001",
            group_name="测试群",
        ),
        "private-session": BotChatSession(
            session_id="private-session",
            platform="qq",
            user_id="2001",
            user_nickname="小明",
        ),
        "console-session": BotChatSession(
            session_id="console-session",
            platform=BOT_CONSOLE_PLATFORM,
            user_id="local_operator",
            user_nickname="本地操作员",
        ),
    }

    options = manager.get_named_session_options(
        excluded_platforms={BOT_CONSOLE_PLATFORM},
    )

    assert options == {
        "小明的私聊": "private-session",
        "测试群": "group-session",
    }


def test_named_session_options_disambiguate_duplicate_chat_names() -> None:
    manager = ChatManager()
    manager.sessions = {
        "first-group": BotChatSession(
            session_id="first-group",
            platform="qq",
            group_id="1001",
            group_name="测试群",
        ),
        "second-group": BotChatSession(
            session_id="second-group",
            platform="qq",
            group_id="1002",
            group_name="测试群",
        ),
    }

    options = manager.get_named_session_options()

    assert options == {
        "测试群 [qq:1001]": "first-group",
        "测试群 [qq:1002]": "second-group",
    }
