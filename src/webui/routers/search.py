"""WebUI 全局 AI 搜索路由。"""

from collections import OrderedDict
from contextlib import contextmanager, suppress
from hashlib import sha256
from typing import Any, AsyncIterator, Callable, Coroutine, Dict, Iterator, List, Sequence, Set, Tuple, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

import asyncio
import json
import time

from src.common.database.database_model import WebUIHelpSession
from src.common.logger import get_logger
from src.webui.services.ai_help_history import (
    HelpSessionDetail,
    begin_help_turn,
    delete_help_session,
    finish_help_turn,
    get_help_session,
    list_help_sessions,
)
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

router = APIRouter(prefix="/search", tags=["Search"])

AI_SEARCH_CACHE_TTL_SECONDS = 300.0
AI_SEARCH_CACHE_MAX_ENTRIES = 128

_AI_SEARCH_CACHE: "OrderedDict[str, Tuple[float, AISearchResponse]]" = OrderedDict()
_ACTIVE_HELP_SESSIONS: Set[str] = set()
_WriteResult = TypeVar("_WriteResult")


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

    payload = event.model_dump(exclude={"type", "plan"}, exclude_none=True)
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
    *,
    history: Sequence[Tuple[str, str]] = (),
) -> AISearchResponse:
    """执行一次带进度回调的 AI 搜索。"""

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

    cache_key = (
        await asyncio.to_thread(_build_cache_key, request)
        if request.help_session_id is None and not request.allow_plugin_config_write
        else None
    )
    cached_response = _get_cached_response(cache_key) if cache_key is not None else None
    if cached_response is not None:
        await record_progress(AISearchProgressEvent(stage="cache_hit"))
        log_record("cached", cached_response)
        return cached_response

    try:
        response = await run_ai_search_agent(request, record_progress, history=history)
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

    if cache_key is not None and not response.used_local_config and not response.used_plugin_market:
        _cache_response(cache_key, response)
    log_record("completed", response)
    return response


@contextmanager
def _reserve_help_session(session_id: str) -> Iterator[None]:
    if session_id in _ACTIVE_HELP_SESSIONS:
        raise HTTPException(status_code=409, detail="该答疑会话正在处理，请稍后重试")
    _ACTIVE_HELP_SESSIONS.add(session_id)
    try:
        yield
    finally:
        _ACTIVE_HELP_SESSIONS.remove(session_id)


async def _complete_help_write(operation: Coroutine[Any, Any, _WriteResult]) -> _WriteResult:
    """写线程无法撤回，确保它收尾后才释放会话占用。"""

    write = asyncio.create_task(operation)
    try:
        return await asyncio.shield(write)
    except asyncio.CancelledError:
        await write
        raise


async def _persist_help_turn(turn_id: int, *, response: AISearchResponse | None = None, error: str = "") -> None:
    await _complete_help_write(asyncio.to_thread(finish_help_turn, turn_id, response=response, error=error))


def _stream_error(exc: Exception) -> Tuple[str, int]:
    if isinstance(exc, HTTPException):
        return str(exc.detail), exc.status_code
    return f"AI 搜索失败: {exc}", 500


async def _run_help_session(
    request: AISearchRequest,
    progress_callback: AISearchProgressCallback,
    publish_session: Callable[[str], None],
) -> AISearchResponse:
    """接管单轮答疑，从登记问题到保存结果期间持有会话占用。"""

    new_session = request.help_session_id == "new"
    session_id = str(uuid4() if new_session else request.help_session_id)
    with _reserve_help_session(session_id):
        opening = asyncio.create_task(
            asyncio.to_thread(begin_help_turn, session_id, request.query.strip(), new_session=new_session)
        )
        try:
            turn = await asyncio.shield(opening)
        except asyncio.CancelledError:
            # 断开时登记事务可能已经提交，等待确认后再结束这轮答疑。
            turn = await opening
            if turn is not None:
                await _persist_help_turn(turn[0], error="答疑已中断")
            raise
        if turn is None:
            raise HTTPException(status_code=404, detail="答疑会话不存在")

        turn_id, history = turn
        publish_session(session_id)
        try:
            response = await _execute_ai_search_request(request, progress_callback, history=history)
        except asyncio.CancelledError:
            await _persist_help_turn(turn_id, error="答疑已中断")
            raise
        except Exception as exc:
            error_message, _ = _stream_error(exc)
            await _persist_help_turn(turn_id, error=error_message)
            raise
        await _persist_help_turn(turn_id, response=response)
        return response


async def _stream_ai_search_events(request: AISearchRequest) -> AsyncIterator[str]:
    """把 AI 搜索过程与最终结果编码为逐行 JSON。"""

    queue: asyncio.Queue[Dict[str, Any] | None] = asyncio.Queue()

    async def publish_progress(event: AISearchProgressEvent) -> None:
        queue.put_nowait(event.model_dump())

    def publish_session(session_id: str) -> None:
        queue.put_nowait({"type": "session", "id": session_id})

    async def run_search() -> None:
        try:
            _validate_candidate_ids(request.candidates)
            if request.help_session_id is None:
                response = await _execute_ai_search_request(request, publish_progress)
            else:
                response = await _run_help_session(request, publish_progress, publish_session)
            await publish_progress(AISearchProgressEvent(stage="completed", status="completed"))
            queue.put_nowait({"type": "result", "response": response.model_dump()})
        except Exception as exc:
            if not isinstance(exc, HTTPException):
                logger.error(f"WebUI AI 搜索流式调用失败: {exc}", exc_info=True)
            error_message, status_code = _stream_error(exc)
            await publish_progress(AISearchProgressEvent(stage="failed", status="failed", error=error_message))
            queue.put_nowait({"type": "error", "message": error_message, "status": status_code})
        finally:
            queue.put_nowait(None)

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
        with suppress(asyncio.CancelledError):
            await asyncio.shield(task)


@router.get("/help/sessions")
def read_help_sessions() -> List[WebUIHelpSession]:
    return list_help_sessions()


@router.get("/help/sessions/{session_id}")
def read_help_session(session_id: UUID) -> HelpSessionDetail:
    session = get_help_session(str(session_id))
    if session is None:
        raise HTTPException(status_code=404, detail="答疑会话不存在")
    return session


@router.delete("/help/sessions/{session_id}", status_code=204)
async def remove_help_session(session_id: UUID) -> Response:
    key = str(session_id)
    with _reserve_help_session(key):
        if not await _complete_help_write(asyncio.to_thread(delete_help_session, key)):
            raise HTTPException(status_code=404, detail="答疑会话不存在")
    return Response(status_code=204)


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
