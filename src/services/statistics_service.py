from datetime import datetime, timedelta
from typing import Any, Dict, List

import asyncio

from sqlalchemy import desc, func, or_
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import Messages, ModelUsage, OnlineTime, ToolRecord
from src.common.logger import get_logger
from src.common.message_repository import count_messages
from src.manager.local_store_manager import local_storage
from src.webui.schemas.statistics import DashboardData, ModelStatistics, StatisticsSummary, TimeSeriesData

logger = get_logger("statistics_service")

DASHBOARD_STATISTICS_CACHE_KEY = "webui_dashboard_statistics_cache"
DASHBOARD_STATISTICS_CACHE_VERSION = 2
DEFAULT_DASHBOARD_CACHE_MAX_AGE_SECONDS = 20 * 60
DEFAULT_DASHBOARD_CACHE_HOURS = (24, 168, 720)
_SPARSE_TIME_SERIES_FIELDS = ("hourly_data", "daily_data")


async def get_dashboard_statistics(hours: int = 24, *, use_cache: bool = True) -> DashboardData:
    """获取 WebUI 仪表盘统计数据。"""
    if use_cache:
        cached_data = get_cached_dashboard_statistics(hours)
        if cached_data is not None:
            return cached_data

    data = await compute_dashboard_statistics(hours=hours)
    if use_cache:
        update_dashboard_statistics_cache_entry(hours, data)
    return data


def build_empty_dashboard_statistics() -> DashboardData:
    """构造空的 WebUI 仪表盘统计数据。"""
    return DashboardData(
        summary=StatisticsSummary(),
        model_stats=[],
        hourly_data=[],
        daily_data=[],
        recent_activity=[],
    )


async def compute_dashboard_statistics(hours: int = 24) -> DashboardData:
    """获取 WebUI 仪表盘统计数据。"""
    now = datetime.now()
    start_time = now - timedelta(hours=hours)

    summary = await get_summary_statistics(start_time, now)
    model_stats = await get_model_statistics(start_time, now)
    hourly_data = await get_hourly_statistics(start_time, now)
    daily_data = await get_daily_statistics(start_time, now)
    recent_activity = await get_recent_activity(start_time=start_time, end_time=now, limit=10)

    return DashboardData(
        summary=summary,
        model_stats=model_stats,
        hourly_data=hourly_data,
        daily_data=daily_data,
        recent_activity=recent_activity,
    )


def get_cached_dashboard_statistics(
    hours: int = 24,
    *,
    max_age_seconds: int = DEFAULT_DASHBOARD_CACHE_MAX_AGE_SECONDS,
) -> DashboardData | None:
    """从本地快照读取 WebUI 仪表盘统计数据。"""
    raw_cache = local_storage[DASHBOARD_STATISTICS_CACHE_KEY]
    if not isinstance(raw_cache, dict):
        return None
    if raw_cache.get("version") != DASHBOARD_STATISTICS_CACHE_VERSION:
        return None

    generated_at = raw_cache.get("generated_at")
    if not isinstance(generated_at, (int, float)):
        return None
    if datetime.now().timestamp() - float(generated_at) > max_age_seconds:
        return None

    entries = raw_cache.get("entries")
    if not isinstance(entries, dict):
        return None

    entry = entries.get(str(hours))
    if not isinstance(entry, dict):
        return None

    try:
        expanded_entry = _expand_dashboard_cache_entry(entry, hours=hours, generated_at=float(generated_at))
        return DashboardData.model_validate(expanded_entry)
    except Exception as e:
        logger.warning(f"读取 WebUI 统计缓存失败，将实时计算: {e}")
        return None


def store_dashboard_statistics_cache(entries: dict[int, DashboardData], *, generated_at: datetime | None = None) -> None:
    """保存 WebUI 仪表盘统计数据快照。"""
    snapshot_time = generated_at or datetime.now()
    local_storage[DASHBOARD_STATISTICS_CACHE_KEY] = {
        "version": DASHBOARD_STATISTICS_CACHE_VERSION,
        "generated_at": snapshot_time.timestamp(),
        "entries": {str(hours): _compact_dashboard_cache_entry(data) for hours, data in entries.items()},
    }


def update_dashboard_statistics_cache_entry(
    hours: int,
    data: DashboardData,
    *,
    generated_at: datetime | None = None,
) -> None:
    """更新单个 WebUI 仪表盘统计缓存条目。"""
    raw_cache = local_storage[DASHBOARD_STATISTICS_CACHE_KEY]
    entries: dict[str, Any] = {}
    if isinstance(raw_cache, dict) and isinstance(raw_cache.get("entries"), dict):
        entries.update(raw_cache["entries"])

    snapshot_time = generated_at or datetime.now()
    entries[str(hours)] = _compact_dashboard_cache_entry(data)
    local_storage[DASHBOARD_STATISTICS_CACHE_KEY] = {
        "version": DASHBOARD_STATISTICS_CACHE_VERSION,
        "generated_at": snapshot_time.timestamp(),
        "entries": entries,
    }


async def refresh_dashboard_statistics_cache(hours_values: tuple[int, ...] = DEFAULT_DASHBOARD_CACHE_HOURS) -> None:
    """刷新 WebUI 仪表盘统计数据快照。"""
    cache_entries: dict[int, DashboardData] = {}
    for hours in hours_values:
        cache_entries[hours] = await compute_dashboard_statistics(hours=hours)
    store_dashboard_statistics_cache(cache_entries)


def _compact_dashboard_cache_entry(data: DashboardData) -> dict[str, Any]:
    """压缩 WebUI 仪表盘缓存条目，去掉全 0 时间桶。"""
    entry = data.model_dump(mode="json")
    for field_name in _SPARSE_TIME_SERIES_FIELDS:
        series = entry.get(field_name)
        if isinstance(series, list):
            entry[field_name] = [item for item in series if not _is_empty_time_series_item(item)]
    entry["sparse"] = True
    return entry


def _expand_dashboard_cache_entry(entry: dict[str, Any], *, hours: int, generated_at: float) -> dict[str, Any]:
    """将稀疏缓存条目展开为前端需要的完整时间序列。"""
    if entry.get("sparse") is not True:
        return entry

    expanded = dict(entry)
    generated_datetime = datetime.fromtimestamp(generated_at)
    expanded["hourly_data"] = _expand_time_series(
        sparse_series=entry.get("hourly_data"),
        start_time=generated_datetime - timedelta(hours=hours),
        end_time=generated_datetime,
        step=timedelta(hours=1),
        timestamp_format="%Y-%m-%dT%H:00:00",
    )
    expanded["daily_data"] = _expand_time_series(
        sparse_series=entry.get("daily_data"),
        start_time=generated_datetime - timedelta(hours=hours),
        end_time=generated_datetime,
        step=timedelta(days=1),
        timestamp_format="%Y-%m-%dT00:00:00",
    )
    expanded.pop("sparse", None)
    return expanded


def _expand_time_series(
    *,
    sparse_series: Any,
    start_time: datetime,
    end_time: datetime,
    step: timedelta,
    timestamp_format: str,
) -> list[dict[str, Any]]:
    sparse_items = sparse_series if isinstance(sparse_series, list) else []
    sparse_by_timestamp = {
        item.get("timestamp"): item
        for item in sparse_items
        if isinstance(item, dict) and isinstance(item.get("timestamp"), str)
    }

    result: list[dict[str, Any]] = []
    current = _floor_time_for_format(start_time, timestamp_format)
    while current <= end_time:
        timestamp = current.strftime(timestamp_format)
        item = sparse_by_timestamp.get(timestamp)
        if isinstance(item, dict):
            result.append(item)
        else:
            result.append({"timestamp": timestamp, "requests": 0, "cost": 0.0, "tokens": 0})
        current += step
    return result


def _floor_time_for_format(value: datetime, timestamp_format: str) -> datetime:
    if "%H" in timestamp_format:
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _is_empty_time_series_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        int(item.get("requests") or 0) == 0
        and float(item.get("cost") or 0.0) == 0.0
        and int(item.get("tokens") or 0) == 0
    )


async def get_summary_statistics(start_time: datetime, end_time: datetime) -> StatisticsSummary:
    """获取指定时间范围内的摘要统计数据。"""
    return await asyncio.to_thread(_get_summary_statistics_sync, start_time, end_time)


def _get_summary_statistics_sync(start_time: datetime, end_time: datetime) -> StatisticsSummary:
    """在线程中同步查询指定时间范围内的摘要统计数据。"""
    summary = StatisticsSummary(
        total_requests=0,
        total_cost=0.0,
        total_tokens=0,
        online_time=0.0,
        total_messages=0,
        total_replies=0,
        avg_response_time=0.0,
        cost_per_hour=0.0,
        tokens_per_hour=0.0,
    )

    with get_db_session(auto_commit=False) as session:
        statement = select(
            func.count().label("total_requests"),
            func.sum(col(ModelUsage.cost)).label("total_cost"),
            func.sum(col(ModelUsage.total_tokens)).label("total_tokens"),
            func.avg(col(ModelUsage.time_cost)).label("avg_response_time"),
        ).where(col(ModelUsage.timestamp) >= start_time, col(ModelUsage.timestamp) <= end_time)
        result = session.exec(statement).first()

    if result:
        total_requests, total_cost, total_tokens, avg_response_time = result
        summary.total_requests = total_requests or 0
        summary.total_cost = float(total_cost or 0.0)
        summary.total_tokens = total_tokens or 0
        summary.avg_response_time = float(avg_response_time or 0.0)

    with get_db_session(auto_commit=False) as session:
        statement = select(OnlineTime).where(
            or_(
                col(OnlineTime.start_timestamp) >= start_time,
                col(OnlineTime.end_timestamp) >= start_time,
            )
        )
        online_records = session.exec(statement).all()

    for record in online_records:
        start = max(record.start_timestamp, start_time)
        end = min(record.end_timestamp, end_time)
        if end > start:
            summary.online_time += (end - start).total_seconds()

    summary.total_messages = count_messages(start_time=start_time.timestamp(), end_time=end_time.timestamp())
    summary.total_replies = count_messages(
        start_time=start_time.timestamp(),
        end_time=end_time.timestamp(),
        has_reply_to=True,
    )

    if summary.online_time > 0:
        online_hours = summary.online_time / 3600.0
        summary.cost_per_hour = summary.total_cost / online_hours
        summary.tokens_per_hour = summary.total_tokens / online_hours

    return summary


async def get_model_statistics(start_time: datetime, end_time: datetime | None = None) -> List[ModelStatistics]:
    """获取指定时间范围内的模型统计数据。"""
    return await asyncio.to_thread(_get_model_statistics_sync, start_time, end_time)


def _get_model_statistics_sync(start_time: datetime, end_time: datetime | None = None) -> List[ModelStatistics]:
    """在线程中同步查询指定时间范围内的模型统计数据。"""
    model_name_expr = func.coalesce(col(ModelUsage.model_assign_name), col(ModelUsage.model_name), "unknown")
    statement = (
        select(
            model_name_expr.label("model_name"),
            func.count().label("request_count"),
            func.sum(col(ModelUsage.cost)).label("total_cost"),
            func.sum(col(ModelUsage.total_tokens)).label("total_tokens"),
            func.avg(col(ModelUsage.time_cost)).label("avg_response_time"),
        )
        .where(col(ModelUsage.timestamp) >= start_time)
        .group_by(model_name_expr)
        .order_by(desc(func.count()))
        .limit(10)
    )
    if end_time is not None:
        statement = statement.where(col(ModelUsage.timestamp) <= end_time)

    with get_db_session(auto_commit=False) as session:
        rows = session.exec(statement).all()

    return [
        ModelStatistics(
            model_name=row[0] or "unknown",
            request_count=int(row[1] or 0),
            total_cost=float(row[2] or 0.0),
            total_tokens=int(row[3] or 0),
            avg_response_time=float(row[4] or 0.0),
        )
        for row in rows
    ]


async def get_hourly_statistics(start_time: datetime, end_time: datetime) -> List[TimeSeriesData]:
    """按小时聚合 LLM 请求、费用和 token。"""
    return await asyncio.to_thread(_get_hourly_statistics_sync, start_time, end_time)


def _get_hourly_statistics_sync(start_time: datetime, end_time: datetime) -> List[TimeSeriesData]:
    """在线程中同步执行按小时聚合。"""
    hour_expr = func.strftime("%Y-%m-%dT%H:00:00", col(ModelUsage.timestamp))
    statement = (
        select(
            hour_expr.label("hour"),
            func.count().label("requests"),
            func.sum(col(ModelUsage.cost)).label("cost"),
            func.sum(col(ModelUsage.total_tokens)).label("tokens"),
        )
        .where(col(ModelUsage.timestamp) >= start_time, col(ModelUsage.timestamp) <= end_time)
        .group_by(hour_expr)
    )

    with get_db_session(auto_commit=False) as session:
        rows = session.exec(statement).all()

    data_dict = {row[0]: row for row in rows}
    result = []
    current = start_time.replace(minute=0, second=0, microsecond=0)
    while current <= end_time:
        hour_str = current.strftime("%Y-%m-%dT%H:00:00")
        if hour_str in data_dict:
            row = data_dict[hour_str]
            result.append(
                TimeSeriesData(
                    timestamp=hour_str,
                    requests=row[1] or 0,
                    cost=float(row[2] or 0.0),
                    tokens=row[3] or 0,
                )
            )
        else:
            result.append(TimeSeriesData(timestamp=hour_str, requests=0, cost=0.0, tokens=0))
        current += timedelta(hours=1)

    return result


async def get_daily_statistics(start_time: datetime, end_time: datetime) -> List[TimeSeriesData]:
    """按天聚合 LLM 请求、费用和 token。"""
    return await asyncio.to_thread(_get_daily_statistics_sync, start_time, end_time)


def _get_daily_statistics_sync(start_time: datetime, end_time: datetime) -> List[TimeSeriesData]:
    """在线程中同步执行按天聚合。"""
    day_expr = func.strftime("%Y-%m-%dT00:00:00", col(ModelUsage.timestamp))
    statement = (
        select(
            day_expr.label("day"),
            func.count().label("requests"),
            func.sum(col(ModelUsage.cost)).label("cost"),
            func.sum(col(ModelUsage.total_tokens)).label("tokens"),
        )
        .where(col(ModelUsage.timestamp) >= start_time, col(ModelUsage.timestamp) <= end_time)
        .group_by(day_expr)
    )

    with get_db_session(auto_commit=False) as session:
        rows = session.exec(statement).all()

    data_dict = {row[0]: row for row in rows}
    result = []
    current = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end_time:
        day_str = current.strftime("%Y-%m-%dT00:00:00")
        if day_str in data_dict:
            row = data_dict[day_str]
            result.append(
                TimeSeriesData(
                    timestamp=day_str,
                    requests=row[1] or 0,
                    cost=float(row[2] or 0.0),
                    tokens=row[3] or 0,
                )
            )
        else:
            result.append(TimeSeriesData(timestamp=day_str, requests=0, cost=0.0, tokens=0))
        current += timedelta(days=1)

    return result


async def get_recent_activity(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """获取指定时间范围内最近的 LLM 调用记录。"""
    return await asyncio.to_thread(_get_recent_activity_sync, start_time, end_time, limit)


def _get_recent_activity_sync(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """在线程中同步查询指定时间范围内最近的 LLM 调用记录。"""
    with get_db_session(auto_commit=False) as session:
        statement = select(ModelUsage)
        if start_time is not None:
            statement = statement.where(col(ModelUsage.timestamp) >= start_time)
        if end_time is not None:
            statement = statement.where(col(ModelUsage.timestamp) <= end_time)
        statement = statement.order_by(desc(col(ModelUsage.timestamp))).limit(limit)
        records = session.exec(statement).all()

    activities = []
    for record in records:
        activities.append(
            {
                "timestamp": record.timestamp.isoformat(),
                "model": record.model_assign_name or record.model_name,
                "request_type": record.request_type,
                "tokens": record.total_tokens or 0,
                "cost": record.cost or 0.0,
                "time_cost": record.time_cost or 0.0,
                "status": None,
            }
        )

    return activities


def fetch_online_time_since(query_start_time: datetime) -> list[tuple[datetime, datetime]]:
    """获取指定时间之后仍有覆盖的在线时间区间。"""
    with get_db_session(auto_commit=False) as session:
        statement = select(OnlineTime).where(col(OnlineTime.end_timestamp) >= query_start_time)
        records = session.exec(statement).all()
        return [(record.start_timestamp, record.end_timestamp) for record in records]


def fetch_model_usage_since(query_start_time: datetime) -> list[dict[str, object]]:
    """获取指定时间之后的 LLM 使用记录。"""
    with get_db_session(auto_commit=False) as session:
        statement = select(ModelUsage).where(col(ModelUsage.timestamp) >= query_start_time)
        records = session.exec(statement).all()
        return [
            {
                "timestamp": record.timestamp,
                "request_type": record.request_type,
                "model_api_provider_name": record.model_api_provider_name,
                "model_assign_name": record.model_assign_name,
                "model_name": record.model_name,
                "session_id": record.session_id,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "prompt_cache_enabled": bool(getattr(record, "prompt_cache_enabled", False)),
                "prompt_cache_hit_tokens": getattr(record, "prompt_cache_hit_tokens", 0) or 0,
                "prompt_cache_miss_tokens": getattr(record, "prompt_cache_miss_tokens", 0) or 0,
                "cost": record.cost,
                "time_cost": record.time_cost,
            }
            for record in records
        ]


def fetch_messages_since(query_start_time: datetime) -> list[Messages]:
    """获取指定时间之后的消息记录。"""
    with get_db_session(auto_commit=False) as session:
        statement = select(Messages).where(col(Messages.timestamp) >= query_start_time)
        return list(session.exec(statement).all())


def fetch_tool_records_since(query_start_time: datetime) -> list[ToolRecord]:
    """获取指定时间之后的工具调用记录。"""
    with get_db_session(auto_commit=False) as session:
        statement = select(ToolRecord).where(col(ToolRecord.timestamp) >= query_start_time)
        return list(session.exec(statement).all())


def get_earliest_statistics_time(fallback_time: datetime) -> datetime:
    """获取统计数据中最早的记录时间。"""
    try:
        with get_db_session(auto_commit=False) as session:
            start_times = [
                session.exec(select(func.min(ModelUsage.timestamp))).first(),
                session.exec(select(func.min(Messages.timestamp))).first(),
                session.exec(select(func.min(OnlineTime.start_timestamp))).first(),
                session.exec(select(func.min(ToolRecord.timestamp))).first(),
            ]
    except Exception as e:
        logger.warning(f"获取全量统计起始时间失败，将使用回退时间: {e}")
        return fallback_time

    valid_start_times = [item for item in start_times if isinstance(item, datetime)]
    if valid_start_times:
        return min(valid_start_times)
    return fallback_time
