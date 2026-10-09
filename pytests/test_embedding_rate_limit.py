from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import asyncio

import pytest

from src.llm_models.exceptions import RespNotOkException
from src.llm_models.model_client import embedding_rate_limit as rate_module
from src.llm_models.model_client import adapter_base as adapter_module
from src.llm_models.model_client.base_client import APIResponse, EmbeddingRequest, RequestTraceContext
from src.llm_models.model_client.openai_client import OpenaiClient
from src.llm_models.utils_model import LLMOrchestrator
from src.llm_models import utils_model


@pytest.mark.asyncio
async def test_halves_twice_and_deduplicates_inflight_errors(monkeypatch):
    limiter = rate_module.EmbeddingRateLimit()
    limiter._times.extend([0.0, 1.0])
    clock = [3.0]

    async def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(rate_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(rate_module, "asyncio", SimpleNamespace(sleep=sleep))
    generation, _ = await limiter.wait()
    limiter.reduce(generation)
    limiter.reduce(generation)
    assert limiter.halvings == 1
    assert limiter.interval == 2.0
    generation, waited = await limiter.wait()
    assert waited == 2.0
    limiter.reduce(generation)
    assert limiter.interval == 4.0
    generation, waited = await limiter.wait()
    assert waited == 4.0
    limiter.reduce(generation)
    assert limiter.halvings == 2
    assert limiter.interval == 4.0


def test_rate_limit_is_shared_across_event_loops():
    limiter = rate_module.EmbeddingRateLimit()
    limiter.interval = 0.01

    async def send():
        for _ in range(3):
            await limiter.wait()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(asyncio.run, send()) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    times = list(limiter._times)
    assert len(times) == 6
    assert all(later - earlier >= 0.01 for earlier, later in zip(times, times[1:], strict=False))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 429, 500])
async def test_clients_share_limit_by_provider_and_actual_model(monkeypatch, status):
    monkeypatch.setattr(rate_module, "_limits", {})
    async def no_wait(delay):
        pass
    monkeypatch.setattr(adapter_module, "asyncio", SimpleNamespace(sleep=no_wait))
    provider = SimpleNamespace(name="provider", base_url="https://example.test/v1")
    model = SimpleNamespace(model_identifier="actual-model", name="alias-one")
    limiter = rate_module.get_embedding_rate_limit(provider.name, provider.base_url, model.model_identifier)
    assert limiter is rate_module.get_embedding_rate_limit(provider.name, provider.base_url + "/", "actual-model")
    assert limiter is not rate_module.get_embedding_rate_limit(provider.name, provider.base_url, "other-model")
    trace = RequestTraceContext()

    attempts = 0
    async def execute(request):
        nonlocal attempts
        attempts += 1
        raise RespNotOkException(status, "arbitrary provider error")

    clients = [object.__new__(OpenaiClient), object.__new__(OpenaiClient)]
    for client in clients:
        client.api_provider = provider
        monkeypatch.setattr(client, "_execute_embedding_request", execute)
        with pytest.raises(RespNotOkException):
            await client.get_embedding(EmbeddingRequest(model, "text", trace_context=trace))
    assert attempts == (2 if status == 400 else 6)
    assert limiter.halvings == (2 if status == 429 else 0)


@pytest.mark.asyncio
async def test_wait_time_accumulates_across_failed_attempts(monkeypatch):
    class FakeLimit:
        async def wait(self):
            return 0, 2.0

        def reduce(self, generation):
            pass

    monkeypatch.setattr(adapter_module, "get_embedding_rate_limit", lambda *args: FakeLimit())
    client = object.__new__(OpenaiClient)
    client.api_provider = SimpleNamespace(name="provider", base_url="https://example.test")
    attempts = 0

    async def execute(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RespNotOkException(429)
        return APIResponse(embedding=[1.0]), None

    monkeypatch.setattr(client, "_execute_embedding_request", execute)
    request = EmbeddingRequest(SimpleNamespace(model_identifier="model"), "text", trace_context=RequestTraceContext())
    async def no_wait(delay):
        pass
    monkeypatch.setattr(adapter_module, "asyncio", SimpleNamespace(sleep=no_wait))
    response = await client.get_embedding(request)
    assert attempts == 2
    assert response.rate_limit_wait_seconds == 5.0


@pytest.mark.asyncio
async def test_embedding_usage_excludes_throttle_wait(monkeypatch):
    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.request_type = "expression.selection.index_batch"
    orchestrator.task_name = "embedding"
    monkeypatch.setattr(orchestrator, "_refresh_task_config", lambda: None)
    monkeypatch.setattr(orchestrator, "_resolve_effective_session_id", lambda session_id: session_id)
    times = iter([100.0, 110.0])
    monkeypatch.setattr(utils_model, "time", SimpleNamespace(time=lambda: next(times)))
    recorded = {}
    monkeypatch.setattr(utils_model.llm_usage_recorder, "record_usage_to_database", lambda **kwargs: recorded.update(kwargs))

    async def execute(**kwargs):
        return SimpleNamespace(
            api_response=APIResponse(embedding=[1.0], usage=SimpleNamespace(), rate_limit_wait_seconds=7.0),
            model_info=SimpleNamespace(name="alias", model_identifier="model", api_provider="provider"),
            request_started_at=None,
        )

    monkeypatch.setattr(orchestrator, "_execute_request", execute)
    await orchestrator.get_embedding("text")
    assert recorded["time_cost"] == 3.0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500])
@pytest.mark.parametrize("with_trace", [False, True])
async def test_orchestrator_embedding_retries_do_not_multiply(monkeypatch, status, with_trace):
    class FakeLimit:
        async def wait(self):
            return 0, 0.0
        def reduce(self, generation):
            pass
    monkeypatch.setattr(adapter_module, "get_embedding_rate_limit", lambda *args: FakeLimit())
    monkeypatch.setattr(utils_model, "has_request_snapshot", lambda error: True)
    monkeypatch.setattr(utils_model, "update_failed_request_attempt", lambda *args, **kwargs: None)
    provider = SimpleNamespace(name="provider", base_url="https://example.test", max_retry=3, retry_interval=0)
    client = object.__new__(OpenaiClient)
    client.api_provider = provider
    attempts = 0
    async def execute(request):
        nonlocal attempts
        attempts += 1
        raise RespNotOkException(status)
    monkeypatch.setattr(client, "_execute_embedding_request", execute)
    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.request_type = "expression.selection.index_batch"
    monkeypatch.setattr(orchestrator, "_schedule_llm_retry_event", lambda **kwargs: None)
    request = EmbeddingRequest(
        SimpleNamespace(name="alias", model_identifier="model"), "text",
        trace_context=RequestTraceContext() if with_trace else None,
    )
    with pytest.raises(utils_model.ModelAttemptFailed):
        await orchestrator._attempt_request_on_model(provider, client, request)
    assert attempts == provider.max_retry
