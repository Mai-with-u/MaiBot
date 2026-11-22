from copy import deepcopy

import json

import httpx
import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.model_client.openai_client import OpenaiClient
from src.llm_models.model_client.openai_responses_client import OpenAIResponsesClient
from src.llm_models.payload_content.context_item import ContextItemMeta, ContextTextPart, UserMessageItem
from src.llm_models.payload_content.tool_option import normalize_tool_option
from src.maisaka.builtin_tool import BUILTIN_TOOL_ENTRIES


def test_official_provider_default_and_explicit_protocol():
    url = "https://api.openai.com/v1/"
    assert APIProvider(base_url=url).client_type == "openai_responses"
    assert APIProvider(base_url=url, client_type="openai").client_type == "openai"
    assert APIProvider(base_url="https://gateway.test/v1").client_type == "openai"


@pytest.mark.asyncio
@pytest.mark.parametrize("responses", [False, True], ids=["chat", "responses"])
@pytest.mark.parametrize(
    ("host", "identifier", "effort", "sampling"),
    [
        ("api.openai.com", "gpt-6.1-terra", None, False),
        ("api.openai.com", "gpt-6.1-terra-2026-10-01", "low", False),
        ("api.openai.com", "gpt-6.1-terra", "none", True),
        ("api.openai.com", "gpt-6.2-sol", "medium", False),
        ("api.openai.com", "gpt-6.2-astra", "low", False),
        ("api.openai.com", "gpt-5-mini", None, False),
        ("api.openai.com", "gpt-5.2", None, True),
        ("api.openai.com", "gpt-5.4", "high", False),
        ("api.openai.com", "gpt-4.1", None, True),
        ("gateway.test", "gpt-6.1-terra", None, True),
    ],
)
async def test_sampling_on_wire(monkeypatch, host, identifier, effort, sampling, responses):
    extra = {"top_p": 0.9, "body": {"top_logprobs": 2}, "stream_options": {"include_usage": True}}
    if responses:
        extra["include"] = ["reasoning.encrypted_content"]
        if host == "api.openai.com":
            extra["logprobs"] = True
        else:
            extra["include"].append("message.output_text.logprobs")
    else:
        extra["logprobs"] = True
        if host == "api.openai.com":
            extra["max_output_tokens"] = 250
    if effort is not None:
        if responses:
            extra["reasoning_effort"] = effort
        else:
            extra["reasoning"] = {"effort": effort}
    original = deepcopy(extra)

    def receive(request):
        assert request.url.path == ("/v1/responses" if responses else "/v1/chat/completions")
        payload = json.loads(request.content)
        assert ("stream_options" in payload) == (host != "api.openai.com")
        for key in ("temperature", "top_p", "top_logprobs"):
            assert (key in payload) == sampling
        if sampling:
            assert payload["temperature"] == 0.0
        if responses:
            assert "logprobs" not in payload
            assert "reasoning.encrypted_content" in payload["include"]
            assert ("message.output_text.logprobs" in payload["include"]) == sampling
            if effort:
                assert payload["reasoning"]["effort"] == effort and "reasoning_effort" not in payload
            message = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}
            return httpx.Response(200, json={"id": "resp-1", "status": "completed", "output": [message]})
        assert ("logprobs" in payload) == sampling
        if host == "api.openai.com":
            assert payload["max_completion_tokens"] == 250 and "max_output_tokens" not in payload
            assert "reasoning" not in payload and payload.get("reasoning_effort") == effort
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as transport:
        monkeypatch.setattr("src.llm_models.model_client.openai_client.DefaultAsyncHttpxClient", lambda **kw: transport)
        provider = APIProvider(name="test", base_url=f"https://{host}/v1", api_key="test")
        model = ModelInfo(model_type="llm", model_identifier=identifier, api_provider="test", name="test")
        client = (OpenAIResponsesClient if responses else OpenaiClient)(provider)
        request = ResponseRequest(
            model,
            [UserMessageItem(ContextItemMeta.create(), (ContextTextPart("hi"),))],
            temperature=0.0,
            extra_params=extra,
        )
        assert (await client.get_response(request)).content == "ok"
    assert extra == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream", "identifier"), [(False, "gpt-6.1-terra"), (True, "gpt-6.1-terra"), (False, "gpt-5.6-luna")]
)
async def test_responses_tools_and_chat_overrides(monkeypatch, stream, identifier):
    definitions = [entry.build_spec(None).to_llm_definition() for entry in BUILTIN_TOOL_ENTRIES]
    definitions.append(
        {
            "name": "plugin_lookup",
            "description": "示例",
            "parameters_schema": {"type": "object", "properties": {"term": {"type": "string"}}},
        }
    )
    tools = [normalize_tool_option(spec) for spec in definitions]
    extra = {
        "body": {"temperature": 0.8, "reasoning": {"effort": "high", "summary": "auto"}},
        "reasoning_effort": "none",
        "max_completion_tokens": 256,
        "stream_options": {"include_usage": True, "include_obfuscation": False},
        "tools": [
            {"type": "web_search"},
            {
                "type": "function",
                "name": "native",
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            },
        ],
    }
    original = deepcopy(extra)

    def receive(request):
        payload = json.loads(request.content)
        assert payload["reasoning"] == {"effort": "high", "summary": "auto"}
        assert payload["max_output_tokens"] == 256
        assert not {"temperature", "reasoning_effort", "max_completion_tokens"} & payload.keys()
        assert payload.get("stream_options") == ({"include_obfuscation": False} if stream else None)
        assert payload["tools"][-2:] == original["tools"]
        functions = {tool["name"]: tool for tool in payload["tools"][:-2]}
        assert set(functions) == {tool.name for tool in tools}
        assert all(tool["strict"] is False for tool in functions.values())
        assert "not" not in functions["schedule"]["parameters"]
        assert functions["schedule"]["parameters"]["required"] == ["action"]
        assert functions["plugin_lookup"]["parameters"] == tools[-1].parameters_schema
        call = {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "schedule",
            "arguments": '{"action":"list"}',
        }
        response = {"id": "resp-1", "status": "completed", "output": [call]}
        if stream:
            event = {"type": "response.completed", "response": response}
            return httpx.Response(
                200,
                text=f"event: response.completed\ndata: {json.dumps(event)}\n\n",
                headers={"Content-Type": "text/event-stream"},
            )
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as transport:
        monkeypatch.setattr("src.llm_models.model_client.openai_client.DefaultAsyncHttpxClient", lambda **kw: transport)
        provider = APIProvider(name="test", base_url="https://api.openai.com/v1", api_key="test")
        model = ModelInfo(
            model_type="llm",
            model_identifier=identifier,
            api_provider="test",
            name="test",
            force_stream_mode=stream,
        )
        request = ResponseRequest(
            model,
            [UserMessageItem(ContextItemMeta.create(), (ContextTextPart("hi"),))],
            tool_options=tools,
            max_tokens=1024,
            extra_params=extra,
        )
        response = await OpenAIResponsesClient(provider).get_response(request)
        assert [(tool.func_name, tool.args) for tool in response.tool_calls] == [("schedule", {"action": "list"})]
    assert extra == original
