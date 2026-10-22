"""DeepSeek Search / Fetch API。"""

from ..models import PageResult, SearchHit, SearchResult, WebSearchError
from .http import HTTPAdapter


class DeepSeekAdapter(HTTPAdapter):
    def endpoint(self, action: str) -> str:
        base = (self.config.base_url or "https://api.deepseek.com").rstrip("/")
        # 同时接受官方根地址与已有 OpenAI 兼容连接常用的 /v1 地址。
        if not base.endswith("/v1"):
            base += "/v1"
        return f"{base}/tools/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        if len(query) > 128:
            raise WebSearchError("DeepSeek 搜索词不能超过 128 个字符，请缩短后重试")
        data = await self.post(
            self.endpoint("search"),
            {
                "query": query,
                "count": max_results,
                "need_content": False,
            },
        )
        return SearchResult(
            query=query,
            results=[
                SearchHit(
                    title=item["title"],
                    url=item["url"],
                    snippet=item["snippet"],
                    summary=item.get("summary", ""),
                    published_at=item.get("date"),
                )
                for item in data["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(self.endpoint("fetch"), {"url": url})
        return PageResult(url=url, title=data["title"], content=data["content"])
