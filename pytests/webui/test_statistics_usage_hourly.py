"""模型调用小时汇总：新旧算法结果一致；分批回填可在崩溃、报错、数据库被锁后继续。"""

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta

import json
import random

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select

from src.chat.utils import statistic
from src.chat.utils.statistic import StatisticOutputTask
from src.common.database.database_model import ModelUsage, StatisticsAggregationCursor, StatisticsUsageHourly
from src.services import statistics_usage_hourly as hourly
from src.services.statistics_usage_hourly import UsageHourlyState
from src.services.statistics_usage_rules import bucket_start, normalize_usage_record

NOW = datetime(2026, 11, 5, 18, 37, 21, 123456)
PERIODS = [
    ("all_time", NOW - timedelta(days=45, minutes=17)),
    ("last_30_days", NOW - timedelta(days=30)),
    ("last_7_days", NOW - timedelta(days=7)),
    ("last_3_days", NOW - timedelta(days=3)),
    ("last_24_hours", NOW - timedelta(days=1)),
    ("last_3_hours", NOW - timedelta(hours=3)),
    ("last_hour", NOW - timedelta(hours=1)),
    ("last_15_minutes", NOW - timedelta(minutes=15)),
]


def make_records(count: int = 3000, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    records = []
    for record_id in range(1, count + 1):
        # 大多数落在 46 天内，少量晚于基准时刻（逐条统计没有上限，汇总统计也必须算进去）
        offset = timedelta(seconds=rng.uniform(-46 * 86400, 600))
        records.append({
            "id": record_id,
            "timestamp": NOW + offset,
            "request_type": rng.choice(["maisaka.planner", "replyer", "", None, "a.b.c"]),
            "model_api_provider_name": rng.choice(["ds", "openai", "", None]),
            "model_assign_name": rng.choice(["flash", "", None]),
            "model_name": rng.choice(["deepseek-v4", "gpt-4o", "", None]),
            "session_id": rng.choice(["", "  chat-1 ", "chat-2", None]),
            "prompt_tokens": rng.choice([0, 12, 1500, None]),
            "completion_tokens": rng.choice([0, 7, 300, None]),
            "prompt_cache_enabled": rng.choice([True, False]),
            "prompt_cache_hit_tokens": rng.choice([0, 5, 900, -3, None]),
            "prompt_cache_miss_tokens": rng.choice([0, 4, None]),
            "cost": rng.choice([0.0, 0.0012, 0.31, None]),
            "time_cost": rng.choice([0.0, -1.0, None, 0.4, 2.5, 13.75]),
            "task_name": rng.choice(["replyer", "embedding", "image_embedding", "", None]),
        })
    return records


def install_in_memory_sources(monkeypatch, records: list[dict], cursor: int) -> None:
    """明细与汇总都由同一批内存记录提供；汇总只含 id <= cursor 的记录，模拟刷新后才写入的记录。"""

    monkeypatch.setattr(
        statistic, "fetch_model_usage_since",
        lambda start: [r for r in records if r["timestamp"] >= start],
    )

    def fetch_usage_hourly(start_bucket, end_bucket):
        grouped: dict = {}
        for record in records:
            if record["id"] > cursor:
                continue
            bucket = bucket_start(record["timestamp"])
            if bucket < start_bucket or (end_bucket is not None and bucket >= end_bucket):
                continue
            key, metrics = normalize_usage_record(record)
            totals = grouped.setdefault((bucket, key), [0] * len(metrics))
            for index, value in enumerate(metrics):
                totals[index] += value
        return [(bucket, key, tuple(totals)) for (bucket, key), totals in grouped.items()]

    def fetch_usage_duration_moments(start_bucket):
        grouped: dict = defaultdict(lambda: [0, 0.0, 0.0])
        for _bucket, key, metrics in fetch_usage_hourly(start_bucket, None):
            if metrics[6]:
                totals = grouped[(key.request_type, key.provider_name, key.model_name, key.task_name)]
                totals[0] += metrics[6]
                totals[1] += metrics[7]
                totals[2] += metrics[8]
        return [
            {"request_type": k[0], "provider_name": k[1], "model_name": k[2], "task_name": k[3],
             "count": v[0], "sum": v[1], "sum_sq": v[2]}
            for k, v in grouped.items()
        ]

    def fetch_model_duration_aggregates_since(start):
        # 旧算法：直接在明细上按原始字段分组（库里由 SQL 完成）
        grouped: dict = defaultdict(lambda: [0, 0.0, 0.0])
        for record in records:
            time_cost = record["time_cost"] or 0.0
            if record["timestamp"] < start or time_cost <= 0:
                continue
            key = (record["request_type"], record["model_api_provider_name"], record["model_assign_name"],
                   record["model_name"], record["task_name"])
            grouped[key][0] += 1
            grouped[key][1] += time_cost
            grouped[key][2] += time_cost * time_cost
        return [
            {"request_type": k[0], "model_api_provider_name": k[1], "model_assign_name": k[2],
             "model_name": k[3], "task_name": k[4], "count": v[0], "sum": v[1], "sum_sq": v[2]}
            for k, v in grouped.items()
        ]

    monkeypatch.setattr(statistic, "fetch_usage_hourly", fetch_usage_hourly)
    monkeypatch.setattr(statistic, "fetch_usage_duration_moments", fetch_usage_duration_moments)
    monkeypatch.setattr(statistic, "fetch_model_duration_aggregates_since", fetch_model_duration_aggregates_since)
    monkeypatch.setattr(
        statistic, "fetch_usage_records_between",
        lambda start, end: [r for r in records if r["timestamp"] >= start and (end is None or r["timestamp"] < end)],
    )
    monkeypatch.setattr(
        statistic, "fetch_usage_records_after_id", lambda last_id: [r for r in records if r["id"] > last_id],
    )


def normalized(value):
    """浮点误差内相等：统一保留 6 位小数后比较。"""
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, str):
        try:
            return normalized(json.loads(value)) if value.startswith("{") else value
        except ValueError:
            return value
    return value


@pytest.mark.parametrize("cursor", [3000, 2950])
def test_hourly_and_raw_produce_identical_period_statistics(monkeypatch, cursor):
    records = make_records()
    install_in_memory_sources(monkeypatch, records, cursor)

    raw = StatisticOutputTask._collect_model_request_for_period(list(PERIODS))
    mixed = StatisticOutputTask._collect_model_request_for_period(
        list(PERIODS), UsageHourlyState(ready=True, cursor=cursor), NOW
    )

    assert raw.keys() == mixed.keys()
    for period_key in raw:
        assert normalized(dict(mixed[period_key])) == normalized(dict(raw[period_key])), period_key
    # 确实走了汇总：长时间段的大部分记录来自汇总行
    assert raw["last_30_days"]["total_requests"] > 1500


def test_hourly_not_ready_falls_back_to_raw(monkeypatch):
    records = make_records(400)
    install_in_memory_sources(monkeypatch, records, cursor=0)
    monkeypatch.setattr(statistic, "fetch_usage_hourly", lambda *args: pytest.fail("未就绪时不应读汇总"))

    result = StatisticOutputTask._collect_model_request_for_period(
        list(PERIODS), UsageHourlyState(ready=False, cursor=0), NOW
    )
    assert result["all_time"]["total_requests"] == sum(1 for r in records if r["timestamp"] >= PERIODS[0][1])


@pytest.mark.parametrize("cursor", [3000, 2900])
def test_all_time_duration_moments_match_raw(monkeypatch, cursor):
    records = make_records()
    install_in_memory_sources(monkeypatch, records, cursor)
    task = object.__new__(StatisticOutputTask)
    task.all_time_start_time = PERIODS[0][1]

    def durations(state):
        task._usage_hourly = state
        data = StatisticOutputTask._build_stat_period_data()
        task._refresh_all_time_duration_stats(data)
        return {key: dict(data[key]) for key in data if key.startswith(("avg_time_cost", "std_time_cost"))}

    assert normalized(durations(UsageHourlyState(ready=True, cursor=cursor))) == normalized(durations(None))


# ─── 回填与增量：真实 SQLite，模拟崩溃、报错与数据库被锁 ─────────────────────


@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    SQLModel.metadata.create_all(
        engine,
        tables=[ModelUsage.__table__, StatisticsAggregationCursor.__table__, StatisticsUsageHourly.__table__],
    )

    @contextmanager
    def session_factory(auto_commit: bool = True):
        session = Session(engine)
        try:
            yield session
            if auto_commit:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(hourly, "get_db_session", session_factory)
    monkeypatch.setattr(hourly.time, "sleep", lambda seconds: None)
    hourly.reset_retry_state()
    yield engine
    hourly.reset_retry_state()


def insert_usage(engine, records):
    with Session(engine) as session:
        for record in records:
            if record["timestamp"] is None:
                continue
            session.add(ModelUsage(
                id=record["id"], timestamp=record["timestamp"],
                request_type=record["request_type"] or "", model_api_provider_name=record["model_api_provider_name"] or "",
                model_assign_name=record["model_assign_name"], model_name=record["model_name"] or "",
                session_id=record["session_id"] or "", task_name=record["task_name"],
                prompt_tokens=record["prompt_tokens"] or 0, completion_tokens=record["completion_tokens"] or 0,
                total_tokens=0, prompt_cache_enabled=record["prompt_cache_enabled"],
                prompt_cache_hit_tokens=record["prompt_cache_hit_tokens"] or 0,
                prompt_cache_miss_tokens=record["prompt_cache_miss_tokens"] or 0,
                cost=record["cost"] or 0.0, time_cost=record["time_cost"],
            ))
        session.commit()


def stored_totals(engine) -> dict:
    with Session(engine) as session:
        rows = session.exec(select(StatisticsUsageHourly)).all()
    return {
        (row.bucket_time, row.request_type, row.provider_name, row.model_name, row.task_name, row.session_id): (
            row.request_count, row.prompt_tokens, row.completion_tokens, row.cache_hit_tokens,
            row.cache_miss_tokens, round(row.cost, 9), row.duration_count, round(row.duration_sum, 9),
            round(row.duration_sum_sq, 9), row.response_time_count, round(row.response_time_sum, 9),
        )
        for row in rows
    }


def expected_totals(engine) -> dict:
    """按库里实际存下的字段逐条归一化后分组，作为汇总的期望结果。"""
    with Session(engine) as session:
        rows = session.exec(select(ModelUsage)).all()
    grouped: dict = {}
    for row in rows:
        key, metrics = normalize_usage_record(row.model_dump())
        totals = grouped.setdefault((bucket_start(row.timestamp), *key), [0] * len(metrics))
        for index, value in enumerate(metrics):
            totals[index] += value
    return {
        key: (*totals[:5], round(totals[5], 9), totals[6], round(totals[7], 9), round(totals[8], 9),
              totals[9], round(totals[10], 9))
        for key, totals in grouped.items()
    }


def test_backfill_runs_in_chunks_until_ready(usage_db):
    insert_usage(usage_db, make_records(250))

    first = hourly.refresh_usage_hourly(time_budget_seconds=0, chunk_size=40)
    assert first == UsageHourlyState(ready=False, cursor=40)
    for _ in range(10):
        state = hourly.refresh_usage_hourly(time_budget_seconds=0, chunk_size=40)
        if state.ready:
            break
    assert state == UsageHourlyState(ready=True, cursor=250)
    assert stored_totals(usage_db) == expected_totals(usage_db)


def test_crash_mid_chunk_rolls_back_and_resumes_without_double_counting(usage_db, monkeypatch):
    insert_usage(usage_db, make_records(200))
    assert hourly.refresh_usage_hourly(time_budget_seconds=0, chunk_size=50).cursor == 50

    original_write_cursor = hourly._write_cursor

    def crash_after_upsert(session, last_id):
        raise RuntimeError("进程在写游标前崩溃")

    monkeypatch.setattr(hourly, "_write_cursor", crash_after_upsert)
    failed = hourly.refresh_usage_hourly(time_budget_seconds=0, chunk_size=50)
    assert failed == UsageHourlyState(ready=False, cursor=50)
    # 这一批的汇总写入随游标一起回滚，表里仍然只有前 50 条
    with Session(usage_db) as session:
        assert sum(row.request_count for row in session.exec(select(StatisticsUsageHourly)).all()) == 50

    # 退避期内不重试
    monkeypatch.setattr(hourly, "_write_cursor", original_write_cursor)
    assert hourly.refresh_usage_hourly(time_budget_seconds=0, chunk_size=50) == UsageHourlyState(ready=False, cursor=50)

    hourly.reset_retry_state()
    while not hourly.refresh_usage_hourly(time_budget_seconds=60, chunk_size=50).ready:
        pass
    assert stored_totals(usage_db) == expected_totals(usage_db)


def test_backoff_grows_after_repeated_failures(usage_db, monkeypatch):
    insert_usage(usage_db, make_records(20))
    clock = {"now": 1000.0}
    monkeypatch.setattr(hourly.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(hourly, "_aggregate_next_chunk", lambda chunk_size: (_ for _ in ()).throw(RuntimeError("坏")))

    hourly.refresh_usage_hourly()
    first_retry = hourly._retry_state.next_attempt_at - clock["now"]
    clock["now"] += first_retry
    hourly.refresh_usage_hourly()
    second_retry = hourly._retry_state.next_attempt_at - clock["now"]
    assert first_retry == 30 and second_retry == 60


def test_database_locked_is_retried_within_the_same_round(usage_db, monkeypatch):
    insert_usage(usage_db, make_records(30))
    original = hourly._aggregate_next_chunk
    calls = {"count": 0}

    def flaky(chunk_size):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("UPDATE", {}, Exception("database is locked"))
        return original(chunk_size)

    monkeypatch.setattr(hourly, "_aggregate_next_chunk", flaky)
    assert hourly.refresh_usage_hourly(time_budget_seconds=60).ready
    assert stored_totals(usage_db) == expected_totals(usage_db)


def test_incremental_refresh_adds_only_new_rows(usage_db):
    records = make_records(120)
    insert_usage(usage_db, records[:80])
    assert hourly.refresh_usage_hourly(time_budget_seconds=60).ready
    insert_usage(usage_db, records[80:])
    assert hourly.refresh_usage_hourly(time_budget_seconds=60) == UsageHourlyState(ready=True, cursor=120)
    assert stored_totals(usage_db) == expected_totals(usage_db)


@pytest.mark.parametrize("cursor", [3000, 2950])
def test_hourly_and_raw_produce_identical_charts_and_trends(monkeypatch, cursor):
    from src.chat.utils.statistics_timeseries import collect_time_series

    records = make_records()
    install_in_memory_sources(monkeypatch, records, cursor)
    chart_now = NOW + timedelta(minutes=3, seconds=7)
    task = object.__new__(StatisticOutputTask)
    task.all_time_start_time = PERIODS[0][1]
    task.stat_period = [(key, NOW - start) for key, start in PERIODS]
    start = min(chart_now - timedelta(hours=48), NOW - timedelta(days=30))

    def run(usage, aggregate_limit):
        trends = task._build_period_trend_collector(NOW)
        if usage is None:
            usage = StatisticOutputTask._time_series_usage_entries(start, NOW, chart_now, trends, cursor)
        charts, metrics = collect_time_series(
            NOW, chart_now, usage, [], [], lambda platform, user: False, trends, aggregate_limit,
        )
        return normalized({"charts": charts, "metrics": metrics, "trends": trends.result()})

    raw = run([r for r in records if r["timestamp"] >= start], None)
    mixed = run(None, bucket_start(NOW))
    diffs = []

    def walk(path, a, b):
        if isinstance(a, dict):
            for key in set(a) | set(b):
                walk(path + "/" + str(key), a.get(key), b.get(key))
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for index, (x, y) in enumerate(zip(a, b, strict=True)):
                walk(f"{path}[{index}]", x, y)
        elif a != b:
            diffs.append((path, a, b))

    walk("", raw, mixed)
    assert not diffs, diffs[:8]
    # 确实有汇总行参与，而不是全部回退成明细
    entries = StatisticOutputTask._time_series_usage_entries(
        start, NOW, chart_now, task._build_period_trend_collector(NOW), cursor
    )
    assert sum(1 for entry in entries if entry.source == "hourly") > 100


def test_dashboard_and_previous_period_match_sql(usage_db, monkeypatch):
    from src.common.database.database_model import OnlineTime
    from src.services import statistics_service as service

    SQLModel.metadata.create_all(usage_db, tables=[OnlineTime.__table__])
    monkeypatch.setattr(service, "get_db_session", hourly.get_db_session)
    records = make_records(1500)
    for record in records:
        # 写入记录时分配名取自模型配置名，不会是空字符串（空字符串在 SQL 与统计规则里含义不同）
        if record["model_assign_name"] == "":
            record["model_assign_name"] = None
    insert_usage(usage_db, records[:1450])
    state = hourly.refresh_usage_hourly(time_budget_seconds=60)
    assert state.ready
    insert_usage(usage_db, records[1450:])  # 刷新之后才写入的记录

    for hours in (3, 24, 168, 720):
        start = NOW - timedelta(hours=hours)
        items = service._window_usage_items(start, NOW, True, state)
        assert normalized(_summary(service, items)) == normalized(_summary(service, None, start))
        mixed_models = [(m.model_name, m.request_count) for m in service._get_model_statistics_sync(start, NOW, items)]
        sql_models = [(m.model_name, m.request_count) for m in service._get_model_statistics_sync(start, NOW)]
        assert mixed_models == sql_models, (hours, mixed_models, sql_models)
        assert normalized([m.model_dump() for m in service._get_model_statistics_sync(start, NOW, items)]) ==             normalized([m.model_dump() for m in service._get_model_statistics_sync(start, NOW)])
        for series in (service._get_hourly_statistics_sync, service._get_daily_statistics_sync):
            assert normalized([m.model_dump() for m in series(start, NOW, items)]) ==                 normalized([m.model_dump() for m in series(start, NOW)])

        previous = (start - timedelta(hours=hours), start)
        assert normalized(service.fetch_window_usage_totals(*previous, usage_hourly=state)) ==             normalized(service.fetch_window_usage_totals(*previous))


def _summary(service, items, start=None):
    if items is not None:
        totals = service._usage_totals(items)
        return {key: totals[key] for key in (
            "total_requests", "total_cost", "input_tokens", "output_tokens", "cache_hit_tokens",
            "cache_miss_tokens", "chat_cache_hit_tokens", "chat_cache_miss_tokens", "avg_response_time",
        )}
    row = service._summary_usage_row(start, NOW)
    keys = ("total_requests", "total_cost", "input_tokens", "output_tokens", "cache_hit_tokens",
            "cache_miss_tokens", "chat_cache_hit_tokens", "chat_cache_miss_tokens", "avg_response_time")
    return {key: (value or 0) for key, value in zip(keys, row, strict=True)}


def test_backfill_continues_after_output_when_not_caught_up(monkeypatch):
    import asyncio

    calls = []

    def fake_refresh(**kwargs):
        calls.append(kwargs)
        return UsageHourlyState(ready=True, cursor=10)

    monkeypatch.setattr(statistic, "refresh_usage_hourly", fake_refresh)
    task = object.__new__(StatisticOutputTask)

    task._usage_hourly = UsageHourlyState(ready=True, cursor=10)
    asyncio.run(task._finish_usage_hourly_backfill())
    assert calls == []  # 已追上时不再回填

    task._usage_hourly = UsageHourlyState(ready=False, cursor=5)
    asyncio.run(task._finish_usage_hourly_backfill())
    assert calls == [{"time_budget_seconds": StatisticOutputTask.BACKFILL_AFTER_OUTPUT_SECONDS}]
