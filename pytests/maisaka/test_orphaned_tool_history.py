from datetime import datetime

from src.llm_models.payload_content.context_item import (
    ContextItemMeta, ContextToolCall, FunctionCallItem, FunctionCallOutputItem,
)
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.context.messages import ReferenceMessage, ToolResultMessage, build_model_output_context_messages


def test_request_discards_tool_turn_without_preceding_user_anchor():
    stamp = datetime(2026, 10, 1, 12)
    call = FunctionCallItem(
        meta=ContextItemMeta.create(logical_turn_id="orphan", timestamp=stamp),
        tool_call=ContextToolCall.create(call_id="call-orphan", func_name="test_tool"),
    )
    history = [
        *build_model_output_context_messages([call]),
        ToolResultMessage(content="done", timestamp=stamp, tool_call_id="call-orphan", logical_turn_id="orphan"),
        ReferenceMessage(content="new user message", timestamp=stamp, remaining_uses_value=None),
    ]
    service = MaisakaChatLoopService(chat_system_prompt="system")
    items, _ = service._build_request_messages(history, enable_visual_message=False)
    assert not any(isinstance(item, (FunctionCallItem, FunctionCallOutputItem)) for item in items)
    assert len(history) == 1
