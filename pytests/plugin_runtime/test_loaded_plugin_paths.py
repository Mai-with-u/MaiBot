from pathlib import Path

import pytest

from src.plugin_runtime.integration import PluginRuntimeManager


def test_get_loaded_plugin_paths_deduplicates_plugins_shared_by_supervisors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_path = tmp_path / "hello_world_plugin"
    plugin_path.mkdir()
    manager = PluginRuntimeManager()

    monkeypatch.setattr(
        manager,
        "get_plugin_load_statuses",
        lambda: {"example.hello": "success"},
    )
    monkeypatch.setattr(
        manager,
        "_iter_plugin_dirs",
        lambda: iter((tmp_path, tmp_path)),
    )
    monkeypatch.setattr(
        manager,
        "_iter_discovered_plugin_paths",
        lambda _plugin_dirs: iter(
            (
                ("example.hello", plugin_path),
                ("example.hello", plugin_path),
            )
        ),
    )

    assert manager.get_loaded_plugin_paths() == [("example.hello", plugin_path)]
