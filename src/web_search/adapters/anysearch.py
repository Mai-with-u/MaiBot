"""AnySearch v1 Search / Extract API。"""

from src.web_search.models import PageResult, SearchHit, SearchResult, WebSearchError

from .http import HTTPAdapter


class AnySearchAdapter(HTTPAdapter):
    def endpoint(self, action: str) -> str:
        base = (self.config.base_url or "https://api.anysearch.com").rstrip("/")
        return f"{base if base.endswith('/v1') else base + '/v1'}/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        if max_results > 10:
            raise WebSearchError("AnySearch 的 max_results 不能超过 10")
        data = await self.post(self.endpoint("search"), {"query": query, "max_results": max_results})
        if data["code"] != 0:
            raise WebSearchError("AnySearch 返回业务错误")
        return SearchResult(
            query=query,
            results=[
                SearchHit(title=item["title"], url=item["url"], snippet=item.get("snippet", ""))
                for item in data["data"]["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(self.endpoint("extract"), {"url": url})
        if data["code"] != 0:
            raise WebSearchError("AnySearch 未能提取指定网页的正文")
        item = data["data"]
        return PageResult(url=item["url"], title=item["title"], content=item["content"])
