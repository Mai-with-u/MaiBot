"""会话级回复效果 v2 观察器。"""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Dict, List

import asyncio
import json
import time
import uuid

from src.chat.message_receive.message import SessionMessage
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
        self._finalize_lock = asyncio.Lock()

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
        effect_id = str(uuid.uuid4())
        target_user_info = target_message.message_info.user_info
        normalized_send_results = list(send_results or [])
        metadata = dict(reply_metadata or {})
        sent_message_ids = [
            str(item.get("message_id") or "").strip()
            for item in normalized_send_results
            if str(item.get("message_id") or "").strip()
        ]
        model_name, prompt_fingerprint = _extract_generation_version(metadata)
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
        self._pending_records[effect_id] = record
        self._tracked_records[effect_id] = record
        self._timeout_tasks[effect_id] = asyncio.create_task(self._finalize_after_timeout(effect_id))
        return record

    async def observe_user_message(self, message: SessionMessage) -> None:
        """把消息写入当时所有 pending 候选，并锁定显式引用关系。"""

        if not self._pending_records or message.session_id != self._session_id:
            return
        candidate_ids = list(self._pending_records)
        sent_id_to_effect = {
            message_id: effect_id
            for effect_id, record in self._pending_records.items()
            for message_id in record.reply.sent_message_ids
        }
        for _effect_id, record in list(self._pending_records.items()):
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
        for effect_id, record in list(self._pending_records.items()):
            if len(record.followup_messages) >= SESSION_FOLLOWUP_LIMIT:
                await self.finalize(effect_id, "session_followups_limit")

    async def finalize_all(self, reason: str = "runtime_stop") -> None:
        for effect_id in list(self._pending_records):
            await self.finalize(effect_id, reason)

    async def finalize(self, effect_id: str, reason: str) -> None:
        async with self._finalize_lock:
            record = self._pending_records.pop(effect_id, None)
            if record is None or record.status != ReplyEffectStatus.PENDING:
                return
            timeout_task = self._timeout_tasks.pop(effect_id, None)
            current_task = asyncio.current_task()
            if timeout_task is not None and timeout_task is not current_task:
                timeout_task.cancel()
            candidate_ids = {
                candidate_id
                for followup in record.followup_messages
                for candidate_id in followup.candidate_effect_ids
            }
            candidate_ids.add(record.effect_id)
            candidates = [self._tracked_records[item] for item in candidate_ids if item in self._tracked_records]
            try:
                primary, secondary, strategy_confidence, associations = await judge_reply_effect(
                    record,
                    candidates,
                    self._judge_runner,
                )
                record.reply.strategy_primary = primary
                record.reply.strategy_secondary = secondary
                record.reply.strategy_confidence = strategy_confidence
                self._apply_associations(associations)
                history = self._storage.load_finalized_records(exclude_effect_id=effect_id)
                record.scores = score_reply_effect(
                    record,
                    history,
                    observation_complete=reason in {"window_timeout", "session_followups_limit"},
                )
                record.status = ReplyEffectStatus.FINALIZED
            except Exception as exc:
                record.status = ReplyEffectStatus.EVALUATION_FAILED
                record.evaluation_error = str(exc)
            record.finalized_at = now_iso()
            record.updated_at = record.finalized_at
            record.finalize_reason = reason
            record.confidence_note = self._build_confidence_note(record)
            record.followup_summary = self._build_followup_summary(record)
            self._storage.save_record(record)

    def _apply_associations(self, evaluated: Dict[str, list[ReplyAssociation]]) -> None:
        """把一次批量评审的关联边同步到所有仍保留的候选记录。"""

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
            if changed and tracked_record.status == ReplyEffectStatus.PENDING:
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

    async def _finalize_after_timeout(self, effect_id: str) -> None:
        try:
            await asyncio.sleep(OBSERVATION_WINDOW_SECONDS)
            await self.finalize(effect_id, "window_timeout")
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


def _extract_generation_version(metadata: Dict[str, Any]) -> tuple[str, str]:
    monitor = metadata.get("monitor_detail")
    monitor = monitor if isinstance(monitor, dict) else {}
    metrics = monitor.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    model_name = str(metrics.get("model_name") or "").strip()
    prompt_payload = monitor.get("request_messages") or monitor.get("prompt_text") or ""
    serialized = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True, default=str)
    fingerprint = sha256(serialized.encode("utf-8")).hexdigest() if serialized else ""
    return model_name, fingerprint


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
