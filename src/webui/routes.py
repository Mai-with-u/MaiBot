"""WebUI API 路由"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.common.logger import get_logger
from src.webui.routers.chat_migration import router as chat_migration_router
from src.webui.routers.agent import router as agent_router
from src.webui.routers.desktop_pet import router as desktop_pet_router
from src.webui.routers.desktop_pet_remote import router as desktop_pet_remote_router
from src.webui.routers.files import router as files_router
from src.webui.routers.avatar import router as avatar_router
from src.webui.routers.bot_accounts import router as bot_accounts_router
from src.webui.routers.config import router as config_router
from src.webui.routers.emoji import router as emoji_router
from src.webui.routers.expression import router as expression_router
from src.webui.routers.jargon import router as jargon_router
from src.webui.routers.sharing_groups import router as sharing_groups_router
from src.webui.routers.memory import router as memory_router
from src.webui.routers.mcp import router as mcp_router
from src.webui.routers.model import router as model_router
from src.webui.routers.news import router as news_router
from src.webui.routers.person import router as person_router
from src.webui.routers.plugin import router as plugin_router
from src.webui.routers.reasoning_process import router as reasoning_process_router
from src.webui.routers.search import router as search_router
from src.webui.routers.statistics import router as statistics_router
from src.webui.routers.system import router as system_router
from src.webui.routers.text_learning import router as text_learning_router
from src.webui.routers.user_emoji import router as user_emoji_router
from src.webui.routers.websocket.unified import router as unified_ws_router
from src.webui.version_compatibility import (
    WebUICompatibilityStatus,
    get_webui_version_compatibility,
)

logger = get_logger("webui.api")

# 创建路由器
router = APIRouter(prefix="/api/webui", tags=["WebUI"])

# 注册配置管理路由
router.include_router(config_router)
router.include_router(chat_migration_router)
router.include_router(mcp_router)
# 注册统计数据路由
router.include_router(statistics_router)
# 注册人物信息管理路由
router.include_router(person_router)
# 注册表达方式管理路由
router.include_router(expression_router)
# 注册黑话管理路由
router.include_router(jargon_router)
router.include_router(sharing_groups_router)
router.include_router(text_learning_router)
# 注册表情包管理路由
router.include_router(bot_accounts_router)
router.include_router(emoji_router)
router.include_router(avatar_router)
router.include_router(user_emoji_router)
# 注册插件管理路由
router.include_router(plugin_router)
# 注册系统控制路由
router.include_router(system_router)
router.include_router(agent_router)
router.include_router(desktop_pet_router)
router.include_router(desktop_pet_remote_router)
router.include_router(files_router)
router.include_router(reasoning_process_router)
router.include_router(search_router)
# 注册模型列表获取路由
router.include_router(model_router)
# 注册首页资讯路由
router.include_router(news_router)
# 注册长期记忆管理路由
router.include_router(memory_router)
# 注册统一 WebSocket 路由
router.include_router(unified_ws_router)


class VersionCompatibilityResponse(BaseModel):
    """主程序与 WebUI 的版本兼容性。"""

    status: WebUICompatibilityStatus
    main_program_version: str
    webui_version: str
    required_webui_version: str


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "MaiBot WebUI"}


@router.get("/version-compatibility", response_model=VersionCompatibilityResponse)
async def get_version_compatibility(
    webui_version: str = Query(..., min_length=1, max_length=64),
) -> VersionCompatibilityResponse:
    """比较当前 WebUI 版本与主程序在 pyproject.toml 中声明的版本。"""

    try:
        compatibility = get_webui_version_compatibility(webui_version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return VersionCompatibilityResponse(
        status=compatibility.status,
        main_program_version=compatibility.main_program_version,
        webui_version=compatibility.webui_version,
        required_webui_version=compatibility.required_webui_version,
    )
