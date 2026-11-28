"""Maisaka 消息触发门控的判定结果。"""

from dataclasses import dataclass
from typing import Literal, Union

TurnGateDecision = Literal["trigger", "wait", "delay"]


@dataclass(frozen=True)
class WaitTimeoutTrigger:
    tool_call_id: str


TurnTrigger = Union[Literal["message", "proactive", "desktop_pet_screen"], WaitTimeoutTrigger]


@dataclass(frozen=True)
class TurnGateResult:
    """单个触发门控的判定结果。"""

    decision: TurnGateDecision
    detail: str
    delay_seconds: float = 0.0

    @property
    def should_trigger(self) -> bool:
        return self.decision == "trigger"
