"""Wire-level SIWC tests. All HTTP is handled by httpx.MockTransport."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import json

import httpx
import pytest
from openai import AsyncOpenAI


@pytest.fixture(scope="module")
def modules(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path_factory.mktemp("chatgpt-instance")))
        from src.llm_models.model_client import chatgpt_client
        from src.llm_models import utils_model

        yield chatgpt_client, utils_model


def provider(module, **changes):
    return module.APIProvider(name="ChatGPT test", client_type="chatgpt", base_url=module.RESOURCE, **changes)


def test_request_contract_tools_and_no_mutation(modules):
    module, _ = modules
    original = {
        "input": [
            {"role": "system", "content": "system"},
            {"type": "function_call", "name": "weather", "call_id": "c", "arguments": "{}"},
        ],
        "model": "test-model",
        "store": True,
        "stream": False,
        "temperature": 0.7,
        "max_output_tokens": 100,
        "tools": [{"type": "function", "name": "weather", "parameters": {"type": "object"}}],
        "extra_body": {"top_p": 0.8, "tool_choice": {"type": "function", "name": "weather"}},
    }
    request = module.prepare_chatgpt_request(original)
    assert request["store"] is False and request["stream"] is True
    assert "temperature" not in request and "max_output_tokens" not in request
    assert "top_p" not in request["extra_body"]
    assert request["input"][0]["role"] == "developer"
    assert request["tools"][0]["type"] == "namespace"
    assert request["tools"][0]["name"] == request["input"][1]["namespace"] == "maibot"
    assert request["extra_body"]["tool_choice"]["namespace"] == "maibot"
    assert original["input"][0]["role"] == "system"


@pytest.mark.parametrize(
    "overrides",
    [
        {"extra_body": {"store": True}},
        {"extra_body": {"input": []}},
        {"extra_headers": {"Authorization": "test-wrong-credential"}},
        {"extra_query": {"api_key": "test-key"}},
        {"tools": [{"type": "file_search"}]},
        {"tools": [{"type": "mcp"}]},
    ],
)
def test_unsupported_overrides_rejected_before_http(modules, overrides):
    module, _ = modules
    with pytest.raises(ValueError):
        module.prepare_chatgpt_request({"input": [], "model": "test-model", **overrides})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal", ["response.completed", "response.incomplete", "response.failed", "disconnect", "http401"]
)
async def test_real_sdk_wire_and_stream_completion(modules, monkeypatch, terminal):
    module, _ = modules
    from src.config.model_configs import ModelInfo
    from src.llm_models.payload_content.context_item import ContextItemMeta, ContextTextPart, SystemMessageItem

    client = module.ChatGPTClient(provider(module))
    await client.client.close()
    captured = []
    response = {
        "id": "resp-test",
        "object": "response",
        "status": "completed",
        "created_at": 1,
        "model": "test-model",
        "output": [
            {
                "type": "message",
                "id": "m",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hello", "annotations": []}],
            }
        ],
    }

    def transport(request):
        captured.append(request)
        if terminal == "http401":
            return httpx.Response(401, json={"error": {"message": "test-secret-must-not-escape"}})
        events = [{"type": "response.output_text.delta", "delta": "partial"}]
        if terminal != "disconnect":
            events.append({"type": terminal, "response": response})
        sse = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)

    client.client = AsyncOpenAI(
        api_key="unused",
        base_url=module.RESOURCE,
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False),
    )
    monkeypatch.setattr(client.accounts, "access_token", lambda label: "test-app-owned-access")
    monkeypatch.setattr(client, "_attach_failure_snapshot", Mock())
    request = module.ResponseRequest(
        model_info=ModelInfo(name="test", model_identifier="test-model", api_provider="ChatGPT test", model_type="llm"),
        context_items=[SystemMessageItem(ContextItemMeta.create(), (ContextTextPart("be concise"),))],
        temperature=1,
        max_tokens=100,
    )
    try:
        if terminal == "response.completed":
            result = await client.get_response(request)
            assert result.wire_protocol == "responses"
        else:
            with pytest.raises(module.ReqAbortException) as error:
                await client.get_response(request)
            assert "test-secret" not in str(error.value)
            assert "test-app-owned-access" not in str(error.value)
        assert len(captured) == 1
        sent = captured[0]
        assert str(sent.url) == module.RESOURCE + "/responses"
        assert sent.headers["authorization"] == "Bearer test-app-owned-access"
        body = json.loads(sent.content)
        assert body["stream"] is True and body["store"] is False
        assert body["input"][0]["role"] == "developer"
        assert "temperature" not in body and "max_output_tokens" not in body
    finally:
        await client.client.close()


@pytest.mark.asyncio
async def test_oauth_failure_does_not_send_placeholder_token(modules, monkeypatch):
    module, _ = modules
    client = module.ChatGPTClient(provider(module))

    def no_grant(label):
        raise RuntimeError("test-secret")

    monkeypatch.setattr(client.accounts, "access_token", no_grant)
    send = AsyncMock()
    monkeypatch.setattr(client.client.responses, "create", send)
    try:
        with pytest.raises(module.ReqAbortException):
            await client.get_response(None)
        send.assert_not_called()
    finally:
        await client.client.close()


@pytest.mark.asyncio
async def test_hard_timeout_never_advances_to_paid_candidate(modules):
    module, utils = modules
    model = SimpleNamespace(name="test")
    orchestrator = SimpleNamespace(
        model_for_task=SimpleNamespace(model_list=["test", "paid-fallback"]),
        task_name="replyer",
        request_type="test",
        _resolve_effective_session_id=lambda value: value,
        _select_model=AsyncMock(return_value=(model, provider(module), None)),
        _build_client_request=Mock(return_value=None),
        _adjust_model_usage=Mock(),
        _attempt_request_on_model_with_timeout=AsyncMock(side_effect=utils.ModelAttemptFailed("mock timeout")),
    )
    with pytest.raises(module.ReqAbortException):
        await utils.LLMOrchestrator._execute_request(orchestrator, utils.RequestType.RESPONSE)
    assert orchestrator._select_model.await_count == 1


@pytest.mark.asyncio
async def test_replay_is_scoped_to_selected_registration(modules):
    module, _ = modules
    first = module.ChatGPTClient(provider(module, chatgpt_account="first"))
    second = module.ChatGPTClient(provider(module, chatgpt_account="second"))
    try:
        assert first._replay_provider_name() != second._replay_provider_name()
        assert first._replay_provider_name() != first.api_provider.name
    finally:
        await first.client.close()
        await second.client.close()
