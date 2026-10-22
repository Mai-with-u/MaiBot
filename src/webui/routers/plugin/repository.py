from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException

from src.common.logger import get_logger
from src.config.config import MMC_VERSION
from src.webui.services.git_mirror_service import get_git_mirror_service

from .schemas import (
    CloneRepositoryRequest,
    CloneRepositoryResponse,
    GitStatusResponse,
    VersionResponse,
)
from .support import get_plugins_dir, parse_version, require_plugin_token, validate_safe_path

logger = get_logger("webui.plugin_routes")

router = APIRouter()


@router.get("/version", response_model=VersionResponse)
def get_maimai_version() -> VersionResponse:
    major, minor, patch = parse_version(MMC_VERSION)
    return VersionResponse(version=MMC_VERSION, version_major=major, version_minor=minor, version_patch=patch)


@router.get("/git-status", response_model=GitStatusResponse)
def check_git_status() -> GitStatusResponse:
    service = get_git_mirror_service()
    return GitStatusResponse(**service.check_git_installed())


@router.post("/clone", response_model=CloneRepositoryResponse)
async def clone_repository(
    request: CloneRepositoryRequest,
    maibot_session: Optional[str] = Cookie(None),
) -> CloneRepositoryResponse:
    require_plugin_token(maibot_session)
    logger.info(f"收到克隆仓库请求: {request.owner}/{request.repo} -> {request.target_path}")

    try:
        target_path = validate_safe_path(request.target_path, get_plugins_dir())
        service = get_git_mirror_service()
        result = await service.clone_repository(
            owner=request.owner,
            repo=request.repo,
            target_path=target_path,
            branch=request.branch,
            mirror_id=request.mirror_id,
            custom_url=request.custom_url,
            depth=request.depth,
        )
        return CloneRepositoryResponse(**result)
    except Exception as e:
        logger.error(f"克隆仓库失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e
