"""Tavily Search / Extract API。"""

from jsonschema import Draft202012Validator
from typing import Any, Dict

from src.web_search.models import PageResult, SearchHit, SearchResult, WebSearchError
from .http import HTTPAdapter


SEARCH_OPTIONS = {
    "time_range": {"type": "string", "enum": ["day", "week", "month", "year"]},
    "include_domains": {"type": "array", "items": {"type": "string", "pattern": r"\S"}, "maxItems": 300},
    "exclude_domains": {"type": "array", "items": {"type": "string", "pattern": r"\S"}, "maxItems": 150},
}
FETCH_OPTIONS = {
    "query": {"type": "string", "pattern": r"\S", "description": "只提取与此问题相关的网页片段。"},
    "chunks_per_source": {"type": "integer", "minimum": 1, "maximum": 5, "description": "需要同时提供 query。"},
}
FETCH_DEPENDENCIES = {"chunks_per_source": ["query"]}


def validate_options(options: Dict[str, Any], *, search: bool) -> None:
    schema = {
        "properties": SEARCH_OPTIONS if search else FETCH_OPTIONS,
        "additionalProperties": False,
    }
    if not search:
        schema["dependentRequired"] = FETCH_DEPENDENCIES
    for error in Draft202012Validator(schema).iter_errors(options):
        raise WebSearchError(f"Tavily 参数格式错误：{error.json_path} {error.message}")


class TavilyAdapter(HTTPAdapter):
    def endpoint(self, action: str) -> str:
        return f"{(self.config.base_url or 'https://api.tavily.com').rstrip('/')}/{action}"

    async def search(self, query: str, max_results: int, **options: Any) -> SearchResult:
        validate_options(options, search=True)
        data = await self.post(
            self.endpoint("search"),
            {
                "query": query,
                "max_results": max_results,
                "search_depth": self.config.search_depth,
                "include_answer": False,
                "include_raw_content": False,
                "include_published_date": True,
                **options,
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

    async def fetch(self, url: str, **options: Any) -> PageResult:
        validate_options(options, search=False)
        data = await self.post(
            self.endpoint("extract"),
            {
                "urls": [url],
                "extract_depth": self.config.extract_depth,
                "format": "markdown",
                **options,
            },
        )
        if data.get("failed_results") or not data["results"]:
            raise WebSearchError("Tavily 未能提取指定网页的正文")
        item = data["results"][0]
        return PageResult(url=item["url"], title=item.get("title", ""), content=item["raw_content"])
