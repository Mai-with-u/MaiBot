"""麦麦 2025 年度总结 API 路由"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    ActionRecord,
    Expression,
    Images,
    Jargon,
    Messages,
    ModelUsage,
    OnlineTime,
    PersonInfo,
)
from src.common.logger import get_logger
from src.webui.core import verify_auth_token_from_cookie_or_header

logger = get_logger("webui.annual_report")

router = APIRouter(prefix="/annual-report", tags=["annual-report"])


def require_auth(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> bool:
    """认证依赖：验证用户是否已登录"""
    return verify_auth_token_from_cookie_or_header(maibot_session, authorization)


# ==================== Pydantic 模型定义 ====================


class TimeFootprintData(BaseModel):
    """时光足迹数据"""

    total_online_hours: float = Field(0.0, description="年度在线总时长(小时)")
    first_message_time: Optional[str] = Field(None, description="初次消息时间")
    first_message_user: Optional[str] = Field(None, description="初次消息用户昵称")
    first_message_content: Optional[str] = Field(None, description="初次消息内容(截断)")
    busiest_day: Optional[str] = Field(None, description="最忙碌的一天")
    busiest_day_count: int = Field(0, description="最忙碌那天的消息数")
    hourly_distribution: list[int] = Field(default_factory=lambda: [0] * 24, description="24小时活跃分布")
    midnight_chat_count: int = Field(0, description="深夜(0-4点)互动次数")
    is_night_owl: bool = Field(False, description="是否是夜猫子")


class SocialNetworkData(BaseModel):
    """社交网络数据"""

    total_groups: int = Field(0, description="加入的群组总数")
    top_groups: list[dict[str, Any]] = Field(default_factory=list, description="话痨群组TOP5")
    top_users: list[dict[str, Any]] = Field(default_factory=list, description="互动最多的用户TOP5")
    at_count: int = Field(0, description="被@次数")
    mentioned_count: int = Field(0, description="被提及次数")
    longest_companion_user: Optional[str] = Field(None, description="最长情陪伴的用户")
    longest_companion_days: int = Field(0, description="陪伴天数")


class BrainPowerData(BaseModel):
    """最强大脑数据"""

    total_tokens: int = Field(0, description="年度消耗Token总量")
    total_cost: float = Field(0.0, description="年度总花费")
    favorite_model: Optional[str] = Field(None, description="最爱用的模型")
    favorite_model_count: int = Field(0, description="最爱模型的调用次数")
    model_distribution: list[dict[str, Any]] = Field(default_factory=list, description="模型使用分布")
    top_reply_models: list[dict[str, Any]] = Field(default_factory=list, description="最喜欢的回复模型TOP5")
    most_expensive_cost: float = Field(0.0, description="最昂贵的一次思考花费")
    most_expensive_time: Optional[str] = Field(None, description="最昂贵思考的时间")
    top_token_consumers: list[dict[str, Any]] = Field(default_factory=list, description="烧钱大户TOP3")
    silence_rate: float = Field(0.0, description="高冷指数(沉默率)")
    total_actions: int = Field(0, description="总动作数")
    no_reply_count: int = Field(0, description="选择沉默的次数")
    avg_interest_value: float = Field(0.0, description="平均兴趣值")
    max_interest_value: float = Field(0.0, description="最高兴趣值")
    max_interest_time: Optional[str] = Field(None, description="最高兴趣值时间")
    avg_reasoning_length: float = Field(0.0, description="平均思考长度")
    max_reasoning_length: int = Field(0, description="最长思考长度")
    max_reasoning_time: Optional[str] = Field(None, description="最长思考的时间")


class ExpressionVibeData(BaseModel):
    """个性与表达数据"""

    top_emoji: Optional[dict[str, Any]] = Field(None, description="表情包之王")
    top_emojis: list[dict[str, Any]] = Field(default_factory=list, description="TOP3表情包")
    top_expressions: list[dict[str, Any]] = Field(default_factory=list, description="印象最深刻的表达风格")
    rejected_expression_count: int = Field(0, description="被拒绝的表达次数")
    checked_expression_count: int = Field(0, description="已检查的表达次数")
    total_expressions: int = Field(0, description="表达总数")
    action_types: list[dict[str, Any]] = Field(default_factory=list, description="动作类型分布")
    image_processed_count: int = Field(0, description="处理的图片数量")
    late_night_reply: Optional[dict[str, Any]] = Field(None, description="深夜还在回复")
    favorite_reply: Optional[dict[str, Any]] = Field(None, description="最喜欢的回复")


class AchievementData(BaseModel):
    """趣味成就数据"""

    new_jargon_count: int = Field(0, description="新学到的黑话数量")
    sample_jargons: list[dict[str, Any]] = Field(default_factory=list, description="代表性黑话示例")
    total_messages: int = Field(0, description="总消息数")
    total_replies: int = Field(0, description="总回复数")


class AnnualReportData(BaseModel):
    """年度报告完整数据"""

    year: int = Field(2025, description="报告年份")
    bot_name: str = Field("麦麦", description="Bot名称")
    generated_at: str = Field(..., description="报告生成时间")
    time_footprint: TimeFootprintData = Field(default_factory=lambda: TimeFootprintData.model_construct())
    social_network: SocialNetworkData = Field(default_factory=lambda: SocialNetworkData.model_construct())
    brain_power: BrainPowerData = Field(default_factory=lambda: BrainPowerData.model_construct())
    expression_vibe: ExpressionVibeData = Field(default_factory=lambda: ExpressionVibeData.model_construct())
    achievements: AchievementData = Field(default_factory=lambda: AchievementData.model_construct())


# ==================== 辅助函数 ====================


def get_year_time_range(year: int = 2025) -> tuple[float, float]:
    """获取指定年份的时间戳范围"""
    start = datetime(year, 1, 1, 0, 0, 0).timestamp()
    end = datetime(year, 12, 31, 23, 59, 59).timestamp()
    return start, end


def get_year_datetime_range(year: int = 2025) -> tuple[datetime, datetime]:
    """获取指定年份的 datetime 范围"""
    start = datetime(year, 1, 1, 0, 0, 0)
    end = datetime(year, 12, 31, 23, 59, 59)
    return start, end


# ==================== 维度一：时光足迹 ====================


async def get_time_footprint(year: int = 2025) -> TimeFootprintData:
    """获取时光足迹数据"""
    data = TimeFootprintData.model_construct()
    start_ts, end_ts = get_year_time_range(year)
    start_dt, end_dt = get_year_datetime_range(year)

    try:
        # 1. 年度在线时长
        with get_db_session() as session:
            statement = select(OnlineTime).where(
                col(OnlineTime.start_timestamp) >= start_dt,
                col(OnlineTime.end_timestamp) <= end_dt,
            )
            online_records = session.exec(statement).all()
        total_seconds = 0
        for record in online_records:
            try:
                start = max(record.start_timestamp, start_dt)
                end = min(record.end_timestamp, end_dt)
                if end > start:
                    total_seconds += (end - start).total_seconds()
            except Exception:
                continue
        data.total_online_hours = round(total_seconds / 3600, 2)

        # 2. 初次相遇 - 年度第一条消息
        with get_db_session() as session:
            statement = (
                select(Messages)
                .where(
                    col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                    col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                )
                .order_by(col(Messages.timestamp).asc())
                .limit(1)
            )
            first_msg = session.exec(statement).first()
        if first_msg:
            data.first_message_time = first_msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            data.first_message_user = first_msg.user_nickname or first_msg.user_id or "未知用户"
            content = first_msg.processed_plain_text or first_msg.display_message or ""
            data.first_message_content = content[:50] + "..." if len(content) > 50 else content

        # 3. 最忙碌的一天
        # 使用 SQLite 的 date 函数按日期分组
        day_expr = func.date(col(Messages.timestamp))
        with get_db_session() as session:
            statement = (
                select(
                    day_expr.label("day"),
                    func.count().label("count"),
                )
                .where(
                    col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                    col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                )
                .group_by(day_expr)
                .order_by(func.count().desc())
                .limit(1)
            )
            busiest_result = session.exec(statement).all()
        if busiest_result:
            data.busiest_day = busiest_result[0][0]
            data.busiest_day_count = busiest_result[0][1] or 0

        # 4. 昼夜节律 - 24小时活跃分布
        hour_expr = func.strftime("%H", col(Messages.timestamp))
        with get_db_session() as session:
            statement = (
                select(
                    hour_expr.label("hour"),
                    func.count().label("count"),
                )
                .where(
                    col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                    col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                )
                .group_by(hour_expr)
            )
            hourly_rows = session.exec(statement).all()
        hourly_distribution = [0] * 24
        for row in hourly_rows:
            try:
                hour = int(row[0] or 0)
                if 0 <= hour < 24:
                    hourly_distribution[hour] = row[1] or 0
            except (ValueError, TypeError):
                continue
        data.hourly_distribution = hourly_distribution

        # 5. 深夜食堂 (0-4点)
        data.midnight_chat_count = sum(hourly_distribution[0:5])

        # 6. 判断是否夜猫子 (22点-4点活跃度 vs 6点-12点)
        night_activity = sum(hourly_distribution[22:24]) + sum(hourly_distribution[0:5])
        morning_activity = sum(hourly_distribution[6:13])
        data.is_night_owl = night_activity > morning_activity

    except Exception as e:
        logger.error(f"获取时光足迹数据失败: {e}")

    return data


# ==================== 维度二：社交网络 ====================


async def get_social_network(year: int = 2025) -> SocialNetworkData:
    """获取社交网络数据"""
    from src.config.config import global_config

    data = SocialNetworkData.model_construct()
    start_ts, end_ts = get_year_time_range(year)

    # 获取 bot 自身的 QQ 账号，用于过滤
    bot_qq = str(global_config.bot.qq_account or "")

    try:
        # 1. 加入的群组总数
        with get_db_session() as session:
            statement = select(func.count(func.distinct(col(Messages.group_id)))).where(
                col(Messages.group_id).is_not(None),
                col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
            )
            data.total_groups = int(session.exec(statement).first() or 0)

        # 2. 话痨群组 TOP3
        with get_db_session() as session:
            statement = (
                select(
                    col(Messages.group_id),
                    func.max(col(Messages.group_name)).label("group_name"),
                    func.count().label("count"),
                )
                .where(
                    col(Messages.group_id).is_not(None),
                    col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                    col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                )
                .group_by(col(Messages.group_id))
                .order_by(func.count().desc())
                .limit(5)
            )
            top_groups_rows = session.exec(statement).all()
        data.top_groups = [
            {
                "group_id": row[0],
                "group_name": row[1] or "未知群组",
                "message_count": row[2] or 0,
                "is_webui": str(row[0]).startswith("webui_"),
            }
            for row in top_groups_rows
        ]

        # 3. 互动最多的用户 TOP5（过滤 bot 自身）
        with get_db_session() as session:
            statement = (
                select(
                    col(Messages.user_id),
                    func.max(col(Messages.user_nickname)).label("user_nickname"),
                    func.count().label("count"),
                )
                .where(
                    col(Messages.user_id).is_not(None),
                    col(Messages.user_id) != bot_qq,
                    col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                    col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                )
                .group_by(col(Messages.user_id))
                .order_by(func.count().desc())
                .limit(5)
            )
            top_users_rows = session.exec(statement).all()
        data.top_users = [
            {
                "user_id": row[0],
                "user_nickname": row[1] or "未知用户",
                "message_count": row[2] or 0,
                "is_webui": str(row[0]).startswith("webui_"),
            }
            for row in top_users_rows
        ]

        # 4. 被@次数
        with get_db_session() as session:
            statement = select(func.count()).where(
                col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                col(Messages.is_at),
            )
            data.at_count = int(session.exec(statement).first() or 0)

        # 5. 被提及次数
        with get_db_session() as session:
            statement = select(func.count()).where(
                col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                col(Messages.is_mentioned),
            )
            data.mentioned_count = int(session.exec(statement).first() or 0)

        # 6. 最长情陪伴的用户（过滤 bot 自身）
        with get_db_session() as session:
            statement = select(PersonInfo).where(
                col(PersonInfo.user_id) != bot_qq,
                col(PersonInfo.first_known_time).is_not(None),
                col(PersonInfo.last_known_time).is_not(None),
            )
            persons = session.exec(statement).all()
        if persons:

            def _companion_days(person: PersonInfo) -> float:
                if not person.first_known_time or not person.last_known_time:
                    return 0.0
                return (person.last_known_time - person.first_known_time).total_seconds()

            longest = max(persons, key=_companion_days)
            data.longest_companion_user = longest.person_name or longest.user_nickname or longest.user_id
            data.longest_companion_days = int(_companion_days(longest) / 86400)
        else:
            data.longest_companion_user = None
            data.longest_companion_days = 0

    except Exception as e:
        logger.error(f"获取社交网络数据失败: {e}")

    return data


# ==================== 维度三：最强大脑 ====================


async def get_brain_power(year: int = 2025) -> BrainPowerData:
    """获取最强大脑数据"""
    data = BrainPowerData.model_construct()
    start_dt, end_dt = get_year_datetime_range(year)
    start_ts, end_ts = get_year_time_range(year)

    try:
        # 1. 年度消耗 Token 总量和总花费
        with get_db_session() as session:
            statement = select(
                func.sum(col(ModelUsage.total_tokens)).label("total_tokens"),
                func.sum(col(ModelUsage.cost)).label("total_cost"),
            ).where(col(ModelUsage.timestamp) >= start_dt, col(ModelUsage.timestamp) <= end_dt)
            result = session.exec(statement).first()
        if result:
            data.total_tokens = int(result[0] or 0)
            data.total_cost = round(float(result[1] or 0), 4)

        # 2. 最爱用的模型
        with get_db_session() as session:
            statement = (
                select(ModelUsage)
                .where(col(ModelUsage.timestamp) >= start_dt, col(ModelUsage.timestamp) <= end_dt)
                .order_by(desc(col(ModelUsage.timestamp)))
            )
            records = session.exec(statement).all()

        model_agg: dict[str, dict[str, float | int]] = {}
        for record in records:
            model_name = record.model_assign_name or record.model_name or "unknown"
            if model_name not in model_agg:
                model_agg[model_name] = {"count": 0, "tokens": 0, "cost": 0.0}
            bucket = model_agg[model_name]
            bucket["count"] = int(bucket["count"]) + 1
            bucket["tokens"] = int(bucket["tokens"]) + int(record.total_tokens or 0)
            bucket["cost"] = float(bucket["cost"]) + float(record.cost or 0.0)

        model_results = sorted(
            model_agg.items(),
            key=lambda item: float(item[1]["count"]),
            reverse=True,
        )[:10]
        if model_results:
            data.favorite_model = model_results[0][0]
            data.favorite_model_count = int(model_results[0][1]["count"])
            data.model_distribution = [
                {
                    "model": model_name,
                    "count": int(bucket["count"]),
                    "tokens": int(bucket["tokens"]),
                    "cost": round(float(bucket["cost"]), 4),
                }
                for model_name, bucket in model_results
            ]

        # 3. 最昂贵的一次思考
        if records:
            expensive_record = max(records, key=lambda record: record.cost or 0.0)
            data.most_expensive_cost = round(expensive_record.cost or 0.0, 4)
            data.most_expensive_time = expensive_record.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        # 4. 烧钱大户 TOP3 (按用户，过滤 system)
        consumer_agg: dict[str, dict[str, float | int]] = {}
        for record in records:
            user_id = record.model_api_provider_name
            if not user_id or user_id == "system":
                continue
            if user_id not in consumer_agg:
                consumer_agg[user_id] = {"cost": 0.0, "tokens": 0}
            bucket = consumer_agg[user_id]
            bucket["cost"] = float(bucket["cost"]) + float(record.cost or 0.0)
            bucket["tokens"] = int(bucket["tokens"]) + int(record.total_tokens or 0)

        data.top_token_consumers = [
            {
                "user_id": user_id,
                "cost": round(float(bucket["cost"]), 4),
                "tokens": int(bucket["tokens"]),
            }
            for user_id, bucket in sorted(
                consumer_agg.items(),
                key=lambda item: float(item[1]["cost"]),
                reverse=True,
            )[:3]
        ]

        # 5. 最喜欢的回复模型 TOP5（按模型的回复次数统计，只统计 replyer 调用）
        # 假设 replyer 调用有特定的 model_assign_name 格式或可以通过某种方式识别
        reply_model_agg: dict[str, int] = {}
        for record in records:
            model_assign_name = record.model_assign_name or ""
            if "replyer" not in model_assign_name and "回复" not in model_assign_name:
                continue
            model_name = model_assign_name or record.model_name or "unknown"
            reply_model_agg[model_name] = reply_model_agg.get(model_name, 0) + 1
        data.top_reply_models = [
            {"model": model_name, "count": count}
            for model_name, count in sorted(reply_model_agg.items(), key=lambda item: item[1], reverse=True)[:5]
        ]

        # 6. 高冷指数 (沉默率) - 基于 ActionRecords
        with get_db_session() as session:
            statement = select(func.count()).where(
                col(ActionRecord.timestamp) >= datetime.fromtimestamp(start_ts),
                col(ActionRecord.timestamp) <= datetime.fromtimestamp(end_ts),
            )
            total_actions = int(session.exec(statement).first() or 0)
        with get_db_session() as session:
            statement = select(func.count()).where(
                col(ActionRecord.timestamp) >= datetime.fromtimestamp(start_ts),
                col(ActionRecord.timestamp) <= datetime.fromtimestamp(end_ts),
                col(ActionRecord.action_name) == "no_reply",
            )
            no_reply_count = int(session.exec(statement).first() or 0)
        data.total_actions = total_actions
        data.no_reply_count = no_reply_count
        data.silence_rate = round(no_reply_count / total_actions * 100, 2) if total_actions > 0 else 0

        # 6. 情绪波动 (兴趣值)
        data.avg_interest_value = 0.0
        data.max_interest_value = 0.0

        # 找到最高兴趣值的时间
        if data.max_interest_value > 0:
            data.max_interest_time = None

        # 7. 思考深度 (基于 action_reasoning 长度)
        with get_db_session() as session:
            statement = select(ActionRecord).where(
                col(ActionRecord.timestamp) >= datetime.fromtimestamp(start_ts),
                col(ActionRecord.timestamp) <= datetime.fromtimestamp(end_ts),
                col(ActionRecord.action_reasoning).is_not(None),
                col(ActionRecord.action_reasoning) != "",
            )
            reasoning_records = session.exec(statement).all()
        reasoning_lengths = []
        max_len = 0
        max_len_time = None
        for record in reasoning_records:
            if record.action_reasoning:
                length = len(record.action_reasoning)
                reasoning_lengths.append(length)
                if length > max_len:
                    max_len = length
                max_len_time = record.timestamp

        if reasoning_lengths:
            data.avg_reasoning_length = round(sum(reasoning_lengths) / len(reasoning_lengths), 1)
            data.max_reasoning_length = max_len
            if max_len_time:
                data.max_reasoning_time = max_len_time.strftime("%Y-%m-%d %H:%M:%S")

    except Exception as e:
        logger.error(f"获取最强大脑数据失败: {e}")

    return data


# ==================== 维度四：个性与表达 ====================


async def get_expression_vibe(year: int = 2025) -> ExpressionVibeData:
    """获取个性与表达数据"""
    from src.config.config import global_config

    data = ExpressionVibeData.model_construct()
    start_ts, end_ts = get_year_time_range(year)

    # 获取 bot 自身的 QQ 账号，用于筛选 bot 发送的消息
    bot_qq = str(global_config.bot.qq_account or "")

    try:
        # 1. 表情包之王 - 使用次数最多的表情包
        with get_db_session() as session:
            statement = (
                select(Images).where(col(Images.is_registered)).order_by(desc(col(Images.query_count))).limit(5)
            )
            top_emojis = session.exec(statement).all()
        if top_emojis:
            data.top_emoji = {
                "id": top_emojis[0].id,
                "path": top_emojis[0].full_path,
                "description": top_emojis[0].description,
                "usage_count": top_emojis[0].query_count,
                "hash": top_emojis[0].image_hash,
            }
            data.top_emojis = [
                {
                    "id": e.id,
                    "path": e.full_path,
                    "description": e.description,
                    "usage_count": e.query_count,
                    "hash": e.image_hash,
                }
                for e in top_emojis
            ]

        # 2. 百变麦麦 - 最常用的表达风格
        with get_db_session() as session:
            statement = (
                select(Expression.style, func.sum(col(Expression.count)).label("total_count"))
                .where(
                    col(Expression.last_active_time) >= datetime.fromtimestamp(start_ts),
                    col(Expression.last_active_time) <= datetime.fromtimestamp(end_ts),
                )
                .group_by(Expression.style)
                .order_by(func.sum(col(Expression.count)).desc())
                .limit(5)
            )
            expression_rows = session.exec(statement).all()
        data.top_expressions = [{"style": row[0], "count": row[1] or 0} for row in expression_rows]

        # 3. 被拒绝的表达
        data.rejected_expression_count = 0

        # 4. 已检查的表达
        data.checked_expression_count = 0

        # 5. 表达总数
        with get_db_session() as session:
            statement = select(func.count()).where(
                col(Expression.last_active_time) >= datetime.fromtimestamp(start_ts),
                col(Expression.last_active_time) <= datetime.fromtimestamp(end_ts),
            )
            data.total_expressions = int(session.exec(statement).first() or 0)

        # 6. 动作类型分布 (过滤无意义的动作)
        # 过滤掉: no_reply_until_call, make_question, no_action, wait, complete_talk, listening, block_and_ignore
        excluded_actions = [
            "reply",
            "no_reply",
            "no_reply_until_call",
            "make_question",
            "no_action",
            "wait",
            "complete_talk",
            "listening",
            "block_and_ignore",
        ]
        with get_db_session() as session:
            statement = (
                select(ActionRecord.action_name, func.count().label("count"))
                .where(
                    col(ActionRecord.timestamp) >= datetime.fromtimestamp(start_ts),
                    col(ActionRecord.timestamp) <= datetime.fromtimestamp(end_ts),
                    col(ActionRecord.action_name).not_in(excluded_actions),
                )
                .group_by(ActionRecord.action_name)
                .order_by(func.count().desc())
                .limit(10)
            )
            action_rows = session.exec(statement).all()
        data.action_types = [{"action": row[0], "count": row[1]} for row in action_rows]

        # 7. 处理的图片数量
        with get_db_session() as session:
            statement = select(func.count()).where(
                col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                col(Messages.is_picture),
            )
            data.image_processed_count = int(session.exec(statement).first() or 0)

        # 8. 深夜还在回复 (0-6点最晚的10条消息中随机抽取一条)
        import random
        import re

        def clean_message_content(content: str) -> str:
            """清理消息内容，移除回复引用等标记"""
            if not content:
                return ""
            # 移除 [回复<xxx:xxx> 的消息：...] 格式的引用
            content = re.sub(r"\[回复<[^>]+>\s*的消息[：:][^\]]*\]", "", content)
            # 移除 [图片] [表情] 等标记
            content = re.sub(r"\[(图片|表情|语音|视频|文件)\]", "", content)
            # 移除多余的空白
            content = re.sub(r"\s+", " ", content).strip()
            return content

        # 使用 user_id 判断是否是 bot 发送的消息
        with get_db_session() as session:
            statement = (
                select(Messages)
                .where(
                    col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                    col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                    col(Messages.user_id) == bot_qq,
                )
                .order_by(desc(col(Messages.timestamp)))
                .limit(200)
            )
            late_night_messages = session.exec(statement).all()
        # 筛选出0-6点的消息
        late_night_filtered = []
        for msg in late_night_messages:
            msg_dt = msg.timestamp
            hour = msg_dt.hour
            if 0 <= hour < 6:  # 0点到6点
                raw_content = msg.processed_plain_text or msg.display_message or ""
                cleaned_content = clean_message_content(raw_content)
                # 只保留有意义的内容
                if cleaned_content and len(cleaned_content) > 2:
                    late_night_filtered.append(
                        {
                            "time": msg_dt.timestamp(),
                            "hour": hour,
                            "minute": msg_dt.minute,
                            "content": cleaned_content,
                            "datetime_str": msg_dt.strftime("%H:%M"),
                        }
                    )
            if len(late_night_filtered) >= 10:
                break

        if late_night_filtered:
            selected = random.choice(late_night_filtered)
            content = selected["content"][:50] + "..." if len(selected["content"]) > 50 else selected["content"]
            data.late_night_reply = {
                "time": selected["datetime_str"],
                "content": content,
            }

        # 9. 最喜欢的回复（按 action_data 统计回复内容出现次数）
        from collections import Counter
        import json as json_lib

        with get_db_session() as session:
            statement = select(ActionRecord).where(
                col(ActionRecord.timestamp) >= datetime.fromtimestamp(start_ts),
                col(ActionRecord.timestamp) <= datetime.fromtimestamp(end_ts),
                col(ActionRecord.action_name) == "reply",
                col(ActionRecord.action_data).is_not(None),
                col(ActionRecord.action_data) != "",
            )
            reply_records = session.exec(statement).all()

        reply_contents = []
        for record in reply_records:
            try:
                action_data = record.action_data
                if action_data:
                    content = None
                    # 尝试解析 JSON 格式
                    try:
                        parsed = json_lib.loads(action_data)
                        if isinstance(parsed, dict):
                            # 优先使用 reply_text，其次使用 content
                            content = parsed.get("reply_text") or parsed.get("content")
                        elif isinstance(parsed, str):
                            content = parsed
                    except (json_lib.JSONDecodeError, TypeError):
                        pass

                    # 如果 JSON 解析失败，尝试解析 Python 字典字符串格式
                    # 例如: "{'reply_text': '墨白灵不知道哦'}"
                    if content is None:
                        import ast

                        try:
                            parsed = ast.literal_eval(action_data)
                            if isinstance(parsed, dict):
                                content = parsed.get("reply_text") or parsed.get("content")
                            elif isinstance(parsed, str):
                                content = parsed
                        except (ValueError, SyntaxError):
                            # 无法解析，使用原始字符串
                            content = action_data

                    # 只统计有意义的回复（长度大于2）
                    if content and len(content) > 2:
                        reply_contents.append(content)
            except Exception:
                continue

        if reply_contents:
            content_counter = Counter(reply_contents)
            most_common = content_counter.most_common(1)
            if most_common:
                fav_content, fav_count = most_common[0]
                # 截断过长的内容
                display_content = fav_content[:50] + "..." if len(fav_content) > 50 else fav_content
                data.favorite_reply = {
                    "content": display_content,
                    "count": fav_count,
                }

    except Exception as e:
        logger.error(f"获取个性与表达数据失败: {e}")

    return data


# ==================== 维度五：趣味成就 ====================


async def get_achievements(year: int = 2025) -> AchievementData:
    """获取趣味成就数据"""
    data = AchievementData.model_construct()
    start_ts, end_ts = get_year_time_range(year)

    try:
        # 1. 新学到的黑话数量
        # Jargon 表没有时间字段,统计全部已确认的黑话
        with get_db_session() as session:
            statement = select(func.count()).where(col(Jargon.is_jargon))
            data.new_jargon_count = int(session.exec(statement).first() or 0)

        # 2. 代表性黑话示例
        with get_db_session() as session:
            statement = select(Jargon).where(col(Jargon.is_jargon)).order_by(desc(col(Jargon.count))).limit(5)
            jargon_samples = session.exec(statement).all()
        data.sample_jargons = [
            {
                "content": j.content,
                "meaning": j.meaning,
                "count": j.count,
            }
            for j in jargon_samples
        ]

        # 3. 总消息数
        with get_db_session() as session:
            statement = select(func.count()).where(
                col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
            )
            data.total_messages = int(session.exec(statement).first() or 0)

        # 4. 总回复数 (有 reply_to 的消息)
        with get_db_session() as session:
            statement = select(func.count()).where(
                col(Messages.timestamp) >= datetime.fromtimestamp(start_ts),
                col(Messages.timestamp) <= datetime.fromtimestamp(end_ts),
                col(Messages.reply_to).is_not(None),
            )
            data.total_replies = int(session.exec(statement).first() or 0)

    except Exception as e:
        logger.error(f"获取趣味成就数据失败: {e}")

    return data


# ==================== API 路由 ====================


@router.get("/full", response_model=AnnualReportData)
async def get_full_annual_report(year: int = 2025, _auth: bool = Depends(require_auth)):
    """
    获取完整年度报告数据

    Args:
        year: 报告年份，默认2025

    Returns:
        完整的年度报告数据
    """
    try:
        from src.config.config import global_config

        logger.info(f"开始生成 {year} 年度报告...")

        # 获取 bot 名称
        bot_name = global_config.bot.nickname or "麦麦"

        # 并行获取各维度数据
        time_footprint = await get_time_footprint(year)
        social_network = await get_social_network(year)
        brain_power = await get_brain_power(year)
        expression_vibe = await get_expression_vibe(year)
        achievements = await get_achievements(year)

        report = AnnualReportData(
            year=year,
            bot_name=bot_name,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            time_footprint=time_footprint,
            social_network=social_network,
            brain_power=brain_power,
            expression_vibe=expression_vibe,
            achievements=achievements,
        )

        logger.info(f"{year} 年度报告生成完成")
        return report

    except Exception as e:
        logger.error(f"生成年度报告失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成年度报告失败: {str(e)}") from e


@router.get("/time-footprint", response_model=TimeFootprintData)
async def get_time_footprint_api(year: int = 2025, _auth: bool = Depends(require_auth)):
    """获取时光足迹数据"""
    try:
        return await get_time_footprint(year)
    except Exception as e:
        logger.error(f"获取时光足迹数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/social-network", response_model=SocialNetworkData)
async def get_social_network_api(year: int = 2025, _auth: bool = Depends(require_auth)):
    """获取社交网络数据"""
    try:
        return await get_social_network(year)
    except Exception as e:
        logger.error(f"获取社交网络数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/brain-power", response_model=BrainPowerData)
async def get_brain_power_api(year: int = 2025, _auth: bool = Depends(require_auth)):
    """获取最强大脑数据"""
    try:
        return await get_brain_power(year)
    except Exception as e:
        logger.error(f"获取最强大脑数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/expression-vibe", response_model=ExpressionVibeData)
async def get_expression_vibe_api(year: int = 2025, _auth: bool = Depends(require_auth)):
    """获取个性与表达数据"""
    try:
        return await get_expression_vibe(year)
    except Exception as e:
        logger.error(f"获取个性与表达数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/achievements", response_model=AchievementData)
async def get_achievements_api(year: int = 2025, _auth: bool = Depends(require_auth)):
    """获取趣味成就数据"""
    try:
        return await get_achievements(year)
    except Exception as e:
        logger.error(f"获取趣味成就数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
