from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import col, select

import hashlib

from src.services.bot_account_service import get_all_bot_account_pairs
from src.common.database.database import get_db_session
from src.common.database.database_model import OnlineTime
from src.config.config import config_manager
from src.config.model_configs import TaskConfig

FREQUENCY_BUCKETS = (
    "0",
    "0.05",
    "0.1",
    "0.25",
    "0.5",
    "0.75",
    "1",
    "unknown",
)


def build_telemetry_stats_payload(
    *,
    client_uuid: str,
    period_start: datetime,
    period_end: datetime,
    truncated: bool,
    client_info: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构建一次遥测统计上传的聚合数据。"""

    upload_id = _build_upload_id(client_uuid, period_start, period_end)
    period_seconds = max(0, int((period_end - period_start).total_seconds()))
    return {
        "schema_version": 2,
        "upload_id": upload_id,
        "client": _build_client_info(client_info),
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "seconds": period_seconds,
            "truncated": truncated,
        },
        "runtime": _collect_runtime_stats(period_start, period_end, period_seconds),
        "messages": _collect_message_stats(period_start, period_end),
        "model_task_assignment": _collect_model_task_assignment(),
        "llm_usage": _collect_llm_usage(period_start, period_end),
    }


def _build_client_info(client_info: dict[str, str] | None) -> dict[str, str]:
    """构建允许上传的客户端基础信息。"""

    client_info = client_info or {}
    return {
        "os_type": str(client_info.get("os_type") or "Unknown"),
        "mmc_version": str(client_info.get("mmc_version") or "Unknown"),
    }


def _build_upload_id(client_uuid: str, period_start: datetime, period_end: datetime) -> str:
    raw_value = f"{client_uuid}:{period_start.isoformat()}:{period_end.isoformat()}"
    return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()


def _collect_runtime_stats(period_start: datetime, period_end: datetime, period_seconds: int) -> dict[str, Any]:
    period_start_db = _to_db_datetime(period_start)
    period_end_db = _to_db_datetime(period_end)
    intervals: list[tuple[datetime, datetime]] = []

    with get_db_session(auto_commit=False) as session:
        records = session.exec(
            select(OnlineTime).where(
                col(OnlineTime.end_timestamp) > period_start_db,
                col(OnlineTime.start_timestamp) < period_end_db,
            )
        ).all()

    for record in records:
        overlap_start = max(record.start_timestamp, period_start_db)
        overlap_end = min(record.end_timestamp, period_end_db)
        if overlap_end > overlap_start:
            intervals.append((overlap_start, overlap_end))

    online_seconds = _sum_merged_interval_seconds(intervals)
    coverage_ratio = round(online_seconds / period_seconds, 4) if period_seconds > 0 else 0.0
    return {
        "online_seconds": online_seconds,
        "coverage_ratio": min(coverage_ratio, 1.0),
        "precision": "minute_heartbeat",
    }


def _sum_merged_interval_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
    if not intervals:
        return 0

    intervals.sort(key=lambda interval: interval[0])
    merged_intervals: list[tuple[datetime, datetime]] = []
    current_start, current_end = intervals[0]
    for interval_start, interval_end in intervals[1:]:
        if interval_start <= current_end:
            current_end = max(current_end, interval_end)
            continue
        merged_intervals.append((current_start, current_end))
        current_start, current_end = interval_start, interval_end

    merged_intervals.append((current_start, current_end))
    return int(sum((interval_end - interval_start).total_seconds() for interval_start, interval_end in merged_intervals))


def _collect_message_stats(period_start: datetime, period_end: datetime) -> dict[str, Any]:
    bot_accounts = get_all_bot_account_pairs()
    stats = {
        "by_direction": {
            "received": _empty_direction_stats(),
            "bot_sent": _empty_direction_stats(),
        }
    }

    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            text(
                """
                SELECT platform, user_id, group_id, reply_frequency
                FROM mai_messages
                WHERE timestamp >= :period_start
                  AND timestamp < :period_end
                """
            ),
            params={
                "period_start": _to_db_datetime(period_start),
                "period_end": _to_db_datetime(period_end),
            },
        ).all()

    for row in rows:
        platform = str(row[0] or "unknown").strip().lower() or "unknown"
        user_id = str(row[1] or "").strip()
        group_id = str(row[2] or "").strip()
        reply_frequency = row[3]

        direction = "bot_sent" if (platform, user_id) in bot_accounts else "received"
        chat_type = "group" if group_id else "private"
        bucket = _get_frequency_bucket(reply_frequency)
        _add_message_stat(stats["by_direction"][direction], platform, chat_type, bucket)

    return stats


def _empty_direction_stats() -> dict[str, Any]:
    return {
        "total": 0,
        "by_platform": {},
    }


def _add_message_stat(direction_stats: dict[str, Any], platform: str, chat_type: str, bucket: str) -> None:
    direction_stats["total"] += 1
    platform_stats = direction_stats["by_platform"].setdefault(
        platform,
        {
            "total": 0,
            "private": 0,
            "group": 0,
            "by_reply_frequency": {
                "private": _empty_frequency_buckets(),
                "group": _empty_frequency_buckets(),
            },
        },
    )
    platform_stats["total"] += 1
    platform_stats[chat_type] += 1
    platform_stats["by_reply_frequency"][chat_type][bucket] += 1


def _empty_frequency_buckets() -> dict[str, int]:
    return {bucket: 0 for bucket in FREQUENCY_BUCKETS}


def _get_frequency_bucket(value: Any) -> str:
    if value is None:
        return "unknown"
    try:
        frequency = float(value)
    except (TypeError, ValueError):
        return "unknown"

    if frequency <= 0:
        return "0"
    if frequency <= 0.05:
        return "0.05"
    if frequency <= 0.1:
        return "0.1"
    if frequency <= 0.25:
        return "0.25"
    if frequency <= 0.5:
        return "0.5"
    if frequency <= 0.75:
        return "0.75"
    return "1"


def _collect_model_task_assignment() -> dict[str, dict[str, list[str]]]:
    model_task_config = config_manager.get_model_config().model_task_config
    result: dict[str, dict[str, list[str]]] = {}
    for task_name in type(model_task_config).model_fields:
        task_config = getattr(model_task_config, task_name, None)
        if not isinstance(task_config, TaskConfig):
            continue
        models = [str(model_name).strip() for model_name in task_config.model_list if str(model_name).strip()]
        result[task_name] = {"models": models}
    return result


def _collect_llm_usage(period_start: datetime, period_end: datetime) -> dict[str, Any]:
    task_usage: dict[str, Any] = {}

    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            text(
                """
                SELECT
                    COALESCE(NULLIF(task_name, ''), request_type, 'unknown') AS task_name,
                    COALESCE(NULLIF(model_assign_name, ''), model_name, 'unknown') AS model_name,
                    COUNT(*) AS request_count,
                    SUM(COALESCE(prompt_tokens, 0)) AS prompt_tokens,
                    SUM(COALESCE(completion_tokens, 0)) AS completion_tokens,
                    SUM(COALESCE(total_tokens, 0)) AS total_tokens
                FROM llm_usage
                WHERE timestamp >= :period_start
                  AND timestamp < :period_end
                GROUP BY task_name, model_name
                """
            ),
            params={
                "period_start": _to_db_datetime(period_start),
                "period_end": _to_db_datetime(period_end),
            },
        ).all()

    for row in rows:
        task_name = str(row[0] or "unknown")
        model_name = str(row[1] or "unknown")
        usage = {
            "request_count": int(row[2] or 0),
            "prompt_tokens": int(row[3] or 0),
            "completion_tokens": int(row[4] or 0),
            "total_tokens": int(row[5] or 0),
        }
        task_stats = task_usage.setdefault(
            task_name,
            {
                "total": defaultdict(int),
                "models": {},
            },
        )
        task_stats["models"][model_name] = usage
        for key, value in usage.items():
            task_stats["total"][key] += value

    return {
        task_name: {
            "total": dict(task_stats["total"]),
            "models": task_stats["models"],
        }
        for task_name, task_stats in task_usage.items()
    }


def _to_db_datetime(value: datetime) -> datetime:
    """SQLite 中历史时间为本地 naive datetime，查询前去掉时区信息。"""

    return value.replace(tzinfo=None)


def clamp_period_start(
    *,
    requested_start: datetime,
    period_end: datetime,
    max_lookback: timedelta,
) -> tuple[datetime, bool]:
    """限制遥测统计最大回溯窗口，返回实际起点与是否截断。"""

    min_start = period_end - max_lookback
    if requested_start < min_start:
        return min_start, True
    return requested_start, False
