"""WebUI Schemas - Pydantic models for API requests and responses."""

# News schemas
from .news import NewsItem, NewsResponse

# Statistics schemas
from .statistics import DashboardData, ModelStatistics, StatisticsSummary, TimeSeriesData

__all__ = [
    # Statistics
    "StatisticsSummary",
    "ModelStatistics",
    "TimeSeriesData",
    "DashboardData",
    # News
    "NewsItem",
    "NewsResponse",
]
