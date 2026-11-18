"""表达与黑话的长文学习接口。分段请求无入库副作用，可按失败位置继续。"""

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.common.logger import get_logger
from src.common.runtime_loop import run_on_main_loop
from src.webui.services.text_learning import (
    CHUNK_LENGTH,
    MAX_SOURCE_LENGTH,
    LearnedCandidate,
    LearningKind,
    extract_text_candidates,
    split_learning_text,
)

router = APIRouter(prefix="/learning/text", tags=["TextLearning"])
logger = get_logger("webui.text_learning")


class PrepareTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH)


class PrepareTextResponse(BaseModel):
    chunks: List[str]


class ExtractTextRequest(BaseModel):
    kind: LearningKind
    text: str = Field(min_length=1, max_length=CHUNK_LENGTH)


class ExtractTextResponse(BaseModel):
    candidates: List[LearnedCandidate]


@router.post("/prepare", response_model=PrepareTextResponse)
def prepare_text(request: PrepareTextRequest) -> PrepareTextResponse:
    try:
        return PrepareTextResponse(chunks=split_learning_text(request.text))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/extract", response_model=ExtractTextResponse)
async def extract_text(request: ExtractTextRequest) -> ExtractTextResponse:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="学习文段不能为空")
    try:
        candidates = await run_on_main_loop(extract_text_candidates(request.text, request.kind))
        return ExtractTextResponse(candidates=candidates)
    except Exception as exc:
        logger.exception(f"长文学习失败: kind={request.kind}")
        raise HTTPException(status_code=502, detail=f"学习失败：{exc}") from exc
