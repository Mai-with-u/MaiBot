from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread

import sqlite3
import pytest

from src.A_memorix.core.storage.metadata_store import MetadataStore


def test_metadata_store_uses_distinct_connections_per_thread(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        main_connection_id = id(store.get_connection())

        def write_paragraph(index: int) -> int:
            connection_id = id(store.get_connection())
            store.add_paragraph(f"线程隔离段落 {index}", source=f"thread-{index}")
            return connection_id

        with ThreadPoolExecutor(max_workers=4) as executor:
            worker_connection_ids = set(executor.map(write_paragraph, range(8)))

        assert main_connection_id not in worker_connection_ids
        assert len(worker_connection_ids) >= 2
        assert store.count_paragraphs() == 8
    finally:
        store.close()


def test_metadata_store_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with pytest.raises(RuntimeError, match="触发回滚"):
            with store.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO paragraphs (hash, content, knowledge_type) VALUES (?, ?, ?)",
                    ("rollback-hash", "不会提交", "mixed"),
                )
                raise RuntimeError("触发回滚")

        assert store.get_paragraph("rollback-hash") is None
    finally:
        store.close()


def test_metadata_store_transaction_rolls_back_on_base_exception(tmp_path: Path) -> None:
    class StopTransaction(BaseException):
        pass

    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with pytest.raises(StopTransaction):
            with store.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO paragraphs (hash, content, knowledge_type) VALUES (?, ?, ?)",
                    ("base-exception-hash", "不会提交", "mixed"),
                )
                raise StopTransaction

        assert store.get_paragraph("base-exception-hash") is None
        manager = store._connection_manager
        assert manager is not None
        connection = manager.connection()
        assert connection._managed_transaction_depth == 0
        assert connection.in_transaction is False
    finally:
        store.close()


def test_metadata_store_rejects_unmanaged_override_transaction(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    connection = sqlite3.connect(":memory:")
    store._conn = connection
    try:
        with pytest.raises(TypeError, match="不支持受管事务"):
            store.transaction()
    finally:
        store.close()


def test_metadata_store_reinitializes_schema_after_switching_data_directory(tmp_path: Path) -> None:
    first_data_dir = tmp_path / "first"
    second_data_dir = tmp_path / "second"
    store = MetadataStore(data_dir=first_data_dir)
    store.connect()
    try:
        store.add_paragraph("第一个数据库中的段落")

        store.connect(second_data_dir)
        second_hash = store.add_paragraph("第二个数据库中的段落")

        assert store.get_paragraph(second_hash) is not None
        assert store.count_paragraphs() == 1
    finally:
        store.close()


def test_metadata_store_transaction_rolls_back_public_write_methods(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with pytest.raises(RuntimeError, match="触发公开写方法回滚"):
            with store.transaction(immediate=True):
                paragraph_hash = store.add_paragraph("事务中的公开写方法")
                raise RuntimeError("触发公开写方法回滚")

        assert store.get_paragraph(paragraph_hash) is None
    finally:
        store.close()


def test_metadata_store_nested_transaction_rolls_back_inner_scope(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        connection = store.get_connection()
        connection.execute(
            "INSERT INTO paragraphs (hash, content, knowledge_type) VALUES (?, ?, ?)",
            ("outer-hash", "外层事务数据", "mixed"),
        )

        with pytest.raises(RuntimeError, match="触发嵌套回滚"):
            with store.transaction(immediate=True):
                paragraph_hash = store.add_paragraph("嵌套事务数据")
                raise RuntimeError("触发嵌套回滚")

        rows = connection.execute(
            "SELECT hash FROM paragraphs WHERE hash IN (?, ?)",
            ("outer-hash", paragraph_hash),
        ).fetchall()
        assert [str(row["hash"]) for row in rows] == ["outer-hash"]
        connection.rollback()
    finally:
        store.close()


def test_metadata_store_transaction_commits_public_write_methods(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with store.transaction(immediate=True):
            paragraph_hash = store.add_paragraph("事务成功提交公开写方法")

        assert store.get_paragraph(paragraph_hash) is not None
    finally:
        store.close()


def test_metadata_store_nested_transaction_preserves_outer_commit_control(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with store.transaction(immediate=True):
            outer_hash = store.add_paragraph("外层事务公开写方法")
            with store.transaction():
                inner_hash = store.add_paragraph("内层事务公开写方法")

        assert store.get_paragraph(outer_hash) is not None
        assert store.get_paragraph(inner_hash) is not None
    finally:
        store.close()


def test_metadata_store_reaps_connections_owned_by_finished_threads(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    worker_connections = []

    def write_paragraph() -> None:
        worker_connections.append(store.get_connection())
        store.add_paragraph("短生命周期线程写入")

    try:
        manager = store._connection_manager
        assert manager is not None
        worker = Thread(target=write_paragraph, name="metadata-short-lived-worker")
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert manager.connection_count == 2

        store.get_connection()

        assert manager.connection_count == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            worker_connections[0].execute("SELECT 1")
    finally:
        store.close()


def test_metadata_store_closed_manager_cannot_create_new_connections(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    manager = store._connection_manager
    assert manager is not None

    store.close()

    assert manager.closed is True
    assert manager.connection_count == 0
    with pytest.raises(RuntimeError, match="连接管理器已关闭"):
        manager.connection()


@pytest.mark.parametrize("operation", ["upsert", "delete"])
def test_fts_paragraph_write_preserves_unmanaged_outer_transaction(
    operation: str,
    tmp_path: Path,
) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("FTS 外部事务测试")
        connection = store.get_connection()
        connection.execute("BEGIN")
        connection.execute("UPDATE paragraphs SET content = ? WHERE hash = ?", ("事务内内容", paragraph_hash))

        if operation == "upsert":
            assert store.fts_upsert_paragraph(paragraph_hash) is True
        else:
            assert store.fts_delete_paragraph(paragraph_hash) is True

        assert connection.in_transaction is True
        connection.rollback()
        assert store.get_paragraph(paragraph_hash)["content"] == "FTS 外部事务测试"
    finally:
        store.close()
