from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import json
import re
import sqlite3

from ..utils.hash import compute_hash, normalize_text
from .tokenizer_runtime import HAS_JIEBA, JIEBA_MODULE


class MetadataEpisodeMixin:
    """维护 Episode、重建队列与段落回填任务。"""

    @staticmethod
    def _normalize_episode_source(source: Any) -> str:
        return str(source or "").strip()

    def _dedupe_episode_sources(self, sources: List[Any]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for item in sources or []:
            token = self._normalize_episode_source(item)
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return normalized

    def _get_sources_for_paragraph_hashes(
        self,
        hashes: List[str],
        *,
        include_deleted: bool = True,
    ) -> List[str]:
        normalized_hashes = [str(item or "").strip() for item in (hashes or []) if str(item or "").strip()]
        if not normalized_hashes:
            return []

        placeholders = ",".join(["?"] * len(normalized_hashes))
        conditions = ["hash IN ({})".format(placeholders), "TRIM(COALESCE(source, '')) != ''"]
        if not include_deleted:
            conditions.append("(is_deleted IS NULL OR is_deleted = 0)")

        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT DISTINCT TRIM(source) AS source
            FROM paragraphs
            WHERE {" AND ".join(conditions)}
            """,
            tuple(normalized_hashes),
        )
        return self._dedupe_episode_sources([row["source"] for row in cursor.fetchall()])

    def _enqueue_episode_source_rebuilds(self, sources: List[Any], reason: str = "") -> int:
        normalized_sources = self._dedupe_episode_sources(sources)
        if not normalized_sources:
            return 0

        now = datetime.now().timestamp()
        reason_text = str(reason or "").strip()[:200] or None
        cursor = self._conn.cursor()
        cursor.executemany(
            """
            INSERT INTO episode_rebuild_sources (
                source, status, retry_count, last_error, reason, requested_at, updated_at
            ) VALUES (?, 'pending', 0, NULL, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                status = 'pending',
                retry_count = 0,
                last_error = NULL,
                reason = excluded.reason,
                requested_at = excluded.requested_at,
                updated_at = excluded.updated_at
            """,
            [(source, reason_text, now, now) for source in normalized_sources],
        )
        self._conn.commit()
        return len(normalized_sources)

    def enqueue_episode_source_rebuild(self, source: str, reason: str = "") -> bool:
        """将 source 入队到 episode 重建队列。"""
        return bool(self._enqueue_episode_source_rebuilds([source], reason=reason))

    def fetch_episode_source_rebuild_batch(
        self,
        limit: int = 20,
        max_retry: int = 3,
    ) -> List[Dict[str, Any]]:
        """获取待处理的 source 重建任务。"""
        safe_limit = max(1, int(limit))
        safe_retry = max(0, int(max_retry))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT source, status, retry_count, last_error, reason, requested_at, updated_at
            FROM episode_rebuild_sources
            WHERE status = 'pending'
               OR (status = 'failed' AND retry_count < ?)
            ORDER BY requested_at ASC, updated_at ASC
            LIMIT ?
            """,
            (safe_retry, safe_limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_episode_source_running(
        self,
        source: str,
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        """将 source 标记为 running。"""
        token = self._normalize_episode_source(source)
        if not token:
            return False

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        params: List[Any] = [now, token]
        sql = """
            UPDATE episode_rebuild_sources
            SET status = 'running',
                updated_at = ?
            WHERE source = ?
              AND status IN ('pending', 'failed')
        """
        if requested_at is not None:
            sql += " AND requested_at = ?"
            params.append(float(requested_at))
        cursor.execute(sql, tuple(params))
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_episode_source_done(
        self,
        source: str,
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        """将 source 标记为 done；若运行期间发生新写入，则保持 pending。"""
        token = self._normalize_episode_source(source)
        if not token:
            return False

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        if requested_at is None:
            cursor.execute(
                """
                UPDATE episode_rebuild_sources
                SET status = 'done',
                    last_error = NULL,
                    updated_at = ?
                WHERE source = ?
                """,
                (now, token),
            )
        else:
            req_ts = float(requested_at)
            cursor.execute(
                """
                UPDATE episode_rebuild_sources
                SET status = CASE
                        WHEN requested_at > ? THEN 'pending'
                        ELSE 'done'
                    END,
                    last_error = NULL,
                    updated_at = ?
                WHERE source = ?
                """,
                (req_ts, now, token),
            )
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_episode_source_failed(
        self,
        source: str,
        error: str = "",
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        """标记 source 失败；若运行期间发生新写入，则重新回到 pending。"""
        token = self._normalize_episode_source(source)
        if not token:
            return False

        err_text = str(error or "").strip()[:500]
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        if requested_at is None:
            cursor.execute(
                """
                UPDATE episode_rebuild_sources
                SET status = 'failed',
                    retry_count = COALESCE(retry_count, 0) + 1,
                    last_error = ?,
                    updated_at = ?
                WHERE source = ?
                """,
                (err_text, now, token),
            )
        else:
            req_ts = float(requested_at)
            cursor.execute(
                """
                UPDATE episode_rebuild_sources
                SET status = CASE
                        WHEN requested_at > ? THEN 'pending'
                        ELSE 'failed'
                    END,
                    retry_count = CASE
                        WHEN requested_at > ? THEN COALESCE(retry_count, 0)
                        ELSE COALESCE(retry_count, 0) + 1
                    END,
                    last_error = CASE
                        WHEN requested_at > ? THEN NULL
                        ELSE ?
                    END,
                    updated_at = ?
                WHERE source = ?
                """,
                (req_ts, req_ts, req_ts, err_text, now, token),
            )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_episode_source_rebuilds(
        self,
        *,
        statuses: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """列出 source 重建状态。"""
        safe_limit = max(1, int(limit))
        params: List[Any] = []
        conditions: List[str] = []
        normalized_statuses = [
            str(item or "").strip().lower()
            for item in (statuses or [])
            if str(item or "").strip().lower() in {"pending", "running", "done", "failed"}
        ]
        if normalized_statuses:
            placeholders = ",".join(["?"] * len(normalized_statuses))
            conditions.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(safe_limit)
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT source, status, retry_count, last_error, reason, requested_at, updated_at
            FROM episode_rebuild_sources
            {where_sql}
            ORDER BY updated_at DESC, source ASC
            LIMIT ?
            """,
            tuple(params),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_episode_source_rebuild_summary(self, failed_limit: int = 20) -> Dict[str, Any]:
        """汇总 source 重建队列状态。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM episode_rebuild_sources
            GROUP BY status
            """
        )
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "total": 0}
        for row in cursor.fetchall():
            status = str(row["status"] or "").strip().lower()
            cnt = int(row["cnt"] or 0)
            counts[status] = counts.get(status, 0) + cnt
            counts["total"] += cnt

        running = self.list_episode_source_rebuilds(statuses=["running"], limit=20)
        failed = self.list_episode_source_rebuilds(
            statuses=["failed"],
            limit=max(1, int(failed_limit)),
        )
        return {
            "counts": counts,
            "running": running,
            "failed": failed,
        }

    def get_live_paragraphs_by_source(self, source: str, *, exclude_stale: bool = False) -> List[Dict[str, Any]]:
        """获取指定 source 下所有 live paragraphs。"""
        token = self._normalize_episode_source(source)
        if not token:
            return []
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM paragraphs
            WHERE TRIM(COALESCE(source, '')) = ?
              AND (is_deleted IS NULL OR is_deleted = 0)
            ORDER BY created_at ASC, hash ASC
            """,
            (token,),
        )
        rows = [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]
        if not exclude_stale:
            return rows
        paragraph_hashes = [
            str(row.get("hash", "") or "").strip() for row in rows if str(row.get("hash", "") or "").strip()
        ]
        marks_by_paragraph = self.get_paragraph_stale_relation_marks_batch(paragraph_hashes) if paragraph_hashes else {}
        relation_hashes: List[str] = []
        seen = set()
        for marks in marks_by_paragraph.values():
            for mark in marks:
                relation_hash = str(mark.get("relation_hash", "") or "").strip()
                if not relation_hash or relation_hash in seen:
                    continue
                seen.add(relation_hash)
                relation_hashes.append(relation_hash)
        status_map = self.get_relation_status_batch(relation_hashes) if relation_hashes else {}

        filtered: List[Dict[str, Any]] = []
        for row in rows:
            paragraph_hash = str(row.get("hash", "") or "").strip()
            marks = marks_by_paragraph.get(paragraph_hash, [])
            if any(
                status_map.get(str(mark.get("relation_hash", "") or "").strip()) is None
                or bool((status_map.get(str(mark.get("relation_hash", "") or "").strip()) or {}).get("is_inactive"))
                for mark in marks
                if str(mark.get("relation_hash", "") or "").strip()
            ):
                continue
            filtered.append(row)
        return filtered

    def list_episode_sources_for_rebuild(self) -> List[str]:
        """列出全量重建涉及的 source（live paragraphs + stale episodes）。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT source
            FROM (
                SELECT TRIM(source) AS source
                FROM paragraphs
                WHERE TRIM(COALESCE(source, '')) != ''
                  AND (is_deleted IS NULL OR is_deleted = 0)
                UNION
                SELECT TRIM(source) AS source
                FROM episodes
                WHERE TRIM(COALESCE(source, '')) != ''
            )
            WHERE TRIM(COALESCE(source, '')) != ''
            ORDER BY source ASC
            """
        )
        return self._dedupe_episode_sources([row["source"] for row in cursor.fetchall()])

    def is_episode_source_query_blocked(self, source: str) -> bool:
        """判断 source 是否处于重建中或失败状态。"""
        token = self._normalize_episode_source(source)
        if not token:
            return False
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM episode_rebuild_sources
            WHERE source = ?
              AND status IN ('pending', 'running', 'failed')
            LIMIT 1
            """,
            (token,),
        )
        return cursor.fetchone() is not None

    def replace_episodes_for_source(
        self,
        source: str,
        episodes_payloads: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """按 source 全量替换 episode 结果。"""
        token = self._normalize_episode_source(source)
        if not token:
            return {"source": "", "episode_count": 0}

        payloads = [dict(item) for item in (episodes_payloads or []) if isinstance(item, dict)]
        now = datetime.now().timestamp()

        with self.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT episode_id, created_at
                FROM episodes
                WHERE TRIM(COALESCE(source, '')) = ?
                """,
                (token,),
            )
            existing_created_at = {
                str(row["episode_id"]): self._as_optional_float(row["created_at"]) for row in cursor.fetchall()
            }

            cursor.execute(
                "DELETE FROM episodes WHERE TRIM(COALESCE(source, '')) = ?",
                (token,),
            )

            inserted_count = 0
            for raw_payload in payloads:
                title = str(raw_payload.get("title", "") or "").strip()
                summary = str(raw_payload.get("summary", "") or "").strip()
                evidence_ids = [
                    str(item).strip() for item in (raw_payload.get("evidence_ids") or []) if str(item).strip()
                ]
                evidence_ids = list(dict.fromkeys(evidence_ids))
                if not title or not summary or not evidence_ids:
                    continue

                episode_id = str(raw_payload.get("episode_id", "") or "").strip()
                if not episode_id:
                    seed = json.dumps(
                        {
                            "source": token,
                            "title": title,
                            "summary": summary,
                            "event_time_start": raw_payload.get("event_time_start"),
                            "event_time_end": raw_payload.get("event_time_end"),
                            "evidence_ids": evidence_ids,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    episode_id = compute_hash(seed)

                participants = [
                    str(item).strip() for item in (raw_payload.get("participants") or []) if str(item).strip()
                ][:16]
                keywords = [str(item).strip() for item in (raw_payload.get("keywords") or []) if str(item).strip()][:20]
                paragraph_count = raw_payload.get("paragraph_count", len(evidence_ids))
                try:
                    paragraph_count = max(0, int(paragraph_count))
                except Exception:
                    paragraph_count = len(evidence_ids)
                if paragraph_count <= 0:
                    paragraph_count = len(evidence_ids)
                if paragraph_count <= 0:
                    continue

                time_confidence = raw_payload.get("time_confidence", 1.0)
                llm_confidence = raw_payload.get("llm_confidence", 0.0)
                try:
                    time_confidence = float(time_confidence)
                except Exception:
                    time_confidence = 1.0
                try:
                    llm_confidence = float(llm_confidence)
                except Exception:
                    llm_confidence = 0.0

                created_at = existing_created_at.get(episode_id)
                created_ts = created_at if created_at is not None else now
                updated_ts = self._as_optional_float(raw_payload.get("updated_at")) or now

                cursor.execute(
                    """
                    INSERT INTO episodes (
                        episode_id, source, title, summary,
                        event_time_start, event_time_end, time_granularity, time_confidence,
                        participants_json, keywords_json, evidence_ids_json,
                        paragraph_count, llm_confidence, segmentation_model, segmentation_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        episode_id,
                        token,
                        title[:120],
                        summary[:2000],
                        self._as_optional_float(raw_payload.get("event_time_start")),
                        self._as_optional_float(raw_payload.get("event_time_end")),
                        str(raw_payload.get("time_granularity", "") or "").strip() or None,
                        time_confidence,
                        json.dumps(participants, ensure_ascii=False),
                        json.dumps(keywords, ensure_ascii=False),
                        json.dumps(evidence_ids, ensure_ascii=False),
                        paragraph_count,
                        llm_confidence,
                        str(raw_payload.get("segmentation_model", "") or "").strip() or None,
                        str(raw_payload.get("segmentation_version", "") or "").strip() or None,
                        created_ts,
                        updated_ts,
                    ),
                )
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO episode_paragraphs (episode_id, paragraph_hash, position)
                    VALUES (?, ?, ?)
                    """,
                    [(episode_id, hash_value, idx) for idx, hash_value in enumerate(evidence_ids)],
                )
                inserted_count += 1

            return {"source": token, "episode_count": inserted_count}

    def enqueue_episode_pending(
        self,
        paragraph_hash: str,
        source: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> None:
        """将段落入队到 episode 异步生成队列。"""
        token = str(paragraph_hash or "").strip()
        if not token:
            return
        now = datetime.now().timestamp()
        created_ts = float(created_at) if created_at is not None else now
        src = str(source or "").strip() or None

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO episode_pending_paragraphs (
                paragraph_hash, source, created_at, status, retry_count, last_error, updated_at
            ) VALUES (?, ?, ?, 'pending', 0, NULL, ?)
            ON CONFLICT(paragraph_hash) DO UPDATE SET
                source = excluded.source,
                created_at = COALESCE(episode_pending_paragraphs.created_at, excluded.created_at),
                status = CASE
                    WHEN episode_pending_paragraphs.status = 'done' THEN 'done'
                    ELSE 'pending'
                END,
                last_error = CASE
                    WHEN episode_pending_paragraphs.status = 'done' THEN episode_pending_paragraphs.last_error
                    ELSE NULL
                END,
                updated_at = excluded.updated_at
            """,
            (token, src, created_ts, now),
        )
        self._conn.commit()

    def fetch_episode_pending_batch(self, limit: int = 20, max_retry: int = 3) -> List[Dict[str, Any]]:
        """获取待处理 episode 队列批次。"""
        safe_limit = max(1, int(limit))
        safe_retry = max(0, int(max_retry))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT paragraph_hash, source, created_at, status, retry_count, last_error, updated_at
            FROM episode_pending_paragraphs
            WHERE status = 'pending'
               OR (status = 'failed' AND retry_count < ?)
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (safe_retry, safe_limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_episode_pending_running(self, hashes: List[str]) -> None:
        """批量标记队列项为 running。"""
        if not hashes:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        chunk_size = 500
        uniq = list(dict.fromkeys([str(h).strip() for h in hashes if str(h).strip()]))
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE episode_pending_paragraphs
                SET status = 'running', updated_at = ?
                WHERE paragraph_hash IN ({placeholders})
                  AND status IN ('pending', 'failed')
                """,
                [now] + chunk,
            )
        self._conn.commit()

    def mark_episode_pending_done(self, hashes: List[str]) -> None:
        """批量标记队列项为 done。"""
        if not hashes:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        chunk_size = 500
        uniq = list(dict.fromkeys([str(h).strip() for h in hashes if str(h).strip()]))
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE episode_pending_paragraphs
                SET status = 'done',
                    last_error = NULL,
                    updated_at = ?
                WHERE paragraph_hash IN ({placeholders})
                """,
                [now] + chunk,
            )
        self._conn.commit()

    def mark_episode_pending_failed(self, hash_value: str, error: str = "") -> None:
        """标记单条队列项失败并累加重试次数。"""
        token = str(hash_value or "").strip()
        if not token:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE episode_pending_paragraphs
            SET status = 'failed',
                retry_count = COALESCE(retry_count, 0) + 1,
                last_error = ?,
                updated_at = ?
            WHERE paragraph_hash = ?
            """,
            (str(error or ""), now, token),
        )
        self._conn.commit()

    def get_episode_pending_status_counts(self, source: str) -> Dict[str, int]:
        """统计某个 source 当前 pending 队列中的状态分布。"""
        token = self._normalize_episode_source(source)
        if not token:
            return {"pending": 0, "running": 0, "failed": 0, "done": 0}

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM episode_pending_paragraphs
            WHERE TRIM(COALESCE(source, '')) = ?
            GROUP BY status
            """,
            (token,),
        )
        counts = {"pending": 0, "running": 0, "failed": 0, "done": 0}
        for row in cursor.fetchall():
            status = str(row["status"] or "").strip().lower()
            if status in counts:
                counts[status] = int(row["count"] or 0)
        return counts

    def enqueue_paragraph_vector_backfill(
        self,
        paragraph_hash: str,
        *,
        created_at: Optional[float] = None,
        error: str = "",
    ) -> None:
        """登记段落向量回填任务。"""
        token = str(paragraph_hash or "").strip()
        if not token:
            return

        now = datetime.now().timestamp()
        created_ts = float(created_at) if created_at is not None else now
        error_text = str(error or "").strip() or None

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO paragraph_vector_backfill (
                paragraph_hash, status, retry_count, last_error, created_at, updated_at
            ) VALUES (?, 'pending', 0, ?, ?, ?)
            ON CONFLICT(paragraph_hash) DO UPDATE SET
                status = CASE
                    WHEN paragraph_vector_backfill.status = 'done' THEN 'done'
                    ELSE 'pending'
                END,
                last_error = CASE
                    WHEN paragraph_vector_backfill.status = 'done' THEN paragraph_vector_backfill.last_error
                    ELSE excluded.last_error
                END,
                created_at = COALESCE(paragraph_vector_backfill.created_at, excluded.created_at),
                updated_at = excluded.updated_at
            """,
            (token, error_text, created_ts, now),
        )
        self._conn.commit()

    def fetch_paragraph_vector_backfill_batch(
        self,
        limit: int = 64,
        max_retry: int = 5,
    ) -> List[Dict[str, Any]]:
        """获取段落向量回填批次。"""
        safe_limit = max(1, int(limit))
        safe_retry = max(0, int(max_retry))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT paragraph_hash, status, retry_count, last_error, created_at, updated_at
            FROM paragraph_vector_backfill
            WHERE status = 'pending'
               OR (status = 'failed' AND retry_count < ?)
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (safe_retry, safe_limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_paragraph_vector_backfill_running(self, hashes: List[str]) -> None:
        """批量标记段落回填任务为 running。"""
        if not hashes:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        uniq = list(dict.fromkeys([str(h or "").strip() for h in hashes if str(h or "").strip()]))
        if not uniq:
            return
        chunk_size = 500
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE paragraph_vector_backfill
                SET status = 'running', updated_at = ?
                WHERE paragraph_hash IN ({placeholders})
                  AND status IN ('pending', 'failed')
                """,
                [now] + chunk,
            )
        self._conn.commit()

    def mark_paragraph_vector_backfill_done(self, hashes: List[str]) -> None:
        """批量标记段落回填任务为 done。"""
        if not hashes:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        uniq = list(dict.fromkeys([str(h or "").strip() for h in hashes if str(h or "").strip()]))
        if not uniq:
            return
        chunk_size = 500
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE paragraph_vector_backfill
                SET status = 'done',
                    last_error = NULL,
                    updated_at = ?
                WHERE paragraph_hash IN ({placeholders})
                """,
                [now] + chunk,
            )
        self._conn.commit()

    def mark_paragraph_vector_backfill_failed(self, paragraph_hash: str, error: str = "") -> None:
        """标记单个段落回填任务失败并累加重试。"""
        token = str(paragraph_hash or "").strip()
        if not token:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE paragraph_vector_backfill
            SET status = 'failed',
                retry_count = COALESCE(retry_count, 0) + 1,
                last_error = ?,
                updated_at = ?
            WHERE paragraph_hash = ?
            """,
            (str(error or ""), now, token),
        )
        self._conn.commit()

    def get_paragraph_vector_backfill_status_counts(self) -> Dict[str, int]:
        """统计段落回填任务状态。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM paragraph_vector_backfill
            GROUP BY status
            """
        )
        counts = {"pending": 0, "running": 0, "failed": 0, "done": 0}
        for row in cursor.fetchall():
            status = str(row["status"] or "").strip().lower()
            if status in counts:
                counts[status] = int(row["count"] or 0)
        return counts

    def _episode_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)

        def _load_list(raw: Any) -> List[Any]:
            if not raw:
                return []
            try:
                val = json.loads(raw)
                return val if isinstance(val, list) else []
            except Exception:
                return []

        data["participants"] = _load_list(data.pop("participants_json", None))
        data["keywords"] = _load_list(data.pop("keywords_json", None))
        data["evidence_ids"] = _load_list(data.pop("evidence_ids_json", None))
        return data

    @staticmethod
    def _as_optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def upsert_episode(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """写入或更新 episode。"""
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是字典")

        title = str(payload.get("title", "") or "").strip()
        summary = str(payload.get("summary", "") or "").strip()
        if not title:
            raise ValueError("episode.title 不能为空")
        if not summary:
            raise ValueError("episode.summary 不能为空")

        source = str(payload.get("source", "") or "").strip() or None
        participants_raw = payload.get("participants", []) or []
        keywords_raw = payload.get("keywords", []) or []
        evidence_ids_raw = payload.get("evidence_ids", []) or []
        participants = [str(x).strip() for x in participants_raw if str(x).strip()]
        keywords = [str(x).strip() for x in keywords_raw if str(x).strip()]
        evidence_ids = [str(x).strip() for x in evidence_ids_raw if str(x).strip()]

        now = datetime.now().timestamp()
        created_at = self._as_optional_float(payload.get("created_at"))
        updated_at = self._as_optional_float(payload.get("updated_at"))
        created_ts = created_at if created_at is not None else now
        updated_ts = updated_at if updated_at is not None else now

        episode_id = str(payload.get("episode_id", "") or "").strip()
        if not episode_id:
            seed = json.dumps(
                {
                    "source": source,
                    "title": title,
                    "summary": summary,
                    "event_time_start": payload.get("event_time_start"),
                    "event_time_end": payload.get("event_time_end"),
                    "evidence_ids": evidence_ids,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            episode_id = compute_hash(seed)

        paragraph_count = payload.get("paragraph_count")
        if paragraph_count is None:
            paragraph_count = len(evidence_ids)
        try:
            paragraph_count = int(paragraph_count)
        except Exception:
            paragraph_count = len(evidence_ids)

        time_conf = payload.get("time_confidence", 1.0)
        llm_conf = payload.get("llm_confidence", 0.0)
        try:
            time_conf = float(time_conf)
        except Exception:
            time_conf = 1.0
        try:
            llm_conf = float(llm_conf)
        except Exception:
            llm_conf = 0.0

        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT created_at FROM episodes WHERE episode_id = ? LIMIT 1",
            (episode_id,),
        )
        existed = cursor.fetchone()
        if existed and existed[0] is not None:
            created_ts = float(existed[0])

        cursor.execute(
            """
            INSERT INTO episodes (
                episode_id, source, title, summary,
                event_time_start, event_time_end, time_granularity, time_confidence,
                participants_json, keywords_json, evidence_ids_json,
                paragraph_count, llm_confidence, segmentation_model, segmentation_version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                source = excluded.source,
                title = excluded.title,
                summary = excluded.summary,
                event_time_start = excluded.event_time_start,
                event_time_end = excluded.event_time_end,
                time_granularity = excluded.time_granularity,
                time_confidence = excluded.time_confidence,
                participants_json = excluded.participants_json,
                keywords_json = excluded.keywords_json,
                evidence_ids_json = excluded.evidence_ids_json,
                paragraph_count = excluded.paragraph_count,
                llm_confidence = excluded.llm_confidence,
                segmentation_model = excluded.segmentation_model,
                segmentation_version = excluded.segmentation_version,
                updated_at = excluded.updated_at
            """,
            (
                episode_id,
                source,
                title,
                summary,
                self._as_optional_float(payload.get("event_time_start")),
                self._as_optional_float(payload.get("event_time_end")),
                str(payload.get("time_granularity", "") or "").strip() or None,
                time_conf,
                json.dumps(participants, ensure_ascii=False),
                json.dumps(keywords, ensure_ascii=False),
                json.dumps(evidence_ids, ensure_ascii=False),
                max(0, paragraph_count),
                llm_conf,
                str(payload.get("segmentation_model", "") or "").strip() or None,
                str(payload.get("segmentation_version", "") or "").strip() or None,
                created_ts,
                updated_ts,
            ),
        )
        self._conn.commit()
        return self.get_episode_by_id(episode_id) or {"episode_id": episode_id}

    def bind_episode_paragraphs(self, episode_id: str, paragraph_hashes_ordered: List[str]) -> int:
        """重建 episode 与段落映射。"""
        token = str(episode_id or "").strip()
        if not token:
            raise ValueError("episode_id 不能为空")

        normalized: List[str] = []
        seen = set()
        for item in paragraph_hashes_ordered or []:
            h = str(item or "").strip()
            if not h or h in seen:
                continue
            seen.add(h)
            normalized.append(h)

        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM episode_paragraphs WHERE episode_id = ?", (token,))

        if normalized:
            cursor.executemany(
                """
                INSERT OR IGNORE INTO episode_paragraphs (episode_id, paragraph_hash, position)
                VALUES (?, ?, ?)
                """,
                [(token, h, idx) for idx, h in enumerate(normalized)],
            )

        now = datetime.now().timestamp()
        cursor.execute(
            """
            UPDATE episodes
            SET paragraph_count = ?, updated_at = ?
            WHERE episode_id = ?
            """,
            (len(normalized), now, token),
        )
        self._conn.commit()
        return len(normalized)

    def _build_episode_query_components(
        self,
        *,
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Tuple[str, str, str, List[str], List[Any]]:
        source_expr = "TRIM(COALESCE(e.source, ''))"
        effective_start = "COALESCE(e.event_time_start, e.event_time_end, e.updated_at)"
        effective_end = "COALESCE(e.event_time_end, e.event_time_start, e.updated_at)"
        conditions: List[str] = []
        params: List[Any] = []

        conditions.append(f"{source_expr} != ''")
        conditions.append("COALESCE(e.paragraph_count, 0) > 0")
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM episode_rebuild_sources ers
                WHERE ers.source = TRIM(COALESCE(e.source, ''))
                  AND ers.status IN ('pending', 'running')
            )
            """
        )

        if source:
            token = self._normalize_episode_source(source)
            if not token:
                return source_expr, effective_start, effective_end, ["1 = 0"], []
            conditions.append(f"{source_expr} = ?")
            params.append(token)

        p = str(person or "").strip().lower()
        if p:
            like_person = f"%{p}%"
            conditions.append(
                """
                (
                    LOWER(COALESCE(e.participants_json, '')) LIKE ?
                    OR EXISTS (
                        SELECT 1
                        FROM episode_paragraphs ep_person
                        JOIN paragraph_entities pe ON pe.paragraph_hash = ep_person.paragraph_hash
                        JOIN entities en ON en.hash = pe.entity_hash
                        WHERE ep_person.episode_id = e.episode_id
                          AND LOWER(en.name) LIKE ?
                    )
                )
                """
            )
            params.extend([like_person, like_person])

        if time_from is not None and time_to is not None:
            conditions.append(f"({effective_end} >= ? AND {effective_start} <= ?)")
            params.extend([float(time_from), float(time_to)])
        elif time_from is not None:
            conditions.append(f"({effective_end} >= ?)")
            params.append(float(time_from))
        elif time_to is not None:
            conditions.append(f"({effective_start} <= ?)")
            params.append(float(time_to))

        return source_expr, effective_start, effective_end, conditions, params

    @staticmethod
    def _tokenize_episode_query(query: str) -> Tuple[str, List[str]]:
        """将 episode 查询归一化为短语和 token。"""
        normalized = normalize_text(str(query or "")).strip().lower()
        if not normalized:
            return "", []

        tokens: List[str] = []
        seen = set()

        def _push(token: str) -> None:
            clean = str(token or "").strip().lower()
            if len(clean) < 2 or clean in seen:
                return
            seen.add(clean)
            tokens.append(clean)

        for span in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", normalized):
            if re.fullmatch(r"[A-Za-z0-9_]+", span):
                _push(span)
                continue

            segmented: List[str] = []
            if HAS_JIEBA:
                try:
                    segmented = [
                        str(item).strip().lower()
                        for item in JIEBA_MODULE.cut_for_search(span)  # type: ignore[union-attr]
                        if len(str(item).strip()) >= 2
                    ]
                except Exception:
                    segmented = []

            if not segmented:
                compact = span.strip()
                if len(compact) <= 3:
                    segmented = [compact]
                else:
                    for n in range(2, min(4, len(compact)) + 1):
                        segmented.extend(compact[i : i + n] for i in range(0, len(compact) - n + 1))

            for token in segmented:
                _push(token)

        if not tokens and len(normalized) >= 2:
            tokens = [normalized]
        return normalized, tokens

    def get_episode_rows_by_paragraph_hashes(
        self,
        paragraph_hashes: List[str],
        *,
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        normalized: List[str] = []
        seen = set()
        for item in paragraph_hashes or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        if not normalized:
            return []

        _, _, _, conditions, params = self._build_episode_query_components(
            time_from=time_from,
            time_to=time_to,
            person=person,
            source=source,
        )
        placeholders = ",".join(["?"] * len(normalized))
        conditions.append(f"ep.paragraph_hash IN ({placeholders})")
        conditions.append("(p.is_deleted IS NULL OR p.is_deleted = 0)")
        where_sql = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT e.*, ep.paragraph_hash AS matched_paragraph_hash
            FROM episodes e
            JOIN episode_paragraphs ep ON ep.episode_id = e.episode_id
            JOIN paragraphs p ON p.hash = ep.paragraph_hash
            {where_sql}
            ORDER BY e.updated_at DESC
        """
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params + normalized))

        grouped: Dict[str, Dict[str, Any]] = {}
        for row in cursor.fetchall():
            episode_id = str(row["episode_id"] or "").strip()
            if not episode_id:
                continue
            payload = grouped.get(episode_id)
            if payload is None:
                payload = self._episode_row_to_dict(row)
                payload["matched_paragraph_hashes"] = []
                grouped[episode_id] = payload
            matched_hash = str(row["matched_paragraph_hash"] or "").strip()
            if matched_hash and matched_hash not in payload["matched_paragraph_hashes"]:
                payload["matched_paragraph_hashes"].append(matched_hash)

        out = list(grouped.values())
        for item in out:
            item["matched_paragraph_count"] = len(item.get("matched_paragraph_hashes", []))
        return out

    def get_episode_rows_by_relation_hashes(
        self,
        relation_hashes: List[str],
        *,
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        normalized: List[str] = []
        seen = set()
        for item in relation_hashes or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        if not normalized:
            return []

        _, _, _, conditions, params = self._build_episode_query_components(
            time_from=time_from,
            time_to=time_to,
            person=person,
            source=source,
        )
        placeholders = ",".join(["?"] * len(normalized))
        conditions.append(f"pr.relation_hash IN ({placeholders})")
        conditions.append("(p.is_deleted IS NULL OR p.is_deleted = 0)")
        where_sql = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT
                e.*,
                p.hash AS matched_paragraph_hash,
                pr.relation_hash AS matched_relation_hash
            FROM episodes e
            JOIN episode_paragraphs ep ON ep.episode_id = e.episode_id
            JOIN paragraphs p ON p.hash = ep.paragraph_hash
            JOIN paragraph_relations pr ON pr.paragraph_hash = p.hash
            {where_sql}
            ORDER BY e.updated_at DESC
        """
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params + normalized))

        grouped: Dict[str, Dict[str, Any]] = {}
        for row in cursor.fetchall():
            episode_id = str(row["episode_id"] or "").strip()
            if not episode_id:
                continue
            payload = grouped.get(episode_id)
            if payload is None:
                payload = self._episode_row_to_dict(row)
                payload["matched_paragraph_hashes"] = []
                payload["matched_relation_hashes"] = []
                grouped[episode_id] = payload
            matched_paragraph = str(row["matched_paragraph_hash"] or "").strip()
            matched_relation = str(row["matched_relation_hash"] or "").strip()
            if matched_paragraph and matched_paragraph not in payload["matched_paragraph_hashes"]:
                payload["matched_paragraph_hashes"].append(matched_paragraph)
            if matched_relation and matched_relation not in payload["matched_relation_hashes"]:
                payload["matched_relation_hashes"].append(matched_relation)

        out = list(grouped.values())
        for item in out:
            item["matched_paragraph_count"] = len(item.get("matched_paragraph_hashes", []))
            item["matched_relation_count"] = len(item.get("matched_relation_hashes", []))
        return out

    def query_episodes(
        self,
        query: str = "",
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """查询 episode 列表。"""
        safe_limit = max(1, int(limit))
        _, effective_start, effective_end, conditions, params = self._build_episode_query_components(
            time_from=time_from,
            time_to=time_to,
            person=person,
            source=source,
        )

        q, tokens = self._tokenize_episode_query(query)
        select_score_sql = "0.0 AS lexical_score"
        order_sql = f"{effective_end} DESC, e.updated_at DESC"
        select_params: List[Any] = []
        query_params: List[Any] = []
        if q:
            field_exprs = {
                "title": "LOWER(COALESCE(e.title, ''))",
                "summary": "LOWER(COALESCE(e.summary, ''))",
                "keywords": "LOWER(COALESCE(e.keywords_json, ''))",
                "participants": "LOWER(COALESCE(e.participants_json, ''))",
            }

            score_parts: List[str] = []
            phrase_like = f"%{q}%"
            score_parts.extend(
                [
                    f"CASE WHEN {field_exprs['title']} LIKE ? THEN 6.0 ELSE 0.0 END",
                    f"CASE WHEN {field_exprs['keywords']} LIKE ? THEN 4.5 ELSE 0.0 END",
                    f"CASE WHEN {field_exprs['summary']} LIKE ? THEN 3.0 ELSE 0.0 END",
                    f"CASE WHEN {field_exprs['participants']} LIKE ? THEN 2.0 ELSE 0.0 END",
                ]
            )
            select_params.extend([phrase_like, phrase_like, phrase_like, phrase_like])

            token_predicates: List[str] = []
            for token in tokens:
                like = f"%{token}%"
                token_any = (
                    f"({field_exprs['title']} LIKE ? OR "
                    f"{field_exprs['summary']} LIKE ? OR "
                    f"{field_exprs['keywords']} LIKE ? OR "
                    f"{field_exprs['participants']} LIKE ?)"
                )
                token_predicates.append(token_any)
                query_params.extend([like, like, like, like])

                score_parts.append(
                    "("
                    f"CASE WHEN {field_exprs['title']} LIKE ? THEN 3.0 ELSE 0.0 END + "
                    f"CASE WHEN {field_exprs['keywords']} LIKE ? THEN 2.5 ELSE 0.0 END + "
                    f"CASE WHEN {field_exprs['summary']} LIKE ? THEN 2.0 ELSE 0.0 END + "
                    f"CASE WHEN {field_exprs['participants']} LIKE ? THEN 1.5 ELSE 0.0 END + "
                    f"CASE WHEN {token_any.replace('?', '?')} THEN 2.0 ELSE 0.0 END"
                    ")"
                )
                select_params.extend([like, like, like, like, like, like, like, like])

            if token_predicates:
                conditions.append("(" + " OR ".join(token_predicates) + ")")

            select_score_sql = f"({' + '.join(score_parts)}) AS lexical_score"
            order_sql = f"lexical_score DESC, {effective_end} DESC, e.updated_at DESC"

        where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT e.*, {select_score_sql}
            FROM episodes e
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ?
        """
        final_params = list(select_params) + list(params) + list(query_params) + [safe_limit]

        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(final_params))
        return [self._episode_row_to_dict(row) for row in cursor.fetchall()]

    def get_episode_by_id(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """获取单条 episode。"""
        token = str(episode_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM episodes WHERE episode_id = ? LIMIT 1",
            (token,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._episode_row_to_dict(row)

    def get_episode_paragraphs(self, episode_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取 episode 关联段落（按 position 排序）。"""
        token = str(episode_id or "").strip()
        if not token:
            return []
        safe_limit = max(1, int(limit))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT p.*, ep.position
            FROM episode_paragraphs ep
            JOIN paragraphs p ON p.hash = ep.paragraph_hash
            WHERE ep.episode_id = ?
              AND (p.is_deleted IS NULL OR p.is_deleted = 0)
            ORDER BY ep.position ASC
            LIMIT ?
            """,
            (token, safe_limit),
        )
        items = []
        for row in cursor.fetchall():
            payload = self._row_to_dict(row, "paragraph")
            payload["position"] = row["position"]
            items.append(payload)
        return items

    def has_table(self, table_name: str) -> bool:
        """检查数据库是否存在指定表。"""
        if not self._conn:
            return False
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
            (table_name,),
        )
        return cursor.fetchone() is not None
