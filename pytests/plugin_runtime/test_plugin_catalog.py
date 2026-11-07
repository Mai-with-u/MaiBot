from pathlib import Path
from unittest.mock import Mock

import asyncio
import json

import pytest
from watchfiles import Change

from src.config.file_watcher import FileChange
from src.plugin_runtime import plugin_catalog as catalog_module
from src.plugin_runtime.integration import PluginRuntimeManager
from src.plugin_runtime.plugin_catalog import PluginCatalog
from src.webui.routers.plugin.management import _collect_installed_plugins


def _write_plugin(root: Path, folder: str, plugin_id: str, *, plugin_type: str = "extension") -> Path:
    path = root / folder
    path.mkdir()
    (path / "_manifest.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": folder,
                "version": "1.0.0",
                "plugin_type": plugin_type,
            }
        ),
        encoding="utf-8",
    )
    (path / "config.toml").write_text("[plugin]\nenabled = true\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_queries_read_only_published_metadata_and_live_state(tmp_path, monkeypatch):
    path = _write_plugin(tmp_path, "demo", "test.demo")
    manager = PluginRuntimeManager()
    manager._plugin_catalog._roots = {tmp_path: False}
    await manager._plugin_catalog.refresh()
    manager._started = True
    monkeypatch.setattr(manager, "get_plugin_load_statuses", lambda: {"test.demo": "timed_out"})
    monkeypatch.setattr(manager, "get_plugin_load_failure_reasons", lambda: {"test.demo": "still loading"})
    monkeypatch.setattr(catalog_module, "_read_entry", Mock(side_effect=AssertionError("query read metadata")))
    monkeypatch.setattr(Path, "resolve", Mock(side_effect=AssertionError("query resolved paths")))

    result = _collect_installed_plugins(await manager.get_plugin_state_snapshot())
    plugin = result["plugins"][0]
    assert plugin["path"] == str(path)
    assert plugin["load_status"] == "timed_out"
    assert plugin["load_error"] == "still loading"
    assert "changelog" not in plugin


@pytest.mark.asyncio
async def test_targeted_refresh_replaces_ids_and_removes_deleted_plugins(tmp_path, monkeypatch):
    first = _write_plugin(tmp_path, "first", "test.first")
    second = _write_plugin(tmp_path, "second", "test.second")
    catalog = PluginCatalog(Mock(), asyncio.Lock())
    catalog._roots = {tmp_path: False}
    await catalog.refresh()
    original_second = next(entry for entry in catalog.entries if entry.path == second)
    read_entry = Mock(wraps=catalog_module._read_entry)
    monkeypatch.setattr(catalog_module, "_read_entry", read_entry)
    (first / "_manifest.json").write_text(
        json.dumps({"id": "test.renamed", "name": "first", "version": "2.0.0"}), encoding="utf-8"
    )
    (first / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")
    await catalog.refresh([first])
    assert read_entry.call_count == 1
    assert next(entry for entry in catalog.entries if entry.path == second) is original_second
    changed = next(entry for entry in catalog.entries if entry.path == first)
    assert changed.plugin_id == "test.renamed"
    assert changed.enabled is False
    (first / "_manifest.json").unlink()
    await catalog.refresh([first])
    assert [entry.plugin_id for entry in catalog.entries] == ["test.second"]


@pytest.mark.asyncio
async def test_file_notifications_wait_for_file_transaction_without_blocking_callback(tmp_path):
    path = _write_plugin(tmp_path, "demo", "test.demo")
    lock = asyncio.Lock()
    catalog = PluginCatalog(Mock(), lock)
    catalog._roots = {tmp_path: False}
    await catalog.refresh()
    async with lock:
        (path / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")
        await asyncio.wait_for(catalog._handle_changes([FileChange(Change.modified, path / "config.toml")]), 0.5)
        await asyncio.sleep(0)
        assert catalog.entries[0].enabled is True
        assert not catalog._refresh_task.done()
    await catalog._refresh_task
    assert catalog.entries[0].enabled is False


@pytest.mark.asyncio
async def test_invalid_config_is_reported_and_can_be_repaired(tmp_path):
    path = _write_plugin(tmp_path, "demo", "test.demo")
    catalog = PluginCatalog(Mock(), asyncio.Lock())
    catalog._roots = {tmp_path: False}
    (path / "config.toml").write_text("[plugin\nenabled = true", encoding="utf-8")
    await catalog.refresh()
    assert catalog.entries[0].enabled is None
    assert "test.demo" in catalog.entries[0].metadata_error
    (path / "config.toml").write_text("[plugin]\nenabled = true\n", encoding="utf-8")
    await catalog.refresh_plugins(["test.demo"])
    assert catalog.entries[0].enabled is True
    assert not catalog.entries[0].metadata_error


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", ["null", "[]", "{broken json"])
async def test_corrupt_receipt_only_fails_the_affected_plugin(tmp_path, receipt):
    bad = _write_plugin(tmp_path, "bad", "test.bad")
    _write_plugin(tmp_path, "good", "test.good")
    (bad / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")
    (bad / catalog_module.RECEIPT_NAME).write_text(receipt, encoding="utf-8")
    manager = PluginRuntimeManager()
    manager._plugin_catalog._roots = {tmp_path: False}
    await manager._plugin_catalog.refresh()
    result = _collect_installed_plugins(await manager.get_plugin_state_snapshot())
    plugins = {plugin["id"]: plugin for plugin in result["plugins"]}
    assert plugins["test.bad"]["load_status"] == "failed"
    assert "安装记录失败" in plugins["test.bad"]["load_error"]
    assert plugins["test.good"]["load_status"] == "stopped"
    assert not plugins["test.good"]["load_error"]


@pytest.mark.asyncio
async def test_explicit_config_write_updates_catalog_before_returning(tmp_path):
    path = _write_plugin(tmp_path, "demo", "test.demo")
    manager = PluginRuntimeManager()
    manager._plugin_catalog._roots = {tmp_path: False}
    await manager._plugin_catalog.refresh()

    async def write_config():
        assert manager._plugin_file_update_lock.locked()
        (path / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")

    assert await manager.apply_plugin_config("test.demo", write_config) == "stopped"
    assert manager._plugin_catalog.entries[0].enabled is False


@pytest.mark.asyncio
async def test_file_transaction_updates_index_even_if_plugin_activation_fails(tmp_path):
    path = _write_plugin(tmp_path, "demo", "test.demo")
    manager = PluginRuntimeManager()
    manager._plugin_catalog._roots = {tmp_path: False}
    await manager._plugin_catalog.refresh()

    async def replace_files():
        assert manager._plugin_file_update_lock.locked()
        (path / "_manifest.json").write_text(
            json.dumps({"id": "test.demo", "name": "demo", "version": "2.0.0"}),
            encoding="utf-8",
        )
        raise RuntimeError("activation failed")

    with pytest.raises(RuntimeError, match="activation failed"):
        await manager.run_plugin_file_update(replace_files, plugin_paths=[path])
    assert manager._plugin_catalog.entries[0].manifest["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_unexpected_refresh_failure_is_not_hidden_as_stale_data(tmp_path, monkeypatch):
    _write_plugin(tmp_path, "demo", "test.demo")
    catalog = PluginCatalog(Mock(), asyncio.Lock())
    catalog._roots = {tmp_path: False}
    await catalog.refresh()
    with monkeypatch.context() as patch:
        patch.setattr(catalog_module, "_read_entry", Mock(side_effect=RuntimeError("unexpected failure")))
        with pytest.raises(RuntimeError, match="unexpected failure"):
            await catalog.refresh()
        with pytest.raises(RuntimeError, match="索引更新失败"):
            _ = catalog.entries
    await catalog.refresh([tmp_path / "demo"])
    with pytest.raises(RuntimeError, match="索引更新失败"):
        _ = catalog.entries
    await catalog.refresh()
    assert [entry.plugin_id for entry in catalog.entries] == ["test.demo"]


@pytest.mark.asyncio
async def test_metadata_watcher_tracks_directory_move_and_stops_cleanly(tmp_path):
    catalog = PluginCatalog(Mock(), asyncio.Lock())
    await catalog.start([], [tmp_path])
    try:
        path = _write_plugin(tmp_path, "demo", "test.demo")
        # 使用确定的目录事件，覆盖整体移入时没有逐文件事件的安装方式。
        await catalog._handle_changes([FileChange(Change.added, path)])
        await catalog._refresh_task
        assert [entry.plugin_id for entry in catalog.entries] == ["test.demo"]
        assert catalog._watcher.running
    finally:
        await catalog.stop()
    assert catalog._watcher is None
