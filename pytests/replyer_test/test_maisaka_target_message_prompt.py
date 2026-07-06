from datetime import datetime
from typing import Any

from src.chat.message_receive.message import SessionMessage
from src.chat.replyer.maisaka_expression_selector import MaisakaExpressionSelector
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.config.config import global_config
from src.maisaka.context.messages import SessionBackedMessage


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


def build_message(
    user_id: str,
    user_nickname: str,
    text: str,
    *,
    message_id: str = "556928467",
    user_cardname: str = "",
) -> SessionMessage:
    message = SessionMessage(message_id=message_id, timestamp=datetime(2026, 7, 4, 12, 52, 31), platform="qq")
    message.message_info = MessageInfo(
        user_info=UserInfo(user_id=user_id, user_nickname=user_nickname, user_cardname=user_cardname)
    )
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


def test_build_final_user_message_mentions_rich_reply_attachments() -> None:
    generator = build_generator()
    target_message = build_message(
        user_id="20002",
        user_nickname="可乐",
        user_cardname="可乐群名片",
        text="看图",
        message_id="msg-1",
    )
    another_message = build_message(
        user_id="30003",
        user_nickname="雪糕",
        text="我也看看",
        message_id="msg-2",
    )
    chat_history = [
        SessionBackedMessage.from_session_message(
            another_message,
            raw_message=another_message.raw_message,
            visible_text=another_message.processed_plain_text,
        )
    ]

    final_user_message = generator._build_final_user_message(
        chat_history=chat_history,
        reply_message=target_message,
        reply_reason="回复一下",
        reply_tool_args={
            "attach_pic": [{"msg_id": "msg-1", "index": 1}],
            "attach_at": ["msg-1", "msg-2"],
            "attach_emoji": "开心",
        },
    )

    assert "【额外发送内容参考】" in final_user_message
    assert "除了当前你输出的回复，你还会（由另一个模型控制）发送图片可乐群名片 的消息 msg_id=msg-1 中的第 2 张图片。" in final_user_message
    assert "除了当前你输出的回复，你还会（由另一个模型控制）at @可乐群名片 和 @雪糕。" in final_user_message
    assert "除了当前你输出的回复，你还会（由另一个模型控制）发送一个 开心 表情包。" in final_user_message


def test_build_final_user_message_prefers_guide_and_reference_info_over_reason() -> None:
    generator = build_generator()
    target_message = build_message(
        user_id="20002",
        user_nickname="可乐",
        text="刚才那个人是谁",
        message_id="msg-1",
    )

    final_user_message = generator._build_final_user_message(
        chat_history=[],
        reply_message=target_message,
        reply_reason="planner 推理：随便猜一个关系",
        reply_guide="先澄清对方问的是群里的小张。",
        reply_tool_args={
            "reference_info": "小张和可乐是同事，不是情侣；昨天在群里一起排查过部署问题。",
        },
    )

    assert "回复指引：\n先澄清对方问的是群里的小张。" in final_user_message
    assert "关键信息参考：\n小张和可乐是同事，不是情侣；昨天在群里一起排查过部署问题。" in final_user_message
    assert "planner 推理：随便猜一个关系" not in final_user_message


def test_expression_selector_query_prefers_guide_and_reference_info_over_reason() -> None:
    query_text = MaisakaExpressionSelector._build_expression_query_text(
        reply_reason="planner 推理：随便猜一个关系",
        reply_tool_args={
            "reply_guide": "先澄清对方问的是群里的小张。",
            "reference_info": "小张和可乐是同事，不是情侣。",
        },
        use_expression_intent=False,
    )

    assert "回复指引：\n先澄清对方问的是群里的小张。" in query_text
    assert "关键信息参考：\n小张和可乐是同事，不是情侣。" in query_text
    assert "planner 推理：随便猜一个关系" not in query_text
