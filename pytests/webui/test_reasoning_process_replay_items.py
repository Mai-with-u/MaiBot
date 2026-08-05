from typing import Any

import pytest

from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    FunctionCallItem,
    FunctionCallOutputItem,
    ReasoningItem,
)
from src.webui.routers.reasoning_process import (
    _deserialize_replay_items,
    _extract_action_preview_from_json_payload,
    _extract_output_text_from_json_payload,
)


def _meta(item_id: str, *, include_deprecated_fields: bool = False) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "item_id": item_id,
        "logical_turn_id": "turn-1",
        "timestamp": "2026-08-05T00:00:00",
    }
    if include_deprecated_fields:
        meta.update({"response_group_id": "legacy-group", "ordinal": 99})
    return meta


def test_deserialize_replay_items_preserves_flat_item_types_and_ignores_legacy_group_fields() -> None:
    raw_items = [
        {
            "item_type": "ReasoningItem",
            "meta": _meta("reasoning-1", include_deprecated_fields=True),
            "representation": "raw_text",
            "summary_parts": [],
            "text_parts": ["先思考"],
        },
        {
            "item_type": "AssistantMessageItem",
            "meta": _meta("assistant-1", include_deprecated_fields=True),
            "parts": [{"type": "text", "text": "再回答"}],
        },
        {
            "item_type": "FunctionCallItem",
            "meta": _meta("call-1"),
            "tool_call": {
                "call_id": "tool-call-1",
                "func_name": "lookup",
                "args": {"key": "value"},
                "extra_content": {},
            },
        },
        {
            "item_type": "FunctionCallOutputItem",
            "meta": _meta("call-output-1"),
            "call_id": "tool-call-1",
            "output": "ok",
            "success": True,
            "tool_name": "lookup",
        },
    ]

    items = _deserialize_replay_items(raw_items)

    assert isinstance(items[0], ReasoningItem)
    assert isinstance(items[1], AssistantMessageItem)
    assert isinstance(items[2], FunctionCallItem)
    assert isinstance(items[3], FunctionCallOutputItem)
    assert [item.meta.item_id for item in items] == [
        "reasoning-1",
        "assistant-1",
        "call-1",
        "call-output-1",
    ]
    assert all(item.meta.logical_turn_id == "turn-1" for item in items)
    assert all(not hasattr(item.meta, "response_group_id") for item in items)
    assert all(not hasattr(item.meta, "ordinal") for item in items)


def test_deserialize_replay_items_accepts_independent_ordinary_item() -> None:
    raw_items = [
        {
            "item_type": "AssistantMessageItem",
            "meta": _meta("assistant-1"),
            "parts": [{"type": "text", "text": "独立 Item"}],
        }
    ]

    items = _deserialize_replay_items(raw_items)

    assert len(items) == 1
    assert isinstance(items[0], AssistantMessageItem)


def test_deserialize_replay_items_rejects_orphan_tool_output() -> None:
    raw_items = [
        {
            "item_type": "FunctionCallOutputItem",
            "meta": _meta("call-output-1"),
            "call_id": "missing-call",
            "output": "orphan",
            "success": True,
            "tool_name": "lookup",
        }
    ]

    with pytest.raises(ValueError, match="孤儿"):
        _deserialize_replay_items(raw_items)


def test_item_first_list_previews_derive_text_and_actions_from_output_items() -> None:
    payload = {
        "schema_version": 5,
        "output_items": [
            {
                "item_type": "ReasoningItem",
                "meta": _meta("reasoning-1"),
                "representation": "raw_text",
                "summary_parts": ["不应作为正文摘要"],
                "text_parts": [],
            },
            {
                "item_type": "ProviderActivityItem",
                "meta": _meta("provider-1"),
                "provider_type": "web_search",
                "display_summary": "搜索完成",
            },
            {
                "item_type": "FunctionCallItem",
                "meta": _meta("call-1"),
                "tool_call": {"call_id": "reply-1", "func_name": "reply", "args": {}},
            },
            {
                "item_type": "AssistantMessageItem",
                "meta": _meta("assistant-1"),
                "parts": [{"type": "text", "text": "最终\n可见回复"}],
            },
        ],
    }

    assert _extract_output_text_from_json_payload(payload) == "最终 可见回复"
    assert _extract_action_preview_from_json_payload(payload) == "动作：web_search、reply"
