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


def _make_plugin_host_incompatible(plugin_dir: Path, *, omit_plugin_type: bool = False) -> None:
    manifest_path = plugin_dir / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["host_application"]["max_version"] = "1.0.0"
    if omit_plugin_type:
        manifest.pop("plugin_type", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


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


def test_incompatible_extension_manifest_is_only_validated_by_third_party_runtime(tmp_path: Path) -> None:
    third_party_root = tmp_path / "plugins"
    third_party_root.mkdir()
    plugin_dir = _write_plugin(third_party_root, "normal-plugin", "extension")
    _make_plugin_host_incompatible(plugin_dir)

    builtin_loader = PluginLoader(
        host_version="1.1.0",
        plugin_type_filter="trusted_or_adapter",
        trusted_plugin_dirs=[],
    )
    third_party_loader = PluginLoader(host_version="1.1.0", plugin_type_filter="not_adapter")

    builtin_loader.discover_candidates([str(third_party_root)])
    third_party_loader.discover_candidates([str(third_party_root)])

    assert builtin_loader.failed_plugins == {}
    assert "test.normal-plugin" in third_party_loader.failed_plugins


def test_incompatible_adapter_manifest_is_only_validated_by_builtin_runtime(tmp_path: Path) -> None:
    third_party_root = tmp_path / "plugins"
    third_party_root.mkdir()
    plugin_dir = _write_plugin(third_party_root, "snowluma-adapter", "adapter")
    _make_plugin_host_incompatible(plugin_dir)

    builtin_loader = PluginLoader(
        host_version="1.1.0",
        plugin_type_filter="trusted_or_adapter",
        trusted_plugin_dirs=[],
    )
    third_party_loader = PluginLoader(host_version="1.1.0", plugin_type_filter="not_adapter")

    builtin_loader.discover_candidates([str(third_party_root)])
    third_party_loader.discover_candidates([str(third_party_root)])

    assert "test.snowluma-adapter" in builtin_loader.failed_plugins
    assert third_party_loader.failed_plugins == {}


def test_missing_plugin_type_defaults_to_extension_before_validation(tmp_path: Path) -> None:
    third_party_root = tmp_path / "plugins"
    third_party_root.mkdir()
    plugin_dir = _write_plugin(third_party_root, "untyped-plugin", "extension")
    _make_plugin_host_incompatible(plugin_dir, omit_plugin_type=True)

    builtin_loader = PluginLoader(
        host_version="1.1.0",
        plugin_type_filter="trusted_or_adapter",
        trusted_plugin_dirs=[],
    )
    third_party_loader = PluginLoader(host_version="1.1.0", plugin_type_filter="not_adapter")

    builtin_loader.discover_candidates([str(third_party_root)])
    third_party_loader.discover_candidates([str(third_party_root)])

    assert builtin_loader.failed_plugins == {}
    assert "test.untyped-plugin" in third_party_loader.failed_plugins
