"""群聊查看频率：外部概率、连续查看衰减与判定恢复。"""

from typing import Literal, Optional, Sequence

DECAY_STEP = 1.0 / 16
RECOVERY_ROUNDS = 16


class ViewFrequencyController:
    """外部值决定初始概率及连续查看预算，内部值独立变化。"""

    def __init__(self) -> None:
        self.phase: Literal["idle", "decay", "recovery"] = "idle"
        self.internal_frequency = 0.0
        self.recovery_rounds = 0

    def probability(self, external_frequency: float) -> float:
        external_frequency = min(1.0, max(0.0, external_frequency))
        if external_frequency == 0:
            return 0.0
        if self.phase == "decay":
            return 1.0
        if self.phase == "recovery":
            return external_frequency * self.recovery_rounds / RECOVERY_ROUNDS
        return external_frequency

    def decide(self, external_frequency: float, draw: float) -> bool:
        """每个机会只抽签一次；未通过也推进恢复，当前机会使用推进前的概率。"""
        external_frequency = min(1.0, max(0.0, external_frequency))
        if self.phase == "idle":
            self.internal_frequency = external_frequency
        accepted = draw < self.probability(external_frequency)
        if self.phase == "recovery":
            self.recovery_rounds += 1
            self.internal_frequency = external_frequency * self.recovery_rounds / RECOVERY_ROUNDS
            if self.recovery_rounds >= RECOVERY_ROUNDS:
                self.phase = "idle"
        return accepted

    def activate(self, external_frequency: float) -> None:
        """所有来源的 Planner 激活均计入，衰减期内不重置。"""
        if self.phase != "decay":
            self.phase = "decay"
            self.internal_frequency = min(1.0, max(0.0, external_frequency))
            self.recovery_rounds = 0

    def record_planner_run(self) -> bool:
        """完整运行一次后扣减预算，返回是否耗尽；打断重试不应调用。"""
        if self.phase != "decay":
            return False
        self.internal_frequency = max(0.0, self.internal_frequency - DECAY_STEP)
        if self.internal_frequency > 0:
            return False
        self.phase = "recovery"
        self.recovery_rounds = 0
        return True


def mean_message_interval(received_times: Sequence[float]) -> Optional[float]:
    """按真实接收时间计算上下文平均间隔，忽略同一时刻的重复值。"""
    times = sorted(set(received_times))
    if len(times) < 2:
        return None
    return (times[-1] - times[0]) / (len(times) - 1)


def predict_message_interval(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """当前与上一轮上下文均有间隔时取均值；不足两条消息时不预测。"""
    intervals = [interval for interval in (current, previous) if interval is not None]
    return sum(intervals) / len(intervals) if intervals else None
