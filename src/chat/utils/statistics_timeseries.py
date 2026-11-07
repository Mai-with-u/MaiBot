"""一次遍历生成统计图表和指标趋势，不持有历史明细。"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Dict, Iterable, List, Mapping, Optional, Tuple, cast

import math

from src.services.statistics_usage_rules import bucket_start as hour_bucket_start
from src.services.statistics_usage_rules import normalize_usage_record

if TYPE_CHECKING:
    from src.services.statistics_service import StatisticsMessageRow

_HOUR = timedelta(hours=1)

# 用量条目的来源：
# - HOURLY：小时汇总的一行（时间戳为整点），只交给「这一小时干净」的时间桶
# - RAW：已经进入汇总的明细，只交给「这一小时不干净」的时间桶，避免与汇总重复
# - UNAGGREGATED：汇总刷新之后才写入的明细（或汇总未就绪时的全部明细），交给所有时间桶
SOURCE_HOURLY = "hourly"
SOURCE_RAW = "raw"
SOURCE_UNAGGREGATED = "unaggregated"


@dataclass(frozen=True)
class UsageEntry:
    """一条调用明细，或同一小时内同一模型和模块的若干调用。"""

    timestamp: datetime
    cost: float
    tokens: int
    requests: int
    model: str
    module: str
    source: str = SOURCE_UNAGGREGATED


def usage_entry_from_record(record: Mapping[str, object], source: str = SOURCE_UNAGGREGATED) -> UsageEntry:
    key, metrics = normalize_usage_record(record)
    request_type = key.request_type
    return UsageEntry(
        timestamp=cast(datetime, record["timestamp"]),
        cost=metrics[5],
        tokens=metrics[1] + metrics[2],
        requests=1,
        model=key.model_name,
        module=request_type.split(".")[0] if "." in request_type else request_type,
        source=source,
    )


def _grid_has_boundary_inside_hour(hour: datetime, grid_start: datetime, step: timedelta) -> bool:
    """以 grid_start 起、step 为间隔的分界点是否落在 (hour, hour + 1h) 之内。"""
    offset = (hour - grid_start) % step
    return offset != timedelta(0) and step - offset < _HOUR


@dataclass
class _Window:
    start: datetime
    hours: int
    interval_minutes: int
    size: int = field(init=False)
    seconds: int = field(init=False)
    start_timestamp: float = field(init=False)

    def __post_init__(self) -> None:
        # 保留末尾时间桶，包括恰好落在统计结束时间上的记录。
        self.size = self.hours * 60 // self.interval_minutes + 1
        self.seconds = self.interval_minutes * 60
        self.start_timestamp = self.start.timestamp()

    def index(self, elapsed_seconds: float) -> int:
        return int(elapsed_seconds // self.seconds)

    def labels(self, format_string: str) -> List[str]:
        return [
            (self.start + timedelta(minutes=i * self.interval_minutes)).strftime(format_string)
            for i in range(self.size)
        ]

    def clean_hour(self, hour: datetime, aggregate_limit: Optional[datetime]) -> bool:
        """这一小时的记录是否全部落进同一个桶（或全部在范围外），可以直接用小时汇总。"""
        if aggregate_limit is None or hour >= aggregate_limit:
            return False
        if hour + _HOUR <= self.start or hour >= self.start + timedelta(seconds=self.seconds * self.size):
            return True  # 整小时都在这个窗口之外，取汇总还是明细都不会计入
        if self.interval_minutes % 60:
            return False
        return not _grid_has_boundary_inside_hour(hour, self.start, timedelta(seconds=self.seconds))


@dataclass
class _Chart:
    window: _Window
    costs: List[float] = field(init=False)
    by_model: Dict[str, List[float]] = field(default_factory=dict)
    by_module: Dict[str, List[float]] = field(default_factory=dict)
    by_chat: Dict[str, List[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.costs = [0.0] * self.window.size

    def result(self) -> Dict[str, object]:
        return {
            "time_labels": self.window.labels("%H:%M"),
            "total_cost_data": self.costs,
            "cost_by_model": self.by_model,
            "cost_by_module": self.by_module,
            "message_by_chat": self.by_chat,
        }


@dataclass
class _Metrics:
    window: _Window
    costs: List[float] = field(init=False)
    tokens: List[int] = field(init=False)
    messages: List[int] = field(init=False)
    replies: List[int] = field(init=False)
    online_hours: List[float] = field(init=False)

    def __post_init__(self) -> None:
        size = self.window.size
        self.costs = [0.0] * size
        self.tokens = [0] * size
        self.messages = [0] * size
        self.replies = [0] * size
        self.online_hours = [0.0] * size

    def result(self) -> Dict[str, object]:
        return {
            "time_labels": self.window.labels("%H:%M" if self.window.interval_minutes == 60 else "%m-%d"),
            "cost_per_100_messages": [
                cost / count * 100 if count > 0 else 0.0
                for cost, count in zip(self.costs, self.messages, strict=True)
            ],
            "cost_per_hour": [
                cost / hours if hours > 0 else 0.0
                for cost, hours in zip(self.costs, self.online_hours, strict=True)
            ],
            "tokens_per_hour": [
                tokens / hours if hours > 0 else 0.0
                for tokens, hours in zip(self.tokens, self.online_hours, strict=True)
            ],
            "cost_per_100_replies": [
                cost / count * 100 if count > 0 else 0.0
                for cost, count in zip(self.costs, self.replies, strict=True)
            ],
        }


@dataclass
class PeriodTrendWindow:
    """与详细统计时段对齐的时间桶；末桶包含恰好落在结束时刻的记录。"""

    start: datetime
    end: datetime
    bucket_minutes: int
    truncated: bool = False
    size: int = field(init=False)
    seconds: int = field(init=False)

    def __post_init__(self) -> None:
        self.seconds = self.bucket_minutes * 60
        self.size = max(1, math.ceil((self.end - self.start).total_seconds() / self.seconds))

    def index(self, timestamp: datetime) -> Optional[int]:
        if timestamp < self.start or timestamp > self.end:
            return None
        return min(int((timestamp - self.start).total_seconds() // self.seconds), self.size - 1)

    def clean_hour(self, hour: datetime, aggregate_limit: Optional[datetime]) -> bool:
        """同 _Window.clean_hour；结束时刻是闭区间，所在小时只能读明细。"""
        if aggregate_limit is None or hour >= aggregate_limit:
            return False
        if hour + _HOUR <= self.start or hour > self.end:
            return True  # 整小时都在这个时段之外
        if self.bucket_minutes % 60 or hour <= self.end < hour + _HOUR:
            return False
        return not _grid_has_boundary_inside_hour(hour, self.start, timedelta(seconds=self.seconds))

    def labels(self) -> List[str]:
        if self.bucket_minutes < 60:
            format_string = "%H:%M"
        elif self.bucket_minutes < 1440:
            format_string = "%m-%d %H:%M"
        else:
            format_string = "%m-%d"
        step = timedelta(minutes=self.bucket_minutes)
        return [(self.start + step * i).strftime(format_string) for i in range(self.size)]


@dataclass
class _PeriodSeries:
    window: PeriodTrendWindow
    costs: List[float] = field(init=False)
    tokens: List[int] = field(init=False)
    requests: List[int] = field(init=False)
    messages: List[int] = field(init=False)
    attempts: List[int] = field(init=False)
    failures: List[int] = field(init=False)
    cost_by_model: Dict[str, List[float]] = field(default_factory=dict)
    tokens_by_model: Dict[str, List[int]] = field(default_factory=dict)
    requests_by_model: Dict[str, List[int]] = field(default_factory=dict)
    cost_by_module: Dict[str, List[float]] = field(default_factory=dict)
    message_by_chat: Dict[str, List[int]] = field(default_factory=dict)
    heatmap_messages: List[List[int]] = field(default_factory=lambda: [[0] * 24 for _ in range(7)])
    heatmap_costs: List[List[float]] = field(default_factory=lambda: [[0.0] * 24 for _ in range(7)])

    def __post_init__(self) -> None:
        size = self.window.size
        self.costs = [0.0] * size
        self.tokens = [0] * size
        self.requests = [0] * size
        self.messages = [0] * size
        self.attempts = [0] * size
        self.failures = [0] * size

    def result(self) -> Dict[str, object]:
        return {
            "start_time": self.window.start.isoformat(),
            "bucket_minutes": self.window.bucket_minutes,
            "truncated": self.window.truncated,
            "time_labels": self.window.labels(),
            "costs": self.costs,
            "tokens": self.tokens,
            "requests": self.requests,
            "messages": self.messages,
            "attempts": self.attempts,
            "failures": self.failures,
            "cost_by_model": self.cost_by_model,
            "tokens_by_model": self.tokens_by_model,
            "requests_by_model": self.requests_by_model,
            "cost_by_module": self.cost_by_module,
            "message_by_chat": self.message_by_chat,
            "heatmap_messages": self.heatmap_messages,
            "heatmap_costs": self.heatmap_costs,
        }


def _series_slot(breakdown: Dict[str, List], name: str, size: int, zero: float | int) -> List:
    if name not in breakdown:
        breakdown[name] = [zero] * size
    return breakdown[name]


class PeriodTrendCollector:
    """在图表的同一次明细遍历中，为每个详细统计时段累计趋势与热力图。"""

    def __init__(self, windows: Mapping[str, PeriodTrendWindow]):
        self._series = {key: _PeriodSeries(window) for key, window in windows.items()}

    def windows(self) -> List[PeriodTrendWindow]:
        return [series.window for series in self._series.values()]

    def add_usage(
        self,
        timestamp: datetime,
        cost: float,
        tokens: int,
        model: str,
        module: str,
        requests: int = 1,
        accept: Optional[Callable[[PeriodTrendWindow], bool]] = None,
    ) -> None:
        for series in self._series.values():
            if accept is not None and not accept(series.window):
                continue
            index = series.window.index(timestamp)
            if index is None:
                continue
            size = series.window.size
            series.costs[index] += cost
            series.tokens[index] += tokens
            series.requests[index] += requests
            _series_slot(series.cost_by_model, model, size, 0.0)[index] += cost
            _series_slot(series.tokens_by_model, model, size, 0)[index] += tokens
            _series_slot(series.requests_by_model, model, size, 0)[index] += requests
            _series_slot(series.cost_by_module, module, size, 0.0)[index] += cost
            series.heatmap_costs[timestamp.weekday()][timestamp.hour] += cost

    def add_message(self, timestamp: datetime, chat_name: Optional[str]) -> None:
        # 与时段汇总的消息口径一致：没有群号也没有用户号的消息不计入。
        if not chat_name:
            return
        for series in self._series.values():
            index = series.window.index(timestamp)
            if index is None:
                continue
            series.messages[index] += 1
            _series_slot(series.message_by_chat, chat_name, series.window.size, 0)[index] += 1
            series.heatmap_messages[timestamp.weekday()][timestamp.hour] += 1

    def add_task_usage(self, bucket_time: datetime, attempts: int, failures: int) -> None:
        for series in self._series.values():
            index = series.window.index(bucket_time)
            if index is None:
                continue
            series.attempts[index] += attempts
            series.failures[index] += failures

    def result(self) -> Dict[str, Dict[str, object]]:
        return {key: series.result() for key, series in self._series.items()}


def build_series_windows(now: datetime, chart_now: datetime) -> Tuple[Dict[str, _Window], Dict[str, _Window]]:
    """图表与指标趋势的时间桶；用量来源的划分和实际累计必须用同一组窗口。"""
    # 图表：6/12/24/48 小时，分别按 10/15/15/30 分钟分桶。
    chart_windows = {
        key: _Window(chart_now - timedelta(hours=hours), hours, minutes)
        for key, hours, minutes in (("6h", 6, 10), ("12h", 12, 15), ("24h", 24, 15), ("48h", 48, 30))
    }
    # 趋势：24 小时按小时，7 天及 30 天按天分桶。
    metric_windows = {
        key: _Window(now - timedelta(hours=hours), hours, minutes)
        for key, hours, minutes in (("24h", 24, 60), ("7d", 168, 1440), ("30d", 720, 1440))
    }
    return chart_windows, metric_windows


def hours_needing_detail(
    windows: Iterable["_Window | PeriodTrendWindow"],
    first_hour: datetime,
    aggregate_limit: datetime,
) -> List[Tuple[datetime, Optional[datetime]]]:
    """[first_hour, aggregate_limit) 里至少有一个时间桶不能直接用汇总的小时，合并成连续区间；当前小时起一律读明细。"""
    window_list = list(windows)
    ranges: List[Tuple[datetime, Optional[datetime]]] = []
    hour = first_hour
    while hour < aggregate_limit:
        if any(not window.clean_hour(hour, aggregate_limit) for window in window_list):
            if ranges and ranges[-1][1] == hour:
                ranges[-1] = (ranges[-1][0], hour + _HOUR)
            else:
                ranges.append((hour, hour + _HOUR))
        hour += _HOUR
    if ranges and ranges[-1][1] == aggregate_limit:
        ranges[-1] = (ranges[-1][0], None)
    else:
        ranges.append((aggregate_limit, None))
    return ranges


def collect_time_series(
    now: datetime,
    chart_now: datetime,
    usage: Iterable["Mapping[str, object] | UsageEntry"],
    messages: Iterable["StatisticsMessageRow"],
    online_intervals: Iterable[Tuple[datetime, datetime]],
    is_bot_self: Callable[[str, str], bool],
    period_trends: Optional[PeriodTrendCollector] = None,
    aggregate_limit: Optional[datetime] = None,
) -> Tuple[Dict[str, Dict[str, object]], Dict[str, Dict[str, object]]]:
    """共享数据读取，保留图表和趋势各自的时间起点、分桶方式及回复口径。

    usage 可以是明细记录，也可以是 UsageEntry（含小时汇总行）；aggregate_limit 为小时汇总的截止整点，
    每个时间桶按 clean_hour 决定某一小时取汇总还是明细，两者不会重复计入。
    """

    chart_windows, metric_windows = build_series_windows(now, chart_now)
    charts = {key: _Chart(window) for key, window in chart_windows.items()}
    metrics = {key: _Metrics(window) for key, window in metric_windows.items()}

    for item in usage:
        entry = item if isinstance(item, UsageEntry) else usage_entry_from_record(item)
        timestamp = entry.timestamp
        hour = hour_bucket_start(timestamp)

        def accept(window: "_Window | PeriodTrendWindow", entry: UsageEntry = entry, hour: datetime = hour) -> bool:
            if entry.source == SOURCE_UNAGGREGATED:
                return True
            clean = window.clean_hour(hour, aggregate_limit)
            return clean if entry.source == SOURCE_HOURLY else not clean

        cost, tokens, model, module = entry.cost, entry.tokens, entry.model, entry.module
        for chart in charts.values():
            if not accept(chart.window):
                continue
            index = chart.window.index((timestamp - chart.window.start).total_seconds())
            if 0 <= index < chart.window.size:
                chart.costs[index] += cost
                for breakdown, name in ((chart.by_model, model), (chart.by_module, module)):
                    if name not in breakdown:
                        breakdown[name] = [0.0] * chart.window.size
                    breakdown[name][index] += cost
        for series in metrics.values():
            if not accept(series.window):
                continue
            index = series.window.index((timestamp - series.window.start).total_seconds())
            if 0 <= index < series.window.size:
                series.costs[index] += cost
                series.tokens[index] += tokens
        if period_trends is not None:
            period_trends.add_usage(timestamp, cost, tokens, model, module, entry.requests, accept)

    for message in messages:
        # 新入站按接收时间，历史和出站按消息时间；沿用消息的本地时间戳分桶方式。
        timestamp = message.statistics_time.timestamp()
        if message.group_id:
            chat_name = message.group_name or f"群{message.group_id}"
        elif message.user_id:
            chat_name = message.user_nickname or f"用户{message.user_id}"
        else:
            chat_name = None
        if chat_name:
            for chart in charts.values():
                index = chart.window.index(timestamp - chart.window.start_timestamp)
                if 0 <= index < chart.window.size:
                    if chat_name not in chart.by_chat:
                        chart.by_chat[chat_name] = [0] * chart.window.size
                    chart.by_chat[chat_name][index] += 1
        if period_trends is not None:
            period_trends.add_message(message.statistics_time, chat_name)
        # 趋势仍按机器人发送的消息计回复数，不替换成终端统计的 reply 工具调用次数。
        reply = None
        for series in metrics.values():
            index = series.window.index(timestamp - series.window.start_timestamp)
            if 0 <= index < series.window.size:
                series.messages[index] += 1
                if reply is None:
                    reply = is_bot_self(message.platform or "", message.user_id or "")
                series.replies[index] += int(reply)

    for start, end in online_intervals:
        for series in metrics.values():
            # 在线区间按与各桶的实际重叠时长累计，保留末尾桶，不裁剪到报告时间。
            step = timedelta(minutes=series.window.interval_minutes)
            for index in range(series.window.size):
                bucket_start = series.window.start + index * step
                overlap_start = max(start, bucket_start)
                overlap_end = min(end, bucket_start + step)
                if overlap_end > overlap_start:
                    series.online_hours[index] += (overlap_end - overlap_start).total_seconds() / 3600.0

    return (
        {key: chart.result() for key, chart in charts.items()},
        {key: series.result() for key, series in metrics.items()},
    )
