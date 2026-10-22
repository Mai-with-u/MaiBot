"""统一联网工具的协议、失败语义和原文回查回归测试。"""

from typing import Any, Dict, Optional
import asyncio
import json

import httpx
import pytest

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation
from src.maisaka.tool_result_store import ToolResultStore
from src.config.config import ModelConfig
from src.config.default_model_config import create_default_model_config
from src.web_search.config import WebConfig, WebRuntimeConfig, resolve_web_search_config
from src.web_search.models import WebSearchError
from src.web_search.provider import WebSearchToolProvider
from src.web_search.service import WebSearchService


class FakeMCP:
    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        self.data = data
        self.calls = []

    async def call_tool_invocation(self, invocation, context=None):
        self.calls.append((invocation, context))
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=self.data)


def config(adapter="deepseek", **kwargs):
    service = (
        {"adapter": adapter, "server_ref": "stepsearch"}
        if adapter == "stepsearch_mcp"
        else {
            "adapter": adapter,
            "api_key": "test-secret",
            **kwargs,
        }
    )
    return WebRuntimeConfig(enabled=True, search="main", fetch="main", preview_chars=500, services={"main": service})


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter,endpoint", [("deepseek", "/v1/tools/search"), ("tavily", "/search")])
async def test_search_contract_and_request_mapping(adapter, endpoint):
    def handler(request):
        assert request.url.path == endpoint
        assert request.headers["Authorization"] == "Bearer test-secret"
        payload = json.loads(request.content)
        assert payload["query"] == "uv sync"
        if adapter == "deepseek":
            assert payload["count"] == 2
            assert payload["need_content"] is False
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com",
                            "title": "文档",
                            "snippet": "片段",
                            "summary": "摘要",
                            "date": None,
                        }
                    ]
                },
            )
        assert payload["max_results"] == 2
        assert payload["search_depth"] == "advanced"
        assert payload["include_answer"] is False
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com",
                        "title": "文档",
                        "content": "片段",
                    }
                ]
            },
        )

    service = WebSearchService(FakeMCP(), httpx.MockTransport(handler))
    result = await service.execute(
        config(adapter, search_depth="advanced"),
        ToolInvocation(
            tool_name="web_search",
            arguments={"query": " uv sync ", "max_results": 2},
        ),
    )
    assert result.query == "uv sync"
    assert result.results[0].published_at is None
    assert result.results[0].snippet == "片段"
    assert result.content_kind == "search_results"


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["deepseek", "tavily"])
async def test_fetch_preserves_body_and_supports_session_scoped_continuation(adapter):
    body = "前文" * 400 + "准确价格：123 元" + "后文" * 400

    def handler(request):
        payload = json.loads(request.content)
        if adapter == "deepseek":
            assert request.url.path == "/v1/tools/fetch"
            assert payload == {"url": "https://example.com/page"}
            return httpx.Response(200, json={"title": "标题", "content": body})
        assert payload["urls"] == ["https://example.com/page"]
        return httpx.Response(200, json={"results": [{"url": "https://example.com/page", "raw_content": body}]})

    settings = config(adapter, base_url="https://example.com/v1" if adapter == "deepseek" else "")
    store = ToolResultStore()
    provider = WebSearchToolProvider(WebSearchService(FakeMCP(), httpx.MockTransport(handler)), lambda: settings, store)
    result = await provider.invoke(
        ToolInvocation(
            tool_name="web_fetch",
            arguments={"url": "https://example.com/page"},
            session_id="chat-a",
        )
    )
    assert result.success
    assert result.structured_content["truncated"] is True
    assert result.structured_content["next_offset"] == 500
    assert "read_tool_result" in result.content
    assert store.get("chat-a", result.metadata["result_id"]).content == body
    assert store.get("chat-b", result.metadata["result_id"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_http_failures_do_not_leak_secrets_or_fallback(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="credentials: test-secret")

    settings = config()
    provider = WebSearchToolProvider(WebSearchService(FakeMCP(), httpx.MockTransport(handler)), lambda: settings)
    result = await provider.invoke(ToolInvocation(tool_name="web_search", session_id="a", arguments={"query": "test"}))
    assert not result.success
    assert str(status) in result.error_message
    assert "test-secret" not in result.error_message
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"results": None}, {"results": [{"title": "missing URL"}]}])
async def test_malformed_upstream_is_not_reported_as_empty_search(payload):
    service = WebSearchService(FakeMCP(), httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    with pytest.raises(WebSearchError, match="接口协议"):
        await service.execute(config(), ToolInvocation(tool_name="web_search", arguments={"query": "test"}))


@pytest.mark.asyncio
async def test_empty_search_is_success_but_failed_extraction_is_error():
    service = WebSearchService(FakeMCP(), httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []})))
    assert (
        await service.execute(config(), ToolInvocation(tool_name="web_search", arguments={"query": "test"}))
    ).results == []
    with pytest.raises(WebSearchError, match="未能提取"):
        await service.execute(
            config("tavily"), ToolInvocation(tool_name="web_fetch", arguments={"url": "https://example.com"})
        )


@pytest.mark.asyncio
async def test_stepsearch_uses_named_existing_connection_and_preserves_context():
    mcp = FakeMCP({"results": [{"title": "标题", "url": "https://example.com", "snippet": "正文片段", "time": None}]})
    service = WebSearchService(mcp)
    context = ToolExecutionContext(session_id="chat-a")
    invocation = ToolInvocation(tool_name="web_search", arguments={"query": "test"}, call_id="c1", session_id="chat-a")
    result = await service.execute(config("stepsearch_mcp"), invocation, context)
    assert result.results[0].snippet == "正文片段"
    native, forwarded_context = mcp.calls[0]
    assert native.arguments == {"query": "test", "n": 5}
    assert native.metadata["mcp_server_name"] == "stepsearch"
    assert native.call_id == "c1" and native.session_id == "chat-a"
    assert forwarded_context is context
    mcp.data = {"code": 0, "page": {"url": "https://example.com", "title": "标题", "markdown": "正文"}}
    page = await service.execute(
        config("stepsearch_mcp"),
        ToolInvocation(
            tool_name="web_fetch",
            arguments={"url": "https://example.com"},
        ),
    )
    assert page.content == "正文"


@pytest.mark.asyncio
async def test_deadline_and_cancellation():
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json={"results": []})

    service = WebSearchService(FakeMCP(), httpx.MockTransport(handler))
    invocation = ToolInvocation(tool_name="web_search", arguments={"query": "test"})
    with pytest.raises(WebSearchError, match="超过"):
        await service.execute(config(timeout=0.01), invocation)
    task = asyncio.create_task(service.execute(config(), invocation))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_provider_visibility_follows_reload_and_validates_before_network():
    settings = WebRuntimeConfig()
    mcp = FakeMCP()
    provider = WebSearchToolProvider(WebSearchService(mcp), lambda: settings)
    assert await provider.list_tools() == []
    settings = config("stepsearch_mcp")
    assert {tool.name for tool in await provider.list_tools()} == {"web_search", "web_fetch"}
    for arguments in ({"query": ""}, {"query": "test", "max_results": True}, {"query": "test", "provider": "x"}):
        result = await provider.invoke(ToolInvocation(tool_name="web_search", arguments=arguments, session_id="a"))
        assert not result.success
    result = await provider.invoke(
        ToolInvocation(tool_name="web_fetch", arguments={"url": "file:///secret"}, session_id="a")
    )
    assert not result.success
    assert not mcp.calls


@pytest.mark.asyncio
async def test_service_definition_requires_task_assignment_and_revocation_blocks_calls():
    settings = create_default_model_config(ModelConfig)
    settings.web = WebConfig(services={"step": {"adapter": "stepsearch_mcp", "server_ref": "stepsearch"}})
    mcp = FakeMCP({"results": []})
    provider = WebSearchToolProvider(
        WebSearchService(mcp), lambda: resolve_web_search_config(settings.web, settings.model_task_config.web_search)
    )
    invocation = ToolInvocation(tool_name="web_search", arguments={"query": "test"}, session_id="a")
    assert await provider.list_tools() == []
    assert not (await provider.invoke(invocation)).success
    assert not mcp.calls

    settings.model_task_config.web_search.model_list = ["step"]
    assert {tool.name for tool in await provider.list_tools()} == {"web_search", "web_fetch"}
    assert (await provider.invoke(invocation)).success
    assert len(mcp.calls) == 1

    settings.model_task_config.web_search.model_list = []
    assert await provider.list_tools() == []
    assert not (await provider.invoke(invocation)).success
    assert len(mcp.calls) == 1


def test_task_rejects_llm_name_instead_of_search_service():
    settings = create_default_model_config(ModelConfig)
    data = settings.model_dump(exclude={"field_docs"})
    data["model_task_config"]["web_search"]["model_list"] = [settings.models[0].name]
    with pytest.raises(ValueError, match="不存在的联网服务"):
        ModelConfig.model_validate(data)


@pytest.mark.asyncio
async def test_missing_or_mismatched_session_is_rejected_before_network():
    mcp = FakeMCP()
    provider = WebSearchToolProvider(WebSearchService(mcp), lambda: config("stepsearch_mcp"))
    invocation = ToolInvocation(tool_name="web_search", arguments={"query": "test"})
    assert not (await provider.invoke(invocation)).success
    invocation.session_id = "a"
    assert not (await provider.invoke(invocation, ToolExecutionContext(session_id="b"))).success
    assert not mcp.calls
