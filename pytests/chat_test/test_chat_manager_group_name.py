from types import SimpleNamespace

from src.chat.message_receive.chat_manager import BotChatSession, ChatManager


def _make_group_message(group_id: str, group_name: str):
    return SimpleNamespace(
        message_info=SimpleNamespace(
            group_info=SimpleNamespace(group_id=group_id, group_name=group_name),
            user_info=SimpleNamespace(user_id="1001", user_nickname="", user_cardname=""),
        )
    )


def _make_session(group_name: str) -> BotChatSession:
    return BotChatSession(
        session_id="group-session",
        platform="qq",
        group_id="1001",
        group_name=group_name,
    )


def test_empty_group_name_does_not_overwrite_real_name() -> None:
    manager = ChatManager()
    # 群名为空是适配器「未知」的显式语义；不应覆盖另一个适配器记录的真实群名
    session = _make_session("麦麦交流群")
    changed = manager._update_session_identity(session, _make_group_message("1001", ""))
    assert session.group_name == "麦麦交流群"
    assert changed is False


def test_real_group_name_still_updates_session() -> None:
    manager = ChatManager()
    session = _make_session("麦麦交流群")
    changed = manager._update_session_identity(session, _make_group_message("1001", "改名后的群"))
    assert session.group_name == "改名后的群"
    assert changed is True


def test_empty_group_name_keeps_session_unnamed() -> None:
    manager = ChatManager()
    # 会话还没有名称时空群名也不写入，保持无名（展示层按目标 ID 回退）
    session = _make_session("")
    changed = manager._update_session_identity(session, _make_group_message("1001", ""))
    assert session.group_name == ""
    assert changed is False
