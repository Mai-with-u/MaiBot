"""WebUI 全局 AI 搜索路由。"""

from collections import OrderedDict
from hashlib import sha256
from typing import List, Tuple
import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.services.llm_service import LLMServiceClient
from src.webui.dependencies import require_auth

logger = get_logger("webui.ai_search")

router = APIRouter(prefix="/search", tags=["Search"], dependencies=[Depends(require_auth)])

AI_SEARCH_MAX_CANDIDATES = 600
AI_SEARCH_MAX_RESULTS = 6
AI_SEARCH_TIMEOUT_SECONDS = 15.0
AI_SEARCH_CACHE_TTL_SECONDS = 300.0
AI_SEARCH_CACHE_MAX_ENTRIES = 128


class AISearchCandidate(BaseModel):
    """由 WebUI 真实搜索索引提供的候选项。"""

    id: str = Field(..., min_length=1, max_length=180)
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=240)
    category: str = Field(default="", max_length=80)


class AISearchRequest(BaseModel):
    """AI 搜索请求。"""

    query: str = Field(..., min_length=1, max_length=500)
    language: str = Field(default="zh-CN", max_length=16)
    candidates: List[AISearchCandidate] = Field(..., min_length=1, max_length=AI_SEARCH_MAX_CANDIDATES)


class AISearchModelResult(BaseModel):
    """模型选择的单个搜索候选。"""

    id: str = Field(..., min_length=1, max_length=180)
    score: float = Field(default=0.5, ge=0, le=1)
    reason: str = Field(default="", max_length=160)


class AISearchModelOutput(BaseModel):
    """模型必须返回的结构化搜索结果。"""

    expanded_terms: List[str] = Field(default_factory=list, max_length=10)
    results: List[AISearchModelResult] = Field(default_factory=list, max_length=AI_SEARCH_MAX_RESULTS)


class AISearchResponse(BaseModel):
    """经过候选 ID 校验后的 AI 搜索响应。"""

    success: bool = True
    cached: bool = False
    model_name: str = ""
    expanded_terms: List[str] = Field(default_factory=list)
    results: List[AISearchModelResult] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


_AI_SEARCH_CACHE: "OrderedDict[str, Tuple[float, AISearchResponse]]" = OrderedDict()
_ai_search_model: LLMServiceClient | None = None


def _get_ai_search_model() -> LLMServiceClient:
    """延迟创建 utils 模型客户端，避免路由导入阶段绑定未就绪配置。"""

    global _ai_search_model
    if _ai_search_model is None:
        _ai_search_model = LLMServiceClient(task_name="utils", request_type="webui.ai_search")
    return _ai_search_model


def _resolve_prompt_locale(language: str) -> str:
    """把 WebUI 语言代码映射到现有 Prompt 语言目录。"""

    normalized_language = language.strip().lower().replace("_", "-")
    if normalized_language.startswith("en"):
        return "en-US"
    if normalized_language.startswith("ja"):
        return "ja-JP"
    return "zh-CN"


def _build_candidate_catalog(candidates: List[AISearchCandidate]) -> str:
    """生成紧凑候选目录，模型看不到 URL 或配置值。"""

    catalog = [[candidate.id, candidate.title, candidate.category, candidate.description] for candidate in candidates]
    return json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))


def _build_ai_search_prompt(request: AISearchRequest) -> str:
    """按界面语言构造 AI 搜索 Prompt。"""

    return load_prompt(
        "webui_ai_search",
        locale=_resolve_prompt_locale(request.language),
        query_json=json.dumps(request.query.strip(), ensure_ascii=False),
        candidate_catalog=_build_candidate_catalog(request.candidates),
    )


def _extract_model_output(raw_response: str) -> AISearchModelOutput:
    """解析结构化响应，同时兼容被 Markdown 代码块包裹的 JSON。"""

    normalized_response = raw_response.strip()
    try:
        return AISearchModelOutput.model_validate_json(normalized_response)
    except ValidationError as first_error:
        start_index = normalized_response.find("{")
        end_index = normalized_response.rfind("}")
        if start_index < 0 or end_index <= start_index:
            raise ValueError("模型没有返回可解析的 JSON 对象") from first_error

        try:
            return AISearchModelOutput.model_validate_json(normalized_response[start_index : end_index + 1])
        except ValidationError as second_error:
            raise ValueError("模型返回的 AI 搜索结果结构无效") from second_error


def _normalize_model_output(
    model_output: AISearchModelOutput,
    candidates: List[AISearchCandidate],
) -> AISearchModelOutput:
    """仅保留本次真实候选集中的唯一 ID。"""

    candidate_ids = {candidate.id for candidate in candidates}
    seen_ids: set[str] = set()
    results: List[AISearchModelResult] = []
    for result in model_output.results:
        if result.id not in candidate_ids or result.id in seen_ids:
            continue
        seen_ids.add(result.id)
        results.append(result)
        if len(results) >= AI_SEARCH_MAX_RESULTS:
            break

    expanded_terms: List[str] = []
    seen_terms: set[str] = set()
    for term in model_output.expanded_terms:
        normalized_term = term.strip()
        if not normalized_term or normalized_term in seen_terms:
            continue
        seen_terms.add(normalized_term)
        expanded_terms.append(normalized_term[:80])
        if len(expanded_terms) >= 10:
            break

    return AISearchModelOutput(expanded_terms=expanded_terms, results=results)


def _build_cache_key(request: AISearchRequest) -> str:
    """缓存键同时包含问题、语言和候选目录，避免 schema 更新后复用旧结果。"""

    payload = {
        "query": request.query.strip(),
        "language": _resolve_prompt_locale(request.language),
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


@router.post("/ai", response_model=AISearchResponse)
async def search_with_ai(request: AISearchRequest) -> AISearchResponse:
    """使用 utils 模型在 WebUI 提供的真实候选索引中选择结果。"""

    _validate_candidate_ids(request.candidates)
    cache_key = _build_cache_key(request)
    cached_response = _get_cached_response(cache_key)
    if cached_response is not None:
        return cached_response

    prompt = _build_ai_search_prompt(request)
    try:
        generation_result = await asyncio.wait_for(
            _get_ai_search_model().generate_response(
                prompt,
                options=LLMGenerationOptions(
                    temperature=0,
                    max_tokens=512,
                    response_format=RespFormat(
                        format_type=RespFormatType.JSON_SCHEMA,
                        schema=AISearchModelOutput,
                    ),
                ),
            ),
            timeout=AI_SEARCH_TIMEOUT_SECONDS,
        )
        model_output = _normalize_model_output(
            _extract_model_output(generation_result.response),
            request.candidates,
        )
    except asyncio.TimeoutError as exc:
        logger.warning(f"WebUI AI 搜索超时: query={request.query[:80]}")
        raise HTTPException(status_code=504, detail="AI 搜索超时，请稍后重试") from exc
    except (ValueError, ValidationError) as exc:
        logger.error(f"WebUI AI 搜索响应解析失败: {exc}")
        raise HTTPException(status_code=502, detail=f"AI 搜索结果解析失败: {str(exc)}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"WebUI AI 搜索调用失败: {exc}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"AI 搜索调用失败: {str(exc)}") from exc

    response = AISearchResponse(
        model_name=generation_result.model_name,
        expanded_terms=model_output.expanded_terms,
        results=model_output.results,
        prompt_tokens=generation_result.prompt_tokens,
        completion_tokens=generation_result.completion_tokens,
        total_tokens=generation_result.total_tokens,
    )
    _cache_response(cache_key, response)
    return response
