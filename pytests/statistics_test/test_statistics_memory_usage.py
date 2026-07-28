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


def _patch_statistics_database(monkeypatch, tmp_path, records: list[ModelUsage]) -> None:
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
