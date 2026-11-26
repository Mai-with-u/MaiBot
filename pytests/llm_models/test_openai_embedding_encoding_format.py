import array
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai.types.create_embedding_response import CreateEmbeddingResponse
from openai.types.embedding import Embedding

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import EmbeddingRequest
from src.llm_models.model_client.openai_client import OpenaiClient


def _build_client() -> OpenaiClient:
    provider = APIProvider(
        name="test",
        base_url="https://example.invalid/v1",
        api_key="sk-test",
    )
    return OpenaiClient(provider)


def _build_request(extra_params: dict | None = None) -> EmbeddingRequest:
    model_info = ModelInfo(
        name="embedding-model",
        model_type="embedding",
        model_identifier="embedding-model",
        api_provider="test",
    )
    return EmbeddingRequest(
        model_info=model_info,
        embedding_input="hello",
        extra_params=extra_params or {},
    )


def _mock_response() -> SimpleNamespace:
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2])],
        usage=None,
    )


@pytest.mark.asyncio
async def test_embedding_defaults_to_float_encoding_format() -> None:
    client = _build_client()
    create_mock = AsyncMock(return_value=_mock_response())
    client.client.embeddings.create = create_mock

    await client._execute_embedding_request(_build_request())

    kwargs = create_mock.await_args.kwargs
    assert kwargs["encoding_format"] == "float"
    extra_body = kwargs.get("extra_body") or {}
    assert "encoding_format" not in extra_body


@pytest.mark.asyncio
async def test_embedding_respects_top_level_encoding_format() -> None:
    client = _build_client()
    create_mock = AsyncMock(return_value=_mock_response())
    client.client.embeddings.create = create_mock

    await client._execute_embedding_request(_build_request({"encoding_format": "base64"}))

    kwargs = create_mock.await_args.kwargs
    assert kwargs["encoding_format"] == "base64"
    extra_body = kwargs.get("extra_body") or {}
    assert "encoding_format" not in extra_body


@pytest.mark.asyncio
async def test_embedding_other_extra_params_flow_into_extra_body() -> None:
    client = _build_client()
    create_mock = AsyncMock(return_value=_mock_response())
    client.client.embeddings.create = create_mock

    await client._execute_embedding_request(_build_request({"dimensions": 1024, "user": "u1"}))

    kwargs = create_mock.await_args.kwargs
    assert kwargs["encoding_format"] == "float"
    assert "dimensions" not in kwargs
    extra_body = kwargs.get("extra_body") or {}
    assert extra_body.get("dimensions") == 1024
    assert extra_body.get("user") == "u1"


@pytest.mark.asyncio
async def test_embedding_base64_response_is_decoded_to_floats() -> None:
    values = [0.5, -0.25, 1.0]
    payload = base64.b64encode(array.array("f", values).tobytes()).decode("ascii")
    raw = CreateEmbeddingResponse.model_construct(
        data=[Embedding.model_construct(embedding=payload, index=0, object="embedding")],
        model="embedding-model",
        object="list",
        usage=None,
    )
    client = _build_client()
    create_mock = AsyncMock(return_value=raw)
    client.client.embeddings.create = create_mock

    response, _ = await client._execute_embedding_request(_build_request({"encoding_format": "base64"}))

    kwargs = create_mock.await_args.kwargs
    assert kwargs["encoding_format"] == "base64"
    assert isinstance(response.embedding, list)
    assert all(isinstance(value, float) for value in response.embedding)
    assert response.embedding == pytest.approx(values)
