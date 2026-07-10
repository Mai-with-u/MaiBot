from datetime import datetime
from typing import Any, Dict, List, Optional

import json
import sqlite3

from .value_coercion import optional_float


class MetadataProfileMixin:
    """维护人物画像开关、快照、覆盖项与刷新队列。"""

    def set_person_profile_switch(
        self,
        stream_id: str,
        user_id: str,
        enabled: bool,
        updated_at: Optional[float] = None,
    ) -> None:
        """设置人物画像自动注入开关（按 stream_id + user_id）。"""
        if not stream_id or not user_id:
            raise ValueError("stream_id 和 user_id 不能为空")

        ts = float(updated_at) if updated_at is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO person_profile_switches (stream_id, user_id, enabled, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stream_id, user_id) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (str(stream_id), str(user_id), 1 if enabled else 0, ts),
        )
        self._conn.commit()

    def get_person_profile_switch(self, stream_id: str, user_id: str, default: bool = False) -> bool:
        """读取人物画像自动注入开关。"""
        if not stream_id or not user_id:
            return bool(default)

        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT enabled FROM person_profile_switches WHERE stream_id = ? AND user_id = ?",
            (str(stream_id), str(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return bool(default)
        return bool(row[0])

    def get_enabled_person_profile_switches(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取已开启人物画像注入开关的会话范围。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT stream_id, user_id, enabled, updated_at
            FROM person_profile_switches
            WHERE enabled = 1
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (int(max(1, limit)),),
        )
        return [
            {
                "stream_id": row[0],
                "user_id": row[1],
                "enabled": bool(row[2]),
                "updated_at": row[3],
            }
            for row in cursor.fetchall()
        ]

    def mark_person_profile_active(
        self,
        stream_id: str,
        user_id: str,
        person_id: str,
        seen_at: Optional[float] = None,
    ) -> None:
        """记录活跃人物（用于定时按需刷新）。"""
        if not stream_id or not user_id or not person_id:
            return
        ts = float(seen_at) if seen_at is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO person_profile_active_persons (stream_id, user_id, person_id, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stream_id, user_id, person_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at
            """,
            (str(stream_id), str(user_id), str(person_id), ts),
        )
        self._conn.commit()

    def get_active_person_ids_for_enabled_switches(
        self,
        active_after: Optional[float] = None,
        limit: int = 200,
    ) -> List[str]:
        """获取“已开启开关范围内”的活跃人物集合。"""
        cursor = self._conn.cursor()
        sql = """
            SELECT a.person_id, MAX(a.last_seen_at) AS last_seen
            FROM person_profile_active_persons a
            JOIN person_profile_switches s
              ON a.stream_id = s.stream_id AND a.user_id = s.user_id
            WHERE s.enabled = 1
        """
        params: List[Any] = []
        if active_after is not None:
            sql += " AND a.last_seen_at >= ?"
            params.append(float(active_after))
        sql += """
            GROUP BY a.person_id
            ORDER BY last_seen DESC
            LIMIT ?
        """
        params.append(int(max(1, limit)))
        cursor.execute(sql, tuple(params))
        return [str(row[0]) for row in cursor.fetchall() if row and row[0]]

    def get_latest_person_profile_snapshot(self, person_id: str) -> Optional[Dict[str, Any]]:
        """获取人物最新画像快照。"""
        if not person_id:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT
                snapshot_id, person_id, profile_version, profile_text,
                aliases_json, relation_edges_json, vector_evidence_json, evidence_ids_json,
                updated_at, expires_at, source_note
            FROM person_profile_snapshots
            WHERE person_id = ?
            ORDER BY profile_version DESC
            LIMIT 1
            """,
            (str(person_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None

        def _load_list(raw: Any) -> List[Any]:
            if not raw:
                return []
            try:
                data = json.loads(raw)
                return data if isinstance(data, list) else []
            except Exception:
                return []

        return {
            "snapshot_id": row[0],
            "person_id": row[1],
            "profile_version": int(row[2]),
            "profile_text": row[3] or "",
            "aliases": _load_list(row[4]),
            "relation_edges": _load_list(row[5]),
            "vector_evidence": _load_list(row[6]),
            "evidence_ids": _load_list(row[7]),
            "updated_at": row[8],
            "expires_at": row[9],
            "source_note": row[10] or "",
        }

    def upsert_person_profile_snapshot(
        self,
        person_id: str,
        profile_text: str,
        aliases: Optional[List[str]] = None,
        relation_edges: Optional[List[Dict[str, Any]]] = None,
        vector_evidence: Optional[List[Dict[str, Any]]] = None,
        evidence_ids: Optional[List[str]] = None,
        expires_at: Optional[float] = None,
        source_note: str = "",
        updated_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """写入人物画像快照（按 person_id 自动递增版本）。"""
        if not person_id:
            raise ValueError("person_id 不能为空")

        aliases = aliases or []
        relation_edges = relation_edges or []
        vector_evidence = vector_evidence or []
        evidence_ids = evidence_ids or []
        ts = float(updated_at) if updated_at is not None else datetime.now().timestamp()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT profile_version
            FROM person_profile_snapshots
            WHERE person_id = ?
            ORDER BY profile_version DESC
            LIMIT 1
            """,
            (str(person_id),),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1

        cursor.execute(
            """
            INSERT INTO person_profile_snapshots (
                person_id, profile_version, profile_text,
                aliases_json, relation_edges_json, vector_evidence_json, evidence_ids_json,
                updated_at, expires_at, source_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(person_id),
                next_version,
                str(profile_text or ""),
                json.dumps(aliases, ensure_ascii=False),
                json.dumps(relation_edges, ensure_ascii=False),
                json.dumps(vector_evidence, ensure_ascii=False),
                json.dumps(evidence_ids, ensure_ascii=False),
                ts,
                float(expires_at) if expires_at is not None else None,
                str(source_note or ""),
            ),
        )
        self._conn.commit()
        latest = self.get_latest_person_profile_snapshot(person_id)
        return latest or {
            "person_id": person_id,
            "profile_version": next_version,
            "profile_text": str(profile_text or ""),
            "aliases": aliases,
            "relation_edges": relation_edges,
            "vector_evidence": vector_evidence,
            "evidence_ids": evidence_ids,
            "updated_at": ts,
            "expires_at": expires_at,
            "source_note": source_note,
        }

    def get_person_profile_override(self, person_id: str) -> Optional[Dict[str, Any]]:
        """获取人物画像手工覆盖内容。"""
        if not person_id:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT person_id, override_text, updated_at, updated_by, source
            FROM person_profile_overrides
            WHERE person_id = ?
            LIMIT 1
            """,
            (str(person_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "person_id": str(row[0]),
            "override_text": str(row[1] or ""),
            "updated_at": row[2],
            "updated_by": str(row[3] or ""),
            "source": str(row[4] or ""),
        }

    def set_person_profile_override(
        self,
        person_id: str,
        override_text: str,
        updated_by: str = "",
        source: str = "webui",
        updated_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """写入人物画像手工覆盖；空文本等价于清除覆盖。"""
        if not person_id:
            raise ValueError("person_id 不能为空")

        text = str(override_text or "").strip()
        if not text:
            self.delete_person_profile_override(person_id)
            return {
                "person_id": str(person_id),
                "override_text": "",
                "updated_at": None,
                "updated_by": str(updated_by or ""),
                "source": str(source or ""),
            }

        ts = float(updated_at) if updated_at is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO person_profile_overrides (
                person_id, override_text, updated_at, updated_by, source
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                override_text = excluded.override_text,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by,
                source = excluded.source
            """,
            (
                str(person_id),
                text,
                ts,
                str(updated_by or ""),
                str(source or ""),
            ),
        )
        self._conn.commit()
        return self.get_person_profile_override(person_id) or {
            "person_id": str(person_id),
            "override_text": text,
            "updated_at": ts,
            "updated_by": str(updated_by or ""),
            "source": str(source or ""),
        }

    def delete_person_profile_override(self, person_id: str) -> bool:
        """删除人物画像手工覆盖。"""
        if not person_id:
            return False
        cursor = self._conn.cursor()
        cursor.execute(
            "DELETE FROM person_profile_overrides WHERE person_id = ?",
            (str(person_id),),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _person_profile_refresh_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        payload = dict(row)
        payload["person_id"] = str(payload.get("person_id", "") or "").strip()
        payload["status"] = str(payload.get("status", "") or "").strip().lower() or "pending"
        payload["reason"] = str(payload.get("reason", "") or "").strip()
        payload["source_query_tool_id"] = str(payload.get("source_query_tool_id", "") or "").strip()
        payload["retry_count"] = int(payload.get("retry_count", 0) or 0)
        payload["last_error"] = str(payload.get("last_error", "") or "").strip()
        payload["requested_at"] = optional_float(payload.get("requested_at"))
        payload["updated_at"] = optional_float(payload.get("updated_at"))
        return payload

    def get_person_profile_refresh_request(self, person_id: str) -> Optional[Dict[str, Any]]:
        token = str(person_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT person_id, status, reason, source_query_tool_id, retry_count, last_error, requested_at, updated_at
            FROM person_profile_refresh_queue
            WHERE person_id = ?
            LIMIT 1
            """,
            (token,),
        )
        return self._person_profile_refresh_row_to_dict(cursor.fetchone())

    def enqueue_person_profile_refresh(
        self,
        *,
        person_id: str,
        reason: str = "",
        source_query_tool_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        token = str(person_id or "").strip()
        if not token:
            return None

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO person_profile_refresh_queue (
                person_id, status, reason, source_query_tool_id, retry_count, last_error, requested_at, updated_at
            ) VALUES (?, 'pending', ?, ?, 0, NULL, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                status = 'pending',
                reason = excluded.reason,
                source_query_tool_id = excluded.source_query_tool_id,
                retry_count = 0,
                last_error = NULL,
                requested_at = excluded.requested_at,
                updated_at = excluded.updated_at
            """,
            (
                token,
                str(reason or "").strip() or None,
                str(source_query_tool_id or "").strip() or None,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_person_profile_refresh_request(token)

    def fetch_person_profile_refresh_batch(
        self,
        *,
        limit: int = 20,
        max_retry: int = 3,
        debounce_seconds: float = 0.0,
        retry_backoff_seconds: float = 0.0,
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(limit))
        safe_retry = max(0, int(max_retry))
        now = datetime.now().timestamp()
        pending_ready_before = now - max(0.0, float(debounce_seconds or 0.0))
        failed_ready_before = now - max(0.0, float(retry_backoff_seconds or 0.0))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT person_id, status, reason, source_query_tool_id, retry_count, last_error, requested_at, updated_at
            FROM person_profile_refresh_queue
            WHERE (status = 'pending' AND requested_at <= ?)
               OR (status = 'failed' AND retry_count < ? AND updated_at <= ?)
            ORDER BY requested_at ASC, updated_at ASC
            LIMIT ?
            """,
            (pending_ready_before, safe_retry, failed_ready_before, safe_limit),
        )
        return [
            item
            for item in (
                self._person_profile_refresh_row_to_dict(row)
                for row in cursor.fetchall()
            )
            if item is not None
        ]

    def mark_person_profile_refresh_running(
        self,
        person_id: str,
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        token = str(person_id or "").strip()
        if not token:
            return False

        now = datetime.now().timestamp()
        params: List[Any] = [now, token]
        sql = """
            UPDATE person_profile_refresh_queue
            SET status = 'running',
                updated_at = ?
            WHERE person_id = ?
              AND status IN ('pending', 'failed')
        """
        if requested_at is not None:
            sql += " AND requested_at = ?"
            params.append(float(requested_at))
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_person_profile_refresh_done(
        self,
        person_id: str,
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        token = str(person_id or "").strip()
        if not token:
            return False

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        if requested_at is None:
            cursor.execute(
                """
                UPDATE person_profile_refresh_queue
                SET status = 'done',
                    last_error = NULL,
                    updated_at = ?
                WHERE person_id = ?
                """,
                (now, token),
            )
        else:
            req_ts = float(requested_at)
            cursor.execute(
                """
                UPDATE person_profile_refresh_queue
                SET status = CASE
                        WHEN requested_at > ? THEN 'pending'
                        ELSE 'done'
                    END,
                    last_error = NULL,
                    updated_at = ?
                WHERE person_id = ?
                """,
                (req_ts, now, token),
            )
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_person_profile_refresh_failed(
        self,
        person_id: str,
        error: str = "",
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        token = str(person_id or "").strip()
        if not token:
            return False

        err_text = str(error or "").strip()[:500]
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        if requested_at is None:
            cursor.execute(
                """
                UPDATE person_profile_refresh_queue
                SET status = 'failed',
                    retry_count = COALESCE(retry_count, 0) + 1,
                    last_error = ?,
                    updated_at = ?
                WHERE person_id = ?
                """,
                (err_text, now, token),
            )
        else:
            req_ts = float(requested_at)
            cursor.execute(
                """
                UPDATE person_profile_refresh_queue
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
                WHERE person_id = ?
                """,
                (req_ts, req_ts, req_ts, err_text, now, token),
            )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_person_profile_refresh_requests(
        self,
        *,
        statuses: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
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
            SELECT person_id, status, reason, source_query_tool_id, retry_count, last_error, requested_at, updated_at
            FROM person_profile_refresh_queue
            {where_sql}
            ORDER BY updated_at DESC, person_id ASC
            LIMIT ?
            """,
            tuple(params),
        )
        return [
            item
            for item in (
                self._person_profile_refresh_row_to_dict(row)
                for row in cursor.fetchall()
            )
            if item is not None
        ]

    def get_person_profile_refresh_summary(self, failed_limit: int = 20) -> Dict[str, Any]:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM person_profile_refresh_queue
            GROUP BY status
            """
        )
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "total": 0}
        for row in cursor.fetchall():
            status = str(row["status"] or "").strip().lower()
            cnt = int(row["cnt"] or 0)
            counts[status] = counts.get(status, 0) + cnt
            counts["total"] += cnt
        running = self.list_person_profile_refresh_requests(statuses=["running"], limit=20)
        failed = self.list_person_profile_refresh_requests(
            statuses=["failed"],
            limit=max(1, int(failed_limit)),
        )
        return {
            "counts": counts,
            "running": running,
            "failed": failed,
        }
