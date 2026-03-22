"""
时间解析工具。

约束：
1. 查询参数（Action/Command/Tool）仅接受结构化绝对时间：
   - YYYY/MM/DD
   - YYYY/MM/DD HH:mm
2. 入库时允许更宽松格式（含时间戳、YYYY-MM-DD 等）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


_QUERY_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_QUERY_MINUTE_RE = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")
_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

_INGEST_FORMATS = [
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y/%m/%d",
    "%Y-%m-%d",
]

_INGEST_DATE_FORMATS = {"%Y/%m/%d", "%Y-%m-%d"}


def parse_query_datetime_to_timestamp(value: str, is_end: bool = False) -> float:
    """解析查询时间，仅支持 YYYY/MM/DD 或 YYYY/MM/DD HH:mm。"""
    text = str(value).strip()
    if not text:
        raise ValueError("时间不能为空")

    if _QUERY_DATE_RE.fullmatch(text):
        dt = datetime.strptime(text, "%Y/%m/%d")
        if is_end:
            dt = dt.replace(hour=23, minute=59, second=0, microsecond=0)
        return dt.timestamp()

    if _QUERY_MINUTE_RE.fullmatch(text):
        dt = datetime.strptime(text, "%Y/%m/%d %H:%M")
        return dt.timestamp()

    raise ValueError(
        f"时间格式错误: {text}。仅支持 YYYY/MM/DD 或 YYYY/MM/DD HH:mm"
    )


def parse_query_time_range(
    time_from: Optional[str],
    time_to: Optional[str],
) -> Tuple[Optional[float], Optional[float]]:
    """解析查询窗口并验证区间。"""
    ts_from = (
        parse_query_datetime_to_timestamp(time_from, is_end=False)
        if time_from
        else None
    )
    ts_to = (
        parse_query_datetime_to_timestamp(time_to, is_end=True)
        if time_to
        else None
    )

    if ts_from is not None and ts_to is not None and ts_from > ts_to:
        raise ValueError("time_from 不能晚于 time_to")

    return ts_from, ts_to


def parse_ingest_datetime_to_timestamp(
    value: Any,
    is_end: bool = False,
) -> Optional[float]:
    """解析入库时间，允许 timestamp/常见字符串格式。"""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    if _NUMERIC_RE.fullmatch(text):
        return float(text)

    for fmt in _INGEST_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            if fmt in _INGEST_DATE_FORMATS and is_end:
                dt = dt.replace(hour=23, minute=59, second=0, microsecond=0)
            return dt.timestamp()
        except ValueError:
            continue

    raise ValueError(f"无法解析时间: {text}")


def normalize_time_meta(time_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """归一化 time_meta 到存储层字段。"""
    if not time_meta:
        return {}

    normalized: Dict[str, Any] = {}

    event_time = parse_ingest_datetime_to_timestamp(time_meta.get("event_time"))
    event_start = parse_ingest_datetime_to_timestamp(
        time_meta.get("event_time_start"),
        is_end=False,
    )
    event_end = parse_ingest_datetime_to_timestamp(
        time_meta.get("event_time_end"),
        is_end=True,
    )

    time_range = time_meta.get("time_range")
    if (
        isinstance(time_range, (list, tuple))
        and len(time_range) == 2
    ):
        if event_start is None:
            event_start = parse_ingest_datetime_to_timestamp(time_range[0], is_end=False)
        if event_end is None:
            event_end = parse_ingest_datetime_to_timestamp(time_range[1], is_end=True)

    if event_start is not None and event_end is not None and event_start > event_end:
        raise ValueError("event_time_start 不能晚于 event_time_end")

    if event_time is not None:
        normalized["event_time"] = event_time
    if event_start is not None:
        normalized["event_time_start"] = event_start
    if event_end is not None:
        normalized["event_time_end"] = event_end

    granularity = time_meta.get("time_granularity")
    if granularity:
        normalized["time_granularity"] = str(granularity)
    else:
        raw_time_values = [
            time_meta.get("event_time"),
            time_meta.get("event_time_start"),
            time_meta.get("event_time_end"),
        ]
        has_minute = any(isinstance(v, str) and ":" in v for v in raw_time_values if v is not None)
        normalized["time_granularity"] = "minute" if has_minute else "day"

    confidence = time_meta.get("time_confidence")
    if confidence is not None:
        normalized["time_confidence"] = float(confidence)

    return normalized


def format_timestamp(ts: Optional[float]) -> Optional[str]:
    """将 timestamp 格式化为 YYYY/MM/DD HH:mm。"""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).strftime("%Y/%m/%d %H:%M")

