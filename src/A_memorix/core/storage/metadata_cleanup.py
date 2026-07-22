from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import sqlite3
import uuid


class MetadataCleanupMixin:
    """管理跨存储清理任务及可恢复的元数据快照。"""

    _conn: sqlite3.Connection

    @staticmethod
    def _validate_cleanup_job_payload(
        *,
        resource_type: str,
        resource_id: str,
        action: str,
        payload: Any,
    ) -> List[str]:
        """校验正式 Outbox 协议，并返回任务声明的资源 hash。"""
        if not isinstance(payload, dict):
            raise ValueError(f"清理任务 payload 必须是对象: action={action}")
        if action in {"graph_rebuild", "graph_restore"}:
            expected_resource_id = "structure" if action == "graph_rebuild" else "restore"
            if resource_type != "graph" or resource_id != expected_resource_id or payload:
                raise ValueError(f"图清理任务协议非法: action={action}")
            return []
        if resource_type not in {"paragraph", "entity", "relation"}:
            raise ValueError(f"未知向量资源类型: {resource_type}")
        if action == "vector_delete":
            key = f"{resource_type}_hashes"
            if set(payload) != {key} or not isinstance(payload[key], list):
                raise ValueError(f"vector_delete payload 必须且只能包含 {key}")
            raw_ids = payload[key]
            resource_ids = [str(item or "").strip() for item in raw_ids]
            if not resource_ids or any(not item for item in resource_ids):
                raise ValueError("vector_delete 资源 hash 不能为空")
            if len(set(resource_ids)) != len(resource_ids):
                raise ValueError("vector_delete 资源 hash 不允许重复")
            return resource_ids
        if action == "vector_upsert":
            if set(payload) != {"item"} or not isinstance(payload["item"], dict):
                raise ValueError("vector_upsert payload 必须且只能包含 item 对象")
            item_hash = str(payload["item"].get("hash", "") or "").strip()
            if not item_hash or item_hash != resource_id:
                raise ValueError("vector_upsert item.hash 必须等于 resource_id")
            return [item_hash]
        raise ValueError(f"未知清理任务 action: {action}")

    @staticmethod
    def _active_cleanup_resource_ids(
        conn: sqlite3.Connection,
        *,
        resource_type: str,
        resource_ids: Sequence[str],
    ) -> set[str]:
        if not resource_ids:
            return set()
        table = {
            "paragraph": "paragraphs",
            "entity": "entities",
            "relation": "relations",
        }[resource_type]
        active: set[str] = set()
        for offset in range(0, len(resource_ids), 900):
            chunk = list(resource_ids[offset : offset + 900])
            placeholders = ",".join(["?"] * len(chunk))
            active_clause = "" if resource_type == "relation" else "AND (is_deleted IS NULL OR is_deleted = 0)"
            rows = conn.execute(
                f"SELECT hash FROM {table} WHERE hash IN ({placeholders}) {active_clause}",
                tuple(chunk),
            ).fetchall()
            active.update(str(row["hash"]) for row in rows)
        return active

    def _cleanup_job_summary_in_transaction(
        self,
        conn: sqlite3.Connection,
        operation_id: str,
    ) -> Dict[str, int]:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM storage_cleanup_jobs
            WHERE operation_id = ?
            GROUP BY status
            """,
            (operation_id,),
        ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        counts["total"] = sum(counts.values())
        counts["unfinished"] = sum(
            count for status, count in counts.items() if status in {"pending", "failed", "running"}
        )
        return counts

    def _reconcile_settled_delete_operation(
        self,
        conn: sqlite3.Connection,
        operation_id: str,
    ) -> Optional[str]:
        summary = self._cleanup_job_summary_in_transaction(conn, operation_id)
        if summary["unfinished"] != 0:
            return None
        row = conn.execute(
            "SELECT status, summary_json FROM delete_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        current_status = str(row["status"] or "")
        if current_status == "pending_cleanup":
            target_status = "completed"
            restored_at = None
        elif current_status == "restore_pending":
            target_status = "restored"
            restored_at = datetime.now().timestamp()
        else:
            return None
        operation_summary = self._json_loads(row["summary_json"], {})
        operation_summary.update({"state": target_status, "cleanup": summary})
        conn.execute(
            """
            UPDATE delete_operations
            SET status = ?, restored_at = COALESCE(?, restored_at), summary_json = ?
            WHERE operation_id = ? AND status = ?
            """,
            (
                target_status,
                restored_at,
                self._json_dumps(operation_summary),
                operation_id,
                current_status,
            ),
        )
        return target_status

    def set_paragraph_expiration(
        self,
        paragraph_hashes: Sequence[str],
        *,
        expires_at: Optional[float],
        reason: str,
    ) -> int:
        """显式设置段落过期时间；普通段落保持 ``expires_at=NULL``。"""
        hashes = [str(item or "").strip() for item in paragraph_hashes if str(item or "").strip()]
        if not hashes:
            return 0
        reason_token = str(reason or "").strip()
        if expires_at is not None and not reason_token:
            raise ValueError("设置段落过期时间必须提供 reason")
        placeholders = ",".join(["?"] * len(hashes))
        cursor = self._conn.execute(
            f"""
            UPDATE paragraphs
            SET expires_at = ?,
                deletion_reason = CASE WHEN ? IS NULL THEN NULL ELSE ? END,
                updated_at = ?
            WHERE hash IN ({placeholders})
              AND (is_deleted IS NULL OR is_deleted = 0)
            """,
            (
                None if expires_at is None else float(expires_at),
                expires_at,
                reason_token or None,
                datetime.now().timestamp(),
                *hashes,
            ),
        )
        self._conn.commit()
        return max(0, int(cursor.rowcount or 0))

    def get_expired_paragraph_hashes(
        self,
        *,
        now: Optional[float] = None,
        limit: int = 1000,
    ) -> List[str]:
        """返回显式到期且没有永久或外部引用保护的段落。"""
        current = float(datetime.now().timestamp() if now is None else now)
        rows = self._conn.execute(
            """
            SELECT p.hash
            FROM paragraphs p
            WHERE (p.is_deleted IS NULL OR p.is_deleted = 0)
              AND p.expires_at IS NOT NULL
              AND p.expires_at <= ?
              AND COALESCE(p.is_permanent, 0) = 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM external_memory_refs r
                  WHERE r.paragraph_hash = p.hash
              )
            ORDER BY p.expires_at ASC, p.hash ASC
            LIMIT ?
            """,
            (current, max(1, int(limit))),
        ).fetchall()
        return [str(row["hash"]) for row in rows]

    def enqueue_storage_cleanup_jobs(
        self,
        *,
        operation_id: str,
        jobs: Sequence[Dict[str, Any]],
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """在调用方事务中写入幂等清理任务。"""
        operation_token = str(operation_id or "").strip()
        if not operation_token:
            raise ValueError("operation_id 不能为空")

        target_conn = conn or self._conn
        now = datetime.now().timestamp()
        inserted = 0
        for job in jobs:
            resource_type = str(job.get("resource_type", "") or "").strip()
            resource_id = str(job.get("resource_id", "") or "").strip()
            action = str(job.get("action", "") or "").strip()
            if not resource_type or not resource_id or not action:
                raise ValueError("清理任务必须包含 resource_type、resource_id 和 action")
            expected_status_by_action = {
                "vector_delete": "pending_cleanup",
                "graph_rebuild": "pending_cleanup",
                "vector_upsert": "restore_pending",
                "graph_restore": "restore_pending",
            }
            if action not in expected_status_by_action:
                raise ValueError(f"未知清理任务 action: {action}")
            expected_state = job.get("expected_state")
            required_expected_state = {
                "operation_status": expected_status_by_action[action],
            }
            if expected_state != required_expected_state:
                raise ValueError(
                    f"清理任务 expected_state 必须为 {required_expected_state}: action={action}"
                )
            payload = job.get("payload")
            self._validate_cleanup_job_payload(
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                payload=payload,
            )
            cursor = target_conn.execute(
                """
                INSERT OR IGNORE INTO storage_cleanup_jobs (
                    operation_id, resource_type, resource_id, action,
                    payload_json, expected_state_json, status, attempt_count,
                    next_attempt_at, lease_token, lease_until, last_error,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, NULL, ?, ?, NULL)
                """,
                (
                    operation_token,
                    resource_type,
                    resource_id,
                    action,
                    self._json_dumps(payload),
                    self._json_dumps(expected_state),
                    now,
                    now,
                    now,
                ),
            )
            inserted += max(0, int(cursor.rowcount or 0))
        return inserted

    def claim_storage_cleanup_jobs(
        self,
        *,
        worker_token: str,
        limit: int = 100,
        lease_seconds: float = 60.0,
        operation_id: str = "",
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """领取待执行任务；过期租约可以由其他工作线程重新领取。"""
        worker = str(worker_token or "").strip()
        if not worker:
            raise ValueError("worker_token 不能为空")
        current = float(datetime.now().timestamp() if now is None else now)
        lease_until = current + max(1.0, float(lease_seconds))
        operation_token = str(operation_id or "").strip()

        with self.transaction(immediate=True) as conn:
            params: List[Any] = [current, current]
            operation_clause = ""
            if operation_token:
                operation_clause = "AND operation_id = ?"
                params.append(operation_token)
            params.append(max(1, int(limit)))
            rows = conn.execute(
                f"""
                SELECT job_id
                FROM storage_cleanup_jobs
                WHERE (
                    (status IN ('pending', 'failed') AND next_attempt_at <= ?)
                    OR (status = 'running' AND lease_until <= ?)
                )
                {operation_clause}
                ORDER BY created_at ASC, job_id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            job_ids = [int(row["job_id"]) for row in rows]
            if not job_ids:
                return []
            placeholders = ",".join(["?"] * len(job_ids))
            conn.execute(
                f"""
                UPDATE storage_cleanup_jobs
                SET status = 'running',
                    attempt_count = attempt_count + 1,
                    lease_token = ?,
                    lease_until = ?,
                    updated_at = ?,
                    last_error = NULL
                WHERE job_id IN ({placeholders})
                """,
                (worker, lease_until, current, *job_ids),
            )
            claimed = conn.execute(
                f"""
                SELECT *
                FROM storage_cleanup_jobs
                WHERE job_id IN ({placeholders}) AND lease_token = ?
                ORDER BY created_at ASC, job_id ASC
                """,
                (*job_ids, worker),
            ).fetchall()

        result: List[Dict[str, Any]] = []
        for row in claimed:
            payload = dict(row)
            payload["payload"] = self._json_loads(payload.pop("payload_json", None), {})
            payload["expected_state"] = self._json_loads(payload.pop("expected_state_json", None), {})
            result.append(payload)
        return result

    def complete_storage_cleanup_job(
        self,
        *,
        job_id: int,
        worker_token: str,
        status: str = "completed",
    ) -> bool:
        """完成或取消当前工作线程持有的任务。"""
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"completed", "cancelled"}:
            raise ValueError("status 只能是 completed 或 cancelled")
        now = datetime.now().timestamp()
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                """
                SELECT operation_id
                FROM storage_cleanup_jobs
                WHERE job_id = ? AND status = 'running' AND lease_token = ?
                """,
                (int(job_id), str(worker_token or "").strip()),
            ).fetchone()
            if row is None:
                return False
            cursor = conn.execute(
                """
                UPDATE storage_cleanup_jobs
                SET status = ?, lease_token = NULL, lease_until = NULL,
                    updated_at = ?, completed_at = ?, last_error = NULL
                WHERE job_id = ? AND status = 'running' AND lease_token = ?
                """,
                (normalized_status, now, now, int(job_id), str(worker_token or "").strip()),
            )
            if cursor.rowcount != 1:
                return False
            self._reconcile_settled_delete_operation(conn, str(row["operation_id"]))
        return True

    def fail_storage_cleanup_job(
        self,
        *,
        job_id: int,
        worker_token: str,
        error: str,
        retry_delay_seconds: float,
    ) -> bool:
        """记录失败并按确定的退避时间重新排队。"""
        now = datetime.now().timestamp()
        cursor = self._conn.execute(
            """
            UPDATE storage_cleanup_jobs
            SET status = 'failed', lease_token = NULL, lease_until = NULL,
                next_attempt_at = ?, updated_at = ?, last_error = ?
            WHERE job_id = ? AND status = 'running' AND lease_token = ?
            """,
            (
                now + max(0.0, float(retry_delay_seconds)),
                now,
                str(error or "")[:2000],
                int(job_id),
                str(worker_token or "").strip(),
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_storage_cleanup_jobs(
        self,
        *,
        operation_id: str = "",
        statuses: Sequence[str] = (),
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        operation_token = str(operation_id or "").strip()
        if operation_token:
            clauses.append("operation_id = ?")
            params.append(operation_token)
        normalized_statuses = [str(item or "").strip().lower() for item in statuses if str(item or "").strip()]
        if normalized_statuses:
            placeholders = ",".join(["?"] * len(normalized_statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        rows = self._conn.execute(
            f"""
            SELECT *
            FROM storage_cleanup_jobs
            {where}
            ORDER BY created_at ASC, job_id ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["payload"] = self._json_loads(payload.pop("payload_json", None), {})
            payload["expected_state"] = self._json_loads(payload.pop("expected_state_json", None), {})
            result.append(payload)
        return result

    def authorize_storage_cleanup_job(
        self,
        *,
        job_id: int,
        worker_token: str,
        lease_seconds: float = 300.0,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """校验租约、operation 及资源权威状态，并延长外部提交租约。"""
        worker = str(worker_token or "").strip()
        if not worker:
            raise ValueError("worker_token 不能为空")
        current = float(datetime.now().timestamp() if now is None else now)
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                """
                SELECT j.status AS job_status,
                       j.lease_token,
                       j.lease_until,
                       j.resource_type,
                       j.resource_id,
                       j.action,
                       j.payload_json,
                       j.expected_state_json,
                       o.status AS operation_status
                FROM storage_cleanup_jobs j
                JOIN delete_operations o ON o.operation_id = j.operation_id
                WHERE j.job_id = ?
                """,
                (int(job_id),),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"storage cleanup job 不存在: job_id={job_id}")
            if str(row["job_status"] or "") != "running" or str(row["lease_token"] or "") != worker:
                raise RuntimeError(f"storage cleanup job 租约已失效: job_id={job_id}")
            lease_until = float(row["lease_until"] or 0.0)
            if lease_until <= current:
                raise RuntimeError(f"storage cleanup job 租约已过期: job_id={job_id}")

            expected_state = self._json_loads(row["expected_state_json"], {})
            if set(expected_state) != {"operation_status"}:
                raise ValueError(
                    f"storage cleanup job expected_state 必须且只能包含 operation_status: job_id={job_id}"
                )
            expected_status = str(expected_state["operation_status"] or "").strip()
            if expected_status not in {"pending_cleanup", "restore_pending"}:
                raise ValueError(
                    f"storage cleanup job operation_status 非法: job_id={job_id}, status={expected_status}"
                )
            resource_type = str(row["resource_type"] or "")
            resource_id = str(row["resource_id"] or "")
            action = str(row["action"] or "")
            payload = self._json_loads(row["payload_json"], {})
            declared_ids = self._validate_cleanup_job_payload(
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                payload=payload,
            )
            operation_matches = str(row["operation_status"] or "") == expected_status
            active_ids = self._active_cleanup_resource_ids(
                conn,
                resource_type=resource_type,
                resource_ids=declared_ids,
            ) if declared_ids else set()
            if action == "vector_delete":
                authorized_ids = [item for item in declared_ids if item not in active_ids]
            elif action == "vector_upsert":
                authorized_ids = [item for item in declared_ids if item in active_ids]
            else:
                authorized_ids = []
            if not operation_matches:
                return {
                    "operation_matches": False,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_ids": [],
                }

            cursor = conn.execute(
                """
                UPDATE storage_cleanup_jobs
                SET lease_until = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_token = ?
                """,
                (current + max(1.0, float(lease_seconds)), current, int(job_id), worker),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"storage cleanup job 租约续期失败: job_id={job_id}")
        return {
            "operation_matches": True,
            "action": action,
            "resource_type": resource_type,
            "resource_ids": authorized_ids,
        }

    def cancel_delete_cleanup_jobs(
        self,
        operation_id: str,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """恢复删除操作时取消未领取任务；运行中的任务必须先完成或释放租约。"""
        operation_token = str(operation_id or "").strip()
        if not operation_token:
            raise ValueError("operation_id 不能为空")
        target_conn = conn or self._conn
        running = target_conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM storage_cleanup_jobs
            WHERE operation_id = ?
              AND action IN ('vector_delete', 'graph_rebuild')
              AND status = 'running'
            """,
            (operation_token,),
        ).fetchone()
        if running is not None and int(running["count"] or 0) > 0:
            raise RuntimeError("删除外部清理任务仍在运行，暂不能恢复")

        now = datetime.now().timestamp()
        cursor = target_conn.execute(
            """
            UPDATE storage_cleanup_jobs
            SET status = 'cancelled', lease_token = NULL, lease_until = NULL,
                updated_at = ?, completed_at = ?
            WHERE operation_id = ?
              AND action IN ('vector_delete', 'graph_rebuild')
              AND status IN ('pending', 'failed')
            """,
            (now, now, operation_token),
        )
        if conn is None:
            target_conn.commit()
        return max(0, int(cursor.rowcount or 0))

    def supersede_delete_cleanup_resources(
        self,
        *,
        resource_type: str,
        resource_ids: Sequence[str],
        conn: sqlite3.Connection,
    ) -> Dict[str, int]:
        """从旧删除任务中事务化剔除已恢复资源，不影响同批其他资源。"""
        if resource_type not in {"paragraph", "entity", "relation"}:
            raise ValueError(f"未知资源类型: {resource_type}")
        normalized = [str(item or "").strip() for item in resource_ids if str(item or "").strip()]
        normalized = list(dict.fromkeys(normalized))
        if not normalized:
            return {"updated_jobs": 0, "cancelled_jobs": 0, "superseded_operations": 0}
        placeholders = ",".join(["?"] * len(normalized))
        operation_rows = conn.execute(
            f"""
            SELECT DISTINCT i.operation_id
            FROM delete_operation_items i
            JOIN delete_operations o ON o.operation_id = i.operation_id
            WHERE i.item_type = ?
              AND i.item_hash IN ({placeholders})
              AND o.status IN ('pending_cleanup', 'completed')
            """,
            (resource_type, *normalized),
        ).fetchall()
        operation_ids = [str(row["operation_id"]) for row in operation_rows]
        target_ids = set(normalized)
        updated_jobs = 0
        cancelled_jobs = 0
        superseded_operations = 0
        now = datetime.now().timestamp()

        def operation_items_fully_restored(operation_id: str) -> bool:
            item_rows = conn.execute(
                """
                SELECT item_type, item_hash
                FROM delete_operation_items
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchall()
            if not item_rows:
                return False
            grouped: Dict[str, List[str]] = {"paragraph": [], "entity": [], "relation": []}
            for item_row in item_rows:
                item_type = str(item_row["item_type"] or "").strip()
                item_hash = str(item_row["item_hash"] or "").strip()
                if item_type not in grouped or not item_hash:
                    return False
                grouped[item_type].append(item_hash)

            for item_type, item_hashes in grouped.items():
                expected = set(item_hashes)
                if item_type == resource_type:
                    expected.difference_update(target_ids)
                if not expected:
                    continue
                item_placeholders = ",".join(["?"] * len(expected))
                if item_type == "relation":
                    rows = conn.execute(
                        f"SELECT hash FROM relations WHERE hash IN ({item_placeholders})",
                        tuple(expected),
                    ).fetchall()
                else:
                    table_name = "paragraphs" if item_type == "paragraph" else "entities"
                    rows = conn.execute(
                        f"""
                        SELECT hash FROM {table_name}
                        WHERE hash IN ({item_placeholders})
                          AND (is_deleted IS NULL OR is_deleted = 0)
                        """,
                        tuple(expected),
                    ).fetchall()
                active_ids = {str(row["hash"] or "").strip() for row in rows}
                if active_ids != expected:
                    return False
            return True

        for operation_id in operation_ids:
            running = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM storage_cleanup_jobs
                WHERE operation_id = ?
                  AND action IN ('vector_delete', 'graph_rebuild')
                  AND status = 'running'
                """,
                (operation_id,),
            ).fetchone()
            if running is not None and int(running["count"] or 0) > 0:
                raise RuntimeError("旧删除外部清理任务仍在运行，暂不能恢复")

            jobs = conn.execute(
                """
                SELECT job_id, resource_id, payload_json
                FROM storage_cleanup_jobs
                WHERE operation_id = ?
                  AND resource_type = ?
                  AND action = 'vector_delete'
                  AND status IN ('pending', 'failed')
                """,
                (operation_id, resource_type),
            ).fetchall()
            payload_key = f"{resource_type}_hashes"
            for job in jobs:
                payload = self._json_loads(job["payload_json"], {})
                declared_ids = self._validate_cleanup_job_payload(
                    resource_type=resource_type,
                    resource_id=str(job["resource_id"]),
                    action="vector_delete",
                    payload=payload,
                )
                remaining_ids = [item for item in declared_ids if item not in target_ids]
                if len(remaining_ids) == len(declared_ids):
                    continue
                if remaining_ids:
                    conn.execute(
                        """
                        UPDATE storage_cleanup_jobs
                        SET payload_json = ?, updated_at = ?, last_error = NULL
                        WHERE job_id = ? AND status IN ('pending', 'failed')
                        """,
                        (
                            self._json_dumps({payload_key: remaining_ids}),
                            now,
                            int(job["job_id"]),
                        ),
                    )
                    updated_jobs += 1
                else:
                    conn.execute(
                        """
                        UPDATE storage_cleanup_jobs
                        SET status = 'cancelled', lease_token = NULL, lease_until = NULL,
                            updated_at = ?, completed_at = ?, last_error = NULL
                        WHERE job_id = ? AND status IN ('pending', 'failed')
                        """,
                        (now, now, int(job["job_id"])),
                    )
                    cancelled_jobs += 1

            remaining = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM storage_cleanup_jobs
                WHERE operation_id = ?
                  AND action = 'vector_delete'
                  AND status IN ('pending', 'failed', 'running')
                """,
                (operation_id,),
            ).fetchone()
            if remaining is not None and int(remaining["count"] or 0) > 0:
                continue
            # 只恢复批量操作的一部分时，旧 operation 仍是其余删除项的唯一恢复入口。
            if not operation_items_fully_restored(operation_id):
                continue
            graph_cursor = conn.execute(
                """
                UPDATE storage_cleanup_jobs
                SET status = 'cancelled', lease_token = NULL, lease_until = NULL,
                    updated_at = ?, completed_at = ?, last_error = NULL
                WHERE operation_id = ?
                  AND action = 'graph_rebuild'
                  AND status IN ('pending', 'failed')
                """,
                (now, now, operation_id),
            )
            cancelled_jobs += max(0, int(graph_cursor.rowcount or 0))
            operation = conn.execute(
                "SELECT summary_json FROM delete_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            summary = self._json_loads(operation["summary_json"], {}) if operation is not None else {}
            summary.update(
                {
                    "state": "superseded",
                    "superseded_by_restore": {
                        "resource_type": resource_type,
                        "resource_ids": normalized,
                    },
                    "cleanup": self._cleanup_job_summary_in_transaction(conn, operation_id),
                }
            )
            cursor = conn.execute(
                """
                UPDATE delete_operations
                SET status = 'superseded', summary_json = ?
                WHERE operation_id = ? AND status IN ('pending_cleanup', 'completed')
                """,
                (self._json_dumps(summary), operation_id),
            )
            superseded_operations += max(0, int(cursor.rowcount or 0))
        return {
            "updated_jobs": updated_jobs,
            "cancelled_jobs": cancelled_jobs,
            "superseded_operations": superseded_operations,
        }

    def reconcile_settled_delete_operations(
        self,
        operation_ids: Sequence[str] = (),
    ) -> List[str]:
        """修复任务已终态但 operation 尚未迁移的崩溃窗口。"""
        normalized = [str(item or "").strip() for item in operation_ids if str(item or "").strip()]
        with self.transaction(immediate=True) as conn:
            params: List[Any] = []
            operation_clause = ""
            if normalized:
                placeholders = ",".join(["?"] * len(normalized))
                operation_clause = f"AND operation_id IN ({placeholders})"
                params.extend(normalized)
            rows = conn.execute(
                f"""
                SELECT operation_id
                FROM delete_operations
                WHERE status IN ('pending_cleanup', 'restore_pending')
                {operation_clause}
                ORDER BY created_at ASC
                """,
                tuple(params),
            ).fetchall()
            reconciled: List[str] = []
            for row in rows:
                operation_id = str(row["operation_id"])
                if self._reconcile_settled_delete_operation(conn, operation_id) is not None:
                    reconciled.append(operation_id)
        return reconciled

    def create_cleanup_worker_token(self) -> str:
        return f"cleanup_{uuid.uuid4().hex}"

    def update_delete_operation_state(
        self,
        operation_id: str,
        *,
        status: str,
        summary_patch: Optional[Dict[str, Any]] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """更新删除操作状态并合并可审计摘要。"""
        operation_token = str(operation_id or "").strip()
        status_token = str(status or "").strip()
        if not operation_token or not status_token:
            raise ValueError("operation_id 和 status 不能为空")
        target_conn = conn or self._conn
        row = target_conn.execute(
            "SELECT summary_json FROM delete_operations WHERE operation_id = ?",
            (operation_token,),
        ).fetchone()
        if row is None:
            return False
        summary = self._json_loads(row["summary_json"], {})
        summary.update(summary_patch or {})
        cursor = target_conn.execute(
            """
            UPDATE delete_operations
            SET status = ?, summary_json = ?
            WHERE operation_id = ?
            """,
            (status_token, self._json_dumps(summary), operation_token),
        )
        if conn is None:
            target_conn.commit()
        return cursor.rowcount > 0

    def summarize_storage_cleanup_jobs(self, operation_id: str) -> Dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM storage_cleanup_jobs
            WHERE operation_id = ?
            GROUP BY status
            """,
            (str(operation_id or "").strip(),),
        ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        counts["total"] = sum(counts.values())
        counts["unfinished"] = sum(
            count for status, count in counts.items() if status in {"pending", "failed", "running"}
        )
        return counts

    def restore_table_row_from_snapshot(
        self,
        table: str,
        snapshot: Dict[str, Any],
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """按当前正式 schema 恢复被物理清理的基础行。"""
        allowed_tables = {"paragraphs", "entities", "relations"}
        if table not in allowed_tables:
            raise ValueError(f"不允许恢复表: {table}")
        if not isinstance(snapshot, dict) or not str(snapshot.get("hash", "") or "").strip():
            raise ValueError("恢复快照缺少 hash")

        target_conn = conn or self._conn
        table_columns = {
            str(row["name"])
            for row in target_conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        restored = {key: value for key, value in snapshot.items() if key in table_columns}
        if isinstance(restored.get("metadata"), dict):
            restored["metadata"] = self._encode_metadata(restored["metadata"])
        if table == "paragraphs":
            if not str(restored.get("content", "") or ""):
                raise ValueError("段落恢复快照缺少 content")
            restored.update({"is_deleted": 0, "deleted_at": None})
        elif table == "entities":
            if not str(restored.get("name", "") or "").strip():
                raise ValueError("实体恢复快照缺少 name")
            restored.update({"is_deleted": 0, "deleted_at": None})

        columns = list(restored)
        placeholders = ",".join(["?"] * len(columns))
        updates = ",".join(
            f"{column}=excluded.{column}" for column in columns if column != "hash"
        )
        target_conn.execute(
            f"""
            INSERT INTO {table} ({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(hash) DO UPDATE SET {updates}
            """,
            tuple(restored[column] for column in columns),
        )
        if conn is None:
            target_conn.commit()
        return True
