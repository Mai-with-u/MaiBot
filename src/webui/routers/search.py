"""WebUI 全局 AI 搜索路由。"""

from collections import OrderedDict
from hashlib import sha256
from typing import Any, AsyncIterator, Dict, List, Tuple
from uuid import uuid4
import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from src.common.logger import get_logger
from src.webui.dependencies import require_auth
from src.webui.services.ai_search_agent import resolve_prompt_locale, run_ai_search_agent
from src.webui.services.ai_search_models import (
    AISearchCandidate,
    AISearchOutputError,
    AISearchProgressCallback,
    AISearchProgressEvent,
    AISearchRequest,
    AISearchResponse,
)

logger = get_logger("webui.ai_search")

router = APIRouter(prefix="/search", tags=["Search"], dependencies=[Depends(require_auth)])

AI_SEARCH_CACHE_TTL_SECONDS = 300.0
AI_SEARCH_CACHE_MAX_ENTRIES = 128

_AI_SEARCH_CACHE: "OrderedDict[str, Tuple[float, AISearchResponse]]" = OrderedDict()


def _build_cache_key(request: AISearchRequest) -> str:
    """缓存键同时包含问题、语言和候选目录，避免 schema 更新后复用旧结果。"""

    payload = {
        "query": request.query.strip(),
        "language": resolve_prompt_locale(request.language),
        "candidates": [candidate.model_dump() for candidate in request.candidates],
    }
    serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(serialized_payload.encode("utf-8")).hexdigest()


def _get_cached_response(cache_key: str) -> AISearchResponse | None:
    """读取未过期缓存并提升其 LRU 顺序。"""

    cached_entry = _AI_SEARCH_CACHE.get(cache_key)
    if cached_entry is None:
        return None

    expires_at, response = cached_entry
    if expires_at <= time.monotonic():
        del _AI_SEARCH_CACHE[cache_key]
        return None

    _AI_SEARCH_CACHE.move_to_end(cache_key)
    return response.model_copy(update={"cached": True}, deep=True)


def _cache_response(cache_key: str, response: AISearchResponse) -> None:
    """写入有界 TTL/LRU 缓存。"""

    _AI_SEARCH_CACHE[cache_key] = (
        time.monotonic() + AI_SEARCH_CACHE_TTL_SECONDS,
        response.model_copy(deep=True),
    )
    _AI_SEARCH_CACHE.move_to_end(cache_key)
    while len(_AI_SEARCH_CACHE) > AI_SEARCH_CACHE_MAX_ENTRIES:
        _AI_SEARCH_CACHE.popitem(last=False)


def _validate_candidate_ids(candidates: List[AISearchCandidate]) -> None:
    """拒绝重复 ID，确保模型结果可以无歧义映射回前端索引。"""

    candidate_ids = [candidate.id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise HTTPException(status_code=400, detail="AI 搜索候选项包含重复 ID")


def _compact_progress_for_log(event: AISearchProgressEvent) -> Dict[str, Any]:
    """移除进度事件中的空字段，控制检索日志体积。"""

    payload = event.model_dump(exclude={"type"}, exclude_none=True)
    return {key: value for key, value in payload.items() if value not in ("", [])}


def _log_ai_search_record(
    *,
    request: AISearchRequest,
    search_id: str,
    started_at: float,
    status: str,
    progress: List[Dict[str, Any]],
    response: AISearchResponse | None = None,
    error: str = "",
) -> None:
    """写入可随主日志轮转和过期清理的 AI 搜索记录。"""

    record: Dict[str, Any] = {
        "search_id": search_id,
        "status": status,
        "duration_ms": round((time.monotonic() - started_at) * 1000),
        "query": request.query.strip(),
        "language": resolve_prompt_locale(request.language),
        "candidate_count": len(request.candidates),
        "progress": progress,
    }
    if error:
        record["error"] = error
    if response is not None:
        record.update(
            {
                "cached": response.cached,
                "model_name": response.model_name,
                "result_ids": [result.id for result in response.results],
                "source_urls": [source.url for source in response.sources],
                "total_tokens": response.total_tokens,
            }
        )
    logger.info("WebUI AI 搜索记录", **record)

    if response is not None:
        logger.debug(
            "WebUI AI 搜索回答",
            search_id=search_id,
            answer=response.answer,
            suggestions=response.suggestions,
            expanded_terms=response.expanded_terms,
        )


async def _execute_ai_search_request(
    request: AISearchRequest,
    progress_callback: AISearchProgressCallback,
) -> AISearchResponse:
    """执行一次带进度回调的 AI 搜索。"""

    _validate_candidate_ids(request.candidates)
    search_id = uuid4().hex[:12]
    started_at = time.monotonic()
    progress: List[Dict[str, Any]] = []

    async def record_progress(event: AISearchProgressEvent) -> None:
        progress.append(_compact_progress_for_log(event))
        await progress_callback(event)

    def log_record(status: str, response: AISearchResponse | None = None, error: str = "") -> None:
        _log_ai_search_record(
            request=request,
            search_id=search_id,
            started_at=started_at,
            status=status,
            progress=progress,
            response=response,
            error=error,
        )

    cache_key = _build_cache_key(request)
    cached_response = _get_cached_response(cache_key)
    if cached_response is not None:
        await record_progress(AISearchProgressEvent(stage="cache_hit"))
        log_record("cached", cached_response)
        return cached_response

    try:
        response = await run_ai_search_agent(request, record_progress)
    except asyncio.CancelledError:
        log_record("cancelled")
        raise
    except AISearchOutputError as exc:
        log_record("parse_failed", error=str(exc))
        logger.error("WebUI AI 搜索响应解析失败", search_id=search_id, error=str(exc))
        raise HTTPException(status_code=502, detail=f"AI 搜索结果解析失败: {str(exc)}") from exc
    except Exception as exc:
        log_record("failed", error=str(exc))
        logger.error("WebUI AI 搜索调用失败", search_id=search_id, error=str(exc), exc_info=True)
        raise HTTPException(status_code=502, detail=f"AI 搜索调用失败: {str(exc)}") from exc

    if response.grounding_error:
        # 回答正文被证据校验丢弃的结果不写缓存，让用户可以立即重试
        log_record("grounding_failed", response, response.grounding_error)
        return response

    if not response.used_local_config:
        _cache_response(cache_key, response)
    log_record("completed", response)
    return response


async def _stream_ai_search_events(request: AISearchRequest) -> AsyncIterator[str]:
    """把 AI 搜索过程与最终结果编码为逐行 JSON。"""

    queue: asyncio.Queue[Dict[str, Any] | None] = asyncio.Queue()

    async def publish_progress(event: AISearchProgressEvent) -> None:
        await queue.put(event.model_dump())

    async def run_search() -> None:
        try:
            response = await _execute_ai_search_request(request, publish_progress)
            await publish_progress(AISearchProgressEvent(stage="completed", status="completed"))
            await queue.put({"type": "result", "response": response.model_dump()})
        except HTTPException as exc:
            error_message = str(exc.detail)
            await publish_progress(
                AISearchProgressEvent(stage="failed", status="failed", error=error_message)
            )
            await queue.put({"type": "error", "message": error_message, "status": exc.status_code})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"WebUI AI 搜索流式调用失败: {exc}", exc_info=True)
            error_message = f"AI 搜索失败: {str(exc)}"
            await publish_progress(
                AISearchProgressEvent(stage="failed", status="failed", error=error_message)
            )
            await queue.put({"type": "error", "message": error_message, "status": 500})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run_search())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@router.post("/ai/stream")
def stream_search_with_ai(request: AISearchRequest) -> StreamingResponse:
    """流式返回 AI 搜索的操作过程与最终结果。"""

    return StreamingResponse(
        _stream_ai_search_events(request),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
