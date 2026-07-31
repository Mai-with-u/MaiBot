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
    online_seconds: float = Field(0.0, description="在线时间（秒）")
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


class DetailedStatisticsSummary(BaseModel):
    """详细统计页面的汇总指标。"""

    online_time: float = 0.0
    total_messages: int = 0
    total_replies: int = 0
    total_requests: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_hit_rate: Optional[float] = None
    total_cost: float = 0.0
    cost_per_100_messages: float = 0.0
    cost_per_100_messages_excluding_replies: float = 0.0
    cost_per_100_replies: float = 0.0
    cost_per_hour: float = 0.0
    tokens_per_hour: float = 0.0


class DetailedStatisticsBreakdown(BaseModel):
    """模型、模块或请求类型维度的详细统计行。"""

    name: str
    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_hit_rate: Optional[float] = None
    total_cost: float = 0.0
    avg_time_cost: float = 0.0
    std_time_cost: float = 0.0
    avg_calls_per_reply: Optional[float] = None
    avg_tokens_per_reply: Optional[float] = None
    avg_tokens_per_call: Optional[float] = None


class DetailedChatStatistics(BaseModel):
    """聊天流消息数量统计。"""

    name: str
    message_count: int = 0


class DetailedDistributionItem(BaseModel):
    """饼图使用的名称和值。"""

    name: str
    value: float = 0.0


class DetailedStatisticsDistributions(BaseModel):
    """单个统计时段的分布图数据。"""

    owner_costs: List[DetailedDistributionItem]
    model_costs: List[DetailedDistributionItem]
    module_costs: List[DetailedDistributionItem]
    request_type_costs: List[DetailedDistributionItem]
    chat_messages: List[DetailedDistributionItem]
    chat_costs: List[DetailedDistributionItem]


class DetailedStatisticsPeriod(BaseModel):
    """详细统计中的一个统计时段。"""

    key: str
    start_time: str
    end_time: str
    summary: DetailedStatisticsSummary
    models: List[DetailedStatisticsBreakdown]
    modules: List[DetailedStatisticsBreakdown]
    request_types: List[DetailedStatisticsBreakdown]
    chats: List[DetailedChatStatistics]
    distributions: DetailedStatisticsDistributions


class DetailedStatisticsTrendData(BaseModel):
    """详细统计的基础趋势数据。"""

    time_labels: List[str]
    total_cost_data: List[float]
    cost_by_model: Dict[str, List[float]]
    cost_by_module: Dict[str, List[float]]
    message_by_chat: Dict[str, List[int]]


class DetailedStatisticsMetricsData(BaseModel):
    """详细统计的派生指标趋势数据。"""

    time_labels: List[str]
    cost_per_100_messages: List[float]
    cost_per_hour: List[float]
    tokens_per_hour: List[float]
    cost_per_100_replies: List[float]


class DetailedStatisticsData(BaseModel):
    """原生 WebUI 与 HTML 报告共享的详细统计快照。"""

    generated_at: str
    periods: List[DetailedStatisticsPeriod]
    trends: Dict[str, DetailedStatisticsTrendData]
    metrics: Dict[str, DetailedStatisticsMetricsData]
