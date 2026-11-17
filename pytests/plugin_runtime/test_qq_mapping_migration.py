from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock

import json
import sqlite3

import pytest

from src.plugins.built_in.qq_official_adapter import plugin as plugin_module
from src.plugins.built_in.qq_official_adapter.mapping_migration import disable_mapping, migration_candidates
from src.plugins.built_in.qq_official_adapter.plugin import QQOfficialAdapterPlugin
from src.plugins.built_in.qq_official_adapter.settings import AdapterSettings


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    with sqlite3.connect(tmp_path / "data/MaiBot.db") as database:
        for table in ("person_info", "mai_messages"):
            database.execute(f"CREATE TABLE {table} (platform TEXT, user_id TEXT)")
        database.execute("INSERT INTO person_info VALUES ('qq', '123')")
        database.execute("INSERT INTO mai_messages VALUES ('qq', '456')")
    return tmp_path


def row(source: str, target: str, conflict: bool = False) -> Dict[str, Any]:
    return {"id": source, "openid": target, "source": "manual", "conflict": conflict}


def test_candidates_require_explicit_target_and_preserve_conflicts(root: Path) -> None:
    items = migration_candidates(root, [row("123", "a"), row("123", "b"), row("456", "c", True), row("789", "d")])
    assert items[0]["targets"] == ["a", "b"]
    assert items[0]["target"] == ""
    assert [item["status"] for item in items] == ["pending", "conflict", "empty"]


def test_completion_requires_matching_alias_and_no_remaining_source_data(root: Path) -> None:
    aliases = root / "data/person_identity_aliases.json"
    aliases.write_text(json.dumps({"version": 1, "aliases": {"qq": {"123": "a", "456": "unrelated"}}, "private_routes": []}), encoding="utf-8")
    rows = [row("123", "a"), row("456", "c")]
    assert migration_candidates(root, rows)[0]["status"] == "conflict"
    with sqlite3.connect(root / "data/MaiBot.db") as database:
        database.execute("DELETE FROM person_info WHERE user_id = '123'")
    items = migration_candidates(root, rows)
    assert items[0]["status"] == "migrated"
    assert items[1]["status"] == "conflict"


def test_disable_preserves_sync_and_credentials(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[qq_official]\nenable_mapping = true\nsilent_user_sync = true\napp_id = "app"\n', encoding="utf-8")
    result = disable_mapping(path)
    assert result["qq_official"] == {"enable_mapping": False, "silent_user_sync": True, "app_id": "app"}


@pytest.mark.asyncio
async def test_finish_checks_backend_progress_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = QQOfficialAdapterPlugin()
    adapter._plugin_config_instance = AdapterSettings()
    adapter.config.qq_official.enable_mapping = True
    adapter.webui_mapping_migrations = AsyncMock(return_value={"candidates": [{"status": "pending"}]})
    path = tmp_path / "config.toml"
    path.write_text('[plugin]\nconfig_version = "0.5.2"\n[qq_official]\nenable_mapping = true\n', encoding="utf-8")
    monkeypatch.setattr(plugin_module, "resolve_plugin_config_path", lambda _: path)
    with pytest.raises(ValueError, match="未迁移"):
        await adapter.webui_finish_mapping()
    assert "true" in path.read_text(encoding="utf-8")
    adapter.webui_mapping_migrations.return_value = {"candidates": [{"status": "migrated"}]}
    await adapter.webui_finish_mapping()
    assert adapter.config.qq_official.enable_mapping is False
    assert "false" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_mode_switch_persists_without_migrating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = QQOfficialAdapterPlugin()
    adapter._plugin_config_instance = AdapterSettings()
    path = tmp_path / "config.toml"
    path.write_text('[plugin]\nconfig_version = "0.5.2"\n[qq_official]\nenable_mapping = false\nsilent_user_sync = true\n', encoding="utf-8")
    monkeypatch.setattr(plugin_module, "resolve_plugin_config_path", lambda _: path)
    adapter.webui_mapping_migrations = AsyncMock()
    for enabled in (True, False):
        assert await adapter.webui_set_mapping_mode(enabled) == {"mapping_enabled": enabled}
        assert adapter.config.qq_official.enable_mapping is enabled
        assert adapter.config.qq_official.silent_user_sync is True
        assert f"enable_mapping = {str(enabled).lower()}" in path.read_text(encoding="utf-8")
    adapter.webui_mapping_migrations.assert_not_called()


def test_interrupted_binding_delete_recovers_before_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.built_in.qq_official_adapter import binding_store

    contents = {name: "[]" if name == "auto_user_profiles.json" else "{}" for name in binding_store.FILES}
    original = binding_store._write
    calls = 0

    def fail_after_first_file(path: Path, content: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("disk failure")
        original(path, content)

    monkeypatch.setattr(binding_store, "_write", fail_after_first_file)
    with pytest.raises(OSError, match="disk failure"):
        binding_store.commit_binding_delete(tmp_path, contents)
    assert (tmp_path / binding_store.JOURNAL).exists()
    monkeypatch.setattr(binding_store, "_write", original)
    binding_store.recover_binding_delete(tmp_path)
    assert not (tmp_path / binding_store.JOURNAL).exists()
    for name, content in contents.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == content
