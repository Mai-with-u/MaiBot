"""Firecrawl v2 Search / Scrape API。"""

from src.web_search.models import PageResult, SearchHit, SearchResult, WebSearchError

from .http import HTTPAdapter


class FirecrawlAdapter(HTTPAdapter):
    def endpoint(self, action: str) -> str:
        base = (self.config.base_url or "https://api.firecrawl.dev").rstrip("/")
        return f"{base if base.endswith('/v2') else base + '/v2'}/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        if len(query) > 500:
            raise WebSearchError("Firecrawl 的 query 不能超过 500 个字符")
        data = await self.post(
            self.endpoint("search"), {"query": query, "limit": max_results, "sources": ["web"]},
        )
        if data["success"] is not True:
            raise WebSearchError("Firecrawl 返回业务错误")
        return SearchResult(
            query=query,
            results=[
                SearchHit(title=item["title"], url=item["url"], snippet=item["description"])
                for item in data["data"]["web"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(self.endpoint("scrape"), {"url": url, "formats": ["markdown"]})
        if data["success"] is not True:
            raise WebSearchError("Firecrawl 未能提取指定网页的正文")
        item = data["data"]
        return PageResult(url=url, title=item.get("metadata", {}).get("title", ""), content=item["markdown"])
