"""百炼/豆包原生图片嵌入协议自动适配的合约测试。

通过 httpx.MockTransport 走真实 AsyncOpenAI 请求链路，验证绝对 URL 拼接、
鉴权头、请求体结构与响应解析；不发起真实网络请求。
"""

from types import SimpleNamespace
from typing import Any, Callable

import base64
import json

import httpx
import pytest
from openai import AsyncOpenAI

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.exceptions import NetworkConnectionError, RespNotOkException
from src.llm_models.model_client.base_client import ImageEmbeddingRequest
from src.llm_models.model_client.image_embedding_protocols import (
    build_native_image_embedding_diagnostic_payload,
    build_native_image_embedding_fingerprint,
    build_native_image_embedding_request,
    parse_native_image_embedding_response,
    resolve_native_image_embedding_protocol,
)
from src.llm_models.model_client.openai_client import OpenaiClient

DASHSCOPE_NATIVE_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
)
ARK_PLAN_NATIVE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3/embeddings/multimodal"
ARK_NORMAL_NATIVE_URL = "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"


def _build_provider(base_url: str) -> APIProvider:
    return APIProvider(
        name="test-provider",
        base_url=base_url,
        api_key="test-key",
        auth_type="bearer",
        client_type="openai",
    )


def _build_model(model_identifier: str) -> ModelInfo:
    return ModelInfo(name="test-model", model_identifier=model_identifier, api_provider="test-provider")


def _build_request(model_identifier: str, extra_params: dict[str, Any] | None = None) -> ImageEmbeddingRequest:
    return ImageEmbeddingRequest(
        model_info=_build_model(model_identifier),
        image_bytes=b"image-bytes",
        mime_type="image/png",
        preprocess_version="identity_v1",
        extra_params=extra_params or {},
    )


def _expected_data_uri() -> str:
    return f"data:image/png;base64,{base64.b64encode(b'image-bytes').decode('ascii')}"


def _make_openai_client(provider: APIProvider, handler: Callable[[httpx.Request], httpx.Response]) -> OpenaiClient:
    client = object.__new__(OpenaiClient)
    client.api_provider = provider
    client.client = AsyncOpenAI(
        api_key="test-key",
        base_url=provider.base_url,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1/", "dashscope"),
        ("https://dashscope.aliyuncs.com/api/v1", "dashscope"),
        ("https://ark.cn-beijing.volces.com/api/v3", "ark"),
        ("https://ark.cn-beijing.volces.com/api/plan/v3/", "ark"),
        ("https://fake.example.com/v1", None),
        # 路径不匹配官方基路径时不猜测协议
        ("https://dashscope.aliyuncs.com/compatible-mode/v2", None),
        ("https://dashscope.aliyuncs.com/api/v2", None),
        ("https://evil.com/https://dashscope.aliyuncs.com/api/v1", None),
        ("", None),
    ],
)
def test_resolve_native_image_embedding_protocol_matches_official_urls(base_url: str, expected: str | None) -> None:
    assert resolve_native_image_embedding_protocol(base_url) == expected


@pytest.mark.asyncio
async def test_dashscope_compatible_mode_url_routes_to_native_protocol() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "output": {"embeddings": [{"index": 0, "type": "vl", "embedding": [0.25, -0.5]}]},
                "usage": {"input_tokens": 3, "image_tokens": 100, "total_tokens": 103},
                "request_id": "req-1",
            },
        )

    provider = _build_provider("https://dashscope.aliyuncs.com/compatible-mode/v1")
    client = _make_openai_client(provider, handler)
    try:
        request = _build_request("qwen3-vl-embedding", {"parameters": {"dimension": 1024}})
        response = await client.get_image_embedding(request)
    finally:
        await client.client.close()

    # 兼容层地址自动改写到原生端点，鉴权与模型参数按原生协议携带
    assert captured["url"] == DASHSCOPE_NATIVE_URL
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == "qwen3-vl-embedding"
    assert captured["body"]["input"]["contents"][0]["image"] == _expected_data_uri()
    assert captured["body"]["parameters"] == {"dimension": 1024}

    assert response.embedding == [0.25, -0.5]
    assert response.usage is not None
    # 百炼 input_tokens 只含文本部分，统计以 total_tokens 为准
    assert response.usage.prompt_tokens == 103
    assert response.usage.total_tokens == 103
    assert response.usage.completion_tokens == 0
    assert response.request_protocol_hash


@pytest.mark.asyncio
async def test_ark_plan_v3_url_routes_to_native_protocol_with_dimensions() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "created": 1752133360,
                "data": {"embedding": [0.1, 0.2], "object": "embedding"},
                "id": "emb-1",
                "model": "doubao-embedding-vision-251215",
                "object": "list",
                "usage": {"prompt_tokens": 25, "total_tokens": 25},
            },
        )

    provider = _build_provider("https://ark.cn-beijing.volces.com/api/plan/v3")
    client = _make_openai_client(provider, handler)
    try:
        request = _build_request(
            "doubao-embedding-vision-251215",
            {"dimensions": 1024, "instructions": "Instruction:Compress the image into one word."},
        )
        response = await client.get_image_embedding(request)
    finally:
        await client.client.close()

    # 套餐网关的图片嵌入端点挂在套餐基路径下，必须原样保留，不能改写到普通 API
    assert captured["url"] == ARK_PLAN_NATIVE_URL
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == "doubao-embedding-vision-251215"
    assert captured["body"]["input"][0]["type"] == "image_url"
    assert captured["body"]["input"][0]["image_url"]["url"] == _expected_data_uri()
    assert captured["body"]["encoding_format"] == "float"
    assert captured["body"]["dimensions"] == 1024
    assert "instructions" in captured["body"]

    assert response.embedding == [0.1, 0.2]
    assert response.usage is not None
    assert response.usage.total_tokens == 25
    assert response.request_protocol_hash


@pytest.mark.asyncio
async def test_dashscope_dimensions_normalize_to_parameters_dimension() -> None:
    request = build_native_image_embedding_request(
        "dashscope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_identifier="qwen3-vl-embedding",
        image_bytes=b"image-bytes",
        mime_type="image/png",
        extra_params={"dimensions": 1024},
    )
    assert request.payload["parameters"] == {"dimension": 1024}

    with pytest.raises(ValueError, match="冲突"):
        build_native_image_embedding_request(
            "dashscope",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_identifier="qwen3-vl-embedding",
            image_bytes=b"image-bytes",
            mime_type="image/png",
            extra_params={"parameters": {"dimension": 2048}, "dimensions": 1024},
        )


@pytest.mark.parametrize(
    ("protocol", "reserved_key"),
    [
        ("dashscope", "model"),
        ("dashscope", "input"),
        ("ark", "model"),
        ("ark", "input"),
        ("ark", "encoding_format"),
    ],
)
def test_native_payload_rejects_reserved_key_conflicts(protocol: str, reserved_key: str) -> None:
    with pytest.raises(ValueError, match="保留字段"):
        build_native_image_embedding_request(
            protocol,
            base_url="https://example.com/v1",
            model_identifier="test-model-id",
            image_bytes=b"image-bytes",
            mime_type="image/png",
            extra_params={reserved_key: "user-override"},
        )


@pytest.mark.parametrize(
    ("base_url", "expected_endpoint"),
    [
        # 套餐网关必须保留自己的基路径；普通网关在 /api/v3 下派生
        ("https://ark.cn-beijing.volces.com/api/v3", ARK_NORMAL_NATIVE_URL),
        ("https://ark.cn-beijing.volces.com/api/plan/v3", ARK_PLAN_NATIVE_URL),
        # 百炼兼容层地址统一改写到 /api/v1 原生路径；原生地址派生结果一致
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", DASHSCOPE_NATIVE_URL),
        ("https://dashscope.aliyuncs.com/api/v1", DASHSCOPE_NATIVE_URL),
    ],
)
def test_native_endpoint_url_derives_from_provider_base_url(base_url: str, expected_endpoint: str) -> None:
    protocol = resolve_native_image_embedding_protocol(base_url)
    assert protocol is not None
    request = build_native_image_embedding_request(
        protocol,
        base_url=base_url,
        model_identifier="test-model-id",
        image_bytes=b"image-bytes",
        mime_type="image/png",
    )
    assert request.endpoint_url == expected_endpoint


@pytest.mark.asyncio
async def test_explicit_template_takes_precedence_on_official_url() -> None:
    captured: dict[str, Any] = {}

    class FakeEmbeddings:
        async def create(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.5])], usage=None)

    # 显式配置模板时保留用户定制，不切换原生协议
    client = object.__new__(OpenaiClient)
    client.api_provider = _build_provider("https://dashscope.aliyuncs.com/compatible-mode/v1")
    client.client = SimpleNamespace(embeddings=FakeEmbeddings())
    request = _build_request("qwen3-vl-embedding", {"image_embedding_input": "{data_uri}"})

    response = await client.get_image_embedding(request)

    assert captured["input"] == _expected_data_uri()
    assert response.embedding == [0.5]
    # 旧模板路径不产生新指纹，保持既有索引指纹算法不变
    assert response.request_protocol_hash is None


@pytest.mark.asyncio
async def test_unknown_domain_without_template_still_requires_template() -> None:
    client = object.__new__(OpenaiClient)
    client.api_provider = _build_provider("https://example.com/v1")
    request = _build_request("test-model-id")

    with pytest.raises(ValueError, match="必须配置 Provider"):
        await client.get_image_embedding(request)


@pytest.mark.asyncio
async def test_native_protocol_requires_https_transport() -> None:
    client = object.__new__(OpenaiClient)
    client.api_provider = _build_provider("http://dashscope.aliyuncs.com/api/v1")
    request = _build_request("qwen3-vl-embedding")

    with pytest.raises(ValueError, match="必须使用 HTTPS"):
        await client.get_image_embedding(request)


@pytest.mark.asyncio
async def test_native_protocol_maps_http_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "InvalidApiKey", "message": "Invalid API-key provided."})

    provider = _build_provider("https://dashscope.aliyuncs.com/api/v1")
    client = _make_openai_client(provider, handler)
    try:
        with pytest.raises(RespNotOkException) as exc_info:
            await client.get_image_embedding(_build_request("qwen3-vl-embedding"))
    finally:
        await client.client.close()

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_native_protocol_maps_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    provider = _build_provider("https://ark.cn-beijing.volces.com/api/v3")
    client = _make_openai_client(provider, handler)
    try:
        with pytest.raises(NetworkConnectionError):
            await client.get_image_embedding(_build_request("doubao-embedding-vision-251215"))
    finally:
        await client.client.close()


@pytest.mark.parametrize(
    ("protocol", "raw_response"),
    [
        # 百炼缺少 output.embeddings
        ("dashscope", {"output": {}, "usage": None}),
        ("dashscope", {"output": {"embeddings": []}}),
        # 豆包 data 是单个对象而非列表；缺失或列表形态都应报解析错误
        ("ark", {"data": {}}),
        ("ark", {"data": [{"embedding": [0.1]}]}),
        # 向量包含非数值项
        ("dashscope", {"output": {"embeddings": [{"embedding": ["oops"]}]}}),
        ("ark", {"data": {"embedding": [None]}}),
    ],
)
@pytest.mark.asyncio
async def test_native_protocol_rejects_malformed_responses(protocol: str, raw_response: Any) -> None:
    with pytest.raises(ValueError):
        parse_native_image_embedding_response(protocol, raw_response)


def test_native_fingerprint_changes_with_semantic_params_only() -> None:
    def _build(dimension: int) -> Any:
        return build_native_image_embedding_request(
            "dashscope",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_identifier="qwen3-vl-embedding",
            image_bytes=b"image-bytes",
            mime_type="image/png",
            extra_params={"parameters": {"dimension": dimension}},
        )

    first = _build(1024)
    again = _build(1024)
    other_dimension = _build(2048)
    other_image = build_native_image_embedding_request(
        "dashscope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_identifier="qwen3-vl-embedding",
        image_bytes=b"other-image",
        mime_type="image/png",
        extra_params={"parameters": {"dimension": 1024}},
    )

    assert build_native_image_embedding_fingerprint(first, "qwen3-vl-embedding") == (
        build_native_image_embedding_fingerprint(again, "qwen3-vl-embedding")
    )
    assert build_native_image_embedding_fingerprint(first, "qwen3-vl-embedding") != (
        build_native_image_embedding_fingerprint(other_dimension, "qwen3-vl-embedding")
    )
    # 图片内容不参与向量空间指纹
    assert build_native_image_embedding_fingerprint(first, "qwen3-vl-embedding") == (
        build_native_image_embedding_fingerprint(other_image, "qwen3-vl-embedding")
    )


@pytest.mark.parametrize("protocol", ["dashscope", "ark"])
def test_diagnostic_payload_omits_image_data(protocol: str) -> None:
    native_request = build_native_image_embedding_request(
        protocol,
        base_url="https://example.com/v1",
        model_identifier="test-model-id",
        image_bytes=b"secret-image-payload",
        mime_type="image/png",
    )

    diagnostic = build_native_image_embedding_diagnostic_payload(native_request.payload)
    serialized = json.dumps(diagnostic, ensure_ascii=False)

    assert base64.b64encode(b"secret-image-payload").decode("ascii") not in serialized
    assert "<data URI 已省略" in serialized
