"""模型调用的小时汇总（statistics_usage_hourly）。

每条 llm_usage 记录按统计口径归一化后，累加进 (小时, 请求类型, 提供商, 模型, 任务, 聊天) 的一行。
统计长时间段时读这张表，只在时间段两端不满一小时的部分读明细，结果与逐条统计一致。

可靠性：
- 回填与增量是同一个过程：按 id 从游标往后分批读取，一批的汇总写入与游标前进在同一事务里提交。
  崩溃或报错时这一批整体回滚，下次从游标继续，不会重复累加，也不会漏。
- 数据库被锁时同一批短暂重试；仍失败则按指数退避推迟到之后的统计轮次再试。
- 汇总追上最新记录之前，统计照常读明细（调用方根据 UsageHourlyState.ready 选择）。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

import threading
import time

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from src.common.database.database import get_db_session
from src.common.database.chat_migration_models import copies as migration_copies
from src.common.database.database_model import ModelUsage, StatisticsAggregationCursor, StatisticsUsageHourly
from src.common.logger import get_logger
from src.services.statistics_usage_rules import clamp_hour_range
from src.services.statistics_usage_rules import (
    METRIC_COLUMNS,
    UsageKey,
    UsageMetrics,
    bucket_start,
    normalize_usage_record,
)

logger = get_logger("statistics_usage_hourly")

CURSOR_SOURCE = "usage_hourly_v1"
DEFAULT_CHUNK_SIZE = 20_000
DEFAULT_TIME_BUDGET_SECONDS = 20.0
_LOCK_RETRY_DELAYS = (0.5, 1.0, 2.0)
_BACKOFF_BASE_SECONDS = 30.0
_BACKOFF_MAX_SECONDS = 15 * 60.0
_UPSERT_BATCH_ROWS = 500

_USAGE_COLUMNS = (
    "timestamp", "request_type", "model_api_provider_name", "model_assign_name", "model_name", "session_id",
    "prompt_tokens", "completion_tokens", "prompt_cache_enabled", "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens", "cost", "time_cost", "task_name",
)
_METRIC_COLUMNS = METRIC_COLUMNS


@dataclass(frozen=True)
class UsageHourlyState:
    """ready：汇总已追上本次刷新时的最新记录，可以替代明细；cursor：已汇总到的最大 id。"""

    ready: bool
    cursor: int


@dataclass
class _RetryState:
    failures: int = 0
    next_attempt_at: float = 0.0


_refresh_lock = threading.Lock()
_retry_state = _RetryState()


def refresh_usage_hourly(
    *,
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> UsageHourlyState:
    """把游标之后的新记录汇总进小时表；首次运行即为分批回填。

    单轮用时受 time_budget_seconds 限制，没追上时返回 ready=False，下一轮接着做。
    任何失败都不抛出：记录日志并按指数退避推迟重试，本轮统计改读明细。
    """

    with _refresh_lock:
        if time.monotonic() < _retry_state.next_attempt_at:
            return UsageHourlyState(ready=False, cursor=_safe_read_cursor())
        deadline = time.monotonic() + time_budget_seconds
        processed_total = 0
        try:
            while True:
                processed = _run_with_lock_retry(lambda: _aggregate_next_chunk(chunk_size))
                processed_total += processed
                if processed < chunk_size or time.monotonic() >= deadline:
                    break
            cursor, max_id = _run_with_lock_retry(_read_progress)
        except Exception as error:
            _retry_state.failures += 1
            delay = min(_BACKOFF_BASE_SECONDS * 2 ** (_retry_state.failures - 1), _BACKOFF_MAX_SECONDS)
            _retry_state.next_attempt_at = time.monotonic() + delay
            logger.warning(
                f"模型调用小时汇总失败（第 {_retry_state.failures} 次），{delay:.0f} 秒后重试，本轮统计改读明细: {error}"
            )
            return UsageHourlyState(ready=False, cursor=_safe_read_cursor())

        _retry_state.failures = 0
        _retry_state.next_attempt_at = 0.0
        ready = cursor >= max_id
        if processed_total and not ready:
            logger.info(f"模型调用小时汇总回填中：已到 id {cursor} / {max_id}，本轮统计改读明细")
        return UsageHourlyState(ready=ready, cursor=cursor)


def reset_retry_state() -> None:
    """测试与手动修复后清除退避状态。"""

    _retry_state.failures = 0
    _retry_state.next_attempt_at = 0.0


def _run_with_lock_retry(operation):
    for attempt, delay in enumerate((*_LOCK_RETRY_DELAYS, None)):
        try:
            return operation()
        except OperationalError as error:
            if delay is None or "locked" not in str(error).lower():
                raise
            logger.debug(f"数据库被占用，{delay} 秒后重试小时汇总（第 {attempt + 1} 次）")
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _aggregate_next_chunk(chunk_size: int) -> int:
    """汇总游标之后的一批记录；汇总写入与游标前进同一事务提交。返回本批读到的记录数。"""

    with get_db_session(auto_commit=False) as session:
        cursor = _read_cursor(session)
        columns = [getattr(ModelUsage, name) for name in ("id", *_USAGE_COLUMNS)]
        rows = session.exec(
            select(*columns).where(ModelUsage.id > cursor).order_by(ModelUsage.id).limit(chunk_size)
        ).all()
        if not rows:
            return 0

        grouped: dict[tuple[datetime, UsageKey], list[float]] = {}
        copied_ids = set(session.scalars(select(migration_copies.c.target_key).where(
            migration_copies.c.table_name == "llm_usage",
            migration_copies.c.target_key.in_([str(row[0]) for row in rows]),
        )))
        for row in rows:
            if str(row[0]) in copied_ids:
                continue  # 迁移事务已同步复制对应小时汇总，不能再次累加。
            record = dict(zip(_USAGE_COLUMNS, row[1:], strict=True))
            timestamp = record["timestamp"]
            if not isinstance(timestamp, datetime):
                # 没有时间戳的记录不进入任何统计时段，逐条统计同样不会读到
                continue
            key, metrics = normalize_usage_record(record)
            totals = grouped.get((bucket_start(timestamp), key))
            if totals is None:
                grouped[(bucket_start(timestamp), key)] = list(metrics)
            else:
                for index, value in enumerate(metrics):
                    totals[index] += value

        _upsert_buckets(session, grouped)
        _write_cursor(session, int(rows[-1][0]))
        session.commit()
        return len(rows)


def _upsert_buckets(session, grouped: Mapping[tuple[datetime, UsageKey], Sequence[float]]) -> None:
    table = StatisticsUsageHourly.__table__
    values = [
        {"bucket_time": bucket, **key._asdict(), **dict(zip(_METRIC_COLUMNS, totals, strict=True))}
        for (bucket, key), totals in grouped.items()
    ]
    for start in range(0, len(values), _UPSERT_BATCH_ROWS):
        statement = sqlite_insert(table).values(values[start:start + _UPSERT_BATCH_ROWS])
        statement = statement.on_conflict_do_update(
            index_elements=["bucket_time", *UsageKey._fields],
            set_={name: table.c[name] + statement.excluded[name] for name in _METRIC_COLUMNS},
        )
        session.exec(statement)


def _read_cursor(session) -> int:
    value = session.scalar(
        select(StatisticsAggregationCursor.last_processed_id).where(
            StatisticsAggregationCursor.source_name == CURSOR_SOURCE
        )
    )
    return int(value) if value is not None else 0


def _write_cursor(session, last_id: int) -> None:
    table = StatisticsAggregationCursor.__table__
    statement = sqlite_insert(table).values(source_name=CURSOR_SOURCE, last_processed_id=last_id, updated_at=datetime.now())
    session.exec(
        statement.on_conflict_do_update(
            index_elements=["source_name"],
            set_={"last_processed_id": statement.excluded.last_processed_id, "updated_at": statement.excluded.updated_at},
        )
    )


def _read_progress() -> tuple[int, int]:
    with get_db_session(auto_commit=False) as session:
        cursor = _read_cursor(session)
        max_id = session.scalar(select(func.max(ModelUsage.id)))
    return cursor, int(max_id or 0)


def _safe_read_cursor() -> int:
    try:
        return _read_progress()[0]
    except Exception:
        return 0


def fetch_usage_records_between(start: datetime, end: datetime | None) -> Iterable[dict[str, object]]:
    """读取 [start, end) 的调用明细（end 为 None 表示不设上限），供时间段两端不满一小时的部分使用。"""

    columns = [getattr(ModelUsage, name) for name in ("id", *_USAGE_COLUMNS)]
    conditions = [ModelUsage.timestamp >= start]
    if end is not None:
        conditions.append(ModelUsage.timestamp < end)
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(select(*columns).where(*conditions)).all()
    return [dict(zip(("id", *_USAGE_COLUMNS), row, strict=True)) for row in rows]


def fetch_usage_records_after_id(cursor: int) -> Iterable[dict[str, object]]:
    """读取游标之后、还没进入汇总的记录（汇总刷新与本轮查询之间新写入的少量记录）。"""

    columns = [getattr(ModelUsage, name) for name in ("id", *_USAGE_COLUMNS)]
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(select(*columns).where(
            ModelUsage.id > cursor,
            ~ModelUsage.id.in_(select(migration_copies.c.target_key).where(migration_copies.c.table_name == "llm_usage")),
        )).all()
    return [dict(zip(("id", *_USAGE_COLUMNS), row, strict=True)) for row in rows]


def fetch_usage_hourly(start_bucket: datetime, end_bucket: datetime | None) -> list[tuple[datetime, UsageKey, UsageMetrics]]:
    """读取 [start_bucket, end_bucket) 内的小时汇总行；end_bucket 为 None 表示不设上限。"""

    table = StatisticsUsageHourly.__table__
    columns = [table.c.bucket_time, *(table.c[name] for name in UsageKey._fields), *(table.c[name] for name in _METRIC_COLUMNS)]
    conditions = [table.c.bucket_time >= start_bucket]
    if end_bucket is not None:
        conditions.append(table.c.bucket_time < end_bucket)
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(select(*columns).where(*conditions)).all()
    key_size = len(UsageKey._fields)
    return [
        (row[0], UsageKey(*row[1:1 + key_size]), tuple(row[1 + key_size:]))  # type: ignore[misc]
        for row in rows
    ]


def fetch_usage_duration_moments(start_bucket: datetime) -> list[dict[str, object]]:
    """按请求类型、提供商、模型、任务汇总 start_bucket 之后全部小时桶的有效耗时矩。"""

    table = StatisticsUsageHourly.__table__
    statement = (
        select(
            table.c.request_type, table.c.provider_name, table.c.model_name, table.c.task_name,
            func.sum(table.c.duration_count), func.sum(table.c.duration_sum), func.sum(table.c.duration_sum_sq),
        )
        .where(table.c.bucket_time >= start_bucket, table.c.duration_count > 0)
        .group_by(table.c.request_type, table.c.provider_name, table.c.model_name, table.c.task_name)
    )
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(statement).all()
    return [
        {
            "request_type": row[0], "provider_name": row[1], "model_name": row[2], "task_name": row[3],
            "count": int(row[4] or 0), "sum": float(row[5] or 0.0), "sum_sq": float(row[6] or 0.0),
        }
        for row in rows
    ]


def window_usage(
    start: datetime, end: datetime, *, end_inclusive: bool, cursor: int,
) -> list[tuple[datetime, UsageKey, UsageMetrics]]:
    """时间窗口内的全部用量：整点区间读汇总，起点所在的半小时、终点所在的半小时、刷新后新写入的记录读明细。

    返回 (时间, 维度, 指标)；汇总行的时间是整点，明细行是记录时间。各部分互不重叠。
    """
    upper = end + timedelta(microseconds=1) if end_inclusive else end
    items: list[tuple[datetime, UsageKey, UsageMetrics]] = []

    def add_records(range_start: datetime, range_end: datetime) -> None:
        for record in fetch_usage_records_between(range_start, range_end):
            key, metrics = normalize_usage_record(record)
            items.append((record["timestamp"], key, metrics))  # type: ignore[arg-type]

    hour_range = clamp_hour_range(start, end)
    if hour_range is None:
        add_records(start, upper)
        return items
    first, last = hour_range
    items.extend(fetch_usage_hourly(first, last))
    if start < first:
        add_records(start, first)
    add_records(last, upper)
    for record in fetch_usage_records_after_id(cursor):
        timestamp = record["timestamp"]
        if isinstance(timestamp, datetime) and first <= timestamp < last:
            key, metrics = normalize_usage_record(record)
            items.append((timestamp, key, metrics))
    return items
