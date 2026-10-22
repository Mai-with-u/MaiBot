"""联网服务调用入口：选择服务、校验参数、规范化结果和限制调用时限。"""

from typing import Optional, Union
from urllib.parse import urlsplit
import asyncio

import httpx

from src.core.tooling import ToolExecutionContext, ToolInvocation

from .adapters.deepseek import DeepSeekAdapter
from .adapters.stepsearch import MCPCaller, StepSearchAdapter
from .adapters.tavily import TavilyAdapter
from .config import WebRuntimeConfig
from .models import PageResult, SearchResult, WebSearchError


def validate_url(url: str) -> str:
    """只接受无嵌入凭据的 HTTP(S) URL；本模块不直接请求目标网页。"""
    if not isinstance(url, str) or not url.strip() or len(url) > 8192:
        raise WebSearchError("url 必须是非空字符串且不超过 8192 个字符")
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise WebSearchError("url 必须是无用户名和密码的 HTTP(S) 地址")
    return url


class WebSearchService:
    def __init__(self, mcp: MCPCaller, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.mcp = mcp
        self.transport = transport

    async def execute(
        self,
        config: WebRuntimeConfig,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> Union[SearchResult, PageResult]:
        if not config.enabled:
            raise WebSearchError("统一联网功能未启用")
        if invocation.tool_name not in {"web_search", "web_fetch"}:
            raise WebSearchError("未知联网工具")
        search = invocation.tool_name == "web_search"
        name = config.search if search else config.fetch
        if not name or name not in config.services:
            raise WebSearchError("当前操作未配置联网服务")
        settings = config.services[name]
        if settings.adapter == "deepseek":
            adapter = DeepSeekAdapter(settings, self.transport)
        elif settings.adapter == "tavily":
            adapter = TavilyAdapter(settings, self.transport)
        else:
            adapter = StepSearchAdapter(settings, self.mcp, invocation, context)
        arguments = invocation.arguments
        try:
            async with asyncio.timeout(settings.timeout):
                if search:
                    if set(arguments) - {"query", "max_results"}:
                        raise WebSearchError("web_search 仅支持 query 和 max_results")
                    query = arguments.get("query")
                    count = arguments.get("max_results", 5)
                    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
                        raise WebSearchError("query 必须是非空字符串且不超过 1000 个字符")
                    if type(count) is not int or not 1 <= count <= 20:
                        raise WebSearchError("max_results 必须是 1 到 20 的整数")
                    result = await adapter.search(query.strip(), count)
                    for item in result.results:
                        validate_url(item.url)
                        if not all(isinstance(value, str) for value in (item.title, item.snippet, item.summary)):
                            raise WebSearchError("搜索服务返回了无效文本字段")
                        if item.published_at is not None and not isinstance(item.published_at, str):
                            raise WebSearchError("搜索服务返回了无效日期字段")
                    return result
                if set(arguments) != {"url"}:
                    raise WebSearchError("web_fetch 仅支持必填参数 url")
                page = await adapter.fetch(validate_url(arguments["url"]))
                validate_url(page.url)
                if not isinstance(page.title, str) or not isinstance(page.content, str) or not page.content.strip():
                    raise WebSearchError("网页读取服务没有返回有效正文")
                return page
        except TimeoutError:
            raise WebSearchError(f"联网服务调用超过 {settings.timeout:g} 秒") from None
        except (KeyError, TypeError, ValueError, AttributeError):
            # 协议错误必须报告，不能冒充零搜索结果，也不能回显含凭据的响应。
            raise WebSearchError(f"{settings.adapter} 返回数据不符合接口协议，或请求参数格式错误") from None
