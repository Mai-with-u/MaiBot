"""黑话推断的主循环任务队列：统一手动、次数触发与定期更新。"""

from collections import Counter, deque
from copy import deepcopy
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, text
from sqlmodel import col, select

import asyncio
import json
import time
import uuid

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    ChatSession,
    Jargon,
    JargonCreatedBy,
    JargonInferenceJob,
    JargonInferenceState,
)
from src.common.logger import get_logger
from src.common.runtime_loop import run_on_main_loop
from src.config.config import config_manager, global_config

from .jargon_inference_result import InferenceResult, evidence_hash

logger = get_logger("jargon.inference")
ACTIVE = {"queued", "running"}
RETRYABLE = {"failed", "interrupted"}
MAX_MANUAL_ITEMS = 5000


def _snapshot(record: Jargon) -> Dict[str, Any]:
    return {
        "content": record.content,
        "session_ids": sorted(json.loads(record.session_id_dict or "{}")),
        "is_global": record.is_global,
        "created_timestamp": str(record.created_timestamp),
    }


def _job_summary(job: Dict[str, Any]) -> Dict[str, Any]:
    counts = dict(Counter(item["status"] for item in job["items"]))
    return {
        "id": job["id"],
        "source": job["source"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "total": len(job["items"]),
        "completed": sum(count for status, count in counts.items() if status not in ACTIVE),
        "counts": counts,
    }


def _deserialize(row: JargonInferenceJob) -> Dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "options": json.loads(row.options_json),
        "items": json.loads(row.items_json),
    }


class JargonInferenceService:
    """所有内存状态只在 bot 主循环访问，数据库操作均在线程池执行。"""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._reserved: Set[int] = set()
        self._pending: Deque[Tuple[str, int]] = deque()
        self._workers: Set[asyncio.Task] = set()
        self._dispatcher: Optional[asyncio.Task] = None
        self._scheduler: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._schedule_wake = asyncio.Event()
        self._submit_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0
        self._stopping = False
        self._threshold_policy = (
            tuple(global_config.jargon.inference.thresholds),
            global_config.jargon.inference.stop_after_last_threshold,
        )

    def _check_ready(self) -> None:
        if self._loop is not asyncio.get_running_loop() or self._stopping:
            raise RuntimeError("黑话推断服务尚未就绪")

    async def start(self) -> None:
        if self._loop is not None:
            return
        self._stopping = False
        await asyncio.to_thread(self._recover_interrupted_jobs)
        self._loop = asyncio.get_running_loop()
        config_manager.register_reload_callback(self.on_config_changed, sections=("jargon",))
        self._dispatcher = asyncio.create_task(self._dispatch(), name="jargon-inference-dispatch")
        self._scheduler = asyncio.create_task(self._schedule(), name="jargon-inference-schedule")

    async def stop(self) -> None:
        if self._loop is None:
            return
        self._stopping = True
        config_manager.unregister_reload_callback(self.on_config_changed)
        tasks = [task for task in (self._dispatcher, self._scheduler) if task is not None]
        tasks.extend(self._workers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # 队列不自动重放，重启后用户可以检查已完成结果并重试中断项。
        for job in list(self._jobs.values()):
            for item in job["items"]:
                if item["status"] in ACTIVE:
                    item.update(status="interrupted", reason="服务已停止，可手动重试")
                    await asyncio.to_thread(self._write_state, item, job["source"])
            self._finish_job(job)
            await self._persist(job)
        self._pending.clear()
        self._reserved.clear()
        self._workers.clear()
        self._jobs.clear()
        self._loop = None

    async def on_config_changed(self) -> None:
        await run_on_main_loop(self._configuration_changed())

    async def _configuration_changed(self) -> None:
        if self._loop is None or self._stopping:
            return
        self._wake.set()
        self._schedule_wake.set()
        policy = (
            tuple(global_config.jargon.inference.thresholds),
            global_config.jargon.inference.stop_after_last_threshold,
        )
        if policy != self._threshold_policy:
            self._threshold_policy = policy
            # 次数策略变化才补扫存量，调整显示或限速不产生额外模型任务。
            await self.rescan()

    @staticmethod
    def _recover_interrupted_jobs() -> None:
        with get_db_session() as session:
            for row in session.exec(
                select(JargonInferenceJob).where(
                    col(JargonInferenceJob.status).in_(["queued", "running", "cancelling"])
                )
            ).all():
                items = json.loads(row.items_json)
                for item in items:
                    if item["status"] in ACTIVE:
                        item.update(status="interrupted", reason="上次运行中断，可手动重试")
                row.items_json = json.dumps(items, ensure_ascii=False)
                row.status = "interrupted"
                row.updated_at = time.time()
                session.add(row)
            for state in session.exec(
                select(JargonInferenceState).where(col(JargonInferenceState.status).in_(["queued", "running"]))
            ).all():
                state.status = "interrupted"
                state.reason = "上次运行中断，可手动重试"
                session.add(state)

    def _select(
        self, request: Dict[str, Any], source: str, reserved_ids: Optional[Set[int]] = None
    ) -> List[Dict[str, Any]]:
        from src.webui.routers.jargon import (
            apply_jargon_list_filters,
            build_session_display_name_cache,
            build_session_id_dict_filter,
        )
        from .jargon_miner import JargonMiner

        filters = request.get("filters") or {}
        statement = apply_jargon_list_filters(
            select(Jargon),
            **{
                key: filters.get(key)
                for key in ("search", "session_id", "jargon_status", "is_jargon", "is_complete", "is_global")
            },
        )
        if "ids" in request:
            statement = statement.where(col(Jargon.id).in_(request["ids"]))
        if "session_ids" in filters:
            condition = build_session_id_dict_filter(filters["session_ids"])
            if condition is None:
                return []
            statement = statement.where(condition)
        settings = global_config.jargon.inference
        now = time.time()
        items: List[Dict[str, Any]] = []
        with get_db_session() as session:
            # 流式扫描，定时任务仅收集单轮上限；手动全选超限明确报错。
            records = session.exec(
                statement.outerjoin(JargonInferenceState, JargonInferenceState.jargon_id == Jargon.id).order_by(
                    func.coalesce(JargonInferenceState.last_attempt_at, 0), col(Jargon.id)
                )
            )
            states = {state.jargon_id: state for state in session.exec(select(JargonInferenceState)).all()}
            existing_sessions = {chat.session_id for chat in session.exec(select(ChatSession)).all()}
            eligible_count = 0
            for record in records:
                if source != "manual" and reserved_ids and record.id in reserved_ids:
                    continue
                state = states.get(record.id)
                if request.get("last_status") and (state.status if state else "unattempted") != request["last_status"]:
                    continue
                reason = ""
                status = "queued"
                if record.created_by == JargonCreatedBy.MANUAL or (state and state.locked):
                    status, reason = "protected", "人工维护或已锁定"
                elif request.get("mode", "missing") == "missing" and (record.meaning or "").strip():
                    status, reason = "skipped", "已有含义"
                elif not request.get("force", True) and not JargonMiner("", "")._should_infer_meaning(record):
                    status, reason = "skipped", "未达到自动推断条件"
                scope = json.loads(record.session_id_dict or "{}")
                session_ids = sorted(set(scope) & existing_sessions)
                if not reason and not session_ids:
                    status, reason = "missing_evidence", "词条没有可解析的所属会话"
                fingerprint = evidence_hash(record.evidence_messages)
                if source == "scheduled":
                    if settings.scheduled_scope == "failed" and (
                        not state
                        or state.status not in ("failed", "interrupted", "insufficient_info", "missing_evidence")
                    ):
                        continue
                    if (
                        state
                        and state.last_attempt_at
                        and now - state.last_attempt_at < settings.refresh_interval_hours * 3600
                    ):
                        continue
                    if (
                        settings.require_new_evidence
                        and state
                        and state.status in ("succeeded", "insufficient_info", "missing_evidence")
                        and fingerprint == state.last_attempt_evidence_hash
                    ):
                        continue
                if source != "manual" and reason:
                    continue
                items.append(
                    {
                        "jargon_id": record.id,
                        "content": record.content,
                        "session_id": session_ids[0] if session_ids else "",
                        "session_name": "",
                        "status": status,
                        "reason": reason,
                        "attempts": 0,
                        "requests": 0,
                        "snapshot": _snapshot(record),
                        "evidence_hash": fingerprint,
                        "count": record.count,
                    }
                )
                eligible_count += status == "queued"
                if source != "manual" and eligible_count >= settings.max_items_per_scan:
                    break
                if source == "manual" and len(items) > MAX_MANUAL_ITEMS:
                    raise ValueError(f"单次最多处理 {MAX_MANUAL_ITEMS} 条，请缩小筛选范围")
            names = build_session_display_name_cache(
                [item["session_id"] for item in items if item["session_id"]], session
            )
            for item in items:
                item["session_name"] = names.get(item["session_id"], "未关联聊天")
        return items

    async def preview(self, request: Dict[str, Any]) -> Dict[str, Any]:
        self._check_ready()
        items = await asyncio.to_thread(self._select, request, "manual")
        for item in items:
            if item["jargon_id"] in self._reserved:
                item.update(status="skipped", reason="已有排队或运行中的任务")
        reasons = Counter(item["reason"] for item in items if item["status"] != "queued")
        eligible = sum(item["status"] == "queued" for item in items)
        return {"total": len(items), "eligible": eligible, "skipped": len(items) - eligible, "reasons": dict(reasons)}

    async def submit(self, request: Dict[str, Any], source: str = "manual") -> Dict[str, Any]:
        self._check_ready()
        async with self._submit_lock:
            items = await asyncio.to_thread(self._select, request, source, set(self._reserved))
            for item in items:
                if item["jargon_id"] in self._reserved:
                    item.update(status="skipped", reason="已有排队或运行中的任务")
            now = time.time()
            job = {
                "id": uuid.uuid4().hex,
                "source": source,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "options": deepcopy(request),
                "items": items,
            }
            self._finish_job(job)
            # 自动扫描没有候选时不产生空历史任务。
            if not items and source not in ("manual", "threshold"):
                return _job_summary(job)
            # 事务成功后才暴露队列；磁盘写入失败不会留下半个后台任务。
            await asyncio.to_thread(self._save_new_job, job)
            self._jobs[job["id"]] = job
            for index, item in enumerate(items):
                if item["status"] == "queued":
                    self._reserved.add(item["jargon_id"])
                    self._pending.append((job["id"], index))
            self._wake.set()
            if job["status"] not in ACTIVE:
                self._jobs.pop(job["id"], None)
            return _job_summary(job)

    async def enqueue_auto(self, jargon_id: int, session_id: str, session_name: str) -> bool:
        if self._loop is None or self._stopping:
            return False
        result = await self.submit({"ids": [jargon_id], "mode": "all", "force": False}, source="automatic")
        return result["counts"].get("queued", 0) > 0

    async def rescan(self) -> Dict[str, Any]:
        return await self.submit({"mode": "all", "force": False}, source="threshold")

    async def _persist(self, job: Dict[str, Any]) -> None:
        async with self._persist_lock:
            snapshot = deepcopy(job)
            await asyncio.to_thread(self._save_job, snapshot)

    @staticmethod
    def _save_job(job: Dict[str, Any]) -> None:
        with get_db_session() as session:
            row = session.get(JargonInferenceJob, job["id"])
            if row is None:
                row = JargonInferenceJob(id=job["id"], source=job["source"], created_at=job["created_at"])
            row.status = job["status"]
            row.updated_at = job["updated_at"]
            row.options_json = json.dumps(job["options"], ensure_ascii=False)
            row.items_json = json.dumps(job["items"], ensure_ascii=False)
            session.add(row)

    @staticmethod
    def _save_new_job(job: Dict[str, Any]) -> None:
        with get_db_session() as session:
            session.connection().execute(text("BEGIN IMMEDIATE"))
            for item in job["items"]:
                if item["status"] != "queued":
                    continue
                record = session.get(Jargon, item["jargon_id"])
                if record is None or _snapshot(record) != item["snapshot"]:
                    item.update(status="stale", reason="词条已删除或归属已改变")
                    continue
                state = session.get(JargonInferenceState, item["jargon_id"]) or JargonInferenceState(
                    jargon_id=item["jargon_id"]
                )
                if record.created_by == JargonCreatedBy.MANUAL or state.locked:
                    item.update(status="protected", reason="人工维护或已锁定")
                    continue
                state.status = "queued"
                state.reason = ""
                state.last_source = job["source"]
                session.add(state)
            JargonInferenceService._finish_job(job)
            session.add(
                JargonInferenceJob(
                    id=job["id"],
                    source=job["source"],
                    status=job["status"],
                    created_at=job["created_at"],
                    updated_at=job["updated_at"],
                    options_json=json.dumps(job["options"], ensure_ascii=False),
                    items_json=json.dumps(job["items"], ensure_ascii=False),
                )
            )

    @staticmethod
    def _write_state(item: Dict[str, Any], source: str) -> None:
        with get_db_session() as session:
            session.connection().execute(text("BEGIN IMMEDIATE"))
            record = session.get(Jargon, item["jargon_id"])
            if record is None or _snapshot(record) != item["snapshot"]:
                return
            state = session.get(JargonInferenceState, item["jargon_id"]) or JargonInferenceState(
                jargon_id=item["jargon_id"]
            )
            state.status = item["status"]
            state.reason = item["reason"]
            state.last_source = source
            if item["status"] == "running":
                state.last_attempt_at = time.time()
                state.last_attempt_evidence_hash = item["evidence_hash"]
                state.last_count = item["count"]
            if item["attempts"] and item["status"] not in ACTIVE:
                state.last_attempt_evidence_hash = item["evidence_hash"]
            if item["status"] == "succeeded":
                state.last_success_at = time.time()
                state.last_evidence_hash = item["evidence_hash"]
            session.add(state)

    @staticmethod
    def _validate_item(item: Dict[str, Any]) -> Optional[InferenceResult]:
        with get_db_session() as session:
            record = session.get(Jargon, item["jargon_id"])
            if record is None:
                return InferenceResult(status="missing", reason="词条已删除")
            if _snapshot(record) != item["snapshot"]:
                return InferenceResult(status="stale", reason="词条内容或归属已修改，请重新选择")
            state = session.get(JargonInferenceState, record.id)
            if record.created_by == JargonCreatedBy.MANUAL or (state and state.locked):
                return InferenceResult(status="protected", reason="词条已转为人工维护或已锁定")
            item["evidence_hash"] = evidence_hash(record.evidence_messages)
            item["count"] = record.count
        return None

    @staticmethod
    def _finish_job(job: Dict[str, Any]) -> None:
        statuses = {item["status"] for item in job["items"]}
        if statuses & ACTIVE:
            job["status"] = (
                "cancelling" if job["status"] == "cancelling" else ("running" if "running" in statuses else "queued")
            )
        elif "cancelled" in statuses:
            job["status"] = "cancelled"
        elif "interrupted" in statuses:
            job["status"] = "interrupted"
        else:
            job["status"] = "completed"
        job["updated_at"] = time.time()

    async def _before_request(self) -> None:
        async with self._rate_lock:
            wait = global_config.jargon.inference.request_interval_seconds - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _dispatch(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending and len(self._workers) < global_config.jargon.inference.max_concurrency:
                job_id, index = self._pending.popleft()
                if self._jobs[job_id]["items"][index]["status"] != "queued":
                    continue
                # 在交还事件循环前占用该项，取消只能处理尚未分配给工作任务的词条。
                self._jobs[job_id]["items"][index]["status"] = "running"
                task = asyncio.create_task(self._run_item(job_id, index), name=f"jargon-inference-{job_id}-{index}")
                self._workers.add(task)
                task.add_done_callback(self._worker_finished)

    def _worker_finished(self, task: asyncio.Task) -> None:
        self._workers.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("黑话推断后台任务异常", exc_info=task.exception())
        self._wake.set()

    async def _run_item(self, job_id: str, index: int) -> None:
        from .jargon_miner import JargonMiner

        job = self._jobs[job_id]
        item = job["items"][index]
        result = InferenceResult(status="failed", reason="任务未完成")
        try:
            item["status"] = "running"
            self._finish_job(job)
            await self._persist(job)
            for attempt in range(global_config.jargon.inference.max_retries + 1):
                invalid = await asyncio.to_thread(self._validate_item, item)
                if invalid is not None:
                    result = invalid
                    break
                item["attempts"] += 1
                await asyncio.to_thread(self._write_state, item, job["source"])

                async def before_request() -> None:
                    await self._before_request()
                    item["requests"] += 1

                try:
                    result = await JargonMiner(item["session_id"], item["session_name"])._infer_meaning_by_id(
                        item["jargon_id"],
                        before_request=before_request,
                        source=job["source"],
                        job_id=job_id,
                    )
                except Exception as exc:
                    logger.exception("黑话推断请求失败: %s", item["jargon_id"])
                    result = InferenceResult(status="failed", reason=str(exc)[:500])
                if result.status != "failed" or attempt == global_config.jargon.inference.max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 30))
        except asyncio.CancelledError:
            result = InferenceResult(status="interrupted", reason="服务停止，可手动重试")
            raise
        except Exception as exc:
            logger.exception("黑话推断任务失败: %s", item["jargon_id"])
            result = InferenceResult(status="failed", reason=str(exc)[:500])
        finally:
            if result.evidence_hash:
                item["evidence_hash"] = result.evidence_hash
            item.update(status=result.status, reason=result.reason)
            self._finish_job(job)
            try:
                await asyncio.to_thread(self._write_state, item, job["source"])
                await self._persist(job)
            except Exception as exc:
                item.update(status="failed", reason=f"任务状态写入失败: {str(exc)[:400]}")
                self._finish_job(job)
                logger.exception("黑话推断任务状态写入失败: %s", job_id)
                raise
            else:
                if job["status"] not in ACTIVE and job["status"] != "cancelling":
                    self._jobs.pop(job_id, None)
            finally:
                # 完成持久化之后才释放词条，避免旧任务的终态覆盖新任务状态。
                self._reserved.discard(item["jargon_id"])

    async def _schedule(self) -> None:
        while True:
            self._schedule_wake.clear()
            settings = global_config.jargon.inference
            if settings.scheduled_enabled:
                request: Dict[str, Any] = {
                    "mode": "missing" if settings.scheduled_scope == "pending" else "all",
                    "force": True,
                }
                if settings.scheduled_session_ids:
                    request["filters"] = {"session_ids": list(settings.scheduled_session_ids)}
                try:
                    await self.submit(request, source="scheduled")
                except Exception:
                    logger.exception("定期黑话推断扫描失败")
            try:
                await asyncio.wait_for(self._schedule_wake.wait(), timeout=settings.scan_interval_hours * 3600)
            except TimeoutError:
                pass

    async def list_jobs(self) -> List[Dict[str, Any]]:
        self._check_ready()

        def read() -> List[Dict[str, Any]]:
            with get_db_session() as session:
                return [
                    _job_summary(_deserialize(row))
                    for row in session.exec(
                        select(JargonInferenceJob).order_by(col(JargonInferenceJob.created_at).desc()).limit(50)
                    ).all()
                ]

        return await asyncio.to_thread(read)

    async def _load_job(self, job_id: str) -> Dict[str, Any]:
        if job_id in self._jobs:
            return self._jobs[job_id]

        def read() -> Dict[str, Any]:
            with get_db_session() as session:
                row = session.get(JargonInferenceJob, job_id)
                if row is None:
                    raise LookupError("推断任务不存在")
                return _deserialize(row)

        return await asyncio.to_thread(read)

    async def get_job(self, job_id: str, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        self._check_ready()
        job = await self._load_job(job_id)
        items = job["items"][(page - 1) * page_size : page * page_size]
        return {
            "job": _job_summary(job),
            "items": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in ("snapshot", "evidence_hash", "session_id", "count")
                }
                for item in items
            ],
            "total": len(job["items"]),
        }

    async def cancel(self, job_id: str) -> Dict[str, Any]:
        self._check_ready()
        async with self._submit_lock:
            job = await self._load_job(job_id)
            cancelled = [item for item in job["items"] if item["status"] == "queued"]
            # 先同步标记全部待处理项，调度器不会在一次数据库等待期间领取剩余项。
            for item in cancelled:
                item.update(status="cancelled", reason="用户已取消尚未开始的任务")
            self._pending = deque((pending_id, index) for pending_id, index in self._pending if pending_id != job_id)
            job["status"] = "cancelling"
            self._finish_job(job)
            try:
                for item in cancelled:
                    await asyncio.to_thread(self._write_state, item, job["source"])
                await self._persist(job)
            finally:
                for item in cancelled:
                    self._reserved.discard(item["jargon_id"])
            if job["status"] not in ACTIVE and job["status"] != "cancelling":
                self._jobs.pop(job_id, None)
            return _job_summary(job)

    async def retry(self, job_id: str) -> Dict[str, Any]:
        self._check_ready()
        job = await self._load_job(job_id)
        ids = [item["jargon_id"] for item in job["items"] if item["status"] in RETRYABLE]
        if not ids:
            raise ValueError("没有可重试的失败或中断项")
        options = dict(job["options"], ids=ids)
        # 原筛选可能是“从未推断”，执行失败后其状态已改变；重试范围由失败项确定。
        options.pop("last_status", None)
        return await self.submit(options)


_service: Optional[JargonInferenceService] = None


def get_jargon_inference_service() -> JargonInferenceService:
    global _service
    if _service is None:
        _service = JargonInferenceService()
    return _service
