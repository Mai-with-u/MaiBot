from src.maisaka.display.prompt_cli_renderer import (
    PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES,
    PromptCLIVisualizer,
)
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    ReasoningItem,
    ReasoningRepresentation,
    UserMessageItem,
)


def test_preview_metadata_keeps_token_usage() -> None:
    metadata = PromptCLIVisualizer._normalize_preview_metadata(
        {
            "model_name": "test-model",
            "duration_ms": 123.456,
            "prompt_tokens": 1200,
            "completion_tokens": 34,
            "total_tokens": 1234,
        }
    )

    assert metadata == {
        "model_name": "test-model",
        "duration_ms": 123.46,
        "prompt_tokens": 1200,
        "completion_tokens": 34,
        "total_tokens": 1234,
    }


def test_reasoning_item_is_displayed_as_independent_item_content() -> None:
    item = ReasoningItem(
        meta=ContextItemMeta.create(),
        text_parts=("独立推理内容",),
        representation=ReasoningRepresentation.RAW_TEXT,
    )

    payload = PromptCLIVisualizer.build_structured_context_item_payload([item], keep_base64=False)

    assert payload[0]["item_type"] == "ReasoningItem"
    assert payload[0]["text_parts"] == ["独立推理内容"]
    assert "reasoning_content" not in payload[0]


def test_structured_preview_keeps_output_items_independent_and_ordered() -> None:
    reasoning = ReasoningItem(
        meta=ContextItemMeta.create(
            logical_turn_id="turn-1",
        ),
        text_parts=("独立推理",),
        representation=ReasoningRepresentation.RAW_TEXT,
    )
    assistant = AssistantMessageItem(
        meta=ContextItemMeta.create(
            logical_turn_id="turn-1",
        ),
        parts=(ContextTextPart("最终正文"),),
    )

    payload = PromptCLIVisualizer._build_structured_preview_payload(
        [UserMessageItem(meta=ContextItemMeta.create(), parts=(ContextTextPart("测试"),))],
        request_kind="planner",
        selection_reason="测试 Item 输出",
        tool_definitions=None,
        output_title="输出结果",
        output_items=(reasoning, assistant),
        metadata=None,
        generation_attempts=({
            "attempt_id": "attempt-1",
            "trace": {
                "provider": "test-provider",
                "endpoint": "https://api.example.com/v1",
                "model": "test-model",
                "response_id": "resp-test",
                "status": "completed",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_cache_hit_tokens": 4,
                "prompt_cache_miss_tokens": 6,
                "output_item_ids": [reasoning.meta.item_id, assistant.meta.item_id],
            },
        },),
        keep_base64=False,
    )

    assert [item["item_type"] for item in payload["output_items"]] == [
        "ReasoningItem",
        "AssistantMessageItem",
    ]
    assert [item["meta"]["logical_turn_id"] for item in payload["output_items"]] == ["turn-1", "turn-1"]
    assert all("ordinal" not in item["meta"] for item in payload["output_items"])
    assert all("response_group_id" not in item["meta"] for item in payload["output_items"])
    assert payload["generation_attempts"][0]["trace"]["output_item_ids"] == [
        reasoning.meta.item_id,
        assistant.meta.item_id,
    ]
    assert payload["metadata"]["prompt_tokens"] == 10
    assert payload["metadata"]["completion_tokens"] == 5
    assert payload["metadata"]["total_tokens"] == 15


def test_structured_preview_extracts_responses_usage_when_trace_is_zero() -> None:
    payload = PromptCLIVisualizer._build_structured_preview_payload(
        [],
        request_kind="planner",
        selection_reason="Responses usage",
        tool_definitions=None,
        output_title="输出结果",
        output_items=(),
        metadata={"model_name": "responses-model"},
        generation_attempts=(
            {
                "trace": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "wire_response": {
                    "usage": {"input_tokens": 120, "output_tokens": 30}
                },
            },
        ),
        keep_base64=False,
    )

    assert payload["metadata"]["prompt_tokens"] == 120
    assert payload["metadata"]["completion_tokens"] == 30
    assert payload["metadata"]["total_tokens"] == 150


def test_structured_preview_prefers_explicit_token_metadata() -> None:
    payload = PromptCLIVisualizer._build_structured_preview_payload(
        [],
        request_kind="replyer",
        selection_reason="显式用量",
        tool_definitions=None,
        output_title="输出结果",
        output_items=(),
        metadata={"prompt_tokens": 10, "completion_tokens": 2},
        generation_attempts=(
            {
                "trace": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        ),
        keep_base64=False,
    )

    assert payload["metadata"]["prompt_tokens"] == 10
    assert payload["metadata"]["completion_tokens"] == 2
    assert payload["metadata"]["total_tokens"] == 12


def test_structured_preview_accepts_serialized_reasoning_item_snapshots() -> None:
    reasoning = ReasoningItem(
        meta=ContextItemMeta.create(
            logical_turn_id="turn-1",
        ),
        text_parts=("独立推理",),
        representation=ReasoningRepresentation.RAW_TEXT,
    )
    request_items = PromptCLIVisualizer.build_structured_context_item_payload(
        [reasoning],
        keep_base64=False,
    )

    payload = PromptCLIVisualizer._build_structured_preview_payload(
        request_items,
        request_kind="replyer",
        selection_reason="测试序列化 Item 输入",
        tool_definitions=None,
        output_title="输出结果",
        output_items=(
            AssistantMessageItem(
                meta=ContextItemMeta.create(logical_turn_id="turn-2"),
                parts=(ContextTextPart("最终正文"),),
            ),
        ),
        metadata=None,
        generation_attempts=(),
        keep_base64=False,
    )

    assert payload["request_items"] == request_items
    assert payload["request_items"][0]["item_type"] == "ReasoningItem"


def test_structured_prompt_omits_replay_secrets_and_large_base64() -> None:
    small_base64 = "YWJjZA=="
    large_base64 = "A" * ((PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES * 4 // 3) + 8)
    provider_response = {
        "id": "resp_test",
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_test",
                "summary": [{"type": "summary_text", "text": "先检索再回复"}],
                "encrypted_content": small_base64,
            },
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "最终回答"}],
                "large_blob": large_base64,
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }

    payload = PromptCLIVisualizer._build_structured_preview_payload(
        [UserMessageItem(meta=ContextItemMeta.create(), parts=(ContextTextPart("测试"),))],
        request_kind="planner",
        selection_reason="测试完整响应",
        tool_definitions=None,
        output_title="输出结果",
        output_items=(
            AssistantMessageItem(
                meta=ContextItemMeta.create(logical_turn_id="turn-2"),
                parts=(ContextTextPart("最终回答"),),
            ),
        ),
        metadata={"model_name": "test-model"},
        generation_attempts=({
            "attempt_id": "attempt-1",
            "wire_response": provider_response,
        },),
        keep_base64=False,
    )

    assert payload["schema_version"] == 6
    assert "messages" not in payload
    assert "output" not in payload
    assert payload["request_items"][0]["item_type"] == "UserMessageItem"
    assert payload["output_items"][0]["item_type"] == "AssistantMessageItem"
    assert "provider_response" not in payload
    stored_response = payload["generation_attempts"][0]["wire_response"]
    assert stored_response["id"] == "resp_test"
    assert stored_response["output"][0]["summary"][0]["text"] == "先检索再回复"
    assert stored_response["output"][0]["encrypted_content"] == "[仅在内存 replay fragment 中保留]"
    omitted_blob = stored_response["output"][1]["large_blob"]
    assert omitted_blob["type"] == "omitted_binary"
    assert omitted_blob["base64_omitted"] is True
    assert omitted_blob["size_bytes"] > PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES
    assert len(omitted_blob["sha256"]) == 64
    assert stored_response["usage"]["total_tokens"] == 30
