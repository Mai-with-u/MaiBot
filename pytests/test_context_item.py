from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    ProviderReplayFragment,
    ProviderScope,
    ReasoningItem,
    ReasoningRepresentation,
    UserMessageItem,
)
from src.llm_models.payload_content.context_protocol import (
    ContextProtocolMode,
    select_context_items_with_protocol_closure,
    validate_context_items,
)
from src.plugin_runtime.hook_payloads import deserialize_prompt_items, serialize_prompt_items


def _meta(
    item_id: str,
    *,
    turn_id: str | None = None,
) -> ContextItemMeta:
    return ContextItemMeta(
        item_id=item_id,
        logical_turn_id=turn_id,
        timestamp=datetime(2026, 8, 5),
    )


def _scope() -> ProviderScope:
    return ProviderScope(
        schema_version=1,
        client_type="openai_responses",
        provider_name="provider",
        endpoint_fingerprint="endpoint",
        model_identifier="gpt-test",
    )


def test_replay_fragment_is_deeply_immutable_and_round_trips() -> None:
    payload = {"type": "reasoning", "summary": [{"type": "summary_text", "text": "摘要"}]}
    fragment = ProviderReplayFragment.from_payload(_scope(), payload)

    payload["summary"][0]["text"] = "外部修改"
    materialized = fragment.materialize()
    materialized["summary"][0]["text"] = "返回值修改"

    assert fragment.materialize()["summary"][0]["text"] == "摘要"
    with pytest.raises(FrozenInstanceError):
        fragment.payload_sha256 = "changed"  # type: ignore[misc]


def test_context_tool_call_is_deeply_immutable() -> None:
    args = {"query": {"keyword": "麦麦"}}
    tool_call = ContextToolCall.create(call_id="call-1", func_name="search", args=args)
    args["query"]["keyword"] = "changed"

    materialized = tool_call.materialize_args()
    materialized["query"]["keyword"] = "again"

    assert tool_call.materialize_args() == {"query": {"keyword": "麦麦"}}


def test_item_list_uses_position_as_only_order_and_validates_tool_relations() -> None:
    reasoning = ReasoningItem(
        meta=_meta("reasoning", turn_id="turn-1"),
        summary_parts=("摘要",),
        representation=ReasoningRepresentation.SUMMARY,
    )
    message = AssistantMessageItem(
        meta=_meta("message", turn_id="turn-1"),
        parts=(ContextTextPart("正文"),),
    )

    items = (reasoning, message)
    validate_context_items(items, ContextProtocolMode.REQUEST_CONTEXT)
    assert items == (reasoning, message)
    assert tuple(reversed(items)) == (message, reasoning)
    assert select_context_items_with_protocol_closure(items, ("message",)) == (message,)

    with pytest.raises(ValueError, match="重复 item_id"):
        validate_context_items((reasoning, reasoning), ContextProtocolMode.REQUEST_CONTEXT)


def test_protocol_closure_keeps_complete_logical_tool_turn() -> None:
    reasoning = ReasoningItem(
        meta=_meta("reasoning", turn_id="turn-1"),
        text_parts=("分析",),
        representation=ReasoningRepresentation.RAW_TEXT,
    )
    call = FunctionCallItem(
        meta=_meta("call", turn_id="turn-1"),
        tool_call=ContextToolCall.create(call_id="call-1", func_name="search", args={}),
    )
    output = FunctionCallOutputItem(
        meta=_meta("output", turn_id="turn-1"),
        call_id="call-1",
        output="结果",
    )
    user = UserMessageItem(meta=_meta("user"), parts=(ContextTextPart("问题"),))
    items = (user, reasoning, call, output)

    selected = select_context_items_with_protocol_closure(items, ("call",))
    assert selected == (reasoning, call, output)

    selected = select_context_items_with_protocol_closure(items, ("user",))
    assert selected == (user,)


def test_hook_edit_invalidates_only_changed_item_replay() -> None:
    first_replay = ProviderReplayFragment.from_payload(_scope(), {"type": "reasoning", "id": "r1"})
    second_replay = ProviderReplayFragment.from_payload(_scope(), {"type": "message", "id": "m1"})
    reasoning = ReasoningItem(
        meta=_meta("reasoning", turn_id="turn-1"),
        representation=ReasoningRepresentation.OPAQUE,
        replay=first_replay,
    )
    message = AssistantMessageItem(
        meta=_meta("message", turn_id="turn-1"),
        parts=(ContextTextPart("原正文"),),
        replay=second_replay,
    )
    hook_payload = serialize_prompt_items((reasoning, message))
    hook_payload[1]["parts"][0]["text"] = "修改后正文"

    restored = deserialize_prompt_items(hook_payload, original_items=(reasoning, message))

    assert restored[0] is reasoning
    assert restored[0].replay is first_replay
    assert isinstance(restored[1], AssistantMessageItem)
    assert restored[1].replay is None
    assert restored[1].parts == (ContextTextPart("修改后正文"),)


def test_hook_reorder_keeps_each_item_replay() -> None:
    reasoning = ReasoningItem(
        meta=_meta("reasoning", turn_id="turn-1"),
        representation=ReasoningRepresentation.OPAQUE,
        replay=ProviderReplayFragment.from_payload(_scope(), {"type": "reasoning", "id": "r1"}),
    )
    message = AssistantMessageItem(
        meta=_meta("message", turn_id="turn-1"),
        parts=(ContextTextPart("正文"),),
        replay=ProviderReplayFragment.from_payload(_scope(), {"type": "message", "id": "m1"}),
    )

    restored = deserialize_prompt_items(
        list(reversed(serialize_prompt_items((reasoning, message)))),
        original_items=(reasoning, message),
    )

    assert [item.meta.item_id for item in restored] == ["message", "reasoning"]
    assert restored[0].replay is message.replay
    assert restored[1].replay is reasoning.replay


def test_hook_rejects_entire_item_list_when_tool_turn_relationship_is_invalid() -> None:
    call = FunctionCallItem(
        meta=_meta("call", turn_id="turn-1"),
        tool_call=ContextToolCall.create(call_id="call-1", func_name="search", args={}),
    )
    output = FunctionCallOutputItem(
        meta=_meta("output", turn_id="turn-1"),
        call_id="call-1",
        output="结果",
    )
    hook_payload = serialize_prompt_items((call, output))
    hook_payload[1]["meta"]["logical_turn_id"] = "turn-2"

    with pytest.raises(ValueError, match="logical_turn_id 不一致"):
        deserialize_prompt_items(hook_payload, original_items=(call, output))


def test_protocol_modes_distinguish_pending_model_output_and_request_closure() -> None:
    call = FunctionCallItem(
        meta=_meta("call", turn_id="turn-1"),
        tool_call=ContextToolCall.create(call_id="call-1", func_name="search", args={}),
    )

    validate_context_items((call,), ContextProtocolMode.MODEL_OUTPUT)
    validate_context_items(
        (call,),
        ContextProtocolMode.HISTORY,
        pending_call_ids={"call-1"},
    )
    with pytest.raises(ValueError, match="未回答 function call"):
        validate_context_items((call,), ContextProtocolMode.REQUEST_CONTEXT)


@pytest.mark.parametrize(
    ("items", "error_pattern"),
    [
        (
            (
                FunctionCallOutputItem(
                    meta=_meta("output", turn_id="turn-1"),
                    call_id="call-1",
                    output="结果",
                ),
            ),
            "孤儿 function output",
        ),
        (
            (
                FunctionCallOutputItem(
                    meta=_meta("output", turn_id="turn-1"),
                    call_id="call-1",
                    output="结果",
                ),
                FunctionCallItem(
                    meta=_meta("call", turn_id="turn-1"),
                    tool_call=ContextToolCall.create(call_id="call-1", func_name="search", args={}),
                ),
            ),
            "位于调用之前",
        ),
    ],
)
def test_protocol_rejects_orphan_and_misordered_outputs(
    items: tuple[FunctionCallItem | FunctionCallOutputItem, ...],
    error_pattern: str,
) -> None:
    with pytest.raises(ValueError, match=error_pattern):
        validate_context_items(items, ContextProtocolMode.REQUEST_CONTEXT)


def test_tool_items_require_non_empty_logical_turn_id() -> None:
    with pytest.raises(ValueError, match="logical_turn_id"):
        FunctionCallItem(
            meta=_meta("call"),
            tool_call=ContextToolCall.create(call_id="call-1", func_name="search", args={}),
        )
    with pytest.raises(ValueError, match="logical_turn_id"):
        FunctionCallOutputItem(
            meta=_meta("output"),
            call_id="call-1",
            output="结果",
        )


def test_hook_ignores_unknown_and_deprecated_fields_without_invalidating_replay() -> None:
    replay = ProviderReplayFragment.from_payload(_scope(), {"type": "message", "id": "m1"})
    message = AssistantMessageItem(
        meta=_meta("message", turn_id="turn-1"),
        parts=(ContextTextPart("正文"),),
        replay=replay,
    )
    hook_payload = serialize_prompt_items((message,))
    hook_payload[0]["unknown_extension"] = {"kept_by_plugin": True}
    hook_payload[0]["response_group_id"] = "deprecated-group"
    hook_payload[0]["ordinal"] = 99
    hook_payload[0]["meta"]["response_group_id"] = "deprecated-group"
    hook_payload[0]["meta"]["ordinal"] = 99

    restored = deserialize_prompt_items(hook_payload, original_items=(message,))

    assert restored == [message]
    assert restored[0] is message
    assert restored[0].replay is replay


def test_hook_rejects_schema_mismatch_and_non_model_output() -> None:
    message = AssistantMessageItem(
        meta=_meta("message", turn_id="turn-1"),
        parts=(ContextTextPart("正文"),),
    )
    with pytest.raises(ValueError, match="item_schema_version"):
        deserialize_prompt_items(
            serialize_prompt_items((message,)),
            item_schema_version=999,
            original_items=(message,),
        )

    user = UserMessageItem(meta=_meta("user"), parts=(ContextTextPart("非法输出"),))
    with pytest.raises(ValueError, match="非模型输出 Item"):
        deserialize_prompt_items(
            serialize_prompt_items((user,)),
            mode=ContextProtocolMode.MODEL_OUTPUT,
        )
