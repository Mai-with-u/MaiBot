"""根据群聊消息的实际接收时间估算静默查看间隔。"""

from typing import Optional, Sequence


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
