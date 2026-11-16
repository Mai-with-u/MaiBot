from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import json
import pytest

from src.llm_models.exceptions import RespParseException
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType
from src.maisaka import planner_action_judge as module


@pytest.mark.parametrize("arguments,valid", [
    ({"chat_anlyze": "facts", "reply_style": "长回复", "plugin_options": {"enabled": True}}, True),
    ({"reply_style": "长回复"}, False),
    ({"chat_anlyze": "facts", "reply_style": "invalid"}, False),
    ({"chat_anlyze": "facts", "unknown": True}, False),
])
def test_judge_uses_live_schema_and_preserves_arguments(monkeypatch, arguments, valid):
    schema = {
        "type": "object",
        "properties": {
            "chat_anlyze": {"type": "string"},
            "reply_style": {"type": "string", "enum": ["长回复"]},
            "plugin_options": {"type": "object"},
        },
        "required": ["chat_anlyze"],
    }
    tools = [SimpleNamespace(name="reply", description="reply description", parameters_schema=schema),
             SimpleNamespace(name="wait", description="wait description", parameters_schema={"type": "object"})]
    monkeypatch.setattr(module, "normalize_tool_options", lambda _: tools)
    monkeypatch.setattr(module, "load_prompt", lambda _: "judge instruction")
    result = SimpleNamespace(response=json.dumps({"action": "reply", "arguments": arguments}),
                             tool_calls=[], native_tool_calls=[], model_name="test", prompt_tokens=1,
                             completion_tokens=1)
    generate = AsyncMock(return_value=result)
    monkeypatch.setattr(module, "LLMServiceClient", lambda *a, **kw: SimpleNamespace(generate_response_with_context=generate))
    items = [ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("Planner text").build()]

    async def run():
        call = module.complete_missing_planner_action(items, [], session_id="session", logical_turn_id=None)
        if valid:
            judgment = await call
            assert judgment.tool_call.tool_call.materialize_args() == arguments
        else:
            with pytest.raises(RespParseException):
                await call
        messages = generate.call_args.kwargs["context_factory"](None)
        payload = json.loads(messages[1].parts[0].text)
        assert payload["tools"][0]["parameters"] == schema
        assert payload["planner_text"] == "Planner text"
    asyncio.run(run())

