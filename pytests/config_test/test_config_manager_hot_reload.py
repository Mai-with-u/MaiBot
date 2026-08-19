from pathlib import Path

from watchfiles import Change

import asyncio
import pytest

from src.config.config import ConfigManager
from src.config.file_watcher import FileChange, FileWatcherStats


@pytest.mark.asyncio
async def test_handle_file_changes_throttles_reload():
    manager = ConfigManager()
    manager._hot_reload_min_interval_s = 100.0

    called = 0

    async def reload_stub(changed_scopes=None, **kwargs) -> bool:
        nonlocal called
        called += 1
        return True

    manager.reload_config = reload_stub  # type: ignore[method-assign]
    changes = [FileChange(change_type=Change.modified, path=Path("/tmp/bot_config.toml"))]

    await manager._handle_file_changes(changes)
    await manager._handle_file_changes(changes)

    assert called == 1


@pytest.mark.asyncio
async def test_handle_file_changes_timeout_logged(caplog):
    manager = ConfigManager()
    manager._hot_reload_min_interval_s = 0.0
    manager._hot_reload_timeout_s = 0.01

    async def reload_stub(changed_scopes=None, **kwargs) -> bool:
        await asyncio.sleep(0.05)
        return True

    manager.reload_config = reload_stub  # type: ignore[method-assign]
    changes = [FileChange(change_type=Change.modified, path=Path("/tmp/model_config.toml"))]

    with caplog.at_level("ERROR"):
        await manager._handle_file_changes(changes)

    assert "配置热重载超时" in caplog.text


@pytest.mark.asyncio
async def test_handle_file_changes_empty_skips_reload():
    manager = ConfigManager()

    called = 0

    async def reload_stub(changed_scopes=None, **kwargs) -> bool:
        nonlocal called
        called += 1
        return True

    manager.reload_config = reload_stub  # type: ignore[method-assign]

    await manager._handle_file_changes([])

    assert called == 0


@pytest.mark.asyncio
async def test_handle_file_changes_skips_already_loaded_content(monkeypatch: pytest.MonkeyPatch):
    manager = ConfigManager()
    manager._hot_reload_min_interval_s = 0.0
    manager._config_file_fingerprints["model"] = "same-content"
    monkeypatch.setattr(manager, "_get_config_file_fingerprint", lambda path: "same-content")

    called = 0

    async def reload_stub(changed_scopes=None, **kwargs) -> bool:
        nonlocal called
        called += 1
        return True

    manager.reload_config = reload_stub  # type: ignore[method-assign]
    changes = [FileChange(change_type=Change.modified, path=Path("/tmp/model_config.toml"))]

    await manager._handle_file_changes(changes)

    assert called == 0
    assert manager._last_hot_reload_monotonic == 0


@pytest.mark.asyncio
async def test_queued_file_reload_rechecks_loaded_content_inside_lock(monkeypatch: pytest.MonkeyPatch):
    manager = ConfigManager()
    monkeypatch.setattr(manager, "_config_files_match_loaded_fingerprints", lambda scopes: True)

    assert await manager.reload_config(changed_scopes=["model"], skip_if_unchanged=True) is True
    assert manager.reload_revision == 0


class _FakeWatcher:
    def __init__(self):
        self.unsubscribe_called_with: str | None = None
        self.stop_called = False
        self.stats = FileWatcherStats(
            batches_seen=1,
            changes_seen=2,
            callbacks_succeeded=3,
            callbacks_failed=4,
            callbacks_timed_out=5,
            callbacks_skipped_cooldown=6,
            restart_count=7,
        )

    def unsubscribe(self, subscription_id: str) -> bool:
        self.unsubscribe_called_with = subscription_id
        return True

    async def stop(self) -> None:
        self.stop_called = True


@pytest.mark.asyncio
async def test_stop_file_watcher_cleans_state():
    manager = ConfigManager()
    fake_watcher = _FakeWatcher()
    manager._file_watcher = fake_watcher  # type: ignore[assignment]
    manager._file_watcher_subscription_id = "sub-1"

    await manager.stop_file_watcher()

    assert fake_watcher.unsubscribe_called_with == "sub-1"
    assert fake_watcher.stop_called is True
    assert manager._file_watcher is None
    assert manager._file_watcher_subscription_id is None
