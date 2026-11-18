"""WebUI 答疑工具的定义、执行与单轮调度。"""

from itertools import groupby
from typing import Any, Awaitable, Callable, Dict, List, Literal, Set, Tuple
from urllib.parse import parse_qs, urlsplit
import asyncio
import json
import re

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import httpx

from src.common.logger import get_logger
from src.llm_models.payload_content.tool_option import ToolCall, ToolDefinitionInput

from .ai_help_artifacts import PUBLISH_ARTIFACT_TOOL, HelpArtifactStore, build_help_artifact_tool
from .ai_help_diagnostics import DIAGNOSTIC_TOOL_NAMES, build_diagnostic_tools, execute_diagnostic_tool
from .ai_help_harness import HARNESS_TOOLS, HelpHarness, build_harness_tools
from .ai_help_references import match_help_references
from .ai_search_documents import (
    AI_SEARCH_DEFAULT_TOOL_LIMIT,
    OFFICIAL_DOCS_BASE_URL,
    OFFICIAL_DOCS_MAX_READ_COUNT,
    OFFICIAL_DOCS_SEARCH_MAX_LIMIT,
    WEBUI_SEARCH_MAX_LIMIT,
    AISearchDocumentStore,
)
from .ai_search_local_config import LOCAL_CONFIG_MAX_READ_COUNT, read_local_config
from .ai_search_market import PLUGIN_TOOL_NAMES, AISearchMarketStore, build_market_tools
from .ai_search_models import AISearchCandidate, AISearchProgressCallback, AISearchProgressEvent, AISearchRequest

logger = get_logger("webui.ai_search")

AI_SEARCH_MAX_TOOL_QUERY_LENGTH = 200
AI_SEARCH_PROGRESS_MAX_TITLES = 6
AI_SEARCH_PROGRESS_MAX_TARGETS = 8
HELP_LOOKUP_MAX_CHARS_PER_DOC = 3000
_PLUGIN_CONFIG_WRITE_TOOL = "update_plugin_config"
LOOKUP_HELP_TOOL = "lookup_help"
_REUSABLE_READ_TOOLS = {LOOKUP_HELP_TOOL, "search_webui_index", "search_official_docs", "read_official_docs"}
_PARALLEL_READ_TOOLS = (
    _REUSABLE_READ_TOOLS
    | {
        "read_local_config",
        "list_installed_plugins",
    }
    | DIAGNOSTIC_TOOL_NAMES
)
_HELP_URL = re.compile(r"""(?:https?://|/api/webui/reasoning-process/(?:image|file)\?)[^\s<>"`]+""", re.IGNORECASE)
_REASONING_IMAGE_PATH = "/api/webui/reasoning-process/image"
_REASONING_FILE_PATH = "/api/webui/reasoning-process/file"


def _user_references(query: str) -> Tuple[Set[str], Set[str]]:
    images: Set[str] = set()
    records: Set[str] = set()
    for match in _HELP_URL.findall(query):
        url = match.rstrip(".,;!?)]}，。；！？")
        try:
            parsed = urlsplit(url)
            if parsed.path.endswith(_REASONING_IMAGE_PATH):
                images.add(f"{_REASONING_IMAGE_PATH}?{parsed.query}")
            elif parsed.path.endswith(_REASONING_FILE_PATH):
                records.update(parse_qs(parsed.query).get("path", []))
            elif parsed.scheme == "https":
                images.add(url)
        except ValueError:
            # 忽略用户消息里的不完整 URL，由发布工具报告缺少可用引用。
            continue
    return images, records


class LookupArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=AI_SEARCH_MAX_TOOL_QUERY_LENGTH, description="用空格分隔的简短检索词")


class IndexSearchArguments(LookupArguments):
    limit: int = Field(default=AI_SEARCH_DEFAULT_TOOL_LIMIT, ge=1, le=WEBUI_SEARCH_MAX_LIMIT)


class OfficialSearchArguments(LookupArguments):
    limit: int = Field(default=AI_SEARCH_DEFAULT_TOOL_LIMIT, ge=1, le=OFFICIAL_DOCS_SEARCH_MAX_LIMIT)


class DocumentReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    paths: List[str] = Field(min_length=1, max_length=OFFICIAL_DOCS_MAX_READ_COUNT, description="检索结果中的文档路径")
    offset: int = Field(default=0, ge=0, description="从 snippet_offset 跳到命中位置，或从 next_offset 继续读取")


class ConfigReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    paths: List[str] = Field(
        min_length=1,
        max_length=LOCAL_CONFIG_MAX_READ_COUNT,
        description="点号分隔的配置节或字段，列表使用下标，例如 chat.reply_timing、api_providers.0",
    )


_CORE_TOOLS: List[ToolDefinitionInput] = [
    {
        "name": LOOKUP_HELP_TOOL,
        "description": (
            "Find WebUI destinations and read the top two official documentation matches at their relevant "
            "offsets in one call. Prefer this for an initial product question. Use narrow search or read tools "
            "for follow-up evidence when needed."
        ),
        "parameters": LookupArguments.model_json_schema(),
    },
    {
        "name": "search_webui_index",
        "description": "搜索 WebUI 页面和配置字段，返回候选 ID、路径、类型和选项信息。",
        "parameters": IndexSearchArguments.model_json_schema(),
    },
    {
        "name": "search_official_docs",
        "description": f"搜索 {OFFICIAL_DOCS_BASE_URL} 的 MaiBot 官方文档，返回路径和相关片段。",
        "parameters": OfficialSearchArguments.model_json_schema(),
    },
    {
        "name": "read_official_docs",
        "description": f"按路径读取 {OFFICIAL_DOCS_BASE_URL} 的正文；next_offset 表示有后续内容。",
        "parameters": DocumentReadArguments.model_json_schema(),
    },
    {
        "name": "read_local_config",
        "description": "读取 bot_config.toml 与 model_config.toml 中实际生效的字段，隐藏密钥和令牌。",
        "parameters": ConfigReadArguments.model_json_schema(),
    },
]


async def emit_progress(
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
    raw_query = arguments.get("query")
    query = raw_query.strip()[:AI_SEARCH_MAX_TOOL_QUERY_LENGTH] if isinstance(raw_query, str) else ""
    raw_paths = arguments.get("paths")
    targets = (
        [path for path in raw_paths[:AI_SEARCH_PROGRESS_MAX_TARGETS] if isinstance(path, str)]
        if isinstance(raw_paths, list)
        else []
    )

    titles: List[str] = []
    count: int | None = None
    error = ""
    if payload is not None:
        error = payload.get("error", "")
        documents = payload.get("documents")
        if documents is not None:
            count = len(documents)
            titles = [document["title"] for document in documents[:AI_SEARCH_PROGRESS_MAX_TITLES]]
        plugins = payload.get("plugins")
        if plugins is None and "plugin" in payload:
            plugins = [payload["plugin"]]
        if plugins is not None:
            count = len(plugins)
            titles = [plugin["name"] for plugin in plugins[:AI_SEARCH_PROGRESS_MAX_TITLES]]
    plugin_id = arguments.get("plugin_id")
    if isinstance(plugin_id, str):
        targets = [plugin_id]

    return AISearchProgressEvent(
        stage="tool",
        status="failed" if error else status,
        tool=tool_call.func_name,
        query=query,
        targets=targets,
        titles=titles,
        count=count,
        error=error,
        install_request=payload.get("install_request") if payload is not None else None,
        config_update=payload.get("config_update") if payload is not None else None,
    )


async def _lookup_help(
    document_store: AISearchDocumentStore, query: str, candidates: List[AISearchCandidate]
) -> Dict[str, Any]:
    """把索引扫描与文档定位并行执行，随后读取命中位置的正文。"""

    async def read_matches() -> Dict[str, Any]:
        documents: List[Dict[str, Any]] = list(match_help_references(query))
        try:
            hits = await document_store.search_official_docs(query, limit=2)
            for hit in hits:
                for document in await document_store.read_official_docs([hit["path"]], hit["snippet_offset"]):
                    if len(document["content"]) > HELP_LOOKUP_MAX_CHARS_PER_DOC:
                        document = {
                            **document,
                            "content": document["content"][:HELP_LOOKUP_MAX_CHARS_PER_DOC],
                            "next_offset": document["offset"] + HELP_LOOKUP_MAX_CHARS_PER_DOC,
                        }
                    documents.append(document)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("答疑组合检索读取文档失败", error=str(exc))
            return {"documents": documents, "docs_error": "暂时无法读取官方文档"}
        return {"documents": documents}

    async with asyncio.TaskGroup() as group:
        navigation = group.create_task(asyncio.to_thread(document_store.search_candidates, query, candidates, 4))
        documents = group.create_task(read_matches())
    return {"navigation": navigation.result(), **documents.result()}


async def _execute_agent_tool(
    tool_call: ToolCall,
    candidates: List[AISearchCandidate],
    document_store: AISearchDocumentStore,
    market_store: AISearchMarketStore,
) -> Dict[str, Any]:
    """执行白名单内的 Agent 工具；安装只能生成待手动确认的请求。"""

    name = tool_call.func_name
    arguments = tool_call.args or {}
    if name in PLUGIN_TOOL_NAMES:
        try:
            return await market_store.execute(name, arguments)
        except HTTPException as exc:
            logger.warning("插件工具调用失败", tool=name, status=exc.status_code)
            # 配置校验错误可能包含敏感值，不把原始错误交给模型或进度日志。
            return {"error": f"插件服务拒绝操作（HTTP {exc.status_code}），请在插件管理页查看具体错误"}
        except ValidationError:
            return {"error": "插件工具参数无效，请按工具定义提交"}
        except (OSError, ValueError, httpx.HTTPError) as exc:
            logger.warning("插件市场工具调用失败", tool=name, error=str(exc))
            return {"error": f"插件市场查询失败: {exc}"}

    try:
        if name == LOOKUP_HELP_TOOL:
            lookup = LookupArguments.model_validate(arguments)
            return await _lookup_help(document_store, lookup.query, candidates)
        if name == "search_webui_index":
            index_search = IndexSearchArguments.model_validate(arguments)
            # 候选索引的分词与扫描放到线程里，避免占住 WebUI 事件循环。
            documents = await asyncio.to_thread(
                document_store.search_candidates, index_search.query, candidates, index_search.limit
            )
            return {"query": index_search.query, "documents": documents}
        if name == "read_local_config":
            config_read = ConfigReadArguments.model_validate(arguments)
            return {"documents": await asyncio.to_thread(read_local_config, config_read.paths)}
        if name == "search_official_docs":
            doc_search = OfficialSearchArguments.model_validate(arguments)
            return {
                "query": doc_search.query,
                "documents": await document_store.search_official_docs(doc_search.query, doc_search.limit),
            }
        if name == "read_official_docs":
            doc_read = DocumentReadArguments.model_validate(arguments)
            return {"documents": await document_store.read_official_docs(doc_read.paths, doc_read.offset)}
    except ValidationError:
        return {"error": "查询参数无效，请按工具定义提交"}
    except (httpx.HTTPError, ValueError) as exc:
        if name not in {"search_official_docs", "read_official_docs"}:
            raise
        logger.warning("官方文档工具调用失败", tool=name, error=str(exc))
        return {"error": f"暂时无法读取官方文档: {exc}"}

    raise ValueError(f"已声明的工具缺少执行器: {name}")


ReadTool = Callable[[ToolCall], Awaitable[Dict[str, Any]]]
ToolExecutor = Callable[[ToolCall, ReadTool], Awaitable[Dict[str, Any]]]


class AISearchToolRunner:
    """在一次答疑内调度工具，并记录可复用的读取结果。"""

    def __init__(
        self,
        request: AISearchRequest,
        harness: HelpHarness,
        document_store: AISearchDocumentStore,
        market_store: AISearchMarketStore,
        progress_callback: AISearchProgressCallback | None,
    ) -> None:
        self.request = request
        self.harness = harness
        self.document_store = document_store
        self.market_store = market_store
        self.progress_callback = progress_callback
        self.read_calls: Dict[Tuple[str, str], str] = {}
        self.artifact_store = HelpArtifactStore()
        self.image_urls, self.record_paths = _user_references(request.query)

    def definitions(self) -> List[ToolDefinitionInput]:
        tools = [*_CORE_TOOLS, *build_harness_tools()]
        if self.request.help_session_id is not None:
            tools.append(build_help_artifact_tool())
            if "troubleshoot" in self.harness.active_skills:
                tools.extend(build_diagnostic_tools())
        if "plugin" in self.harness.active_skills:
            tools.extend(
                tool
                for tool in build_market_tools()
                if self.request.allow_plugin_config_write or tool["name"] != _PLUGIN_CONFIG_WRITE_TOOL
            )
        return tools

    async def run_round(self, calls: List[ToolCall], advertised: Set[str], limit: int) -> List[Dict[str, Any]]:
        """并行执行相邻的独立读取，按模型请求顺序返回结果。"""

        async def execute(call: ToolCall, read: ReadTool | None = None) -> Dict[str, Any]:
            name = call.func_name
            if name in advertised and name in HARNESS_TOOLS:
                payload, event = self.harness.execute(name, call.args or {})
                if event is not None:
                    await emit_progress(self.progress_callback, event)
                return payload

            await emit_progress(self.progress_callback, _build_tool_progress_event(call, "started"))
            if name not in advertised:
                if name == _PLUGIN_CONFIG_WRITE_TOOL and not self.request.allow_plugin_config_write:
                    payload = {"error": "本轮未启用插件配置写入权限"}
                elif name in DIAGNOSTIC_TOOL_NAMES:
                    payload = {"error": "仅在独立答疑会话加载 troubleshoot 技能后可读取诊断数据"}
                else:
                    payload = {"error": "本步未提供该工具"}
            elif read is not None and name in _REUSABLE_READ_TOOLS:
                payload = await read(call)
            else:
                payload = await self._execute(call)
            await emit_progress(self.progress_callback, _build_tool_progress_event(call, "completed", payload))
            return payload

        results: List[Dict[str, Any]] = []
        for parallel, batch in groupby(calls[:limit], key=lambda call: call.func_name in _PARALLEL_READ_TOOLS):
            if parallel:
                results.extend(await self._run_read_batch(list(batch), execute))
            else:
                for call in batch:
                    results.append(await execute(call))

        # 供应商原生记录中的超额调用也必须逐一配对。
        results.extend({"error": "本轮工具执行数量已达上限"} for _ in calls[limit:])
        return results

    async def _execute(self, call: ToolCall) -> Dict[str, Any]:
        arguments = call.args or {}
        if call.func_name in DIAGNOSTIC_TOOL_NAMES:
            record_path = arguments.get("record_path")
            if call.func_name == "read_reasoning_record" and record_path is not None:
                if not isinstance(record_path, str) or not (
                    record_path in self.record_paths
                    or re.search(rf"(?<![\w./-]){re.escape(record_path)}(?![\w./-])", self.request.query)
                ):
                    return {"error": "推理文件路径必须由用户在本轮明确提供"}
            return await execute_diagnostic_tool(call.func_name, arguments)
        if call.func_name == PUBLISH_ARTIFACT_TOOL:
            content = arguments.get("content")
            if arguments.get("kind") == "image" and (
                not isinstance(content, str) or content.strip() not in self.image_urls
            ):
                return {"error": "图片地址必须来自本轮用户输入"}
            try:
                artifact = self.artifact_store.publish(arguments)
            except ValueError as exc:
                return {"error": str(exc)}
            # 展示内容由后端带回页面，工具消息只保留发布回执。
            return {"published": artifact.kind}
        return await _execute_agent_tool(call, self.request.candidates, self.document_store, self.market_store)

    async def _run_read_batch(self, calls: List[ToolCall], execute: ToolExecutor) -> List[Dict[str, Any]]:
        pending: Dict[Tuple[str, str], Tuple[str, asyncio.Task[Dict[str, Any]]]] = {}

        async with asyncio.TaskGroup() as group:

            async def read(call: ToolCall) -> Dict[str, Any]:
                key = (call.func_name, json.dumps(call.args or {}, sort_keys=True, ensure_ascii=False))
                if key in self.read_calls:
                    return {"reused_call_id": self.read_calls[key], "message": "请使用该调用已返回的资料"}

                if key not in pending:
                    pending[key] = (call.call_id, group.create_task(self._execute(call)))
                original_id, task = pending[key]
                payload = await task
                if payload.get("error") or payload.get("docs_error"):
                    return payload

                self.read_calls[key] = original_id
                if original_id == call.call_id:
                    return payload
                return {"reused_call_id": original_id, "message": "请使用该调用已返回的资料"}

            results = [group.create_task(execute(call, read)) for call in calls]
        return [result.result() for result in results]
