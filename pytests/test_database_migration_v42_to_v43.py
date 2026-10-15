from pathlib import Path

from sqlalchemy import create_engine, event

import pytest

from src.common.database.migrations.builtin import build_default_migration_registry
from src.common.database.migrations.exceptions import DatabaseMigrationExecutionError
from src.common.database.migrations.manager import DatabaseMigrationManager


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
@pytest.mark.parametrize("version", [41, 42])
def test_upgrade_reclaims_disk_space_and_runs_once(tmp_path: Path, journal_mode: str, version: int) -> None:
    database_path = tmp_path / "maibot.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        connection.exec_driver_sql(f"PRAGMA journal_mode={journal_mode}")
        connection.exec_driver_sql("CREATE TABLE mai_messages (id INTEGER PRIMARY KEY, content TEXT)")
        connection.exec_driver_sql("INSERT INTO mai_messages VALUES (1, '保留聊天内容')")
        connection.exec_driver_sql("CREATE TABLE behavior_experience_paths (data BLOB)")
        connection.exec_driver_sql("INSERT INTO behavior_experience_paths VALUES (zeroblob(2097152))")
        connection.exec_driver_sql(f"PRAGMA user_version={version}")
        connection.commit()
        if version == 42:
            connection.exec_driver_sql("DROP TABLE behavior_experience_paths")
            connection.commit()
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
    original_size = database_path.stat().st_size
    vacuum_calls = []

    @event.listens_for(engine, "before_cursor_execute")
    def track_vacuum(connection, cursor, statement, parameters, context, executemany):
        if statement == "VACUUM":
            vacuum_calls.append(statement)

    manager = DatabaseMigrationManager(engine, registry=build_default_migration_registry())
    manager.migrate()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 43
        assert connection.exec_driver_sql("PRAGMA freelist_count").scalar_one() == 0
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("SELECT * FROM mai_messages").fetchall() == [(1, "保留聊天内容")]
    assert database_path.stat().st_size < original_size // 2
    assert manager.migrate().is_empty()
    assert vacuum_calls == ["VACUUM"]
    engine.dispose()


def test_vacuum_failure_does_not_mark_migration_complete(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'maibot.db').as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE mai_messages (id INTEGER)")
        connection.exec_driver_sql("PRAGMA user_version=42")

    @event.listens_for(engine, "before_cursor_execute")
    def reject_vacuum(connection, cursor, statement, parameters, context, executemany):
        if statement == "VACUUM":
            raise RuntimeError("vacuum failed")

    manager = DatabaseMigrationManager(engine, registry=build_default_migration_registry())
    with pytest.raises(DatabaseMigrationExecutionError):
        manager.migrate()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 42
    assert manager.plan().step_count() == 1
    engine.dispose()
