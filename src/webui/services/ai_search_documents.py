"""WebUI AI 搜索使用的候选索引与官方文档仓库。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Tuple
import asyncio
import re
import time

import httpx


OFFICIAL_DOCS_BUNDLE_URL = "https://docs.mai-mai.org/llms-full.txt"
OFFICIAL_DOCS_BASE_URL = "https://docs.mai-mai.org"
OFFICIAL_DOCS_CACHE_TTL_SECONDS = 600.0
OFFICIAL_DOCS_MAX_BUNDLE_SIZE = 2_000_000
OFFICIAL_DOCS_MAX_READ_SIZE = 8_000


class SearchCandidate(Protocol):
    """文档仓库使用的最小候选项结构。"""

    id: str
    title: str
    description: str
    category: str
    document: str


@dataclass(frozen=True, slots=True)
class OfficialDocument:
    """从官方 LLM 文档包解析出的单篇文档。"""

    path: str
    title: str
    content: str


class AISearchDocumentStore:
    """封装候选索引检索、官方文档下载和短期缓存。"""

    def __init__(self) -> None:
        self._official_docs_cache: Tuple[float, List[OfficialDocument]] | None = None
        self._official_docs_lock = asyncio.Lock()

    @staticmethod
    def search_candidates(
        query: str,
        candidates: List[SearchCandidate],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """在本次请求提供的候选文档中执行确定性关键词检索。"""

        terms = list(dict.fromkeys(re.findall(r"[\w.\-]+", query.lower())))
        if not terms:
            return []

        ranked_candidates: List[Tuple[int, SearchCandidate]] = []
        for candidate in candidates:
            title = candidate.title.lower()
            category = candidate.category.lower()
            description = candidate.description.lower()
            document = candidate.document.lower()
            score = sum(
                5 * int(term in title)
                + 3 * int(term in category)
                + 2 * int(term in description)
                + int(term in document)
                for term in terms
            )
            if score > 0:
                ranked_candidates.append((score, candidate))

        ranked_candidates.sort(key=lambda item: (-item[0], item[1].title))
        return [
            {
                "id": candidate.id,
                "title": candidate.title,
                "category": candidate.category,
                "summary": candidate.description,
            }
            for _, candidate in ranked_candidates[:limit]
        ]

    @staticmethod
    def read_candidates(ids: Any, candidates: List[SearchCandidate], limit: int) -> List[Dict[str, str]]:
        """按 ID 读取候选正文，忽略越界、重复和不存在的 ID。"""

        if not isinstance(ids, list):
            return []
        candidate_map = {candidate.id: candidate for candidate in candidates}
        documents: List[Dict[str, str]] = []
        seen_ids: set[str] = set()
        for raw_id in ids:
            document_id = str(raw_id).strip()
            candidate = candidate_map.get(document_id)
            if candidate is None or document_id in seen_ids:
                continue
            seen_ids.add(document_id)
            documents.append(
                {
                    "id": candidate.id,
                    "title": candidate.title,
                    "category": candidate.category,
                    "content": candidate.document or candidate.description,
                }
            )
            if len(documents) >= limit:
                break
        return documents

    async def search_official_docs(self, query: str, limit: int) -> List[Dict[str, str]]:
        """搜索官方文档并返回相关片段。"""

        documents = await self._load_official_docs()
        terms = list(dict.fromkeys(re.findall(r"[\w.\-]+", query.lower())))
        if not terms:
            return []

        ranked_documents: List[Tuple[int, OfficialDocument]] = []
        for document in documents:
            title = document.title.lower()
            path = document.path.lower()
            content = document.content.lower()
            score = sum(
                8 * int(term in title) + 4 * int(term in path) + int(term in content)
                for term in terms
            )
            if score > 0:
                ranked_documents.append((score, document))
        ranked_documents.sort(key=lambda item: (-item[0], item[1].title))
        return [
            {
                "path": document.path,
                "title": document.title,
                "url": self.build_official_doc_url(document.path),
                "snippet": self._build_document_snippet(document.content, terms),
            }
            for _, document in ranked_documents[:limit]
        ]

    async def read_official_docs(self, paths: Any) -> List[Dict[str, str]]:
        """按路径读取官方文档正文并限制单篇返回长度。"""

        if not isinstance(paths, list):
            return []
        documents = await self._load_official_docs()
        document_map = {document.path: document for document in documents}
        results: List[Dict[str, str]] = []
        seen_paths: set[str] = set()
        for raw_path in paths:
            path = str(raw_path).strip()
            document = document_map.get(path)
            if document is None or path in seen_paths:
                continue
            seen_paths.add(path)
            results.append(
                {
                    "source_id": document.path,
                    "title": document.title,
                    "url": self.build_official_doc_url(document.path),
                    "content": document.content[:OFFICIAL_DOCS_MAX_READ_SIZE],
                }
            )
            if len(results) >= 4:
                break
        return results

    def build_sources(self, source_ids: List[str]) -> List[Dict[str, str]]:
        """把已读取的官方文档 ID 转成可点击来源。"""

        if self._official_docs_cache is None:
            return []
        document_map = {document.path: document for document in self._official_docs_cache[1]}
        return [
            {
                "title": document_map[source_id].title,
                "url": self.build_official_doc_url(source_id),
            }
            for source_id in source_ids
            if source_id in document_map
        ]

    async def _load_official_docs(self) -> List[OfficialDocument]:
        """下载并短期缓存官方 LLM 文档包。"""

        now = time.monotonic()
        if self._official_docs_cache is not None and self._official_docs_cache[0] > now:
            return self._official_docs_cache[1]

        async with self._official_docs_lock:
            now = time.monotonic()
            if self._official_docs_cache is not None and self._official_docs_cache[0] > now:
                return self._official_docs_cache[1]
            async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
                response = await client.get(OFFICIAL_DOCS_BUNDLE_URL)
                response.raise_for_status()
            if len(response.content) > OFFICIAL_DOCS_MAX_BUNDLE_SIZE:
                raise ValueError("官方文档包大小超出限制")
            documents = self._parse_official_docs_bundle(response.text)
            if not documents:
                raise ValueError("官方文档包中没有可读取的文档")
            self._official_docs_cache = (now + OFFICIAL_DOCS_CACHE_TTL_SECONDS, documents)
            return documents

    @staticmethod
    def _parse_official_docs_bundle(bundle: str) -> List[OfficialDocument]:
        """解析官方站点提供的 `llms-full.txt` 文档包。"""

        documents: List[OfficialDocument] = []
        pattern = re.compile(r"(?:\A|\n)---\s*\nurl:\s*(/[^\n]+)\n---\s*\n")
        matches = list(pattern.finditer(bundle))
        for index, match in enumerate(matches):
            path = match.group(1).strip()
            content_end = matches[index + 1].start() if index + 1 < len(matches) else len(bundle)
            content = bundle[match.end() : content_end].strip()
            title_match = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
            title = title_match.group(1).strip() if title_match else path.rsplit("/", 1)[-1]
            documents.append(OfficialDocument(path=path, title=title, content=content))
        return documents

    @staticmethod
    def _build_document_snippet(content: str, terms: List[str]) -> str:
        """截取首个命中词附近的官方文档片段。"""

        normalized_content = content.lower()
        positions = [normalized_content.find(term) for term in terms if term in normalized_content]
        start = max(0, min(positions) - 160) if positions else 0
        return re.sub(r"\s+", " ", content[start : start + 500]).strip()

    @staticmethod
    def build_official_doc_url(path: str) -> str:
        """把 LLM 文档路径转换为面向用户的文档站页面 URL。"""

        return f"{OFFICIAL_DOCS_BASE_URL}{path.removesuffix('.md')}"
