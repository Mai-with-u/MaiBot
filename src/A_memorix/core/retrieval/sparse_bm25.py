"""
稀疏检索组件（FTS5 + BM25）

支持：
- 懒加载索引连接
- jieba / char n-gram 分词
- 可卸载并收缩 SQLite 内存缓存
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import re
import sqlite3
import time

from src.common.logger import get_logger
from ..storage import MetadataStore

logger = get_logger("A_Memorix.SparseBM25")

try:
    import jieba  # type: ignore

    HAS_JIEBA = True
except Exception:
    HAS_JIEBA = False
    jieba = None


@dataclass
class SparseBM25Config:
    """BM25 稀疏检索配置。"""

    enabled: bool = True
    backend: str = "fts5"
    lazy_load: bool = True
    mode: str = "auto"  # 检索模式：auto、fallback_only 或 hybrid
    tokenizer_mode: str = "jieba"  # 分词方式：jieba、mixed 或 char_2gram
    jieba_user_dict: str = ""
    char_ngram_n: int = 2
    candidate_k: int = 80
    max_doc_len: int = 2000
    enable_tokenized_shadow_index: bool = True
    enable_ngram_fallback_index: bool = True
    enable_like_fallback: bool = False
    enable_relation_sparse_fallback: bool = True
    relation_candidate_k: int = 60
    relation_max_doc_len: int = 512
    unload_on_disable: bool = True
    shrink_memory_on_unload: bool = True

    def __post_init__(self) -> None:
        self.backend = str(self.backend or "fts5").strip().lower()
        self.mode = str(self.mode or "auto").strip().lower()
        self.tokenizer_mode = str(self.tokenizer_mode or "jieba").strip().lower()
        self.char_ngram_n = max(1, int(self.char_ngram_n))
        self.candidate_k = max(1, int(self.candidate_k))
        self.max_doc_len = max(0, int(self.max_doc_len))
        self.enable_tokenized_shadow_index = bool(self.enable_tokenized_shadow_index)
        self.relation_candidate_k = max(1, int(self.relation_candidate_k))
        self.relation_max_doc_len = max(0, int(self.relation_max_doc_len))
        if self.backend not in {"fts5", "tantivy", "lucene"}:
            raise ValueError(f"sparse.backend 非法: {self.backend}")
        if self.mode not in {"auto", "fallback_only", "hybrid"}:
            raise ValueError(f"sparse.mode 非法: {self.mode}")
        if self.tokenizer_mode not in {"jieba", "mixed", "char_2gram"}:
            raise ValueError(f"sparse.tokenizer_mode 非法: {self.tokenizer_mode}")


class SparseSearchBackend(ABC):
    """稀疏倒排检索 backend 接口，用于隔离 FTS5 与实验 backend。"""

    name: str

    @abstractmethod
    def ensure_loaded(self, conn: sqlite3.Connection) -> bool:
        """初始化 backend 需要的 schema/index。"""

    @abstractmethod
    def search_paragraphs(
        self,
        *,
        match_query: str,
        limit: int,
        max_doc_len: int,
        conn: sqlite3.Connection,
    ) -> List[Dict[str, Any]]:
        """检索段落。"""

    @abstractmethod
    def search_relations(
        self,
        *,
        match_query: str,
        limit: int,
        max_doc_len: int,
        include_inactive: bool,
        conn: sqlite3.Connection,
    ) -> List[Dict[str, Any]]:
        """检索关系。"""

    def stats(self, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        del conn
        return {"backend": self.name}


class SQLiteFTS5SparseBackend(SparseSearchBackend):
    """基于 MetadataStore/SQLite FTS5 的默认稀疏检索 backend。"""

    name = "fts5"

    def __init__(self, metadata_store: MetadataStore, config: SparseBM25Config):
        self.metadata_store = metadata_store
        self.config = config

    def ensure_loaded(self, conn: sqlite3.Connection) -> bool:
        if not self.metadata_store.ensure_fts_schema(conn=conn):
            return False
        self.metadata_store.ensure_fts_backfilled(conn=conn)
        if self.config.enable_tokenized_shadow_index:
            self.metadata_store.ensure_paragraph_tokenized_fts_schema(conn=conn)
            self.metadata_store.ensure_paragraph_tokenized_fts_backfilled(conn=conn)
        # 关系稀疏检索按独立开关加载，避免不必要的初始化开销。
        if self.config.enable_relation_sparse_fallback:
            self.metadata_store.ensure_relations_fts_schema(conn=conn)
            self.metadata_store.ensure_relations_fts_backfilled(conn=conn)
        if self.config.enable_ngram_fallback_index:
            self.metadata_store.ensure_paragraph_ngram_schema(conn=conn)
            if not self.metadata_store.is_paragraph_ngram_ready(
                n=self.config.char_ngram_n,
                conn=conn,
            ):
                logger.warning("paragraph ngram 索引未就绪，检索路径将跳过 ngram fallback")
        return True

    def search_paragraphs(
        self,
        *,
        match_query: str,
        limit: int,
        max_doc_len: int,
        conn: sqlite3.Connection,
    ) -> List[Dict[str, Any]]:
        return self.metadata_store.fts_search_bm25(
            match_query=match_query,
            limit=limit,
            max_doc_len=max_doc_len,
            conn=conn,
        )

    def search_relations(
        self,
        *,
        match_query: str,
        limit: int,
        max_doc_len: int,
        include_inactive: bool,
        conn: sqlite3.Connection,
    ) -> List[Dict[str, Any]]:
        return self.metadata_store.fts_search_relations_bm25(
            match_query=match_query,
            limit=limit,
            max_doc_len=max_doc_len,
            include_inactive=include_inactive,
            conn=conn,
        )

    def stats(self, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        doc_count = 0
        if conn is not None:
            doc_count = self.metadata_store.fts_doc_count(conn=conn)
        return {"backend": self.name, "doc_count": doc_count}


class ExperimentalExternalInvertedIndexBackend(SparseSearchBackend):
    """Tantivy/Lucene 实验 backend 占位接口。"""

    def __init__(self, backend_name: str):
        self.name = str(backend_name or "").strip().lower()

    def ensure_loaded(self, conn: sqlite3.Connection) -> bool:
        del conn
        raise NotImplementedError(
            f"sparse.backend={self.name} 仍是实验接口，当前运行时请使用 fts5"
        )

    def search_paragraphs(
        self,
        *,
        match_query: str,
        limit: int,
        max_doc_len: int,
        conn: sqlite3.Connection,
    ) -> List[Dict[str, Any]]:
        del match_query, limit, max_doc_len, conn
        raise NotImplementedError(
            f"sparse.backend={self.name} 尚未接入段落倒排索引实现"
        )

    def search_relations(
        self,
        *,
        match_query: str,
        limit: int,
        max_doc_len: int,
        include_inactive: bool,
        conn: sqlite3.Connection,
    ) -> List[Dict[str, Any]]:
        del match_query, limit, max_doc_len, include_inactive, conn
        raise NotImplementedError(
            f"sparse.backend={self.name} 尚未接入关系倒排索引实现"
        )


class SparseBM25Index:
    """
    基于 SQLite FTS5 的 BM25 检索适配层。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        config: Optional[SparseBM25Config] = None,
    ):
        self.metadata_store = metadata_store
        self.config = config or SparseBM25Config()
        self._conn: Optional[sqlite3.Connection] = None
        self._loaded: bool = False
        self._jieba_dict_loaded: bool = False
        self._last_load_error = ""
        self._backend = self._create_backend()

    @property
    def loaded(self) -> bool:
        return self._loaded and self._conn is not None

    def _create_backend(self) -> SparseSearchBackend:
        if self.config.backend == "fts5":
            return SQLiteFTS5SparseBackend(self.metadata_store, self.config)
        return ExperimentalExternalInvertedIndexBackend(self.config.backend)

    def ensure_loaded(self) -> bool:
        """按需加载 FTS 连接与索引。"""
        if not self.config.enabled:
            return False
        if self.loaded:
            return True

        db_path = self.metadata_store.get_db_path()
        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")

        try:
            backend_ready = self._backend.ensure_loaded(conn)
        except NotImplementedError as e:
            logger.warning(f"稀疏检索 backend 未启用: {e}")
            self._last_load_error = str(e)
            conn.close()
            return False
        except Exception as e:
            logger.warning(f"稀疏检索 backend 加载失败: {e}")
            self._last_load_error = str(e)
            conn.close()
            return False
        if not backend_ready:
            self._last_load_error = "backend_not_ready"
            conn.close()
            return False

        self._conn = conn
        self._loaded = True
        self._last_load_error = ""
        self._prepare_tokenizer()
        logger.debug(
            "SparseBM25Index loaded: "
            f"backend=fts5, tokenizer={self.config.tokenizer_mode}, mode={self.config.mode}"
        )
        return True

    def warmup(
        self,
        sample_queries: Optional[Sequence[str]] = None,
        *,
        relation_query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        预热稀疏索引、分词器和首轮 FTS 查询路径。

        返回结构化指标，方便 runtime 启动日志和性能测试对齐。
        """
        started = time.perf_counter()
        summary: Dict[str, Any] = {
            "ok": False,
            "enabled": self.config.enabled,
            "backend": self.config.backend,
            "loaded": False,
            "doc_count": 0,
            "tokenized_query_count": 0,
            "paragraph_probe_count": 0,
            "relation_probe_count": 0,
            "duration_ms": 0.0,
            "error": "",
        }
        if not self.config.enabled:
            summary["duration_ms"] = (time.perf_counter() - started) * 1000.0
            return summary

        try:
            if not self.ensure_loaded():
                summary["error"] = self._last_load_error or "ensure_loaded_failed"
                summary["duration_ms"] = (time.perf_counter() - started) * 1000.0
                return summary

            probes = [
                str(item or "").strip()
                for item in (
                    sample_queries
                    if sample_queries is not None
                    else ("记忆 检索", "关系 证据")
                )
                if str(item or "").strip()
            ]
            for probe in probes:
                self._tokenize(probe)
            paragraph_query = probes[0] if probes else "记忆"
            paragraph_hits = self.search(paragraph_query, k=1)
            relation_hits: List[Dict[str, Any]] = []
            if self.config.enable_relation_sparse_fallback:
                rel_probe = str(relation_query or paragraph_query).strip() or paragraph_query
                relation_hits = self.search_relations(rel_probe, k=1)

            summary.update(
                {
                    "ok": True,
                    "loaded": self.loaded,
                    "doc_count": self.metadata_store.fts_doc_count(conn=self._conn),
                    "tokenized_query_count": len(probes),
                    "paragraph_probe_count": len(paragraph_hits),
                    "relation_probe_count": len(relation_hits),
                }
            )
        except Exception as e:
            summary["error"] = str(e)
            logger.warning(f"SparseBM25Index warmup 失败: {e}")
        finally:
            summary["duration_ms"] = (time.perf_counter() - started) * 1000.0

        return summary

    def _prepare_tokenizer(self) -> None:
        if self._jieba_dict_loaded:
            return
        if self.config.tokenizer_mode not in {"jieba", "mixed"}:
            return
        if not HAS_JIEBA:
            logger.warning("jieba 不可用，tokenizer 将退化为 char n-gram")
            return
        user_dict = str(self.config.jieba_user_dict or "").strip()
        if user_dict:
            try:
                jieba.load_userdict(user_dict)  # type: ignore[union-attr]
                logger.info(f"已加载 jieba 用户词典: {user_dict}")
            except Exception as e:
                logger.warning(f"加载 jieba 用户词典失败: {e}")
        self._jieba_dict_loaded = True

    def _tokenize_jieba(self, text: str) -> List[str]:
        if not HAS_JIEBA:
            return []
        try:
            tokens = list(jieba.cut_for_search(text))  # type: ignore[union-attr]
            return [t.strip().lower() for t in tokens if t and t.strip()]
        except Exception:
            return []

    def _tokenize_char_ngram(self, text: str, n: int) -> List[str]:
        compact = re.sub(r"\s+", "", text.lower())
        if not compact:
            return []
        if len(compact) < n:
            return [compact]
        return [compact[i : i + n] for i in range(0, len(compact) - n + 1)]

    @staticmethod
    def _tokenize_phrases(text: str) -> List[str]:
        return [token.lower() for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", str(text or ""))]

    def _tokenize(self, text: str) -> List[str]:
        text = str(text or "").strip()
        if not text:
            return []

        mode = self.config.tokenizer_mode
        if mode == "jieba":
            tokens = self._tokenize_jieba(text)
            if tokens:
                tokens.extend(self._tokenize_phrases(text))
                return list(dict.fromkeys(tokens))
            fallback_tokens = self._tokenize_char_ngram(text, self.config.char_ngram_n)
            fallback_tokens.extend(self._tokenize_phrases(text))
            return list(dict.fromkeys(fallback_tokens))

        if mode == "mixed":
            toks = self._tokenize_jieba(text)
            toks.extend(self._tokenize_char_ngram(text, self.config.char_ngram_n))
            toks.extend(self._tokenize_phrases(text))
            return list(dict.fromkeys([t for t in toks if t]))

        return list(dict.fromkeys(self._tokenize_char_ngram(text, self.config.char_ngram_n)))

    @staticmethod
    def _is_low_signal_query_token(token: str) -> bool:
        """识别长查询中容易造成 OR 误召回的低信息中文单字 token。"""
        text = str(token or "").strip()
        return len(text) == 1 and bool(re.fullmatch(r"[\u4e00-\u9fff]", text))

    def _match_tokens(self, tokens: List[str]) -> List[str]:
        """
        生成实际参与 MATCH 的 token。

        当查询包含多字词/短语时，单字中文 token 往往是停用词或切词残片。
        若继续用 OR 查询，会因为“的”等 token 召回无关段落。
        """
        normalized = [str(token or "").strip() for token in tokens if str(token or "").strip()]
        if not normalized:
            return []
        informative = [
            token
            for token in normalized
            if not self._is_low_signal_query_token(token)
        ]
        return list(dict.fromkeys(informative or normalized))

    def _build_match_query(self, tokens: List[str]) -> str:
        safe_tokens: List[str] = []
        for token in tokens:
            t = token.replace('"', '""').strip()
            if not t:
                continue
            safe_tokens.append(f'"{t}"')
        if not safe_tokens:
            return ""
        # 采用 OR 提升召回，再交由 RRF 和阈值做稳健排序。
        return " OR ".join(safe_tokens[:64])

    def _fallback_substring_search(
        self,
        tokens: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """
        当 FTS5 因分词不一致召回为空时，退化为子串匹配召回。

        说明：
        - FTS 索引当前采用 unicode61 tokenizer。
        - 若查询 token 来源为 char n-gram 或中文词元，可能与索引 token 不一致。
        - 这里使用 SQL LIKE 做兜底，按命中 token 覆盖度打分。
        """
        if not tokens:
            return []

        # 去重并裁剪 token 数量，避免生成超长 SQL。
        uniq_tokens = [t for t in dict.fromkeys(tokens) if t]
        uniq_tokens = uniq_tokens[:32]
        if not uniq_tokens:
            return []

        if self.config.enable_ngram_fallback_index:
            try:
                self.metadata_store.ensure_paragraph_ngram_schema(conn=self._conn)
                if not self.metadata_store.is_paragraph_ngram_ready(
                    n=self.config.char_ngram_n,
                    conn=self._conn,
                ):
                    logger.debug("paragraph ngram 索引未就绪，跳过 ngram fallback")
                else:
                    rows = self.metadata_store.ngram_search_paragraphs(
                        tokens=uniq_tokens,
                        limit=limit,
                        max_doc_len=self.config.max_doc_len,
                        conn=self._conn,
                    )
                    if rows:
                        return rows
            except Exception as e:
                logger.warning(f"ngram 倒排回退失败，将按配置决定是否使用 LIKE 回退: {e}")

        if not self.config.enable_like_fallback:
            return []

        conditions = " OR ".join(["p.content LIKE ?"] * len(uniq_tokens))
        params: List[Any] = [f"%{tok}%" for tok in uniq_tokens]
        scan_limit = max(int(limit) * 8, 200)
        params.append(scan_limit)

        sql = f"""
            SELECT p.hash, p.content
            FROM paragraphs p
            WHERE (p.is_deleted IS NULL OR p.is_deleted = 0)
              AND ({conditions})
            LIMIT ?
        """
        rows = self.metadata_store.query(sql, tuple(params))
        if not rows:
            return []

        scored: List[Dict[str, Any]] = []
        token_count = max(1, len(uniq_tokens))
        for row in rows:
            content = str(row.get("content") or "")
            content_low = content.lower()
            matched = [tok for tok in uniq_tokens if tok in content_low]
            if not matched:
                continue
            coverage = len(matched) / token_count
            length_bonus = sum(len(tok) for tok in matched) / max(1, len(content_low))
            # 兜底路径使用相对分，保持与上层接口兼容。
            fallback_score = coverage * 0.8 + length_bonus * 0.2
            scored.append(
                {
                    "hash": row["hash"],
                    "content": content[: self.config.max_doc_len] if self.config.max_doc_len > 0 else content,
                    "bm25_score": -float(fallback_score),
                    "fallback_score": float(fallback_score),
                }
            )

        scored.sort(key=lambda x: x["fallback_score"], reverse=True)
        return scored[:limit]

    def search(self, query: str, k: int = 20) -> List[Dict[str, Any]]:
        """执行 BM25 检索。"""
        if not self.config.enabled:
            return []
        if self.config.lazy_load and not self.loaded:
            if not self.ensure_loaded():
                return []
        if not self.loaded:
            return []

        tokens = self._tokenize(query)
        match_tokens = self._match_tokens(tokens)
        match_query = self._build_match_query(match_tokens)
        if not match_query:
            return []

        limit = max(1, int(k))
        rows: List[Dict[str, Any]] = []
        if self.config.enable_tokenized_shadow_index:
            rows = self.metadata_store.fts_search_tokenized_paragraphs_bm25(
                match_query=match_query,
                limit=limit,
                max_doc_len=self.config.max_doc_len,
                conn=self._conn,
            )
        if not rows:
            rows = self._backend.search_paragraphs(
                match_query=match_query,
                limit=limit,
                max_doc_len=self.config.max_doc_len,
                conn=self._conn,
            )
        if not rows:
            rows = self._fallback_substring_search(tokens=match_tokens, limit=limit)

        results: List[Dict[str, Any]] = []
        token_count = max(1, len(match_tokens))
        for rank, row in enumerate(rows, start=1):
            bm25_score = float(row.get("bm25_score", 0.0))
            content = str(row.get("content", "") or "")
            content_low = content.lower()
            matched_tokens = [token for token in match_tokens if token in content_low]
            matched_token_count = len(dict.fromkeys(matched_tokens))
            results.append(
                {
                    "hash": row["hash"],
                    "content": content,
                    "rank": rank,
                    "bm25_score": bm25_score,
                    "score": -bm25_score,  # bm25 越小越相关，这里取反作为相对分数
                    "matched_token_count": matched_token_count,
                    "matched_token_ratio": matched_token_count / float(token_count),
                }
            )
        return results

    def search_relations(self, query: str, k: int = 20) -> List[Dict[str, Any]]:
        """执行关系稀疏检索（FTS5 + BM25）。"""
        if not self.config.enabled or not self.config.enable_relation_sparse_fallback:
            return []
        if self.config.lazy_load and not self.loaded:
            if not self.ensure_loaded():
                return []
        if not self.loaded:
            return []

        tokens = self._tokenize(query)
        match_query = self._build_match_query(self._match_tokens(tokens))
        if not match_query:
            return []

        rows = self._backend.search_relations(
            match_query=match_query,
            limit=max(1, int(k)),
            max_doc_len=self.config.relation_max_doc_len,
            include_inactive=False,
            conn=self._conn,
        )
        out: List[Dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            bm25_score = float(row.get("bm25_score", 0.0))
            out.append(
                {
                    "hash": row["hash"],
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "object": row["object"],
                    "content": row["content"],
                    "rank": rank,
                    "bm25_score": bm25_score,
                    "score": -bm25_score,
                }
            )
        return out

    def upsert_paragraph(self, paragraph_hash: str) -> bool:
        if not self.loaded:
            return False
        ok = self.metadata_store.fts_upsert_paragraph(paragraph_hash, conn=self._conn)
        if self.config.enable_tokenized_shadow_index:
            shadow_ok = self.metadata_store.fts_upsert_tokenized_paragraph(paragraph_hash, conn=self._conn)
            ok = bool(ok and shadow_ok)
        return ok

    def delete_paragraph(self, paragraph_hash: str) -> bool:
        if not self.loaded:
            return False
        ok = self.metadata_store.fts_delete_paragraph(paragraph_hash, conn=self._conn)
        if self.config.enable_tokenized_shadow_index:
            shadow_ok = self.metadata_store.fts_delete_tokenized_paragraph(paragraph_hash, conn=self._conn)
            ok = bool(ok and shadow_ok)
        return ok

    def unload(self) -> None:
        """卸载 BM25 连接并尽量释放内存。"""
        if self._conn is not None:
            try:
                if self.config.shrink_memory_on_unload:
                    self.metadata_store.shrink_memory(conn=self._conn)
            except sqlite3.Error as exc:
                logger.warning(f"SparseBM25Index 释放 SQLite 缓存失败: {exc}")
            try:
                self._conn.close()
            except sqlite3.Error as exc:
                logger.warning(f"SparseBM25Index 关闭连接失败: {exc}")
        self._conn = None
        self._loaded = False
        logger.info("SparseBM25Index unloaded")

    def stats(self) -> Dict[str, Any]:
        backend_stats = self._backend.stats(self._conn if self.loaded else None)
        doc_count = int(backend_stats.get("doc_count", 0) or 0)
        return {
            "enabled": self.config.enabled,
            "backend": self.config.backend,
            "mode": self.config.mode,
            "tokenizer_mode": self.config.tokenizer_mode,
            "enable_tokenized_shadow_index": self.config.enable_tokenized_shadow_index,
            "enable_ngram_fallback_index": self.config.enable_ngram_fallback_index,
            "enable_like_fallback": self.config.enable_like_fallback,
            "enable_relation_sparse_fallback": self.config.enable_relation_sparse_fallback,
            "loaded": self.loaded,
            "has_jieba": HAS_JIEBA,
            "doc_count": doc_count,
        }
