"""回复效果观察器的数据模型。"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1


class ReplyEffectStatus(str, Enum):
    """回复效果记录状态。"""

    PENDING = "pending"
    FINALIZED = "finalized"


@dataclass(slots=True)
class SessionSnapshot:
    """会话快照。"""

    session_id: str
    platform_type_id: str
    platform: str
    chat_type: str
    group_id: str
    user_id: str
    session_name: str


@dataclass(slots=True)
class UserSnapshot:
    """用户快照。"""

    user_id: str
    nickname: str
    cardname: str


@dataclass(slots=True)
class ReplySnapshot:
    """被观察的回复内容。"""

    tool_call_id: str
    target_message_id: str
    set_quote: bool
    reply_text: str
    reply_segments: List[str]
    planner_reasoning: str
    tool_context: Dict[str, Any] = field(default_factory=dict)
    send_results: List[Dict[str, Any]] = field(default_factory=list)
    reply_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FollowupMessageSnapshot:
    """后续用户消息快照。"""

    message_id: str
    timestamp: str
    user_id: str
    nickname: str
    cardname: str
    visible_text: str
    plain_text: str
    latency_seconds: float
    is_target_user: bool
    quote_target_ids: List[str] = field(default_factory=list)
    attachments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class BehaviorSignals:
    """行为满意度信号。"""

    continue_2turns: float = 0.0
    next_user_sentiment: float = 0.5
    user_expansion: float = 0.0
    no_correction: float = 1.0
    no_abort: float = 1.0
    evidence_source: str = "no_followup"


@dataclass(slots=True)
class RubricScoreItem:
    """单个 LLM rubric 项。"""

    score: float = 3.0
    normalized_score: float = 0.5
    reason: str = ""
    evidence_spans: List[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass(slots=True)
class RubricScores:
    """LLM 感知质量评分。"""

    social_presence: RubricScoreItem = field(default_factory=RubricScoreItem)
    warmth: RubricScoreItem = field(default_factory=RubricScoreItem)
    competence: RubricScoreItem = field(default_factory=RubricScoreItem)
    appropriateness: RubricScoreItem = field(default_factory=RubricScoreItem)
    uncanny_risk: RubricScoreItem = field(default_factory=RubricScoreItem)
    available: bool = False


@dataclass(slots=True)
class FrictionSignals:
    """摩擦和反感信号。"""

    explicit_negative: float = 0.0
    repair_loop: float = 0.0
    uncanny_risk: float = 0.5
    evidence_messages: List[str] = field(default_factory=list)


@dataclass(slots=True)
class ReplyEffectScores:
    """最终效果评分。"""

    asi: float
    behavior_score: float
    relational_score: float
    friction_score: float
    behavior_signals: BehaviorSignals
    rubric_scores: RubricScores
    friction_signals: FrictionSignals
    judge_error: str = ""


@dataclass(slots=True)
class ReplyEffectRecord:
    """一条回复效果观察记录。"""

    effect_id: str
    status: ReplyEffectStatus
    created_at: str
    updated_at: str
    session: SessionSnapshot
    reply: ReplySnapshot
    target_user: UserSnapshot
    context_snapshot: List[Dict[str, Any]] = field(default_factory=list)
    followup_messages: List[FollowupMessageSnapshot] = field(default_factory=list)
    scores: Optional[ReplyEffectScores] = None
    finalized_at: str = ""
    finalize_reason: str = ""
    confidence_note: str = ""
    followup_summary: Dict[str, Any] = field(default_factory=dict)
    file_path: Optional[Path] = field(default=None, repr=False)

    def to_json_dict(self) -> Dict[str, Any]:
        """转换为可直接写入 JSON 的字典。"""

        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        payload["status"] = self.status.value
        payload.pop("file_path", None)
        return payload


def now_iso() -> str:
    """返回本地时区 ISO 时间字符串。"""

    return datetime.now().astimezone().isoformat(timespec="seconds")
