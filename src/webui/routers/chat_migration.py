"""聊天数据迁移 API；执行任务独立于浏览器连接。"""

from typing import Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.common.runtime_loop import run_on_main_loop
from src.services import chat_migration_service as service


router = APIRouter(prefix="/chat-migrations", tags=["Chat migrations"])


class PreviewRequest(BaseModel):
    source: str = Field(min_length=1, max_length=255)
    target: str = Field(min_length=1, max_length=255)


class ExecuteRequest(BaseModel):
    decisions: Dict[str, str]
    confirm_memory: bool = False


@router.post("/preview")
async def preview(request: PreviewRequest):
    try:
        return await service.preview(request.source, request.target)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("")
def list_jobs():
    service.store.initialize()
    return {"items": service.store.list()}


@router.get("/{job_id}")
def get_job(job_id: str):
    try:
        return service.public_job(service.store.get(job_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{job_id}/execute")
async def execute(job_id: str, request: ExecuteRequest):
    try:
        return await service.start(job_id, request.decisions, request.confirm_memory)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{job_id}/recover")
async def recover(job_id: str):
    try:
        return await run_on_main_loop(service.recover(job_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
