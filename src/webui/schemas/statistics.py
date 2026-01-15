from pydantic import BaseModel, Field
from typing import Dict, Any, List


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
    model_stats: List[ModelStatistics]
    hourly_data: List[TimeSeriesData]
    daily_data: List[TimeSeriesData]
    recent_activity: List[Dict[str, Any]]
