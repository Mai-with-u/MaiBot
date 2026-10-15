"""
主程序自更新路由

WebUI 只负责检查更新与登记更新请求；代码与依赖的实际更新由 Runner 在重启间隙执行。
"""

from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import asyncio

from src.common.logger import get_logger
from src.common.self_update import (
    OFFICIAL_REPOSITORY_URL,
    SelfUpdateError,
    UpdateCheck,
    check_for_update,
    clear_pending_update,
    evaluate_update,
    inspect_local,
    read_last_result,
    request_update,
)
from src.webui.dependencies import require_auth
from src.webui.routers.system import schedule_restart
from src.webui.services.git_mirror_service import get_git_mirror_service

router = APIRouter(prefix="/system/self-update", tags=["system"], dependencies=[Depends(require_auth)])
logger = get_logger("webui_self_update")

# 最近一次联网检查的结论；状态接口据此展示新版本信息而不必每次联网。
_last_check: Optional[UpdateCheck] = None


class SelfUpdateCommitResponse(BaseModel):
    """待更新的单个提交。"""

    short_id: str
    summary: str
    committed_at: str


class SelfUpdateResultResponse(BaseModel):
    """最近一次更新的执行结果。"""

    success: bool
    started_at: float
    finished_at: float
    branch: str
    from_commit: str
    to_commit: str
    from_version: str
    to_version: str
    stage: str
    error_code: str
    error: str
    rolled_back: bool
    rollback_error: str
    dependencies_synced: bool
    source_name: str


class SelfUpdateStatusResponse(BaseModel):
    """自更新状态。"""

    supported: bool
    deployment_type: str = "git"
    repository_url: str
    current_version: str
    current_commit: Optional[str] = None
    branch: Optional[str] = None
    checked_at: Optional[float] = None
    latest_version: Optional[str] = None
    latest_commit: Optional[str] = None
    behind: int = 0
    ahead: int = 0
    update_available: bool = False
    can_apply: bool = False
    blockers: List[str] = Field(default_factory=list)
    dirty_files: List[str] = Field(default_factory=list)
    dependencies_changed: bool = False
    dependency_tool: Optional[str] = None
    commits: List[SelfUpdateCommitResponse] = Field(default_factory=list)
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    last_result: Optional[SelfUpdateResultResponse] = None


class SelfUpdateApplyRequest(BaseModel):
    """执行更新请求。"""

    expected_commit: str = Field(description="界面确认的更新目标：源码版为提交 SHA，包安装版为正式版本号")


class SelfUpdateApplyResponse(BaseModel):
    """执行更新响应。"""

    success: bool
    message: str


def _build_status_response(check: UpdateCheck) -> SelfUpdateStatusResponse:
    local = check.local
    last_result = read_last_result()
    return SelfUpdateStatusResponse(
        supported=local.blocker is None,
        deployment_type=local.deployment_type,
        repository_url="https://pypi.org/project/maibot/" if local.deployment_type == "package" else OFFICIAL_REPOSITORY_URL,
        current_version=local.version,
        current_commit=local.commit,
        branch=local.branch,
        checked_at=check.checked_at,
        latest_version=check.latest_version,
        latest_commit=check.latest_commit,
        behind=check.behind,
        ahead=check.ahead,
        update_available=check.update_available,
        can_apply=check.can_apply,
        blockers=check.blockers,
        dirty_files=local.dirty_files,
        dependencies_changed=check.dependencies_changed,
        dependency_tool=local.dependency_tool,
        commits=[SelfUpdateCommitResponse(**asdict(commit)) for commit in check.commits],
        source_id=check.source.id if check.source is not None else None,
        source_name=check.source.name if check.source is not None else None,
        last_result=SelfUpdateResultResponse(**asdict(last_result)) if last_result is not None else None,
    )


@router.get("/status", response_model=SelfUpdateStatusResponse)
def get_self_update_status() -> SelfUpdateStatusResponse:
    """获取自更新状态；只读取本地仓库，不联网。"""

    try:
        local = inspect_local()
        if local.blocker is not None:
            return _build_status_response(UpdateCheck(local=local, blockers=[local.blocker]))

        cached = _last_check
        if (
            cached is None
            or cached.latest_commit is None
            or cached.checked_at is None
            or cached.local.branch != local.branch
        ):
            return _build_status_response(UpdateCheck(local=local))
        # 本地状态可能在检查之后变化（如还原了本地改动），用最新的本地状态重新评估。
        check = evaluate_update(local, cached.latest_commit, cached.checked_at)
        check.source = cached.source
        return _build_status_response(check)
    except SelfUpdateError as e:
        logger.exception(f"获取自更新状态失败: {e.message}")
        raise HTTPException(status_code=500, detail=e.message) from e


@router.post("/check", response_model=SelfUpdateStatusResponse)
def check_self_update() -> SelfUpdateStatusResponse:
    """联网检查官方仓库是否有新版本。"""

    global _last_check

    # 源码更新与插件市场共用镜像；包安装只访问 PyPI。
    if inspect_local().deployment_type == "git":
        get_git_mirror_service()
    try:
        check = check_for_update()
    except SelfUpdateError as e:
        logger.warning(f"检查更新失败: {e.message}")
        status_code = 502 if e.code == "fetch_failed" else 500
        raise HTTPException(status_code=status_code, detail=e.message) from e

    _last_check = check
    return _build_status_response(check)


@router.post("/apply", response_model=SelfUpdateApplyResponse)
async def apply_self_update(request: SelfUpdateApplyRequest) -> SelfUpdateApplyResponse:
    """登记更新请求并重启；Runner 会在重启间隙完成更新。"""

    try:
        pending = await asyncio.to_thread(request_update, request.expected_commit)
    except SelfUpdateError as e:
        logger.warning(f"登记更新请求失败: {e.message}")
        raise HTTPException(status_code=409, detail=e.message) from e

    logger.info(f"WebUI 触发自更新: {pending.base_commit[:10]} -> {pending.target_commit[:10]}")
    try:
        schedule_restart()
    except Exception:
        # 重启没有排上时撤回请求，避免之后的普通重启意外执行更新。
        clear_pending_update()
        raise

    return SelfUpdateApplyResponse(success=True, message="麦麦正在更新并重启...")
