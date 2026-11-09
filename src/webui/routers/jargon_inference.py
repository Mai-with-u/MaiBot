"""黑话推断管理：所有任务投递到主循环，数据库管理操作使用同步端点。"""

from datetime import datetime
from typing import Annotated, Any, Awaitable, Dict, List, Literal, Optional, TypeVar

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlmodel import col, delete, select

import asyncio

from src.common.database.database import get_db_session
from src.common.database.database_model import Jargon, JargonCreatedBy, JargonInferenceState, JargonMeaningHistory
from src.common.runtime_loop import run_on_main_loop
from src.config.config import config_manager, global_config
from src.config.official_configs import JargonInferenceConfig

router = APIRouter(prefix="/inference", tags=["Jargon"])
T = TypeVar("T")
PositiveId = Annotated[int, Field(gt=0)]
_settings_lock = asyncio.Lock()


class InferenceFilters(BaseModel):
    """与列表共享筛选语义；显式空会话列表表示空范围。"""

    model_config = ConfigDict(extra="forbid")

    search: Optional[str] = Field(default=None, max_length=255)
    session_id: Optional[str] = None
    session_ids: Optional[List[str]] = Field(default=None, max_length=1000)
    jargon_status: Optional[Literal["confirmed_jargon", "confirmed_not_jargon", "manual_jargon", "pending"]] = None
    is_jargon: Optional[bool] = None
    is_complete: Optional[bool] = None
    is_global: Optional[bool] = None


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: Optional[List[PositiveId]] = Field(default=None, min_length=1, max_length=5000)
    filters: InferenceFilters = Field(default_factory=InferenceFilters)
    mode: Literal["missing", "all"] = "missing"
    force: bool = True
    last_status: Optional[
        Literal[
            "unattempted",
            "queued",
            "running",
            "succeeded",
            "insufficient_info",
            "missing_evidence",
            "failed",
            "protected",
            "stale",
            "missing",
            "cancelled",
            "interrupted",
            "skipped",
        ]
    ] = None


class InferenceLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool


def get_service() -> Any:
    """延迟导入，防止任务服务的列表筛选依赖形成循环。"""
    from src.learners.jargon_inference_service import get_jargon_inference_service

    return get_jargon_inference_service()


async def _run_task_operation(operation: Awaitable[T]) -> T:
    try:
        return await run_on_main_loop(operation)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/settings")
def get_inference_settings() -> Dict[str, Any]:
    return global_config.jargon.inference.model_dump()


async def _save_inference_settings(settings: JargonInferenceConfig) -> Dict[str, Any]:
    """在主循环串行保存和重载，避免并发请求确认了其他请求的配置。"""
    from .config import update_bot_config_section

    async with _settings_lock:
        await asyncio.to_thread(update_bot_config_section, "jargon", {"inference": settings.model_dump()})
        reloaded = await config_manager.reload_config(changed_scopes=["bot"], changed_sections=["jargon"])
        if not reloaded:
            raise HTTPException(status_code=500, detail="配置已保存，但热重载失败，请检查日志后重试加载")
        return get_inference_settings()


@router.put("/settings")
async def update_inference_settings(settings: JargonInferenceConfig) -> Dict[str, Any]:
    """通过统一配置保存路径验证整个配置，再在主循环热重载。"""
    return await run_on_main_loop(_save_inference_settings(settings))


@router.post("/preview")
async def preview_inference(request: InferenceRequest) -> Dict[str, Any]:
    return await _run_task_operation(get_service().preview(request.model_dump(exclude_none=True)))


@router.post("/jobs")
async def submit_inference(request: InferenceRequest) -> Dict[str, Any]:
    return await _run_task_operation(get_service().submit(request.model_dump(exclude_none=True), source="manual"))


@router.get("/jobs")
async def list_inference_jobs() -> Dict[str, Any]:
    return {"items": await _run_task_operation(get_service().list_jobs())}


@router.get("/jobs/{job_id}")
async def get_inference_job(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> Dict[str, Any]:
    return await _run_task_operation(get_service().get_job(job_id, page, page_size))


@router.post("/jobs/{job_id}/cancel")
async def cancel_inference_job(job_id: str) -> Dict[str, Any]:
    return await _run_task_operation(get_service().cancel(job_id))


@router.post("/jobs/{job_id}/retry")
async def retry_inference_job(job_id: str) -> Dict[str, Any]:
    return await _run_task_operation(get_service().retry(job_id))


@router.post("/rescan")
async def rescan_inference() -> Dict[str, Any]:
    return await _run_task_operation(get_service().rescan())


@router.get("/history/{jargon_id}")
def get_inference_history(jargon_id: int) -> Dict[str, Any]:
    with get_db_session() as session:
        if session.get(Jargon, jargon_id) is None:
            raise HTTPException(status_code=404, detail="黑话不存在")
        history = session.exec(
            select(JargonMeaningHistory)
            .where(col(JargonMeaningHistory.jargon_id) == jargon_id)
            .order_by(col(JargonMeaningHistory.id).desc())
            .limit(global_config.jargon.inference.history_limit)
        ).all()
        return {"items": [item.model_dump() for item in history]}


@router.post("/history/{history_id}/restore")
def restore_inference_history(history_id: int) -> Dict[str, bool]:
    """恢复该条变更前的含义及分类，并转为人工维护，防止排队任务覆盖。"""
    with get_db_session() as session:
        session.connection().execute(text("BEGIN IMMEDIATE"))
        history = session.get(JargonMeaningHistory, history_id)
        if history is None:
            raise HTTPException(status_code=404, detail="含义历史不存在")
        jargon = session.get(Jargon, history.jargon_id)
        if jargon is None:
            raise HTTPException(status_code=404, detail="黑话不存在")
        restored = JargonMeaningHistory(
            jargon_id=history.jargon_id,
            source="restore",
            previous_meaning=jargon.meaning,
            meaning=history.previous_meaning,
            previous_is_jargon=jargon.is_jargon,
            is_jargon=history.previous_is_jargon,
        )
        jargon.meaning = history.previous_meaning
        jargon.is_jargon = history.previous_is_jargon
        jargon.created_by = JargonCreatedBy.MANUAL
        jargon.updated_timestamp = datetime.now()
        state = session.get(JargonInferenceState, history.jargon_id) or JargonInferenceState(
            jargon_id=history.jargon_id
        )
        state.locked = True
        session.add(jargon)
        session.add(state)
        session.add(restored)
        session.flush()
        expired_ids = session.exec(
            select(JargonMeaningHistory.id)
            .where(col(JargonMeaningHistory.jargon_id) == history.jargon_id)
            .order_by(col(JargonMeaningHistory.id).desc())
            .offset(global_config.jargon.inference.history_limit)
        ).all()
        if expired_ids:
            session.exec(delete(JargonMeaningHistory).where(col(JargonMeaningHistory.id).in_(expired_ids)))
    return {"success": True}


@router.post("/lock/{jargon_id}")
def set_inference_lock(jargon_id: int, request: InferenceLockRequest) -> Dict[str, bool]:
    with get_db_session() as session:
        session.connection().execute(text("BEGIN IMMEDIATE"))
        if session.get(Jargon, jargon_id) is None:
            raise HTTPException(status_code=404, detail="黑话不存在")
        state = session.get(JargonInferenceState, jargon_id) or JargonInferenceState(jargon_id=jargon_id)
        state.locked = request.locked
        session.add(state)
    return {"success": True}
