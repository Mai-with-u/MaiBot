"""WebUI AI 搜索 Agent 的工具编排与回答生成。"""

from typing import Any, Dict, List, Literal
import asyncio
import json

import httpx
from pydantic import ValidationError

from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.llm_models.payload_content.context_item import (
    ContextItem,
    ContextItemBuilder,
    ContextItemMeta,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    replace_output_projection,
)
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolCall, ToolDefinitionInput
from src.services.llm_service import LLMServiceClient

from .ai_search_documents import (
    AI_SEARCH_DEFAULT_TOOL_LIMIT,
    OFFICIAL_DOCS_MAX_READ_COUNT,
    OFFICIAL_DOCS_SEARCH_MAX_LIMIT,
    WEBUI_SEARCH_MAX_LIMIT,
    AISearchDocumentStore,
)
from .ai_search_grounding import AISearchGroundingError, validate_model_output_evidence
from .ai_search_local_config import LOCAL_CONFIG_MAX_READ_COUNT, read_local_config
from .ai_search_models import (
    AISearchCandidate,
    AISearchModelOutput,
    AISearchModelResult,
    AISearchOutputError,
    AISearchProgressCallback,
    AISearchProgressEvent,
    AISearchRequest,
    AISearchResponse,
)

logger = get_logger("webui.ai_search")

AI_SEARCH_MAX_TOOL_ROUNDS = 4
AI_SEARCH_MAX_TOOL_CALLS_PER_ROUND = 4
AI_SEARCH_MAX_TOOL_QUERY_LENGTH = 200
AI_SEARCH_PROGRESS_MAX_TITLES = 6
AI_SEARCH_PROGRESS_MAX_TARGETS = 8

_ai_search_model: LLMServiceClient | None = None
_document_store = AISearchDocumentStore()


def _get_ai_search_model() -> LLMServiceClient:
    """延迟创建 utils 模型客户端，避免模块导入阶段绑定未就绪配置。"""

    global _ai_search_model
    if _ai_search_model is None:
        _ai_search_model = LLMServiceClient(task_name="utils", request_type="webui.ai_search")
    return _ai_search_model


def resolve_prompt_locale(language: str) -> str:
    """把 WebUI 语言代码映射到现有 Prompt 语言目录。"""

    normalized_language = language.strip().lower().replace("_", "-")
    if normalized_language.startswith("en"):
        return "en-US"
    if normalized_language.startswith("ja"):
        return "ja-JP"
    return "zh-CN"


def _build_text_item(text: str) -> ContextItem:
    """把纯文本包装成一条上下文消息。"""

    return ContextItemBuilder().add_text_content(text).build()


def _build_ai_search_prompt(request: AISearchRequest, locale: str) -> str:
    """按界面语言构造带只读检索工具说明的 Agent Prompt。"""

    return load_prompt(
        "webui_ai_search",
        locale=locale,
        query_json=json.dumps(request.query.strip(), ensure_ascii=False),
        candidate_count=len(request.candidates),
    )


def _build_agent_tools() -> List[ToolDefinitionInput]:
    """构造本地 WebUI 索引、官方文档站与本地配置的只读工具。"""

    return [
        {
            "name": "search_webui_index",
            "description": (
                "搜索当前 WebUI 中可导航的页面和配置项，返回候选 ID、标题，以及配置说明、字段路径、类型和选项信息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "一个或多个简短检索词，用空格分隔"},
                    "limit": {"type": "integer", "description": f"返回数量，范围 1 到 {WEBUI_SEARCH_MAX_LIMIT}"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "search_official_docs",
            "description": "搜索 docs.mai-mai.org 上的 MaiBot 官方文档，返回文档路径、标题和相关片段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "一个或多个简短检索词，用空格分隔"},
                    "limit": {
                        "type": "integer",
                        "description": f"返回数量，范围 1 到 {OFFICIAL_DOCS_SEARCH_MAX_LIMIT}",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "read_official_docs",
            "description": (
                "按路径读取 docs.mai-mai.org 官方文档正文。回答文档问题前应先调用此工具。"
                "单次返回的正文有长度上限，结果带有 next_offset 时说明文档还有后续内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (f"search_official_docs 返回的文档路径，最多 {OFFICIAL_DOCS_MAX_READ_COUNT} 个"),
                    },
                    "offset": {
                        "type": "integer",
                        "description": (
                            "开始读取的字符位置，默认 0。可填上次返回的 next_offset 继续读取，"
                            "或填 search_official_docs 返回的 snippet_offset 直接跳到命中位置"
                        ),
                    },
                },
                "required": ["paths"],
            },
        },
        {
            "name": "read_local_config",
            "description": (
                "读取本机 bot_config.toml 与 model_config.toml 中配置节或字段当前实际生效的值，"
                "用于核对用户现在的设置。密钥、令牌等敏感内容会被隐藏。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "点号分隔的配置路径，可以是配置节或具体字段，列表元素用下标表示，"
                            "例如 chat.reply_timing、emoji.steal_emoji、api_providers.0，"
                            f"最多 {LOCAL_CONFIG_MAX_READ_COUNT} 个"
                        ),
                    }
                },
                "required": ["paths"],
            },
        },
    ]


async def _emit_progress(
    callback: AISearchProgressCallback | None,
    event: AISearchProgressEvent,
) -> None:
    """按需发送单条搜索过程事件。"""

    if callback is not None:
        await callback(event)


def _build_tool_progress_event(
    tool_call: ToolCall,
    status: Literal["started", "completed"],
    payload: Dict[str, Any] | None = None,
) -> AISearchProgressEvent:
    """把工具调用及结果压缩为适合界面展示的过程摘要。"""

    arguments = tool_call.args or {}
    query = str(arguments.get("query") or "").strip()
    raw_paths = arguments.get("paths")
    targets = [str(path) for path in raw_paths[:AI_SEARCH_PROGRESS_MAX_TARGETS]] if isinstance(raw_paths, list) else []

    titles: List[str] = []
    count: int | None = None
    error = ""
    if payload is not None:
        error = str(payload.get("error") or "")
        documents = payload.get("documents")
        if documents is not None:
            count = len(documents)
            titles = [document["title"] for document in documents[:AI_SEARCH_PROGRESS_MAX_TITLES]]

    return AISearchProgressEvent(
        stage="tool",
        status="failed" if error else status,
        tool=tool_call.func_name,
        query=query,
        targets=targets,
        titles=titles,
        count=count,
        error=error,
    )


def _resolve_tool_query(arguments: Dict[str, Any]) -> str:
    """读取并截断工具入参中的检索词。"""

    return str(arguments.get("query") or "").strip()[:AI_SEARCH_MAX_TOOL_QUERY_LENGTH]


def _resolve_tool_limit(arguments: Dict[str, Any], maximum: int) -> int:
    """把工具入参中的返回数量收敛到允许范围内。"""

    raw_limit = arguments.get("limit")
    limit = raw_limit if isinstance(raw_limit, int) else AI_SEARCH_DEFAULT_TOOL_LIMIT
    return max(1, min(maximum, limit))


async def _execute_agent_tool(
    tool_call: ToolCall,
    candidates: List[AISearchCandidate],
    read_source_ids: set[str],
) -> Dict[str, Any]:
    """执行白名单内的只读 Agent 工具，返回尚未序列化的结果。"""

    arguments = tool_call.args or {}
    if tool_call.func_name == "search_webui_index":
        query = _resolve_tool_query(arguments)
        limit = _resolve_tool_limit(arguments, WEBUI_SEARCH_MAX_LIMIT)
        # 候选索引的分词与扫描放到线程里，避免占住 WebUI 事件循环
        documents = await asyncio.to_thread(_document_store.search_candidates, query, candidates, limit)
        return {"query": query, "documents": documents}

    if tool_call.func_name == "read_local_config":
        return {"documents": read_local_config(arguments.get("paths"))}

    if tool_call.func_name not in {"search_official_docs", "read_official_docs"}:
        return {"error": f"未知工具: {tool_call.func_name}"}

    try:
        if tool_call.func_name == "search_official_docs":
            query = _resolve_tool_query(arguments)
            limit = _resolve_tool_limit(arguments, OFFICIAL_DOCS_SEARCH_MAX_LIMIT)
            return {"query": query, "documents": await _document_store.search_official_docs(query, limit)}

        raw_offset = arguments.get("offset")
        offset = max(0, raw_offset) if isinstance(raw_offset, int) else 0
        documents = await _document_store.read_official_docs(arguments.get("paths"), offset)
        read_source_ids.update(document["source_id"] for document in documents)
        return {"documents": documents}
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"官方文档工具调用失败: {exc}")
        return {"error": f"暂时无法读取官方文档: {exc}"}


def _build_tool_result_item(
    tool_call: ToolCall,
    content: str,
    logical_turn_id: str,
) -> FunctionCallOutputItem:
    """构造与调用 ID 严格对应的工具结果消息。"""

    return FunctionCallOutputItem(
        meta=ContextItemMeta.create(logical_turn_id=logical_turn_id),
        call_id=tool_call.call_id,
        output=content,
        tool_name=tool_call.func_name,
    )


def _build_evidence_messages(messages: List[ContextItem], locale: str) -> List[ContextItem]:
    """把已读取的工具结果转换为普通证据消息，供无工具的请求继续使用。"""

    evidence_messages: List[ContextItem] = []
    tool_turn_ids = {
        item.meta.logical_turn_id
        for item in messages
        if isinstance(item, FunctionCallItem) and item.meta.logical_turn_id is not None
    }
    for message in messages:
        if isinstance(message, FunctionCallOutputItem):
            evidence = load_prompt(
                "webui_ai_search_evidence",
                locale=locale,
                tool_name=message.tool_name or "unknown",
                tool_output=message.output,
            )
            evidence_messages.append(_build_text_item(evidence))
            continue

        if message.meta.logical_turn_id in tool_turn_ids:
            continue

        evidence_messages.append(message)

    return evidence_messages


async def _generate_json_answer(model: LLMServiceClient, messages: List[ContextItem]) -> LLMResponseResult:
    """发起一次不带工具、要求返回 JSON 对象的回答请求。"""

    return await model.generate_response_with_context(
        lambda _client: list(messages),
        options=LLMGenerationOptions(
            response_format=RespFormat(format_type=RespFormatType.JSON_OBJ),
        ),
    )


async def run_ai_search_agent(
    request: AISearchRequest,
    progress_callback: AISearchProgressCallback | None = None,
) -> AISearchResponse:
    """运行有限轮次的文档检索 Agent，并生成经过校验的搜索响应。"""

    model = _get_ai_search_model()
    locale = resolve_prompt_locale(request.language)
    messages: List[ContextItem] = [_build_text_item(_build_ai_search_prompt(request, locale))]
    tools = _build_agent_tools()
    total_tokens = 0
    read_source_ids: set[str] = set()
    grounding_evidence: List[str] = []
    answer_result: LLMResponseResult | None = None
    used_local_config = False

    # 首轮规划通常要几秒，趁这段时间在后台准备好官方文档包
    _document_store.prewarm_official_docs()
    await _emit_progress(progress_callback, AISearchProgressEvent(stage="start"))
    for round_index in range(AI_SEARCH_MAX_TOOL_ROUNDS):
        await _emit_progress(
            progress_callback,
            AISearchProgressEvent(stage="planning", round=round_index + 1),
        )
        generation_result = await model.generate_response_with_context(
            lambda _client: list(messages),
            options=LLMGenerationOptions(tool_options=tools),
        )
        total_tokens += generation_result.total_tokens
        tool_calls = (generation_result.tool_calls or [])[:AI_SEARCH_MAX_TOOL_CALLS_PER_ROUND]
        if not tool_calls:
            # 模型不再调用工具时，本轮输出就是它给出的最终回答
            messages.extend(generation_result.output_items)
            answer_result = generation_result
            break

        selected_output_items = replace_output_projection(
            generation_result.output_items,
            tool_calls=[
                ContextToolCall.create(
                    call_id=tool_call.call_id,
                    func_name=tool_call.func_name,
                    args=tool_call.args,
                    extra_content=tool_call.extra_content,
                )
                for tool_call in tool_calls
            ],
            replace_tool_calls=len(tool_calls) != len(generation_result.tool_calls or []),
        )
        messages.extend(selected_output_items)
        logical_turn_by_call_id = {
            item.tool_call.call_id: item.meta.logical_turn_id
            for item in selected_output_items
            if isinstance(item, FunctionCallItem) and item.meta.logical_turn_id
        }
        for tool_call in tool_calls:
            await _emit_progress(progress_callback, _build_tool_progress_event(tool_call, "started"))
            tool_payload = await _execute_agent_tool(tool_call, request.candidates, read_source_ids)
            used_local_config = used_local_config or tool_call.func_name == "read_local_config"
            tool_result = json.dumps(tool_payload, ensure_ascii=False, separators=(",", ":"))
            logical_turn_id = logical_turn_by_call_id.get(tool_call.call_id)
            if not logical_turn_id:
                raise ValueError(f"工具调用缺少 logical_turn_id: {tool_call.call_id}")
            messages.append(_build_tool_result_item(tool_call, tool_result, logical_turn_id))
            if not tool_payload.get("error"):
                grounding_evidence.append(tool_result)
            await _emit_progress(
                progress_callback,
                _build_tool_progress_event(tool_call, "completed", tool_payload),
            )

    # 后续的定稿与纠错请求都不带工具，统一改用证据消息承载已读资料
    answer_messages = _build_evidence_messages(messages, locale)
    parsed_output: AISearchModelOutput | None = None
    if answer_result is not None:
        try:
            parsed_output = _extract_model_output(answer_result.response)
        except AISearchOutputError as exc:
            logger.info(f"WebUI AI 搜索 Agent 结束检索时未给出最终 JSON，转入定稿请求: {exc}")

    if answer_result is None or parsed_output is None:
        # 仅在工具轮次用尽或模型没有按格式作答时，才需要额外的定稿请求
        await _emit_progress(progress_callback, AISearchProgressEvent(stage="finalizing"))
        answer_messages.append(_build_text_item(load_prompt("webui_ai_search_final", locale=locale)))
        answer_result = await _generate_json_answer(model, answer_messages)
        total_tokens += answer_result.total_tokens
        answer_messages.extend(answer_result.output_items)
        parsed_output = _extract_model_output(answer_result.response)

    model_output = _normalize_model_output(parsed_output, request.candidates, read_source_ids)
    # 用户问题里出现的字段名也算依据，否则“没有这个配置项”这类回答会因复述字段名被误判
    evidence = "\n".join([request.query, *grounding_evidence])
    grounding_error = ""
    try:
        validate_model_output_evidence(model_output, evidence)
    except AISearchGroundingError as exc:
        await _emit_progress(
            progress_callback,
            AISearchProgressEvent(stage="correcting", status="started", error=str(exc)),
        )
        answer_messages.append(
            _build_text_item(load_prompt("webui_ai_search_correction", locale=locale, error=str(exc)))
        )
        answer_result = await _generate_json_answer(model, answer_messages)
        total_tokens += answer_result.total_tokens
        model_output = _normalize_model_output(
            _extract_model_output(answer_result.response),
            request.candidates,
            read_source_ids,
        )
        try:
            validate_model_output_evidence(model_output, evidence)
        except AISearchGroundingError as correction_exc:
            # 重写后仍有无依据的技术项：只丢弃回答正文，导航结果已通过候选 ID 校验，照常返回
            grounding_error = f"AI 回答证据校验失败: {correction_exc}"
            logger.warning(f"WebUI AI 搜索回答重写后仍未通过证据校验: {correction_exc}")
            await _emit_progress(
                progress_callback,
                AISearchProgressEvent(stage="correcting", status="failed", error=str(correction_exc)),
            )
            model_output = model_output.model_copy(update={"answer": "", "suggestions": [], "source_ids": []})

    return AISearchResponse(
        model_name=answer_result.model_name,
        answer=model_output.answer,
        suggestions=model_output.suggestions,
        sources=_document_store.build_sources(model_output.source_ids),
        expanded_terms=model_output.expanded_terms,
        results=model_output.results,
        total_tokens=total_tokens,
        grounding_error=grounding_error,
        used_local_config=used_local_config,
    )


def _extract_model_output(raw_response: str) -> AISearchModelOutput:
    """解析结构化响应，同时兼容被 Markdown 代码块包裹的 JSON。"""

    normalized_response = raw_response.strip()
    try:
        return AISearchModelOutput.model_validate_json(normalized_response)
    except ValidationError as first_error:
        start_index = normalized_response.find("{")
        end_index = normalized_response.rfind("}")
        if normalized_response.startswith("{") and not normalized_response.endswith("}"):
            raise AISearchOutputError("模型返回的 JSON 不完整，可能因 max_token 限制被截断") from first_error
        if start_index < 0 or end_index <= start_index:
            raise AISearchOutputError("模型没有返回可解析的 JSON 对象") from first_error

        try:
            return AISearchModelOutput.model_validate_json(normalized_response[start_index : end_index + 1])
        except ValidationError as second_error:
            raise AISearchOutputError("模型返回的 AI 搜索结果结构无效") from second_error


def _dedupe_stripped(values: List[str]) -> List[str]:
    """去除首尾空白、空项和重复项，保持原有顺序。"""

    return list(dict.fromkeys(stripped for value in values if (stripped := value.strip())))


def _normalize_model_output(
    model_output: AISearchModelOutput,
    candidates: List[AISearchCandidate],
    read_source_ids: set[str],
) -> AISearchModelOutput:
    """仅保留真实候选 ID 和 Agent 实际读过的官方文档来源，不截断模型输出。"""

    candidate_ids = {candidate.id for candidate in candidates}
    results: Dict[str, AISearchModelResult] = {}
    for result in model_output.results:
        if result.id in candidate_ids:
            results.setdefault(result.id, result)

    return AISearchModelOutput(
        answer=model_output.answer.strip(),
        suggestions=_dedupe_stripped(model_output.suggestions),
        source_ids=[
            source_id for source_id in _dedupe_stripped(model_output.source_ids) if source_id in read_source_ids
        ],
        expanded_terms=_dedupe_stripped(model_output.expanded_terms),
        results=list(results.values()),
    )
