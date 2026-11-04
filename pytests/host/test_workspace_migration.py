"""发布前统一迁移：旧单实例一次升级，失败不留下部分公共配置。"""
from pathlib import Path

import json
import pytest
import tomlkit

from src.common.process_lease import ProcessLease
from src.config.shared_models import CATALOG_VERSION
from src.config.shared_settings import merge_shared_settings
from src.host import config_transaction
from src.host.config import HostConfig, save_host_config
from src.host.layout import create_bot, open_workspace
from src.host.registry import Registry
from src.host.workspace_migration import migrate_workspace_config


def old_workspace(root: Path):
    save_host_config(root, HostConfig())
    registry = Registry(root)
    registry.create()
    directory = root / "old-bot"
    config = directory / "config"
    config.mkdir(parents=True)
    bot = registry.add_bot("old", directory, owned=False, initialization="initialized")
    # v1.3.19 的 bot/model 配置版本，包含各类待迁入公共配置的字段。
    (config / "bot_config.toml").write_text(tomlkit.dumps({
        "inner": {"version": "8.15.68"},
        "webui": {"webui_style": 1, "enforce_public_outbound_url": False},
        "files": {"transfer_base_url": "https://example.com"},
        "maim_message": {"ws_server_port": 8000, "auth_token": ["keep"]},
        "visual": {"image_cache_cleanup": {"enabled": False, "image_file_retention_days": 90}},
        "log": {"log_cleanup_days": 60, "console_log_level": "DEBUG"},
    }), encoding="utf-8")
    (config / "model_config.toml").write_text(tomlkit.dumps({
        "inner": {"version": "1.17.25"},
        "api_providers": [{"name": "provider", "base_url": "https://example.com/v1", "api_key": "secret"}],
        "models": [{"name": "model", "api_provider": "provider", "model_identifier": "model", "model_type": "llm"}],
        "web": {"services": {"search": {"adapter": "tavily", "api_key": "search-secret"}}},
        "model_task_config": {"planner": {"model_list": ["model"]}, "web_search": {"model_list": ["search"]}},
    }), encoding="utf-8")
    return bot


def test_1319_migration_is_one_commit_and_one_backup(tmp_path, monkeypatch):
    bot = old_workspace(tmp_path)
    calls = []
    real_commit = config_transaction.commit_config_files

    def commit(host, files):
        calls.append(set(files))
        return real_commit(host, files)

    monkeypatch.setattr("src.host.workspace_migration.commit_config_files", commit)
    migrate_workspace_config(tmp_path, [bot])
    assert len(calls) == 1 and len(calls[0]) == 4
    host = tomlkit.parse((tmp_path / "host/config.toml").read_text(encoding="utf-8"))
    assert host["webui"]["public_base_url"] == "https://example.com"
    assert host["webui"]["webui_style"] == 1
    assert host["webui"]["enforce_public_outbound_url"] is False
    assert host["retention"]["images"]["enabled"] is False
    assert host["retention"]["logs"]["log_cleanup_days"] == 60
    local = tomlkit.parse((bot.directory / "config/bot_config.toml").read_text(encoding="utf-8"))
    assert "webui" not in local
    monkeypatch.setenv("MAIBOT_SHARED_MODEL_CATALOG", str(tmp_path / "host/model_catalog.toml"))
    assert merge_shared_settings(local.unwrap())["webui"] == {
        "webui_style": 1, "enforce_public_outbound_url": False,
    }
    assert local["maim_message"] == {"auth_token": ["keep"]}
    assert "image_cache_cleanup" not in local.get("visual", {})
    shared = tomlkit.parse((tmp_path / "host/model_catalog.toml").read_text(encoding="utf-8"))
    assert shared["api_providers"][0]["api_key"] == "secret"
    assert shared["web"]["services"]["search"]["api_key"] == "search-secret"
    [backup] = (tmp_path / "host/backups").iterdir()
    assert len(json.loads((backup / "manifest.json").read_text(encoding="utf-8"))) == 4
    migrate_workspace_config(tmp_path, [bot])
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["validation", "write"])
def test_failure_preserves_all_original_files(tmp_path, monkeypatch, failure):
    bot = old_workspace(tmp_path)
    model = bot.directory / "config/model_config.toml"
    if failure == "validation":
        model.write_text(model.read_text(encoding="utf-8").replace('api_provider = "provider"', 'api_provider = "missing"'), encoding="utf-8")
    originals = {path: path.read_bytes() for path in [model, bot.directory / "config/bot_config.toml", tmp_path / "host/config.toml"]}
    if failure == "write":
        real_write = config_transaction.write_private
        failed = False

        def write(path, content):
            nonlocal failed
            if path == model and not failed:
                failed = True
                raise OSError("simulated disk failure")
            return real_write(path, content)

        monkeypatch.setattr(config_transaction, "write_private", write)
    with pytest.raises((ValueError, OSError)):
        migrate_workspace_config(tmp_path, [bot])
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert not (tmp_path / "host/model_catalog.toml").exists()
    assert not (tmp_path / "host/model-config-transaction.json").exists()


def test_running_host_is_rejected_before_migration(tmp_path):
    old_workspace(tmp_path)
    with ProcessLease(tmp_path / "host/run.lock"):
        with pytest.raises(RuntimeError, match="租约"):
            open_workspace(tmp_path)
    assert not (tmp_path / "host/backups").exists()


def test_new_instances_write_final_config_without_migration(tmp_path):
    workspace = open_workspace(tmp_path)
    catalog = tmp_path / "host/model_catalog.toml"
    original = catalog.read_bytes()
    create_bot(workspace, "second")
    assert catalog.read_bytes() == original
    assert tomlkit.parse(original.decode())["inner"]["version"] == CATALOG_VERSION
    assert not (tmp_path / "host/backups").exists()
    for bot in workspace.registry.list_bots():
        config = tomlkit.parse((bot.directory / "config/bot_config.toml").read_text(encoding="utf-8"))
        model = tomlkit.parse((bot.directory / "config/model_config.toml").read_text(encoding="utf-8"))
        assert "image_cache_cleanup" not in config.get("visual", {})
        assert "cache_cleanup" not in config.get("emoji", {})
        assert "log_cleanup_days" not in config["log"]
        assert "webui" not in config
        assert "models" not in model and "api_providers" not in model
        assert "services" not in model.get("web", {})
