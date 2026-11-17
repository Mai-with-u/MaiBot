from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import json
import pytest

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.llm_models.exceptions import RespParseException
from src.llm_models.payload_content.context_item import (
    ContextItemBuilder, ContextItemMeta, ContextToolCall, FunctionCallItem, RoleType,
)
from src.llm_models.payload_content.tool_option import ToolOption
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
    tools = [ToolOption(name="reply", description="reply description", parameters_schema_override=schema),
             ToolOption(name="wait", description="wait description", parameters_schema_override={"type": "object"})]
    monkeypatch.setattr(module, "load_prompt", lambda _: "judge instruction")
    result = LLMResponseResult(output_items=(FunctionCallItem(
        meta=ContextItemMeta.create(logical_turn_id="judge-turn"),
        tool_call=ContextToolCall.create(call_id="native_call", func_name="reply", args=arguments),
    ),), model_name="test", prompt_tokens=1, completion_tokens=1)
    generate = AsyncMock(return_value=result)
    monkeypatch.setattr(module, "LLMServiceClient", lambda *a, **kw: SimpleNamespace(generate_response_with_context=generate))
    items = [ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("Planner text").build()]

    async def run():
        call = module.complete_missing_planner_action(items, tools, session_id="session", logical_turn_id="turn")
        if valid:
            judgment = await call
            assert judgment.tool_call.tool_call.materialize_args() == arguments
            assert judgment.tool_call.meta.logical_turn_id == "turn"
            assert judgment.metadata["action"] == "reply"
        else:
            with pytest.raises(RespParseException):
                await call
        messages = generate.call_args.kwargs["context_factory"](None)
        payload = json.loads(messages[1].parts[0].text)
        assert generate.call_args.kwargs["options"].tool_options == tools
        assert payload["planner_text"] == "Planner text"
    asyncio.run(run())


@pytest.mark.parametrize("action", ["reply", "wait"])
@pytest.mark.parametrize("invalid_call", [
    '<{action}><session_id>invalid-session</session_id></{action}>',
    '<{action} session_id="real-session"></{action}>',
])
def test_judge_reuses_text_tool_parsing_and_retry(monkeypatch, action, invalid_call):
    """通过真实 LLM 重试入口验证坏调用重试、XML 恢复及 Focus 路由参数。"""
    from src.llm_models import utils_model
    from src.llm_models.model_client.base_client import APIResponse, ResponseRequest

    schema = {
        "type": "object",
        "properties": {"session_id": {"type": "string", "enum": ["real-session"]}},
        "required": ["session_id"],
    }
    tools = [ToolOption(name=name, description=name, parameters_schema_override=schema)
             for name in ("reply", "wait")]
    # 首次返回非法参数或格式，重试才返回合法调用。
    texts = [invalid_call.format(action=action),
             f'<{action}><session_id>real-session</session_id></{action}>']
    responses = [APIResponse(output_items=(
        ContextItemBuilder().set_role(RoleType.Assistant)
        .add_text_content(text).build(),
    )) for text in texts]
    provider_client = SimpleNamespace(get_response=AsyncMock(side_effect=responses))
    provider = SimpleNamespace(max_retry=2, retry_interval=0, name="test", client_type="test")
    orchestrator = object.__new__(utils_model.LLMOrchestrator)
    orchestrator.task_name = "fast_model"
    orchestrator.request_type = "maisaka.planner_action_judge"
    monkeypatch.setattr(orchestrator, "_record_success_generation_attempt", lambda **kw: None)
    monkeypatch.setattr(orchestrator, "_schedule_llm_retry_event", lambda **kw: None)
    monkeypatch.setattr(orchestrator, "_get_original_error_info", lambda _: "")
    monkeypatch.setattr(utils_model, "has_request_snapshot", lambda _: True)
    monkeypatch.setattr(utils_model, "update_failed_request_attempt", lambda *a, **kw: None)
    monkeypatch.setattr(utils_model, "mark_request_succeeded", lambda *a, **kw: None)
    monkeypatch.setattr(module, "load_prompt", lambda _: "judge instruction")

    async def generate(context_factory, options):
        messages = context_factory(None)
        payload = json.loads(messages[1].parts[0].text)
        assert payload["reply_sessions"] == ["real-session"]
        request = ResponseRequest(
            model_info=SimpleNamespace(name="test", model_identifier="test"), context_items=messages,
            tool_options=options.tool_options,
        )
        response = await orchestrator._attempt_request_on_model(provider, provider_client, request)
        return LLMResponseResult(output_items=response.output_items, model_name="test")

    monkeypatch.setattr(module, "LLMServiceClient", lambda *a, **kw: SimpleNamespace(
        generate_response_with_context=generate,
    ))
    items = [ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("Planner text").build()]
    judgment = asyncio.run(module.complete_missing_planner_action(
        items, tools, session_id="session", logical_turn_id="turn",
    ))
    assert provider_client.get_response.await_count == 2
    assert judgment.tool_call.tool_call.func_name == action
    assert judgment.tool_call.tool_call.materialize_args() == {"session_id": "real-session"}

