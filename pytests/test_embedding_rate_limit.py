from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace

import asyncio
import logging
import sys

import pytest


@pytest.fixture
def rate_module(monkeypatch):
    """Load the limiter without starting configured model clients or file logging."""
    logger = ModuleType("src.common.logger")
    logger.get_logger = logging.getLogger
    monkeypatch.setitem(sys.modules, "src.common.logger", logger)
    path = Path(__file__).resolve().parents[1] / "src/llm_models/model_client/embedding_rate_limit.py"
    spec = spec_from_file_location("embedding_rate_limit_test", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clock(rate_module, monkeypatch):
    """Replace elapsed time and sleep with an adjustable, deterministic clock."""
    now = [3600.0]

    async def sleep(delay):
        """Advance simulated time without waiting in real time."""
        now[0] += delay

    monkeypatch.setattr(rate_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(rate_module, "asyncio", SimpleNamespace(sleep=sleep))
    return now


@pytest.mark.asyncio
@pytest.mark.parametrize("history, expected", [([], 1.0), ([0.0, 1.0], 1.0), ([3540.0, 3550.0], 5.0)])
async def test_sparse_history_has_bounded_wait(rate_module, clock, history, expected):
    """Sparse or insufficient samples must not produce excessive retry intervals."""
    limiter = rate_module.EmbeddingRateLimit()
    limiter._times.extend(history)
    generation, waited = await limiter.wait()
    assert waited == 0.0
    limiter.reduce(generation)
    _, waited = await limiter.wait()
    assert waited == expected


@pytest.mark.asyncio
async def test_concurrent_errors_reduce_only_twice_and_waiters_recheck(rate_module, clock, monkeypatch):
    """Deduplicate concurrent errors and make sleepers observe later interval changes."""
    limiter = rate_module.EmbeddingRateLimit()
    limiter._times.extend([3597.0, 3598.0])
    generation, _ = await limiter.wait()
    limiter.reduce(generation)
    limiter.reduce(generation)
    assert (limiter.halvings, limiter.interval) == (1, 2.0)
    second_generation, waited = await limiter.wait()
    assert waited == 2.0

    async def concurrent_sleep(delay):
        """Deliver another request's 429 while a caller is waiting."""
        clock[0] += delay
        limiter.reduce(second_generation)

    monkeypatch.setattr(rate_module, "asyncio", SimpleNamespace(sleep=concurrent_sleep))
    capped_generation, waited = await limiter.wait()
    assert waited == 4.0
    limiter.reduce(capped_generation)
    assert (limiter.halvings, limiter.interval) == (2, 4.0)


@pytest.mark.asyncio
async def test_cooldown_recovers_and_rejects_old_generations(rate_module, clock):
    """Recovery clears backoff without letting pre-recovery errors slow new requests."""
    limiter = rate_module.EmbeddingRateLimit()
    first_generation, _ = await limiter.wait()
    limiter.reduce(first_generation)
    second_generation, _ = await limiter.wait()
    limiter.reduce(second_generation)
    old_generation, _ = await limiter.wait()
    clock[0] += 300.0

    generation, waited = await limiter.wait()
    assert waited == 0.0
    assert (limiter.halvings, limiter.interval) == (0, 0.0)
    assert list(limiter._times) == [clock[0]]
    for stale in (first_generation, second_generation, old_generation):
        limiter.reduce(stale)
    assert limiter.interval == 0.0
    limiter.reduce(generation)
    assert (limiter.halvings, limiter.interval) == (1, 1.0)


@pytest.mark.asyncio
async def test_ongoing_429_at_cap_extends_cooldown(rate_module, clock):
    """Current-generation errors postpone recovery even after both reductions."""
    limiter = rate_module.EmbeddingRateLimit()
    for _ in range(2):
        generation, _ = await limiter.wait()
        limiter.reduce(generation)
    clock[0] += 299.0
    generation, _ = await limiter.wait()
    limiter.reduce(generation)
    clock[0] += 2.0
    await limiter.wait()
    assert (limiter.halvings, limiter.interval) == (2, 2.0)
    clock[0] += 298.0
    _, waited = await limiter.wait()
    assert waited == 0.0
    assert limiter.interval == 0.0


def test_limiter_is_shared_and_spaces_requests_across_event_loops(rate_module):
    """Both event loops share send spacing while different models remain independent."""
    limiter = rate_module.get_embedding_rate_limit("provider", "https://example.test/v1", "model")
    assert limiter is rate_module.get_embedding_rate_limit("provider", "https://example.test/v1/", "model")
    assert limiter is not rate_module.get_embedding_rate_limit("provider", "https://example.test/v1", "other")
    limiter.interval = 0.01

    async def send():
        """Send three requests through the same limiter from one event loop."""
        for _ in range(3):
            await limiter.wait()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(asyncio.run, send()) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    times = list(limiter._times)
    assert len(times) == 6
    assert all(later - earlier >= 0.01 for earlier, later in zip(times, times[1:], strict=False))
