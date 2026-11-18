from datetime import datetime, timedelta

import pytest

from src.chat.utils import statistic
from src.chat.utils.statistics_timeseries import PeriodTrendCollector, PeriodTrendWindow, collect_time_series
from src.services.statistics_service import StatisticsMessageRow


def usage(timestamp, cost, tokens, model="m", request_type="module.reply"):
    return {
        "timestamp": timestamp, "cost": cost, "prompt_tokens": tokens, "completion_tokens": 0,
        "model_assign_name": model, "model_name": "raw", "request_type": request_type,
    }


def test_period_trend_aligns_buckets_heatmap_and_failures():
    now = datetime(2026, 11, 2, 12)  # 星期一
    collector = PeriodTrendCollector({
        "last_hour": PeriodTrendWindow(now - timedelta(hours=1), now, 5),
        "last_15_minutes": PeriodTrendWindow(now - timedelta(minutes=15), now, 1),
    })
    records = [
        usage(now - timedelta(minutes=50), 1.0, 10, model="a"),
        usage(now, 2.0, 20, model="b", request_type="other"),  # 恰好落在结束时刻，计入末桶。
        usage(now - timedelta(hours=2), 9.0, 90),  # 超出两个时段。
    ]
    messages = [
        StatisticsMessageRow(now - timedelta(minutes=3), "qq", "u1", "g1", "群一", None),
        StatisticsMessageRow(now - timedelta(minutes=3), "qq", "", None, None, None),  # 无聊天归属，不计入。
    ]
    collect_time_series(now, now, records, messages, [], lambda *_: False, collector)
    collector.add_task_usage(now - timedelta(minutes=2), 10, 3)
    collector.add_task_usage(now - timedelta(hours=3), 99, 99)
    result = collector.result()

    hour = result["last_hour"]
    assert len(hour["time_labels"]) == 12
    assert hour["costs"][2] == 1.0  # 距起点 10 分钟，落在第 3 个 5 分钟桶。
    assert hour["costs"][-1] == 2.0
    assert hour["requests"] == [0, 0, 1] + [0] * 8 + [1]
    assert hour["cost_by_model"]["b"][-1] == 2.0
    assert hour["tokens_by_model"]["a"][2] == 10
    assert hour["cost_by_module"]["other"][-1] == 2.0
    assert hour["messages"][-1] == 1
    assert hour["message_by_chat"] == {"群一": [0] * 11 + [1]}
    assert hour["attempts"][-1] == 10 and hour["failures"][-1] == 3
    assert hour["heatmap_costs"][0][11] == 1.0 and hour["heatmap_costs"][0][12] == 2.0
    assert hour["heatmap_messages"][0][11] == 1

    quarter = result["last_15_minutes"]
    assert len(quarter["time_labels"]) == 15
    assert sum(quarter["costs"]) == 2.0
    assert quarter["failures"][-2] == 3  # 距终点 2 分钟，落在倒数第 2 个 1 分钟桶。


def test_reliability_merges_task_usage_by_kind_and_marks_truncation(monkeypatch):
    now = datetime(2026, 11, 5, 12)
    rows = [
        {"model_name": "m", "provider_name": "p1", "task_name": "replyer",
         "attempt_count": 10, "failure_count": 2, "success_duration": 16.0},
        {"model_name": "m", "provider_name": "p2", "task_name": "embedding",
         "attempt_count": 5, "failure_count": 0, "success_duration": 5.0},
    ]
    captured = {}

    def fake_groups(start, end):
        captured["start"] = start
        return rows

    monkeypatch.setattr(statistic, "fetch_model_task_usage_groups", fake_groups)
    reliability, model_lookup, task_lookup = statistic.StatisticOutputTask._build_reliability(
        now - timedelta(days=30), now,
    )

    assert reliability.truncated
    assert captured["start"] == now - timedelta(days=7)
    assert reliability.attempt_count == 15
    assert reliability.failure_rate == pytest.approx(2 / 15)
    assert reliability.avg_success_duration == pytest.approx(21 / 13)
    assert [item.name for item in reliability.by_provider] == ["p1", "p2"]
    assert reliability.by_model[0].failure_rate == pytest.approx(2 / 15)
    assert model_lookup == {("generation", "m"): (10, 2), ("embedding", "m"): (5, 0)}
    assert task_lookup[("generation", "replyer")] == (10, 2)

    short, _, _ = statistic.StatisticOutputTask._build_reliability(now - timedelta(hours=1), now)
    assert not short.truncated
    assert short.coverage_start == (now - timedelta(hours=1)).isoformat()


def test_breakdown_rows_attach_failures_only_when_matched(monkeypatch):
    now = datetime(2026, 11, 5, 12)
    record = {
        "timestamp": now, "task_name": "replyer", "request_type": "replyer.generate",
        "model_api_provider_name": "p", "model_assign_name": "m", "model_name": "raw",
        "prompt_tokens": 10, "completion_tokens": 1, "prompt_cache_enabled": False, "cost": 0.1, "time_cost": 1.0,
    }
    monkeypatch.setattr(statistic, "fetch_model_usage_since", lambda start: iter([record]))
    stats = statistic.StatisticOutputTask._collect_model_request_for_period([("all_time", now - timedelta(days=1))])
    matched = statistic.StatisticOutputTask._build_breakdown_rows(
        stats["all_time"], "model", {("generation", "m"): (12, 2)},
    )[0]
    assert (matched.attempt_count, matched.failure_count) == (12, 2)
    unmatched = statistic.StatisticOutputTask._build_breakdown_rows(stats["all_time"], "module")[0]
    assert unmatched.attempt_count is None and unmatched.failure_count is None


def test_detailed_summary_shares_derived_metrics():
    summary = statistic.StatisticOutputTask._build_detailed_summary(
        total_cost=2.0, total_requests=4, input_tokens=300, output_tokens=100,
        cache_hit_tokens=30, cache_miss_tokens=10, online_time=7200.0, total_messages=50, total_replies=10,
    )
    assert summary.total_tokens == 400
    assert summary.cache_hit_rate == pytest.approx(0.75)
    assert summary.cost_per_100_messages == pytest.approx(4.0)
    assert summary.cost_per_100_messages_excluding_replies == pytest.approx(5.0)
    assert summary.cost_per_100_replies == pytest.approx(20.0)
    assert summary.cost_per_hour == pytest.approx(1.0)
    assert summary.tokens_per_hour == pytest.approx(200.0)
