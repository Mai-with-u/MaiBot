"""模型任务的近期调用统计；成功和失败使用相同的实际 Provider 调用口径。"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any, AsyncIterator, Dict, List

import asyncio

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import col, delete, select

from src.common.database.database import get_db_session
from src.common.database.database_model import ModelTaskUsage
from src.common.logger import get_logger

logger = get_logger("model_task_usage")

# 分钟汇总只保留最近七天，详细统计据此标注失败率的实际覆盖范围。
MODEL_TASK_USAGE_RETENTION = timedelta(days=7)


def record_model_task_usage(
    task_name: str,
    model_name: str,
    model_identifier: str,
    provider_name: str,
    duration: float,
    succeeded: bool,
    recorded_at: datetime,
) -> None:
    """原子累加分钟桶并清理七天前的汇总，存储量不随调用次数增长。"""
    bucket_time = recorded_at.replace(second=0, microsecond=0)
    statement = insert(ModelTaskUsage).values(
        bucket_time=bucket_time,
        task_name=task_name,
        model_name=model_name,
        model_identifier=model_identifier,
        provider_name=provider_name,
        request_count=1,
        failure_count=0 if succeeded else 1,
        success_duration=duration if succeeded else 0.0,
    )
    statement = statement.on_conflict_do_update(
        index_elements=["bucket_time", "task_name", "model_name", "model_identifier", "provider_name"],
        set_={
            "request_count": ModelTaskUsage.request_count + 1,
            "failure_count": ModelTaskUsage.failure_count + statement.excluded.failure_count,
            "success_duration": ModelTaskUsage.success_duration + statement.excluded.success_duration,
        },
    )
    with get_db_session() as session:
        session.exec(statement)
        session.exec(
            delete(ModelTaskUsage).where(col(ModelTaskUsage.bucket_time) < bucket_time - MODEL_TASK_USAGE_RETENTION)
        )


@asynccontextmanager
async def track_model_task_usage(
    task_name: str, model_name: str, model_identifier: str, provider_name: str,
) -> AsyncIterator[None]:
    """只包裹实际调用，耗时不包含重试等待；超时取消也计入失败尝试。"""
    started = perf_counter()
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        duration = max(0.0, perf_counter() - started)
        try:
            # shield 保证硬超时取消后仍可落下统计；数据库写入不占用事件循环。
            await asyncio.shield(asyncio.to_thread(
                record_model_task_usage,
                task_name, model_name, model_identifier, provider_name,
                duration, succeeded, datetime.now(),
            ))
        except Exception:
            # 统计存储故障完整记录，但不能将已经成功的模型调用变成失败。
            logger.exception("记录模型任务调用统计失败")


def get_model_task_usage(task_name: str, hours: int = 24) -> Dict[str, Any]:
    """数据库中按模型、提供商聚合，返回成功调用平均耗时与全部尝试的失败率。"""
    end = datetime.now()
    start = (end - timedelta(hours=hours)).replace(second=0, microsecond=0)
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            select(
                ModelTaskUsage.model_name,
                ModelTaskUsage.model_identifier,
                ModelTaskUsage.provider_name,
                func.sum(ModelTaskUsage.request_count),
                func.sum(ModelTaskUsage.failure_count),
                func.sum(ModelTaskUsage.success_duration),
            )
            .where(
                ModelTaskUsage.task_name == task_name,
                ModelTaskUsage.bucket_time >= start,
                ModelTaskUsage.bucket_time <= end,
            )
            .group_by(ModelTaskUsage.model_name, ModelTaskUsage.model_identifier, ModelTaskUsage.provider_name)
            .order_by(func.sum(ModelTaskUsage.request_count).desc(), ModelTaskUsage.model_name)
        ).all()
    models = []
    for name, identifier, provider, count, failures, duration in rows:
        successes = count - failures
        models.append({
            "model_name": name,
            "model_identifier": identifier,
            "provider_name": provider,
            "request_count": count,
            "failure_count": failures,
            "avg_duration": duration / successes if successes else None,
            "failure_rate": failures / count,
        })
    return {
        "task_name": task_name,
        "hours": hours,
        "request_count": sum(model["request_count"] for model in models),
        "models": models,
    }


def fetch_model_task_usage_groups(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """按模型、供应商、任务聚合时间范围内的调用尝试，供详细统计计算失败率。"""
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            select(
                ModelTaskUsage.model_name,
                ModelTaskUsage.provider_name,
                ModelTaskUsage.task_name,
                func.sum(ModelTaskUsage.request_count),
                func.sum(ModelTaskUsage.failure_count),
                func.sum(ModelTaskUsage.success_duration),
            )
            .where(ModelTaskUsage.bucket_time >= start, ModelTaskUsage.bucket_time <= end)
            .group_by(ModelTaskUsage.model_name, ModelTaskUsage.provider_name, ModelTaskUsage.task_name)
        ).all()
    return [
        {
            "model_name": model_name,
            "provider_name": provider_name,
            "task_name": task_name,
            "attempt_count": int(attempts or 0),
            "failure_count": int(failures or 0),
            "success_duration": float(duration or 0.0),
        }
        for model_name, provider_name, task_name, attempts, failures, duration in rows
    ]


def fetch_model_task_usage_minutes(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """按分钟桶汇总调用尝试与失败次数，供详细统计绘制失败时间线。"""
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            select(
                ModelTaskUsage.bucket_time,
                func.sum(ModelTaskUsage.request_count),
                func.sum(ModelTaskUsage.failure_count),
            )
            .where(ModelTaskUsage.bucket_time >= start, ModelTaskUsage.bucket_time <= end)
            .group_by(ModelTaskUsage.bucket_time)
        ).all()
    return [
        {"bucket_time": bucket_time, "attempt_count": int(attempts or 0), "failure_count": int(failures or 0)}
        for bucket_time, attempts, failures in rows
    ]
