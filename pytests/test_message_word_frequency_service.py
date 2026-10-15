from datetime import datetime
from typing import List

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.maisaka.context.messages import SessionBackedMessage
from src.services import message_word_frequency_service as service


def make_message(text: str, *, notify: bool = False, user_id: str = "human") -> SessionBackedMessage:
    original = SessionMessage("message-1", datetime(2026, 10, 11), "qq")
    original.session_id = "real-session"
    original.message_info = MessageInfo(UserInfo(user_id, "说话人昵称"))
    original.is_notify = notify
    original.raw_message = MessageSequence([TextComponent(text)])
    return SessionBackedMessage.from_session_message(
        original,
        raw_message=MessageSequence([TextComponent('<message user="包装昵称">\n加工后的包装文字')]),
        visible_text="包装文字",
    )


@pytest.fixture(autouse=True)
def mock_bot_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "is_bot_self", lambda platform, user_id: user_id == "bot")


def test_learning_reads_original_text_and_deduplicates_messages() -> None:
    message = make_message("模型插件 模型插件")
    assert service._extract_user_texts_by_chat_id_from_context([message, message]) == {
        "real-session": ["模型插件 模型插件"]
    }


@pytest.mark.parametrize("notify,user_id", [(True, "human"), (False, "bot")])
def test_notifications_and_self_messages_are_not_learned(notify: bool, user_id: str) -> None:
    message = make_message("[事件-回应] id 表达标识", notify=notify, user_id=user_id)
    assert service._extract_user_texts_by_chat_id_from_context([message]) == {}


def test_messages_without_original_or_non_user_source_are_not_learned() -> None:
    message = make_message("模型插件")
    message.source_kind = "tool_result_media"
    assert service._extract_user_texts_by_chat_id_from_context([message]) == {}
    message.source_kind = "user"
    message.original_message = None
    assert service._extract_user_texts_by_chat_id_from_context([message]) == {}


def test_real_words_are_not_filtered_by_pollution_blacklist(monkeypatch: pytest.MonkeyPatch) -> None:
    message = make_message("事件响应事件响应")
    recorded: List[str] = []

    def record_terms(*, chat_id, terms, generated_at, max_terms):
        assert chat_id == "real-session"
        recorded.extend(item.term for item in terms)
        return len(terms)

    monkeypatch.setattr(service, "_merge_high_frequency_terms", record_terms)
    assert service.update_high_frequency_terms_from_context_messages([message]) > 0
    assert "事件" in recorded


def test_same_word_from_token_and_phrase_has_one_row_and_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_tokenize_meaningful_terms", lambda text: ["机器学习", "机器", "学习"])
    terms = service._build_message_word_frequency_terms(["机器学习 机器 学习"])
    assert [(item.term, item.count, item.message_count) for item in terms] == [("机器学习", 2, 1)]
