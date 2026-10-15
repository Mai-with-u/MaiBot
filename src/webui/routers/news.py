"""首页资讯 API 路由"""

from fastapi import APIRouter, Depends, HTTPException, Query

from src.common.logger import get_logger
from src.services.news_service import NewsSourceError, fetch_news
from src.webui.dependencies import require_auth
from src.webui.schemas.news import NewsResponse

logger = get_logger("webui.news")

router = APIRouter(prefix="/news", tags=["news"], dependencies=[Depends(require_auth)])


@router.get("", response_model=NewsResponse)
async def get_news(force: bool = Query(False, description="是否跳过缓存强制回源")) -> NewsResponse:
    """获取首页资讯列表。"""

    try:
        return await fetch_news(force=force)
    except NewsSourceError as exc:
        logger.warning(f"获取首页资讯失败: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
