"""通过 Runner 的真实读取、迁移和落盘流程验证旧配置不会提前被裁剪。"""

from copy import deepcopy
from maibot_sdk import Field, MaiBotPlugin, PluginConfigBase
from pathlib import Path
from pydantic import model_validator
from types import SimpleNamespace
from typing import Any, Dict

import pytest
import tomllib
import tomlkit

from src.plugin_runtime.runner.runner_main import PluginRunner


class VersionSection(PluginConfigBase):
    config_version: str = "2.8.0"


class ClientSection(PluginConfigBase):
    server: str = "127.0.0.1"
    port: int = 3001
    token: str = ""


class AbilitiesSection(PluginConfigBase):
    enable_poke_tool: bool = False


class MigratingConfig(PluginConfigBase):
    plugin: VersionSection = Field(default_factory=VersionSection)
    client: ClientSection = Field(default_factory=ClientSection)
    chat_abilities: AbilitiesSection = Field(default_factory=AbilitiesSection)

    @model_validator(mode="before")
    @classmethod
    def migrate(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
        data = deepcopy(raw)
        old_client = data.pop("luma_client", None)
        if old_client is not None:
            data.setdefault("client", {}).update(old_client)
        if "enable_poke_tool" in data.get("plugin", {}):
            data.setdefault("chat_abilities", {})["enable_poke_tool"] = data["plugin"].pop("enable_poke_tool")
        return data


class MigratingPlugin(MaiBotPlugin):
    config_model = MigratingConfig


def _legacy_config() -> Dict[str, Any]:
    return {
        "plugin": {"config_version": "2.7.1", "enabled": False, "enable_poke_tool": True},
        "luma_client": {"server": "192.0.2.10", "port": 3456, "token": "test-only-token"},
        "removed_option": "discard after migration",
    }


def test_upgrade_migrates_before_pruning_and_persists_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(tomlkit.dumps(_legacy_config()), encoding="utf-8")
    plugin = MigratingPlugin()
    runner = PluginRunner("unused", "test", [str(tmp_path)])
    meta = SimpleNamespace(instance=plugin, plugin_dir=str(tmp_path), plugin_id="test.migration")

    result = runner._apply_plugin_config(meta)
    saved = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert saved == result
    assert saved["client"] == _legacy_config()["luma_client"]
    assert saved["chat_abilities"]["enable_poke_tool"] is True
    assert saved["plugin"] == {"config_version": "2.8.0", "enabled": False}
    assert "luma_client" not in saved
    assert "removed_option" not in saved
    assert plugin.config.client.token == "test-only-token"
    # 第二次加载不再重建，也不覆盖已迁移的值。
    first_bytes = config_path.read_bytes()
    assert runner._apply_plugin_config(meta) == result
    assert config_path.read_bytes() == first_bytes


def test_invalid_migrated_value_does_not_overwrite_original_file(tmp_path: Path) -> None:
    raw = _legacy_config()
    raw["luma_client"]["port"] = "invalid-port"
    config_path = tmp_path / "config.toml"
    config_path.write_text(tomlkit.dumps(raw), encoding="utf-8")
    original = config_path.read_bytes()
    runner = PluginRunner("unused", "test", [str(tmp_path)])
    plugin = MigratingPlugin()
    meta = SimpleNamespace(instance=plugin, plugin_dir=str(tmp_path), plugin_id="test.migration")
    with pytest.raises(ValueError):
        runner._apply_plugin_config(meta)
    assert config_path.read_bytes() == original
    result = runner._normalize_plugin_config(plugin, raw)
    assert result.config_error
    assert not result.should_persist


def test_unversioned_config_is_rejected_before_migration() -> None:
    runner = PluginRunner("unused", "test", [])
    raw = _legacy_config()
    raw["plugin"].pop("config_version")
    result = runner._normalize_plugin_config(MigratingPlugin(), raw)
    assert result.config_error
    assert not result.should_persist


def test_plugin_without_migration_still_rebuilds_current_fields() -> None:
    class PlainPlugin:
        def get_default_config(self) -> Dict[str, Any]:
            return {"plugin": {"config_version": "2.0.0"}, "port": 3001}

    runner = PluginRunner("unused", "test", [])
    result = runner._normalize_plugin_config(
        PlainPlugin(), {"plugin": {"config_version": "1.0.0", "enabled": False}, "port": 3456, "old": True},
    )
    assert result.normalized_config == {"plugin": {"config_version": "2.0.0", "enabled": False}, "port": 3456}
    assert result.should_persist
