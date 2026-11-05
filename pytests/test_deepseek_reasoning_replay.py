from typing import Any, Dict, List
import json

import httpx
import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client import openai_client
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.payload_content.context_item import ContextItemBuilder, ContextItemMeta, FunctionCallOutputItem
from src.llm_models.payload_content.tool_option import ToolOption


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_deepseek_replays_native_reasoning_only_to_the_same_model_with_tools(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    requests: List[Dict[str, Any]] = []
    first_reasoning = "  Decide which document to read.\n"
    second_reasoning = "\nAnswer from the retrieved document. "

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        first_call = len(requests) == 1
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": None if first_call else "Here is the answer.",
            "reasoning_content": first_reasoning if first_call else second_reasoning,
        }
        if first_call:
            message["tool_calls"] = [
                {
                    "id": "call_doc",
                    "type": "function",
                    "function": {"name": "read_document", "arguments": "{}"},
                }
            ]
        finish_reason = "tool_calls" if first_call else "stop"
        response_base = {"id": "chat-1", "created": 0, "model": "deepseek-flash"}
        if not stream:
            return httpx.Response(
                200,
                json={
                    **response_base,
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                },
            )
        native_reasoning = message.pop("reasoning_content")
        for index, tool_call in enumerate(message.get("tool_calls", [])):
            tool_call["index"] = index
        chunks = [
            {"index": 0, "delta": {"reasoning_content": native_reasoning}, "finish_reason": None},
            {"index": 0, "delta": message, "finish_reason": finish_reason},
        ]
        body = "".join(
            "data: " + json.dumps({**response_base, "object": "chat.completion.chunk", "choices": [choice]}) + "\n\n"
            for choice in chunks
        )
        return httpx.Response(200, text=body + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(
        openai_client,
        "DefaultAsyncHttpxClient",
        lambda **_: httpx.AsyncClient(transport=httpx.MockTransport(handle_request)),
    )
    provider = APIProvider(name="DeepSeek", base_url="https://api.deepseek.com/v1", reasoning_parse_mode="none")
    model = ModelInfo(
        name="flash",
        model_type="llm",
        model_identifier="deepseek-flash",
        api_provider=provider.name,
        force_stream_mode=stream,
    )
    adapter = openai_client.OpenaiClient(provider)
    try:
        question = ContextItemBuilder().add_text_part("Where is the option?").build()
        request = ResponseRequest(
            model_info=model,
            context_items=[question],
            tool_options=[ToolOption(name="read_document", description="Read documentation.")],
        )
        tool_plan = await adapter.get_response(request)
        assert tool_plan.reasoning_content is None
        tool_result = FunctionCallOutputItem(
            meta=ContextItemMeta.create(logical_turn_id=request.logical_turn_id),
            call_id="call_doc",
            output="It is in the model settings.",
        )
        request = request.copy_with(
            context_items=[question, *tool_plan.output_items, tool_result], logical_turn_id="answer"
        )
        answer = await adapter.get_response(request)
        assert requests[1]["messages"][1]["reasoning_content"] == first_reasoning
        assert requests[1]["messages"][2]["tool_call_id"] == "call_doc"

        next_question = ContextItemBuilder().add_text_part("How do I set it?").build()
        request = request.copy_with(
            context_items=[*request.context_items, *answer.output_items, next_question], logical_turn_id="next"
        )
        await adapter.get_response(request)
        assert requests[2]["messages"][3]["reasoning_content"] == second_reasoning

        await adapter.get_response(request.copy_with(tool_options=None))
        await adapter.get_response(
            request.copy_with(model_info=model.model_copy(update={"model_identifier": "deepseek-v4-pro"}))
        )
        assert all("reasoning_content" not in message for sent in requests[3:] for message in sent["messages"])
    finally:
        await adapter.client.close()
