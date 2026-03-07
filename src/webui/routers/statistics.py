"""统计数据 API 路由"""

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import Messages, ModelUsage, OnlineTime
from src.common.logger import get_logger
from src.webui.core import verify_auth_token_from_cookie_or_header

logger = get_logger("webui.statistics")

router = APIRouter(prefix="/statistics", tags=["statistics"])


def require_auth(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> bool:
    """认证依赖：验证用户是否已登录"""
    return verify_auth_token_from_cookie_or_header(maibot_session, authorization)


class StatisticsSummary(BaseModel):
    """统计数据摘要"""

    total_requests: int = Field(0, description="总请求数")
    total_cost: float = Field(0.0, description="总花费")
    total_tokens: int = Field(0, description="总token数")
    online_time: float = Field(0.0, description="在线时间（秒）")
    total_messages: int = Field(0, description="总消息数")
    total_replies: int = Field(0, description="总回复数")
    avg_response_time: float = Field(0.0, description="平均响应时间")
    cost_per_hour: float = Field(0.0, description="每小时花费")
    tokens_per_hour: float = Field(0.0, description="每小时token数")


class ModelStatistics(BaseModel):
    """模型统计"""

    model_name: str
    request_count: int
    total_cost: float
    total_tokens: int
    avg_response_time: float


class TimeSeriesData(BaseModel):
    """时间序列数据"""

    timestamp: str
    requests: int = 0
    cost: float = 0.0
    tokens: int = 0


class DashboardData(BaseModel):
    """仪表盘数据"""

    summary: StatisticsSummary
    model_stats: list[ModelStatistics]
    hourly_data: list[TimeSeriesData]
    daily_data: list[TimeSeriesData]
    recent_activity: list[dict[str, Any]]


@router.get("/dashboard", response_model=DashboardData)
async def get_dashboard_data(hours: int = 24, _auth: bool = Depends(require_auth)):
    """
    获取仪表盘统计数据

    Args:
        hours: 统计时间范围（小时），默认24小时

    Returns:
        仪表盘数据
    """
    try:
        now = datetime.now()
        start_time = now - timedelta(hours=hours)

        # 获取摘要数据
        summary = await _get_summary_statistics(start_time, now)

        # 获取模型统计
        model_stats = await _get_model_statistics(start_time)

        # 获取小时级时间序列数据
        hourly_data = await _get_hourly_statistics(start_time, now)

        # 获取日级时间序列数据（最近7天）
        daily_start = now - timedelta(days=7)
        daily_data = await _get_daily_statistics(daily_start, now)

        # 获取最近活动
        recent_activity = await _get_recent_activity(limit=10)

        return DashboardData(
            summary=summary,
            model_stats=model_stats,
            hourly_data=hourly_data,
            daily_data=daily_data,
            recent_activity=recent_activity,
        )
    except Exception as e:
        logger.error(f"获取仪表盘数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}") from e


async def _get_summary_statistics(start_time: datetime, end_time: datetime) -> StatisticsSummary:
    """获取摘要统计数据（优化：使用数据库聚合）"""
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

    # 使用聚合查询替代全量加载
    with get_db_session() as session:
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

    # 查询在线时间 - 这个数据量通常不大，保留原逻辑
    with get_db_session() as session:
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

    # 查询消息数量 - 使用聚合优化
    with get_db_session() as session:
        statement = select(func.count()).where(
            col(Messages.timestamp) >= start_time,
            col(Messages.timestamp) <= end_time,
        )
        total_messages = session.exec(statement).one()
    summary.total_messages = int(total_messages or 0)

    # 统计回复数量
    with get_db_session() as session:
        statement = select(func.count()).where(
            col(Messages.timestamp) >= start_time,
            col(Messages.timestamp) <= end_time,
            col(Messages.reply_to).is_not(None),
        )
        total_replies = session.exec(statement).one()
    summary.total_replies = int(total_replies or 0)

    # 计算派生指标
    if summary.online_time > 0:
        online_hours = summary.online_time / 3600.0
        summary.cost_per_hour = summary.total_cost / online_hours
        summary.tokens_per_hour = summary.total_tokens / online_hours

    return summary


async def _get_model_statistics(start_time: datetime) -> list[ModelStatistics]:
    """获取模型统计数据（优化：使用数据库聚合和分组）"""
    # 使用GROUP BY聚合，避免全量加载
    statement = (
        select(ModelUsage)
        .where(col(ModelUsage.timestamp) >= start_time)
        .order_by(desc(col(ModelUsage.timestamp)))
        .limit(200)
    )

    with get_db_session() as session:
        rows = session.exec(statement).all()

        aggregates: dict[str, dict[str, float | int]] = {}
        for record in rows:
            model_name = record.model_assign_name or record.model_name or "unknown"
            if model_name not in aggregates:
                aggregates[model_name] = {
                    "request_count": 0,
                    "total_cost": 0.0,
                    "total_tokens": 0,
                    "total_time_cost": 0.0,
                    "time_cost_count": 0,
                }
            bucket = aggregates[model_name]
            bucket["request_count"] = int(bucket["request_count"]) + 1
            bucket["total_cost"] = float(bucket["total_cost"]) + float(record.cost or 0.0)
            bucket["total_tokens"] = int(bucket["total_tokens"]) + int(record.total_tokens or 0)
            if record.time_cost:
                bucket["total_time_cost"] = float(bucket["total_time_cost"]) + float(record.time_cost)
                bucket["time_cost_count"] = int(bucket["time_cost_count"]) + 1

    result: list[ModelStatistics] = []
    for model_name, bucket in sorted(
        aggregates.items(),
        key=lambda item: float(item[1]["request_count"]),
        reverse=True,
    )[:10]:
        time_cost_count = int(bucket["time_cost_count"])
        avg_time_cost = float(bucket["total_time_cost"]) / time_cost_count if time_cost_count > 0 else 0.0
        result.append(
            ModelStatistics(
                model_name=model_name,
                request_count=int(bucket["request_count"]),
                total_cost=float(bucket["total_cost"]),
                total_tokens=int(bucket["total_tokens"]),
                avg_response_time=avg_time_cost,
            )
        )

    return result


async def _get_hourly_statistics(start_time: datetime, end_time: datetime) -> list[TimeSeriesData]:
    """获取小时级统计数据（优化：使用数据库聚合）"""
    # SQLite的日期时间函数进行小时分组
    # 使用strftime将timestamp格式化为小时级别
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

    with get_db_session() as session:
        rows = session.exec(statement).all()

    # 转换为字典以快速查找
    data_dict = {row[0]: row for row in rows}

    # 填充所有小时（包括没有数据的）
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


async def _get_daily_statistics(start_time: datetime, end_time: datetime) -> list[TimeSeriesData]:
    """获取日级统计数据（优化：使用数据库聚合）"""
    # 使用strftime按日期分组
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

    with get_db_session() as session:
        rows = session.exec(statement).all()

    # 转换为字典
    data_dict = {row[0]: row for row in rows}

    # 填充所有天
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


async def _get_recent_activity(limit: int = 10) -> list[dict[str, Any]]:
    """获取最近活动"""
    with get_db_session() as session:
        statement = select(ModelUsage).order_by(desc(col(ModelUsage.timestamp))).limit(limit)
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


@router.get("/summary")
async def get_summary(hours: int = 24, _auth: bool = Depends(require_auth)):
    """
    获取统计摘要

    Args:
        hours: 统计时间范围（小时）
    """
    try:
        now = datetime.now()
        start_time = now - timedelta(hours=hours)
        summary = await _get_summary_statistics(start_time, now)
        return summary
    except Exception as e:
        logger.error(f"获取统计摘要失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/models")
async def get_model_stats(hours: int = 24, _auth: bool = Depends(require_auth)):
    """
    获取模型统计

    Args:
        hours: 统计时间范围（小时）
    """
    try:
        now = datetime.now()
        start_time = now - timedelta(hours=hours)
        stats = await _get_model_statistics(start_time)
        return stats
    except Exception as e:
        logger.error(f"获取模型统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
