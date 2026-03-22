"""
稀疏检索组件（FTS5 + BM25）

支持：
- 懒加载索引连接
- jieba / char n-gram 分词
- 可卸载并收缩 SQLite 内存缓存
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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
    mode: str = "auto"  # auto | fallback_only | hybrid
    tokenizer_mode: str = "jieba"  # jieba | mixed | char_2gram
    jieba_user_dict: str = ""
    char_ngram_n: int = 2
    candidate_k: int = 80
    max_doc_len: int = 2000
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
        self.relation_candidate_k = max(1, int(self.relation_candidate_k))
        self.relation_max_doc_len = max(0, int(self.relation_max_doc_len))
        if self.backend != "fts5":
            raise ValueError(f"sparse.backend 暂仅支持 fts5: {self.backend}")
        if self.mode not in {"auto", "fallback_only", "hybrid"}:
            raise ValueError(f"sparse.mode 非法: {self.mode}")
        if self.tokenizer_mode not in {"jieba", "mixed", "char_2gram"}:
            raise ValueError(f"sparse.tokenizer_mode 非法: {self.tokenizer_mode}")


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

    @property
    def loaded(self) -> bool:
        return self._loaded and self._conn is not None

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

        if not self.metadata_store.ensure_fts_schema(conn=conn):
            conn.close()
            return False
        self.metadata_store.ensure_fts_backfilled(conn=conn)
        # 关系稀疏检索按独立开关加载，避免不必要的初始化开销。
        if self.config.enable_relation_sparse_fallback:
            self.metadata_store.ensure_relations_fts_schema(conn=conn)
            self.metadata_store.ensure_relations_fts_backfilled(conn=conn)
        if self.config.enable_ngram_fallback_index:
            self.metadata_store.ensure_paragraph_ngram_schema(conn=conn)
            self.metadata_store.ensure_paragraph_ngram_backfilled(
                n=self.config.char_ngram_n,
                conn=conn,
            )

        self._conn = conn
        self._loaded = True
        self._prepare_tokenizer()
        logger.info(
            "SparseBM25Index loaded: "
            f"backend=fts5, tokenizer={self.config.tokenizer_mode}, mode={self.config.mode}"
        )
        return True

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

    def _tokenize(self, text: str) -> List[str]:
        text = str(text or "").strip()
        if not text:
            return []

        mode = self.config.tokenizer_mode
        if mode == "jieba":
            tokens = self._tokenize_jieba(text)
            if tokens:
                return list(dict.fromkeys(tokens))
            return self._tokenize_char_ngram(text, self.config.char_ngram_n)

        if mode == "mixed":
            toks = self._tokenize_jieba(text)
            toks.extend(self._tokenize_char_ngram(text, self.config.char_ngram_n))
            return list(dict.fromkeys([t for t in toks if t]))

        return list(dict.fromkeys(self._tokenize_char_ngram(text, self.config.char_ngram_n)))

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
                # 允许运行时切换开关后按需补齐 schema/回填。
                self.metadata_store.ensure_paragraph_ngram_schema(conn=self._conn)
                self.metadata_store.ensure_paragraph_ngram_backfilled(
                    n=self.config.char_ngram_n,
                    conn=self._conn,
                )
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
        # 关系稀疏检索可独立开关，运行时开启后也能按需补齐 schema/回填。
        self.metadata_store.ensure_relations_fts_schema(conn=self._conn)
        self.metadata_store.ensure_relations_fts_backfilled(conn=self._conn)

        tokens = self._tokenize(query)
        match_query = self._build_match_query(tokens)
        if not match_query:
            return []

        limit = max(1, int(k))
        rows = self.metadata_store.fts_search_bm25(
            match_query=match_query,
            limit=limit,
            max_doc_len=self.config.max_doc_len,
            conn=self._conn,
        )
        if not rows:
            rows = self._fallback_substring_search(tokens=tokens, limit=limit)

        results: List[Dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            bm25_score = float(row.get("bm25_score", 0.0))
            results.append(
                {
                    "hash": row["hash"],
                    "content": row["content"],
                    "rank": rank,
                    "bm25_score": bm25_score,
                    "score": -bm25_score,  # bm25 越小越相关，这里取反作为相对分数
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
        match_query = self._build_match_query(tokens)
        if not match_query:
            return []

        rows = self.metadata_store.fts_search_relations_bm25(
            match_query=match_query,
            limit=max(1, int(k)),
            max_doc_len=self.config.relation_max_doc_len,
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
        return self.metadata_store.fts_upsert_paragraph(paragraph_hash, conn=self._conn)

    def delete_paragraph(self, paragraph_hash: str) -> bool:
        if not self.loaded:
            return False
        return self.metadata_store.fts_delete_paragraph(paragraph_hash, conn=self._conn)

    def unload(self) -> None:
        """卸载 BM25 连接并尽量释放内存。"""
        if self._conn is not None:
            try:
                if self.config.shrink_memory_on_unload:
                    self.metadata_store.shrink_memory(conn=self._conn)
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._loaded = False
        logger.info("SparseBM25Index unloaded")

    def stats(self) -> Dict[str, Any]:
        doc_count = 0
        if self.loaded:
            doc_count = self.metadata_store.fts_doc_count(conn=self._conn)
        return {
            "enabled": self.config.enabled,
            "backend": self.config.backend,
            "mode": self.config.mode,
            "tokenizer_mode": self.config.tokenizer_mode,
            "enable_ngram_fallback_index": self.config.enable_ngram_fallback_index,
            "enable_like_fallback": self.config.enable_like_fallback,
            "enable_relation_sparse_fallback": self.config.enable_relation_sparse_fallback,
            "loaded": self.loaded,
            "has_jieba": HAS_JIEBA,
            "doc_count": doc_count,
        }
