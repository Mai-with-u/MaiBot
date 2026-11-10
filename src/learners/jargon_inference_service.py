"""黑话推断的主循环任务队列：统一手动、次数触发与定期更新。"""

from collections import Counter, deque
from copy import deepcopy
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, text
from sqlmodel import Session, col, delete, select

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
from src.common.utils.utils_jargon import parse_session_id_dict
from src.config.config import config_manager, global_config

from .jargon_inference_result import InferenceResult, evidence_hash

logger = get_logger("jargon.inference")
ACTIVE = {"queued", "running"}
RETRYABLE = {"failed", "interrupted"}
MAX_MANUAL_ITEMS = 5000
JOB_CLEANUP_INTERVAL_SECONDS = 3600.0
THRESHOLD_RETRY_SECONDS = 30.0


async def _complete_database_write(operation: Callable[..., Any], *args: Any) -> Any:
    """等待已发起的数据库写入终止后再传播取消，避免旧线程晚于终态写回。"""
    writing = asyncio.create_task(asyncio.to_thread(operation, *args))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(writing)
            break
        except asyncio.CancelledError:
            cancelled = True
            if writing.done():
                result = writing.result()
                break
    if cancelled:
        raise asyncio.CancelledError
    return result


def _snapshot(record: Jargon) -> Dict[str, Any]:
    """只记录可解析的会话 ID；命中次数和存储格式不改变词条身份。"""
    return {
        "content": record.content,
        "session_ids": sorted(parse_session_id_dict(record.session_id_dict)),
        "is_global": record.is_global,
        "created_timestamp": str(record.created_timestamp),
    }


def _job_summary(job: Dict[str, Any]) -> Dict[str, Any]:
    """计算可公开的任务进度，不暴露证据快照和内部会话 ID。"""
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
    """读取持久任务；损坏的 JSON 向调用者显式报告。"""
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
        """初始化队列、保留期和阈值策略，等待主循环启动工作任务。"""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._reserved: Set[int] = set()
        self._pending: Deque[Tuple[str, int]] = deque()
        self._workers: Set[asyncio.Task] = set()
        self._dispatcher: Optional[asyncio.Task] = None
        self._scheduler: Optional[asyncio.Task] = None
        self._stop_task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._schedule_wake = asyncio.Event()
        self._submit_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._rescan_lock = asyncio.Lock()
        self._threshold_pending = False
        self._threshold_seen: Set[int] = set()
        self._threshold_retry_at = 0.0
        self._last_scheduled_scan: Optional[float] = None
        self._last_cleanup = 0.0
        self._retention_days = global_config.jargon.inference.job_retention_days
        self._cleanup_requested = False
        self._last_request = 0.0
        self._stopping = False
        self._threshold_target_policy = (
            tuple(global_config.jargon.inference.thresholds),
            global_config.jargon.inference.stop_after_last_threshold,
        )

    def _check_ready(self) -> None:
        """拒绝错误事件循环、尚未启动或正在停止时的请求。"""
        if self._loop is not asyncio.get_running_loop() or self._stopping:
            raise RuntimeError("黑话推断服务尚未就绪")

    async def start(self) -> None:
        """先恢复持久任务，再注册主循环上的调度器与配置回调。"""
        if self._loop is not None:
            return
        self._stopping = False
        await _complete_database_write(self._recover_interrupted_jobs)
        self._loop = asyncio.get_running_loop()
        self._last_cleanup = time.monotonic()
        try:
            config_manager.register_reload_callback(self.on_config_changed, sections=("jargon",))
            self._dispatcher = asyncio.create_task(self._dispatch(), name="jargon-inference-dispatch")
            self._scheduler = asyncio.create_task(self._schedule(), name="jargon-inference-schedule")
        except BaseException:
            self._loop = None
            config_manager.unregister_reload_callback(self.on_config_changed)
            tasks = [task for task in (self._dispatcher, self._scheduler) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def stop(self) -> None:
        """即使调用方取消也等待内部关停完成，防止遗留仍可写回的数据库线程。"""
        if self._loop is None:
            return
        if self._stop_task is None or self._stop_task.done():
            self._stop_task = asyncio.create_task(self._stop(), name="jargon-inference-stop")
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._stop_task)
                break
            except asyncio.CancelledError:
                cancelled = True
                if self._stop_task.done():
                    self._stop_task.result()
                    break
        if cancelled:
            raise asyncio.CancelledError

    async def _stop(self) -> None:
        """停止接收提交并等待事务，取消工作任务后持久化中断状态。"""
        self._stopping = True
        config_manager.unregister_reload_callback(self.on_config_changed)
        # 等待已进入事务的提交发布内存队列，再取消工作任务和标记中断。
        async with self._submit_lock:
            pass
        tasks = [task for task in (self._dispatcher, self._scheduler) if task is not None]
        tasks.extend(self._workers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # 队列不自动重放，重启后用户可以检查已完成结果并重试中断项。
        try:
            for job in list(self._jobs.values()):
                interrupted = []
                for item in job["items"]:
                    if item["status"] in ACTIVE:
                        item.update(status="interrupted", reason="服务已停止，可手动重试")
                        interrupted.append(item)
                await _complete_database_write(self._write_states, interrupted, job["source"])
                self._finish_job(job)
                await self._persist(job)
        finally:
            self._pending.clear()
            self._reserved.clear()
            self._workers.clear()
            self._jobs.clear()
            self._loop = None

    async def on_config_changed(self) -> None:
        """将 WebUI 线程的配置通知转交给队列所属的主循环。"""
        await run_on_main_loop(self._configuration_changed())

    async def _configuration_changed(self) -> None:
        """发布最新阈值策略并唤醒调度器，不在配置重载回调中等待数据库。"""
        if self._loop is None or self._stopping:
            return
        self._wake.set()
        if global_config.jargon.inference.job_retention_days != self._retention_days:
            self._retention_days = global_config.jargon.inference.job_retention_days
            self._cleanup_requested = True
        policy = (
            tuple(global_config.jargon.inference.thresholds),
            global_config.jargon.inference.stop_after_last_threshold,
        )
        if policy != self._threshold_target_policy:
            self._request_threshold_rescan(policy)
        self._schedule_wake.set()

    def _request_threshold_rescan(self, policy: Tuple[Tuple[int, ...], bool]) -> None:
        """每次策略切换创建独立断点，避免旧批次清除或覆盖新策略的进度。"""
        self._threshold_target_policy = policy
        self._threshold_seen = set()
        self._threshold_pending = True
        self._threshold_retry_at = 0.0

    @staticmethod
    def _recover_interrupted_jobs() -> None:
        """在一个事务中恢复活动任务；损坏数据回滚并保留供修复。"""
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
            session.flush()
            JargonInferenceService._prune_jobs(session)

    def _select(
        self,
        request: Dict[str, Any],
        source: str,
        reserved_ids: Optional[Set[int]] = None,
        excluded_ids: Optional[Set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """按筛选范围读取候选与关联状态，排除受保护、已预留或已扫描项。"""
        from src.webui.routers.jargon import (
            apply_jargon_list_filters,
            build_session_display_name_cache,
            build_session_id_dict_filter,
        )
        from .jargon_miner import JargonMiner

        filters = request.get("filters") or {}
        statement = apply_jargon_list_filters(
            select(Jargon, JargonInferenceState),
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
            existing_sessions: Set[str] = set()
            checked_sessions: Set[str] = set()
            eligible_count = 0
            for record, state in records:
                if excluded_ids and record.id in excluded_ids:
                    continue
                if source != "manual" and reserved_ids and record.id in reserved_ids:
                    continue
                if request.get("last_status") and (state.status if state else "unattempted") != request["last_status"]:
                    continue
                reason = ""
                reason_code = None
                status = "queued"
                if record.created_by == JargonCreatedBy.MANUAL or (state and state.locked):
                    status, reason = "protected", "人工维护或已锁定"
                    reason_code = "manual" if record.created_by == JargonCreatedBy.MANUAL else "locked"
                elif request.get("mode", "missing") == "missing" and (record.meaning or "").strip():
                    status, reason = "skipped", "已有含义"
                    reason_code = "has_meaning"
                elif not request.get("force", True) and not JargonMiner("", "")._should_infer_meaning(record):
                    status, reason = "skipped", "未达到自动推断条件"
                    reason_code = "threshold"
                snapshot = _snapshot(record)
                scope = set(snapshot["session_ids"])
                unchecked = scope - checked_sessions
                if unchecked:
                    existing_sessions.update(
                        session.exec(
                            select(ChatSession.session_id).where(col(ChatSession.session_id).in_(unchecked))
                        ).all()
                    )
                    checked_sessions.update(unchecked)
                session_ids = sorted(scope & existing_sessions)
                if source == "automatic" and request.get("trigger_session_id") not in session_ids:
                    continue
                if not reason and not session_ids:
                    status, reason = "missing_evidence", "词条没有可解析的所属会话"
                    reason_code = "no_session"
                if source != "manual" and reason:
                    continue
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
                        if state.status != "missing_evidence":
                            continue
                        from src.common.data_models.jargon_data_model import MaiJargon

                        recovered = JargonMiner("", "").recoverable_evidence_hash(MaiJargon.from_db_instance(record))
                        if recovered == fingerprint or recovered == evidence_hash(None):
                            continue
                        fingerprint = recovered
                items.append(
                    {
                        "jargon_id": record.id,
                        "content": record.content,
                        "session_id": request.get("trigger_session_id")
                        if request.get("trigger_session_id") in session_ids
                        else (session_ids[0] if session_ids else ""),
                        "session_name": "",
                        "status": status,
                        "reason": reason,
                        "reason_code": reason_code,
                        "attempts": 0,
                        "requests": 0,
                        "snapshot": snapshot,
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
                item["session_name"] = (
                    request["trigger_session_name"]
                    if item["session_id"] == request.get("trigger_session_id") and request.get("trigger_session_name")
                    else names.get(item["session_id"], "未关联聊天")
                )
        return items

    async def preview(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """预览可入队数量，按稳定原因代码统计跳过项，同时反映正在运行的预留。"""
        self._check_ready()
        items = await asyncio.to_thread(self._select, request, "manual")
        for item in items:
            if item["jargon_id"] in self._reserved:
                item.update(status="skipped", reason="已有排队或运行中的任务", reason_code="duplicate")
        reasons = Counter(item["reason_code"] for item in items if item["status"] != "queued")
        eligible = sum(item["status"] == "queued" for item in items)
        return {"total": len(items), "eligible": eligible, "skipped": len(items) - eligible, "reasons": dict(reasons)}

    async def submit(
        self, request: Dict[str, Any], source: str = "manual", *, excluded_ids: Optional[Set[int]] = None
    ) -> Dict[str, Any]:
        """先提交数据库事务再发布队列，取消也不会遗留孤立的活动记录。"""
        self._check_ready()
        async with self._submit_lock:
            self._check_ready()
            items = await asyncio.to_thread(self._select, request, source, set(self._reserved), excluded_ids)
            if source == "threshold" and excluded_ids is not None and excluded_ids is not self._threshold_seen:
                # 筛选期间阈值策略变化，尚未提交的批次交给新一轮重新判断。
                items = []
            for item in items:
                if item["jargon_id"] in self._reserved:
                    item.update(status="skipped", reason="已有排队或运行中的任务", reason_code="duplicate")
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
            if not items and source != "manual":
                return _job_summary(job)
            # 事务成功后才暴露队列；磁盘写入失败不会留下半个后台任务。
            saving = asyncio.create_task(asyncio.to_thread(self._save_new_job, job))
            cancelled = False
            # 已发起的数据库事务必须到达终态，取消请求也不能留下未发布的排队记录。
            while True:
                try:
                    await asyncio.shield(saving)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                    if saving.done():
                        saving.result()
                        break
            self._jobs[job["id"]] = job
            for index, item in enumerate(items):
                if item["status"] == "queued":
                    self._reserved.add(item["jargon_id"])
                    self._pending.append((job["id"], index))
            self._wake.set()
            if job["status"] not in ACTIVE:
                self._jobs.pop(job["id"], None)
            result = _job_summary(job)
            if excluded_ids is not None:
                excluded_ids.update(item["jargon_id"] for item in items)
            if cancelled:
                raise asyncio.CancelledError
            return result

    async def enqueue_auto(self, jargon_id: int, session_id: str, session_name: str) -> bool:
        """保留触发会话；入队异常记录日志并让当前学习批次继续。"""
        if self._loop is None or self._stopping:
            return False
        try:
            result = await self.submit(
                {
                    "ids": [jargon_id],
                    "mode": "all",
                    "force": False,
                    "trigger_session_id": session_id,
                    "trigger_session_name": session_name,
                },
                source="automatic",
            )
        except Exception:
            logger.exception("黑话自动推断入队失败: %s", jargon_id)
            return False
        return result["counts"].get("queued", 0) > 0

    async def rescan(self) -> Dict[str, Any]:
        """显式补扫等待全部批次提交，并返回第一批任务摘要。"""
        self._check_ready()
        async with self._rescan_lock:
            self._check_ready()
            if not self._threshold_pending:
                self._request_threshold_rescan(
                    (
                        tuple(global_config.jargon.inference.thresholds),
                        global_config.jargon.inference.stop_after_last_threshold,
                    )
                )
            try:
                return await self._drain_threshold_rescan()
            finally:
                self._schedule_wake.set()

    async def _drain_threshold_rescan(self) -> Dict[str, Any]:
        """持补扫锁提交批次；失败保留当前断点，策略改变则重新筛选。"""
        first = None
        while True:
            seen = self._threshold_seen
            try:
                result = await self.submit(
                    {"mode": "all", "force": False},
                    source="threshold",
                    excluded_ids=seen,
                )
            except Exception:
                if seen is self._threshold_seen:
                    self._threshold_retry_at = time.monotonic() + THRESHOLD_RETRY_SECONDS
                raise
            if result["total"]:
                if first is None:
                    first = result
                continue
            if seen is not self._threshold_seen:
                continue
            self._threshold_pending = False
            self._threshold_seen.clear()
            self._threshold_retry_at = 0.0
            return first if first is not None else result

    async def _persist(self, job: Dict[str, Any]) -> None:
        """串行持久化任务快照，防止较早的进度写入覆盖新结果。"""
        async with self._persist_lock:
            snapshot = deepcopy(job)
            await _complete_database_write(self._save_job, snapshot)

    @staticmethod
    def _save_job(job: Dict[str, Any]) -> None:
        """持久化任务进度快照，过期清理由启动恢复与调度器处理。"""
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
        """在写锁内复核词条与保护状态，原子创建任务和排队状态。"""
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
    def _prune_jobs(session: Session) -> None:
        """仅删除超出配置保留期的终态任务，不触发模型调用或清理词条历史。"""
        cutoff = time.time() - global_config.jargon.inference.job_retention_days * 24 * 3600
        session.exec(
            delete(JargonInferenceJob).where(
                col(JargonInferenceJob.status).notin_(["queued", "running", "cancelling"]),
                JargonInferenceJob.updated_at < cutoff,
            )
        )

    @staticmethod
    def _cleanup_jobs() -> None:
        """空闲期间也执行到期清理，活动任务始终保留。"""
        with get_db_session() as session:
            JargonInferenceService._prune_jobs(session)

    @staticmethod
    def _write_state(item: Dict[str, Any], source: str) -> None:
        """使用批量状态写入的同一事务规则保存单条进度。"""
        JargonInferenceService._write_states([item], source)

    @staticmethod
    def _write_states(items: List[Dict[str, Any]], source: str) -> None:
        """在一个写事务中更新状态，保留 ID 重用及锁定保护。"""
        with get_db_session() as session:
            session.connection().execute(text("BEGIN IMMEDIATE"))
            for item in items:
                record = session.get(Jargon, item["jargon_id"])
                if record is None:
                    continue
                state = session.get(JargonInferenceState, item["jargon_id"])
                if _snapshot(record) != item["snapshot"]:
                    if (
                        state is not None
                        and state.status in ACTIVE
                        and item["status"] not in ACTIVE
                        and str(record.created_timestamp) == item["snapshot"]["created_timestamp"]
                    ):
                        state.status = "stale"
                        state.reason = "词条内容或归属已修改，请重新选择"
                        session.add(state)
                    continue
                state = state or JargonInferenceState(jargon_id=item["jargon_id"])
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
        """执行前复核身份、人工维护与锁定，并刷新证据指纹。"""
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
        """根据逐项状态更新任务阶段和完成时间。"""
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
        """在全部并发工作任务之间共享模型请求间隔。"""
        async with self._rate_lock:
            wait = global_config.jargon.inference.request_interval_seconds - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _dispatch(self) -> None:
        """在交还事件循环前认领任务项，防止取消与启动竞争。"""
        while True:
            await self._wake.wait()
            self._wake.clear()
            while (
                not self._stopping
                and self._pending
                and len(self._workers) < global_config.jargon.inference.max_concurrency
            ):
                job_id, index = self._pending.popleft()
                if self._jobs[job_id]["items"][index]["status"] != "queued":
                    continue
                # 在交还事件循环前占用该项，取消只能处理尚未分配给工作任务的词条。
                self._jobs[job_id]["items"][index]["status"] = "running"
                task = asyncio.create_task(self._run_item(job_id, index), name=f"jargon-inference-{job_id}-{index}")
                self._workers.add(task)
                task.add_done_callback(self._worker_finished)

    def _worker_finished(self, task: asyncio.Task) -> None:
        """回收工作槽位并记录异常，唤醒等待中的任务。"""
        self._workers.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("黑话推断后台任务异常", exc_info=task.exception())
        self._wake.set()

    async def _run_item(self, job_id: str, index: int) -> None:
        """带有限重试执行一条推断，持久化终态后才释放词条预留。"""
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
                await _complete_database_write(self._write_state, item, job["source"])

                async def before_request() -> None:
                    """取得全局速率许可后记录实际模型请求数。"""
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
            item.update(
                status=result.status,
                reason=result.reason,
                is_jargon=result.is_jargon if result.status == "succeeded" else None,
            )
            self._finish_job(job)
            try:
                await _complete_database_write(self._write_state, item, job["source"])
                await self._persist(job)
            except Exception as exc:
                item.update(status="failed", reason=f"任务状态写入失败: {str(exc)[:400]}", is_jargon=None)
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
        """配置重载只重新计算截止时间，不提前触发已安排的扫描。"""
        while not self._stopping:
            self._schedule_wake.clear()
            now = time.monotonic()
            if self._cleanup_requested or now - self._last_cleanup >= JOB_CLEANUP_INTERVAL_SECONDS:
                try:
                    await _complete_database_write(self._cleanup_jobs)
                except Exception:
                    logger.exception("黑话推断过期任务清理失败")
                self._cleanup_requested = False
                self._last_cleanup = now
            if self._threshold_pending and now >= self._threshold_retry_at:
                async with self._rescan_lock:
                    if self._threshold_pending and time.monotonic() >= self._threshold_retry_at:
                        try:
                            await self._drain_threshold_rescan()
                        except Exception:
                            logger.exception("黑话阈值补扫失败，将从已提交批次后重试")
            settings = global_config.jargon.inference
            now = time.monotonic()
            due = (
                now
                if self._last_scheduled_scan is None
                else self._last_scheduled_scan + settings.scan_interval_hours * 3600
            )
            if settings.scheduled_enabled and now >= due:
                self._last_scheduled_scan = now
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
            settings = global_config.jargon.inference
            now = time.monotonic()
            timeout = min(
                settings.scan_interval_hours * 3600, max(0, self._last_cleanup + JOB_CLEANUP_INTERVAL_SECONDS - now)
            )
            if settings.scheduled_enabled and self._last_scheduled_scan is not None:
                timeout = min(timeout, max(0, self._last_scheduled_scan + settings.scan_interval_hours * 3600 - now))
            if self._threshold_pending:
                timeout = min(timeout, max(0, self._threshold_retry_at - now))
            try:
                await asyncio.wait_for(self._schedule_wake.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def list_jobs(
        self, page: int = 1, page_size: int = 50, source: Optional[str] = None, active_only: bool = False
    ) -> Dict[str, Any]:
        """分页查看各来源任务，活动任务始终排在终态历史之前。"""
        self._check_ready()

        def read() -> Dict[str, Any]:
            """在同一数据库会话中读取筛选后的总数和当前页。"""
            with get_db_session() as session:
                conditions = []
                if source:
                    conditions.append(JargonInferenceJob.source == source)
                active = col(JargonInferenceJob.status).in_(["queued", "running", "cancelling"])
                if active_only:
                    conditions.append(active)
                total = session.exec(select(func.count()).select_from(JargonInferenceJob).where(*conditions)).one()
                rows = session.exec(
                    select(JargonInferenceJob)
                    .where(*conditions)
                    .order_by(
                        active.desc(),
                        col(JargonInferenceJob.created_at).desc(),
                        col(JargonInferenceJob.id).desc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                ).all()
                return {
                    "items": [_job_summary(_deserialize(row)) for row in rows],
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                }

        return await asyncio.to_thread(read)

    async def _load_job(self, job_id: str) -> Dict[str, Any]:
        """优先读取活动任务，终态任务按 ID 从数据库恢复。"""
        if job_id in self._jobs:
            return self._jobs[job_id]

        def read() -> Dict[str, Any]:
            """从持久记录恢复已离开活动队列的任务。"""
            with get_db_session() as session:
                row = session.get(JargonInferenceJob, job_id)
                if row is None:
                    raise LookupError("推断任务不存在")
                return _deserialize(row)

        return await asyncio.to_thread(read)

    async def get_job(self, job_id: str, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        """分页返回任务保存时的分类结论，未得到结论的词条返回空值。"""
        self._check_ready()
        job = await self._load_job(job_id)
        items = job["items"][(page - 1) * page_size : page * page_size]
        return {
            "job": _job_summary(job),
            "items": [
                {
                    **{
                        key: value
                        for key, value in item.items()
                        if key not in ("snapshot", "evidence_hash", "session_id", "count", "is_jargon")
                    },
                    "is_jargon": item.get("is_jargon") if item["status"] == "succeeded" else None,
                }
                for item in items
            ],
            "total": len(job["items"]),
        }

    async def cancel(self, job_id: str) -> Dict[str, Any]:
        """一次标记全部待处理项并批量写入，正在运行的项保留其结果。"""
        self._check_ready()
        async with self._submit_lock:
            self._check_ready()
            job = await self._load_job(job_id)
            cancelled = [item for item in job["items"] if item["status"] == "queued"]
            if not cancelled:
                return _job_summary(job)
            # 先同步标记全部待处理项，调度器不会在一次数据库等待期间领取剩余项。
            for item in cancelled:
                item.update(status="cancelled", reason="用户已取消尚未开始的任务")
            self._pending = deque((pending_id, index) for pending_id, index in self._pending if pending_id != job_id)
            job["status"] = "cancelling"
            self._finish_job(job)

            async def persist_cancellation() -> None:
                # 两次写入都完成后才释放预留，HTTP 取消不会中断其中一步。
                await _complete_database_write(self._write_states, cancelled, job["source"])
                await self._persist(job)

            persisting = asyncio.create_task(persist_cancellation())
            request_cancelled = False
            try:
                while True:
                    try:
                        await asyncio.shield(persisting)
                        break
                    except asyncio.CancelledError:
                        request_cancelled = True
                        if persisting.done():
                            persisting.result()
                            break
            finally:
                for item in cancelled:
                    self._reserved.discard(item["jargon_id"])
                if job["status"] not in ACTIVE and job["status"] != "cancelling":
                    self._jobs.pop(job_id, None)
            if request_cancelled:
                raise asyncio.CancelledError
            return _job_summary(job)

    async def retry(self, job_id: str) -> Dict[str, Any]:
        """只重试失败或中断项，不复用已失效的旧状态筛选。"""
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
    """返回进程级队列实例，由主程序统一管理生命周期。"""
    global _service
    if _service is None:
        _service = JargonInferenceService()
    return _service
