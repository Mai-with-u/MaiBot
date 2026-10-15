from types import SimpleNamespace

import pytest

from src.config.model_configs import APIProvider, ModelInfo, ReasoningParseMode, ToolArgumentParseMode
from src.llm_models.model_client.base_client import RequestTraceContext, ResponseRequest
from src.llm_models.model_client.openai_client import OpenaiClient
from src.llm_models.payload_content.context_item import ContextItemBuilder
import src.llm_models.model_client.openai_client as openai_client


@pytest.mark.asyncio
@pytest.mark.parametrize("stream, override, configured_limit, expected_limit", [
    (False, {}, 256, "256"),
    (True, {"max_completion_tokens": 128}, 256, "128"),
    (False, {"max_completion_tokens": 128}, 256, "128"),
    (False, {}, None, "未指定（由服务商决定）"),
])
async def test_length_truncation_logs_sent_limit_and_task(monkeypatch, stream, override, configured_limit, expected_limit):
    logged = []
    monkeypatch.setattr(openai_client.logger, "info", logged.append)
    provider = APIProvider(name="test-provider", base_url="https://example.com/v1", auth_type="none", client_type="openai")
    client = object.__new__(OpenaiClient)
    client.api_provider = provider
    client.reasoning_parse_mode = ReasoningParseMode.NONE
    client.tool_argument_parse_mode = ToolArgumentParseMode.STRICT
    client.reasoning_key = "reasoning_content"
    client._omit_sdk_authorization = True

    async def create(**kwargs):
        if kwargs["stream"]:
            async def events():
                yield SimpleNamespace(
                    model="test-model", usage=None,
                    choices=[SimpleNamespace(finish_reason="length", delta=SimpleNamespace(content="partial", tool_calls=None))],
                )
            return events()
        return SimpleNamespace(
            model="test-model", usage=None,
            choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="partial", tool_calls=None))],
        )

    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    request = ResponseRequest(
        model_info=ModelInfo(name="test-model", model_identifier="test-model", api_provider="test-provider", force_stream_mode=stream),
        context_items=[ContextItemBuilder().add_text_content("hello").build()],
        max_tokens=configured_limit,
        extra_params={"body": override} if override else {},
        trace_context=RequestTraceContext(task_name="planner", request_type="maisaka.planner"),
    )

    await client.get_response(request)

    assert len(logged) == 1
    assert f"max_tokens={expected_limit}" in logged[0]
    assert "任务=planner" in logged[0]
    assert "请求类型=maisaka.planner" in logged[0]
