from threading import Event
from unittest.mock import Mock

import asyncio

import pytest

from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.integration import PluginRuntimeManager
from src.plugin_runtime.protocol.envelope import RegisterPluginPayload


@pytest.mark.asyncio
async def test_watch_scan_uses_snapshot_during_plugin_registration_changes(tmp_path, monkeypatch):
    manager = PluginRuntimeManager()
    supervisor = PluginRunnerSupervisor(plugin_dirs=[tmp_path])
    manager._builtin_supervisor = supervisor
    watcher = Mock()
    manager._plugin_file_watcher = watcher
    original_paths = {plugin_id: tmp_path / plugin_id for plugin_id in ("first", "second")}
    manager._plugin_path_cache.update(original_paths)
    supervisor._registered_plugins.update({
        plugin_id: RegisterPluginPayload(plugin_id=plugin_id) for plugin_id in original_paths
    })
    monkeypatch.setattr(manager, "_iter_discovered_plugin_paths", lambda dirs: iter(()))
    entered = Event()
    release = Event()
    original_get_path = manager._get_plugin_path_for_supervisor

    def gated_get_path(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=3.0), "plugin scan was not released"
        return original_get_path(*args, **kwargs)

    monkeypatch.setattr(manager, "_get_plugin_path_for_supervisor", gated_get_path)
    refresh = asyncio.create_task(manager._refresh_plugin_config_watch_subscriptions())
    try:
        assert await asyncio.to_thread(entered.wait, 3.0)
        # 文件扫描暂停期间模拟主循环注销旧插件、注册新插件以及清理路径缓存。
        supervisor._registered_plugins.clear()
        supervisor._registered_plugins["new"] = RegisterPluginPayload(plugin_id="new")
        manager._plugin_path_cache.clear()
        release.set()
        await asyncio.wait_for(refresh, timeout=3.0)

        assert manager._plugin_path_cache == original_paths
        assert set(manager._plugin_config_watcher_subscriptions) == {"first", "second"}

        # 下一轮刷新应取得新名单，并移除旧插件的监听。
        manager._plugin_path_cache["new"] = tmp_path / "new"
        await manager._refresh_plugin_config_watch_subscriptions()
        assert manager._plugin_path_cache == {"new": tmp_path / "new"}
        assert set(manager._plugin_config_watcher_subscriptions) == {"new"}
        assert watcher.unsubscribe.call_count == 2
    finally:
        release.set()
        await asyncio.gather(refresh, return_exceptions=True)
