"""关系记忆生命周期的纯函数策略。

生命周期状态以关系元数据为事实源。图邻接只保存结构，不参与时间衰减。
所有时间计算均基于调用方传入的墙钟时间，因此重复计算和停机恢复具有确定性。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite, log2
from typing import Optional


class RelationLifecycleEvent(str, Enum):
    """会改变关系保留状态的领域事件。"""

    ACCESS = "access"
    EVIDENCE = "evidence"
    REINFORCE = "reinforce"
    WEAKEN = "weaken"
    FREEZE = "freeze"
    FORGET = "forget"


@dataclass(frozen=True)
class RelationLifecyclePolicy:
    """关系生命周期参数。"""

    half_life_hours: float = 24.0
    freeze_threshold: float = 0.1
    revive_threshold: float = 0.15
    access_alpha: float = 0.05
    access_cooldown_seconds: float = 3600.0
    reinforce_alpha: float = 0.5
    weaken_alpha: float = 0.5

    def __post_init__(self) -> None:
        if not isfinite(self.half_life_hours) or self.half_life_hours <= 0.0:
            raise ValueError("half_life_hours 必须是大于0的有限数")
        for name in (
            "freeze_threshold",
            "revive_threshold",
            "access_alpha",
            "reinforce_alpha",
            "weaken_alpha",
        ):
            value = float(getattr(self, name))
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 必须位于[0, 1]")
        if not isfinite(self.access_cooldown_seconds) or self.access_cooldown_seconds < 0.0:
            raise ValueError("access_cooldown_seconds 必须是大于等于0的有限数")
        if not 0.0 < self.freeze_threshold < 1.0:
            raise ValueError("freeze_threshold 必须位于(0, 1)")
        if self.revive_threshold <= self.freeze_threshold:
            raise ValueError("revive_threshold 必须大于 freeze_threshold")


@dataclass(frozen=True)
class RelationLifecycleState:
    """计算生命周期所需的最小关系状态。"""

    strength: float
    anchor_at: float
    is_inactive: bool = False
    inactive_since: Optional[float] = None
    inactive_reason: Optional[str] = None
    last_access_reinforced_at: Optional[float] = None


@dataclass(frozen=True)
class RelationLifecycleDecision:
    """一次维护或事件计算的确定性结果。"""

    strength: float
    anchor_at: float
    next_lifecycle_at: Optional[float]
    is_inactive: bool
    inactive_since: Optional[float]
    inactive_reason: Optional[str]
    last_access_reinforced_at: Optional[float]
    changed: bool


def clamp_unit(value: float) -> float:
    """把有限数限制到[0, 1]。"""

    number = float(value)
    if not isfinite(number):
        raise ValueError("生命周期强度必须是有限数")
    return min(1.0, max(0.0, number))


def retention_at(
    state: RelationLifecycleState,
    *,
    now: float,
    policy: RelationLifecyclePolicy,
) -> float:
    """惰性计算指定墙钟时刻的关系保留强度。"""

    timestamp = float(now)
    if not isfinite(timestamp):
        raise ValueError("now 必须是有限时间戳")
    anchor_at = float(state.anchor_at)
    if not isfinite(anchor_at):
        raise ValueError("anchor_at 必须是有限时间戳")
    elapsed_hours = max(0.0, timestamp - anchor_at) / 3600.0
    return clamp_unit(state.strength) * 2.0 ** (-elapsed_hours / policy.half_life_hours)


def threshold_crossing_at(
    *,
    strength: float,
    anchor_at: float,
    policy: RelationLifecyclePolicy,
) -> float:
    """计算当前锚点首次到达冻结阈值的墙钟时间。"""

    normalized = clamp_unit(strength)
    anchor = float(anchor_at)
    if not isfinite(anchor):
        raise ValueError("anchor_at 必须是有限时间戳")
    if normalized <= policy.freeze_threshold:
        return anchor
    elapsed_hours = policy.half_life_hours * log2(normalized / policy.freeze_threshold)
    return anchor + elapsed_hours * 3600.0


def evaluate_lifecycle(
    state: RelationLifecycleState,
    *,
    now: float,
    policy: RelationLifecyclePolicy,
) -> RelationLifecycleDecision:
    """在维护时刻计算关系是否应冻结或安排下一次到期检查。"""

    requested_timestamp = float(now)
    anchor_at = float(state.anchor_at)
    if not isfinite(requested_timestamp):
        raise ValueError("now 必须是有限时间戳")
    if not isfinite(anchor_at):
        raise ValueError("anchor_at 必须是有限时间戳")
    last_access_reinforced_at = state.last_access_reinforced_at
    if last_access_reinforced_at is not None and not isfinite(float(last_access_reinforced_at)):
        raise ValueError("last_access_reinforced_at 必须是有限时间戳")
    inactive_since = state.inactive_since
    if inactive_since is not None and not isfinite(float(inactive_since)):
        raise ValueError("inactive_since 必须是有限时间戳")
    timestamp = max(
        requested_timestamp,
        anchor_at,
        float(last_access_reinforced_at) if last_access_reinforced_at is not None else anchor_at,
        float(inactive_since) if inactive_since is not None else anchor_at,
    )
    effective = retention_at(state, now=timestamp, policy=policy)
    if state.is_inactive:
        return RelationLifecycleDecision(
            strength=clamp_unit(state.strength),
            anchor_at=float(state.anchor_at),
            next_lifecycle_at=None,
            is_inactive=True,
            inactive_since=state.inactive_since,
            inactive_reason=state.inactive_reason,
            last_access_reinforced_at=state.last_access_reinforced_at,
            changed=False,
        )

    due_at = threshold_crossing_at(
        strength=state.strength,
        anchor_at=state.anchor_at,
        policy=policy,
    )
    if effective <= policy.freeze_threshold or timestamp >= due_at:
        return RelationLifecycleDecision(
            strength=clamp_unit(state.strength),
            anchor_at=float(state.anchor_at),
            next_lifecycle_at=None,
            is_inactive=True,
            inactive_since=timestamp,
            inactive_reason="decay",
            last_access_reinforced_at=state.last_access_reinforced_at,
            changed=True,
        )

    return RelationLifecycleDecision(
        strength=clamp_unit(state.strength),
        anchor_at=float(state.anchor_at),
        next_lifecycle_at=due_at,
        is_inactive=False,
        inactive_since=None,
        inactive_reason=None,
        last_access_reinforced_at=state.last_access_reinforced_at,
        changed=True,
    )


def apply_lifecycle_event(
    state: RelationLifecycleState,
    event: RelationLifecycleEvent,
    *,
    now: float,
    policy: RelationLifecyclePolicy,
    strength: float = 1.0,
) -> RelationLifecycleDecision:
    """把访问、强化、弱化或遗忘事件应用到关系状态。"""

    requested_timestamp = float(now)
    if not isfinite(requested_timestamp):
        raise ValueError("now 必须是有限时间戳")
    anchor_at = float(state.anchor_at)
    if not isfinite(anchor_at):
        raise ValueError("anchor_at 必须是有限时间戳")
    last_access_reinforced_at = state.last_access_reinforced_at
    if last_access_reinforced_at is not None and not isfinite(float(last_access_reinforced_at)):
        raise ValueError("last_access_reinforced_at 必须是有限时间戳")
    inactive_since = state.inactive_since
    if inactive_since is not None and not isfinite(float(inactive_since)):
        raise ValueError("inactive_since 必须是有限时间戳")
    # 墙钟允许被校时回拨，但持久化生命周期时间不能倒退。使用关系自身
    # 已提交时间形成逻辑墙钟下界，避免回拨期间的新事件制造虚假老化。
    timestamp = max(
        requested_timestamp,
        anchor_at,
        float(last_access_reinforced_at) if last_access_reinforced_at is not None else anchor_at,
        float(inactive_since) if inactive_since is not None else anchor_at,
    )
    current = retention_at(state, now=timestamp, policy=policy)
    event_strength = float(strength)
    if not isfinite(event_strength) or event_strength < 0.0:
        raise ValueError("事件强度必须是大于等于0的有限数")

    if event is RelationLifecycleEvent.ACCESS:
        cooldown_elapsed = (
            last_access_reinforced_at is None
            or timestamp - float(last_access_reinforced_at) >= policy.access_cooldown_seconds
        )
        alpha = min(1.0, policy.access_alpha * event_strength)
        updated = current + alpha * (1.0 - current)
        revives_without_score_change = (
            state.is_inactive
            and cooldown_elapsed
            and alpha > 0.0
            and updated >= policy.revive_threshold
        )
        if not cooldown_elapsed or (
            (alpha == 0.0 or updated == current) and not revives_without_score_change
        ):
            next_at = None
            if not state.is_inactive:
                next_at = threshold_crossing_at(
                    strength=state.strength,
                    anchor_at=state.anchor_at,
                    policy=policy,
                )
                if current <= policy.freeze_threshold or timestamp >= next_at:
                    return RelationLifecycleDecision(
                        strength=clamp_unit(state.strength),
                        anchor_at=float(state.anchor_at),
                        next_lifecycle_at=None,
                        is_inactive=True,
                        inactive_since=timestamp,
                        inactive_reason="decay",
                        last_access_reinforced_at=last_access_reinforced_at,
                        changed=True,
                    )
            return RelationLifecycleDecision(
                strength=clamp_unit(state.strength),
                anchor_at=float(state.anchor_at),
                next_lifecycle_at=next_at,
                is_inactive=state.is_inactive,
                inactive_since=state.inactive_since,
                inactive_reason=state.inactive_reason,
                last_access_reinforced_at=last_access_reinforced_at,
                changed=False,
            )
        reason = None
    elif event is RelationLifecycleEvent.REINFORCE:
        alpha = min(1.0, policy.reinforce_alpha * event_strength)
        updated = current + alpha * (1.0 - current)
        reason = None
    elif event is RelationLifecycleEvent.EVIDENCE:
        updated = 1.0
        reason = None
    elif event is RelationLifecycleEvent.WEAKEN:
        alpha = min(1.0, policy.weaken_alpha * event_strength)
        updated = current * (1.0 - alpha)
        reason = "weaken" if updated <= policy.freeze_threshold else None
    elif event is RelationLifecycleEvent.FREEZE:
        updated = current
        reason = "manual_freeze"
    elif event is RelationLifecycleEvent.FORGET:
        updated = 0.0
        reason = "manual_forget"
    else:
        raise ValueError(f"不支持的生命周期事件: {event}")

    updated = clamp_unit(updated)
    force_inactive = event in {RelationLifecycleEvent.FREEZE, RelationLifecycleEvent.FORGET}
    should_freeze = force_inactive or updated <= policy.freeze_threshold
    should_revive = updated >= policy.revive_threshold and not force_inactive
    is_inactive = state.is_inactive
    inactive_since = state.inactive_since
    inactive_reason = state.inactive_reason

    if should_freeze:
        is_inactive = True
        inactive_since = state.inactive_since if state.is_inactive else timestamp
        inactive_reason = reason or "decay"
    elif not state.is_inactive or should_revive:
        is_inactive = False
        inactive_since = None
        inactive_reason = None

    next_at = None
    if not is_inactive:
        next_at = threshold_crossing_at(
            strength=updated,
            anchor_at=timestamp,
            policy=policy,
        )

    return RelationLifecycleDecision(
        strength=updated,
        anchor_at=timestamp,
        next_lifecycle_at=next_at,
        is_inactive=is_inactive,
        inactive_since=inactive_since,
        inactive_reason=inactive_reason,
        last_access_reinforced_at=(
            timestamp
            if event is RelationLifecycleEvent.ACCESS
            else state.last_access_reinforced_at
        ),
        changed=True,
    )
