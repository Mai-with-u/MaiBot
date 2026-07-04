from datetime import datetime
from typing import Any

from src.chat.message_receive.message import SessionMessage
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.config.config import global_config


class DummyLLMClient:
    def __init__(self, **_: Any) -> None:
        self.task_name = "replyer"


def build_generator() -> BaseMaisakaReplyGenerator:
    return BaseMaisakaReplyGenerator(
        llm_client_cls=DummyLLMClient,
        load_prompt_func=lambda *_args, **_kwargs: "",
        enable_visual_message=False,
        replyer_mode="text",
    )


def build_message(user_id: str, user_nickname: str, text: str) -> SessionMessage:
    message = SessionMessage(message_id="556928467", timestamp=datetime(2026, 7, 4, 12, 52, 31), platform="qq")
    message.message_info = MessageInfo(user_info=UserInfo(user_id=user_id, user_nickname=user_nickname))
    message.raw_message = MessageSequence([TextComponent(text)])
    message.processed_plain_text = text
    return message


def test_build_target_message_block_for_bot_self_message(monkeypatch) -> None:
    monkeypatch.setattr(global_config.bot, "qq_account", "10001")
    monkeypatch.setattr(global_config.bot, "nickname", "麦麦")
    generator = build_generator()

    prompt_block = generator._build_target_message_block(
        build_message(user_id="10001", user_nickname="麦麦", text="再复读tokens要扣光了")
    )

    assert "你想要补充说明你自己（麦麦） 发送的 msg_id为 556928467 的消息" in prompt_block
    assert "不要把你自己的发言当成别人的发言" in prompt_block
    assert "- 你之前的发言内容：再复读tokens要扣光了" in prompt_block
    assert "不要把其他历史消息当成当前回复对象" not in prompt_block


def test_build_target_message_block_for_user_message_keeps_reply_format(monkeypatch) -> None:
    monkeypatch.setattr(global_config.bot, "qq_account", "10001")
    generator = build_generator()

    prompt_block = generator._build_target_message_block(
        build_message(user_id="20002", user_nickname="可乐", text="尝试回复一下")
    )

    assert "你想要回复的消息是 可乐 发送的 msg_id为 556928467 的消息" in prompt_block
    assert "- 发言内容：尝试回复一下" in prompt_block
    assert "你想要补充说明你自己" not in prompt_block
