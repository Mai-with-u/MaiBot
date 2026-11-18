from math import ceil
from typing import Any, Dict

from src.config.model_configs import APIProvider
from src.llm_models.exceptions import RespParseException
from src.llm_models.model_client.base_client import ClientProviderRegistration, client_registry
from src.llm_models.model_client.plugin_client import PluginLLMClient
from src.web_search.config import WebSearchConfigError, WebServiceConfig
from src.web_search.models import PageResult, SearchHit, SearchResult, WebSearchError


def find_web_plugin(adapter: str) -> ClientProviderRegistration | None:
    owner, _, client_type = adapter.removeprefix("plugin:").partition("/")
    registration = client_registry.client_registry.get(client_type)
    if registration and registration.owner_plugin_id == owner and registration.formats.web:
        return registration
    return None


class PluginWebAdapter:
    def __init__(self, name: str, config: WebServiceConfig) -> None:
        registration = find_web_plugin(config.adapter)
        if registration is None:
            raise WebSearchConfigError(f"联网插件 {config.adapter} 未加载或未声明联网能力")
        # 使用该联网服务自己的连接信息，不与同名模型提供商共享客户端缓存。
        client = registration.factory(APIProvider(
            name=name, client_type=registration.client_type, base_url=config.base_url,
            api_key=config.api_key, timeout=ceil(config.timeout),
        ))
        if not isinstance(client, PluginLLMClient):
            raise WebSearchConfigError("联网插件未注册可调用的客户端")
        self.client = client

    async def _invoke(self, operation: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if operation not in self.client.formats.web:
            raise WebSearchConfigError(f"联网插件未声明操作 {operation}")
        try:
            return await self.client.invoke_web(operation, args)
        except RespParseException as exc:
            raise WebSearchError(f"联网插件调用 {operation} 失败，请查看插件日志") from exc

    async def search(self, query: str, count: int) -> SearchResult:
        result = await self._invoke("web_search", {"query": query, "max_results": count})
        hits = result["results"]
        if not isinstance(hits, list):
            raise WebSearchError("联网插件的 results 必须是数组")
        return SearchResult(query, [SearchHit(**item) for item in hits[:count]])

    async def fetch(self, url: str) -> PageResult:
        result = await self._invoke("web_fetch", {"url": url})
        return PageResult(url=result["url"], title=result["title"], content=result["content"])
