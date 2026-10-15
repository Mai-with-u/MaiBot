"""OpenAI 兼容客户端各鉴权方式的出站请求头回归测试（#2082）。

通过真实构造的 `OpenaiClient` 与 httpx.MockTransport 走完整 SDK 请求链路。
OpenAI SDK 2.34+ 构造时拒绝空 api_key，鉴权不交给 SDK 的配置曾在构造阶段直接报 Missing credentials。
"""

from typing import Any, Dict, List

import httpx
import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import EmbeddingRequest, ResponseRequest
from src.llm_models.model_client.openai_client import OpenaiClient
from src.llm_models.payload_content.context_item import ContextItemBuilder


def _handler(captured: List[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": "test-model",
                    "data": [{"object": "embedding", "embedding": [0.5], "index": 0}],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            },
        )

    return handle


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_overrides", "expected_authorization", "expected_headers", "expected_query"),
    [
        ({"auth_type": "none", "api_key": ""}, [], {}, {}),
        (
            {"auth_type": "header", "auth_header_name": "X-Api-Key", "auth_header_prefix": ""},
            [],
            {"x-api-key": "secret"},
            {},
        ),
        ({"auth_type": "query", "auth_query_name": "key"}, [], {}, {"key": "secret"}),
        ({"auth_type": "bearer", "auth_header_prefix": "Token"}, ["Token secret"], {}, {}),
        (
            {"auth_type": "header", "auth_header_name": "authorization", "auth_header_prefix": ""},
            ["secret"],
            {},
            {},
        ),
        ({"auth_type": "bearer"}, ["Bearer secret"], {}, {}),
    ],
)
async def test_auth_type_controls_outbound_credentials(
    provider_overrides: Dict[str, Any],
    expected_authorization: List[str],
    expected_headers: Dict[str, str],
    expected_query: Dict[str, str],
) -> None:
    provider = APIProvider(
        **{
            "name": "test-provider",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "secret",
            "client_type": "openai",
            **provider_overrides,
        }
    )
    captured: List[httpx.Request] = []
    client = OpenaiClient(provider)
    client.client = client.client.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler(captured)))
    )
    model_info = ModelInfo(name="test-model", model_identifier="test-model", api_provider="test-provider")
    try:
        await client.get_response(
            ResponseRequest(model_info=model_info, context_items=[ContextItemBuilder().add_text_content("hi").build()])
        )
        await client.get_embedding(EmbeddingRequest(model_info=model_info, embedding_input="hi"))
    finally:
        await client.client.close()

    assert [request.url.path for request in captured] == ["/v1/chat/completions", "/v1/embeddings"]
    for request in captured:
        # 占位密钥生成的 Authorization 头不能出站，自定义 Authorization 头也只能发出一份
        assert request.headers.get_list("authorization") == expected_authorization
        for name, value in expected_headers.items():
            assert request.headers[name] == value
        assert dict(request.url.params) == expected_query
