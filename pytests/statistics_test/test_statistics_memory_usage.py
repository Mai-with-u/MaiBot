from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

from src.chat.utils import statistic
from src.common.database.database_model import ModelUsage, OnlineTime
from src.services import statistics_service


def _model_usage(
    timestamp: datetime,
    *,
    request_type: str = "replyer.chat",
    provider_name: str = "provider-a",
    model_assign_name: str | None = "model-a",
    model_name: str = "upstream-model",
    time_cost: float = 1.0,
    task_name: str | None = None,
    prompt_tokens: int = 10,
    cache_enabled: bool = False,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> ModelUsage:
    return ModelUsage(
        model_name=model_name,
        model_assign_name=model_assign_name,
        model_api_provider_name=provider_name,
        session_id="session-a",
        task_name=task_name,
        request_type=request_type,
        time_cost=time_cost,
        timestamp=timestamp,
        prompt_tokens=prompt_tokens,
        completion_tokens=5,
        total_tokens=prompt_tokens + 5,
        prompt_cache_enabled=cache_enabled,
        prompt_cache_hit_tokens=cache_hit_tokens,
        prompt_cache_miss_tokens=cache_miss_tokens,
        cost=0.01,
    )


def _patch_statistics_database(monkeypatch, tmp_path, records: list[SQLModel]) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'statistics.db'}")
    SQLModel.metadata.create_all(engine, tables=[ModelUsage.__table__, OnlineTime.__table__])
    with Session(engine) as session:
        session.add_all(records)
        session.commit()

    @contextmanager
    def get_test_db_session(*, auto_commit: bool = True):
        with Session(engine) as session:
            yield session
            if auto_commit:
                session.commit()

    monkeypatch.setattr(statistics_service, "get_db_session", get_test_db_session)


def test_hourly_online_seconds_splits_and_merges_intervals(monkeypatch, tmp_path) -> None:
    start_time = datetime(2026, 7, 1)
    records = [
        OnlineTime(
            timestamp=start_time,
            duration_minutes=1200,
            start_timestamp=start_time + timedelta(minutes=50),
            end_timestamp=start_time + timedelta(minutes=70),
        ),
        OnlineTime(
            timestamp=start_time,
            duration_minutes=1200,
            start_timestamp=start_time + timedelta(minutes=60),
            end_timestamp=start_time + timedelta(minutes=80),
        ),
    ]
    _patch_statistics_database(monkeypatch, tmp_path, records)

    result = statistics_service._get_hourly_online_seconds_sync(
        start_time,
        start_time + timedelta(hours=2),
    )

    assert result == {
        "2026-07-01T00:00:00": 600.0,
        "2026-07-01T01:00:00": 1200.0,
    }


def test_summary_cache_rates_separate_all_and_chat_tasks(monkeypatch, tmp_path) -> None:
    start_time = datetime(2026, 7, 1)
    records = [
        _model_usage(
            start_time + timedelta(seconds=1),
            task_name="replyer",
            cache_enabled=True,
            cache_hit_tokens=80,
            cache_miss_tokens=20,
            prompt_tokens=100,
        ),
        _model_usage(
            start_time + timedelta(seconds=2),
            task_name="planner",
            cache_enabled=True,
            cache_hit_tokens=30,
            cache_miss_tokens=70,
            prompt_tokens=100,
        ),
        _model_usage(
            start_time + timedelta(seconds=3),
            task_name="utils",
            cache_enabled=True,
            cache_hit_tokens=100,
            cache_miss_tokens=0,
            prompt_tokens=100,
        ),
    ]
    _patch_statistics_database(monkeypatch, tmp_path, records)
    monkeypatch.setattr(statistics_service, "count_messages", lambda **_: 0)

    summary = statistics_service._get_summary_statistics_sync(
        start_time,
        start_time + timedelta(hours=1),
    )

    assert summary.cache_hit_tokens == 210
    assert summary.cache_miss_tokens == 90
    assert summary.cache_hit_rate == 0.7
    assert summary.chat_cache_hit_tokens == 110
    assert summary.chat_cache_miss_tokens == 90
    assert summary.chat_cache_hit_rate == 0.55


def test_fetch_model_usage_since_reads_records_in_batches(monkeypatch, tmp_path) -> None:
    start_time = datetime(2026, 7, 1)
    records = [
        _model_usage(start_time - timedelta(seconds=1)),
        *[_model_usage(start_time + timedelta(seconds=index), time_cost=float(index)) for index in range(1, 6)],
    ]
    _patch_statistics_database(monkeypatch, tmp_path, records)

    result = list(statistics_service.fetch_model_usage_since(start_time, batch_size=2))

    assert len(result) == 5
    assert [record["time_cost"] for record in result] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert all(record["session_id"] == "session-a" for record in result)


def test_duration_aggregation_preserves_existing_statistics_behavior(monkeypatch, tmp_path) -> None:
    start_time = datetime(2026, 7, 1)
    records = [
        _model_usage(start_time - timedelta(seconds=1), time_cost=100.0),
        _model_usage(start_time, time_cost=1.0),
        _model_usage(start_time + timedelta(seconds=1), time_cost=3.0),
        _model_usage(start_time + timedelta(seconds=2), time_cost=0.0),
        _model_usage(
            start_time + timedelta(seconds=3),
            request_type="planner.plan",
            provider_name="provider-b",
            model_assign_name=None,
            model_name="model-b",
            time_cost=2.0,
        ),
    ]
    _patch_statistics_database(monkeypatch, tmp_path, records)
    task = object.__new__(statistic.StatisticOutputTask)
    task.all_time_start_time = start_time
    stat_data = statistic.StatisticOutputTask._build_stat_period_data()
    monkeypatch.setattr(
        statistic,
        "fetch_model_duration_aggregates_since",
        statistics_service.fetch_model_duration_aggregates_since,
    )

    task._refresh_all_time_duration_stats(stat_data)

    assert stat_data[statistic.AVG_TIME_COST_BY_TYPE] == {
        "planner.plan": 2.0,
        "replyer.chat": 2.0,
    }
    assert stat_data[statistic.STD_TIME_COST_BY_TYPE] == {
        "planner.plan": 0.0,
        "replyer.chat": 1.0,
    }
    assert stat_data[statistic.AVG_TIME_COST_BY_MODULE] == {
        "planner": 2.0,
        "replyer": 2.0,
    }
    assert stat_data[statistic.AVG_TIME_COST_BY_USER] == {
        "provider-a": 2.0,
        "provider-b": 2.0,
    }
    assert stat_data[statistic.AVG_TIME_COST_BY_MODEL] == {
        "model-a": 2.0,
        "model-b": 2.0,
    }


def test_collect_model_statistics_does_not_keep_raw_duration_lists(monkeypatch) -> None:
    start_time = datetime(2026, 7, 1)
    records = [
        {
            "timestamp": start_time,
            "request_type": "replyer.chat",
            "model_api_provider_name": "provider-a",
            "model_assign_name": "model-a",
            "model_name": "upstream-model",
            "session_id": "session-a",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_cache_enabled": False,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "cost": 0.01,
            "time_cost": 1.0,
        },
        {
            "timestamp": start_time + timedelta(seconds=1),
            "request_type": "replyer.chat",
            "model_api_provider_name": "provider-a",
            "model_assign_name": "model-a",
            "model_name": "upstream-model",
            "session_id": "session-a",
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "prompt_cache_enabled": False,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "cost": 0.02,
            "time_cost": 3.0,
        },
    ]
    monkeypatch.setattr(statistic, "fetch_model_usage_since", lambda _: iter(records))

    stats = statistic.StatisticOutputTask._collect_model_request_for_period([("all_time", start_time)])
    all_time_stats = stats["all_time"]

    assert all_time_stats[statistic.TOTAL_REQ_CNT] == 2
    assert all_time_stats[statistic.TOTAL_TOK_BY_MODEL] == {"model-a": 45}
    assert all_time_stats[statistic.TOTAL_COST] == 0.03
    assert all_time_stats[statistic.AVG_TIME_COST_BY_MODEL] == {"model-a": 2.0}
    assert all_time_stats[statistic.STD_TIME_COST_BY_MODEL] == {"model-a": 1.0}
    assert all_time_stats[statistic.TIME_COST_BY_TYPE] == {}
    assert all_time_stats[statistic.TIME_COST_BY_USER] == {}
    assert all_time_stats[statistic.TIME_COST_BY_MODEL] == {}
    assert all_time_stats[statistic.TIME_COST_BY_MODULE] == {}


def test_detailed_statistics_snapshot_reuses_html_report_data(monkeypatch) -> None:
    now = datetime(2026, 7, 1, 12)
    task = object.__new__(statistic.StatisticOutputTask)
    task.all_time_start_time = now - timedelta(days=10)
    task.stat_period = [("all_time", timedelta(days=10), "自部署以来")]
    task.name_mapping = {
        "g100": ("测试群聊", now.timestamp()),
        "session-a": ("测试群聊", now.timestamp()),
    }
    stat_data = statistic.StatisticOutputTask._build_stat_period_data()
    stat_data[statistic.TOTAL_REQ_CNT] = 2
    stat_data[statistic.TOTAL_COST] = 0.3
    stat_data[statistic.ONLINE_TIME] = 3600.0
    stat_data[statistic.TOTAL_MSG_CNT] = 10
    stat_data[statistic.TOTAL_REPLY_CNT] = 2
    stat_data[statistic.REQ_CNT_BY_MODEL]["model-a"] = 2
    stat_data[statistic.IN_TOK_BY_MODEL]["model-a"] = 20
    stat_data[statistic.OUT_TOK_BY_MODEL]["model-a"] = 10
    stat_data[statistic.TOTAL_TOK_BY_MODEL]["model-a"] = 30
    stat_data[statistic.CACHE_HIT_TOK] = 15
    stat_data[statistic.CACHE_MISS_TOK] = 5
    stat_data[statistic.CACHE_HIT_TOK_BY_MODEL]["model-a"] = 15
    stat_data[statistic.CACHE_MISS_TOK_BY_MODEL]["model-a"] = 5
    stat_data[statistic.COST_BY_MODEL]["model-a"] = 0.3
    stat_data[statistic.AVG_TIME_COST_BY_MODEL]["model-a"] = 1.5
    stat_data[statistic.STD_TIME_COST_BY_MODEL]["model-a"] = 0.5
    stat_data[statistic.REQ_CNT_BY_MODULE]["replyer"] = 2
    stat_data[statistic.IN_TOK_BY_MODULE]["replyer"] = 20
    stat_data[statistic.OUT_TOK_BY_MODULE]["replyer"] = 10
    stat_data[statistic.TOTAL_TOK_BY_MODULE]["replyer"] = 30
    stat_data[statistic.COST_BY_MODULE]["replyer"] = 0.3
    stat_data[statistic.REQ_CNT_BY_TYPE]["replyer.chat"] = 2
    stat_data[statistic.IN_TOK_BY_TYPE]["replyer.chat"] = 20
    stat_data[statistic.OUT_TOK_BY_TYPE]["replyer.chat"] = 10
    stat_data[statistic.TOTAL_TOK_BY_TYPE]["replyer.chat"] = 30
    stat_data[statistic.COST_BY_TYPE]["replyer.chat"] = 0.3
    stat_data[statistic.MSG_CNT_BY_CHAT]["g100"] = 10
    stat_data[statistic.COST_BY_CHAT]["session-a"] = 0.3

    chart_data = {
        "24h": {
            "time_labels": ["12:00"],
            "total_cost_data": [0.3],
            "cost_by_model": {"model-a": [0.3]},
            "cost_by_module": {"replyer": [0.3]},
            "message_by_chat": {"测试群聊": [10]},
        }
    }
    metrics_data = {
        "7d": {
            "time_labels": ["07-01"],
            "cost_per_100_messages": [3.0],
            "cost_per_hour": [0.3],
            "tokens_per_hour": [30.0],
            "cost_per_100_replies": [15.0],
        }
    }

    snapshot = task._build_detailed_statistics_snapshot(
        {"all_time": stat_data},
        now,
        chart_data,
        metrics_data,
    )
    monkeypatch.setattr(statistics_service, "_detailed_statistics_snapshot", None)
    statistics_service.store_detailed_statistics_snapshot(snapshot)

    stored_snapshot = statistics_service.get_detailed_statistics_snapshot()
    assert stored_snapshot is snapshot
    assert snapshot.periods[0].summary.cache_hit_rate == 0.75
    assert snapshot.periods[0].summary.cost_per_100_messages == 3.0
    assert snapshot.periods[0].models[0].avg_calls_per_reply == 1.0
    assert snapshot.periods[0].models[0].avg_tokens_per_call == 15.0
    assert snapshot.periods[0].distributions.chat_messages[0].name == "测试群聊"
    assert snapshot.trends["24h"].message_by_chat == {"测试群聊": [10]}
    assert snapshot.metrics["7d"].cost_per_100_replies == [15.0]
