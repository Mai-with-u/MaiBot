"""会话级回复效果 v2 观察器。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List
from weakref import WeakKeyDictionary, WeakSet

import asyncio
import time
import uuid

from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger
from src.common.reply_effect_fingerprint import extract_generation_fingerprints
from src.maisaka.context.history import build_session_message_visible_text

from .image_utils import extract_visual_attachments_from_sequence
from .judge import JudgeRunner, judge_reply_effect
from .models import (
    FollowupMessageSnapshot,
    ReplyAssociation,
    ReplyEffectRecord,
    ReplyEffectStatus,
    ReplySnapshot,
    SessionSnapshot,
    UserSnapshot,
    now_iso,
)
from .path_utils import build_reply_effect_chat_dir_name
from .quote_utils import extract_quote_target_ids
from .scoring import activity_bucket, score_reply_effect
from .storage import ReplyEffectStorage

SESSION_FOLLOWUP_LIMIT = 10
OBSERVATION_WINDOW_SECONDS = 600.0
EVALUATION_CONCURRENCY = 2
# Provider 的 30 秒 timeout 和模型任务的 240 秒 hard_timeout 仍各自生效；
# 此处限制单条回复效果评审的完整流程，包含一次 JSON 校验重试。
EVALUATION_TOTAL_TIMEOUT_SECONDS = 60.0
SHUTDOWN_DRAIN_SECONDS = 5.0

logger = get_logger("maisaka_reply_effect")
_EVALUATION_SEMAPHORES: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()
_ACTIVE_TRACKERS: WeakSet[Any] = WeakSet()


def _get_evaluation_semaphore() -> asyncio.Semaphore:
    """返回当前事件循环共享的评审并发限制器。"""

    event_loop = asyncio.get_running_loop()
    semaphore = _EVALUATION_SEMAPHORES.get(event_loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(EVALUATION_CONCURRENCY)
        _EVALUATION_SEMAPHORES[event_loop] = semaphore
    return semaphore


class _EvaluationTotalTimeoutError(TimeoutError):
    """单条回复效果评审超过模块级总时限。"""


async def _judge_with_total_timeout(
    record: ReplyEffectRecord,
    candidates: List[ReplyEffectRecord],
    judge_runner: JudgeRunner,
) -> tuple[str, list[str], float, Dict[str, list[ReplyAssociation]]]:
    """限制完整评审耗时，并区分 Provider 自己抛出的网络超时。"""

    judge_task = asyncio.create_task(judge_reply_effect(record, candidates, judge_runner))
    try:
        done, _ = await asyncio.wait({judge_task}, timeout=EVALUATION_TOTAL_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        judge_task.cancel()
        await asyncio.gather(judge_task, return_exceptions=True)
        raise
    if judge_task not in done:
        judge_task.cancel()
        await asyncio.gather(judge_task, return_exceptions=True)
        raise _EvaluationTotalTimeoutError
    return await judge_task


class ReplyEffectTracker:
    """追踪单个 Maisaka 会话内 reply 工具回复后的群体反馈。"""

    def __init__(
        self,
        *,
        session_id: str,
        session_name: str,
        chat_stream: Any,
        judge_runner: JudgeRunner | None = None,
        storage: ReplyEffectStorage | None = None,
    ) -> None:
        self._session_id = session_id
        self._session_name = session_name
        self._chat_stream = chat_stream
        self._judge_runner = judge_runner
        self._storage = storage or ReplyEffectStorage()
        self._pending_records: Dict[str, ReplyEffectRecord] = {}
        self._tracked_records: Dict[str, ReplyEffectRecord] = {}
        self._timeout_tasks: Dict[str, asyncio.Task[None]] = {}
        self._evaluation_tasks: Dict[str, asyncio.Task[None]] = {}
        self._state_lock = asyncio.Lock()
        try:
            self._event_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None
        self._started = False
        _ACTIVE_TRACKERS.add(self)

    async def start(self) -> None:
        """恢复未完成记录，并重新建立观察计时器与评审任务。"""

        if self._event_loop is None:
            self._event_loop = asyncio.get_running_loop()
        if self._started:
            return
        self._started = True
        restored = self._storage.load_unfinished_records(self._session_id)
        related_effect_ids = {
            candidate_id
            for record in restored
            for followup in record.followup_messages
            for candidate_id in followup.candidate_effect_ids
        }
        related_effect_ids.update(
            association.effect_id
            for record in restored
            for followup in record.followup_messages
            for association in followup.associations
        )
        for related_record in self._storage.load_records_by_ids(related_effect_ids):
            self._tracked_records[related_record.effect_id] = related_record
        ready: list[tuple[str, str]] = []
        current_time = time.time()
        for record in restored:
            original_status = record.status
            record.status = ReplyEffectStatus.PENDING
            self._pending_records[record.effect_id] = record
            self._tracked_records[record.effect_id] = record
            elapsed_seconds = max(0.0, current_time - _parse_iso_timestamp(record.created_at))
            remaining_seconds = max(0.0, OBSERVATION_WINDOW_SECONDS - elapsed_seconds)
            if original_status == ReplyEffectStatus.EVALUATING:
                ready.append((record.effect_id, record.finalize_reason or "runtime_recovery"))
            elif len(record.followup_messages) >= SESSION_FOLLOWUP_LIMIT:
                ready.append((record.effect_id, "session_followups_limit"))
            elif remaining_seconds <= 0:
                ready.append((record.effect_id, "runtime_recovery"))
            else:
                self._timeout_tasks[record.effect_id] = asyncio.create_task(
                    self._finalize_after_timeout(record.effect_id, remaining_seconds)
                )

        for effect_id, reason in ready:
            await self._schedule_evaluation(effect_id, reason)
        if restored:
            logger.info(
                f"恢复回复效果记录：session_id={self._session_id} total={len(restored)} "
                f"ready={len(ready)} waiting={len(restored) - len(ready)}"
            )

    async def record_reply(
        self,
        *,
        tool_call_id: str,
        target_message: SessionMessage,
        set_quote: bool,
        reply_text: str,
        reply_segments: List[str],
        planner_reasoning: str,
        tool_context: Dict[str, Any] | None = None,
        send_results: List[Dict[str, Any]] | None = None,
        reply_metadata: Dict[str, Any] | None = None,
        context_snapshot: List[Dict[str, Any]] | None = None,
    ) -> ReplyEffectRecord:
        await self.start()
        effect_id = str(uuid.uuid4())
        target_user_info = target_message.message_info.user_info
        normalized_send_results = list(send_results or [])
        metadata = dict(reply_metadata or {})
        sent_message_ids = [
            str(item.get("message_id") or "").strip()
            for item in normalized_send_results
            if str(item.get("message_id") or "").strip()
        ]
        model_name, request_fingerprint, prompt_fingerprint = extract_generation_fingerprints(metadata)
        pre_activity_count = _count_pre_activity(context_snapshot or [])
        record = ReplyEffectRecord(
            effect_id=effect_id,
            status=ReplyEffectStatus.PENDING,
            created_at=now_iso(),
            updated_at=now_iso(),
            session=self._build_session_snapshot(),
            reply=ReplySnapshot(
                tool_call_id=tool_call_id,
                target_message_id=target_message.message_id,
                set_quote=set_quote,
                reply_text=reply_text,
                reply_segments=list(reply_segments),
                planner_reasoning=planner_reasoning,
                sent_message_ids=sent_message_ids,
                model_name=model_name,
                request_fingerprint=request_fingerprint,
                prompt_fingerprint=prompt_fingerprint,
                tool_context=dict(tool_context or {}),
                send_results=normalized_send_results,
                reply_metadata=metadata,
            ),
            target_user=UserSnapshot(
                user_id=str(target_user_info.user_id or "").strip(),
                nickname=str(target_user_info.user_nickname or "").strip(),
                cardname=str(target_user_info.user_cardname or "").strip(),
            ),
            pre_activity_count=pre_activity_count,
            pre_activity_bucket=activity_bucket(pre_activity_count),
            context_snapshot=list(context_snapshot or []),
        )
        self._storage.create_record_file(record)
        async with self._state_lock:
            self._pending_records[effect_id] = record
            self._tracked_records[effect_id] = record
            self._timeout_tasks[effect_id] = asyncio.create_task(self._finalize_after_timeout(effect_id))
        return record

    async def observe_user_message(self, message: SessionMessage) -> None:
        """把消息写入当时所有 pending 候选，并锁定显式引用关系。"""

        await self.start()
        if message.session_id != self._session_id:
            return
        ready_effect_ids: list[str] = []
        async with self._state_lock:
            if not self._pending_records:
                return
            candidate_ids = list(self._pending_records)
            sent_id_to_effect = {
                message_id: effect_id
                for effect_id, record in self._pending_records.items()
                for message_id in record.reply.sent_message_ids
            }
            for record in self._pending_records.values():
                if record.status != ReplyEffectStatus.PENDING:
                    continue
                followup = self._build_followup_snapshot(message, record, candidate_ids)
                quoted_ids = set(followup.quote_target_ids)
                if followup.reply_to:
                    quoted_ids.add(followup.reply_to)
                for quoted_id in quoted_ids:
                    quoted_effect_id = sent_id_to_effect.get(quoted_id)
                    if quoted_effect_id:
                        followup.associations.append(
                            ReplyAssociation(
                                effect_id=quoted_effect_id,
                                attribution_type="explicit_quote",
                                attribution_confidence=1.0,
                                stance_target="bot_content",
                                stance="neutral",
                                contribution="maintain",
                                evaluator_confidence=0.0,
                            )
                        )
                record.followup_messages.append(followup)
                record.updated_at = now_iso()
                self._storage.save_record(record)
                if len(record.followup_messages) >= SESSION_FOLLOWUP_LIMIT:
                    ready_effect_ids.append(record.effect_id)

        for effect_id in ready_effect_ids:
            await self._schedule_evaluation(effect_id, "session_followups_limit")

    async def finalize_all(self, reason: str = "runtime_stop") -> None:
        for effect_id in list(self._pending_records):
            await self._schedule_evaluation(effect_id, reason)
        await self.wait_for_idle()

    async def stop(self) -> None:
        """短暂排空评审任务；未完成项保留 evaluating，供下次启动恢复。"""

        for effect_id in list(self._pending_records):
            await self._schedule_evaluation(effect_id, "runtime_stop")
        tasks = list(self._evaluation_tasks.values())
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_DRAIN_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def clear(self) -> None:
        """取消当前观察与评审，确保清空后旧记录不会被后台任务重新写回。"""

        async with self._state_lock:
            tasks = [*self._timeout_tasks.values(), *self._evaluation_tasks.values()]
            self._pending_records.clear()
            self._tracked_records.clear()
            self._timeout_tasks.clear()
            self._evaluation_tasks.clear()
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def finalize(self, effect_id: str, reason: str) -> None:
        task = await self._schedule_evaluation(effect_id, reason)
        if task is not None:
            await task

    async def wait_for_idle(self) -> None:
        """等待当前已登记的评审任务全部结束，供停机与测试使用。"""

        while self._evaluation_tasks:
            task_items = list(self._evaluation_tasks.items())
            tasks = [task for _, task in task_items]
            await asyncio.gather(*tasks, return_exceptions=True)
            for effect_id, task in task_items:
                if task.done():
                    self._evaluation_tasks.pop(effect_id, None)

    async def _schedule_evaluation(self, effect_id: str, reason: str) -> asyncio.Task[None] | None:
        """原子关闭观察窗口，并确保每条记录只创建一个评审任务。"""

        async with self._state_lock:
            record = self._pending_records.pop(effect_id, None)
            if record is None or record.status != ReplyEffectStatus.PENDING:
                return self._evaluation_tasks.get(effect_id)
            timeout_task = self._timeout_tasks.pop(effect_id, None)
            current_task = asyncio.current_task()
            if timeout_task is not None and timeout_task is not current_task:
                timeout_task.cancel()
            if reason == "session_followups_limit":
                record.followup_messages = record.followup_messages[:SESSION_FOLLOWUP_LIMIT]
            record.status = ReplyEffectStatus.EVALUATING
            record.finalize_reason = reason
            record.updated_at = now_iso()
            self._storage.save_record(record)
            evaluation_task = asyncio.create_task(self._evaluate_record(record, reason))
            self._evaluation_tasks[effect_id] = evaluation_task
            evaluation_task.add_done_callback(
                lambda _task, target_effect_id=effect_id: self._evaluation_tasks.pop(target_effect_id, None)
            )
            return evaluation_task

    async def _evaluate_record(self, record: ReplyEffectRecord, reason: str) -> None:
        """在有限并发下执行语义归因、评分和最终持久化。"""

        async with _get_evaluation_semaphore():
            candidate_ids = {
                candidate_id
                for followup in record.followup_messages
                for candidate_id in followup.candidate_effect_ids
            }
            candidate_ids.add(record.effect_id)
            candidates = sorted(
                (self._tracked_records[item] for item in candidate_ids if item in self._tracked_records),
                key=lambda item: (
                    item.effect_id != record.effect_id,
                    abs(_parse_iso_timestamp(item.created_at) - _parse_iso_timestamp(record.created_at)),
                ),
            )
            try:
                primary, secondary, strategy_confidence, associations = await _judge_with_total_timeout(
                    record,
                    candidates,
                    self._judge_runner,
                )
                record.reply.strategy_primary = primary
                record.reply.strategy_secondary = secondary
                record.reply.strategy_confidence = strategy_confidence
                await self._apply_associations(associations)
                history = self._storage.load_finalized_records(exclude_effect_id=record.effect_id)
                record.scores = score_reply_effect(
                    record,
                    history,
                    observation_complete=reason in {"window_timeout", "session_followups_limit"},
                )
                record.status = ReplyEffectStatus.FINALIZED
            except _EvaluationTotalTimeoutError:
                record.status = ReplyEffectStatus.EVALUATION_FAILED
                record.evaluation_error = (
                    f"回复效果评审超过总时限 {EVALUATION_TOTAL_TIMEOUT_SECONDS:g} 秒"
                )
            except Exception as exc:
                record.status = ReplyEffectStatus.EVALUATION_FAILED
                record.evaluation_error = str(exc)
            if record.status == ReplyEffectStatus.EVALUATION_FAILED:
                logger.warning(
                    f"回复效果评审失败：effect_id={record.effect_id} "
                    f"session_id={self._session_id} error={record.evaluation_error}"
                )
            record.finalized_at = now_iso()
            record.updated_at = record.finalized_at
            record.finalize_reason = reason
            record.confidence_note = self._build_confidence_note(record)
            record.followup_summary = self._build_followup_summary(record)
            self._storage.save_record(record)

    async def _apply_associations(self, evaluated: Dict[str, list[ReplyAssociation]]) -> None:
        """把一次批量评审的关联边同步到所有仍保留的候选记录。"""

        async with self._state_lock:
            for tracked_record in self._tracked_records.values():
                changed = False
                for followup in tracked_record.followup_messages:
                    parsed = evaluated.get(followup.message_id)
                    if parsed is None:
                        continue
                    existing = {item.effect_id: item for item in followup.associations}
                    for association in parsed:
                        locked = existing.get(association.effect_id)
                        if locked is not None and locked.attribution_type == "explicit_quote":
                            association.attribution_type = "explicit_quote"
                            association.attribution_confidence = 1.0
                        existing[association.effect_id] = association
                    followup.associations = list(existing.values())
                    changed = True
                if changed and tracked_record.status in {
                    ReplyEffectStatus.PENDING,
                    ReplyEffectStatus.EVALUATING,
                }:
                    self._storage.save_record(tracked_record)

    def _build_session_snapshot(self) -> SessionSnapshot:
        platform = str(self._chat_stream.platform or "").strip()
        group_id = str(self._chat_stream.group_id or "").strip()
        user_id = str(self._chat_stream.user_id or "").strip()
        return SessionSnapshot(
            session_id=self._session_id,
            platform_type_id=build_reply_effect_chat_dir_name(self._session_id),
            platform=platform,
            chat_type="group" if self._chat_stream.is_group_session else "private",
            group_id=group_id,
            user_id=user_id,
            session_name=self._session_name,
        )

    def _build_followup_snapshot(
        self,
        message: SessionMessage,
        record: ReplyEffectRecord,
        candidate_ids: List[str],
    ) -> FollowupMessageSnapshot:
        user_info = message.message_info.user_info
        plain_text = str(message.processed_plain_text or "").strip()
        try:
            visible_text = build_session_message_visible_text(message)
        except Exception:
            visible_text = plain_text
        user_id = str(user_info.user_id or "").strip()
        return FollowupMessageSnapshot(
            message_id=str(message.message_id or "").strip(),
            timestamp=_message_timestamp_to_iso(message),
            user_id=user_id,
            nickname=str(user_info.user_nickname or "").strip(),
            cardname=str(user_info.user_cardname or "").strip(),
            visible_text=visible_text,
            plain_text=plain_text,
            latency_seconds=round(max(0.0, time.time() - _parse_iso_timestamp(record.created_at)), 3),
            is_target_user=bool(record.target_user.user_id and user_id == record.target_user.user_id),
            reply_to=str(message.reply_to or "").strip(),
            quote_target_ids=extract_quote_target_ids(message.raw_message),
            candidate_effect_ids=list(candidate_ids),
            attachments=extract_visual_attachments_from_sequence(message.raw_message),
        )

    async def _finalize_after_timeout(self, effect_id: str, delay_seconds: float = OBSERVATION_WINDOW_SECONDS) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await self._schedule_evaluation(effect_id, "window_timeout")
        except asyncio.CancelledError:
            return

    @staticmethod
    def _build_confidence_note(record: ReplyEffectRecord) -> str:
        if record.status == ReplyEffectStatus.EVALUATION_FAILED:
            return "评审输出校验失败，本记录未参与策略统计。"
        if not record.followup_messages:
            return "观察窗口内没有后续用户消息。"
        if record.finalize_reason == "runtime_stop":
            return "运行时停止导致观察窗口不完整，置信度已降低。"
        if record.finalize_reason == "runtime_recovery":
            return "重启恢复的观察窗口可能不完整，置信度已降低。"
        return "已完成完整观察窗口与语义归因。"

    @staticmethod
    def _build_followup_summary(record: ReplyEffectRecord) -> Dict[str, Any]:
        associated_ids = {
            followup.message_id
            for followup in record.followup_messages
            if any(item.effect_id == record.effect_id for item in followup.associations)
        }
        return {
            "total_count": len(record.followup_messages),
            "associated_count": len(associated_ids),
            "participant_count": len(
                {item.user_id for item in record.followup_messages if item.message_id in associated_ids}
            ),
        }


async def clear_active_reply_effect_trackers() -> int:
    """清空当前进程中的全部观察状态，返回受影响的追踪器数量。"""

    trackers = list(_ACTIVE_TRACKERS)
    current_loop = asyncio.get_running_loop()
    local_trackers = [tracker for tracker in trackers if tracker._event_loop in {None, current_loop}]
    foreign_trackers = [
        tracker
        for tracker in trackers
        if tracker._event_loop is not None
        and tracker._event_loop is not current_loop
        and tracker._event_loop.is_running()
    ]
    if local_trackers:
        await asyncio.gather(*(tracker.clear() for tracker in local_trackers), return_exceptions=True)
    foreign_futures = []
    for tracker in foreign_trackers:
        if tracker._event_loop is None:
            continue
        clear_coroutine = tracker.clear()
        try:
            foreign_futures.append(
                asyncio.run_coroutine_threadsafe(clear_coroutine, tracker._event_loop)
            )
        except RuntimeError:
            clear_coroutine.close()
    if foreign_futures:
        await asyncio.gather(
            *(asyncio.wrap_future(future) for future in foreign_futures),
            return_exceptions=True,
        )
    return len(trackers)


def _count_pre_activity(context_snapshot: List[Dict[str, Any]]) -> int:
    cutoff = datetime.now().astimezone() - timedelta(minutes=2)
    count = 0
    for item in context_snapshot:
        if str(item.get("role") or "") == "assistant":
            continue
        try:
            timestamp = datetime.fromisoformat(str(item.get("timestamp") or ""))
            if timestamp.tzinfo is None:
                timestamp = timestamp.astimezone()
        except ValueError:
            continue
        if timestamp >= cutoff:
            count += 1
    return count


def _message_timestamp_to_iso(message: SessionMessage) -> str:
    if isinstance(message.timestamp, datetime):
        return message.timestamp.astimezone().isoformat(timespec="seconds")
    return now_iso()


def _parse_iso_timestamp(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()
