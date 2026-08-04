import json
from pathlib import Path
from typing import List

import pytest

from src.plugin_runtime.integration import PluginRuntimeManager
from src.plugin_runtime.runner.plugin_loader import PluginLoader


def _write_plugin(
    root: Path,
    directory_name: str,
    plugin_id: str,
    dependencies: List[str] | None = None,
) -> Path:
    plugin_dir = root / directory_name
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
    (plugin_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "version": "1.0.0",
                "name": directory_name,
                "description": directory_name,
                "author": {"name": "MaiBot", "url": "https://example.com"},
                "license": "GPL-v3.0-or-later",
                "urls": {"repository": "https://example.com/repo"},
                "host_application": {"min_version": "1.0.0", "max_version": "9.9.9"},
                "sdk": {"min_version": "2.0.0", "max_version": "9.9.9"},
                "dependencies": [
                    {"type": "plugin", "id": dependency_id, "version_spec": ">=1.0.0"}
                    for dependency_id in dependencies or []
                ],
                "capabilities": [],
                "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
                "id": plugin_id,
                "plugin_type": "extension",
            }
        ),
        encoding="utf-8",
    )
    return plugin_dir


@pytest.mark.asyncio
async def test_duplicate_plugin_ids_are_isolated_without_blocking_unrelated_plugins(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    first_duplicate = _write_plugin(plugin_root, "duplicate_a", "test.duplicate")
    second_duplicate = _write_plugin(plugin_root, "duplicate_b", "test.duplicate")
    _write_plugin(plugin_root, "dependent", "test.dependent", dependencies=["test.duplicate"])
    _write_plugin(plugin_root, "unrelated", "test.unrelated")

    manager = PluginRuntimeManager()
    await manager._sync_plugin_dependencies([plugin_root])

    statuses = manager.get_plugin_load_statuses()
    failure_reasons = manager.get_plugin_load_failure_reasons()
    assert statuses["test.duplicate"] == "failed"
    assert "插件 ID 重复" in failure_reasons["test.duplicate"]
    assert str(first_duplicate) in failure_reasons["test.duplicate"]
    assert str(second_duplicate) in failure_reasons["test.duplicate"]

    loader = PluginLoader(plugin_type_filter="not_adapter")
    loader.set_blocked_plugin_reasons(failure_reasons)
    candidates, duplicate_candidates = loader.discover_candidates([str(plugin_root)])
    load_order, failed_dependencies = loader.resolve_dependencies(candidates)

    assert duplicate_candidates == {}
    assert set(load_order) == {"test.unrelated"}
    assert failed_dependencies["test.dependent"] == "依赖未满足: test.duplicate (未找到依赖插件)"
    assert loader.failed_plugins["test.duplicate"] == failure_reasons["test.duplicate"]


def test_startup_cleanup_removes_empty_plugin_directories_only(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    empty_plugin_dir = plugin_root / "empty_plugin"
    empty_plugin_dir.mkdir()
    normal_plugin_dir = _write_plugin(plugin_root, "normal_plugin", "test.normal")

    removed_paths = PluginRuntimeManager._cleanup_plugin_load_residue_dirs([plugin_root])

    assert removed_paths == [empty_plugin_dir]
    assert not empty_plugin_dir.exists()
    assert normal_plugin_dir.is_dir()
