import json
from pathlib import Path

from src.plugin_runtime.runner.plugin_loader import PluginLoader


def _write_plugin(root: Path, name: str, plugin_type: str) -> Path:
    return _write_plugin_with_type_key(root, name, plugin_type, "plugin_type")


def _write_plugin_with_type_key(root: Path, name: str, plugin_type: str, type_key: str) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
    (plugin_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "version": "1.0.0",
                "name": name,
                "description": name,
                "author": {"name": "MaiBot", "url": "https://example.com"},
                "license": "GPL-v3.0-or-later",
                "urls": {"repository": "https://example.com/repo"},
                "host_application": {"min_version": "1.0.0", "max_version": "1.1.99"},
                "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"},
                "dependencies": [],
                "capabilities": [],
                "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
                "id": f"test.{name}",
                type_key: plugin_type,
            }
        ),
        encoding="utf-8",
    )
    return plugin_dir


def test_plugin_loader_filters_adapters_for_builtin_runtime(tmp_path: Path) -> None:
    builtin_root = tmp_path / "built_in"
    third_party_root = tmp_path / "plugins"
    builtin_root.mkdir()
    third_party_root.mkdir()
    _write_plugin(builtin_root, "plugin-management", "extension")
    _write_plugin(third_party_root, "snowluma-adapter", "adapter")
    _write_plugin(third_party_root, "normal-plugin", "extension")

    loader = PluginLoader(
        plugin_type_filter="trusted_or_adapter",
        trusted_plugin_dirs=[str(builtin_root)],
    )
    candidates, duplicates = loader.discover_candidates([str(builtin_root), str(third_party_root)])

    assert duplicates == {}
    assert set(candidates) == {"test.plugin-management", "test.snowluma-adapter"}


def test_plugin_loader_skips_adapters_for_third_party_runtime(tmp_path: Path) -> None:
    third_party_root = tmp_path / "plugins"
    third_party_root.mkdir()
    _write_plugin(third_party_root, "snowluma-adapter", "adapter")
    _write_plugin(third_party_root, "normal-plugin", "extension")

    loader = PluginLoader(plugin_type_filter="not_adapter")
    candidates, duplicates = loader.discover_candidates([str(third_party_root)])

    assert duplicates == {}
    assert set(candidates) == {"test.normal-plugin"}


def test_plugin_loader_accepts_manifest_type_alias(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    _write_plugin_with_type_key(plugin_root, "alias-adapter", "adapter", "type")

    loader = PluginLoader(plugin_type_filter="adapter")
    candidates, duplicates = loader.discover_candidates([str(plugin_root)])

    assert duplicates == {}
    assert set(candidates) == {"test.alias-adapter"}
