from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import json

import pytest

from src.plugin_runtime import integration as integration_module
from src.plugin_runtime.builtin_adapter_migration import PLUGIN_ID, migrate_qq_official_adapter


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))
    (tmp_path / "plugins").mkdir()
    return tmp_path


def _install(instance: Path, folder: str, version: str) -> Path:
    plugin = instance / "plugins" / folder
    plugin.mkdir()
    (plugin / "_manifest.json").write_text(json.dumps({"id": PLUGIN_ID, "version": version}), encoding="utf-8")
    (plugin / "plugin.py").write_text("# old adapter\n", encoding="utf-8")
    (plugin / "config.toml").write_bytes(
        b'# keep comment\r\n[plugin]\r\nenabled = false\r\n[qq_official]\r\napp_secret = "old-secret"\r\n'
    )
    return plugin


@pytest.mark.parametrize("folder,version", [
    ("qq_official_adapter", "0.3.0"),
    ("Mai-with-u_qq-adapter", "0.4.1"),
    (PLUGIN_ID, "0.4.4"),
    ("custom-folder", "0.4.6"),
])
def test_old_versions_migrate_config_and_keep_data(instance: Path, folder: str, version: str) -> None:
    source = _install(instance, folder, version)
    expected = (source / "config.toml").read_bytes()
    data = instance / "data" / "plugins" / PLUGIN_ID
    data.mkdir(parents=True)
    (data / "id_map.json").write_bytes(b'{"group":{"openid":"123"}}')
    (data / "auto_user_profiles.json").write_bytes(b'{"user":"name"}')

    backup = migrate_qq_official_adapter()

    assert backup is not None and backup.parent == instance / "plugins" / ".builtin-backups"
    assert not source.exists()  # 旧外部插件已经卸载出扫描目录。
    assert (backup / "plugin.py").is_file()
    assert (backup / "config.toml").read_bytes() == expected
    assert (instance / "config/builtin_plugins/qq_official_adapter/config.toml").read_bytes() == expected
    assert (data / "id_map.json").read_bytes() == b'{"group":{"openid":"123"}}'
    assert (data / "auto_user_profiles.json").read_bytes() == b'{"user":"name"}'
    assert migrate_qq_official_adapter() is None


def test_existing_builtin_config_is_not_overwritten(instance: Path) -> None:
    _install(instance, "qq_official_adapter", "0.4.4")
    config = instance / "config/builtin_plugins/qq_official_adapter/config.toml"
    config.parent.mkdir(parents=True)
    config.write_bytes(b'[plugin]\nenabled = true\n')
    migrate_qq_official_adapter()
    assert config.read_bytes() == b'[plugin]\nenabled = true\n'


def test_legacy_docker_data_migrates_once(instance: Path) -> None:
    _install(instance, "qq_official_adapter", "0.4.4")
    old_data = instance / "plugins" / PLUGIN_ID
    old_data.mkdir()
    (old_data / "id_map.json").write_bytes(b'{"group":{"openid":"123"}}')
    migrate_qq_official_adapter()
    data = instance / "data" / "plugins" / PLUGIN_ID / "id_map.json"
    assert data.read_bytes() == b'{"group":{"openid":"123"}}'
    assert not old_data.exists()
    data.write_bytes(b'{"group":{"openid":"456"}}')
    assert migrate_qq_official_adapter() is None
    assert data.read_bytes() == b'{"group":{"openid":"456"}}'


def test_conflicting_data_does_not_uninstall_old_plugin(instance: Path) -> None:
    plugin = _install(instance, "qq_official_adapter", "0.4.4")
    for base, content in [(instance / "plugins", b"old"), (instance / "data/plugins", b"new")]:
        data = base / PLUGIN_ID
        data.mkdir(parents=True)
        (data / "id_map.json").write_bytes(content)
    with pytest.raises(ValueError, match="新旧数据冲突"):
        migrate_qq_official_adapter()
    assert plugin.exists()
    assert (instance / "plugins" / PLUGIN_ID / "id_map.json").read_bytes() == b"old"
    assert (instance / "data/plugins" / PLUGIN_ID / "id_map.json").read_bytes() == b"new"


def test_duplicate_installations_are_reported(instance: Path) -> None:
    first = _install(instance, "first", "0.3.0")
    second = _install(instance, "second", "0.4.4")
    with pytest.raises(ValueError, match="多个旧安装"):
        migrate_qq_official_adapter()
    assert first.exists() and second.exists()


def test_invalid_config_keeps_old_installation(instance: Path) -> None:
    source = _install(instance, "qq_official_adapter", "0.3.0")
    (source / "config.toml").write_text("[invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        migrate_qq_official_adapter()
    assert source.exists()


def test_fresh_install_does_not_create_credentials(instance: Path) -> None:
    assert migrate_qq_official_adapter() is None
    assert not (instance / "config/builtin_plugins/qq_official_adapter/config.toml").exists()


@pytest.mark.asyncio
async def test_corrupt_legacy_adapter_does_not_prevent_catalog_start(instance, monkeypatch):
    source = _install(instance, "qq_official_adapter", "0.4.4")
    (source / "config.toml").write_bytes(bytes(32))
    manager = integration_module.PluginRuntimeManager()
    monkeypatch.setattr(integration_module.config_manager.get_global_config().plugin_runtime, "enabled", False)
    pipeline = AsyncMock(side_effect=lambda _, initial_blocked_plugin_reasons: SimpleNamespace(
        blocked_plugin_reasons=initial_blocked_plugin_reasons, pending_plugin_ids=set(),
    ))
    monkeypatch.setattr(manager._plugin_dependency_pipeline, "execute", pipeline)

    try:
        await manager.start()
        await manager._sync_plugin_dependencies([instance / "plugins"])
        snapshot = await manager.get_plugin_state_snapshot()
        assert any(entry.plugin_id == PLUGIN_ID for entry in snapshot["plugins"])
        assert snapshot["statuses"][PLUGIN_ID] == "failed"
        assert "旧配置无法解析" in snapshot["failure_reasons"][PLUGIN_ID]
        assert (source / "config.toml").read_bytes() == bytes(32)
    finally:
        await manager.stop()
