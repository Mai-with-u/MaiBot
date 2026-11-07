from contextlib import closing
from pathlib import Path

import json
import sqlite3

import pytest

from src.plugin_runtime.builtin_adapter_migration import (
    TELEGRAM_LEGACY_ID,
    TELEGRAM_PLUGIN_ID,
    migrate_telegram_adapter,
)
from src.plugins.built_in.telegram_adapter.storage import AdapterStore


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))
    (tmp_path / "plugins").mkdir()
    return tmp_path


def _install(instance: Path, plugin_id: str, version: str) -> Path:
    source = instance / "plugins" / "custom_telegram_folder"
    source.mkdir()
    (source / "_manifest.json").write_text(json.dumps({"id": plugin_id, "version": version}), encoding="utf-8")
    (source / "plugin.py").write_text("# old Telegram adapter\n", encoding="utf-8")
    (source / "config.toml").write_bytes(
        b'# original config\r\n[plugin]\r\nenabled = true\r\n[telegram]\r\nbot_token = "test-token"\r\n'
    )
    return source


@pytest.mark.asyncio
@pytest.mark.parametrize("plugin_id,version", [(TELEGRAM_PLUGIN_ID, "0.1.17"), (TELEGRAM_LEGACY_ID, "0.1.9")])
@pytest.mark.parametrize("legacy_docker", [False, True])
async def test_migrate_telegram_keeps_config_and_database(
    instance: Path, plugin_id: str, version: str, legacy_docker: bool
) -> None:
    source = _install(instance, plugin_id, version)
    expected_config = (source / "config.toml").read_bytes()
    data_root = instance / ("plugins" if legacy_docker else "data/plugins")
    database = data_root / plugin_id / "telegram.sqlite3"
    database.parent.mkdir(parents=True)
    # 模拟旧进程已退出的磁盘数据库；迁移前必须关闭连接，Windows 会锁定打开的文件。
    with closing(sqlite3.connect(database)) as db:
        db.executescript(
            "CREATE TABLE topics (account TEXT, chat_id TEXT, thread_id INTEGER, name TEXT);"
            "CREATE TABLE cursors (account TEXT, offset INTEGER, updated_at REAL);"
            "CREATE TABLE failed_updates (account TEXT, update_id INTEGER, payload TEXT, error TEXT, "
            "traceback TEXT, failed_at REAL);"
            "CREATE TABLE stickers (account TEXT, hash TEXT, file_id TEXT);"
        )
        db.execute("INSERT INTO topics VALUES (?, ?, ?, ?)", ("bot", "-123", 42, "原话题"))
        db.execute("INSERT INTO cursors VALUES ('bot', 101, 0)")
        db.execute(
            "INSERT INTO failed_updates VALUES (?, ?, ?, ?, ?, ?)",
            ("bot", 100, '{"update_id":100,"message":"original"}', "error", "trace", 0),
        )
        db.execute("INSERT INTO stickers VALUES ('bot', 'hash', 'file-id')")
        db.commit()
    expected_database = database.read_bytes()

    backup = migrate_telegram_adapter()

    assert backup is not None and backup.parent == instance / "plugins/.builtin-backups"
    assert not source.exists()
    assert (backup / "config.toml").read_bytes() == expected_config
    assert (instance / "config/builtin_plugins/telegram_adapter/config.toml").read_bytes() == expected_config
    target = instance / "data/plugins" / TELEGRAM_PLUGIN_ID / "telegram.sqlite3"
    assert target.read_bytes() == expected_database
    restored = AdapterStore(target)
    assert await restored.topic_name("bot", "-123", 42) == "原话题"
    assert await restored.sticker("bot", "hash") == "file-id"
    assert (await restored.failed_update("bot", 100))["update"]["message"] == "original"
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT offset FROM cursors WHERE account = 'bot'").fetchone() == (101,)
    assert migrate_telegram_adapter() is None


def test_telegram_migration_preserves_existing_builtin_config(instance: Path) -> None:
    _install(instance, TELEGRAM_PLUGIN_ID, "0.1.17")
    target = instance / "config/builtin_plugins/telegram_adapter/config.toml"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'[plugin]\nenabled = false\n')
    migrate_telegram_adapter()
    assert target.read_bytes() == b'[plugin]\nenabled = false\n'


def test_telegram_keeps_current_database_and_archives_historical_database(instance: Path) -> None:
    source = _install(instance, TELEGRAM_PLUGIN_ID, "0.1.17")
    for plugin_id, contents in [(TELEGRAM_LEGACY_ID, b"old database"), (TELEGRAM_PLUGIN_ID, b"new database")]:
        database = instance / "data/plugins" / plugin_id / "telegram.sqlite3"
        database.parent.mkdir(parents=True)
        database.write_bytes(contents)
    old_data = instance / "data/plugins" / TELEGRAM_LEGACY_ID
    (old_data / "telegram.sqlite3-wal").write_bytes(b"old wal")
    assert migrate_telegram_adapter() is not None
    assert not source.exists()
    assert not old_data.exists()
    backup, = (instance / "data/plugins/.builtin-backups").iterdir()
    assert (backup / "telegram.sqlite3").read_bytes() == b"old database"
    assert (backup / "telegram.sqlite3-wal").read_bytes() == b"old wal"
    assert (instance / "data/plugins" / TELEGRAM_PLUGIN_ID / "telegram.sqlite3").read_bytes() == b"new database"
    assert not (instance / "data/plugins" / TELEGRAM_PLUGIN_ID / "telegram.sqlite3-wal").exists()
    assert migrate_telegram_adapter() is None


def test_fresh_telegram_install_has_no_credentials(instance: Path) -> None:
    assert migrate_telegram_adapter() is None
    assert not (instance / "config/builtin_plugins/telegram_adapter/config.toml").exists()
