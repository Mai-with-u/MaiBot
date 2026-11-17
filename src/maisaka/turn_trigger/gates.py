"""Maisaka 消息触发门控的判定结果。"""

from dataclasses import dataclass
from typing import Literal

TurnGateDecision = Literal["trigger", "wait", "delay"]


@dataclass(frozen=True)
class TurnGateResult:
    """单个触发门控的判定结果。"""

    decision: TurnGateDecision
    detail: str
    delay_seconds: float = 0.0

    @property
    def should_trigger(self) -> bool:
        return self.decision == "trigger"
