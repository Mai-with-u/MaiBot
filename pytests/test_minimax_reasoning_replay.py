import asyncio
import json
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI

from src.llm_models.model_client.openai_client import OpenaiClient, _convert_messages
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


def test_minimaxi_flash_dispatch_preserves_reasoning_at_http_boundary() -> None:
    async def run() -> None:
        captured = []

        def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured.append((str(request.url), payload))
            return httpx.Response(
                200,
                json={
                    "id": "offline",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                },
            )

        items = build_portable_output_items(
            content="Checking.",
            reasoning="Keep this reasoning.",
            tool_calls=[ContextToolCall.create(call_id="call-1", func_name="lookup", args={})],
            logical_turn_id="turn-1",
        )
        for hostname in ("api.minimaxi.com", "custom.invalid"):
            for model in ("MiniMax-M3.1-Flash", "MiniMax-M2.7", "custom-model"):
                base_url = f"https://{hostname}/v1"
                request = SimpleNamespace(
                    model_info=SimpleNamespace(model_identifier=model, name=model, force_stream_mode=False),
                    context_items=items,
                    tool_options=[SimpleNamespace(name="lookup", description="lookup", parameters_schema={})],
                    response_format=None,
                    extra_params={},
                    temperature=None,
                    max_tokens=4096,
                    async_response_parser=True,
                    interrupt_flag=None,
                )
                async with AsyncOpenAI(
                    api_key="offline-test",
                    base_url=base_url,
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                ) as client:
                    instance = SimpleNamespace(
                        client=client,
                        api_provider=SimpleNamespace(base_url=base_url, organization=None, project=None),
                        _build_request_headers=lambda headers: headers,
                    )
                    await OpenaiClient._execute_response_request(
                        instance, request, None, lambda response: (SimpleNamespace(), None)
                    )
                url, payload = captured[-1]
                assert url == f"{base_url}/chat/completions"
                assert payload["model"] == model
                assistant = payload["messages"][0]
                assert assistant["content"] == "Checking."
                assert assistant["tool_calls"][0]["id"] == "call-1"
                expected = "Keep this reasoning." if hostname == "api.minimaxi.com" and model == "MiniMax-M3.1-Flash" else None
                assert assistant.get("reasoning_content") == expected

    asyncio.run(run())
