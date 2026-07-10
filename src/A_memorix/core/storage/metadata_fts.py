from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import re
import sqlite3
import time

from src.common.logger import get_logger

from .tokenizer_runtime import HAS_JIEBA, JIEBA_MODULE

logger = get_logger("A_Memorix.MetadataFTS")


class MetadataFTSMixin:
    """维护 FTS、BM25 与字符 n-gram 索引。"""

    def ensure_fts_schema(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """
        确保 FTS5 schema 存在（幂等）。

        采用 external-content 方式，不在 FTS 表重复存储正文。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts
                USING fts5(
                    content,
                    content='paragraphs',
                    content_rowid='rowid',
                    tokenize='unicode61'
                )
            """)

            # 插入触发器（insert trigger）
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS paragraphs_ai
                AFTER INSERT ON paragraphs
                BEGIN
                    INSERT INTO paragraphs_fts(rowid, content)
                    VALUES (new.rowid, new.content);
                END
            """)

            # 删除触发器（delete trigger）
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS paragraphs_ad
                AFTER DELETE ON paragraphs
                BEGIN
                    INSERT INTO paragraphs_fts(paragraphs_fts, rowid, content)
                    VALUES ('delete', old.rowid, old.content);
                END
            """)

            # 更新触发器（update trigger）
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS paragraphs_au
                AFTER UPDATE OF content ON paragraphs
                BEGIN
                    INSERT INTO paragraphs_fts(paragraphs_fts, rowid, content)
                    VALUES ('delete', old.rowid, old.content);
                    INSERT INTO paragraphs_fts(rowid, content)
                    VALUES (new.rowid, new.content);
                END
            """)
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 schema 创建失败（可能不支持 FTS5）: {e}")
            c.rollback()
            return False

    def ensure_fts_backfilled(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """
        确保 FTS 索引已回填。

        当历史数据存在但 FTS 表为空/不一致时执行 rebuild。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("SELECT COUNT(1) AS n FROM paragraphs")
            para_count = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(1) AS n FROM paragraphs_fts")
            fts_count = int(cur.fetchone()[0])

            if para_count > 0 and fts_count != para_count:
                cur.execute("INSERT INTO paragraphs_fts(paragraphs_fts) VALUES ('rebuild')")
                c.commit()
                logger.info(f"FTS 回填完成: paragraphs={para_count}, fts={para_count}")
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS 回填失败: {e}")
            c.rollback()
            return False

    def ensure_relations_fts_schema(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """
        确保关系 FTS5 schema 存在（幂等）。

        注意：relations 表没有 content 列，因此使用独立 FTS 表并通过触发器同步。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS relations_fts
                USING fts5(
                    relation_hash UNINDEXED,
                    content,
                    tokenize='unicode61'
                )
            """)

            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS relations_ai
                AFTER INSERT ON relations
                BEGIN
                    INSERT INTO relations_fts(relation_hash, content)
                    VALUES (
                        new.hash,
                        COALESCE(new.subject, '') || ' ' || COALESCE(new.predicate, '') || ' ' || COALESCE(new.object, '')
                    );
                END
            """)

            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS relations_ad
                AFTER DELETE ON relations
                BEGIN
                    DELETE FROM relations_fts WHERE relation_hash = old.hash;
                END
            """)

            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS relations_au
                AFTER UPDATE OF subject, predicate, object ON relations
                BEGIN
                    DELETE FROM relations_fts WHERE relation_hash = new.hash;
                    INSERT INTO relations_fts(relation_hash, content)
                    VALUES (
                        new.hash,
                        COALESCE(new.subject, '') || ' ' || COALESCE(new.predicate, '') || ' ' || COALESCE(new.object, '')
                    );
                END
            """)
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"relations FTS5 schema 创建失败（可能不支持 FTS5）: {e}")
            c.rollback()
            return False

    def ensure_relations_fts_backfilled(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """确保关系 FTS 索引已回填。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("SELECT COUNT(1) AS n FROM relations")
            rel_count = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(1) AS n FROM relations_fts")
            fts_count = int(cur.fetchone()[0])

            if rel_count != fts_count:
                cur.execute("DELETE FROM relations_fts")
                cur.execute("""
                    INSERT INTO relations_fts(relation_hash, content)
                    SELECT
                        r.hash,
                        COALESCE(r.subject, '') || ' ' || COALESCE(r.predicate, '') || ' ' || COALESCE(r.object, '')
                    FROM relations r
                """)
                c.commit()
                logger.info(f"relations FTS 回填完成: relations={rel_count}, fts={rel_count}")
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"relations FTS 回填失败: {e}")
            c.rollback()
            return False

    def ensure_paragraph_tokenized_fts_schema(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """确保预分词段落 FTS5 shadow index 存在。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_tokenized_fts
                USING fts5(
                    paragraph_hash UNINDEXED,
                    tokenized,
                    tokenize='unicode61'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS paragraph_tokenized_fts_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"paragraph tokenized FTS5 schema 创建失败: {e}")
            c.rollback()
            return False

    @staticmethod
    def _paragraph_phrase_tokens(text: str) -> List[str]:
        return [token.lower() for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", str(text or ""))]

    def _tokenize_paragraph_for_fts(self, text: str) -> str:
        source = str(text or "")
        if HAS_JIEBA and JIEBA_MODULE is not None:
            try:
                tokens = [token.strip().lower() for token in JIEBA_MODULE.cut_for_search(source) if token.strip()]
            except Exception:
                tokens = list(source.lower())
        else:
            tokens = list(source.lower())
        tokens.extend(self._paragraph_phrase_tokens(source))
        return " ".join(dict.fromkeys(token for token in tokens if token))

    def _refresh_paragraph_tokenized_fts_meta(self, conn: sqlite3.Connection) -> None:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paragraph_tokenized_fts_meta'")
        if cur.fetchone() is None:
            return
        cur.execute("SELECT COUNT(1) FROM paragraphs WHERE is_deleted IS NULL OR is_deleted = 0")
        para_count = int(cur.fetchone()[0])
        cur.execute("""
            INSERT INTO paragraph_tokenized_fts_meta(key, value) VALUES('paragraph_count', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (str(para_count),))
        cur.execute("""
            INSERT INTO paragraph_tokenized_fts_meta(key, value) VALUES('updated_at', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (str(datetime.now().timestamp()),))

    def ensure_paragraph_tokenized_fts_backfilled(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """确保预分词段落 FTS5 shadow index 已回填。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        started = time.perf_counter()
        try:
            if not self.ensure_paragraph_tokenized_fts_schema(conn=c):
                return False
            cur.execute("SELECT COUNT(1) FROM paragraphs WHERE is_deleted IS NULL OR is_deleted = 0")
            para_count = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM paragraph_tokenized_fts_meta WHERE key='paragraph_count'")
            meta_row = cur.fetchone()
            indexed_docs = int(meta_row[0]) if meta_row and meta_row[0] is not None else -1
            if indexed_docs == para_count:
                return True

            cur.execute("DELETE FROM paragraphs_tokenized_fts")
            cur.execute("""
                SELECT hash, content
                FROM paragraphs
                WHERE is_deleted IS NULL OR is_deleted = 0
            """)
            batch: List[Tuple[str, str]] = []
            batch_size = 1000
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    batch.append((str(row["hash"]), self._tokenize_paragraph_for_fts(str(row["content"] or ""))))
                cur.executemany(
                    "INSERT INTO paragraphs_tokenized_fts(paragraph_hash, tokenized) VALUES (?, ?)",
                    batch,
                )
                batch.clear()
            if batch:
                cur.executemany(
                    "INSERT INTO paragraphs_tokenized_fts(paragraph_hash, tokenized) VALUES (?, ?)",
                    batch,
                )
            self._refresh_paragraph_tokenized_fts_meta(c)
            c.commit()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "paragraph tokenized FTS 回填完成: "
                f"paragraphs={para_count}, duration_ms={elapsed_ms:.2f}"
            )
            return True
        except Exception as e:
            logger.warning(f"paragraph tokenized FTS 回填失败: {e}")
            c.rollback()
            return False

    def fts_upsert_tokenized_paragraph(
        self,
        paragraph_hash: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """增量维护预分词段落 FTS shadow index。"""
        c = self._resolve_conn(conn)
        owns_transaction = not c.in_transaction
        cur = c.cursor()
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paragraphs_tokenized_fts'")
            if cur.fetchone() is None:
                return False
            cur.execute(
                """
                SELECT hash, content
                FROM paragraphs
                WHERE hash = ?
                  AND (is_deleted IS NULL OR is_deleted = 0)
                """,
                (paragraph_hash,),
            )
            row = cur.fetchone()
            cur.execute("DELETE FROM paragraphs_tokenized_fts WHERE paragraph_hash = ?", (paragraph_hash,))
            if row:
                cur.execute(
                    "INSERT INTO paragraphs_tokenized_fts(paragraph_hash, tokenized) VALUES (?, ?)",
                    (paragraph_hash, self._tokenize_paragraph_for_fts(str(row["content"] or ""))),
                )
            self._refresh_paragraph_tokenized_fts_meta(c)
            if owns_transaction:
                c.commit()
            return True
        except sqlite3.OperationalError as e:
            if owns_transaction and c.in_transaction:
                c.rollback()
            logger.warning(f"paragraph tokenized FTS upsert 失败: {e}")
            return False

    def fts_delete_tokenized_paragraph(
        self,
        paragraph_hash: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """从预分词段落 FTS shadow index 删除段落。"""
        c = self._resolve_conn(conn)
        owns_transaction = not c.in_transaction
        cur = c.cursor()
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paragraphs_tokenized_fts'")
            if cur.fetchone() is None:
                return False
            cur.execute("DELETE FROM paragraphs_tokenized_fts WHERE paragraph_hash = ?", (paragraph_hash,))
            self._refresh_paragraph_tokenized_fts_meta(c)
            if owns_transaction:
                c.commit()
            return True
        except sqlite3.OperationalError as e:
            if owns_transaction and c.in_transaction:
                c.rollback()
            logger.warning(f"paragraph tokenized FTS delete 失败: {e}")
            return False

    def fts_search_tokenized_paragraphs_bm25(
        self,
        match_query: str,
        limit: int = 20,
        max_doc_len: int = 2000,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """使用预分词段落 FTS5 shadow index 执行 BM25 检索。"""
        if not match_query.strip():
            return []

        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute(
                """
                SELECT p.hash, p.content, bm25(paragraphs_tokenized_fts) AS bm25_score
                FROM paragraphs_tokenized_fts
                JOIN paragraphs p ON p.hash = paragraphs_tokenized_fts.paragraph_hash
                WHERE paragraphs_tokenized_fts MATCH ?
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                ORDER BY bm25_score ASC
                LIMIT ?
                """,
                (match_query, max(1, int(limit))),
            )
            rows = cur.fetchall()
            results: List[Dict[str, Any]] = []
            for row in rows:
                content = str(row["content"] or "")
                if max_doc_len > 0:
                    content = content[:max_doc_len]
                results.append(
                    {
                        "hash": row["hash"],
                        "content": content,
                        "bm25_score": float(row["bm25_score"]),
                    }
                )
            return results
        except sqlite3.OperationalError as e:
            logger.warning(f"paragraph tokenized FTS 查询失败: {e}")
            return []

    def ensure_paragraph_ngram_schema(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """确保段落 ngram 倒排表存在。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS paragraph_ngrams (
                    term TEXT NOT NULL,
                    paragraph_hash TEXT NOT NULL,
                    PRIMARY KEY (term, paragraph_hash),
                    FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_paragraph_ngrams_hash
                ON paragraph_ngrams(paragraph_hash)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS paragraph_ngram_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"paragraph ngram schema 创建失败: {e}")
            c.rollback()
            return False

    @staticmethod
    def _char_ngrams(text: str, n: int) -> List[str]:
        compact = "".join(str(text or "").lower().split())
        if not compact:
            return []
        if len(compact) < n:
            return [compact]
        return [compact[i : i + n] for i in range(0, len(compact) - n + 1)]

    def _get_paragraph_ngram_n_if_ready(
        self,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Optional[int]:
        """读取已初始化的 paragraph ngram 配置；未初始化时返回 None。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("SELECT value FROM paragraph_ngram_meta WHERE key='ngram_n'")
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            return max(1, int(row[0]))
        except (sqlite3.OperationalError, TypeError, ValueError):
            return None

    def is_paragraph_ngram_ready(
        self,
        n: int = 2,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """检查 paragraph ngram 索引是否已初始化且与 active 段落数量一致。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            current_n = self._get_paragraph_ngram_n_if_ready(conn=c)
            if current_n != max(1, int(n)):
                return False

            cur.execute("SELECT COUNT(1) FROM paragraphs WHERE is_deleted IS NULL OR is_deleted = 0")
            para_count = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM paragraph_ngram_meta WHERE key='paragraph_count'")
            row = cur.fetchone()
            if not row or row[0] is None:
                return False
            indexed_docs = int(row[0])
            return para_count == indexed_docs
        except (sqlite3.OperationalError, TypeError, ValueError):
            return False

    def _set_paragraph_ngram_meta_value(
        self,
        key: str,
        value: str,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        c = self._resolve_conn(conn)
        c.execute(
            """
            INSERT INTO paragraph_ngram_meta(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(key), str(value)),
        )

    def _adjust_paragraph_ngram_count(
        self,
        delta: int,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """在索引已初始化时维护 active paragraph 计数。"""
        if delta == 0:
            return
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("SELECT value FROM paragraph_ngram_meta WHERE key='paragraph_count'")
            row = cur.fetchone()
            if not row or row[0] is None:
                return
            current = max(0, int(row[0]))
        except (sqlite3.OperationalError, TypeError, ValueError):
            return
        self._set_paragraph_ngram_meta_value(
            "paragraph_count",
            str(max(0, current + int(delta))),
            conn=c,
        )

    def _upsert_paragraph_ngram_if_ready(
        self,
        paragraph_hash: str,
        content: str,
        *,
        count_delta: int = 0,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """若 ngram 索引已初始化，则只增量维护当前段落。"""
        c = self._resolve_conn(conn)
        n = self._get_paragraph_ngram_n_if_ready(conn=c)
        if n is None:
            return False

        cur = c.cursor()
        cur.execute("DELETE FROM paragraph_ngrams WHERE paragraph_hash = ?", (paragraph_hash,))
        terms = list(dict.fromkeys(self._char_ngrams(content, n)))
        if terms:
            cur.executemany(
                "INSERT OR IGNORE INTO paragraph_ngrams(term, paragraph_hash) VALUES (?, ?)",
                [(term, paragraph_hash) for term in terms],
            )
        self._adjust_paragraph_ngram_count(count_delta, conn=c)
        return True

    def _delete_paragraph_ngrams_if_ready(
        self,
        paragraph_hashes: Sequence[str],
        *,
        count_delta: int = 0,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """若 ngram 索引已初始化，则批量移除段落 ngram。"""
        hashes = [str(h) for h in paragraph_hashes if str(h or "").strip()]
        if not hashes:
            return False
        c = self._resolve_conn(conn)
        if self._get_paragraph_ngram_n_if_ready(conn=c) is None:
            return False

        cur = c.cursor()
        batch_size = 900
        for i in range(0, len(hashes), batch_size):
            batch = hashes[i:i + batch_size]
            placeholders = ",".join(["?"] * len(batch))
            cur.execute(
                f"DELETE FROM paragraph_ngrams WHERE paragraph_hash IN ({placeholders})",
                batch,
            )
        self._adjust_paragraph_ngram_count(count_delta, conn=c)
        return True

    def ensure_paragraph_ngram_backfilled(
        self,
        n: int = 2,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        确保段落 ngram 倒排索引已回填。

        仅在 n 变化或文档数量变化时重建，避免每次加载都全量重建。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        n = max(1, int(n))
        started = time.perf_counter()
        try:
            cur.execute("SELECT value FROM paragraph_ngram_meta WHERE key='ngram_n'")
            row = cur.fetchone()
            current_n = int(row[0]) if row and row[0] is not None else None

            cur.execute("SELECT COUNT(1) FROM paragraphs WHERE is_deleted IS NULL OR is_deleted = 0")
            para_count = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM paragraph_ngram_meta WHERE key='paragraph_count'")
            meta_row = cur.fetchone()
            if meta_row and meta_row[0] is not None:
                indexed_docs = int(meta_row[0])
            else:
                cur.execute("SELECT COUNT(DISTINCT paragraph_hash) FROM paragraph_ngrams")
                indexed_docs = int(cur.fetchone()[0])

            need_rebuild = (current_n != n) or (para_count != indexed_docs)
            if not need_rebuild:
                return True

            cur.execute("DELETE FROM paragraph_ngrams")
            cur.execute("""
                SELECT hash, content
                FROM paragraphs
                WHERE is_deleted IS NULL OR is_deleted = 0
            """)
            rows = cur.fetchall()

            batch: List[Tuple[str, str]] = []
            batch_size = 2000
            term_count = 0
            for row in rows:
                p_hash = str(row["hash"])
                terms = list(dict.fromkeys(self._char_ngrams(str(row["content"] or ""), n)))
                term_count += len(terms)
                for term in terms:
                    batch.append((term, p_hash))
                if len(batch) >= batch_size:
                    cur.executemany(
                        "INSERT OR IGNORE INTO paragraph_ngrams(term, paragraph_hash) VALUES (?, ?)",
                        batch,
                    )
                    batch.clear()
            if batch:
                cur.executemany(
                    "INSERT OR IGNORE INTO paragraph_ngrams(term, paragraph_hash) VALUES (?, ?)",
                    batch,
                )

            cur.execute("""
                INSERT INTO paragraph_ngram_meta(key, value) VALUES('ngram_n', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (str(n),))
            cur.execute("""
                INSERT INTO paragraph_ngram_meta(key, value) VALUES('paragraph_count', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (str(para_count),))
            cur.execute("""
                INSERT INTO paragraph_ngram_meta(key, value) VALUES('updated_at', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (str(datetime.now().timestamp()),))
            c.commit()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "paragraph ngram 回填完成: "
                f"n={n}, paragraphs={para_count}, terms={term_count}, duration_ms={elapsed_ms:.2f}"
            )
            return True
        except Exception as e:
            logger.warning(f"paragraph ngram 回填失败: {e}")
            c.rollback()
            return False

    def fts_upsert_paragraph(
        self,
        paragraph_hash: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        将段落写入（或覆盖）到 FTS 索引。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute(
                "SELECT rowid, content FROM paragraphs WHERE hash = ?",
                (paragraph_hash,),
            )
            row = cur.fetchone()
            if not row:
                return False
            rowid = int(row[0])
            content = str(row[1] or "")
            cur.execute(
                "INSERT OR REPLACE INTO paragraphs_fts(rowid, content) VALUES (?, ?)",
                (rowid, content),
            )
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS upsert 失败: {e}")
            c.rollback()
            return False

    def fts_delete_paragraph(
        self,
        paragraph_hash: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        从 FTS 索引删除段落。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute(
                "SELECT rowid, content FROM paragraphs WHERE hash = ?",
                (paragraph_hash,),
            )
            row = cur.fetchone()
            if not row:
                return False
            rowid = int(row[0])
            content = str(row[1] or "")
            cur.execute(
                "INSERT INTO paragraphs_fts(paragraphs_fts, rowid, content) VALUES ('delete', ?, ?)",
                (rowid, content),
            )
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS delete 失败: {e}")
            c.rollback()
            return False

    def fts_search_bm25(
        self,
        match_query: str,
        limit: int = 20,
        max_doc_len: int = 2000,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """
        使用 FTS5 + bm25 执行全文检索。
        """
        if not match_query.strip():
            return []

        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute(
                """
                SELECT p.hash, p.content, bm25(paragraphs_fts) AS bm25_score
                FROM paragraphs_fts
                JOIN paragraphs p ON p.rowid = paragraphs_fts.rowid
                WHERE paragraphs_fts MATCH ?
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                ORDER BY bm25_score ASC
                LIMIT ?
                """,
                (match_query, max(1, int(limit))),
            )
            rows = cur.fetchall()
            results: List[Dict[str, Any]] = []
            for row in rows:
                content = str(row["content"] or "")
                if max_doc_len > 0:
                    content = content[:max_doc_len]
                results.append(
                    {
                        "hash": row["hash"],
                        "content": content,
                        "bm25_score": float(row["bm25_score"]),
                    }
                )
            return results
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS 查询失败: {e}")
            return []

    def fts_search_relations_bm25(
        self,
        match_query: str,
        limit: int = 20,
        max_doc_len: int = 512,
        include_inactive: bool = True,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """使用 FTS5 + bm25 执行关系全文检索。"""
        if not match_query.strip():
            return []

        c = self._resolve_conn(conn)
        cur = c.cursor()
        active_clause = "" if include_inactive else " AND (r.is_inactive IS NULL OR r.is_inactive = 0)"
        try:
            cur.execute(
                f"""
                SELECT
                    r.hash,
                    r.subject,
                    r.predicate,
                    r.object,
                    bm25(relations_fts) AS bm25_score
                FROM relations_fts
                JOIN relations r ON r.hash = relations_fts.relation_hash
                WHERE relations_fts MATCH ?
                {active_clause}
                ORDER BY bm25_score ASC
                LIMIT ?
                """,
                (match_query, max(1, int(limit))),
            )
            rows = cur.fetchall()
            out: List[Dict[str, Any]] = []
            for row in rows:
                content = f"{row['subject']} {row['predicate']} {row['object']}"
                if max_doc_len > 0:
                    content = content[:max_doc_len]
                out.append(
                    {
                        "hash": row["hash"],
                        "subject": row["subject"],
                        "predicate": row["predicate"],
                        "object": row["object"],
                        "content": content,
                        "bm25_score": float(row["bm25_score"]),
                    }
                )
            return out
        except sqlite3.OperationalError as e:
            logger.warning(f"relations FTS 查询失败: {e}")
            return []

    def ngram_search_paragraphs(
        self,
        tokens: List[str],
        limit: int = 20,
        max_doc_len: int = 2000,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """按 ngram 倒排索引检索段落，避免 LIKE 全表扫描。"""
        uniq = [t for t in dict.fromkeys([str(x).strip().lower() for x in tokens]) if t]
        if not uniq:
            return []

        c = self._resolve_conn(conn)
        cur = c.cursor()
        placeholders = ",".join(["?"] * len(uniq))
        try:
            cur.execute(
                f"""
                SELECT
                    p.hash,
                    p.content,
                    COUNT(*) AS hit_terms
                FROM paragraph_ngrams ng
                JOIN paragraphs p ON p.hash = ng.paragraph_hash
                WHERE ng.term IN ({placeholders})
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                GROUP BY p.hash, p.content
                ORDER BY hit_terms DESC
                LIMIT ?
                """,
                tuple(uniq + [max(1, int(limit))]),
            )
            rows = cur.fetchall()
            out: List[Dict[str, Any]] = []
            token_count = max(1, len(uniq))
            for row in rows:
                hit_terms = int(row["hit_terms"])
                score = float(hit_terms / token_count)
                content = str(row["content"] or "")
                if max_doc_len > 0:
                    content = content[:max_doc_len]
                out.append(
                    {
                        "hash": row["hash"],
                        "content": content,
                        "bm25_score": -score,
                        "fallback_score": score,
                    }
                )
            return out
        except sqlite3.OperationalError as e:
            logger.warning(f"ngram 倒排查询失败: {e}")
            return []

    def fts_doc_count(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """获取 FTS 文档数量。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("SELECT COUNT(1) FROM paragraphs_fts")
            return int(cur.fetchone()[0])
        except sqlite3.OperationalError:
            return 0

    def shrink_memory(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """请求 SQLite 收缩当前连接缓存。"""
        c = self._resolve_conn(conn)
        try:
            c.execute("PRAGMA shrink_memory")
        except sqlite3.OperationalError:
            pass
