"""在线迁移跨存储失败补偿、独立后台执行与启动恢复。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import pytest

from pytests.test_chat_migration import store as database_store  # noqa: F401
from src.services import chat_migration_service as service
from src.services.chat_migration_gate import ChatMigrationGate


@pytest.fixture
def runtime(database_store, monkeypatch, tmp_path):  # noqa: F811 - imported pytest fixture
    from src.chat.heart_flow.heartflow_manager import heartflow_manager
    from src.services import statistics_usage_hourly

    config = tmp_path / "bot.toml"
    config.write_text("# preserved\n", encoding="utf-8")
    monkeypatch.setattr(service, "store", database_store)
    monkeypatch.setattr(service, "BOT_CONFIG_PATH", config)
    monkeypatch.setattr(service, "chat_migration_gate", ChatMigrationGate())
    monkeypatch.setattr(service, "running", {})
    monkeypatch.setattr(heartflow_manager, "stop_migrating_chats", AsyncMock())
    monkeypatch.setattr(statistics_usage_hourly, "refresh_usage_hourly", lambda **kwargs: SimpleNamespace(ready=True))
    calls = []

    async def memory(action, **kwargs):
        calls.append(action)
        return {"success": True, "fingerprint": "memory", "counts": {"source": 2, "target": 3}}

    async def finalize(job_id):
        job = database_store.get(job_id)
        database_store.update(job_id, "completed", job["payload"])

    monkeypatch.setattr(service, "memory", memory)
    monkeypatch.setattr(service, "finalize", finalize)
    return database_store, calls


async def start_preview():
    job = await service.preview("A", "B")
    choices = {conflict["id"]: "both" for conflict in job["conflicts"]}
    await service._start(job["id"], choices, True)
    await service.running[job["id"]]
    return service.store.get(job["id"])


@pytest.mark.asyncio
async def test_online_copy_completes_and_resumes_only_its_streams(runtime):
    store, calls = runtime
    service.chat_migration_gate.blocked.add("other")
    job = await start_preview()
    assert job["status"] == "completed"
    assert calls == ["snapshot", "apply"]
    assert service.chat_migration_gate.blocked == {"other"}


@pytest.mark.asyncio
async def test_database_failure_restores_memory_before_resuming(runtime, monkeypatch):
    store, calls = runtime

    def fail(*args):
        raise RuntimeError("database failure")

    monkeypatch.setattr(store, "apply", fail)
    job = await start_preview()
    assert job["status"] == "failed"
    assert "database failure" in job["error"]
    assert calls == ["snapshot", "apply", "restore"]
    assert not service.chat_migration_gate.blocked


@pytest.mark.asyncio
async def test_failed_recovery_keeps_streams_paused(runtime, monkeypatch):
    store, _ = runtime

    def fail(*args):
        raise RuntimeError("database failure")

    async def rollback(*args):
        raise RuntimeError("restore failure")

    monkeypatch.setattr(store, "apply", fail)
    monkeypatch.setattr(service, "rollback", rollback)
    job = await start_preview()
    assert job["status"] == "recovery_required"
    assert service.chat_migration_gate.blocked == {"A", "B"}


@pytest.mark.asyncio
async def test_committed_job_recovers_by_finalizing_not_undoing(runtime):
    store, calls = runtime
    job = await service.preview("A", "B")
    saved = store.get(job["id"])
    saved["payload"]["database_committed"] = True
    store.update(job["id"], "finalizing", saved["payload"])
    await service.initialize_migrations()
    assert service.chat_migration_gate.blocked == {"A", "B"}
    await service.recover(job["id"])
    assert store.get(job["id"])["status"] == "completed"
    assert calls == ["snapshot"]
    assert not service.chat_migration_gate.blocked


@pytest.mark.asyncio
async def test_double_submission_is_claimed_once(runtime):
    job = await service.preview("A", "B")
    choices = {conflict["id"]: "both" for conflict in job["conflicts"]}
    results = await asyncio.gather(
        service._start(job["id"], choices, True), service._start(job["id"], choices, True), return_exceptions=True
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    task = service.running.get(job["id"])
    if task:
        await task
    assert service.store.get(job["id"])["status"] == "completed"
