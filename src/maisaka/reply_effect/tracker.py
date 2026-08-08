"""会话级回复效果观察器。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import asyncio
import time
import uuid

from src.chat.message_receive.message import SessionMessage
from src.maisaka.context.history import build_session_message_visible_text

from .image_utils import extract_visual_attachments_from_sequence
from .judge import JudgeRunner, judge_reply_effect
from .models import (
    FollowupMessageSnapshot,
    ReplyEffectRecord,
    ReplyEffectStatus,
    ReplySnapshot,
    SessionSnapshot,
    UserSnapshot,
    now_iso,
)
from .quote_utils import extract_quote_target_ids
from .path_utils import build_reply_effect_chat_dir_name
from .scoring import (
    has_explicit_negative_feedback,
    has_repair_loop,
    score_reply_effect,
)
from .storage import ReplyEffectStorage

TARGET_USER_FOLLOWUP_LIMIT = 2
SESSION_FOLLOWUP_LIMIT = 5
OBSERVATION_WINDOW_SECONDS = 600.0


class ReplyEffectTracker:
    """追踪单个 Maisaka 会话内 reply 工具回复后的用户反馈。"""

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
        self._timeout_tasks: Dict[str, asyncio.Task[None]] = {}

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
        """登记一条已经成功发出的 reply 回复。"""

        effect_id = str(uuid.uuid4())
        target_user_info = target_message.message_info.user_info
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
                tool_context=dict(tool_context or {}),
                send_results=list(send_results or []),
                reply_metadata=dict(reply_metadata or {}),
            ),
            target_user=UserSnapshot(
                user_id=str(target_user_info.user_id or "").strip(),
                nickname=str(target_user_info.user_nickname or "").strip(),
                cardname=str(target_user_info.user_cardname or "").strip(),
            ),
            context_snapshot=list(context_snapshot or []),
        )
        self._storage.create_record_file(record)
        self._pending_records[effect_id] = record
        self._timeout_tasks[effect_id] = asyncio.create_task(self._finalize_after_timeout(effect_id))
        return record

    async def observe_user_message(self, message: SessionMessage) -> None:
        """观察一条后续用户消息，并在满足规则时完成相关 pending 记录。"""

        if not self._pending_records or message.session_id != self._session_id:
            return

        for effect_id, record in list(self._pending_records.items()):
            if record.status != ReplyEffectStatus.PENDING:
                continue
            followup = self._build_followup_snapshot(message, record)
            record.followup_messages.append(followup)
            record.updated_at = now_iso()
            self._storage.save_record(record)

            reason = self._resolve_finalize_reason(record)
            if reason:
                await self.finalize(effect_id, reason)

    async def finalize_all(self, reason: str = "runtime_stop") -> None:
        """强制完成当前会话所有 pending 记录。"""

        for effect_id in list(self._pending_records.keys()):
            await self.finalize(effect_id, reason)

    async def finalize(self, effect_id: str, reason: str) -> None:
        """完成一条 pending 记录并写回 JSON。"""

        record = self._pending_records.pop(effect_id, None)
        if record is None or record.status == ReplyEffectStatus.FINALIZED:
            return

        timeout_task = self._timeout_tasks.pop(effect_id, None)
        current_task = asyncio.current_task()
        if timeout_task is not None and timeout_task is not current_task:
            timeout_task.cancel()

        rubric_scores, judge_error = await judge_reply_effect(record, self._judge_runner)
        record.scores = score_reply_effect(
            record.followup_messages,
            rubric_scores,
            target_user_id=record.target_user.user_id,
            judge_error=judge_error,
        )
        record.status = ReplyEffectStatus.FINALIZED
        record.finalized_at = now_iso()
        record.updated_at = record.finalized_at
        record.finalize_reason = reason
        record.confidence_note = self._build_confidence_note(record)
        record.followup_summary = self._build_followup_summary(record)
        self._storage.save_record(record)

    def _build_session_snapshot(self) -> SessionSnapshot:
        platform = str(getattr(self._chat_stream, "platform", "") or "").strip()
        group_id = str(getattr(self._chat_stream, "group_id", "") or "").strip()
        user_id = str(getattr(self._chat_stream, "user_id", "") or "").strip()
        is_group_session = bool(getattr(self._chat_stream, "is_group_session", False))
        return SessionSnapshot(
            session_id=self._session_id,
            platform_type_id=build_reply_effect_chat_dir_name(self._session_id),
            platform=platform,
            chat_type="group" if is_group_session else "private",
            group_id=group_id,
            user_id=user_id,
            session_name=self._session_name,
        )

    def _build_followup_snapshot(
        self,
        message: SessionMessage,
        record: ReplyEffectRecord,
    ) -> FollowupMessageSnapshot:
        user_info = message.message_info.user_info
        plain_text = str(message.processed_plain_text or "").strip()
        try:
            visible_text = build_session_message_visible_text(message)
        except Exception:
            visible_text = plain_text
        latency_seconds = max(0.0, time.time() - _parse_iso_timestamp(record.created_at))
        user_id = str(user_info.user_id or "").strip()
        return FollowupMessageSnapshot(
            message_id=str(message.message_id or "").strip(),
            timestamp=_message_timestamp_to_iso(message),
            user_id=user_id,
            nickname=str(user_info.user_nickname or "").strip(),
            cardname=str(user_info.user_cardname or "").strip(),
            visible_text=visible_text,
            plain_text=plain_text,
            latency_seconds=round(latency_seconds, 3),
            is_target_user=bool(record.target_user.user_id and user_id == record.target_user.user_id),
            quote_target_ids=extract_quote_target_ids(message.raw_message),
            attachments=extract_visual_attachments_from_sequence(message.raw_message),
        )

    def _resolve_finalize_reason(self, record: ReplyEffectRecord) -> str:
        target_user_id = record.target_user.user_id
        target_followups = [
            followup
            for followup in record.followup_messages
            if target_user_id and followup.user_id == target_user_id
        ]
        has_target_feedback = bool(target_followups)
        if has_explicit_negative_feedback(target_followups, target_user_id=target_user_id, allow_indirect=False):
            return "explicit_negative"
        if has_repair_loop(target_followups, target_user_id=target_user_id, allow_indirect=False):
            return "repair_loop"
        if len(target_followups) >= TARGET_USER_FOLLOWUP_LIMIT:
            return "target_user_followups"

        if not target_user_id or not has_target_feedback:
            allow_indirect = not target_user_id
            if has_explicit_negative_feedback(
                record.followup_messages,
                target_user_id=target_user_id,
                allow_indirect=allow_indirect,
            ):
                return "explicit_negative"
            if has_repair_loop(
                record.followup_messages,
                target_user_id=target_user_id,
                allow_indirect=allow_indirect,
            ):
                return "repair_loop"
            if len(record.followup_messages) >= SESSION_FOLLOWUP_LIMIT:
                return "session_followups_limit"

        return ""

    async def _finalize_after_timeout(self, effect_id: str) -> None:
        try:
            await asyncio.sleep(OBSERVATION_WINDOW_SECONDS)
            await self.finalize(effect_id, "window_timeout")
        except asyncio.CancelledError:
            return

    @staticmethod
    def _build_confidence_note(record: ReplyEffectRecord) -> str:
        if not record.followup_messages:
            return "没有观察到后续用户消息，行为分使用保守中性信号。"
        if any(followup.is_target_user for followup in record.followup_messages):
            return "行为反馈包含回复对象本人的后续发言。"
        return "行为反馈来自同会话其他用户，不是回复对象本人，置信度较低。"

    @staticmethod
    def _build_followup_summary(record: ReplyEffectRecord) -> Dict[str, Any]:
        target_count = sum(1 for followup in record.followup_messages if followup.is_target_user)
        return {
            "total_count": len(record.followup_messages),
            "target_user_count": target_count,
            "other_user_count": len(record.followup_messages) - target_count,
            "target_user_id": record.target_user.user_id,
        }


def _message_timestamp_to_iso(message: SessionMessage) -> str:
    timestamp = getattr(message, "timestamp", None)
    if isinstance(timestamp, datetime):
        return timestamp.astimezone().isoformat(timespec="seconds")
    return now_iso()


def _parse_iso_timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return time.time()
