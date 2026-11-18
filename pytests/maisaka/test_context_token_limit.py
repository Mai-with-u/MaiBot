"""上下文条数与实际输入 Token 阈值的模式切换。"""

from datetime import datetime
from typing import List

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.config.config import global_config
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.context.messages import LLMContextMessage, SessionBackedMessage
from src.maisaka.context.post_processor import process_chat_history_after_cycle


def history(size: int = 80) -> List[LLMContextMessage]:
    timestamp = datetime(2026, 11, 16, 12)
    return [
        SessionBackedMessage(
            raw_message=MessageSequence([TextComponent(str(index))]),
            visible_text=str(index),
            timestamp=timestamp,
            message_id=str(index),
            original_message=SessionMessage(message_id=str(index), timestamp=timestamp, platform="test"),
            source_kind="user",
        )
        for index in range(size)
    ]


@pytest.mark.parametrize("is_group", [True, False])
def test_token_mode_selects_history_beyond_count_limit(monkeypatch, is_group):
    field = "use_token_limit" if is_group else "private_use_token_limit"
    monkeypatch.setattr(global_config.chat, field, True)
    messages = history()
    selected, _ = MaisakaChatLoopService.select_llm_context_messages(
        messages, max_context_size=30, is_group_chat=is_group, enable_visual_message=False,
    )
    assert selected == messages

    monkeypatch.setattr(global_config.chat, field, False)
    selected, _ = MaisakaChatLoopService.select_llm_context_messages(
        messages, max_context_size=30, is_group_chat=is_group, enable_visual_message=False,
    )
    assert len(selected) < len(messages)
    assert selected[-1] is messages[-1]


@pytest.mark.parametrize("prompt_tokens", [0, 15999])
def test_token_mode_ignores_count_before_token_threshold(prompt_tokens):
    messages = history()
    result = process_chat_history_after_cycle(
        messages, max_context_size=30, max_context_tokens=16000, prompt_tokens=prompt_tokens,
    )
    assert result.history == messages
    assert result.removed_messages == []


def test_actual_input_token_threshold_trims_even_below_count_limit():
    messages = history(20)
    result = process_chat_history_after_cycle(
        messages, max_context_size=300, max_context_tokens=16000, prompt_tokens=16000,
    )
    assert result.history == messages[-5:]
    assert result.removed_messages == messages[:-5]


def test_count_mode_does_not_trim_on_token_usage():
    messages = history(20)
    result = process_chat_history_after_cycle(messages, max_context_size=300, prompt_tokens=1000000)
    assert result.history == messages
    assert result.removed_messages == []
