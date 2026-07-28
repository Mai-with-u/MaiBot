from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StatisticsSummary(BaseModel):
    """统计数据摘要"""

    total_requests: int = Field(0, description="总请求数")
    total_cost: float = Field(0.0, description="总花费")
    total_tokens: int = Field(0, description="总token数")
    input_tokens: int = Field(0, description="输入token数")
    output_tokens: int = Field(0, description="输出token数")
    cache_hit_tokens: int = Field(0, description="Prompt缓存命中token数")
    cache_miss_tokens: int = Field(0, description="Prompt缓存未命中token数")
    cache_hit_rate: Optional[float] = Field(None, description="Prompt缓存命中率")
    chat_cache_hit_tokens: int = Field(0, description="聊天任务Prompt缓存命中token数")
    chat_cache_miss_tokens: int = Field(0, description="聊天任务Prompt缓存未命中token数")
    chat_cache_hit_rate: Optional[float] = Field(None, description="聊天任务Prompt缓存命中率")
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
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_hit_rate: Optional[float] = None
    avg_response_time: float


class TimeSeriesData(BaseModel):
    """时间序列数据"""

    timestamp: str
    requests: int = 0
    cost: float = 0.0
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0


class DashboardData(BaseModel):
    """仪表盘数据"""

    summary: StatisticsSummary
    model_stats: List[ModelStatistics]
    hourly_data: List[TimeSeriesData]
    daily_data: List[TimeSeriesData]
    recent_activity: List[Dict[str, Any]]
