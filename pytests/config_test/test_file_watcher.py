from pathlib import Path

from watchfiles import Change

import asyncio
import pytest

from src.config.file_watcher import FileChange, FileWatcher


@pytest.mark.asyncio
async def test_dispatch_changes_with_path_and_change_type_filters(tmp_path: Path):
    watcher = FileWatcher(paths=[tmp_path])
    target_file = (tmp_path / "bot_config.toml").resolve()

    received: list[list[FileChange]] = []

    async def callback(changes):
        received.append(list(changes))

    watcher.subscribe(callback, paths=[target_file], change_types=[Change.modified])

    await watcher._dispatch_changes(
        [
            FileChange(change_type=Change.added, path=target_file),
            FileChange(change_type=Change.modified, path=target_file),
            FileChange(change_type=Change.modified, path=(tmp_path / "other.toml").resolve()),
        ]
    )

    assert len(received) == 1
    assert len(received[0]) == 1
    assert received[0][0].change_type == Change.modified
    assert received[0][0].path == target_file


@pytest.mark.asyncio
async def test_sync_callback_supported(tmp_path: Path):
    watcher = FileWatcher(paths=[tmp_path])
    target_file = (tmp_path / "model_config.toml").resolve()

    received_paths: list[Path] = []

    def sync_callback(changes):
        received_paths.extend(change.path for change in changes)

    watcher.subscribe(sync_callback, paths=[target_file])

    await watcher._dispatch_changes([FileChange(change_type=Change.modified, path=target_file)])

    assert received_paths == [target_file]


@pytest.mark.asyncio
async def test_callback_timeout_and_cooldown(tmp_path: Path):
    watcher = FileWatcher(
        paths=[tmp_path],
        callback_timeout_s=0.05,
        callback_failure_threshold=2,
        callback_cooldown_s=0.2,
    )
    target_file = (tmp_path / "bot_config.toml").resolve()

    async def slow_callback(changes):
        await asyncio.sleep(0.2)

    watcher.subscribe(slow_callback, paths=[target_file])

    await watcher._dispatch_changes([FileChange(change_type=Change.modified, path=target_file)])
    await watcher._dispatch_changes([FileChange(change_type=Change.modified, path=target_file)])

    stats_after_failures = watcher.stats
    assert stats_after_failures.callbacks_timed_out == 2
    assert stats_after_failures.callbacks_failed == 2

    await watcher._dispatch_changes([FileChange(change_type=Change.modified, path=target_file)])
    stats_after_cooldown_skip = watcher.stats
    assert stats_after_cooldown_skip.callbacks_skipped_cooldown >= 1


@pytest.mark.asyncio
async def test_start_requires_subscription(tmp_path: Path):
    watcher = FileWatcher(paths=[tmp_path])

    with pytest.raises(RuntimeError):
        await watcher.start()


@pytest.mark.asyncio
async def test_unsubscribe_stops_dispatch(tmp_path: Path):
    watcher = FileWatcher(paths=[tmp_path])
    target_file = (tmp_path / "bot_config.toml").resolve()

    calls = 0

    async def callback(changes):
        nonlocal calls
        calls += 1

    subscription_id = watcher.subscribe(callback, paths=[target_file])
    assert watcher.unsubscribe(subscription_id) is True

    await watcher._dispatch_changes([FileChange(change_type=Change.modified, path=target_file)])

    assert calls == 0
