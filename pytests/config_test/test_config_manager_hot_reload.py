from pathlib import Path
from types import SimpleNamespace

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


def test_section_matches_by_dotted_prefix():
    assert ConfigManager._section_matches("chat", "chat.reply_timing")
    assert ConfigManager._section_matches("chat.reply_timing", "chat")
    assert ConfigManager._section_matches("a_memorix", "a_memorix")
    assert not ConfigManager._section_matches("webui", "chat")
    assert not ConfigManager._section_matches("cha", "chat")


def _make_reload_test_manager(monkeypatch: pytest.MonkeyPatch, new_global_config) -> ConfigManager:
    """构造不落盘的 ConfigManager，热重载直接换内存配置对象。"""

    from src.config.config import Config

    manager = ConfigManager()
    manager.global_config = Config()
    monkeypatch.setattr(manager, "_update_config_file_fingerprints", lambda scopes: None)

    def fake_load(config_class, path, new_ver, override_repr=False):
        return (new_global_config, False) if config_class is Config else (SimpleNamespace(), False)

    monkeypatch.setattr("src.config.config.load_config_from_file", fake_load)
    return manager


@pytest.mark.asyncio
async def test_reload_config_notifies_section_callback_only_on_match(monkeypatch: pytest.MonkeyPatch):
    from src.config.config import Config

    new_config = Config()
    new_config.webui.anti_crawler_mode = "strict"
    manager = _make_reload_test_manager(monkeypatch, new_config)

    notified_all: list[int] = []
    notified_webui: list[int] = []
    notified_a_memorix: list[int] = []

    async def cb_all():
        notified_all.append(1)

    async def cb_webui():
        notified_webui.append(1)

    async def cb_a_memorix():
        notified_a_memorix.append(1)

    manager.register_reload_callback(cb_all)
    manager.register_reload_callback(cb_webui, sections=("webui",))
    manager.register_reload_callback(cb_a_memorix, sections=("a_memorix",))

    assert await manager.reload_config(changed_scopes=["bot"]) is True

    # 自动 diff 识别出 webui 节变化：不限节的回调与 webui 回调被通知，a_memorix 回调跳过
    assert notified_all == [1]
    assert notified_webui == [1]
    assert notified_a_memorix == []


@pytest.mark.asyncio
async def test_reload_config_respects_explicit_changed_sections(monkeypatch: pytest.MonkeyPatch):
    from src.config.config import Config

    manager = _make_reload_test_manager(monkeypatch, Config())

    notified_all: list[int] = []
    notified_a_memorix: list[int] = []

    async def cb_all():
        notified_all.append(1)

    async def cb_a_memorix():
        notified_a_memorix.append(1)

    manager.register_reload_callback(cb_all)
    manager.register_reload_callback(cb_a_memorix, sections=("a_memorix",))

    assert await manager.reload_config(changed_scopes=["bot"], changed_sections=["chat.reply_timing"]) is True
    assert notified_all == [1]
    assert notified_a_memorix == []

    assert await manager.reload_config(changed_scopes=["bot"], changed_sections=["a_memorix"]) is True
    assert notified_a_memorix == [1]


@pytest.mark.asyncio
async def test_reload_config_auto_diff_without_change_skips_section_callbacks(
    monkeypatch: pytest.MonkeyPatch,
):
    from src.config.config import Config

    manager = _make_reload_test_manager(monkeypatch, Config())

    notified_all: list[int] = []
    notified_webui: list[int] = []

    async def cb_all():
        notified_all.append(1)

    async def cb_webui():
        notified_webui.append(1)

    manager.register_reload_callback(cb_all)
    manager.register_reload_callback(cb_webui, sections=("webui",))

    assert await manager.reload_config(changed_scopes=["bot"]) is True

    # 新旧配置无差异：节限定回调不通知，不限节回调保持原有行为
    assert notified_all == [1]
    assert notified_webui == []


@pytest.mark.asyncio
async def test_reload_config_model_scope_skips_bot_section_callbacks(monkeypatch: pytest.MonkeyPatch):
    from src.config.config import Config

    manager = _make_reload_test_manager(monkeypatch, Config())
    manager.model_config = SimpleNamespace()  # type: ignore[assignment]

    notified_all: list[int] = []
    notified_webui: list[int] = []

    async def cb_all():
        notified_all.append(1)

    async def cb_webui():
        notified_webui.append(1)

    manager.register_reload_callback(cb_all)
    manager.register_reload_callback(cb_webui, sections=("webui",))

    assert await manager.reload_config(changed_scopes=["model"]) is True

    assert notified_all == [1]
    assert notified_webui == []
