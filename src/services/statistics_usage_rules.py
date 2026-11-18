"""模型调用的统计口径：逐条统计与小时汇总共用的归一化规则。

这里不导入数据库，统计任务导入时不会连接数据库。
"""

from datetime import datetime, timedelta
from typing import Any, Mapping, NamedTuple

UNRECORDED_TASK_NAME = "未记录"


class UsageKey(NamedTuple):
    """一条调用在统计里的全部维度，已归一化。"""

    request_type: str
    provider_name: str
    model_name: str
    task_name: str
    session_id: str


# 次数、输入、输出、缓存命中、缓存未命中、花费、有效耗时条数、耗时和、耗时平方和、
# 非空耗时条数、非空耗时和（首页「平均响应时间」沿用 SQL AVG 口径：含 0 与负数，只忽略空值）
UsageMetrics = tuple[int, int, int, int, int, float, int, float, float, int, float]

METRIC_COLUMNS = (
    "request_count", "prompt_tokens", "completion_tokens", "cache_hit_tokens", "cache_miss_tokens", "cost",
    "duration_count", "duration_sum", "duration_sum_sq", "response_time_count", "response_time_sum",
)


def normalize_prompt_cache_tokens(prompt_tokens: int, hit_tokens: int, miss_tokens: int) -> tuple[int, int]:
    """将 provider 返回的 prompt cache token 统计规范化为可聚合值。"""

    normalized_hit_tokens = max(hit_tokens, 0)
    normalized_miss_tokens = max(miss_tokens, 0)
    if normalized_miss_tokens == 0 and normalized_hit_tokens > 0:
        normalized_miss_tokens = max(prompt_tokens - normalized_hit_tokens, 0)
    if normalized_hit_tokens + normalized_miss_tokens == 0 and prompt_tokens > 0:
        normalized_miss_tokens = prompt_tokens
    return normalized_hit_tokens, normalized_miss_tokens


def normalize_usage_record(record: Mapping[str, Any]) -> tuple[UsageKey, UsageMetrics]:
    """逐条统计与小时汇总共用的唯一归一化规则。"""

    prompt_tokens = record.get("prompt_tokens") or 0
    completion_tokens = record.get("completion_tokens") or 0
    hit_tokens = miss_tokens = 0
    if record.get("prompt_cache_enabled"):
        hit_tokens, miss_tokens = normalize_prompt_cache_tokens(
            prompt_tokens,
            record.get("prompt_cache_hit_tokens") or 0,
            record.get("prompt_cache_miss_tokens") or 0,
        )
    raw_time_cost = record.get("time_cost")
    time_cost = raw_time_cost or 0.0
    valid_duration = time_cost > 0
    key = UsageKey(
        request_type=record.get("request_type") or "unknown",
        provider_name=record.get("model_api_provider_name") or "unknown",
        model_name=record.get("model_assign_name") or record.get("model_name") or "unknown",
        # 历史记录可能没有任务名，单独归为未记录，不根据请求前缀或当前配置推断。
        task_name=record.get("task_name") or UNRECORDED_TASK_NAME,
        session_id=str(record.get("session_id") or "").strip(),
    )
    metrics: UsageMetrics = (
        1, prompt_tokens, completion_tokens, hit_tokens, miss_tokens, record.get("cost") or 0.0,
        int(valid_duration),
        time_cost if valid_duration else 0.0,
        time_cost * time_cost if valid_duration else 0.0,
        int(raw_time_cost is not None),
        raw_time_cost if raw_time_cost is not None else 0.0,
    )
    return key, metrics


def bucket_start(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def ceil_hour(value: datetime) -> datetime:
    first = bucket_start(value)
    return first if first == value else first + timedelta(hours=1)


def clamp_hour_range(start: datetime, now: datetime) -> tuple[datetime, datetime] | None:
    """时间段 [start, now] 中可以直接用小时汇总的整点区间 [ceil(start), floor(now))；不足一小时返回 None。"""

    first = ceil_hour(start)
    last = bucket_start(now)
    return (first, last) if first < last else None
