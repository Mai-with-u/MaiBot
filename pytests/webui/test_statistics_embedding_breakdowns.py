from datetime import datetime, timedelta

import pytest

from src.chat.utils import statistic


def usage(timestamp, task_name, tokens, cost, duration):
    return {
        "timestamp": timestamp,
        "task_name": task_name,
        "request_type": "shared.request",
        "model_api_provider_name": "provider",
        "model_assign_name": "shared-model",
        "model_name": "shared-model",
        "prompt_tokens": tokens,
        "completion_tokens": 10 if task_name == "replyer" else 0,
        "prompt_cache_enabled": task_name == "replyer",
        "prompt_cache_hit_tokens": 50 if task_name == "replyer" else 0,
        "cost": cost,
        "time_cost": duration,
    }


def test_shared_names_are_split_without_changing_totals(monkeypatch):
    now = datetime(2026, 10, 27, 12)
    records = [
        usage(now, "replyer", 100, 0.3, 5),
        usage(now, "embedding", 20, 0.1, 1),
        usage(now, "image_embedding", 40, 0.2, 3),
        usage(now - timedelta(hours=2), "embedding", 10, 0.05, 0),
    ]
    monkeypatch.setattr(statistic, "fetch_model_usage_since", lambda start: iter(records))
    periods = statistic.StatisticOutputTask._collect_model_request_for_period([
        ("all_time", now - timedelta(days=1)), ("last_hour", now - timedelta(hours=1)),
    ])

    for dimension in ("model", "module", "request_type"):
        normal, embedding = statistic.StatisticOutputTask._build_breakdown_rows(periods["last_hour"], dimension)
        assert not normal.is_embedding
        assert embedding.is_embedding
        assert normal.name == embedding.name
        assert normal.total_tokens == 110
        assert normal.cache_hit_tokens == 50
        assert embedding.request_count == 2
        assert embedding.input_tokens == 60
        assert embedding.total_cost == pytest.approx(0.3)
        assert embedding.avg_time_cost == 2
        assert embedding.std_time_cost == 1
        assert sum(row.total_tokens for row in (normal, embedding)) == sum(
            periods["last_hour"][statistic.TOTAL_TOK_BY_MODEL].values()
        )

    tasks = statistic.StatisticOutputTask._build_breakdown_rows(periods["last_hour"], "task")
    assert {row.name: row.is_embedding for row in tasks} == {
        "replyer": False, "embedding": True, "image_embedding": True,
    }
    all_embeddings = statistic.StatisticOutputTask._build_breakdown_rows(periods["all_time"], "model")[1]
    assert all_embeddings.request_count == 3
    assert all_embeddings.input_tokens == 70
    assert all_embeddings.avg_time_cost == 2  # 无有效耗时的请求仍计入调用次数，但不计入耗时统计。
    assert all_embeddings.std_time_cost == 1


class _MemoryCache:
    """替代统计缓存文件，只在内存里保存。"""

    def __init__(self):
        self.value = None

    def load(self):
        return self.value

    def save(self, value):
        self.value = value
        return True


def test_incremental_cache_preserves_split_duration_statistics(monkeypatch):
    now = datetime(2026, 10, 27, 12)
    start = now - timedelta(days=1)
    records = [usage(now - timedelta(minutes=1), "embedding", 20, 0.1, 1)]
    cache = _MemoryCache()
    monkeypatch.setattr(statistic, "full_statistics_cache", cache)
    monkeypatch.setattr(statistic, "fetch_model_usage_since", lambda since: (
        record for record in records if record["timestamp"] >= since
    ))
    task = object.__new__(statistic.StatisticOutputTask)
    task.stat_period = [("all_time", now - start)]
    task.all_time_start_time = start
    task.name_mapping = {}
    monkeypatch.setattr(task, "_collect_online_time_for_period", lambda periods, now: {key: {} for key, _ in periods})
    monkeypatch.setattr(task, "_collect_message_count_for_period", lambda periods: {key: {} for key, _ in periods})
    monkeypatch.setattr(task, "_refresh_all_time_duration_stats", lambda data: None)
    task._collect_all_statistics(now)

    records.extend([
        usage(now + timedelta(seconds=1), "embedding", 40, 0.2, 3),
        usage(now + timedelta(seconds=2), "replyer", 100, 0.3, 5),
    ])
    stats = task._collect_all_statistics(now + timedelta(minutes=1))
    normal, embedding = task._build_breakdown_rows(stats["all_time"], "module")
    assert normal.request_count == 1
    assert embedding.request_count == 2
    assert embedding.input_tokens == 60
    assert embedding.avg_time_cost == 2
    assert embedding.std_time_cost == 1

    # 旧缓存没有分类明细时必须重收历史，不能只统计升级后的新请求。
    del cache.value["stat_data"]["classified_requests"]
    task.stat_period = [("all_time", now + timedelta(minutes=2) - start)]
    rebuilt = task._collect_all_statistics(now + timedelta(minutes=2))
    assert task._build_breakdown_rows(rebuilt["all_time"], "module") == [normal, embedding]


def test_missing_task_name_is_not_inferred_from_model_name(monkeypatch):
    now = datetime(2026, 10, 27, 12)
    record = usage(now, None, 20, 0.1, 1)
    record["model_assign_name"] = "embedding-model"
    monkeypatch.setattr(statistic, "fetch_model_usage_since", lambda start: iter([record]))
    stats = statistic.StatisticOutputTask._collect_model_request_for_period([("all_time", now)])
    row = statistic.StatisticOutputTask._build_breakdown_rows(stats["all_time"], "task")[0]
    assert row.name == "未记录"
    assert not row.is_embedding
