from __future__ import annotations

from types import SimpleNamespace

import asyncio

import numpy as np
import pytest

from A_memorix.core.embedding import api_adapter as api_adapter_module
from A_memorix.core.embedding.api_adapter import EmbeddingAPIAdapter
from A_memorix.core.utils.runtime_self_check import run_embedding_runtime_self_check


class _FakeEmbeddingClient:
    def __init__(self, *, natural_dimension: int = 12) -> None:
        self.natural_dimension = int(natural_dimension)
        self.requests = []

    async def get_embedding(self, request):
        self.requests.append(request)
        requested_dimension = request.extra_params.get("dimensions")
        if requested_dimension is None:
            requested_dimension = request.extra_params.get("output_dimensionality")
        dimension = int(requested_dimension or self.natural_dimension)
        return SimpleNamespace(embedding=[1.0] * dimension)


def _build_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client_type: str,
    configured_dimension: int = 1024,
    effective_dimension: int | None = None,
    dimension_request_mode: str = "explicit",
    model_extra_params: dict | None = None,
):
    adapter = EmbeddingAPIAdapter(
        default_dimension=configured_dimension,
        dimension_request_mode=dimension_request_mode,
    )
    if effective_dimension is not None:
        adapter._dimension = int(effective_dimension)
        adapter._dimension_detected = True

    fake_client = _FakeEmbeddingClient()
    model_info = SimpleNamespace(
        name="embedding-model",
        api_provider="provider-1",
        model_identifier="embedding-model-id",
        extra_params=dict(model_extra_params or {}),
    )
    provider = SimpleNamespace(name="provider-1", client_type=client_type, base_url="https://example.test")

    monkeypatch.setattr(adapter, "_resolve_candidate_model_names", lambda: ["embedding-model"])
    monkeypatch.setattr(adapter, "_find_model_info", lambda model_name: model_info)
    monkeypatch.setattr(adapter, "_find_provider", lambda provider_name: provider)
    monkeypatch.setattr(
        api_adapter_module.client_registry,
        "get_client_class_instance",
        lambda api_provider, force_new=True: fake_client,
    )
    adapter._last_configuration_key = adapter._dimension_cache_key()
    return adapter, fake_client


def test_auto_embedding_fingerprint_uses_resolved_candidate_model(monkeypatch):
    adapter = EmbeddingAPIAdapter(default_dimension=8, model_name="auto")
    adapter._dimension = 8
    adapter._dimension_detected = True
    model_info_by_name = {
        "embedding-model": SimpleNamespace(
            name="embedding-model", api_provider="provider-1", model_identifier="model-id", extra_params={}
        ),
        "fallback-model": SimpleNamespace(
            name="fallback-model", api_provider="provider-2", model_identifier="fallback-id", extra_params={}
        ),
    }

    monkeypatch.setattr(adapter, "_resolve_candidate_model_names", lambda: ["embedding-model", "fallback-model"])
    monkeypatch.setattr(adapter, "_find_model_info", lambda model_name: model_info_by_name[model_name])

    monkeypatch.setattr(adapter, "_find_provider", lambda name: SimpleNamespace(base_url="https://example.test"))
    cold_fingerprint = adapter.get_embedding_fingerprint(dimension=8)
    adapter._last_success_model_name = "embedding-model"
    adapter._last_success_provider_name = "provider-1"
    observed_fingerprint = adapter.get_embedding_fingerprint(dimension=8)

    assert cold_fingerprint["model"] == "embedding-model"
    assert cold_fingerprint["provider"] == "provider-1"
    assert cold_fingerprint["source"] == "configured"
    assert observed_fingerprint["source"] == "observed"
    assert observed_fingerprint["model"] == "embedding-model"
    assert observed_fingerprint["provider"] == "provider-1"
    assert cold_fingerprint["hash"] == observed_fingerprint["hash"]


@pytest.mark.asyncio
async def test_encode_does_not_send_dimension_by_default_for_openai_provider(monkeypatch):
    adapter, fake_client = _build_adapter(
        monkeypatch,
        client_type="openai",
        configured_dimension=1024,
        effective_dimension=1024,
        model_extra_params={"task_type": "SEMANTIC_SIMILARITY"},
    )

    embedding = await adapter.encode("北塔木梯")

    request = fake_client.requests[-1]
    assert "dimensions" not in request.extra_params
    assert "output_dimensionality" not in request.extra_params
    assert request.extra_params["task_type"] == "SEMANTIC_SIMILARITY"
    assert embedding.shape == (fake_client.natural_dimension,)


@pytest.mark.asyncio
async def test_encode_uses_canonical_dimension_when_request_mode_is_always(monkeypatch):
    adapter, fake_client = _build_adapter(
        monkeypatch,
        client_type="openai",
        configured_dimension=1024,
        effective_dimension=1024,
        dimension_request_mode="always",
        model_extra_params={"task_type": "SEMANTIC_SIMILARITY"},
    )

    embedding = await adapter.encode("北塔木梯")

    request = fake_client.requests[-1]
    assert request.extra_params["dimensions"] == 1024
    assert "output_dimensionality" not in request.extra_params
    assert request.extra_params["task_type"] == "SEMANTIC_SIMILARITY"
    assert embedding.shape == (1024,)


@pytest.mark.asyncio
async def test_encode_explicit_dimension_override_wins(monkeypatch):
    adapter, fake_client = _build_adapter(
        monkeypatch,
        client_type="openai",
        configured_dimension=1024,
        effective_dimension=1024,
    )

    embedding = await adapter.encode("海潮图", dimensions=256)

    request = fake_client.requests[-1]
    assert request.extra_params["dimensions"] == 256
    assert "output_dimensionality" not in request.extra_params
    assert embedding.shape == (256,)


@pytest.mark.asyncio
async def test_encode_maps_dimension_to_gemini_output_dimensionality(monkeypatch):
    adapter, fake_client = _build_adapter(
        monkeypatch,
        client_type="gemini",
        configured_dimension=1024,
        effective_dimension=768,
        dimension_request_mode="always",
    )

    embedding = await adapter.encode("广播站")

    request = fake_client.requests[-1]
    assert request.extra_params["output_dimensionality"] == 768
    assert "dimensions" not in request.extra_params
    assert embedding.shape == (768,)


@pytest.mark.asyncio
async def test_encode_explicit_mode_maps_explicit_dimension_to_gemini(monkeypatch):
    adapter, fake_client = _build_adapter(
        monkeypatch,
        client_type="gemini",
        configured_dimension=1024,
        effective_dimension=768,
    )

    embedding = await adapter.encode("广播站", dimensions=512)

    request = fake_client.requests[-1]
    assert request.extra_params["output_dimensionality"] == 512
    assert "dimensions" not in request.extra_params
    assert embedding.shape == (512,)


@pytest.mark.asyncio
async def test_encode_never_mode_ignores_explicit_dimension(monkeypatch):
    adapter, fake_client = _build_adapter(
        monkeypatch,
        client_type="openai",
        configured_dimension=1024,
        effective_dimension=1024,
        dimension_request_mode="never",
    )

    embedding = await adapter.encode("不改维度", dimensions=256)

    request = fake_client.requests[-1]
    assert "dimensions" not in request.extra_params
    assert "output_dimensionality" not in request.extra_params
    assert embedding.shape == (fake_client.natural_dimension,)


@pytest.mark.asyncio
async def test_encode_does_not_force_dimension_for_unsupported_provider(monkeypatch):
    adapter, fake_client = _build_adapter(
        monkeypatch,
        client_type="custom",
        configured_dimension=1024,
        effective_dimension=640,
        model_extra_params={
            "dimensions": 999,
            "output_dimensionality": 888,
            "custom_flag": "keep-me",
        },
    )

    embedding = await adapter.encode("蓝漆铁盒")

    request = fake_client.requests[-1]
    assert "dimensions" not in request.extra_params
    assert "output_dimensionality" not in request.extra_params
    assert request.extra_params["custom_flag"] == "keep-me"
    assert embedding.shape == (fake_client.natural_dimension,)


@pytest.mark.asyncio
async def test_runtime_self_check_reports_requested_dimension_without_explicit_override():
    class _FakeEmbeddingManager:
        def __init__(self) -> None:
            self.detected_dimension = 384
            self.encode_calls = []

        async def _detect_dimension(self) -> int:
            return self.detected_dimension

        def get_requested_dimension(self) -> int:
            return self.detected_dimension

        async def encode(self, text):
            self.encode_calls.append(text)
            return np.ones(self.detected_dimension, dtype=np.float32)

    manager = _FakeEmbeddingManager()

    report = await run_embedding_runtime_self_check(
        config={"embedding": {"dimension": 1024}},
        vector_store=SimpleNamespace(dimension=384),
        embedding_manager=manager,
    )

    assert report["ok"] is True
    assert report["configured_dimension"] == 1024
    assert report["requested_dimension"] == 384
    assert report["detected_dimension"] == 384
    assert report["encoded_dimension"] == 384
    assert manager.encode_calls == ["A_Memorix runtime self check"]


@pytest.mark.asyncio
async def test_encode_batch_keeps_batch_local_indexes_when_cache_hits_previous_batch(monkeypatch):
    adapter = EmbeddingAPIAdapter(default_dimension=4, enable_cache=True)
    adapter._dimension = 4
    adapter._dimension_detected = True

    async def fake_detect_dimension() -> int:
        return 4

    async def fake_get_embedding_direct(text: str, dimensions: int | None = None):
        del dimensions
        base = float(ord(str(text)[0]))
        return [base, base + 1.0, base + 2.0, base + 3.0]

    monkeypatch.setattr(adapter, "_detect_dimension", fake_detect_dimension)
    monkeypatch.setattr(adapter, "_get_embedding_direct", fake_get_embedding_direct)

    embeddings = await adapter.encode(["A", "B", "A", "C"], batch_size=2)

    assert embeddings.shape == (4, 4)
    assert np.array_equal(embeddings[0], embeddings[2])
    assert embeddings[1][0] == float(ord("B"))
    assert embeddings[3][0] == float(ord("C"))


@pytest.mark.asyncio
async def test_failed_batch_finishes_inflight_calls_and_caches_successes(monkeypatch):
    adapter = EmbeddingAPIAdapter(default_dimension=4, enable_cache=True)
    monkeypatch.setattr(EmbeddingAPIAdapter, "_GLOBAL_TEXT_EMBEDDING_CACHE", {})
    calls = []
    async def encode(text, dimensions=None):
        calls.append(text)
        if text == "bad" and calls.count("bad") == 1:
            return None
        await asyncio.sleep(0)
        return [1.0] * 4
    monkeypatch.setattr(adapter, "_get_embedding_direct", encode)
    with pytest.raises(RuntimeError):
        await adapter._encode_batch_internal(["bad", "good"], batch_size=2, dimensions=4)
    vectors = await adapter._encode_batch_internal(["bad", "good"], batch_size=2, dimensions=4)
    assert vectors.shape == (2, 4)
    assert calls.count("bad") == 2
    assert calls.count("good") == 1
