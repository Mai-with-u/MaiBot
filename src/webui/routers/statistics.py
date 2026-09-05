from fastapi import APIRouter, Depends, HTTPException

from src.common.logger import get_logger
from src.services.statistics_service import (
    get_dashboard_statistics,
    get_detailed_statistics_snapshot,
)
from src.webui.dependencies import require_auth
from src.webui.schemas.statistics import DashboardData, DetailedStatisticsData

logger = get_logger("webui.statistics")

router = APIRouter(prefix="/statistics", tags=["statistics"], dependencies=[Depends(require_auth)])


@router.get("/detailed", response_model=DetailedStatisticsData)
async def get_detailed_statistics() -> DetailedStatisticsData:
    """获取与 HTML 报告同源的详细统计快照。"""

    snapshot = get_detailed_statistics_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="详细统计正在生成，请稍后重试")
    return snapshot


@router.get("/dashboard", response_model=DashboardData)
async def get_dashboard_data(hours: int = 24) -> DashboardData:
    """获取仪表盘统计数据。"""
    try:
        return await get_dashboard_statistics(hours=hours)
    except Exception as e:
        logger.error(f"获取仪表盘数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}") from e
