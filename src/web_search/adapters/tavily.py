"""Tavily Search / Extract API。"""

from ..models import PageResult, SearchHit, SearchResult, WebSearchError
from .http import HTTPAdapter


class TavilyAdapter(HTTPAdapter):
    def endpoint(self, action: str) -> str:
        return f"{(self.config.base_url or 'https://api.tavily.com').rstrip('/')}/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        data = await self.post(
            self.endpoint("search"),
            {
                "query": query,
                "max_results": max_results,
                "search_depth": self.config.search_depth,
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        return SearchResult(
            query=query,
            results=[
                SearchHit(
                    title=item["title"],
                    url=item["url"],
                    snippet=item["content"],
                    published_at=item.get("published_date"),
                )
                for item in data["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(
            self.endpoint("extract"),
            {
                "urls": [url],
                "extract_depth": self.config.extract_depth,
                "format": "markdown",
            },
        )
        if data.get("failed_results") or not data["results"]:
            raise WebSearchError("Tavily 未能提取指定网页的正文")
        item = data["results"][0]
        return PageResult(url=item["url"], title=item.get("title", ""), content=item["raw_content"])
