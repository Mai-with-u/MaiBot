"""回复效果观察器的数据模型。"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 3
EVALUATION_VERSION = 6
COMPLETE_OBSERVATION_REASONS = frozenset({"window_timeout", "session_followups_limit"})


class ReplyEffectStatus(str, Enum):
    """回复效果记录状态。"""

    PENDING = "pending"
    EVALUATING = "evaluating"
    FINALIZED = "finalized"
    INCOMPLETE = "incomplete"
    EVALUATION_FAILED = "evaluation_failed"


STANCE_TARGETS = {"topic_or_third_party", "bot_content", "bot_persona"}
STANCES = {
    "appreciation",
    "playful",
    "neutral",
    "confusion",
    "factual_correction",
    "rejection",
    "bot_attack",
}
CONTRIBUTIONS = {"advance", "maintain", "acknowledge", "close", "wrong_push"}
STRATEGIES = {"answer", "opinion", "empathy", "humor", "question", "topic_start", "acknowledgement", "other"}


@dataclass(slots=True)
class SessionSnapshot:
    session_id: str
    platform_type_id: str
    platform: str
    chat_type: str
    group_id: str
    user_id: str
    session_name: str


@dataclass(slots=True)
class UserSnapshot:
    user_id: str
    nickname: str
    cardname: str


@dataclass(slots=True)
class ReplySnapshot:
    tool_call_id: str
    target_message_id: str
    set_quote: bool
    reply_text: str
    reply_segments: List[str]
    planner_reasoning: str
    sent_message_ids: List[str] = field(default_factory=list)
    model_name: str = ""
    request_fingerprint: str = ""
    prompt_fingerprint: str = ""
    strategy_primary: str = "other"
    strategy_secondary: List[str] = field(default_factory=list)
    strategy_confidence: float = 0.0
    tool_context: Dict[str, Any] = field(default_factory=dict)
    send_results: List[Dict[str, Any]] = field(default_factory=list)
    reply_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReplyAssociation:
    effect_id: str
    attribution_type: str
    attribution_confidence: float
    stance_target: str
    stance: str
    contribution: str
    reason: str = ""
    evidence_spans: List[str] = field(default_factory=list)
    evaluator_confidence: float = 0.0


@dataclass(slots=True)
class FollowupMessageSnapshot:
    message_id: str
    timestamp: str
    user_id: str
    nickname: str
    cardname: str
    visible_text: str
    plain_text: str
    latency_seconds: float
    is_target_user: bool
    reply_to: str = ""
    quote_target_ids: List[str] = field(default_factory=list)
    candidate_effect_ids: List[str] = field(default_factory=list)
    associations: List[ReplyAssociation] = field(default_factory=list)
    attachments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ReplyEffectScores:
    response_score: float
    reception_categories: List[str]
    reception_counts: Dict[str, int]
    conversation_score: float
    confidence: Optional[float]
    response_evidence_confidence: float
    reception_evidence_confidence: float
    conversation_evidence_confidence: float


@dataclass(slots=True)
class ReplyEffectRecord:
    effect_id: str
    status: ReplyEffectStatus
    created_at: str
    updated_at: str
    session: SessionSnapshot
    reply: ReplySnapshot
    target_user: UserSnapshot
    pre_activity_count: int = 0
    pre_activity_bucket: str = "low"
    context_snapshot: List[Dict[str, Any]] = field(default_factory=list)
    followup_messages: List[FollowupMessageSnapshot] = field(default_factory=list)
    scores: Optional[ReplyEffectScores] = None
    finalized_at: str = ""
    finalize_reason: str = ""
    confidence_note: str = ""
    followup_summary: Dict[str, Any] = field(default_factory=dict)
    evaluation_error: str = ""
    evaluation_version: int = EVALUATION_VERSION
    file_path: Optional[Path] = field(default=None, repr=False)

    def to_json_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        payload["status"] = self.status.value
        payload.pop("file_path", None)
        return payload


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def reply_effect_record_from_dict(payload: Dict[str, Any]) -> ReplyEffectRecord:
    """从当前 JSON 载荷恢复回复效果记录。"""

    reply_payload = dict(payload.get("reply") or {})
    session_payload = dict(payload.get("session") or {})
    target_payload = dict(payload.get("target_user") or {})
    followups: List[FollowupMessageSnapshot] = []
    for raw_followup in payload.get("followup_messages") or []:
        followup_payload = dict(raw_followup)
        associations = [ReplyAssociation(**dict(item)) for item in followup_payload.pop("associations", [])]
        followups.append(FollowupMessageSnapshot(**followup_payload, associations=associations))
    scores_payload = payload.get("scores")
    return ReplyEffectRecord(
        effect_id=str(payload["effect_id"]),
        status=ReplyEffectStatus(str(payload["status"])),
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
        session=SessionSnapshot(**session_payload),
        reply=ReplySnapshot(**reply_payload),
        target_user=UserSnapshot(**target_payload),
        pre_activity_count=int(payload.get("pre_activity_count", 0)),
        pre_activity_bucket=str(payload.get("pre_activity_bucket") or "low"),
        context_snapshot=list(payload.get("context_snapshot") or []),
        followup_messages=followups,
        scores=ReplyEffectScores(**scores_payload) if isinstance(scores_payload, dict) else None,
        finalized_at=str(payload.get("finalized_at") or ""),
        finalize_reason=str(payload.get("finalize_reason") or ""),
        confidence_note=str(payload.get("confidence_note") or ""),
        followup_summary=dict(payload.get("followup_summary") or {}),
        evaluation_error=str(payload.get("evaluation_error") or ""),
        evaluation_version=int(payload["evaluation_version"]),
    )
