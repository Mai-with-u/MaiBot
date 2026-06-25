from datetime import datetime

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.config.config import global_config
from src.maisaka.context.messages import AssistantMessage, SessionBackedMessage
from src.maisaka.runtime import MaisakaHeartFlowChatting


def _text_sequence(text: str) -> MessageSequence:
    return MessageSequence([TextComponent(text)])


def _user_message(text: str, *, user_id: str = "user-1") -> SessionMessage:
    message = SessionMessage("current-message", datetime.now(), platform="qq")
    message.message_info = MessageInfo(UserInfo(user_id=user_id, user_nickname="测试用户"))
    message.processed_plain_text = text
    message.raw_message = _text_sequence(text)
    return message


def _history_message(text: str, *, source_kind: str) -> SessionBackedMessage:
    return SessionBackedMessage(
        raw_message=_text_sequence(text),
        visible_text=text,
        timestamp=datetime.now(),
        source_kind=source_kind,
    )


def test_direct_followup_context_uses_visible_bot_reply_only(monkeypatch):
    monkeypatch.setattr(global_config.bot, "nickname", "麦麦")
    monkeypatch.setattr(global_config.bot, "qq_account", "bot-self")

    runtime = MaisakaHeartFlowChatting.__new__(MaisakaHeartFlowChatting)
    runtime._chat_history = [
        _history_message("普通用户上一句", source_kind="user"),
        AssistantMessage(content="内部规划器响应，不应该当作群里发言", timestamp=datetime.now()),
        _history_message("bot 实际发出去的话", source_kind="guided_reply"),
    ]

    context_text = runtime._get_recent_direct_followup_context(_user_message("接这句话"))

    assert context_text is not None
    assert "[麦麦] bot 实际发出去的话" in context_text
    assert "[测试用户] 接这句话" in context_text
    assert "内部规划器响应" not in context_text


def test_direct_followup_context_skips_without_recent_visible_bot_reply(monkeypatch):
    monkeypatch.setattr(global_config.bot, "qq_account", "bot-self")

    runtime = MaisakaHeartFlowChatting.__new__(MaisakaHeartFlowChatting)
    runtime._chat_history = [
        _history_message("普通用户上一句", source_kind="user"),
        AssistantMessage(content="内部规划器响应", timestamp=datetime.now()),
    ]

    assert runtime._get_recent_direct_followup_context(_user_message("接这句话")) is None


def test_direct_followup_context_skips_self_message(monkeypatch):
    monkeypatch.setattr(global_config.bot, "qq_account", "bot-self")

    runtime = MaisakaHeartFlowChatting.__new__(MaisakaHeartFlowChatting)
    runtime._chat_history = [_history_message("bot 实际发出去的话", source_kind="guided_reply")]

    assert runtime._get_recent_direct_followup_context(_user_message("自己发的消息", user_id="bot-self")) is None
