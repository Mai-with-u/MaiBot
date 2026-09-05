"""initialize_database 对 create_all 的空库限制测试。

已存在的非空数据库不得调用 ``SQLModel.metadata.create_all``；
空数据库允许调用以建出最新结构。
若相关模块尚未落地，本文件在导入阶段 skip。
"""

from pathlib import Path
from typing import Any, Iterator
from unittest.mock import Mock

import pytest

sqlmodel = pytest.importorskip("sqlmodel")
SQLModel = sqlmodel.SQLModel
create_engine = sqlmodel.create_engine

database_module = pytest.importorskip("src.common.database.database")
migrations_module = pytest.importorskip("src.common.database.migrations")

if not hasattr(database_module, "initialize_database"):
    pytest.skip("initialize_database 尚未实现", allow_module_level=True)
if not hasattr(migrations_module, "create_database_migration_bootstrapper"):
    pytest.skip("create_database_migration_bootstrapper 尚未实现", allow_module_level=True)

create_database_migration_bootstrapper = migrations_module.create_database_migration_bootstrapper
initialize_database = database_module.initialize_database


def _bind_temp_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """将数据库模块绑定到临时 SQLite，并重置初始化标记。"""

    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "MaiBot.db"
    database_url = f"sqlite:///{db_file}"
    engine = create_engine(
        database_url,
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    bootstrapper = create_database_migration_bootstrapper(engine)
    monkeypatch.setattr(database_module, "_DB_DIR", db_dir)
    monkeypatch.setattr(database_module, "_DB_FILE", db_file)
    monkeypatch.setattr(database_module, "DATABASE_URL", database_url)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "_migration_bootstrapper", bootstrapper)
    monkeypatch.setattr(database_module, "_db_initialized", False)
    monkeypatch.setattr(database_module, "ensure_runtime_performance_indexes", lambda: None)
    return engine


def _patch_create_all(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """拦截 ``SQLModel.metadata.create_all``，避免真实建表。"""

    create_all_spy = Mock(name="sqlmodel_metadata_create_all")
    monkeypatch.setattr(SQLModel.metadata, "create_all", create_all_spy)
    return create_all_spy


def _seed_existing_non_empty_database(engine: Any) -> None:
    """写入已有表、数据和 schema 版本，模拟已存在的非空数据库。"""

    schema_version = database_module._migration_bootstrapper.latest_schema_version
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE existing_records (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO existing_records (name) VALUES ('already-here')")
        connection.exec_driver_sql(f"PRAGMA user_version = {schema_version}")


@pytest.fixture
def temp_database_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    """提供绑定到临时 SQLite 的数据库引擎。"""

    engine = _bind_temp_database(monkeypatch, tmp_path)
    try:
        yield engine
    finally:
        engine.dispose()


def test_initialize_database_does_not_call_create_all_on_existing_non_empty_database(
    monkeypatch: pytest.MonkeyPatch,
    temp_database_engine: Any,
) -> None:
    """已存在的非空数据库初始化时不得调用 create_all。"""

    _seed_existing_non_empty_database(temp_database_engine)
    create_all_spy = _patch_create_all(monkeypatch)

    initialize_database()

    assert database_module._db_initialized is True
    create_all_spy.assert_not_called()


def test_initialize_database_may_call_create_all_on_empty_database(
    monkeypatch: pytest.MonkeyPatch,
    temp_database_engine: Any,
) -> None:
    """空数据库初始化时允许调用 create_all。"""

    create_all_spy = _patch_create_all(monkeypatch)

    initialize_database()

    assert database_module._db_initialized is True
    if create_all_spy.called:
        called_args, called_kwargs = create_all_spy.call_args
        if called_args:
            called_engine = called_args[0]
        else:
            called_engine = called_kwargs.get("bind")
        assert called_engine is temp_database_engine
