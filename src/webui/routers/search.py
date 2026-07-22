"""WebUI 全局 AI 搜索路由。"""

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, List, Tuple
import asyncio
import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException
import httpx
from pydantic import BaseModel, Field, ValidationError

from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolCall, ToolDefinitionInput
from src.services.llm_service import LLMServiceClient
from src.webui.dependencies import require_auth

logger = get_logger("webui.ai_search")

router = APIRouter(prefix="/search", tags=["Search"], dependencies=[Depends(require_auth)])

AI_SEARCH_MAX_CANDIDATES = 600
AI_SEARCH_MAX_RESULTS = 6
AI_SEARCH_MAX_TOOL_ROUNDS = 4
AI_SEARCH_MAX_TOOL_CALLS_PER_ROUND = 4
AI_SEARCH_TIMEOUT_SECONDS = 45.0
AI_SEARCH_CACHE_TTL_SECONDS = 300.0
AI_SEARCH_CACHE_MAX_ENTRIES = 128
OFFICIAL_DOCS_BUNDLE_URL = "https://docs.mai-mai.org/llms-full.txt"
OFFICIAL_DOCS_BASE_URL = "https://docs.mai-mai.org"
OFFICIAL_DOCS_CACHE_TTL_SECONDS = 600.0
OFFICIAL_DOCS_MAX_BUNDLE_SIZE = 2_000_000
OFFICIAL_DOCS_MAX_READ_SIZE = 8_000


@dataclass(frozen=True, slots=True)
class OfficialDocument:
    """从官方 LLM 文档包解析出的单篇文档。"""

    path: str
    title: str
    content: str


class AISearchCandidate(BaseModel):
    """由 WebUI 真实搜索索引提供的候选项。"""

    id: str = Field(..., min_length=1, max_length=180)
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=240)
    category: str = Field(default="", max_length=80)
    document: str = Field(default="", max_length=2000)


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

    answer: str = Field(default="", max_length=2000)
    suggestions: List[str] = Field(default_factory=list, max_length=6)
    source_ids: List[str] = Field(default_factory=list, max_length=6)
    expanded_terms: List[str] = Field(default_factory=list, max_length=10)
    results: List[AISearchModelResult] = Field(default_factory=list, max_length=AI_SEARCH_MAX_RESULTS)


class AISearchSource(BaseModel):
    """Agent 阅读并引用的官方文档。"""

    title: str
    url: str


class AISearchResponse(BaseModel):
    """经过候选 ID 与官方文档来源校验后的 AI 搜索响应。"""

    success: bool = True
    cached: bool = False
    model_name: str = ""
    answer: str = ""
    suggestions: List[str] = Field(default_factory=list)
    sources: List[AISearchSource] = Field(default_factory=list)
    expanded_terms: List[str] = Field(default_factory=list)
    results: List[AISearchModelResult] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


_AI_SEARCH_CACHE: "OrderedDict[str, Tuple[float, AISearchResponse]]" = OrderedDict()
_ai_search_model: LLMServiceClient | None = None
_official_docs_cache: Tuple[float, List[OfficialDocument]] | None = None
_official_docs_lock = asyncio.Lock()


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


def _build_ai_search_prompt(request: AISearchRequest) -> str:
    """按界面语言构造带只读检索工具说明的 Agent Prompt。"""

    return load_prompt(
        "webui_ai_search",
        locale=_resolve_prompt_locale(request.language),
        query_json=json.dumps(request.query.strip(), ensure_ascii=False),
        candidate_count=len(request.candidates),
    )


def _build_agent_tools() -> List[ToolDefinitionInput]:
    """构造本地 WebUI 索引与官方文档站的只读工具。"""

    return [
        {
            "name": "search_webui_index",
            "description": "搜索当前 WebUI 中可导航的页面和配置项，返回候选 ID 与摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "一个或多个简短检索词，用空格分隔"},
                    "limit": {"type": "integer", "description": "返回数量，范围 1 到 10"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "read_webui_documents",
            "description": "读取 WebUI 搜索结果的配置说明、字段路径、类型和选项信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要读取的文档 ID，最多 6 个",
                    }
                },
                "required": ["ids"],
            },
        },
        {
            "name": "search_official_docs",
            "description": "搜索 docs.mai-mai.org 上的 MaiBot 官方文档，返回文档路径、标题和相关片段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "一个或多个简短检索词，用空格分隔"},
                    "limit": {"type": "integer", "description": "返回数量，范围 1 到 8"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "read_official_docs",
            "description": "按路径读取 docs.mai-mai.org 官方文档正文。回答文档问题前应先调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "search_official_docs 返回的文档路径，最多 4 个",
                    }
                },
                "required": ["paths"],
            },
        },
    ]


def _parse_official_docs_bundle(bundle: str) -> List[OfficialDocument]:
    """解析官方站点提供的 `llms-full.txt` 文档包。"""

    documents: List[OfficialDocument] = []
    pattern = re.compile(r"(?:\A|\n)---\s*\nurl:\s*(/[^\n]+)\n---\s*\n")
    matches = list(pattern.finditer(bundle))
    for index, match in enumerate(matches):
        path = match.group(1).strip()
        content_end = matches[index + 1].start() if index + 1 < len(matches) else len(bundle)
        content = bundle[match.end() : content_end].strip()
        title_match = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.rsplit("/", 1)[-1]
        documents.append(OfficialDocument(path=path, title=title, content=content))
    return documents


async def _load_official_docs() -> List[OfficialDocument]:
    """下载并短期缓存官方 LLM 文档包。"""

    global _official_docs_cache
    now = time.monotonic()
    if _official_docs_cache is not None and _official_docs_cache[0] > now:
        return _official_docs_cache[1]

    async with _official_docs_lock:
        now = time.monotonic()
        if _official_docs_cache is not None and _official_docs_cache[0] > now:
            return _official_docs_cache[1]
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            response = await client.get(OFFICIAL_DOCS_BUNDLE_URL)
            response.raise_for_status()
        if len(response.content) > OFFICIAL_DOCS_MAX_BUNDLE_SIZE:
            raise ValueError("官方文档包大小超出限制")
        documents = _parse_official_docs_bundle(response.text)
        if not documents:
            raise ValueError("官方文档包中没有可读取的文档")
        _official_docs_cache = (now + OFFICIAL_DOCS_CACHE_TTL_SECONDS, documents)
        return documents


def _build_document_snippet(content: str, terms: List[str]) -> str:
    """截取首个命中词附近的官方文档片段。"""

    normalized_content = content.lower()
    positions = [normalized_content.find(term) for term in terms if term in normalized_content]
    start = max(0, min(positions) - 160) if positions else 0
    snippet = re.sub(r"\s+", " ", content[start : start + 500]).strip()
    return snippet


def _build_official_doc_url(path: str) -> str:
    """把 LLM 文档路径转换为面向用户的文档站页面 URL。"""

    return f"{OFFICIAL_DOCS_BASE_URL}{path.removesuffix('.md')}"


def _search_official_docs(
    query: str,
    documents: List[OfficialDocument],
    limit: int,
) -> List[Dict[str, str]]:
    """在官方文档包内搜索标题和正文。"""

    terms = list(dict.fromkeys(re.findall(r"[\w.\-]+", query.lower())))
    if not terms:
        return []
    ranked_documents: List[Tuple[int, OfficialDocument]] = []
    for document in documents:
        title = document.title.lower()
        path = document.path.lower()
        content = document.content.lower()
        score = sum(8 * int(term in title) + 4 * int(term in path) + int(term in content) for term in terms)
        if score > 0:
            ranked_documents.append((score, document))
    ranked_documents.sort(key=lambda item: (-item[0], item[1].title))
    return [
        {
            "path": document.path,
            "title": document.title,
            "url": _build_official_doc_url(document.path),
            "snippet": _build_document_snippet(document.content, terms),
        }
        for _, document in ranked_documents[:limit]
    ]


def _read_official_docs(paths: Any, documents: List[OfficialDocument]) -> List[Dict[str, str]]:
    """读取官方文档正文并限制单篇返回长度。"""

    if not isinstance(paths, list):
        return []
    document_map = {document.path: document for document in documents}
    results: List[Dict[str, str]] = []
    seen_paths: set[str] = set()
    for raw_path in paths:
        path = str(raw_path).strip()
        document = document_map.get(path)
        if document is None or path in seen_paths:
            continue
        seen_paths.add(path)
        results.append(
            {
                "source_id": document.path,
                "title": document.title,
                "url": _build_official_doc_url(document.path),
                "content": document.content[:OFFICIAL_DOCS_MAX_READ_SIZE],
            }
        )
        if len(results) >= 4:
            break
    return results


def _search_documents(query: str, candidates: List[AISearchCandidate], limit: int) -> List[Dict[str, Any]]:
    """在本次请求提供的文档中执行确定性的关键词检索。"""

    terms = list(dict.fromkeys(re.findall(r"[\w.\-]+", query.lower())))
    if not terms:
        return []

    ranked_candidates: List[Tuple[int, AISearchCandidate]] = []
    for candidate in candidates:
        title = candidate.title.lower()
        category = candidate.category.lower()
        description = candidate.description.lower()
        document = candidate.document.lower()
        score = sum(
            5 * int(term in title) + 3 * int(term in category) + 2 * int(term in description) + int(term in document)
            for term in terms
        )
        if score > 0:
            ranked_candidates.append((score, candidate))

    ranked_candidates.sort(key=lambda item: (-item[0], item[1].title))
    return [
        {
            "id": candidate.id,
            "title": candidate.title,
            "category": candidate.category,
            "summary": candidate.description,
        }
        for _, candidate in ranked_candidates[:limit]
    ]


def _read_documents(ids: Any, candidates: List[AISearchCandidate]) -> List[Dict[str, str]]:
    """按 ID 返回文档正文，忽略越界、重复和不存在的 ID。"""

    if not isinstance(ids, list):
        return []
    candidate_map = {candidate.id: candidate for candidate in candidates}
    documents: List[Dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw_id in ids:
        document_id = str(raw_id).strip()
        candidate = candidate_map.get(document_id)
        if candidate is None or document_id in seen_ids:
            continue
        seen_ids.add(document_id)
        documents.append(
            {
                "id": candidate.id,
                "title": candidate.title,
                "category": candidate.category,
                "content": candidate.document or candidate.description,
            }
        )
        if len(documents) >= AI_SEARCH_MAX_RESULTS:
            break
    return documents


async def _execute_agent_tool(
    tool_call: ToolCall,
    candidates: List[AISearchCandidate],
    read_source_ids: set[str],
) -> str:
    """执行白名单内的只读 Agent 工具并序列化结果。"""

    arguments = tool_call.args or {}
    if tool_call.func_name == "search_webui_index":
        query = str(arguments.get("query") or "").strip()[:200]
        raw_limit = arguments.get("limit", AI_SEARCH_MAX_RESULTS)
        limit = max(1, min(10, raw_limit if isinstance(raw_limit, int) else AI_SEARCH_MAX_RESULTS))
        payload: Dict[str, Any] = {
            "query": query,
            "documents": _search_documents(query, candidates, limit),
        }
    elif tool_call.func_name == "read_webui_documents":
        payload = {"documents": _read_documents(arguments.get("ids"), candidates)}
    elif tool_call.func_name in {"search_official_docs", "read_official_docs"}:
        try:
            official_documents = await _load_official_docs()
            if tool_call.func_name == "search_official_docs":
                query = str(arguments.get("query") or "").strip()[:200]
                raw_limit = arguments.get("limit", AI_SEARCH_MAX_RESULTS)
                limit = max(1, min(8, raw_limit if isinstance(raw_limit, int) else AI_SEARCH_MAX_RESULTS))
                payload = {
                    "query": query,
                    "documents": _search_official_docs(query, official_documents, limit),
                }
            else:
                documents = _read_official_docs(arguments.get("paths"), official_documents)
                read_source_ids.update(document["source_id"] for document in documents)
                payload = {"documents": documents}
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"官方文档工具调用失败: {exc}")
            payload = {"error": f"暂时无法读取官方文档: {exc}"}
    else:
        payload = {"error": f"未知工具: {tool_call.func_name}"}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_assistant_tool_message(response: str, tool_calls: List[ToolCall]) -> Message:
    """把模型的工具调用回复加入下一轮上下文。"""

    builder = MessageBuilder().set_role(RoleType.Assistant).set_tool_calls(tool_calls)
    if response.strip():
        builder.add_text_content(response)
    return builder.build()


def _build_tool_result_message(tool_call: ToolCall, content: str) -> Message:
    """构造与调用 ID 严格对应的工具结果消息。"""

    return (
        MessageBuilder()
        .set_role(RoleType.Tool)
        .add_text_content(content)
        .set_tool_call_id(tool_call.call_id)
        .set_tool_name(tool_call.func_name)
        .build()
    )


def _build_final_instruction(language: str) -> str:
    """要求 Agent 基于已读文档生成最终 JSON 对象。"""

    locale = _resolve_prompt_locale(language)
    if locale == "en-US":
        return "Finish your research and return the final JSON object now. Do not call more tools."
    if locale == "ja-JP":
        return "調査を終了し、最終的な JSON オブジェクトのみ返してください。これ以上ツールを呼び出さないでください。"
    return "结束检索，现在仅返回最终 JSON 对象，不要再调用工具。"


async def _run_ai_search_agent(request: AISearchRequest) -> Tuple[LLMResponseResult, set[str]]:
    """运行有限轮次的文档检索 Agent，并在最后生成格式化结果。"""

    model = _get_ai_search_model()
    messages = [MessageBuilder().add_text_content(_build_ai_search_prompt(request)).build()]
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    read_source_ids: set[str] = set()

    for _ in range(AI_SEARCH_MAX_TOOL_ROUNDS):
        generation_result = await model.generate_response_with_messages(
            lambda _client: list(messages),
            options=LLMGenerationOptions(
                temperature=0,
                max_tokens=512,
                tool_options=_build_agent_tools(),
            ),
        )
        prompt_tokens += generation_result.prompt_tokens
        completion_tokens += generation_result.completion_tokens
        total_tokens += generation_result.total_tokens
        tool_calls = (generation_result.tool_calls or [])[:AI_SEARCH_MAX_TOOL_CALLS_PER_ROUND]
        if not tool_calls:
            if generation_result.response.strip():
                messages.append(
                    MessageBuilder().set_role(RoleType.Assistant).add_text_content(generation_result.response).build()
                )
            break

        messages.append(_build_assistant_tool_message(generation_result.response, tool_calls))
        for tool_call in tool_calls:
            tool_result = await _execute_agent_tool(tool_call, request.candidates, read_source_ids)
            messages.append(_build_tool_result_message(tool_call, tool_result))

    messages.append(MessageBuilder().add_text_content(_build_final_instruction(request.language)).build())
    final_result = await model.generate_response_with_messages(
        lambda _client: list(messages),
        options=LLMGenerationOptions(
            temperature=0,
            max_tokens=1024,
            response_format=RespFormat(format_type=RespFormatType.JSON_OBJ),
        ),
    )
    final_result.prompt_tokens += prompt_tokens
    final_result.completion_tokens += completion_tokens
    final_result.total_tokens += total_tokens
    return final_result, read_source_ids


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
    read_source_ids: set[str],
) -> AISearchModelOutput:
    """仅保留真实候选 ID 和 Agent 实际读过的官方文档来源。"""

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

    suggestions: List[str] = []
    seen_suggestions: set[str] = set()
    for suggestion in model_output.suggestions:
        normalized_suggestion = suggestion.strip()
        if not normalized_suggestion or normalized_suggestion in seen_suggestions:
            continue
        seen_suggestions.add(normalized_suggestion)
        suggestions.append(normalized_suggestion[:240])
        if len(suggestions) >= 6:
            break

    source_ids: List[str] = []
    for source_id in model_output.source_ids:
        normalized_source_id = source_id.strip()
        if normalized_source_id in read_source_ids and normalized_source_id not in source_ids:
            source_ids.append(normalized_source_id)
        if len(source_ids) >= 6:
            break

    return AISearchModelOutput(
        answer=model_output.answer.strip()[:2000],
        suggestions=suggestions,
        source_ids=source_ids,
        expanded_terms=expanded_terms,
        results=results,
    )


def _build_response_sources(source_ids: List[str]) -> List[AISearchSource]:
    """把已校验的官方文档 ID 转成可点击来源。"""

    if _official_docs_cache is None:
        return []
    document_map = {document.path: document for document in _official_docs_cache[1]}
    return [
        AISearchSource(title=document_map[source_id].title, url=_build_official_doc_url(source_id))
        for source_id in source_ids
        if source_id in document_map
    ]


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

    try:
        generation_result, read_source_ids = await asyncio.wait_for(
            _run_ai_search_agent(request),
            timeout=AI_SEARCH_TIMEOUT_SECONDS,
        )
        model_output = _normalize_model_output(
            _extract_model_output(generation_result.response),
            request.candidates,
            read_source_ids,
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
        answer=model_output.answer,
        suggestions=model_output.suggestions,
        sources=_build_response_sources(model_output.source_ids),
        expanded_terms=model_output.expanded_terms,
        results=model_output.results,
        prompt_tokens=generation_result.prompt_tokens,
        completion_tokens=generation_result.completion_tokens,
        total_tokens=generation_result.total_tokens,
    )
    _cache_response(cache_key, response)
    return response
