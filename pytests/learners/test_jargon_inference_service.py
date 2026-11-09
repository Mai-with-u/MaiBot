"""后台队列使用真实 SQLite；推断引擎作为模型请求边界替换。"""

from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

import asyncio
import json
import sys
import time

import pytest

from src.common.database.database_model import ChatSession, Jargon, JargonInferenceJob, JargonInferenceState
from src.config.official_configs import JargonInferenceConfig
from src.learners import jargon_inference_service as module
from src.learners.jargon_inference_result import InferenceResult


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'queue.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Jargon.__table__,
            JargonInferenceJob.__table__,
            JargonInferenceState.__table__,
            ChatSession.__table__,
        ],
    )

    @contextmanager
    def database(auto_commit=True):
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                if auto_commit:
                    session.commit()
            except BaseException:
                session.rollback()
                raise

    with database() as session:
        session.add(ChatSession(session_id="chat", platform="qq", group_id="1", group_name="测试群"))
        for identifier in range(1, 4):
            session.add(
                Jargon(
                    id=identifier,
                    content=f"词{identifier}",
                    meaning="",
                    count=4,
                    session_id_dict='{"chat":4}',
                    evidence_messages=json.dumps([[{"platform": "qq", "message_id": str(identifier)}]]),
                )
            )

    calls = []

    class FakeMiner:
        def __init__(self, _session_id, _session_name):
            pass

        def _should_infer_meaning(self, record):
            return record.count >= 4

        async def _infer_meaning_by_id(self, jargon_id, *, before_request, source, job_id):
            calls.append(jargon_id)
            await before_request()
            return InferenceResult(status="succeeded", meaning="含义")

    miner_module = ModuleType("src.learners.jargon_miner")
    miner_module.JargonMiner = FakeMiner
    router_module = ModuleType("src.webui.routers.jargon")
    router_module.apply_jargon_list_filters = lambda statement, **_filters: statement
    router_module.build_session_display_name_cache = lambda identifiers, _session: {
        key: "测试群" for key in identifiers
    }
    router_module.build_session_id_dict_filter = lambda _identifiers: None
    monkeypatch.setitem(sys.modules, "src.learners.jargon_miner", miner_module)
    monkeypatch.setitem(sys.modules, "src.webui.routers.jargon", router_module)
    settings = JargonInferenceConfig(request_interval_seconds=0, max_retries=0)
    monkeypatch.setattr(module, "global_config", SimpleNamespace(jargon=SimpleNamespace(inference=settings)))
    monkeypatch.setattr(module, "config_manager", Mock())
    monkeypatch.setattr(module, "get_db_session", database)
    service = module.JargonInferenceService()
    yield SimpleNamespace(
        service=service, database=database, settings=settings, miner=FakeMiner, calls=calls, engine=engine
    )
    engine.dispose()


def arm(runtime):
    runtime.service._loop = asyncio.get_running_loop()
    return runtime.service


@pytest.mark.asyncio
async def test_concurrent_submissions_reserve_term_once(runtime):
    service = arm(runtime)
    first, second = await asyncio.gather(service.submit({"ids": [1]}), service.submit({"ids": [1]}))
    assert first["counts"].get("queued", 0) + second["counts"].get("queued", 0) == 1
    assert first["counts"].get("skipped", 0) + second["counts"].get("skipped", 0) == 1
    assert len(service._pending) == 1


@pytest.mark.asyncio
async def test_dispatch_claims_handoff_before_cancellation(runtime, monkeypatch):
    service = arm(runtime)
    job = await service.submit({"ids": [1]})
    handed_off = asyncio.Event()
    release = asyncio.Event()
    real_worker = service._run_item

    async def delayed_worker(job_id, index):
        handed_off.set()
        await release.wait()
        await real_worker(job_id, index)

    monkeypatch.setattr(service, "_run_item", delayed_worker)
    dispatcher = asyncio.create_task(service._dispatch())
    try:
        await asyncio.wait_for(handed_off.wait(), 2)
        cancelled = await service.cancel(job["id"])
        assert cancelled["status"] == "cancelling"
        release.set()
        await asyncio.gather(*service._workers)
        detail = await service.get_job(job["id"])
        assert detail["items"][0]["status"] == "succeeded"
        assert runtime.calls == [1]
    finally:
        release.set()
        dispatcher.cancel()
        await asyncio.gather(dispatcher, *service._workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_reservation_covers_terminal_persistence(runtime, monkeypatch):
    service = arm(runtime)
    first = await service.submit({"ids": [1]})
    finalizing = asyncio.Event()
    release = asyncio.Event()
    real_persist = service._persist

    async def persist(job):
        if job["id"] == first["id"] and job["items"][0]["status"] == "succeeded":
            finalizing.set()
            await release.wait()
        await real_persist(job)

    monkeypatch.setattr(service, "_persist", persist)
    worker = asyncio.create_task(service._run_item(first["id"], 0))
    try:
        await asyncio.wait_for(finalizing.wait(), 2)
        second = await service.submit({"ids": [1]})
        assert second["counts"].get("queued", 0) == 0
    finally:
        release.set()
        await worker


@pytest.mark.asyncio
async def test_cancel_preserves_running_result_and_cancels_only_pending(runtime, monkeypatch):
    service = arm(runtime)
    started = asyncio.Event()
    release = asyncio.Event()

    async def infer(_self, jargon_id, **_kwargs):
        runtime.calls.append(jargon_id)
        started.set()
        await release.wait()
        return InferenceResult(status="succeeded")

    monkeypatch.setattr(runtime.miner, "_infer_meaning_by_id", infer)
    job = await service.submit({"ids": [1, 2]})
    worker = asyncio.create_task(service._run_item(job["id"], 0))
    try:
        await asyncio.wait_for(started.wait(), 2)
        cancelled = await service.cancel(job["id"])
        assert cancelled["status"] == "cancelling"
    finally:
        release.set()
        await worker
    detail = await service.get_job(job["id"])
    assert [item["status"] for item in detail["items"]] == ["succeeded", "cancelled"]
    assert runtime.calls == [1]


@pytest.mark.asyncio
async def test_schedule_does_not_lose_wake_during_scan(runtime, monkeypatch):
    service = arm(runtime)
    runtime.settings.scheduled_enabled = True
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    second_started = asyncio.Event()
    scans = 0

    async def submit(_request, source):
        nonlocal scans
        scans += 1
        if scans == 1:
            first_started.set()
            await first_release.wait()
        else:
            second_started.set()
        return {}

    monkeypatch.setattr(service, "submit", submit)
    scheduler = asyncio.create_task(service._schedule())
    try:
        await asyncio.wait_for(first_started.wait(), 2)
        service._schedule_wake.set()
        first_release.set()
        await asyncio.wait_for(second_started.wait(), 0.5)
    finally:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)


def test_scheduled_scan_prefers_unattempted_over_repeated_low_ids(runtime):
    runtime.settings.max_items_per_scan = 1
    runtime.settings.require_new_evidence = False
    runtime.settings.refresh_interval_hours = 0
    with runtime.database() as session:
        session.add(JargonInferenceState(jargon_id=1, status="succeeded", last_attempt_at=time.time() - 1))
    items = runtime.service._select({"mode": "all", "force": True}, "scheduled")
    assert [item["jargon_id"] for item in items] == [2]


def test_recovery_preserves_completed_results_and_marks_only_active_items(runtime):
    with runtime.database() as session:
        session.add(
            JargonInferenceJob(
                id="prior",
                status="running",
                items_json=json.dumps(
                    [
                        {"jargon_id": 1, "status": "succeeded", "reason": ""},
                        {"jargon_id": 2, "status": "running", "reason": ""},
                        {"jargon_id": 3, "status": "queued", "reason": ""},
                    ]
                ),
            )
        )
        session.add(JargonInferenceState(jargon_id=1, status="succeeded", last_success_at=123))
        session.add(JargonInferenceState(jargon_id=2, status="running"))
    runtime.service._recover_interrupted_jobs()
    with runtime.database() as session:
        job = session.get(JargonInferenceJob, "prior")
        assert job.status == "interrupted"
        assert [item["status"] for item in json.loads(job.items_json)] == ["succeeded", "interrupted", "interrupted"]
        assert session.get(JargonInferenceState, 1).last_success_at == 123
        assert session.get(JargonInferenceState, 2).status == "interrupted"


@pytest.mark.asyncio
async def test_start_failure_never_exposes_ready_service(runtime, monkeypatch):
    def fail_recovery():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(runtime.service, "_recover_interrupted_jobs", fail_recovery)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await runtime.service.start()
    with pytest.raises(RuntimeError, match="尚未就绪"):
        await runtime.service.preview({"ids": [1]})


@pytest.mark.asyncio
async def test_retry_selects_only_failed_or_interrupted(runtime, monkeypatch):
    service = arm(runtime)
    with runtime.database() as session:
        session.add(
            JargonInferenceJob(
                id="prior",
                status="completed",
                options_json='{"mode":"all"}',
                items_json=json.dumps(
                    [
                        {"jargon_id": 1, "status": "failed"},
                        {"jargon_id": 2, "status": "interrupted"},
                        {"jargon_id": 3, "status": "insufficient_info"},
                    ]
                ),
            )
        )
    result = await service.retry("prior")
    detail = await service.get_job(result["id"])
    assert [item["jargon_id"] for item in detail["items"]] == [1, 2]


@pytest.mark.asyncio
async def test_terminal_job_can_be_read_after_memory_eviction(runtime):
    service = arm(runtime)
    job = await service.submit({"ids": [1]})
    await service._run_item(job["id"], 0)
    service._jobs.clear()
    detail = await service.get_job(job["id"])
    assert detail["job"]["status"] == "completed"
    assert detail["items"][0]["status"] == "succeeded"
    assert detail["items"][0]["requests"] == 1


@pytest.mark.asyncio
async def test_dispatch_honors_shared_concurrency_limit(runtime, monkeypatch):
    service = arm(runtime)
    runtime.settings.max_concurrency = 2
    active = 0
    peak = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    async def infer(_self, jargon_id, **_kwargs):
        nonlocal active, peak
        runtime.calls.append(jargon_id)
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_started.set()
        try:
            await release.wait()
            return InferenceResult(status="succeeded")
        finally:
            active -= 1

    monkeypatch.setattr(runtime.miner, "_infer_meaning_by_id", infer)
    job = await service.submit({"ids": [1, 2, 3]})
    dispatcher = asyncio.create_task(service._dispatch())

    async def wait_until_done():
        while (await service.get_job(job["id"]))["job"]["status"] != "completed":
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(two_started.wait(), 2)
        assert len(runtime.calls) == 2
        release.set()
        await asyncio.wait_for(wait_until_done(), 2)
        assert peak == 2
        assert sorted(runtime.calls) == [1, 2, 3]
    finally:
        release.set()
        dispatcher.cancel()
        await asyncio.gather(dispatcher, *service._workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_request_limiter_spaces_requests_across_workers(runtime):
    service = arm(runtime)
    runtime.settings.request_interval_seconds = 0.02
    observed = []

    async def request():
        await service._before_request()
        observed.append(time.monotonic())

    await asyncio.gather(request(), request(), request())
    assert observed[1] - observed[0] >= 0.015
    assert observed[2] - observed[1] >= 0.015


@pytest.mark.asyncio
async def test_shutdown_preserves_finished_result_and_interrupts_remaining_items(runtime, monkeypatch):
    service = runtime.service
    runtime.settings.max_concurrency = 1
    second_started = asyncio.Event()

    async def infer(_self, jargon_id, **_kwargs):
        if jargon_id == 1:
            return InferenceResult(status="succeeded")
        second_started.set()
        await asyncio.Future()

    monkeypatch.setattr(runtime.miner, "_infer_meaning_by_id", infer)
    await service.start()
    job = await service.submit({"ids": [1, 2, 3]})
    try:
        await asyncio.wait_for(second_started.wait(), 2)
    finally:
        await service.stop()
    with runtime.database() as session:
        saved = session.get(JargonInferenceJob, job["id"])
        assert saved.status == "interrupted"
        assert [item["status"] for item in json.loads(saved.items_json)] == ["succeeded", "interrupted", "interrupted"]
    assert not service._reserved
    assert not service._pending
    assert service._loop is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [("succeeded", []), ("insufficient_info", []), ("missing_evidence", []), ("failed", [1]), ("interrupted", [1])],
)
def test_unchanged_evidence_skips_stable_results_but_retries_transient_failures(runtime, status, expected):
    with runtime.database() as session:
        record = session.get(Jargon, 1)
        session.add(
            JargonInferenceState(
                jargon_id=1,
                status=status,
                last_attempt_at=time.time() - 1000000,
                last_attempt_evidence_hash=module.evidence_hash(record.evidence_messages),
            )
        )
    items = runtime.service._select({"ids": [1], "mode": "all", "force": True}, "scheduled")
    assert [item["jargon_id"] for item in items] == expected


@pytest.mark.asyncio
async def test_new_hits_during_queue_do_not_invalidate_session_ownership(runtime):
    service = arm(runtime)
    job = await service.submit({"ids": [1]})
    with runtime.database() as session:
        record = session.get(Jargon, 1)
        record.count = 5
        record.session_id_dict = '{"chat":5}'
        session.add(record)
    await service._run_item(job["id"], 0)
    detail = await service.get_job(job["id"])
    assert detail["items"][0]["status"] == "succeeded"
    assert runtime.calls == [1]


@pytest.mark.asyncio
async def test_lock_added_after_queue_is_respected_before_model_call(runtime):
    service = arm(runtime)
    job = await service.submit({"ids": [1]})
    with runtime.database() as session:
        state = session.get(JargonInferenceState, 1)
        state.locked = True
        session.add(state)
    await service._run_item(job["id"], 0)
    detail = await service.get_job(job["id"])
    assert detail["items"][0]["status"] == "protected"
    assert runtime.calls == []
    with runtime.database() as session:
        assert session.get(JargonInferenceState, 1).locked


@pytest.mark.asyncio
async def test_cancel_keeps_reservation_until_state_and_job_are_durable(runtime, monkeypatch):
    service = arm(runtime)
    first = await service.submit({"ids": [1]})
    finalizing = asyncio.Event()
    release = asyncio.Event()
    real_persist = service._persist

    async def persist(job):
        if job["id"] == first["id"] and job["status"] == "cancelled":
            finalizing.set()
            await release.wait()
        await real_persist(job)

    monkeypatch.setattr(service, "_persist", persist)
    cancellation = asyncio.create_task(service.cancel(first["id"]))
    submitting = None
    try:
        await asyncio.wait_for(finalizing.wait(), 2)
        assert 1 in service._reserved
        submitting = asyncio.create_task(service.submit({"ids": [1]}))
        await asyncio.sleep(0)
        if submitting.done():
            assert submitting.result()["counts"].get("queued", 0) == 0
    finally:
        release.set()
        await cancellation
    if submitting is not None:
        second = await submitting
        with runtime.database() as session:
            state = session.get(JargonInferenceState, 1)
            assert state.status == ("queued" if second["counts"].get("queued", 0) else "cancelled")


@pytest.mark.asyncio
async def test_submit_state_insert_failure_rolls_back_job_and_exposes_no_work(runtime):
    service = arm(runtime)

    def reject_queued_state(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO JARGON_INFERENCE_STATES"):
            raise RuntimeError("injected queued-state write failure")

    event.listen(runtime.engine, "before_cursor_execute", reject_queued_state)
    try:
        with pytest.raises(RuntimeError, match="queued-state write failure"):
            await service.submit({"ids": [1, 2]})
    finally:
        event.remove(runtime.engine, "before_cursor_execute", reject_queued_state)
    assert not service._pending
    assert not service._reserved
    assert not service._jobs
    with runtime.database() as session:
        assert session.exec(select(JargonInferenceJob)).all() == []
        assert session.exec(select(JargonInferenceState)).all() == []
    # 写入恢复后同一词条应能正常入队，而不需要重启服务。
    retry = await service.submit({"ids": [1]})
    assert retry["counts"].get("queued") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["threshold", "scheduled"])
async def test_bounded_scans_skip_reserved_ids_before_applying_limit(runtime, source):
    service = arm(runtime)
    runtime.settings.max_items_per_scan = 1
    observed = []
    for _ in range(3):
        job = await service.submit({"mode": "all", "force": False}, source=source)
        detail = await service.get_job(job["id"])
        assert detail["job"]["counts"].get("queued") == 1
        assert detail["job"]["total"] == 1
        observed.extend(item["jargon_id"] for item in detail["items"])
    assert observed == [1, 2, 3]
    assert service._reserved == {1, 2, 3}


@pytest.mark.asyncio
async def test_config_reload_rescans_only_when_threshold_policy_changes(runtime, monkeypatch):
    service = arm(runtime)
    rescan = AsyncMock(return_value={})
    monkeypatch.setattr(service, "rescan", rescan)
    runtime.settings.request_interval_seconds = 2
    await service._configuration_changed()
    rescan.assert_not_awaited()
    assert service._schedule_wake.is_set()
    runtime.settings.thresholds = [1, 8, 25, 100]
    await service._configuration_changed()
    assert rescan.await_count == 1
    await service._configuration_changed()
    assert rescan.await_count == 1
    runtime.settings.stop_after_last_threshold = False
    await service._configuration_changed()
    assert rescan.await_count == 2


@pytest.mark.asyncio
async def test_retry_uses_failed_items_after_original_status_filter_has_changed(runtime, monkeypatch):
    service = arm(runtime)

    async def infer(_self, jargon_id, **_kwargs):
        return InferenceResult(status="failed" if jargon_id == 1 else "succeeded")

    monkeypatch.setattr(runtime.miner, "_infer_meaning_by_id", infer)
    job = await service.submit({"ids": [1, 2], "last_status": "unattempted"})
    await service._run_item(job["id"], 0)
    await service._run_item(job["id"], 1)
    retried = await service.retry(job["id"])
    detail = await service.get_job(retried["id"])
    assert retried["counts"] == {"queued": 1}
    assert [item["jargon_id"] for item in detail["items"]] == [1]
