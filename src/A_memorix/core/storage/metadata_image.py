from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import json
import sqlite3
import time
import uuid


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class MetadataImageMixin:
    """图片资产、出现记录、认知、关联和嵌入任务的事务读写。"""

    def upsert_image_asset(
        self,
        *,
        content_hash: str,
        storage_key: str,
        mime_type: str,
        byte_size: int,
        width: int,
        height: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        database = self._resolve_conn(conn)
        now = _now()
        database.execute(
            """
            INSERT INTO image_assets(
                asset_id, content_hash, storage_key, mime_type, byte_size, width, height,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                storage_key=excluded.storage_key,
                mime_type=excluded.mime_type,
                byte_size=excluded.byte_size,
                width=excluded.width,
                height=excluded.height,
                status='active',
                updated_at=excluded.updated_at
            """,
            (content_hash, content_hash, storage_key, mime_type, byte_size, width, height, now, now),
        )
        row = database.execute("SELECT * FROM image_assets WHERE content_hash = ?", (content_hash,)).fetchone()
        if row is None:
            raise RuntimeError("图片资产写入后无法读取")
        return dict(row)

    def get_image_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute("SELECT * FROM image_assets WHERE asset_id = ?", (str(asset_id),)).fetchone()
        return dict(row) if row is not None else None

    def get_image_asset_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM image_assets WHERE content_hash = ?",
            (str(content_hash),),
        ).fetchone()
        return dict(row) if row is not None else None

    def upsert_image_occurrence(
        self,
        *,
        asset_id: str,
        external_ref: str,
        source_kind: str,
        scope_type: str,
        chat_id: str = "",
        message_id: str = "",
        component_path: str = "",
        occurred_at: Optional[float] = None,
        installation_id: str = "",
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        database = self._resolve_conn(conn)
        ref = str(external_ref or "").strip()
        if not ref:
            raise ValueError("图片出现记录缺少外部引用")
        now = _now()
        occurrence_id = uuid.uuid4().hex
        database.execute(
            """
            INSERT INTO image_occurrences(
                occurrence_id, asset_id, external_ref, source_kind, scope_type, chat_id,
                message_id, component_path, occurred_at, installation_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(external_ref) DO UPDATE SET
                asset_id=excluded.asset_id,
                source_kind=excluded.source_kind,
                scope_type=excluded.scope_type,
                chat_id=excluded.chat_id,
                message_id=excluded.message_id,
                component_path=excluded.component_path,
                occurred_at=COALESCE(excluded.occurred_at, image_occurrences.occurred_at),
                installation_id=excluded.installation_id,
                status='active',
                updated_at=excluded.updated_at
            """,
            (
                occurrence_id,
                asset_id,
                ref,
                source_kind,
                scope_type,
                chat_id,
                message_id,
                component_path,
                occurred_at,
                installation_id,
                now,
                now,
            ),
        )
        row = database.execute("SELECT * FROM image_occurrences WHERE external_ref = ?", (ref,)).fetchone()
        if row is None:
            raise RuntimeError("图片出现记录写入后无法读取")
        return dict(row)

    def get_image_occurrence(self, occurrence_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM image_occurrences WHERE occurrence_id = ?",
            (str(occurrence_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_image_occurrence_by_ref(self, external_ref: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM image_occurrences WHERE external_ref = ?",
            (str(external_ref),),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_visible_image_occurrences(
        self,
        *,
        chat_ids: Optional[Sequence[str]],
        exclude_occurrence_id: str = "",
    ) -> List[Dict[str, Any]]:
        sql = """
            SELECT occurrence.*
            FROM image_occurrences AS occurrence
            JOIN image_assets AS asset ON asset.asset_id = occurrence.asset_id
            WHERE occurrence.status = 'active' AND asset.status = 'active'
        """
        params: List[Any] = []
        if chat_ids is not None:
            tokens = [str(item).strip() for item in chat_ids if str(item).strip()]
            clauses = ["occurrence.scope_type = 'global'"]
            if tokens:
                placeholders = ",".join("?" for _ in tokens)
                clauses.append(f"(occurrence.scope_type = 'chat' AND occurrence.chat_id IN ({placeholders}))")
                params.extend(tokens)
            sql += " AND (" + " OR ".join(clauses) + ")"
        if exclude_occurrence_id:
            sql += " AND occurrence.occurrence_id != ?"
            params.append(str(exclude_occurrence_id))
        sql += " ORDER BY COALESCE(occurrence.occurred_at, occurrence.created_at) DESC"
        return [dict(row) for row in self._conn.execute(sql, tuple(params)).fetchall()]

    def list_image_occurrences_for_asset(self, asset_id: str, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM image_occurrences WHERE asset_id = ?"
        if not include_deleted:
            sql += " AND status = 'active'"
        sql += " ORDER BY COALESCE(occurred_at, created_at) DESC"
        return [dict(row) for row in self._conn.execute(sql, (str(asset_id),)).fetchall()]

    def add_image_observation(
        self,
        *,
        occurrence_id: str,
        text: str,
        source_kind: str,
        confirm_status: str,
        evidence: Optional[Dict[str, Any]] = None,
        supersedes_id: str = "",
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        content = str(text or "").strip()
        if not content:
            raise ValueError("图片认知文本不能为空")
        database = self._resolve_conn(conn)
        if not supersedes_id and source_kind in {"user_statement", "model_description"}:
            # 回填及描述重放不能重复创建认知，也不能重新激活已被人工修正的旧文本。
            existing = database.execute(
                "SELECT * FROM image_observations WHERE occurrence_id=? AND source_kind=? AND text=? "
                "ORDER BY created_at LIMIT 1", (occurrence_id, source_kind, content),
            ).fetchone()
            if existing is not None:
                return dict(existing)
        observation_id = uuid.uuid4().hex
        now = _now()
        if supersedes_id:
            previous = database.execute(
                "SELECT observation_id, version FROM image_observations WHERE observation_id = ? AND occurrence_id = ?",
                (supersedes_id, occurrence_id),
            ).fetchone()
            if previous is None:
                raise ValueError("被修正的图片认知不存在")
            version = int(previous["version"]) + 1
            database.execute(
                "UPDATE image_observations SET superseded_by = ? WHERE observation_id = ?",
                (observation_id, supersedes_id),
            )
        else:
            version = 1
        database.execute(
            """
            INSERT INTO image_observations(
                observation_id, occurrence_id, text, source_kind, confirm_status,
                evidence_json, version, supersedes_id, superseded_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?)
            """,
            (
                observation_id,
                occurrence_id,
                content,
                source_kind,
                confirm_status,
                _json(evidence or {}),
                version,
                supersedes_id,
                now,
            ),
        )
        row = database.execute(
            "SELECT * FROM image_observations WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("图片认知写入后无法读取")
        return dict(row)

    def list_image_observations(self, occurrence_ids: Sequence[str]) -> List[Dict[str, Any]]:
        tokens = [str(item) for item in occurrence_ids if str(item)]
        if not tokens:
            return []
        placeholders = ",".join("?" for _ in tokens)
        rows = self._conn.execute(
            f"""
            SELECT * FROM image_observations
            WHERE occurrence_id IN ({placeholders}) AND superseded_by = ''
            ORDER BY created_at ASC
            """,
            tuple(tokens),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_image_memory_link(
        self,
        *,
        occurrence_id: str,
        target_type: str,
        target_id: str,
        link_kind: str,
        evidence: Dict[str, Any],
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        database = self._resolve_conn(conn)
        now = _now()
        link_id = uuid.uuid4().hex
        database.execute(
            """
            INSERT INTO image_memory_links(
                link_id, occurrence_id, target_type, target_id, link_kind,
                evidence_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(occurrence_id, target_type, target_id) DO UPDATE SET
                link_kind=excluded.link_kind,
                evidence_json=excluded.evidence_json,
                status='active',
                updated_at=excluded.updated_at
            """,
            (link_id, occurrence_id, target_type, target_id, link_kind, _json(evidence), now, now),
        )
        row = database.execute(
            """
            SELECT * FROM image_memory_links
            WHERE occurrence_id = ? AND target_type = ? AND target_id = ?
            """,
            (occurrence_id, target_type, target_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("图片记忆关联写入后无法读取")
        return dict(row)

    def list_image_memory_links(self, occurrence_ids: Sequence[str]) -> List[Dict[str, Any]]:
        tokens = [str(item) for item in occurrence_ids if str(item)]
        if not tokens:
            return []
        placeholders = ",".join("?" for _ in tokens)
        return [
            dict(row)
            for row in self._conn.execute(
                f"SELECT * FROM image_memory_links WHERE status = 'active' AND occurrence_id IN ({placeholders})",
                tuple(tokens),
            ).fetchall()
        ]

    def invalidate_image_memory_links(
        self,
        *,
        target_type: str,
        target_ids: Sequence[str],
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        tokens = [str(item) for item in target_ids if str(item)]
        if not tokens:
            return 0
        database = self._resolve_conn(conn)
        placeholders = ",".join("?" for _ in tokens)
        cursor = database.execute(
            f"""
            UPDATE image_memory_links SET status='invalid', updated_at=?
            WHERE status='active' AND target_type=? AND target_id IN ({placeholders})
            """,
            (_now(), target_type, *tokens),
        )
        return int(cursor.rowcount or 0)

    def list_active_image_memory_link_ids(
        self,
        *,
        target_type: str,
        target_ids: Sequence[str],
    ) -> List[str]:
        """记录一次权威对象变更实际影响的图片关联，供精确回滚。"""

        tokens = [str(item) for item in target_ids if str(item)]
        if not tokens:
            return []
        placeholders = ",".join("?" for _ in tokens)
        rows = self._conn.execute(
            f"""
            SELECT link_id FROM image_memory_links
            WHERE status='active' AND target_type=? AND target_id IN ({placeholders})
            ORDER BY created_at ASC
            """,
            (target_type, *tokens),
        ).fetchall()
        return [str(row["link_id"]) for row in rows]

    def restore_image_memory_links(
        self,
        link_ids: Sequence[str],
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """恢复本次修正操作曾失效的关联，不触碰其他关联。"""

        tokens = [str(item) for item in link_ids if str(item)]
        if not tokens:
            return 0
        database = self._resolve_conn(conn)
        placeholders = ",".join("?" for _ in tokens)
        cursor = database.execute(
            f"""
            UPDATE image_memory_links SET status='active', updated_at=?
            WHERE status='invalid' AND link_id IN ({placeholders})
            """,
            (_now(), *tokens),
        )
        return int(cursor.rowcount or 0)

    def invalidate_image_memory_link(
        self,
        link_id: str,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        database = self._resolve_conn(conn)
        cursor = database.execute(
            "UPDATE image_memory_links SET status='invalid', updated_at=? WHERE link_id=? AND status='active'",
            (_now(), link_id),
        )
        return int(cursor.rowcount or 0)

    def remap_image_memory_links(
        self,
        *,
        target_type: str,
        target_id_map: Dict[str, str],
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """在权威对象改名时迁移图片关联，并合并已存在的目标关联。"""

        database = self._resolve_conn(conn)
        remapped = 0
        for old_target_id, new_target_id in target_id_map.items():
            old_token = str(old_target_id or "").strip()
            new_token = str(new_target_id or "").strip()
            if not old_token or not new_token or old_token == new_token:
                continue
            rows = database.execute(
                """
                SELECT * FROM image_memory_links
                WHERE status='active' AND target_type=? AND target_id=?
                """,
                (target_type, old_token),
            ).fetchall()
            for row in rows:
                evidence = json.loads(str(row["evidence_json"] or "{}"))
                self.upsert_image_memory_link(
                    occurrence_id=str(row["occurrence_id"]),
                    target_type=target_type,
                    target_id=new_token,
                    link_kind=str(row["link_kind"]),
                    evidence=evidence if isinstance(evidence, dict) else {},
                    conn=database,
                )
                remapped += 1
            self.invalidate_image_memory_links(
                target_type=target_type,
                target_ids=[old_token],
                conn=database,
            )
        return remapped

    def enqueue_image_description_compensation(
        self,
        *,
        occurrence_id: str,
        description_hash: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        database = self._resolve_conn(conn)
        now = _now()
        database.execute(
            """
            INSERT INTO image_description_compensations(
                occurrence_id, description_hash, status, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?)
            ON CONFLICT(occurrence_id, description_hash) DO UPDATE SET
                status=CASE
                    WHEN image_description_compensations.status='done' THEN 'done'
                    ELSE 'pending'
                END,
                lease_token='', lease_until=0, updated_at=excluded.updated_at
            """,
            (occurrence_id, description_hash, now, now),
        )

    def upsert_pending_image_description(
        self,
        *,
        content_hash: str,
        description_hash: str,
        text: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """持久保存先于图片资产到达的模型描述。"""

        database = self._resolve_conn(conn)
        now = _now()
        database.execute(
            """
            INSERT INTO image_pending_descriptions(
                content_hash, description_hash, text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(content_hash, description_hash) DO UPDATE SET
                text=excluded.text,
                updated_at=excluded.updated_at
            """,
            (content_hash, description_hash, text, now, now),
        )

    def list_pending_image_descriptions(self, content_hash: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM image_pending_descriptions
            WHERE content_hash=? ORDER BY created_at ASC
            """,
            (content_hash,),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_pending_image_description(
        self,
        *,
        content_hash: str,
        description_hash: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        database = self._resolve_conn(conn)
        cursor = database.execute(
            "DELETE FROM image_pending_descriptions WHERE content_hash=? AND description_hash=?",
            (content_hash, description_hash),
        )
        return int(cursor.rowcount or 0)

    def claim_image_description_compensations(
        self,
        *,
        lease_token: str,
        lease_seconds: float,
        max_attempts: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        now = _now()
        with self.transaction(immediate=True) as database:
            database.execute(
                """
                UPDATE image_description_compensations
                SET status='pending', lease_token='', lease_until=0, updated_at=?
                WHERE status='running' AND lease_until < ?
                """,
                (now, now),
            )
            rows = database.execute(
                """
                SELECT compensation.*, occurrence.chat_id, occurrence.message_id, occurrence.occurred_at
                FROM image_description_compensations AS compensation
                JOIN image_occurrences AS occurrence
                  ON occurrence.occurrence_id=compensation.occurrence_id
                WHERE compensation.status IN ('pending', 'failed')
                  AND compensation.attempt_count < ?
                  AND occurrence.status='active'
                  AND occurrence.scope_type='chat'
                  AND occurrence.chat_id != '' AND occurrence.message_id != ''
                  AND occurrence.occurred_at IS NOT NULL
                ORDER BY compensation.created_at ASC
                LIMIT ?
                """,
                (max(1, int(max_attempts)), max(1, int(limit))),
            ).fetchall()
            claimed: List[Dict[str, Any]] = []
            for row in rows:
                cursor = database.execute(
                    """
                    UPDATE image_description_compensations
                    SET status='running', lease_token=?, lease_until=?,
                        attempt_count=attempt_count+1, updated_at=?
                    WHERE occurrence_id=? AND description_hash=?
                      AND status IN ('pending', 'failed')
                    """,
                    (
                        lease_token,
                        now + max(1.0, float(lease_seconds)),
                        now,
                        str(row["occurrence_id"]),
                        str(row["description_hash"]),
                    ),
                )
                if int(cursor.rowcount or 0) == 1:
                    claimed.append(dict(row))
        return claimed

    def complete_image_description_compensation(
        self,
        *,
        occurrence_id: str,
        description_hash: str,
        lease_token: str,
        success: bool,
        error: str = "",
    ) -> bool:
        with self.transaction(immediate=True) as database:
            cursor = database.execute(
                """
                UPDATE image_description_compensations
                SET status=?, lease_token='', lease_until=0, last_error=?, updated_at=?
                WHERE occurrence_id=? AND description_hash=? AND lease_token=? AND status='running'
                """,
                (
                    "done" if success else "failed",
                    str(error or "")[:1000],
                    _now(),
                    occurrence_id,
                    description_hash,
                    lease_token,
                ),
            )
        return int(cursor.rowcount or 0) == 1

    def get_image_runtime_state(self) -> Dict[str, Any]:
        row = self._conn.execute("SELECT * FROM image_runtime_state WHERE singleton_id = 1").fetchone()
        return dict(row) if row is not None else {}

    def set_image_runtime_state(
        self,
        *,
        generation: int,
        fingerprint_hash: str,
        dimension: int,
        status: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        database = self._resolve_conn(conn)
        database.execute(
            """
            INSERT INTO image_runtime_state(singleton_id, generation, fingerprint_hash, dimension, status, updated_at)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                generation=excluded.generation,
                fingerprint_hash=excluded.fingerprint_hash,
                dimension=excluded.dimension,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (generation, fingerprint_hash, dimension, status, _now()),
        )

    def get_image_embedding(self, asset_id: str, fingerprint_hash: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM image_embeddings WHERE asset_id=? AND fingerprint_hash=?",
            (asset_id, fingerprint_hash),
        ).fetchone()
        return dict(row) if row is not None else None

    def enqueue_image_embedding_job(
        self,
        *,
        asset_id: str,
        fingerprint_hash: str,
        generation: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        database = self._resolve_conn(conn)
        now = _now()
        database.execute(
            """
            INSERT INTO image_embedding_jobs(
                job_id, asset_id, fingerprint_hash, generation, status, lease_token,
                lease_until, attempt_count, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', '', 0, 0, '', ?, ?)
            ON CONFLICT(asset_id, fingerprint_hash, generation) DO UPDATE SET
                status='pending', lease_token='', lease_until=0, attempt_count=0,
                last_error='', updated_at=excluded.updated_at
            WHERE image_embedding_jobs.status IN ('done', 'aborted')
              AND NOT EXISTS (
                  SELECT 1 FROM image_embeddings e
                  WHERE e.asset_id=excluded.asset_id AND e.fingerprint_hash=excluded.fingerprint_hash
                    AND e.status='ready'
              )
            """,
            (uuid.uuid4().hex, asset_id, fingerprint_hash, generation, now, now),
        )
        row = database.execute(
            "SELECT * FROM image_embedding_jobs WHERE asset_id=? AND fingerprint_hash=? AND generation=?",
            (asset_id, fingerprint_hash, generation),
        ).fetchone()
        if row is None:
            raise RuntimeError("图片嵌入任务写入后无法读取")
        return dict(row)

    def enqueue_unindexed_image_assets(self, *, fingerprint_hash: str, generation: int, limit: int) -> int:
        with self.transaction(immediate=True) as database:
            rows = database.execute(
                """
                SELECT asset.asset_id
                FROM image_assets AS asset
                WHERE asset.status='active'
                  AND EXISTS(
                      SELECT 1 FROM image_occurrences AS occurrence
                      WHERE occurrence.asset_id=asset.asset_id AND occurrence.status='active'
                  )
                  AND NOT EXISTS(
                      SELECT 1 FROM image_embeddings AS embedding
                      WHERE embedding.asset_id=asset.asset_id
                        AND embedding.fingerprint_hash=? AND embedding.status='ready'
                  )
                LIMIT ?
                """,
                (fingerprint_hash, max(1, int(limit))),
            ).fetchall()
            for row in rows:
                self.enqueue_image_embedding_job(
                    asset_id=str(row["asset_id"]),
                    fingerprint_hash=fingerprint_hash,
                    generation=generation,
                    conn=database,
                )
        return len(rows)

    def claim_image_embedding_jobs(
        self,
        *,
        lease_token: str,
        lease_seconds: float,
        max_attempts: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        now = _now()
        lease_until = now + max(1.0, float(lease_seconds))
        claimed: List[Dict[str, Any]] = []
        with self.transaction(immediate=True) as database:
            rows = database.execute(
                """
                SELECT * FROM image_embedding_jobs
                WHERE attempt_count < ?
                  AND (
                    status IN ('pending', 'failed')
                    OR (status='running' AND lease_until <= ?)
                  )
                ORDER BY created_at ASC LIMIT ?
                """,
                (max(1, int(max_attempts)), now, max(1, int(limit))),
            ).fetchall()
            for row in rows:
                cursor = database.execute(
                    """
                    UPDATE image_embedding_jobs
                    SET status='running', lease_token=?, lease_until=?,
                        attempt_count=attempt_count+1, updated_at=?
                    WHERE job_id=? AND (
                        status IN ('pending', 'failed')
                        OR (status='running' AND lease_until <= ?)
                    )
                    """,
                    (lease_token, lease_until, now, row["job_id"], now),
                )
                if int(cursor.rowcount or 0) == 1:
                    loaded = database.execute(
                        "SELECT * FROM image_embedding_jobs WHERE job_id=?",
                        (row["job_id"],),
                    ).fetchone()
                    if loaded is not None:
                        claimed.append(dict(loaded))
        return claimed

    def image_embedding_job_is_publishable(
        self,
        *,
        job_id: str,
        lease_token: str,
        generation: int,
        asset_id: str,
    ) -> bool:
        row = self._conn.execute(
            """
            SELECT 1
            FROM image_embedding_jobs AS job
            JOIN image_assets AS asset ON asset.asset_id=job.asset_id
            WHERE job.job_id=? AND job.lease_token=? AND job.status='running'
              AND job.generation=? AND job.asset_id=? AND asset.status='active'
              AND EXISTS(
                  SELECT 1 FROM image_occurrences AS occurrence
                  WHERE occurrence.asset_id=asset.asset_id AND occurrence.status='active'
              )
            """,
            (job_id, lease_token, generation, asset_id),
        ).fetchone()
        return row is not None

    def publish_image_embedding(
        self,
        *,
        job_id: str,
        lease_token: str,
        asset_id: str,
        fingerprint_hash: str,
        vector_id: str,
        generation: int,
        dimension: int,
    ) -> bool:
        with self.transaction(immediate=True) as database:
            row = database.execute(
                """
                SELECT 1 FROM image_embedding_jobs AS job
                JOIN image_assets AS asset ON asset.asset_id=job.asset_id
                WHERE job.job_id=? AND job.lease_token=? AND job.status='running'
                  AND job.asset_id=? AND job.fingerprint_hash=? AND job.generation=?
                  AND asset.status='active'
                  AND EXISTS(
                      SELECT 1 FROM image_occurrences AS occurrence
                      WHERE occurrence.asset_id=asset.asset_id AND occurrence.status='active'
                  )
                """,
                (job_id, lease_token, asset_id, fingerprint_hash, generation),
            ).fetchone()
            if row is None:
                return False
            now = _now()
            database.execute(
                """
                INSERT INTO image_embeddings(
                    asset_id, fingerprint_hash, vector_id, generation, dimension, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'ready', ?)
                ON CONFLICT(asset_id, fingerprint_hash) DO UPDATE SET
                    vector_id=excluded.vector_id,
                    generation=excluded.generation,
                    dimension=excluded.dimension,
                    status='ready',
                    updated_at=excluded.updated_at
                """,
                (asset_id, fingerprint_hash, vector_id, generation, dimension, now),
            )
            cursor = database.execute(
                """
                UPDATE image_embedding_jobs
                SET status='done', lease_token='', lease_until=0, last_error='', updated_at=?
                WHERE job_id=? AND lease_token=? AND status='running'
                """,
                (now, job_id, lease_token),
            )
            return int(cursor.rowcount or 0) == 1

    def record_imported_image_embedding(
        self,
        *,
        asset_id: str,
        fingerprint_hash: str,
        vector_id: str,
        generation: int,
        dimension: int,
        conn: sqlite3.Connection,
    ) -> None:
        """在向量已写入内存索引后登记包内导入的可信向量。"""

        active = conn.execute(
            """
            SELECT 1 FROM image_assets AS asset
            WHERE asset.asset_id=? AND asset.status='active'
              AND EXISTS(
                  SELECT 1 FROM image_occurrences AS occurrence
                  WHERE occurrence.asset_id=asset.asset_id AND occurrence.status='active'
              )
            """,
            (asset_id,),
        ).fetchone()
        if active is None:
            raise ValueError("图片向量引用了未激活或无出现记录的资产")
        now = _now()
        conn.execute(
            """
            INSERT INTO image_embeddings(
                asset_id, fingerprint_hash, vector_id, generation, dimension, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'ready', ?)
            ON CONFLICT(asset_id, fingerprint_hash) DO UPDATE SET
                vector_id=excluded.vector_id,
                generation=excluded.generation,
                dimension=excluded.dimension,
                status='ready',
                updated_at=excluded.updated_at
            """,
            (asset_id, fingerprint_hash, vector_id, generation, dimension, now),
        )
        conn.execute(
            """
            UPDATE image_embedding_jobs
            SET status='done', lease_token='', lease_until=0, last_error='', updated_at=?
            WHERE asset_id=? AND fingerprint_hash=? AND generation=?
              AND status IN ('pending', 'failed', 'running')
            """,
            (now, asset_id, fingerprint_hash, generation),
        )

    def fail_image_embedding_job(self, *, job_id: str, lease_token: str, error: str) -> bool:
        with self.transaction(immediate=True) as database:
            cursor = database.execute(
                """
                UPDATE image_embedding_jobs
                SET status='failed', lease_token='', lease_until=0, last_error=?, updated_at=?
                WHERE job_id=? AND lease_token=? AND status='running'
                """,
                (str(error)[:1000], _now(), job_id, lease_token),
            )
        return int(cursor.rowcount or 0) == 1

    def deactivate_image_occurrence(self, occurrence_id: str, *, conn: Optional[sqlite3.Connection] = None) -> int:
        database = self._resolve_conn(conn)
        cursor = database.execute(
            "UPDATE image_occurrences SET status='deleted', updated_at=? WHERE occurrence_id=? AND status='active'",
            (_now(), occurrence_id),
        )
        database.execute(
            """
            UPDATE image_description_compensations
            SET status='aborted', lease_token='', lease_until=0, updated_at=?
            WHERE occurrence_id=? AND status IN ('pending', 'failed', 'running')
            """,
            (_now(), occurrence_id),
        )
        return int(cursor.rowcount or 0)

    def deactivate_installation_image_occurrences(
        self,
        installation_id: str,
        *,
        conn: sqlite3.Connection,
    ) -> List[str]:
        rows = conn.execute(
            "SELECT occurrence_id, asset_id FROM image_occurrences WHERE installation_id=? AND status='active'",
            (installation_id,),
        ).fetchall()
        for row in rows:
            self.deactivate_image_occurrence(str(row["occurrence_id"]), conn=conn)
        return list(dict.fromkeys(str(row["asset_id"]) for row in rows))

    def abort_image_jobs_if_unreferenced(self, asset_id: str, *, conn: sqlite3.Connection) -> bool:
        reference = conn.execute(
            "SELECT 1 FROM image_occurrences WHERE asset_id=? AND status='active' LIMIT 1",
            (asset_id,),
        ).fetchone()
        if reference is not None:
            return False
        conn.execute(
            """
            UPDATE image_embedding_jobs
            SET status='aborted', lease_token='', lease_until=0,
                last_error='asset unreferenced', updated_at=?
            WHERE asset_id=? AND status IN ('pending', 'failed', 'running')
            """,
            (_now(), asset_id),
        )
        conn.execute("UPDATE image_assets SET status='deleted', updated_at=? WHERE asset_id=?", (_now(), asset_id))
        # 文件与向量即将释放，必须同时失效索引凭证，避免同图重新出现时误判为已索引。
        conn.execute(
            "UPDATE image_embeddings SET status='deleted', updated_at=? WHERE asset_id=?",
            (_now(), asset_id),
        )
        return True

    def list_image_assets(self, *, limit: int, offset: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT asset.*, COUNT(occurrence.occurrence_id) AS occurrence_count,
                   MAX(COALESCE(occurrence.occurred_at, occurrence.created_at)) AS latest_occurrence_at
            FROM image_assets AS asset
            LEFT JOIN image_occurrences AS occurrence
              ON occurrence.asset_id=asset.asset_id AND occurrence.status='active'
            WHERE asset.status='active'
            GROUP BY asset.asset_id
            ORDER BY latest_occurrence_at DESC, asset.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
        return [dict(row) for row in rows]

    def image_memory_stats(self) -> Dict[str, int]:
        def count(sql: str) -> int:
            row = self._conn.execute(sql).fetchone()
            return int(row[0] if row else 0)

        return {
            "storage_bytes": count("SELECT COALESCE(SUM(byte_size),0) FROM image_assets WHERE status='active'"),
            "asset_count": count("SELECT COUNT(*) FROM image_assets WHERE status='active'"),
            "occurrence_count": count("SELECT COUNT(*) FROM image_occurrences WHERE status='active'"),
            "observation_count": count(
                "SELECT COUNT(*) FROM image_observations o JOIN image_occurrences c "
                "ON c.occurrence_id=o.occurrence_id WHERE o.superseded_by='' AND c.status='active'"
            ),
            "link_count": count(
                "SELECT COUNT(*) FROM image_memory_links l JOIN image_occurrences c "
                "ON c.occurrence_id=l.occurrence_id WHERE l.status='active' AND c.status='active'"
            ),
            "ready_vector_count": count(
                "SELECT COUNT(*) FROM image_embeddings e JOIN image_assets a ON a.asset_id=e.asset_id "
                "WHERE e.status='ready' AND a.status='active'"
            ),
            "pending_job_count": count("SELECT COUNT(*) FROM image_embedding_jobs WHERE status IN ('pending','running')"),
            "failed_job_count": count("SELECT COUNT(*) FROM image_embedding_jobs WHERE status='failed'"),
            "pending_description_count": count(
                "SELECT COUNT(*) FROM image_description_compensations WHERE status IN ('pending','running')"
            ),
            "failed_description_count": count(
                "SELECT COUNT(*) FROM image_description_compensations WHERE status='failed'"
            ),
            "pending_unbound_description_count": count(
                "SELECT COUNT(*) FROM image_pending_descriptions"
            ),
        }

    def image_index_progress(self, fingerprint: str) -> Dict[str, int]:
        total = self._conn.execute("SELECT COUNT(*) FROM image_assets WHERE status='active'").fetchone()[0]
        ready = self._conn.execute(
            "SELECT COUNT(*) FROM image_embeddings e JOIN image_assets a ON a.asset_id=e.asset_id "
            "WHERE a.status='active' AND e.status='ready' AND e.fingerprint_hash=?", (fingerprint,),
        ).fetchone()[0]
        return {"total": int(total), "ready": int(ready), "remaining": int(total - ready)}

    def list_image_jobs(self, *, limit: int, offset: int, status: str = "") -> Dict[str, Any]:
        """统一展示嵌入与描述补偿任务，保留逐项错误、尝试次数和租约状态。"""
        query = (
            "SELECT 'embedding' AS kind, job_id AS id, asset_id, status, attempt_count, last_error, "
            "updated_at, lease_until FROM image_embedding_jobs UNION ALL "
            "SELECT 'description' AS kind, occurrence_id AS id, '' AS asset_id, status, attempt_count, "
            "last_error, updated_at, lease_until FROM image_description_compensations"
        )
        condition = " WHERE status=?" if status else ""
        params = (status,) if status else ()
        total = self._conn.execute(f"SELECT COUNT(*) FROM ({query}){condition}", params).fetchone()[0]
        rows = self._conn.execute(
            f"SELECT * FROM ({query}){condition} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return {"success": True, "items": [dict(row) for row in rows], "total": int(total)}

    def export_image_records(self, occurrence_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
        tokens = list(dict.fromkeys(str(item) for item in occurrence_ids if str(item)))
        if not tokens:
            return {"assets": [], "occurrences": [], "observations": [], "links": []}
        placeholders = ",".join("?" for _ in tokens)
        occurrences = [
            dict(row)
            for row in self._conn.execute(
                f"SELECT * FROM image_occurrences WHERE status='active' AND occurrence_id IN ({placeholders})",
                tuple(tokens),
            ).fetchall()
        ]
        active_ids = [str(row["occurrence_id"]) for row in occurrences]
        asset_ids = list(dict.fromkeys(str(row["asset_id"]) for row in occurrences))
        asset_placeholders = ",".join("?" for _ in asset_ids)
        assets = [
            dict(row)
            for row in self._conn.execute(
                f"SELECT * FROM image_assets WHERE status='active' AND asset_id IN ({asset_placeholders})",
                tuple(asset_ids),
            ).fetchall()
        ] if asset_ids else []
        return {
            "assets": assets,
            "occurrences": occurrences,
            "observations": self.list_image_observations(active_ids),
            "links": self.list_image_memory_links(active_ids),
        }
