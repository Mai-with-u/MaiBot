from datetime import datetime
from types import SimpleNamespace
from typing import List

import pytest

from src.chat.message_receive.message import SessionMessage
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType, get_item_text
from src.maisaka.context.compaction import ContextSummaryMessage
from src.maisaka.context.messages import (
    ComplexSessionMessage,
    ContentContextMessage,
    InternalContextMessage,
    LLMContextMessage,
    ModelOutputContextMessage,
    ReferenceMessage,
    SessionBackedMessage,
    ToolResultMediaMessage,
    ToolResultMessage,
)


def _build_mixed_history() -> List[LLMContextMessage]:
    timestamp = datetime(2026, 10, 19, 12)
    history: List[LLMContextMessage] = []
    for index, (message_type, source, text) in enumerate([
        (SessionBackedMessage, "user", "真实用户发言"),
        (SessionBackedMessage, "guided_reply", "机器人已发送回复"),
        (ComplexSessionMessage, "user", "真实转发消息"),
    ]):
        sequence = MessageSequence([TextComponent(text)])
        original = SessionMessage(message_id=str(index), timestamp=timestamp, platform="test")
        history.append(message_type(
            raw_message=sequence,
            visible_text=text,
            timestamp=timestamp,
            message_id=str(index),
            original_message=original,
            source_kind=source,
        ))
        if isinstance(history[-1], ComplexSessionMessage):
            history[-1].prompt_text = text
    for source in (
        "subagent_result", "plugin_proactive:example", "focus_at_wakeup",
        "focus_cooldown_wakeup", "focus_switch", "internal", "future_internal_type",
    ):
        history.append(InternalContextMessage(
            raw_message=MessageSequence([TextComponent(source)]),
            visible_text=source,
            timestamp=timestamp,
            source_kind=source,
        ))
    history.extend([
        ContentContextMessage(
            raw_message=MessageSequence([TextComponent("非 IM 内容")]),
            visible_text="非 IM 内容", timestamp=timestamp,
        ),
        ToolResultMediaMessage(
            raw_message=MessageSequence([TextComponent("工具媒体")]),
            visible_text="工具媒体", timestamp=timestamp, tool_call_id="call-1",
        ),
        ContextSummaryMessage(content="记忆摘要", timestamp=timestamp, remaining_uses_value=None),
        ReferenceMessage(content="参考资料", timestamp=timestamp),
        ToolResultMessage(
            content="工具结果", timestamp=timestamp, tool_call_id="call-1", logical_turn_id="turn-1",
        ),
        ModelOutputContextMessage(
            output_item=ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("Planner 内部输出").build(),
        ),
    ])
    for message_type, text in (
        (SessionBackedMessage, "[事件-群消息表情回应] 用户对消息表达了赞"),
        (SessionBackedMessage, "平台通知：戳一戳、撤回、禁言、入群退群"),
        (ComplexSessionMessage, "未来平台的复杂通知"),
    ):
        original = SessionMessage(message_id=f"notice-{len(history)}", timestamp=timestamp, platform="test")
        original.is_notify = True
        notification = message_type(
            original_message=original,
            message_id=original.message_id,
            timestamp=timestamp,
            raw_message=MessageSequence([TextComponent(text)]),
            visible_text=text,
        )
        if isinstance(notification, ComplexSessionMessage):
            notification.prompt_text = text
        history.append(notification)
    return history


def test_common_filter_only_keeps_real_chat_messages() -> None:
    history = _build_mixed_history()
    assert [
        message for message in history
        if BaseMaisakaReplyGenerator._should_keep_replyer_history_message(message)
    ] == history[:3]


@pytest.mark.parametrize("mai_replier", [False, True], ids=["reply", "mai-replier"])
@pytest.mark.parametrize("visual", [False, True], ids=["text", "visual"])
def test_request_history_excludes_internal_messages(monkeypatch, mai_replier: bool, visual: bool) -> None:
    generator = object.__new__(BaseMaisakaReplyGenerator)
    history = _build_mixed_history()
    generator.chat_stream = SimpleNamespace(session_id="test", is_group_session=True)
    # 隔离模板与展示依赖，保留真实入口的过滤与图文组装。
    monkeypatch.setattr(generator, "_select_reply_prompt_name", lambda **kwargs: "test")
    monkeypatch.setattr(generator, "_build_personality_prompt", lambda: "测试人设")
    monkeypatch.setattr(generator, "_build_chat_attention_block", lambda session_id: "")
    monkeypatch.setattr(generator, "_build_reply_attachment_prompt", lambda **kwargs: "")
    monkeypatch.setattr(generator, "_build_reply_template_context", lambda **kwargs: {
        "dialogue_prompt": "\n".join(message.processed_plain_text for message in kwargs["chat_history"]),
    })
    monkeypatch.setattr(
        generator, "_load_prompt",
        lambda name, **kwargs: kwargs["identity"] if "identity" in kwargs else kwargs["dialogue_prompt"],
    )
    monkeypatch.setattr(generator, "_render_reply_history_line", lambda message, history_by_id: message.processed_plain_text)
    monkeypatch.setattr(generator, "_prepare_reply_visual_history", lambda messages: (messages, {}))
    items = generator._build_request_messages(
        chat_history=history, reply_message=None, reply_reason="", enable_visual_message=visual,
        prompt_profile="mai_replier" if mai_replier else "reply",
    )

    text = "\n".join(get_item_text(item) for item in items)
    for message in history[:3]:
        assert message.processed_plain_text in text
    for message in history[3:]:
        assert message.processed_plain_text not in text
    # 过滤不能删除或改写 Planner 共享历史里的内部上下文。
    assert history[3].source_kind == "subagent_result"
    assert len(history) == 19
    assert all(message.original_message.is_notify for message in history[-3:])
