from pathlib import Path

from src.A_memorix.core.storage.metadata_store import MetadataStore


def test_feedback_task_claim_and_rollback_claim_use_leases(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        task = store.enqueue_feedback_task(
            query_tool_id="query-1",
            session_id="session-1",
            query_timestamp=1.0,
            due_at=1.0,
        )
        assert task is not None
        task_id = int(task["id"])

        claimed = store.mark_feedback_task_running(task_id, lease_seconds=60.0)
        assert claimed is not None
        assert store.mark_feedback_task_running(task_id, lease_seconds=60.0) is None

        connection = store.get_connection()
        connection.execute(
            "UPDATE memory_feedback_tasks SET updated_at = 0 WHERE id = ?",
            (task_id,),
        )
        connection.commit()
        assert [item["id"] for item in store.fetch_due_feedback_tasks(now=100.0, lease_seconds=60.0)] == [task_id]
        assert store.mark_feedback_task_running(task_id, lease_seconds=60.0) is not None

        store.finalize_feedback_task(task_id=task_id, status="applied")
        assert store.mark_feedback_task_rollback_running(task_id=task_id, lease_seconds=60.0) is not None
        assert store.mark_feedback_task_rollback_running(task_id=task_id, lease_seconds=60.0) is None

        connection.execute(
            "UPDATE memory_feedback_tasks SET updated_at = 0 WHERE id = ?",
            (task_id,),
        )
        connection.commit()
        assert store.mark_feedback_task_rollback_running(task_id=task_id, lease_seconds=60.0) is not None
    finally:
        store.close()
