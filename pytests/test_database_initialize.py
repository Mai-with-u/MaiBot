from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, create_engine

import pytest

from src.common.database import database as database_module
from src.common.database.migrations import (
    LATEST_SCHEMA_VERSION,
    SQLiteSchemaInspector,
    SQLiteUserVersionStore,
    create_database_migration_bootstrapper,
)

_EMPTY_DB_MARKER_TABLES = (
    "bot_platform_accounts",
    "chat_sessions",
    "expressions",
    "jargons",
    "mai_messages",
)


def _install_temp_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Engine, Path]:
    """把主库初始化入口切到临时 SQLite 文件。"""
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
    session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        expire_on_commit=False,
    )
    bootstrapper = create_database_migration_bootstrapper(engine)

    monkeypatch.setattr(database_module, "_DB_DIR", db_dir)
    monkeypatch.setattr(database_module, "_DB_FILE", db_file)
    monkeypatch.setattr(database_module, "DATABASE_URL", database_url)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "SessionLocal", session_local)
    monkeypatch.setattr(database_module, "_migration_bootstrapper", bootstrapper)
    monkeypatch.setattr(database_module, "_db_initialized", False)
    return engine, db_file


def _list_user_tables(engine: Engine) -> list[str]:
    """列出测试库中的用户表。"""
    with engine.connect() as connection:
        return SQLiteSchemaInspector().list_user_tables(connection)


def _read_user_version(engine: Engine) -> int:
    """读取测试库的 schema 版本号。"""
    with engine.connect() as connection:
        return SQLiteUserVersionStore().read_version(connection)


@pytest.fixture
def temp_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Engine, Path]:
    """提供隔离的临时主库，并在结束后释放连接。"""
    engine, db_file = _install_temp_database(monkeypatch, tmp_path)
    try:
        yield engine, db_file
    finally:
        engine.dispose()


def test_is_empty_database_when_file_missing(temp_database: tuple[Engine, Path]) -> None:
    """数据库文件不存在时应视为空库。"""
    _engine, db_file = temp_database
    assert not db_file.exists()
    assert database_module._is_empty_database() is True


def test_is_empty_database_when_file_has_no_user_tables(temp_database: tuple[Engine, Path]) -> None:
    """已有文件但没有任何用户表时应视为空库。"""
    engine, db_file = temp_database
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    assert db_file.exists()
    assert _list_user_tables(engine) == []
    assert database_module._is_empty_database() is True


def test_is_empty_database_when_user_tables_exist(temp_database: tuple[Engine, Path]) -> None:
    """只要存在用户表，即使不是完整最新结构，也不视为空库。"""
    engine, db_file = temp_database
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY)")
    assert db_file.exists()
    assert database_module._is_empty_database() is False


def test_initialize_database_creates_latest_schema_when_file_missing(
    temp_database: tuple[Engine, Path],
) -> None:
    """空库（文件不存在）应按当前模型建出最新结构并写入版本号。"""
    engine, db_file = temp_database
    assert not db_file.exists()

    database_module.initialize_database()

    user_tables = set(_list_user_tables(engine))
    assert set(_EMPTY_DB_MARKER_TABLES).issubset(user_tables)
    assert _read_user_version(engine) == LATEST_SCHEMA_VERSION


def test_initialize_database_creates_latest_schema_when_file_has_no_user_tables(
    temp_database: tuple[Engine, Path],
) -> None:
    """已有空文件、尚无用户表时，仍应按当前模型建出最新结构。"""
    engine, db_file = temp_database
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    assert db_file.exists()
    assert _list_user_tables(engine) == []

    database_module.initialize_database()

    user_tables = set(_list_user_tables(engine))
    assert set(_EMPTY_DB_MARKER_TABLES).issubset(user_tables)
    assert _read_user_version(engine) == LATEST_SCHEMA_VERSION


def test_initialize_database_skips_create_all_and_exposes_missing_tables(
    monkeypatch: pytest.MonkeyPatch,
    temp_database: tuple[Engine, Path],
) -> None:
    """已有库不得用 create_all 静默补表；缺表只允许走 version chain。"""
    engine, _db_file = temp_database
    create_all_calls: list[object] = []

    def _record_create_all(bind) -> None:
        """记录 create_all 调用，避免测试误走静默建表。"""
        del bind
        create_all_calls.append(True)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, platform VARCHAR(100), account_id VARCHAR(255))"
        )
        SQLiteUserVersionStore().write_version(connection, LATEST_SCHEMA_VERSION)

    monkeypatch.setattr(database_module.SQLModel.metadata, "create_all", _record_create_all)
    database_module.initialize_database()

    user_tables = set(_list_user_tables(engine))
    assert create_all_calls == []
    assert user_tables == {"chat_sessions"}
    assert "bot_platform_accounts" not in user_tables
    assert "mai_messages" not in user_tables
    assert _read_user_version(engine) == LATEST_SCHEMA_VERSION
