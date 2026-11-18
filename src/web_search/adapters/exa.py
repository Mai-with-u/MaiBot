"""Exa Search / Contents API。"""

from typing import Dict

from src.web_search.models import PageResult, SearchHit, SearchResult, WebSearchError

from .http import HTTPAdapter


class ExaAdapter(HTTPAdapter):
    def auth_headers(self) -> Dict[str, str]:
        return {"x-api-key": self.config.api_key}

    def endpoint(self, action: str) -> str:
        return f"{(self.config.base_url or 'https://api.exa.ai').rstrip('/')}/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        data = await self.post(
            self.endpoint("search"),
            {"query": query, "numResults": max_results, "type": "auto", "contents": {"highlights": True}},
        )
        return SearchResult(
            query=query,
            results=[
                SearchHit(
                    title=item["title"],
                    url=item["url"],
                    snippet="\n".join(item.get("highlights", [])),
                    summary=item.get("summary", ""),
                    published_at=item.get("publishedDate"),
                )
                for item in data["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(self.endpoint("contents"), {"urls": [url], "text": True})
        if not data["results"] or any(item["status"] != "success" for item in data.get("statuses", [])):
            raise WebSearchError("Exa 未能提取指定网页的正文")
        item = data["results"][0]
        return PageResult(url=item["url"], title=item["title"], content=item["text"])
