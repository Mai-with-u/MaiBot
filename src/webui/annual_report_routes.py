"""麦麦 2025 年度总结 API 路由"""

from fastapi import APIRouter, HTTPException, Depends, Cookie, Header
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime
from peewee import fn

from src.common.logger import get_logger
from src.common.database.database_model import (
    LLMUsage,
    OnlineTime,
    Messages,
    ChatStreams,
    PersonInfo,
    Emoji,
    Expression,
    ActionRecords,
    Jargon,
)
from src.webui.auth import verify_auth_token_from_cookie_or_header

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
    hourly_distribution: List[int] = Field(default_factory=lambda: [0] * 24, description="24小时活跃分布")
    midnight_chat_count: int = Field(0, description="深夜(0-4点)互动次数")
    is_night_owl: bool = Field(False, description="是否是夜猫子")


class SocialNetworkData(BaseModel):
    """社交网络数据"""

    total_groups: int = Field(0, description="加入的群组总数")
    new_friends_count: int = Field(0, description="今年新认识的朋友数")
    top_groups: List[Dict[str, Any]] = Field(default_factory=list, description="话痨群组TOP3")
    top_users: List[Dict[str, Any]] = Field(default_factory=list, description="互动最多的用户TOP3")
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
    model_distribution: List[Dict[str, Any]] = Field(default_factory=list, description="模型使用分布")
    most_expensive_cost: float = Field(0.0, description="最昂贵的一次思考花费")
    most_expensive_time: Optional[str] = Field(None, description="最昂贵思考的时间")
    top_token_consumers: List[Dict[str, Any]] = Field(default_factory=list, description="烧钱大户TOP3")
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

    top_emoji: Optional[Dict[str, Any]] = Field(None, description="表情包之王")
    top_emojis: List[Dict[str, Any]] = Field(default_factory=list, description="TOP5表情包")
    top_expressions: List[Dict[str, Any]] = Field(default_factory=list, description="最常用的表达风格")
    rejected_expression_count: int = Field(0, description="被拒绝的表达次数")
    checked_expression_count: int = Field(0, description="已检查的表达次数")
    total_expressions: int = Field(0, description="表达总数")
    action_types: List[Dict[str, Any]] = Field(default_factory=list, description="动作类型分布")
    image_processed_count: int = Field(0, description="处理的图片数量")


class AchievementData(BaseModel):
    """趣味成就数据"""

    new_jargon_count: int = Field(0, description="新学到的黑话数量")
    sample_jargons: List[Dict[str, Any]] = Field(default_factory=list, description="代表性黑话示例")
    total_messages: int = Field(0, description="总消息数")
    total_replies: int = Field(0, description="总回复数")


class AnnualReportData(BaseModel):
    """年度报告完整数据"""

    year: int = Field(2025, description="报告年份")
    generated_at: str = Field(..., description="报告生成时间")
    time_footprint: TimeFootprintData = Field(default_factory=TimeFootprintData)
    social_network: SocialNetworkData = Field(default_factory=SocialNetworkData)
    brain_power: BrainPowerData = Field(default_factory=BrainPowerData)
    expression_vibe: ExpressionVibeData = Field(default_factory=ExpressionVibeData)
    achievements: AchievementData = Field(default_factory=AchievementData)


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
    data = TimeFootprintData()
    start_ts, end_ts = get_year_time_range(year)
    start_dt, end_dt = get_year_datetime_range(year)

    try:
        # 1. 年度在线时长
        online_records = list(
            OnlineTime.select().where(
                (OnlineTime.start_timestamp >= start_dt) | (OnlineTime.end_timestamp <= end_dt)
            )
        )
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
        first_msg = (
            Messages.select()
            .where((Messages.time >= start_ts) & (Messages.time <= end_ts))
            .order_by(Messages.time.asc())
            .first()
        )
        if first_msg:
            data.first_message_time = datetime.fromtimestamp(first_msg.time).strftime("%Y-%m-%d %H:%M:%S")
            data.first_message_user = first_msg.user_nickname or first_msg.user_id or "未知用户"
            content = first_msg.processed_plain_text or first_msg.display_message or ""
            data.first_message_content = content[:50] + "..." if len(content) > 50 else content

        # 3. 最忙碌的一天
        # 使用 SQLite 的 date 函数按日期分组
        busiest_query = (
            Messages.select(
                fn.date(Messages.time, "unixepoch").alias("day"),
                fn.COUNT(Messages.id).alias("count"),
            )
            .where((Messages.time >= start_ts) & (Messages.time <= end_ts))
            .group_by(fn.date(Messages.time, "unixepoch"))
            .order_by(fn.COUNT(Messages.id).desc())
            .limit(1)
        )
        busiest_result = list(busiest_query.dicts())
        if busiest_result:
            data.busiest_day = busiest_result[0].get("day")
            data.busiest_day_count = busiest_result[0].get("count", 0)

        # 4. 昼夜节律 - 24小时活跃分布
        hourly_query = (
            Messages.select(
                fn.strftime("%H", Messages.time, "unixepoch").alias("hour"),
                fn.COUNT(Messages.id).alias("count"),
            )
            .where((Messages.time >= start_ts) & (Messages.time <= end_ts))
            .group_by(fn.strftime("%H", Messages.time, "unixepoch"))
        )
        hourly_distribution = [0] * 24
        for row in hourly_query.dicts():
            try:
                hour = int(row.get("hour", 0))
                if 0 <= hour < 24:
                    hourly_distribution[hour] = row.get("count", 0)
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
    data = SocialNetworkData()
    start_ts, end_ts = get_year_time_range(year)

    try:
        # 1. 加入的群组总数
        data.total_groups = ChatStreams.select().where(ChatStreams.group_id.is_null(False)).count()

        # 2. 今年新认识的朋友数
        data.new_friends_count = (
            PersonInfo.select()
            .where(
                (PersonInfo.know_times.is_null(False))
                & (PersonInfo.know_times >= start_ts)
                & (PersonInfo.know_times <= end_ts)
            )
            .count()
        )

        # 3. 话痨群组 TOP3
        top_groups_query = (
            Messages.select(
                Messages.chat_info_group_id,
                Messages.chat_info_group_name,
                fn.COUNT(Messages.id).alias("count"),
            )
            .where(
                (Messages.time >= start_ts)
                & (Messages.time <= end_ts)
                & (Messages.chat_info_group_id.is_null(False))
            )
            .group_by(Messages.chat_info_group_id)
            .order_by(fn.COUNT(Messages.id).desc())
            .limit(3)
        )
        data.top_groups = [
            {
                "group_id": row["chat_info_group_id"],
                "group_name": row["chat_info_group_name"] or "未知群组",
                "message_count": row["count"],
            }
            for row in top_groups_query.dicts()
        ]

        # 4. 互动最多的用户 TOP3
        top_users_query = (
            Messages.select(
                Messages.user_id,
                Messages.user_nickname,
                fn.COUNT(Messages.id).alias("count"),
            )
            .where(
                (Messages.time >= start_ts)
                & (Messages.time <= end_ts)
                & (Messages.user_id.is_null(False))
            )
            .group_by(Messages.user_id)
            .order_by(fn.COUNT(Messages.id).desc())
            .limit(3)
        )
        data.top_users = [
            {
                "user_id": row["user_id"],
                "user_nickname": row["user_nickname"] or "未知用户",
                "message_count": row["count"],
            }
            for row in top_users_query.dicts()
        ]

        # 5. 被@次数
        data.at_count = (
            Messages.select()
            .where(
                (Messages.time >= start_ts)
                & (Messages.time <= end_ts)
                & (Messages.is_at == True)
            )
            .count()
        )

        # 6. 被提及次数
        data.mentioned_count = (
            Messages.select()
            .where(
                (Messages.time >= start_ts)
                & (Messages.time <= end_ts)
                & (Messages.is_mentioned == True)
            )
            .count()
        )

        # 7. 最长情陪伴的用户
        # 找出跨度时间最长的用户
        companion_query = (
            ChatStreams.select(
                ChatStreams.user_id,
                ChatStreams.user_nickname,
                (ChatStreams.last_active_time - ChatStreams.create_time).alias("duration"),
            )
            .where(ChatStreams.user_id.is_null(False))
            .order_by((ChatStreams.last_active_time - ChatStreams.create_time).desc())
            .limit(1)
        )
        companion_result = list(companion_query.dicts())
        if companion_result:
            data.longest_companion_user = companion_result[0].get("user_nickname") or "未知用户"
            duration = companion_result[0].get("duration", 0) or 0
            data.longest_companion_days = int(duration / 86400)  # 转换为天

    except Exception as e:
        logger.error(f"获取社交网络数据失败: {e}")

    return data


# ==================== 维度三：最强大脑 ====================


async def get_brain_power(year: int = 2025) -> BrainPowerData:
    """获取最强大脑数据"""
    data = BrainPowerData()
    start_dt, end_dt = get_year_datetime_range(year)
    start_ts, end_ts = get_year_time_range(year)

    try:
        # 1. 年度消耗 Token 总量和总花费
        token_query = LLMUsage.select(
            fn.COALESCE(fn.SUM(LLMUsage.total_tokens), 0).alias("total_tokens"),
            fn.COALESCE(fn.SUM(LLMUsage.cost), 0).alias("total_cost"),
        ).where((LLMUsage.timestamp >= start_dt) & (LLMUsage.timestamp <= end_dt))
        result = token_query.dicts().get()
        data.total_tokens = int(result.get("total_tokens", 0) or 0)
        data.total_cost = round(float(result.get("total_cost", 0) or 0), 4)

        # 2. 最爱用的模型
        model_query = (
            LLMUsage.select(
                fn.COALESCE(LLMUsage.model_assign_name, LLMUsage.model_name).alias("model"),
                fn.COUNT(LLMUsage.id).alias("count"),
                fn.COALESCE(fn.SUM(LLMUsage.total_tokens), 0).alias("tokens"),
                fn.COALESCE(fn.SUM(LLMUsage.cost), 0).alias("cost"),
            )
            .where((LLMUsage.timestamp >= start_dt) & (LLMUsage.timestamp <= end_dt))
            .group_by(fn.COALESCE(LLMUsage.model_assign_name, LLMUsage.model_name))
            .order_by(fn.COUNT(LLMUsage.id).desc())
            .limit(10)
        )
        model_results = list(model_query.dicts())
        if model_results:
            data.favorite_model = model_results[0].get("model")
            data.favorite_model_count = model_results[0].get("count", 0)
            data.model_distribution = [
                {
                    "model": row["model"],
                    "count": row["count"],
                    "tokens": row["tokens"],
                    "cost": round(row["cost"], 4),
                }
                for row in model_results
            ]

        # 3. 最昂贵的一次思考
        expensive_query = (
            LLMUsage.select(LLMUsage.cost, LLMUsage.timestamp)
            .where((LLMUsage.timestamp >= start_dt) & (LLMUsage.timestamp <= end_dt))
            .order_by(LLMUsage.cost.desc())
            .limit(1)
        )
        expensive_result = expensive_query.first()
        if expensive_result:
            data.most_expensive_cost = round(expensive_result.cost or 0, 4)
            data.most_expensive_time = expensive_result.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        # 4. 烧钱大户 TOP3 (按用户)
        consumer_query = (
            LLMUsage.select(
                LLMUsage.user_id,
                fn.COALESCE(fn.SUM(LLMUsage.cost), 0).alias("cost"),
                fn.COALESCE(fn.SUM(LLMUsage.total_tokens), 0).alias("tokens"),
            )
            .where((LLMUsage.timestamp >= start_dt) & (LLMUsage.timestamp <= end_dt))
            .group_by(LLMUsage.user_id)
            .order_by(fn.SUM(LLMUsage.cost).desc())
            .limit(3)
        )
        data.top_token_consumers = [
            {
                "user_id": row["user_id"],
                "cost": round(row["cost"], 4),
                "tokens": row["tokens"],
            }
            for row in consumer_query.dicts()
        ]

        # 5. 高冷指数 (沉默率) - 基于 ActionRecords
        total_actions = ActionRecords.select().where(
            (ActionRecords.time >= start_ts) & (ActionRecords.time <= end_ts)
        ).count()
        no_reply_count = ActionRecords.select().where(
            (ActionRecords.time >= start_ts)
            & (ActionRecords.time <= end_ts)
            & (ActionRecords.action_name == "no_reply")
        ).count()
        data.total_actions = total_actions
        data.no_reply_count = no_reply_count
        data.silence_rate = round(no_reply_count / total_actions * 100, 2) if total_actions > 0 else 0

        # 6. 情绪波动 (兴趣值)
        interest_query = Messages.select(
            fn.AVG(Messages.interest_value).alias("avg_interest"),
            fn.MAX(Messages.interest_value).alias("max_interest"),
        ).where(
            (Messages.time >= start_ts)
            & (Messages.time <= end_ts)
            & (Messages.interest_value.is_null(False))
        )
        interest_result = interest_query.dicts().get()
        data.avg_interest_value = round(float(interest_result.get("avg_interest") or 0), 2)
        data.max_interest_value = round(float(interest_result.get("max_interest") or 0), 2)

        # 找到最高兴趣值的时间
        if data.max_interest_value > 0:
            max_interest_msg = (
                Messages.select(Messages.time)
                .where(
                    (Messages.time >= start_ts)
                    & (Messages.time <= end_ts)
                    & (Messages.interest_value == data.max_interest_value)
                )
                .first()
            )
            if max_interest_msg:
                data.max_interest_time = datetime.fromtimestamp(max_interest_msg.time).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

        # 7. 思考深度 (基于 action_reasoning 长度)
        reasoning_records = (
            ActionRecords.select(ActionRecords.action_reasoning, ActionRecords.time)
            .where(
                (ActionRecords.time >= start_ts)
                & (ActionRecords.time <= end_ts)
                & (ActionRecords.action_reasoning.is_null(False))
                & (ActionRecords.action_reasoning != "")
            )
        )
        reasoning_lengths = []
        max_len = 0
        max_len_time = None
        for record in reasoning_records:
            if record.action_reasoning:
                length = len(record.action_reasoning)
                reasoning_lengths.append(length)
                if length > max_len:
                    max_len = length
                    max_len_time = record.time
        
        if reasoning_lengths:
            data.avg_reasoning_length = round(sum(reasoning_lengths) / len(reasoning_lengths), 1)
            data.max_reasoning_length = max_len
            if max_len_time:
                data.max_reasoning_time = datetime.fromtimestamp(max_len_time).strftime("%Y-%m-%d %H:%M:%S")

    except Exception as e:
        logger.error(f"获取最强大脑数据失败: {e}")

    return data


# ==================== 维度四：个性与表达 ====================


async def get_expression_vibe(year: int = 2025) -> ExpressionVibeData:
    """获取个性与表达数据"""
    data = ExpressionVibeData()
    start_ts, end_ts = get_year_time_range(year)

    try:
        # 1. 表情包之王 - 使用次数最多的表情包
        top_emoji_query = (
            Emoji.select(Emoji.id, Emoji.full_path, Emoji.description, Emoji.usage_count, Emoji.emoji_hash)
            .where(Emoji.is_registered == True)
            .order_by(Emoji.usage_count.desc())
            .limit(5)
        )
        top_emojis = list(top_emoji_query.dicts())
        if top_emojis:
            data.top_emoji = {
                "id": top_emojis[0].get("id"),
                "path": top_emojis[0].get("full_path"),
                "description": top_emojis[0].get("description"),
                "usage_count": top_emojis[0].get("usage_count", 0),
                "hash": top_emojis[0].get("emoji_hash"),
            }
            data.top_emojis = [
                {
                    "id": e.get("id"),
                    "path": e.get("full_path"),
                    "description": e.get("description"),
                    "usage_count": e.get("usage_count", 0),
                    "hash": e.get("emoji_hash"),
                }
                for e in top_emojis
            ]

        # 2. 百变麦麦 - 最常用的表达风格
        expression_query = (
            Expression.select(
                Expression.style,
                fn.SUM(Expression.count).alias("total_count"),
            )
            .where(
                (Expression.last_active_time >= start_ts)
                & (Expression.last_active_time <= end_ts)
            )
            .group_by(Expression.style)
            .order_by(fn.SUM(Expression.count).desc())
            .limit(5)
        )
        data.top_expressions = [
            {"style": row["style"], "count": row["total_count"]}
            for row in expression_query.dicts()
        ]

        # 3. 被拒绝的表达
        data.rejected_expression_count = (
            Expression.select()
            .where(
                (Expression.last_active_time >= start_ts)
                & (Expression.last_active_time <= end_ts)
                & (Expression.rejected == True)
            )
            .count()
        )

        # 4. 已检查的表达
        data.checked_expression_count = (
            Expression.select()
            .where(
                (Expression.last_active_time >= start_ts)
                & (Expression.last_active_time <= end_ts)
                & (Expression.checked == True)
            )
            .count()
        )

        # 5. 表达总数
        data.total_expressions = (
            Expression.select()
            .where(
                (Expression.last_active_time >= start_ts)
                & (Expression.last_active_time <= end_ts)
            )
            .count()
        )

        # 6. 动作类型分布 (非 reply 动作)
        action_query = (
            ActionRecords.select(
                ActionRecords.action_name,
                fn.COUNT(ActionRecords.id).alias("count"),
            )
            .where(
                (ActionRecords.time >= start_ts)
                & (ActionRecords.time <= end_ts)
                & (ActionRecords.action_name != "reply")
                & (ActionRecords.action_name != "no_reply")
            )
            .group_by(ActionRecords.action_name)
            .order_by(fn.COUNT(ActionRecords.id).desc())
            .limit(10)
        )
        data.action_types = [
            {"action": row["action_name"], "count": row["count"]}
            for row in action_query.dicts()
        ]

        # 7. 处理的图片数量
        data.image_processed_count = (
            Messages.select()
            .where(
                (Messages.time >= start_ts)
                & (Messages.time <= end_ts)
                & (Messages.is_picid == True)
            )
            .count()
        )

    except Exception as e:
        logger.error(f"获取个性与表达数据失败: {e}")

    return data


# ==================== 维度五：趣味成就 ====================


async def get_achievements(year: int = 2025) -> AchievementData:
    """获取趣味成就数据"""
    data = AchievementData()
    start_ts, end_ts = get_year_time_range(year)

    try:
        # 1. 新学到的黑话数量
        # Jargon 表没有时间字段,统计全部已确认的黑话
        data.new_jargon_count = Jargon.select().where(Jargon.is_jargon == True).count()

        # 2. 代表性黑话示例
        jargon_samples = (
            Jargon.select(Jargon.content, Jargon.meaning, Jargon.count)
            .where(Jargon.is_jargon == True)
            .order_by(Jargon.count.desc())
            .limit(5)
        )
        data.sample_jargons = [
            {
                "content": j.content,
                "meaning": j.meaning,
                "count": j.count,
            }
            for j in jargon_samples
        ]

        # 3. 总消息数
        data.total_messages = (
            Messages.select()
            .where((Messages.time >= start_ts) & (Messages.time <= end_ts))
            .count()
        )

        # 4. 总回复数 (有 reply_to 的消息)
        data.total_replies = (
            Messages.select()
            .where(
                (Messages.time >= start_ts)
                & (Messages.time <= end_ts)
                & (Messages.reply_to.is_null(False))
            )
            .count()
        )

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
        logger.info(f"开始生成 {year} 年度报告...")

        # 并行获取各维度数据
        time_footprint = await get_time_footprint(year)
        social_network = await get_social_network(year)
        brain_power = await get_brain_power(year)
        expression_vibe = await get_expression_vibe(year)
        achievements = await get_achievements(year)

        report = AnnualReportData(
            year=year,
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
