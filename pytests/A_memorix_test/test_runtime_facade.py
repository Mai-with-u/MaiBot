from pathlib import Path
from typing import Any

import pytest

from src.A_memorix.core.runtime.runtime_facade import KernelRuntimeFacade
from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel


@pytest.mark.asyncio
async def test_runtime_facade_delegates_kernel_runtime_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, Any]] = []
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={"runtime": {"enabled": True}})
    facade = kernel._runtime_facade

    assert isinstance(facade, KernelRuntimeFacade)
    assert facade.config is kernel.config
    assert facade._plugin_config is kernel.config
    assert facade.get_config("runtime.enabled") is True

    monkeypatch.setattr(kernel, "is_runtime_ready", lambda: True)
    monkeypatch.setattr(
        kernel,
        "is_chat_enabled",
        lambda *, stream_id, group_id=None, user_id=None: events.append(
            ("chat_enabled", (stream_id, group_id, user_id))
        )
        or True,
    )
    monkeypatch.setattr(kernel, "_is_embedding_degraded", lambda: False)
    monkeypatch.setattr(kernel, "_dual_vector_pools_enabled", lambda: True)
    monkeypatch.setattr(kernel, "_allow_metadata_only_write", lambda: True)

    async def fake_execute_request_with_dedup(request_key, executor):
        events.append(("dedup", request_key))
        return False, await executor()

    async def fake_apply_retrieval_tuning_profile(profile, *, validate=True):
        events.append(("tuning", (profile, validate)))
        return {"success": True, "profile": profile, "validate": validate}

    async def fake_write_paragraph_vector_or_enqueue(**kwargs):
        events.append(("write_vector", kwargs))
        return {"queued": False, **kwargs}

    monkeypatch.setattr(kernel, "execute_request_with_dedup", fake_execute_request_with_dedup)
    monkeypatch.setattr(kernel, "apply_retrieval_tuning_profile", fake_apply_retrieval_tuning_profile)
    monkeypatch.setattr(kernel, "_write_paragraph_vector_or_enqueue", fake_write_paragraph_vector_or_enqueue)
    monkeypatch.setattr(
        kernel,
        "_enqueue_paragraph_vector_backfill",
        lambda paragraph_hash, *, error="": events.append(("backfill", (paragraph_hash, error))),
    )

    assert facade.is_runtime_ready() is True
    assert facade.is_chat_enabled("session-1", group_id="group-1", user_id="user-1") is True
    assert facade.is_embedding_degraded() is False
    assert facade._dual_vector_pools_enabled() is True
    assert facade.allow_metadata_only_write() is True

    dedup_hit, dedup_payload = await facade.execute_request_with_dedup("request-1", lambda: _async_payload("ok"))
    assert dedup_hit is False
    assert dedup_payload == {"result": "ok"}

    tuning_payload = await facade.apply_retrieval_tuning_profile({"retrieval": {"enable_ppr": False}}, validate=False)
    assert tuning_payload == {
        "success": True,
        "profile": {"retrieval": {"enable_ppr": False}},
        "validate": False,
    }

    write_payload = await facade.write_paragraph_vector_or_enqueue(
        paragraph_hash="paragraph-1",
        content="Alice 喜欢绿茶",
        context="test",
    )
    assert write_payload == {
        "queued": False,
        "paragraph_hash": "paragraph-1",
        "content": "Alice 喜欢绿茶",
        "context": "test",
    }
    facade.enqueue_paragraph_vector_backfill("paragraph-1", error="missing")

    assert ("chat_enabled", ("session-1", "group-1", "user-1")) in events
    assert ("dedup", "request-1") in events
    assert ("tuning", ({"retrieval": {"enable_ppr": False}}, False)) in events
    assert ("backfill", ("paragraph-1", "missing")) in events


async def _async_payload(value: str) -> dict[str, str]:
    return {"result": value}


@pytest.mark.asyncio
async def test_runtime_facade_reinforce_access_filters_empty_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reinforced: list[list[str]] = []
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    facade = kernel._runtime_facade

    class FakeMetadataStore:
        def reinforce_relations(self, hashes: list[str]) -> None:
            reinforced.append(hashes)

    monkeypatch.setattr("src.A_memorix.core.runtime.runtime_facade.time.time", lambda: 1234.5)
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]

    await facade.reinforce_access(["", " relation-1 ", None, "relation-2"])  # type: ignore[list-item]

    assert reinforced == [["relation-1", "relation-2"]]
    assert kernel._last_maintenance_at == 1234.5


@pytest.mark.asyncio
async def test_runtime_facade_reinforce_access_skips_when_store_missing() -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})

    await kernel._runtime_facade.reinforce_access(["relation-1"])

    assert kernel._last_maintenance_at is None
