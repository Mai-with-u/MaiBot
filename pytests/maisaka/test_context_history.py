from datetime import datetime

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    ForwardComponent,
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    ReasoningItem,
    ReasoningRepresentation,
)
from src.maisaka.context.history import (
    drop_unanswered_tool_calls,
    normalize_tool_call_result_pairs,
    normalize_tool_result_order,
)
from src.maisaka.context.inbound_factory import (
    SessionInboundFlags,
    build_session_inbound_message,
    build_session_inbound_message_sync,
)
from src.maisaka.context.messages import (
    ComplexSessionMessage,
    ModelOutputContextMessage,
    SessionBackedMessage,
    ToolResultMessage,
)
from src.maisaka.context.planner_messages import build_planner_user_prefix_from_session_message
from src.maisaka.context.post_processor import (
    _build_trimmed_assistant_tool_user_message,
    _trim_history_to_context_target,
)
from src.maisaka.chat_loop_service import MaisakaChatLoopService


def _meta(item_id: str, logical_turn_id: str = "turn-1") -> ContextItemMeta:
    return ContextItemMeta.create(
        item_id=item_id,
        logical_turn_id=logical_turn_id,
    )


def _call(
    item_id: str,
    call_id: str,
    logical_turn_id: str = "turn-1",
) -> ModelOutputContextMessage:
    return ModelOutputContextMessage(
        output_item=FunctionCallItem(
            meta=_meta(item_id, logical_turn_id),
            tool_call=ContextToolCall.create(
                call_id=call_id,
                func_name="lookup",
                args={"call_id": call_id},
            ),
        )
    )


def _result(call_id: str, logical_turn_id: str = "turn-1") -> ToolResultMessage:
    return ToolResultMessage(
        content=f"result:{call_id}",
        timestamp=datetime.now(),
        tool_call_id=call_id,
        tool_name="lookup",
        logical_turn_id=logical_turn_id,
    )


def _user(content: str) -> SessionBackedMessage:
    return SessionBackedMessage(
        raw_message=MessageSequence([TextComponent(content)]),
        visible_text=content,
        timestamp=datetime.now(),
    )


def test_normalize_tool_result_order_keeps_parallel_calls_together() -> None:
    first_call = _call("call-item-1", "call-1")
    second_call = _call("call-item-2", "call-2")
    first_result = _result("call-1")
    second_result = _result("call-2")

    normalized, moved_count = normalize_tool_result_order(
        [first_call, second_call, second_result, first_result]
    )

    assert normalized == [first_call, second_call, first_result, second_result]
    assert moved_count == 2


def test_drop_unanswered_parallel_call_removes_entire_tool_turn() -> None:
    reasoning = ModelOutputContextMessage(
        output_item=ReasoningItem(
            meta=_meta("reasoning"),
            text_parts=("先查询",),
            representation=ReasoningRepresentation.RAW_TEXT,
        )
    )
    answered_call = _call("call-item-1", "call-1")
    unanswered_call = _call("call-item-2", "call-2")
    assistant = ModelOutputContextMessage(
        output_item=AssistantMessageItem(
            meta=_meta("assistant"),
            parts=(ContextTextPart("查询中"),),
        )
    )
    result = _result("call-1")

    filtered, removed_count = drop_unanswered_tool_calls(
        [reasoning, answered_call, unanswered_call, assistant, result]
    )

    assert removed_count == 1
    assert filtered == []


def test_parallel_tool_turn_folding_preserves_call_order_and_stable_id() -> None:
    first_call = _call("call-item-1", "call-1")
    second_call = _call("call-item-2", "call-2")
    folded = _build_trimmed_assistant_tool_user_message(
        [first_call, second_call],
        tool_result_by_call_id={
            "call-2": _result("call-2"),
            "call-1": _result("call-1"),
        },
    )

    assert folded is not None
    assert folded.message_id == "optimized_tool_history:turn-1"
    assert folded.visible_text.index("tool_call_id: call-1") < folded.visible_text.index("tool_call_id: call-2")
    assert folded.visible_text.index("result:call-1") < folded.visible_text.index("result:call-2")


def test_context_selection_keeps_complete_tool_turn_beyond_window() -> None:
    reasoning = ModelOutputContextMessage(
        output_item=ReasoningItem(
            meta=_meta("reasoning"),
            text_parts=("先查询",),
            representation=ReasoningRepresentation.RAW_TEXT,
        )
    )
    assistant = ModelOutputContextMessage(
        output_item=AssistantMessageItem(
            meta=_meta("assistant"),
            parts=(ContextTextPart("查询中"),),
        )
    )
    history = [
        reasoning,
        _call("call-item-1", "call-1"),
        _call("call-item-2", "call-2"),
        assistant,
        _result("call-1"),
        _result("call-2"),
    ]

    selected, selection_reason = MaisakaChatLoopService.select_llm_context_messages(
        history,
        request_kind="planner",
        max_context_size=1,
        enable_visual_message=False,
    )

    assert selected == history
    assert "tool_turn_overflow" in selection_reason


def test_history_trimming_keeps_user_and_following_tool_turn_together() -> None:
    trigger = _user("触发工具调用")
    call = _call("call-item", "call-1")
    result = _result("call-1")
    latest = _user("最新消息")
    history = [trigger, call, result, latest]

    removed = _trim_history_to_context_target(history, target_context_count=2)

    assert removed == [trigger, call, result]
    assert history == [latest]


def test_history_protocol_removes_both_turns_when_call_and_output_turns_mismatch() -> None:
    call = _call("call-item", "call-1", "turn-call")
    result = _result("call-1", "turn-output")

    normalized, stats = normalize_tool_call_result_pairs([call, result])

    assert normalized == []
    assert stats["invalid_tool_turns"] == 2


def test_history_protocol_keeps_registered_pending_call() -> None:
    call = _call("call-item", "wait-call", "turn-wait")

    normalized, stats = normalize_tool_call_result_pairs(
        [call],
        pending_call_ids={"wait-call"},
    )

    assert normalized == [call]
    assert stats["unanswered_tool_calls"] == 0
    assert stats["invalid_tool_turns"] == 0


def _make_session_message(
    *,
    text: str = "你好",
    message_id: str = "m1",
    raw_message: MessageSequence | None = None,
    processed: str | None = None,
) -> SessionMessage:
    message = SessionMessage(
        message_id=message_id,
        timestamp=datetime(2026, 8, 20, 1, 9, 30),
        platform="test",
    )
    message.message_info = MessageInfo(
        user_info=UserInfo(user_id="u1", user_nickname="用户", user_cardname="群名片"),
    )
    message.raw_message = raw_message or MessageSequence([TextComponent(text)])
    message.session_id = "chat-1"
    message.processed_plain_text = text if processed is None else processed
    message.is_notify = False
    return message


def test_inbound_factory_keeps_planner_prefix_and_raw_wakeup_distinct() -> None:
    message = _make_session_message(processed="原始唤醒正文")
    prefixed = build_session_inbound_message_sync(
        message,
        flags=SessionInboundFlags.planner_ingest(hydrate_visual=False),
    )
    raw = build_session_inbound_message_sync(
        message,
        flags=SessionInboundFlags.raw_focus_wakeup(),
    )

    assert isinstance(prefixed, SessionBackedMessage)
    assert isinstance(raw, SessionBackedMessage)
    expected_prefix = build_planner_user_prefix_from_session_message(message)
    assert isinstance(prefixed.raw_message.components[0], TextComponent)
    assert prefixed.raw_message.components[0].text.startswith(expected_prefix)
    assert raw.raw_message is message.raw_message
    assert raw.visible_text == "原始唤醒正文"
    assert prefixed.visible_text != raw.visible_text


def test_inbound_factory_collapse_flag_does_not_make_paths_identical() -> None:
    forward = ForwardNodeComponent(
        [
            ForwardComponent(
                user_nickname="转发用户",
                message_id="f1",
                content=[TextComponent("转发内容")],
                user_id="fu1",
            )
        ]
    )
    message = _make_session_message(raw_message=MessageSequence([forward]), processed="转发内容")
    collapsed = build_session_inbound_message_sync(
        message,
        flags=SessionInboundFlags.planner_ingest(hydrate_visual=False),
    )
    kept = build_session_inbound_message_sync(
        message,
        flags=SessionInboundFlags.sent_message(),
    )

    assert isinstance(collapsed, ComplexSessionMessage)
    assert isinstance(kept, SessionBackedMessage)
    assert not isinstance(kept, ComplexSessionMessage)
    assert collapsed.prompt_text.startswith("<message")
    assert "[消息类型]转发消息" in collapsed.prompt_text


def test_inbound_factory_plain_text_ingest_and_sent_share_prefix() -> None:
    message = _make_session_message()
    ingest = build_session_inbound_message_sync(
        message,
        flags=SessionInboundFlags.planner_ingest(hydrate_visual=False),
    )
    sent = build_session_inbound_message_sync(
        message,
        flags=SessionInboundFlags.sent_message(),
    )

    assert isinstance(ingest, SessionBackedMessage)
    assert isinstance(sent, SessionBackedMessage)
    assert ingest.raw_message.components[0].text == sent.raw_message.components[0].text
    assert ingest.visible_text == sent.visible_text


@pytest.mark.asyncio
async def test_inbound_factory_visual_hydrate_flag_is_path_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_hashes: list[str] = []

    async def fake_load(self: ImageComponent) -> None:
        loaded_hashes.append(self.binary_hash)

    monkeypatch.setattr(ImageComponent, "load_image_binary", fake_load)
    image = ImageComponent(binary_hash="abc")
    message = _make_session_message(raw_message=MessageSequence([image]), processed="[图片]")

    await build_session_inbound_message(
        message,
        flags=SessionInboundFlags.planner_ingest(hydrate_visual=True),
    )
    assert loaded_hashes == ["abc"]

    loaded_hashes.clear()
    await build_session_inbound_message(
        message,
        flags=SessionInboundFlags.planner_ingest(hydrate_visual=False),
    )
    assert loaded_hashes == []


def test_inbound_factory_sync_rejects_visual_hydrate() -> None:
    message = _make_session_message()
    with pytest.raises(ValueError, match="同步入站构造不能回填视觉二进制数据"):
        build_session_inbound_message_sync(
            message,
            flags=SessionInboundFlags.planner_ingest(hydrate_visual=True),
        )
