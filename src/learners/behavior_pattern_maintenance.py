from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlmodel import select

import json
import time

from src.common.database.database import get_db_session
from src.common.database.database_model import BehaviorExperiencePath
from src.common.logger import get_logger

from .behavior_pattern_store import (
    FEEDBACK_HISTORY_LIMIT,
    MAX_BEHAVIOR_SCORE,
    MIN_BEHAVIOR_SCORE,
)

logger = get_logger("behavior_pattern_maintenance")

MAINTENANCE_COOLDOWN_SECONDS = 60 * 60
DECAY_COOLDOWN_DAYS = 7
UNUSED_DECAY_AFTER_DAYS = 14
UNUSED_DISABLE_AFTER_DAYS = 60
UNRESPONDED_DECAY_AFTER_DAYS = 21
POSITIVE_STALE_DECAY_AFTER_DAYS = 60
MAINTENANCE_SOURCE = "behavior_pattern_maintenance"


@dataclass(frozen=True)
class BehaviorPatternMaintenanceResult:
    """行为表现维护任务的摘要结果。"""

    session_id: str
    scanned_count: int = 0
    decayed_count: int = 0
    disabled_count: int = 0
    merged_count: int = 0
    skipped_reason: str = ""
    touched_pattern_ids: list[int] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.decayed_count > 0 or self.disabled_count > 0 or self.merged_count > 0


class BehaviorPatternMaintenanceService:
    """集中维护行为表现的用进废退规则。"""

    def __init__(self) -> None:
        self._last_run_at_by_session_id: dict[str, float] = {}

    def maybe_maintain_session(
        self,
        *,
        session_id: str,
        related_session_ids: Optional[set[str]] = None,
        force: bool = False,
    ) -> BehaviorPatternMaintenanceResult:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return BehaviorPatternMaintenanceResult(session_id="", skipped_reason="empty_session_id")

        current_time = time.time()
        if not force:
            last_run_at = self._last_run_at_by_session_id.get(normalized_session_id, 0.0)
            if current_time - last_run_at < MAINTENANCE_COOLDOWN_SECONDS:
                return BehaviorPatternMaintenanceResult(
                    session_id=normalized_session_id,
                    skipped_reason="cooldown",
                )

        result = self.maintain_session(
            session_id=normalized_session_id,
            related_session_ids=related_session_ids,
        )
        self._last_run_at_by_session_id[normalized_session_id] = current_time
        return result

    def maintain_session(
        self,
        *,
        session_id: str,
        related_session_ids: Optional[set[str]] = None,
        now: Optional[datetime] = None,
    ) -> BehaviorPatternMaintenanceResult:
        normalized_session_id = str(session_id or "").strip()
        target_session_ids = self._normalize_session_ids(related_session_ids)
        target_session_ids.add(normalized_session_id)
        if not normalized_session_id or not target_session_ids:
            return BehaviorPatternMaintenanceResult(session_id=normalized_session_id, skipped_reason="empty_session_id")

        maintenance_time = now or datetime.now()
        result = BehaviorPatternMaintenanceResult(session_id=normalized_session_id)

        try:
            with get_db_session() as session:
                statement = select(BehaviorExperiencePath).where(
                    BehaviorExperiencePath.session_id.in_(target_session_ids)  # type: ignore[attr-defined]
                )
                patterns = list(session.exec(statement).all())
                result = BehaviorPatternMaintenanceResult(
                    session_id=normalized_session_id,
                    scanned_count=len(patterns),
                )
                if not patterns:
                    return result

                touched_pattern_ids: list[int] = []
                decayed_count = 0
                disabled_count = 0
                for pattern in patterns:
                    if not pattern.enabled:
                        continue
                    decay_result = self._apply_decay(pattern, now=maintenance_time)
                    if decay_result.decayed:
                        decayed_count += 1
                        if pattern.id is not None:
                            touched_pattern_ids.append(pattern.id)
                    if decay_result.disabled:
                        disabled_count += 1
                        if pattern.id is not None:
                            touched_pattern_ids.append(pattern.id)
                    if decay_result.decayed or decay_result.disabled:
                        session.add(pattern)

                result = BehaviorPatternMaintenanceResult(
                    session_id=normalized_session_id,
                    scanned_count=len(patterns),
                    decayed_count=decayed_count,
                    disabled_count=disabled_count,
                    merged_count=0,
                    touched_pattern_ids=self._dedupe_ids(touched_pattern_ids),
                )
                if result.changed:
                    logger.info(
                        f"行为表现维护完成: session_id={normalized_session_id} "
                        f"扫描={result.scanned_count} 衰减={result.decayed_count} "
                        f"禁用={result.disabled_count}"
                    )
                return result
        except Exception as exc:
            logger.error(f"行为表现维护失败: session_id={normalized_session_id} error={exc}")
            return BehaviorPatternMaintenanceResult(session_id=normalized_session_id, skipped_reason="error")

    @staticmethod
    def _normalize_session_ids(session_ids: Optional[set[str]]) -> set[str]:
        if not session_ids:
            return set()
        return {str(session_id or "").strip() for session_id in session_ids if str(session_id or "").strip()}

    @staticmethod
    def _normalize_text(text: str, *, max_length: int) -> str:
        normalized_text = " ".join(str(text or "").split()).strip()
        if len(normalized_text) <= max_length:
            return normalized_text
        return normalized_text[:max_length].rstrip()

    @staticmethod
    def _dedupe_ids(pattern_ids: Sequence[int]) -> list[int]:
        deduped_ids: list[int] = []
        for pattern_id in pattern_ids:
            if pattern_id not in deduped_ids:
                deduped_ids.append(pattern_id)
        return deduped_ids

    @staticmethod
    def _load_json_list(raw_value: Any) -> list[Any]:
        if not raw_value:
            return []
        if isinstance(raw_value, list):
            return raw_value
        if not isinstance(raw_value, str):
            return []
        try:
            parsed_value = json.loads(raw_value)
        except (TypeError, ValueError):
            return []
        return parsed_value if isinstance(parsed_value, list) else []

    @staticmethod
    def _dump_json_list(items: Sequence[Any]) -> str:
        return json.dumps(list(items), ensure_ascii=False)

    @staticmethod
    def _clamp_score(score: float) -> float:
        return min(MAX_BEHAVIOR_SCORE, max(MIN_BEHAVIOR_SCORE, score))

    @staticmethod
    def _days_since(now: datetime, timestamp: Optional[datetime]) -> int:
        if timestamp is None:
            return 0
        return max(0, (now - timestamp).days)

    @staticmethod
    def _latest_activity_time(pattern: BehaviorExperiencePath) -> datetime:
        timestamps = [
            timestamp
            for timestamp in [pattern.last_active_time, pattern.last_feedback_time, pattern.create_time]
            if timestamp is not None
        ]
        return max(timestamps) if timestamps else datetime.now()

    def _last_maintenance_time(self, pattern: BehaviorExperiencePath) -> Optional[datetime]:
        feedback_items = self._load_json_list(pattern.feedback_list)
        maintenance_times: list[datetime] = []
        for feedback_item in feedback_items:
            if not isinstance(feedback_item, dict):
                continue
            if feedback_item.get("source") != MAINTENANCE_SOURCE:
                continue
            raw_created_at = str(feedback_item.get("created_at") or "").strip()
            if not raw_created_at:
                continue
            try:
                maintenance_times.append(datetime.fromisoformat(raw_created_at))
            except ValueError:
                continue
        return max(maintenance_times) if maintenance_times else None

    def _append_maintenance_event(
        self,
        pattern: BehaviorExperiencePath,
        *,
        now: datetime,
        score_delta: float,
        status: str,
        reason: str,
        outcome: str = "",
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        feedback_items = self._load_json_list(pattern.feedback_list)
        event = {
            "score_delta": score_delta,
            "status": status,
            "reason": reason,
            "outcome": outcome,
            "session_id": pattern.session_id,
            "created_at": now.isoformat(timespec="seconds"),
            "source": MAINTENANCE_SOURCE,
        }
        if extra:
            event.update(extra)
        feedback_items.append(event)
        pattern.feedback_list = self._dump_json_list(feedback_items[-FEEDBACK_HISTORY_LIMIT:])

    @dataclass(frozen=True)
    class _DecayResult:
        decayed: bool = False
        disabled: bool = False

    def _apply_decay(self, pattern: BehaviorExperiencePath, *, now: datetime) -> _DecayResult:
        last_maintenance_time = self._last_maintenance_time(pattern)
        if last_maintenance_time is not None and self._days_since(now, last_maintenance_time) < DECAY_COOLDOWN_DAYS:
            return self._DecayResult()

        latest_activity_time = self._latest_activity_time(pattern)
        inactive_days = self._days_since(now, latest_activity_time)
        score_delta, reason = self._calculate_decay(pattern, inactive_days=inactive_days)
        decayed = score_delta < 0
        disabled = False

        if decayed:
            pattern.score = self._clamp_score(float(pattern.score or 0.0) + score_delta)
            self._append_maintenance_event(
                pattern,
                now=now,
                score_delta=score_delta,
                status="maintenance_decay",
                reason=reason,
            )

        if self._should_disable(pattern, inactive_days=inactive_days):
            pattern.enabled = False
            disabled = True
            self._append_maintenance_event(
                pattern,
                now=now,
                score_delta=0.0,
                status="maintenance_disable",
                reason="长期缺少有效强化或负反馈过多，暂时停止作为行为表现候选。",
            )

        if decayed or disabled:
            pattern.update_time = now
        return self._DecayResult(decayed=decayed, disabled=disabled)

    @staticmethod
    def _calculate_decay(pattern: BehaviorExperiencePath, *, inactive_days: int) -> tuple[float, str]:
        count = int(pattern.count or 0)
        activation_count = int(pattern.activation_count or 0)
        success_count = int(pattern.success_count or 0)
        failure_count = int(pattern.failure_count or 0)

        if count <= 1 and activation_count <= 0 and inactive_days >= UNUSED_DECAY_AFTER_DAYS:
            periods = min(4, max(1, inactive_days // UNUSED_DECAY_AFTER_DAYS))
            return -0.35 * periods, "一次性观察长期未被再次强化，按用进废退规则轻度衰减。"

        if activation_count > 0 and success_count <= 0 and inactive_days >= UNRESPONDED_DECAY_AFTER_DAYS:
            periods = min(3, max(1, inactive_days // UNRESPONDED_DECAY_AFTER_DAYS))
            return -0.25 * periods, "被选择后长期没有成功反馈，降低后续抽样权重。"

        if success_count > 0 and failure_count <= success_count and inactive_days >= POSITIVE_STALE_DECAY_AFTER_DAYS:
            return -0.15, "曾经有效但长期未再出现，轻微衰减以给新行为让路。"

        return 0.0, ""

    @staticmethod
    def _should_disable(pattern: BehaviorExperiencePath, *, inactive_days: int) -> bool:
        score = float(pattern.score or 0.0)
        count = int(pattern.count or 0)
        activation_count = int(pattern.activation_count or 0)
        success_count = int(pattern.success_count or 0)
        failure_count = int(pattern.failure_count or 0)

        if score <= MIN_BEHAVIOR_SCORE and (failure_count >= 2 or activation_count >= 3):
            return True
        if count <= 1 and activation_count <= 0 and inactive_days >= UNUSED_DISABLE_AFTER_DAYS and score <= -3.0:
            return True
        if failure_count >= 3 and success_count <= 0 and score <= -4.0:
            return True
        return False


behavior_pattern_maintenance = BehaviorPatternMaintenanceService()
