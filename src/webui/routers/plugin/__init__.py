from fastapi import APIRouter

from src.webui.services.git_mirror_service import set_update_progress_callback

from .catalog import router as catalog_router
from .config_routes import router as config_router
from .icon_routes import router as icon_router
from .management import router as management_router
from .progress import get_progress_router, update_progress
from .runtime_routes import router as runtime_router

router = APIRouter(prefix="/plugins", tags=["插件管理"])
router.include_router(catalog_router)
router.include_router(management_router)
router.include_router(icon_router)
router.include_router(config_router)
router.include_router(runtime_router)

set_update_progress_callback(update_progress)

__all__ = ["get_progress_router", "router"]
