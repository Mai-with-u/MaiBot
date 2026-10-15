"""WebUI AI 搜索使用的候选索引与官方文档仓库。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit
import asyncio
import re
import time

import httpx
import jieba

from src.common.logger import get_logger
from src.webui.utils.http_client import get_shared_ssl_context

from .ai_search_models import AISearchCandidate

logger = get_logger("webui.ai_search")

OFFICIAL_DOCS_BUNDLE_URL = "https://docs.mai-mai.org/llms-full.txt"
OFFICIAL_DOCS_BASE_URL = "https://docs.mai-mai.org"
OFFICIAL_DOCS_CACHE_TTL_SECONDS = 600.0
OFFICIAL_DOCS_MAX_BUNDLE_SIZE = 2_000_000
OFFICIAL_DOCS_MAX_READ_SIZE = 8_000
OFFICIAL_DOCS_MAX_READ_COUNT = 4
OFFICIAL_DOCS_SEARCH_MAX_LIMIT = 8
OFFICIAL_DOCS_SNIPPET_LEAD = 160
OFFICIAL_DOCS_SNIPPET_SIZE = 500
WEBUI_SEARCH_MAX_LIMIT = 10
AI_SEARCH_DEFAULT_TOOL_LIMIT = 6

_CJK_PATTERN = re.compile(r"[一-鿿]")
# 疑问词和泛化动词几乎出现在所有文档里，参与打分只会稀释真正的关键词
_QUERY_STOP_WORDS = frozenset(
    {
        "什么",
        "为什么",
        "为何",
        "怎么",
        "怎样",
        "如何",
        "哪里",
        "是否",
        "有没有",
        "可以",
        "无法",
        "不能",
        "一个",
        "这个",
        "那个",
    }
)


def _split_query_terms(query: str) -> List[str]:
    """把检索词拆成去重后的小写关键词，没有空格的中文句子用 jieba 切分。"""

    terms: List[str] = []
    for token in re.findall(r"[\w.\-]+", query.lower()):
        if not _CJK_PATTERN.search(token):
            terms.append(token)
            continue
        # 只保留两个字以上的中文词；原始整段也保留，以便命中完全一致的短语
        terms.append(token)
        terms.extend(
            word
            for word in jieba.lcut_for_search(token)
            if len(word) >= 2 and _CJK_PATTERN.search(word) and word not in _QUERY_STOP_WORDS
        )
    return [term for term in dict.fromkeys(terms) if term not in _QUERY_STOP_WORDS]


def _score_terms(terms: List[str], weighted_texts: List[Tuple[int, str]]) -> int:
    """按字段权重累加每个关键词的命中得分，文本需已转为小写。"""

    return sum(weight for term in terms for weight, text in weighted_texts if term in text)


@dataclass(slots=True)
class OfficialDocument:
    """从官方 LLM 文档包解析出的单篇文档。"""

    path: str
    title: str
    content: str
    # 检索用的小写文本只在解析文档包时计算一次，避免每次搜索重复转换整包正文
    path_lower: str = field(init=False, repr=False)
    title_lower: str = field(init=False, repr=False)
    content_lower: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.path_lower = self.path.lower()
        self.title_lower = self.title.lower()
        self.content_lower = self.content.lower()


class AISearchDocumentStore:
    """封装候选索引检索、官方文档下载和短期缓存。"""

    def __init__(self) -> None:
        self._official_docs_cache: Tuple[float, List[OfficialDocument]] | None = None
        self._official_docs_refresh_task: asyncio.Task[List[OfficialDocument]] | None = None

    @staticmethod
    def search_candidates(
        query: str,
        candidates: List[AISearchCandidate],
        limit: int,
    ) -> List[Dict[str, str]]:
        """在本次请求提供的候选文档中执行确定性关键词检索，并直接带回候选正文。"""

        terms = _split_query_terms(query)
        if not terms:
            return []

        ranked_candidates: List[Tuple[int, AISearchCandidate]] = []
        for candidate in candidates:
            score = _score_terms(
                terms,
                [
                    (5, candidate.title.lower()),
                    (3, candidate.category.lower()),
                    (2, candidate.description.lower()),
                    (1, candidate.document.lower()),
                ],
            )
            if score > 0:
                ranked_candidates.append((score, candidate))

        ranked_candidates.sort(key=lambda item: (-item[0], item[1].title))
        return [
            {
                "id": candidate.id,
                "title": candidate.title,
                "category": candidate.category,
                "content": candidate.document or candidate.description,
            }
            for _, candidate in ranked_candidates[:limit]
        ]

    async def search_official_docs(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """搜索官方文档并返回相关片段。"""

        documents = await self._load_official_docs()
        # 全量文档的分词与扫描放到线程里，避免占住 WebUI 事件循环
        return await asyncio.to_thread(self._rank_official_docs, documents, query, limit)

    def _rank_official_docs(
        self,
        documents: List[OfficialDocument],
        query: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """对已加载的官方文档执行确定性关键词检索。"""

        terms = _split_query_terms(query)
        if not terms:
            return []

        ranked_documents: List[Tuple[int, OfficialDocument]] = []
        for document in documents:
            score = _score_terms(
                terms,
                [
                    (8, document.title_lower),
                    (4, document.path_lower),
                    (1, document.content_lower),
                ],
            )
            if score > 0:
                ranked_documents.append((score, document))
        ranked_documents.sort(key=lambda item: (-item[0], item[1].title))

        results: List[Dict[str, Any]] = []
        for _, document in ranked_documents[:limit]:
            snippet_offset = self._find_snippet_offset(document, terms)
            results.append(
                {
                    "path": document.path,
                    "title": document.title,
                    "url": self.build_official_doc_url(document.path),
                    "length": len(document.content),
                    "snippet_offset": snippet_offset,
                    "snippet": re.sub(
                        r"\s+",
                        " ",
                        document.content[snippet_offset : snippet_offset + OFFICIAL_DOCS_SNIPPET_SIZE],
                    ).strip(),
                }
            )
        return results

    async def read_official_docs(self, paths: Any, offset: int = 0) -> List[Dict[str, Any]]:
        """按路径从指定偏移读取官方文档正文，并限制单次返回长度。"""

        if not isinstance(paths, list):
            return []
        documents = await self._load_official_docs()
        document_map = {document.path: document for document in documents}
        results: List[Dict[str, Any]] = []
        seen_paths: set[str] = set()
        for raw_path in paths:
            path = str(raw_path).strip()
            document = document_map.get(path)
            if document is None or path in seen_paths:
                continue
            seen_paths.add(path)
            end_offset = offset + OFFICIAL_DOCS_MAX_READ_SIZE
            result: Dict[str, Any] = {
                "source_id": document.path,
                "title": document.title,
                "url": self.build_official_doc_url(document.path),
                "offset": offset,
                "length": len(document.content),
                "content": document.content[offset:end_offset],
            }
            if end_offset < len(document.content):
                # 文档还有后续内容时告知下一段的起点，Agent 可以继续读取
                result["next_offset"] = end_offset
            results.append(result)
            if len(results) >= OFFICIAL_DOCS_MAX_READ_COUNT:
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

    def prewarm_official_docs(self) -> None:
        """在后台提前下载或刷新官方文档包，不等待结果。"""

        if not self._is_official_docs_cache_fresh():
            self._ensure_official_docs_refresh()

    def _is_official_docs_cache_fresh(self) -> bool:
        """判断官方文档缓存是否仍在有效期内。"""

        return self._official_docs_cache is not None and self._official_docs_cache[0] > time.monotonic()

    def _ensure_official_docs_refresh(self) -> "asyncio.Task[List[OfficialDocument]]":
        """确保同一时间只有一个文档包下载任务在运行。"""

        if self._official_docs_refresh_task is None or self._official_docs_refresh_task.done():
            self._official_docs_refresh_task = asyncio.create_task(self._download_official_docs())
            self._official_docs_refresh_task.add_done_callback(self._log_official_docs_refresh_failure)
        return self._official_docs_refresh_task

    @staticmethod
    def _log_official_docs_refresh_failure(task: "asyncio.Task[List[OfficialDocument]]") -> None:
        """记录后台刷新失败的原因；没有调用方等待时异常也不会被吞掉。"""

        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.warning(f"官方文档包下载失败: {exception}")

    async def _load_official_docs(self) -> List[OfficialDocument]:
        """返回官方文档包；缓存过期时先用旧数据并在后台刷新，只有首次加载才等待下载。"""

        if self._official_docs_cache is not None and self._is_official_docs_cache_fresh():
            return self._official_docs_cache[1]

        refresh_task = self._ensure_official_docs_refresh()
        if self._official_docs_cache is not None:
            return self._official_docs_cache[1]
        # 下载任务由所有请求共享，单个搜索被取消时不能连带取消它
        return await asyncio.shield(refresh_task)

    async def _download_official_docs(self) -> List[OfficialDocument]:
        """下载并解析官方 LLM 文档包，成功后更新缓存。"""

        # 复用共享 SSLContext，避免每次拉取文档包都重新加载整套 CA 证书（实测约 5s/次）
        async with httpx.AsyncClient(
            verify=get_shared_ssl_context(), follow_redirects=True, timeout=15.0
        ) as client:
            response = await client.get(OFFICIAL_DOCS_BUNDLE_URL)
            response.raise_for_status()
        if len(response.content) > OFFICIAL_DOCS_MAX_BUNDLE_SIZE:
            raise ValueError("官方文档包大小超出限制")
        documents = await asyncio.to_thread(self._parse_official_docs_bundle, response.text)
        if not documents:
            raise ValueError("官方文档包中没有可读取的文档")
        self._official_docs_cache = (time.monotonic() + OFFICIAL_DOCS_CACHE_TTL_SECONDS, documents)
        return documents

    @staticmethod
    def _parse_official_docs_bundle(bundle: str) -> List[OfficialDocument]:
        """解析官方站点提供的 `llms-full.txt` 文档包。"""

        documents: List[OfficialDocument] = []
        pattern = re.compile(
            r"(?:\A|\n)---[^\S\n]*\nurl:[^\S\n]*(?P<quote>['\"]?)"
            r"(?P<url>(?:https?://|/)[^\s'\"]+)(?P=quote)[^\S\n]*\n---[^\S\n]*\n"
        )
        matches = list(pattern.finditer(bundle))
        for index, match in enumerate(matches):
            # 文档包同时支持相对路径和带引号的完整 URL，统一用路径作为检索与读取 ID。
            path = urlsplit(match.group("url")).path
            content_end = matches[index + 1].start() if index + 1 < len(matches) else len(bundle)
            content = bundle[match.end() : content_end].strip()
            title_match = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
            title = title_match.group(1).strip() if title_match else path.rsplit("/", 1)[-1]
            documents.append(OfficialDocument(path=path, title=title, content=content))
        return documents

    @staticmethod
    def _find_snippet_offset(document: OfficialDocument, terms: List[str]) -> int:
        """定位首个命中词附近的片段起点。"""

        positions = [position for term in terms if (position := document.content_lower.find(term)) >= 0]
        return max(0, min(positions) - OFFICIAL_DOCS_SNIPPET_LEAD) if positions else 0

    @staticmethod
    def build_official_doc_url(path: str) -> str:
        """把 LLM 文档路径转换为面向用户的文档站页面 URL。"""

        return f"{OFFICIAL_DOCS_BASE_URL}{path.removesuffix('.md')}"
