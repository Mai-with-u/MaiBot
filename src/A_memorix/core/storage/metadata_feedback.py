from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import sqlite3

from .value_coercion import optional_float


class MetadataFeedbackMixin:
    """维护反馈任务、回滚日志与陈旧关系标记。"""

    def _feedback_task_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["query_snapshot"] = self._json_loads(data.pop("query_snapshot_json", None), {})
        data["decision_payload"] = self._json_loads(data.get("decision_json"), {})
        data["rollback_status"] = str(data.get("rollback_status", "") or "none").strip().lower() or "none"
        data["rollback_plan"] = self._json_loads(data.pop("rollback_plan_json", None), {})
        data["rollback_result"] = self._json_loads(data.pop("rollback_result_json", None), {})
        data["rollback_error"] = str(data.get("rollback_error", "") or "").strip()
        data["rollback_requested_by"] = str(data.get("rollback_requested_by", "") or "").strip()
        data["rollback_reason"] = str(data.get("rollback_reason", "") or "").strip()
        return data

    def _feedback_action_log_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["id"] = int(data.get("id", 0) or 0)
        data["task_id"] = int(data.get("task_id", 0) or 0)
        data["query_tool_id"] = str(data.get("query_tool_id", "") or "").strip()
        data["action_type"] = str(data.get("action_type", "") or "").strip()
        data["target_hash"] = str(data.get("target_hash", "") or "").strip()
        data["reason"] = str(data.get("reason", "") or "").strip()
        data["before_payload"] = self._json_loads(data.pop("before_json", None), {})
        data["after_payload"] = self._json_loads(data.pop("after_json", None), {})
        return data

    def get_feedback_task(self, query_tool_id: str) -> Optional[Dict[str, Any]]:
        token = str(query_tool_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM memory_feedback_tasks
            WHERE query_tool_id = ?
            LIMIT 1
            """,
            (token,),
        )
        row = cursor.fetchone()
        return self._feedback_task_row_to_dict(row) if row is not None else None

    def get_feedback_task_by_id(self, task_id: int) -> Optional[Dict[str, Any]]:
        if int(task_id or 0) <= 0:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM memory_feedback_tasks
            WHERE id = ?
            LIMIT 1
            """,
            (int(task_id),),
        )
        row = cursor.fetchone()
        return self._feedback_task_row_to_dict(row) if row is not None else None

    def list_feedback_tasks(
        self,
        *,
        limit: int = 50,
        statuses: Optional[List[str]] = None,
        rollback_statuses: Optional[List[str]] = None,
        query: str = "",
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(limit or 50))
        params: List[Any] = []
        conditions: List[str] = []

        normalized_statuses = [
            str(item or "").strip().lower()
            for item in (statuses or [])
            if str(item or "").strip().lower() in {"pending", "running", "applied", "skipped", "error"}
        ]
        if normalized_statuses:
            placeholders = ",".join(["?"] * len(normalized_statuses))
            conditions.append(f"LOWER(COALESCE(status, '')) IN ({placeholders})")
            params.extend(normalized_statuses)

        normalized_rollback_statuses = [
            str(item or "").strip().lower()
            for item in (rollback_statuses or [])
            if str(item or "").strip().lower() in {"none", "running", "rolled_back", "error"}
        ]
        if normalized_rollback_statuses:
            placeholders = ",".join(["?"] * len(normalized_rollback_statuses))
            conditions.append(f"LOWER(COALESCE(rollback_status, 'none')) IN ({placeholders})")
            params.extend(normalized_rollback_statuses)

        query_token = str(query or "").strip().lower()
        if query_token:
            like_value = f"%{query_token}%"
            conditions.append(
                """
                (
                    LOWER(COALESCE(query_tool_id, '')) LIKE ?
                    OR LOWER(COALESCE(session_id, '')) LIKE ?
                    OR LOWER(COALESCE(query_snapshot_json, '')) LIKE ?
                    OR LOWER(COALESCE(decision_json, '')) LIKE ?
                    OR LOWER(COALESCE(last_error, '')) LIKE ?
                    OR LOWER(COALESCE(rollback_reason, '')) LIKE ?
                    OR LOWER(COALESCE(rollback_error, '')) LIKE ?
                )
                """
            )
            params.extend([like_value] * 7)

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(safe_limit)
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM memory_feedback_tasks
            {where_sql}
            ORDER BY query_timestamp DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._feedback_task_row_to_dict(row) for row in cursor.fetchall()]

    def enqueue_feedback_task(
        self,
        *,
        query_tool_id: str,
        session_id: str,
        query_timestamp: float,
        due_at: float,
        query_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        tool_token = str(query_tool_id or "").strip()
        session_token = str(session_id or "").strip()
        if not tool_token or not session_token:
            return None

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO memory_feedback_tasks (
                query_tool_id, session_id, query_timestamp, due_at, status, attempt_count,
                query_snapshot_json, decision_json, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, ?, ?)
            """,
            (
                tool_token,
                session_token,
                float(query_timestamp),
                float(due_at),
                self._json_dumps(query_snapshot or {}),
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_feedback_task(tool_token)

    def update_feedback_task_rollback_plan(
        self,
        *,
        task_id: int,
        rollback_plan: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if int(task_id or 0) <= 0:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE memory_feedback_tasks
            SET rollback_plan_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                self._json_dumps(rollback_plan or {}),
                datetime.now().timestamp(),
                int(task_id),
            ),
        )
        self._conn.commit()
        return self.get_feedback_task_by_id(int(task_id))

    def fetch_due_feedback_tasks(
        self,
        *,
        limit: int = 20,
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(limit))
        now_ts = optional_float(now)
        if now_ts is None:
            now_ts = datetime.now().timestamp()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM memory_feedback_tasks
            WHERE due_at <= ?
              AND status IN ('pending', 'running')
            ORDER BY due_at ASC, id ASC
            LIMIT ?
            """,
            (now_ts, safe_limit),
        )
        return [self._feedback_task_row_to_dict(row) for row in cursor.fetchall()]

    def mark_feedback_task_running(self, task_id: int) -> Optional[Dict[str, Any]]:
        if int(task_id or 0) <= 0:
            return None
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE memory_feedback_tasks
            SET status = 'running',
                attempt_count = COALESCE(attempt_count, 0) + 1,
                updated_at = ?
            WHERE id = ?
              AND status IN ('pending', 'running')
            """,
            (now, int(task_id)),
        )
        self._conn.commit()
        cursor.execute(
            """
            SELECT *
            FROM memory_feedback_tasks
            WHERE id = ?
            LIMIT 1
            """,
            (int(task_id),),
        )
        row = cursor.fetchone()
        return self._feedback_task_row_to_dict(row) if row is not None else None

    def finalize_feedback_task(
        self,
        *,
        task_id: int,
        status: str,
        decision_payload: Optional[Dict[str, Any]] = None,
        last_error: str = "",
    ) -> Optional[Dict[str, Any]]:
        final_status = str(status or "").strip().lower()
        if final_status not in {"applied", "skipped", "error"}:
            raise ValueError(f"不支持的反馈任务结束状态: {status}")
        if int(task_id or 0) <= 0:
            return None

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE memory_feedback_tasks
            SET status = ?,
                decision_json = ?,
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                final_status,
                self._json_dumps(decision_payload or {}),
                str(last_error or "").strip() or None,
                now,
                int(task_id),
            ),
        )
        self._conn.commit()
        cursor.execute(
            """
            SELECT *
            FROM memory_feedback_tasks
            WHERE id = ?
            LIMIT 1
            """,
            (int(task_id),),
        )
        row = cursor.fetchone()
        return self._feedback_task_row_to_dict(row) if row is not None else None

    def mark_feedback_task_rollback_running(
        self,
        *,
        task_id: int,
        requested_by: str = "",
        reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        if int(task_id or 0) <= 0:
            return None
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE memory_feedback_tasks
            SET rollback_status = 'running',
                rollback_requested_by = ?,
                rollback_reason = ?,
                rollback_error = NULL,
                rollback_requested_at = ?,
                updated_at = ?
            WHERE id = ?
              AND LOWER(COALESCE(status, '')) = 'applied'
              AND LOWER(COALESCE(rollback_status, 'none')) IN ('none', 'error')
            """,
            (
                str(requested_by or "").strip() or None,
                str(reason or "").strip() or None,
                now,
                now,
                int(task_id),
            ),
        )
        self._conn.commit()
        if int(cursor.rowcount or 0) <= 0:
            return None
        return self.get_feedback_task_by_id(int(task_id))

    def finalize_feedback_task_rollback(
        self,
        *,
        task_id: int,
        rollback_status: str,
        rollback_result: Optional[Dict[str, Any]] = None,
        rollback_error: str = "",
    ) -> Optional[Dict[str, Any]]:
        if int(task_id or 0) <= 0:
            return None
        final_status = str(rollback_status or "").strip().lower()
        if final_status not in {"none", "rolled_back", "error"}:
            raise ValueError(f"不支持的反馈任务回退状态: {rollback_status}")
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE memory_feedback_tasks
            SET rollback_status = ?,
                rollback_result_json = ?,
                rollback_error = ?,
                rolled_back_at = CASE WHEN ? = 'rolled_back' THEN ? ELSE rolled_back_at END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                final_status,
                self._json_dumps(rollback_result or {}),
                str(rollback_error or "").strip() or None,
                final_status,
                now,
                now,
                int(task_id),
            ),
        )
        self._conn.commit()
        return self.get_feedback_task_by_id(int(task_id))

    def append_feedback_action_log(
        self,
        *,
        task_id: int,
        query_tool_id: str,
        action_type: str,
        target_hash: str = "",
        before_payload: Optional[Dict[str, Any]] = None,
        after_payload: Optional[Dict[str, Any]] = None,
        reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        if int(task_id or 0) <= 0:
            return None
        query_token = str(query_tool_id or "").strip()
        if not query_token:
            return None

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_feedback_action_logs (
                task_id, query_tool_id, action_type, target_hash,
                before_json, after_json, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(task_id),
                query_token,
                str(action_type or "").strip() or "unknown",
                str(target_hash or "").strip() or None,
                self._json_dumps(before_payload) if isinstance(before_payload, dict) else None,
                self._json_dumps(after_payload) if isinstance(after_payload, dict) else None,
                str(reason or "").strip() or None,
                now,
            ),
        )
        self._conn.commit()
        return {
            "id": int(cursor.lastrowid or 0),
            "task_id": int(task_id),
            "query_tool_id": query_token,
            "action_type": str(action_type or "").strip() or "unknown",
            "target_hash": str(target_hash or "").strip(),
            "before_json": self._json_dumps(before_payload) if isinstance(before_payload, dict) else None,
            "after_json": self._json_dumps(after_payload) if isinstance(after_payload, dict) else None,
            "reason": str(reason or "").strip(),
            "created_at": now,
        }

    def list_feedback_action_logs(self, task_id: int) -> List[Dict[str, Any]]:
        if int(task_id or 0) <= 0:
            return []
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, task_id, query_tool_id, action_type, target_hash, before_json, after_json, reason, created_at
            FROM memory_feedback_action_logs
            WHERE task_id = ?
            ORDER BY id ASC
            """,
            (int(task_id),),
        )
        return [self._feedback_action_log_row_to_dict(row) for row in cursor.fetchall()]

    def upsert_paragraph_stale_relation_mark(
        self,
        *,
        paragraph_hash: str,
        relation_hash: str,
        query_tool_id: str = "",
        task_id: Optional[int] = None,
        reason: str = "",
        source_type: str = "",
        source_id: str = "",
        source_operation_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        paragraph_token = str(paragraph_hash or "").strip()
        relation_token = str(relation_hash or "").strip()
        if not paragraph_token or not relation_token:
            return None

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO paragraph_stale_relation_marks (
                paragraph_hash, relation_hash, query_tool_id, task_id, reason,
                source_type, source_id, source_operation_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paragraph_hash, relation_hash) DO UPDATE SET
                query_tool_id = excluded.query_tool_id,
                task_id = excluded.task_id,
                reason = excluded.reason,
                source_type = excluded.source_type,
                source_id = excluded.source_id,
                source_operation_id = excluded.source_operation_id,
                updated_at = excluded.updated_at
            """,
            (
                paragraph_token,
                relation_token,
                str(query_tool_id or "").strip() or None,
                int(task_id) if int(task_id or 0) > 0 else None,
                str(reason or "").strip() or None,
                str(source_type or "").strip() or None,
                str(source_id or "").strip() or None,
                str(source_operation_id or "").strip() or None,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_token,
            relation_hash=relation_token,
        )

    def get_paragraph_stale_relation_mark(
        self,
        *,
        paragraph_hash: str,
        relation_hash: str,
    ) -> Optional[Dict[str, Any]]:
        paragraph_token = str(paragraph_hash or "").strip()
        relation_token = str(relation_hash or "").strip()
        if not paragraph_token or not relation_token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT paragraph_hash, relation_hash, query_tool_id, task_id, reason,
                   source_type, source_id, source_operation_id, created_at, updated_at
            FROM paragraph_stale_relation_marks
            WHERE paragraph_hash = ? AND relation_hash = ?
            """,
            (paragraph_token, relation_token),
        )
        row = cursor.fetchone()
        return self._paragraph_stale_relation_mark_row_to_dict(row)

    def get_paragraph_stale_relation_marks_batch(
        self,
        paragraph_hashes: Sequence[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        normalized: List[str] = []
        seen = set()
        for item in paragraph_hashes or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        if not normalized:
            return {}

        placeholders = ",".join(["?"] * len(normalized))
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT paragraph_hash, relation_hash, query_tool_id, task_id, reason,
                   source_type, source_id, source_operation_id, created_at, updated_at
            FROM paragraph_stale_relation_marks
            WHERE paragraph_hash IN ({placeholders})
            ORDER BY updated_at DESC, paragraph_hash ASC, relation_hash ASC
            """,
            tuple(normalized),
        )
        grouped: Dict[str, List[Dict[str, Any]]] = {token: [] for token in normalized}
        for row in cursor.fetchall():
            payload = self._paragraph_stale_relation_mark_row_to_dict(row)
            if payload is None:
                continue
            grouped.setdefault(payload["paragraph_hash"], []).append(payload)
        return grouped

    def count_paragraph_stale_relation_marks(self) -> int:
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM paragraph_stale_relation_marks")
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def delete_paragraph_stale_relation_marks(
        self,
        marks: Sequence[Tuple[str, str]],
    ) -> int:
        normalized: List[Tuple[str, str]] = []
        seen: set[Tuple[str, str]] = set()
        for paragraph_hash, relation_hash in marks or []:
            paragraph_token = str(paragraph_hash or "").strip()
            relation_token = str(relation_hash or "").strip()
            if not paragraph_token or not relation_token:
                continue
            key = (paragraph_token, relation_token)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        if not normalized:
            return 0

        cursor = self._conn.cursor()
        deleted = 0
        for paragraph_hash, relation_hash in normalized:
            cursor.execute(
                """
                DELETE FROM paragraph_stale_relation_marks
                WHERE paragraph_hash = ? AND relation_hash = ?
                """,
                (paragraph_hash, relation_hash),
            )
            deleted += int(cursor.rowcount or 0)
        self._conn.commit()
        return deleted

    def rollback_paragraph_stale_relation_mark(
        self,
        *,
        paragraph_hash: str,
        relation_hash: str,
        expected_source_type: str,
        expected_source_id: str,
        expected_source_operation_id: str,
        previous_mark: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        paragraph_token = str(paragraph_hash or "").strip()
        relation_token = str(relation_hash or "").strip()
        expected_type = str(expected_source_type or "").strip()
        expected_id = str(expected_source_id or "").strip()
        expected_operation_id = str(expected_source_operation_id or "").strip()
        if not paragraph_token or not relation_token:
            return {
                "success": False,
                "action": "invalid_target",
                "paragraph_hash": paragraph_token,
                "relation_hash": relation_token,
                "error": "paragraph_hash 和 relation_hash 不能为空",
            }

        current = self.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_token,
            relation_hash=relation_token,
        )
        if current is None:
            return {
                "success": True,
                "action": "already_missing",
                "paragraph_hash": paragraph_token,
                "relation_hash": relation_token,
            }

        current_source = (
            str(current.get("source_type", "") or "").strip(),
            str(current.get("source_id", "") or "").strip(),
            str(current.get("source_operation_id", "") or "").strip(),
        )
        expected_source = (expected_type, expected_id, expected_operation_id)
        if current_source != expected_source:
            return {
                "success": True,
                "action": "skipped_due_to_source_mismatch",
                "paragraph_hash": paragraph_token,
                "relation_hash": relation_token,
                "current": current,
                "expected_source": {
                    "source_type": expected_type,
                    "source_id": expected_id,
                    "source_operation_id": expected_operation_id,
                },
            }

        before = current
        if isinstance(previous_mark, dict):
            restored = self._restore_paragraph_stale_relation_mark(previous_mark)
            return {
                "success": restored is not None,
                "action": "restored" if restored is not None else "restore_failed",
                "paragraph_hash": paragraph_token,
                "relation_hash": relation_token,
                "before": before,
                "after": restored,
            }

        deleted = self.delete_paragraph_stale_relation_marks([(paragraph_token, relation_token)])
        return {
            "success": True,
            "action": "deleted" if deleted > 0 else "already_missing",
            "paragraph_hash": paragraph_token,
            "relation_hash": relation_token,
            "before": before,
        }

    def _restore_paragraph_stale_relation_mark(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        paragraph_token = str(snapshot.get("paragraph_hash", "") or "").strip()
        relation_token = str(snapshot.get("relation_hash", "") or "").strip()
        if not paragraph_token or not relation_token:
            return None

        created_at = optional_float(snapshot.get("created_at")) or datetime.now().timestamp()
        updated_at = optional_float(snapshot.get("updated_at")) or created_at
        task_id_raw = snapshot.get("task_id")
        task_id = int(task_id_raw) if int(task_id_raw or 0) > 0 else None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO paragraph_stale_relation_marks (
                paragraph_hash, relation_hash, query_tool_id, task_id, reason,
                source_type, source_id, source_operation_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paragraph_hash, relation_hash) DO UPDATE SET
                query_tool_id = excluded.query_tool_id,
                task_id = excluded.task_id,
                reason = excluded.reason,
                source_type = excluded.source_type,
                source_id = excluded.source_id,
                source_operation_id = excluded.source_operation_id,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                paragraph_token,
                relation_token,
                str(snapshot.get("query_tool_id", "") or "").strip() or None,
                task_id,
                str(snapshot.get("reason", "") or "").strip() or None,
                str(snapshot.get("source_type", "") or "").strip() or None,
                str(snapshot.get("source_id", "") or "").strip() or None,
                str(snapshot.get("source_operation_id", "") or "").strip() or None,
                created_at,
                updated_at,
            ),
        )
        self._conn.commit()
        return self.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_token,
            relation_hash=relation_token,
        )

    @staticmethod
    def _paragraph_stale_relation_mark_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        payload = dict(row)
        payload["paragraph_hash"] = str(payload.get("paragraph_hash", "") or "").strip()
        payload["relation_hash"] = str(payload.get("relation_hash", "") or "").strip()
        payload["query_tool_id"] = str(payload.get("query_tool_id", "") or "").strip()
        payload["task_id"] = int(payload.get("task_id") or 0) if payload.get("task_id") is not None else None
        payload["reason"] = str(payload.get("reason", "") or "").strip()
        payload["source_type"] = str(payload.get("source_type", "") or "").strip()
        payload["source_id"] = str(payload.get("source_id", "") or "").strip()
        payload["source_operation_id"] = str(payload.get("source_operation_id", "") or "").strip()
        payload["created_at"] = optional_float(payload.get("created_at"))
        payload["updated_at"] = optional_float(payload.get("updated_at"))
        return payload
