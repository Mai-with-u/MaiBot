"""统一结果和错误类型，保留摘要与页面正文的语义区别。"""

from dataclasses import dataclass, field
from typing import List, Optional


class WebSearchError(Exception):
    """可以安全返回模型的联网错误，不包含密钥或上游响应体。"""


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    summary: str = ""
    published_at: Optional[str] = None


@dataclass
class SearchResult:
    query: str
    results: List[SearchHit] = field(default_factory=list)
    content_kind: str = "search_results"


@dataclass
class PageResult:
    url: str
    title: str
    content: str
    content_kind: str = "page_text"
