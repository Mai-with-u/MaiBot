"""WebUI AI 搜索 Agent 的工具编排与回答生成。"""

from typing import Dict, List, Sequence, Set, Tuple
import json

from pydantic import ValidationError

from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.common.llm_json import validate_llm_json
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.llm_models.payload_content.context_item import (
    ContextItem,
    ContextItemBuilder,
    ContextItemMeta,
    FunctionCallItem,
    FunctionCallOutputItem,
    RoleType,
    get_item_text,
)
from src.llm_models.payload_content.context_protocol import ContextProtocolMode, validate_context_items
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolCall
from src.services.llm_service import LLMServiceClient

from .ai_help_artifacts import PUBLISH_ARTIFACT_TOOL
from .ai_help_harness import HARNESS_TOOLS, HelpHarness
from .ai_help_references import get_help_reference, match_help_references
from .ai_search_documents import AISearchDocumentStore
from .ai_search_grounding import AISearchGroundingError, validate_model_output_evidence
from .ai_search_market import AISearchMarketStore
from .ai_search_models import (
    AISearchModelOutput,
    AISearchModelResult,
    AISearchOutputError,
    AISearchProgressCallback,
    AISearchProgressEvent,
    AISearchRequest,
    AISearchResponse,
)
from .ai_search_tools import LOOKUP_HELP_TOOL, AISearchToolRunner, emit_progress

logger = get_logger("webui.ai_search")

AI_SEARCH_MAX_TOOL_ROUNDS = 6
AI_SEARCH_MAX_TOOL_CALLS_PER_ROUND = 4

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


def _build_text_item(text: str, role: RoleType = RoleType.User) -> ContextItem:
    """把纯文本包装成一条上下文消息。"""

    return ContextItemBuilder().set_role(role).add_text_content(text).build()


def _build_initial_messages(
    request: AISearchRequest, locale: str, history: Sequence[Tuple[str, str]] = ()
) -> List[ContextItem]:
    """把可信规则和不可信的当前问题、历史记录分开投影。"""

    policy = load_prompt("webui_ai_search", locale=locale, candidate_count=len(request.candidates))
    user_input = load_prompt(
        "webui_ai_search_input",
        locale=locale,
        query_json=json.dumps(request.query.strip(), ensure_ascii=False),
        history_json=json.dumps(
            [{"question": question, "answer": answer} for question, answer in history], ensure_ascii=False
        ),
    )
    return [_build_text_item(policy, RoleType.System), _build_text_item(user_input)]


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


def _build_evidence_message(tool_name: str, output: str, locale: str) -> ContextItem:
    return _build_text_item(
        load_prompt("webui_ai_search_evidence", locale=locale, tool_name=tool_name, tool_output=output)
    )


def _build_evidence_messages(messages: List[ContextItem], harness: HelpHarness) -> List[ContextItem]:
    """把已读取的工具结果转换为普通证据消息，供无工具的请求继续使用。"""

    evidence_messages: List[ContextItem] = []
    tool_turn_ids = {
        item.meta.logical_turn_id
        for item in messages
        if isinstance(item, FunctionCallItem) and item.meta.logical_turn_id is not None
    }
    for message in messages:
        if isinstance(message, FunctionCallOutputItem):
            if message.tool_name not in HARNESS_TOOLS and message.tool_name != PUBLISH_ARTIFACT_TOOL:
                evidence_messages.append(_build_evidence_message(message.tool_name, message.output, harness.locale))
            continue

        if message.meta.logical_turn_id in tool_turn_ids:
            continue

        evidence_messages.append(message)

    if harness.active_skills:
        # 定稿仍遵循本轮加载的工作流程；技能正文不算事实证据。
        policy = "\n\n".join([get_item_text(evidence_messages[0]), *harness.active_skills.values()])
        evidence_messages[0] = _build_text_item(policy, RoleType.System)
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
    *,
    history: Sequence[Tuple[str, str]] = (),
) -> AISearchResponse:
    """运行有限轮次的检索与插件管理 Agent，并生成经过校验的响应。"""

    model = _get_ai_search_model()
    locale = resolve_prompt_locale(request.language)
    messages = _build_initial_messages(request, locale, history)
    harness = HelpHarness(locale)
    total_tokens = 0
    # 已验证且与原问题直接匹配的参考可在首轮使用，节省一次模型和工具的往返。
    direct_references = match_help_references(request.query)
    read_source_ids: Set[str] = {reference["source_id"] for reference in direct_references}
    matched_candidate_ids: Set[str] = set()
    grounding_evidence: List[str] = []
    if direct_references:
        references_json = json.dumps(direct_references, ensure_ascii=False, separators=(",", ":"))
        grounding_evidence.append(references_json)
        messages.append(_build_evidence_message("preloaded_help_references", references_json, locale))
    answer_result: LLMResponseResult | None = None
    used_local_config = False
    market_store = AISearchMarketStore()
    tool_runner = AISearchToolRunner(request, harness, _document_store, market_store, progress_callback)

    await emit_progress(progress_callback, AISearchProgressEvent(stage="start"))
    for round_index in range(AI_SEARCH_MAX_TOOL_ROUNDS):
        tools = tool_runner.definitions()
        await emit_progress(
            progress_callback,
            AISearchProgressEvent(stage="planning", round=round_index + 1),
        )
        generation_result = await model.generate_response_with_context(
            lambda _client: list(messages),
            options=LLMGenerationOptions(tool_options=tools),
        )
        total_tokens += generation_result.total_tokens
        tool_calls = generation_result.tool_calls
        if not tool_calls:
            # 模型不再调用工具时，本轮输出就是它给出的最终回答
            messages.extend(generation_result.output_items)
            answer_result = generation_result
            break

        # 在工具执行前验证整个批次，避免响应不完整或 ID 冲突时已经发生写入。
        logical_turn_by_call_id = {
            item.tool_call.call_id: item.meta.logical_turn_id
            for item in generation_result.output_items
            if isinstance(item, FunctionCallItem) and item.meta.logical_turn_id
        }
        messages.extend(generation_result.output_items)
        try:
            validate_context_items(
                messages, ContextProtocolMode.REQUEST_CONTEXT, pending_call_ids=logical_turn_by_call_id
            )
        except ValueError as exc:
            raise AISearchOutputError(f"模型工具调用记录无效: {exc}") from exc

        tool_results = await tool_runner.run_round(
            tool_calls, {tool["name"] for tool in tools}, AI_SEARCH_MAX_TOOL_CALLS_PER_ROUND
        )
        for tool_call, tool_payload in zip(tool_calls, tool_results, strict=True):
            used_local_config = used_local_config or tool_call.func_name == "read_local_config"
            tool_result = json.dumps(tool_payload, ensure_ascii=False, separators=(",", ":"))
            messages.append(_build_tool_result_item(tool_call, tool_result, logical_turn_by_call_id[tool_call.call_id]))
            if (
                not tool_payload.get("error")
                and "reused_call_id" not in tool_payload
                and tool_call.func_name not in HARNESS_TOOLS
                and tool_call.func_name != PUBLISH_ARTIFACT_TOOL
            ):
                evidence = tool_result
                if "docs_error" in tool_payload:
                    evidence = json.dumps(
                        {key: value for key, value in tool_payload.items() if key != "docs_error"}, ensure_ascii=False
                    )
                grounding_evidence.append(evidence)
                if tool_call.func_name in {"search_webui_index", LOOKUP_HELP_TOOL}:
                    candidates_key = "navigation" if tool_call.func_name == LOOKUP_HELP_TOOL else "documents"
                    matched_candidate_ids.update(document["id"] for document in tool_payload[candidates_key])
                if tool_call.func_name in {"read_official_docs", LOOKUP_HELP_TOOL}:
                    read_source_ids.update(document["source_id"] for document in tool_payload["documents"])

    # 仅在需要定稿或纠错时构造无工具的证据上下文。
    answer_messages: List[ContextItem] | None = None
    read_source_ids.update(market_store.sources)
    parsed_output: AISearchModelOutput | None = None
    if answer_result is not None:
        try:
            parsed_output = _extract_model_output(answer_result.response)
        except AISearchOutputError as exc:
            logger.info(f"WebUI AI 搜索 Agent 结束检索时未给出最终 JSON，转入定稿请求: {exc}")

    if parsed_output is None:
        # 仅在工具轮次用尽或模型没有按格式作答时，才需要额外的定稿请求
        await emit_progress(progress_callback, AISearchProgressEvent(stage="finalizing"))
        answer_messages = _build_evidence_messages(messages, harness)
        answer_messages.append(_build_text_item(load_prompt("webui_ai_search_final", locale=locale)))
        answer_result = await _generate_json_answer(model, answer_messages)
        total_tokens += answer_result.total_tokens
        answer_messages.extend(answer_result.output_items)
        parsed_output = _extract_model_output(answer_result.response)

    model_output = _normalize_model_output(parsed_output, matched_candidate_ids, read_source_ids)
    # 用户问题里出现的字段名也算依据，否则“没有这个配置项”这类回答会因复述字段名被误判
    evidence = "\n".join([request.query, *grounding_evidence])
    grounding_error = ""
    try:
        validate_model_output_evidence(model_output, evidence)
    except AISearchGroundingError as exc:
        await emit_progress(
            progress_callback,
            AISearchProgressEvent(stage="correcting", status="started", error=str(exc)),
        )
        if answer_messages is None:
            answer_messages = _build_evidence_messages(messages, harness)
        answer_messages.append(
            _build_text_item(load_prompt("webui_ai_search_correction", locale=locale, error=str(exc)))
        )
        answer_result = await _generate_json_answer(model, answer_messages)
        total_tokens += answer_result.total_tokens
        model_output = _normalize_model_output(
            _extract_model_output(answer_result.response),
            matched_candidate_ids,
            read_source_ids,
        )
        try:
            validate_model_output_evidence(model_output, evidence)
        except AISearchGroundingError as correction_exc:
            # 重写后仍有无依据的技术项：只丢弃回答正文，导航结果已通过候选 ID 校验，照常返回
            grounding_error = f"AI 回答证据校验失败: {correction_exc}"
            logger.warning(f"WebUI AI 搜索回答重写后仍未通过证据校验: {correction_exc}")
            await emit_progress(
                progress_callback,
                AISearchProgressEvent(stage="correcting", status="failed", error=str(correction_exc)),
            )
            model_output = model_output.model_copy(update={"answer": "", "suggestions": [], "source_ids": []})

    references = {
        source_id: reference
        for source_id in model_output.source_ids
        if (reference := get_help_reference(source_id)) is not None
    }
    official_source_ids = [
        source_id
        for source_id in model_output.source_ids
        if source_id not in market_store.sources and source_id not in references
    ]
    return AISearchResponse(
        model_name=answer_result.model_name,
        answer=model_output.answer,
        suggestions=model_output.suggestions,
        sources=[
            *_document_store.build_sources(official_source_ids),
            *references.values(),
            *(market_store.sources[sid] for sid in model_output.source_ids if sid in market_store.sources),
        ],
        expanded_terms=model_output.expanded_terms,
        results=model_output.results,
        plugins=market_store.select_plugins(model_output.plugin_ids),
        install_requests=list(market_store.install_requests.values()),
        config_updates=market_store.config.updates,
        artifacts=tool_runner.artifact_store.artifacts,
        total_tokens=total_tokens,
        grounding_error=grounding_error,
        used_local_config=used_local_config,
        used_plugin_market=market_store.used,
    )


def _extract_model_output(raw_response: str) -> AISearchModelOutput:
    """解析结构化响应，同时兼容被 Markdown 代码块包裹的 JSON。"""

    try:
        return validate_llm_json(raw_response, AISearchModelOutput)
    except ValidationError as exc:
        raise AISearchOutputError("模型返回的 AI 搜索结果不是有效的 JSON 对象") from exc


def _dedupe_stripped(values: List[str]) -> List[str]:
    """去除首尾空白、空项和重复项，保持原有顺序。"""

    return list(dict.fromkeys(stripped for value in values if (stripped := value.strip())))


def _normalize_model_output(
    model_output: AISearchModelOutput,
    matched_candidate_ids: Set[str],
    read_source_ids: Set[str],
) -> AISearchModelOutput:
    """仅保留真实候选 ID 和 Agent 实际查询过的来源，不截断模型输出。"""

    results: Dict[str, AISearchModelResult] = {}
    for result in model_output.results:
        if result.id in matched_candidate_ids:
            results.setdefault(result.id, result)

    return AISearchModelOutput(
        answer=model_output.answer.strip(),
        suggestions=_dedupe_stripped(model_output.suggestions),
        source_ids=[
            source_id for source_id in _dedupe_stripped(model_output.source_ids) if source_id in read_source_ids
        ],
        expanded_terms=_dedupe_stripped(model_output.expanded_terms),
        results=list(results.values()),
        plugin_ids=_dedupe_stripped(model_output.plugin_ids),
    )
