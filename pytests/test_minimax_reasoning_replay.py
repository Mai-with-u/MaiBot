from src.llm_models.model_client.openai_client import _convert_messages
from src.llm_models.payload_content.context_item import ContextToolCall, build_portable_output_items


def test_flash_reasoning_is_preserved_with_tool_calls() -> None:
    items = build_portable_output_items(
        content="Checking the result.",
        reasoning="Keep the complete reasoning.",
        tool_calls=[ContextToolCall.create(call_id="call-1", func_name="lookup", args={})],
        logical_turn_id="turn-1",
    )

    message = _convert_messages(list(items), preserve_reasoning=True)[0]

    assert message["reasoning_content"] == "Keep the complete reasoning."
    assert message["content"] == "Checking the result."
    assert message["tool_calls"][0]["id"] == "call-1"
    assert "reasoning_content" not in _convert_messages(list(items))[0]


def test_flash_reasoning_only_history_is_not_dropped() -> None:
    items = build_portable_output_items(reasoning="Continue from here.", logical_turn_id="turn-1")

    assert _convert_messages(list(items), preserve_reasoning=True) == [
        {"role": "assistant", "content": None, "reasoning_content": "Continue from here."}
    ]
    assert _convert_messages(list(items)) == []
