from fastapi import APIRouter

from src.webui.services.git_mirror_service import set_update_progress_callback

from .repository import router as repository_router
from .config_routes import router as config_router
from .icon_routes import router as icon_router
from .management import router as management_router
from .marketplace import router as marketplace_router
from .progress import get_progress_router, update_progress
from .runtime_routes import router as runtime_router
from .webui_extensions import router as webui_extensions_router
from .zip_install import router as zip_install_router

router = APIRouter(prefix="/plugins", tags=["插件管理"])
router.include_router(repository_router)
router.include_router(management_router)
router.include_router(marketplace_router)
router.include_router(icon_router)
router.include_router(config_router)
router.include_router(runtime_router)
router.include_router(webui_extensions_router)
router.include_router(zip_install_router)

set_update_progress_callback(update_progress)

__all__ = ["get_progress_router", "router"]
