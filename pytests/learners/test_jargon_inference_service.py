"""后台队列使用真实 SQLite；推断引擎作为模型请求边界替换。"""

from contextlib import contextmanager
from threading import Event
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
from src.common.database.database_model import JargonCreatedBy
from src.config.official_configs import JargonInferenceConfig
from src.learners import jargon_inference_service as module
from src.learners.jargon_inference_result import InferenceResult


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """使用真实 SQLite 事务和可替换的模型边界验证队列状态。"""
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
        """允许测试在提交或回滚后通过独立会话检查持久状态。"""
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

        def recoverable_evidence_hash(self, record):
            return module.evidence_hash(record.evidence_messages)

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
@pytest.mark.parametrize("code", ["has_meaning", "threshold", "locked", "manual", "no_session", "duplicate"])
async def test_preview_counts_stable_reason_codes_and_keeps_task_explanation(runtime, code):
    """预览不以中文句子为键；任务仍保存可独立阅读的跳过说明。"""
    service = arm(runtime)
    with runtime.database() as session:
        record = session.get(Jargon, 1)
        if code == "has_meaning":
            record.meaning = "已有解释"
        elif code == "threshold":
            record.count = 1
        elif code == "locked":
            session.add(JargonInferenceState(jargon_id=1, locked=True))
        elif code == "manual":
            record.created_by = JargonCreatedBy.MANUAL
        elif code == "no_session":
            record.session_id_dict = "null"
        session.add(record)
    if code == "duplicate":
        service._reserved.add(1)
    request = {"ids": [1], "force": code != "threshold"}
    result = await service.preview(request)
    assert result == {"total": 1, "eligible": 0, "skipped": 1, "reasons": {code: 1}}
    submitted = await service.submit(request)
    item = (await service.get_job(submitted["id"]))["items"][0]
    assert item["reason_code"] == code
    assert item["reason"] and item["reason"] != code


@pytest.mark.asyncio
async def test_concurrent_submissions_reserve_term_once(runtime):
    service = arm(runtime)
    first, second = await asyncio.gather(service.submit({"ids": [1]}), service.submit({"ids": [1]}))
    assert first["counts"].get("queued", 0) + second["counts"].get("queued", 0) == 1
    assert first["counts"].get("skipped", 0) + second["counts"].get("skipped", 0) == 1
    assert len(service._pending) == 1


@pytest.mark.asyncio
async def test_dispatch_claims_handoff_before_cancellation(runtime, monkeypatch):
    """派发任务先认领队列项，取消不能把已交接的任务当成待处理项。"""
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
        assert cancelled["status"] == "queued"
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
    """终态落盘之前继续保留词条预留，防止新任务与旧结果互相覆盖。"""
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
    """取消批量任务只停止尚未开始的词条，正在执行的请求保留结果。"""
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
async def test_schedule_wake_during_scan_recalculates_without_running_early(runtime, monkeypatch):
    """扫描中收到唤醒信号时仍以已有扫描时间计算下一轮。"""
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
        await asyncio.sleep(0.05)
        assert not second_started.is_set()
        service._last_scheduled_scan = time.monotonic() - runtime.settings.scan_interval_hours * 3600
        service._schedule_wake.set()
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
    """重启恢复只标记上次运行中断的项目，保留已完成结果及成功时间。"""
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
    """数据库补扫受锁阻塞时，配置通知仍应立即返回并唤醒调度器。"""
    service = arm(runtime)
    scan = AsyncMock(wraps=service._drain_threshold_rescan)
    monkeypatch.setattr(service, "_drain_threshold_rescan", scan)
    runtime.settings.request_interval_seconds = 2
    await service._configuration_changed()
    assert not service._threshold_pending
    assert service._schedule_wake.is_set()

    scheduler = asyncio.create_task(service._schedule())
    try:
        async with service._rescan_lock:
            runtime.settings.thresholds = [1, 8, 25, 100]
            await asyncio.wait_for(service._configuration_changed(), 2)
            assert service._threshold_pending
            scan.assert_not_awaited()
        await asyncio.wait_for(_wait_for_threshold_rescan(service), 2)
        assert scan.await_count == 1
        assert service._threshold_target_policy[0] == (1, 8, 25, 100)

        await service._configuration_changed()
        assert not service._threshold_pending
        runtime.settings.stop_after_last_threshold = False
        await service._configuration_changed()
        await asyncio.wait_for(_wait_for_threshold_rescan(service), 2)
        assert scan.await_count == 2
        assert service._threshold_target_policy[1] is False
    finally:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)


async def _wait_for_threshold_rescan(service):
    """等待后台补扫确认策略，不等待队列中的模型请求。"""
    while service._threshold_pending:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_policy_change_discards_obsolete_selection_and_rescans_latest(runtime, monkeypatch):
    """筛选已返回旧阈值候选时改变策略，旧候选不可入队，新策略仍应补扫。"""
    service = arm(runtime)
    runtime.settings.max_items_per_scan = 1
    with runtime.database() as session:
        record = session.get(Jargon, 1)
        record.count = 1
        session.add(record)
    monkeypatch.setattr(
        runtime.miner,
        "_should_infer_meaning",
        lambda _self, record: record.count >= runtime.settings.thresholds[0],
    )

    selected = Event()
    release = Event()
    original_select = service._select

    def pause_first_selection(*args, **kwargs):
        """在线程中保留旧筛选结果，让主循环在提交前接收新策略。"""
        items = original_select(*args, **kwargs)
        if not selected.is_set():
            assert [item["jargon_id"] for item in items] == [1]
            selected.set()
            assert release.wait(2)
        return items

    monkeypatch.setattr(service, "_select", pause_first_selection)
    scheduler = asyncio.create_task(service._schedule())
    try:
        runtime.settings.thresholds = [1, 8, 25, 100]
        await service._configuration_changed()
        assert await asyncio.to_thread(selected.wait, 2)
        runtime.settings.thresholds = [4, 8, 25, 100]
        await asyncio.wait_for(service._configuration_changed(), 2)
        release.set()
        await asyncio.wait_for(_wait_for_threshold_rescan(service), 2)
    finally:
        release.set()
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)
    assert service._threshold_target_policy[0] == (4, 8, 25, 100)
    with runtime.database() as session:
        saved = session.exec(select(JargonInferenceJob)).all()
        assert [item["jargon_id"] for job in saved for item in json.loads(job.items_json)] == [2, 3]


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


@pytest.mark.asyncio
async def test_threshold_rescan_drains_batches_and_does_not_repeat_fast_failures(runtime, monkeypatch):
    """显式补扫提交全部批次，即使词条预留在每批结束后立即释放。"""
    service = arm(runtime)
    runtime.settings.max_items_per_scan = 1
    original_submit = service.submit
    seen = []

    async def submit(request, source="manual", **kwargs):
        result = await original_submit(request, source, **kwargs)
        if result["total"]:
            detail = await service.get_job(result["id"])
            seen.extend(item["jargon_id"] for item in detail["items"])
            # 模拟每一批立即失败并释放预留，补扫仍不能重复已提交项。
            await service.cancel(result["id"])
        return result

    monkeypatch.setattr(service, "submit", submit)
    first = await service.rescan()
    assert seen == [1, 2, 3]
    assert first["total"] == 1
    assert not service._threshold_pending
    with runtime.database() as session:
        assert len(session.exec(select(JargonInferenceJob)).all()) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_scope", ["{坏数据", '[["chat", 4]]', "null"])
async def test_damaged_scope_does_not_block_manual_or_threshold_scan(runtime, stored_scope):
    """单条损坏归属在手动预览中可见，分批补扫仍能完成。"""
    service = arm(runtime)
    runtime.settings.max_items_per_scan = 1
    with runtime.database() as session:
        record = session.get(Jargon, 1)
        record.session_id_dict = stored_scope
        session.add(record)

    preview = await service.preview({})
    assert preview == {
        "total": 3,
        "eligible": 2,
        "skipped": 1,
        "reasons": {"no_session": 1},
    }
    manual = await service.submit({"ids": [1]})
    detail = await service.get_job(manual["id"])
    assert detail["items"][0]["status"] == "missing_evidence"
    assert detail["items"][0]["is_jargon"] is None

    await asyncio.wait_for(service.rescan(), 2)
    assert not service._threshold_pending
    with runtime.database() as session:
        saved = session.exec(select(JargonInferenceJob).where(JargonInferenceJob.source == "threshold")).all()
        assert {item["jargon_id"] for job in saved for item in json.loads(job.items_json)} == {2, 3}


@pytest.mark.asyncio
async def test_failed_policy_rescan_resumes_checkpoint_on_scheduler_when_disabled(runtime, monkeypatch):
    """关闭定时推断也要后台重试阈值补扫，并保留已提交批次和退避期限。"""
    service = arm(runtime)
    runtime.settings.max_items_per_scan = 1
    runtime.settings.thresholds = [1, 8, 25, 100]
    original_save = service._save_new_job
    attempts = 0

    def fail_second_batch(job):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("database unavailable")
        original_save(job)

    monkeypatch.setattr(service, "_save_new_job", fail_second_batch)
    await service._configuration_changed()
    scheduler = asyncio.create_task(service._schedule())
    try:

        async def wait_for_retry():
            """等待调度器接收第二批写入失败并建立退避期限。"""
            while service._threshold_retry_at == 0:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_retry(), 2)
        assert service._threshold_pending
        assert service._threshold_seen == {1}
        retry_at = service._threshold_retry_at
        # 已成功提交的第一批即使结束也不可重复。
        await service.cancel(next(iter(service._jobs)))
        runtime.settings.request_interval_seconds = 2
        await service._configuration_changed()
        assert service._threshold_retry_at == retry_at
        assert attempts == 2
        service._threshold_retry_at = 0
        service._schedule_wake.set()
        await asyncio.wait_for(_wait_for_threshold_rescan(service), 2)
    finally:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)
    assert service._threshold_target_policy[0] == (1, 8, 25, 100)
    with runtime.database() as session:
        saved = session.exec(select(JargonInferenceJob)).all()
        assert [item["jargon_id"] for job in saved for item in json.loads(job.items_json)] == [1, 2, 3]


@pytest.mark.asyncio
async def test_automatic_task_preserves_triggering_session_and_name(runtime):
    service = arm(runtime)
    with runtime.database() as session:
        session.add(ChatSession(session_id="z-chat", platform="qq", group_id="2", group_name="触发群"))
        record = session.get(Jargon, 1)
        record.session_id_dict = '{"chat":2,"z-chat":2}'
        session.add(record)
    assert await service.enqueue_auto(1, "z-chat", "触发群")
    item = next(iter(service._jobs.values()))["items"][0]
    assert item["session_id"] == "z-chat"
    assert item["session_name"] == "触发群"


@pytest.mark.asyncio
async def test_automatic_enqueue_failure_does_not_abort_learning(runtime, monkeypatch):
    service = arm(runtime)
    monkeypatch.setattr(service, "submit", AsyncMock(side_effect=RuntimeError("database unavailable")))
    assert not await service.enqueue_auto(1, "chat", "测试群")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change, value", [("content", "已修改"), ("session_id_dict", '{"other":4}'), ("session_id_dict", "{损坏")]
)
async def test_modified_queued_term_reaches_stale_status_without_model_call(runtime, change, value):
    """排队后丢失可解析归属的词条也必须阻止模型请求和写回。"""
    service = arm(runtime)
    result = await service.submit({"ids": [1]})
    with runtime.database() as session:
        record = session.get(Jargon, 1)
        setattr(record, change, value)
        session.add(record)
    await service._run_item(result["id"], 0)
    with runtime.database() as session:
        state = session.get(JargonInferenceState, 1)
        assert state.status == "stale"
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_recreated_id_does_not_inherit_old_terminal_state(runtime):
    service = arm(runtime)
    result = await service.submit({"ids": [1]})
    with runtime.database() as session:
        record = session.get(Jargon, 1)
        session.delete(record)
        session.delete(session.get(JargonInferenceState, 1))
        session.flush()
        session.add(Jargon(id=1, content="新记录", meaning="", session_id_dict='{"chat":4}'))
    await service._run_item(result["id"], 0)
    with runtime.database() as session:
        assert session.get(JargonInferenceState, 1) is None


@pytest.mark.asyncio
async def test_job_pagination_filters_preserve_manual_access_amid_automatic_flood(runtime):
    """来源筛选、活动优先排序和分页不受大量自动任务挤占。"""
    service = arm(runtime)
    now = time.time()
    with runtime.database() as session:
        session.add(
            JargonInferenceJob(id="manual", source="manual", status="completed", created_at=now, updated_at=now)
        )
        session.add(
            JargonInferenceJob(id="active", source="manual", status="running", created_at=now - 1000, updated_at=now)
        )
        for index in range(80):
            session.add(
                JargonInferenceJob(
                    id=f"auto-{index:03}",
                    source="automatic",
                    status="completed",
                    created_at=now + index,
                    updated_at=now,
                )
            )
    manual = await service.list_jobs(source="manual", page_size=1)
    assert manual["total"] == 2
    assert manual["items"][0]["id"] == "active"
    assert (await service.list_jobs(source="manual", page=2, page_size=1))["items"][0]["id"] == "manual"
    active = await service.list_jobs(active_only=True)
    assert active["total"] == 1
    assert active["items"][0]["id"] == "active"
    assert len((await service.list_jobs(page=2))["items"]) == 32


def test_terminal_job_retention_uses_configured_age_without_source_count_cap(runtime):
    runtime.settings.job_retention_days = 7
    now = time.time()
    with runtime.database() as session:
        for index in range(250):
            session.add(
                JargonInferenceJob(id=f"recent-{index}", source="automatic", status="completed", updated_at=now)
            )
        for status in ("queued", "running", "cancelling", "completed", "interrupted"):
            session.add(JargonInferenceJob(id=f"old-{status}", status=status, updated_at=now - 8 * 86400))
    runtime.service._cleanup_jobs()
    with runtime.database() as session:
        rows = session.exec(select(JargonInferenceJob)).all()
        ids = {row.id for row in rows}
        assert len(rows) == 253
        assert {"old-queued", "old-running", "old-cancelling"} <= ids
        assert "old-completed" not in ids
        assert "old-interrupted" not in ids


@pytest.mark.asyncio
async def test_submission_and_progress_writes_leave_retention_to_cleanup(runtime):
    """进度写入不触发清理，专用清理仍会删除过期终态任务。"""
    service = arm(runtime)
    runtime.settings.job_retention_days = 7
    with runtime.database() as session:
        session.add(JargonInferenceJob(id="expired", status="completed", updated_at=time.time() - 8 * 86400))

    job = await service.submit({"ids": [1]})
    await service._run_item(job["id"], 0)
    with runtime.database() as session:
        assert session.get(JargonInferenceJob, "expired") is not None
        assert session.get(JargonInferenceJob, job["id"]).status == "completed"

    service._cleanup_jobs()
    with runtime.database() as session:
        assert session.get(JargonInferenceJob, "expired") is None
        assert session.get(JargonInferenceJob, job["id"]) is not None


@pytest.mark.asyncio
async def test_cancelled_submission_waits_for_commit_then_shutdown_marks_it_interrupted(runtime, monkeypatch):
    """取消提交后仍等待已开始的事务，关停时能持久化中断状态。"""
    service = arm(runtime)
    writing = Event()
    release = Event()
    original_save = service._save_new_job

    def blocked_save(job):
        writing.set()
        assert release.wait(2)
        original_save(job)

    monkeypatch.setattr(service, "_save_new_job", blocked_save)
    submission = asyncio.create_task(service.submit({"ids": [1]}))
    try:
        assert await asyncio.to_thread(writing.wait, 2)
        submission.cancel()
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0.02)
        assert not submission.done()
        assert not stopping.done()
        release.set()
        await asyncio.gather(submission, return_exceptions=True)
        await asyncio.wait_for(stopping, 2)
    finally:
        release.set()
    with runtime.database() as session:
        jobs = session.exec(select(JargonInferenceJob)).all()
        assert len(jobs) == 1
        assert jobs[0].status == "interrupted"
        assert session.get(JargonInferenceState, 1).status == "interrupted"
    assert not runtime.calls


@pytest.mark.asyncio
async def test_shutdown_waits_for_progress_write_before_terminal_write(runtime, monkeypatch):
    """关停等待进行中的进度事务，避免旧运行状态覆盖中断终态。"""
    service = runtime.service
    writing = Event()
    release = Event()
    original_save = service._save_job

    def blocked_save(job):
        if job["status"] == "running":
            writing.set()
            assert release.wait(2)
        original_save(job)

    monkeypatch.setattr(service, "_save_job", blocked_save)
    await service.start()
    job = await service.submit({"ids": [1]})
    try:
        assert await asyncio.to_thread(writing.wait, 2)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0.02)
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, 2)
    finally:
        release.set()
    with runtime.database() as session:
        assert session.get(JargonInferenceJob, job["id"]).status == "interrupted"
        assert session.get(JargonInferenceState, 1).status == "interrupted"


@pytest.mark.asyncio
async def test_stop_clears_memory_even_when_database_write_fails(runtime, monkeypatch):
    service = arm(runtime)
    await service.submit({"ids": [1]})
    monkeypatch.setattr(service, "_write_states", Mock(side_effect=RuntimeError("database unavailable")))
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.stop()
    assert service._loop is None
    assert not service._jobs
    assert not service._pending
    assert not service._reserved


def test_missing_evidence_can_recover_later_messages_with_unchanged_reference_hash(runtime, monkeypatch):
    """没有新增引用时也可发现稍后出现的可恢复聊天证据。"""
    with runtime.database() as session:
        record = session.get(Jargon, 1)
        record.evidence_messages = None
        session.add(record)
        session.add(
            JargonInferenceState(
                jargon_id=1, status="missing_evidence", last_attempt_evidence_hash=module.evidence_hash(None)
            )
        )
    recovered_hash = module.evidence_hash('[[{"platform":"qq","message_id":"new"}]]')
    monkeypatch.setattr(runtime.miner, "recoverable_evidence_hash", lambda _self, _record: recovered_hash)
    items = runtime.service._select({"ids": [1], "force": True}, "scheduled")
    assert [item["jargon_id"] for item in items] == [1]
    assert items[0]["evidence_hash"] == recovered_hash


@pytest.mark.asyncio
async def test_retention_change_triggers_cleanup_even_when_scheduling_disabled(runtime):
    service = arm(runtime)
    now = time.time()
    service._last_cleanup = time.monotonic()
    with runtime.database() as session:
        session.add(JargonInferenceJob(id="expired", status="completed", updated_at=now - 3 * 86400))
    runtime.settings.job_retention_days = 2
    await service._configuration_changed()
    scheduler = asyncio.create_task(service._schedule())
    try:

        async def wait_cleanup():
            while service._cleanup_requested:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_cleanup(), 2)
    finally:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)
    with runtime.database() as session:
        assert session.get(JargonInferenceJob, "expired") is None


@pytest.mark.asyncio
async def test_cancelled_stop_finishes_worker_and_pending_cleanup(runtime, monkeypatch):
    from threading import Event

    service = runtime.service
    writing = Event()
    release = Event()
    original_save = service._save_job
    runtime.settings.max_concurrency = 1

    def blocked_save(job):
        if job["status"] == "running":
            writing.set()
            assert release.wait(2)
        original_save(job)

    monkeypatch.setattr(service, "_save_job", blocked_save)
    await service.start()
    job = await service.submit({"ids": [1, 2]})
    stopping = None
    try:
        assert await asyncio.to_thread(writing.wait, 2)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0.01)
        stopping.cancel()
        await asyncio.sleep(0.01)
        assert not stopping.done()
        release.set()
        result = await asyncio.gather(stopping, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
    finally:
        release.set()
        if stopping is not None:
            await asyncio.gather(stopping, return_exceptions=True)
    assert service._loop is None
    assert not service._pending
    assert not service._reserved
    with runtime.database() as session:
        row = session.get(JargonInferenceJob, job["id"])
        assert row.status == "interrupted"
        assert [item["status"] for item in json.loads(row.items_json)] == ["interrupted", "interrupted"]


@pytest.mark.asyncio
async def test_callback_registration_failure_clears_service_readiness(runtime, monkeypatch):
    monkeypatch.setattr(
        module.config_manager, "register_reload_callback", Mock(side_effect=RuntimeError("register failed"))
    )
    with pytest.raises(RuntimeError, match="register failed"):
        await runtime.service.start()
    assert runtime.service._loop is None
    with pytest.raises(RuntimeError, match="尚未就绪"):
        await runtime.service.submit({"ids": [1]})


@pytest.mark.asyncio
async def test_automatic_task_never_substitutes_removed_trigger_session(runtime):
    service = arm(runtime)
    assert not await service.enqueue_auto(1, "removed-session", "已移除群")
    assert not service._pending
    with runtime.database() as session:
        assert session.exec(select(JargonInferenceJob)).all() == []


@pytest.mark.asyncio
async def test_cancel_request_cancellation_waits_for_state_and_job_persistence(runtime, monkeypatch):
    from threading import Event

    service = arm(runtime)
    job = await service.submit({"ids": [1, 2]})
    writing = Event()
    release = Event()
    original_write = service._write_states

    def blocked_write(items, source):
        writing.set()
        assert release.wait(2)
        original_write(items, source)

    monkeypatch.setattr(service, "_write_states", blocked_write)
    cancellation = asyncio.create_task(service.cancel(job["id"]))
    try:
        assert await asyncio.to_thread(writing.wait, 2)
        cancellation.cancel()
        await asyncio.sleep(0.01)
        assert not cancellation.done()
        assert service._reserved == {1, 2}
        release.set()
        result = await asyncio.gather(cancellation, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
    finally:
        release.set()
    with runtime.database() as session:
        persisted = session.get(JargonInferenceJob, job["id"])
        assert persisted.status == "cancelled"
        assert [item["status"] for item in json.loads(persisted.items_json)] == ["cancelled", "cancelled"]
        assert session.get(JargonInferenceState, 1).status == "cancelled"
        assert session.get(JargonInferenceState, 2).status == "cancelled"
    assert not service._reserved
    assert job["id"] not in service._jobs


@pytest.mark.asyncio
async def test_cancel_without_pending_items_keeps_terminal_retention_timestamp(runtime):
    service = arm(runtime)
    job = await service.submit({"ids": [1]})
    await service.cancel(job["id"])
    with runtime.database() as session:
        previous = session.get(JargonInferenceJob, job["id"]).updated_at
    again = await service.cancel(job["id"])
    assert again["updated_at"] == previous
    with runtime.database() as session:
        assert session.get(JargonInferenceJob, job["id"]).updated_at == previous
