from typing import Any, Optional

import json

from fastapi import APIRouter, Cookie, HTTPException

from src.common.logger import get_logger
from src.config.config import MMC_VERSION
from src.webui.services.git_mirror_service import get_git_mirror_service

from .progress import update_progress
from .schemas import (
    AddMirrorRequest,
    AvailableMirrorsResponse,
    CloneRepositoryRequest,
    CloneRepositoryResponse,
    FetchRawFileRequest,
    FetchRawFileResponse,
    GitStatusResponse,
    MirrorConfigResponse,
    UpdateMirrorRequest,
    VersionResponse,
)
from .support import get_plugins_dir, parse_version, require_plugin_token, validate_safe_path

logger = get_logger("webui.plugin_routes")

router = APIRouter()


def _mirror_to_response(mirror: dict[str, Any]) -> MirrorConfigResponse:
    return MirrorConfigResponse(
        id=mirror["id"],
        name=mirror["name"],
        raw_prefix=mirror["raw_prefix"],
        clone_prefix=mirror["clone_prefix"],
        enabled=mirror["enabled"],
        priority=mirror["priority"],
    )


@router.get("/version", response_model=VersionResponse)
async def get_maimai_version() -> VersionResponse:
    major, minor, patch = parse_version(MMC_VERSION)
    return VersionResponse(version=MMC_VERSION, version_major=major, version_minor=minor, version_patch=patch)


@router.get("/git-status", response_model=GitStatusResponse)
async def check_git_status() -> GitStatusResponse:
    service = get_git_mirror_service()
    return GitStatusResponse(**service.check_git_installed())


@router.get("/mirrors", response_model=AvailableMirrorsResponse)
async def get_available_mirrors(maibot_session: Optional[str] = Cookie(None)) -> AvailableMirrorsResponse:
    require_plugin_token(maibot_session)

    service = get_git_mirror_service()
    config = service.get_mirror_config()
    mirrors = [_mirror_to_response(mirror) for mirror in config.get_all_mirrors()]
    return AvailableMirrorsResponse(mirrors=mirrors, default_priority=config.get_default_priority_list())


@router.post("/mirrors", response_model=MirrorConfigResponse)
async def add_mirror(request: AddMirrorRequest, maibot_session: Optional[str] = Cookie(None)) -> MirrorConfigResponse:
    require_plugin_token(maibot_session)

    try:
        service = get_git_mirror_service()
        config = service.get_mirror_config()
        mirror = config.add_mirror(
            mirror_id=request.id,
            name=request.name,
            raw_prefix=request.raw_prefix,
            clone_prefix=request.clone_prefix,
            enabled=request.enabled,
            priority=request.priority,
        )
        return _mirror_to_response(mirror)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"添加镜像源失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.put("/mirrors/{mirror_id}", response_model=MirrorConfigResponse)
async def update_mirror(
    mirror_id: str,
    request: UpdateMirrorRequest,
    maibot_session: Optional[str] = Cookie(None),
) -> MirrorConfigResponse:
    require_plugin_token(maibot_session)

    try:
        service = get_git_mirror_service()
        config = service.get_mirror_config()
        mirror = config.update_mirror(
            mirror_id=mirror_id,
            name=request.name,
            raw_prefix=request.raw_prefix,
            clone_prefix=request.clone_prefix,
            enabled=request.enabled,
            priority=request.priority,
        )
        if mirror is None:
            raise HTTPException(status_code=404, detail=f"未找到镜像源: {mirror_id}")
        return _mirror_to_response(mirror)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新镜像源失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.delete("/mirrors/{mirror_id}")
async def delete_mirror(mirror_id: str, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)

    service = get_git_mirror_service()
    config = service.get_mirror_config()
    if not config.delete_mirror(mirror_id):
        raise HTTPException(status_code=404, detail=f"未找到镜像源: {mirror_id}")
    return {"success": True, "message": f"已删除镜像源: {mirror_id}"}


@router.post("/fetch-raw", response_model=FetchRawFileResponse)
async def fetch_raw_file(
    request: FetchRawFileRequest,
    maibot_session: Optional[str] = Cookie(None),
) -> FetchRawFileResponse:
    require_plugin_token(maibot_session)
    logger.info(f"收到获取 Raw 文件请求: {request.owner}/{request.repo}/{request.branch}/{request.file_path}")

    await update_progress(
        stage="loading",
        progress=10,
        message=f"正在获取插件列表: {request.file_path}",
        total_plugins=0,
        loaded_plugins=0,
    )

    try:
        service = get_git_mirror_service()
        result = await service.fetch_raw_file(
            owner=request.owner,
            repo=request.repo,
            branch=request.branch,
            file_path=request.file_path,
            mirror_id=request.mirror_id,
            custom_url=request.custom_url,
        )

        if result.get("success"):
            await update_progress(
                stage="loading",
                progress=70,
                message="正在解析插件数据...",
                total_plugins=0,
                loaded_plugins=0,
            )
            try:
                data = json.loads(result.get("data", "[]"))
                total = len(data) if isinstance(data, list) else 0
                await update_progress(
                    stage="success",
                    progress=100,
                    message=f"成功加载 {total} 个插件",
                    total_plugins=total,
                    loaded_plugins=total,
                )
            except Exception:
                await update_progress(stage="success", progress=100, message="加载完成", total_plugins=0, loaded_plugins=0)

        return FetchRawFileResponse(**result)
    except Exception as e:
        logger.error(f"获取 Raw 文件失败: {e}")
        await update_progress(stage="error", progress=0, message="加载失败", error=str(e), total_plugins=0, loaded_plugins=0)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


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