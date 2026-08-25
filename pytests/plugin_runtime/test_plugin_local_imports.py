"""插件入口模块本地顶层导入的回归测试。"""

import json
import sys
from pathlib import Path

import pytest

from src.plugin_runtime.runner.plugin_loader import PluginLoader


def _write_local_import_plugin(plugin_dir: Path, plugin_id: str, source_name: str) -> None:
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config.py").write_text(
        f"class ChatLensConfig:\n    source = {source_name!r}\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "from config import ChatLensConfig\n\n"
        "class ChatLensPlugin:\n"
        "    config_type = ChatLensConfig\n\n"
        "    def get_status(self):\n"
        "        import config\n"
        "        return config.ChatLensConfig.source\n\n"
        "def create_plugin():\n"
        "    return ChatLensPlugin()\n",
        encoding="utf-8",
    )
    (plugin_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "version": "1.0.0",
                "name": plugin_id,
                "description": "本地导入测试插件",
                "author": {"name": "MaiBot", "url": "https://example.com/maibot"},
                "license": "GPL-v3.0-or-later",
                "urls": {"repository": f"https://example.com/{plugin_id}"},
                "host_application": {"min_version": "1.0.0", "max_version": "2.0.0"},
                "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"},
                "dependencies": [],
                "capabilities": [],
                "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
                "id": plugin_id,
            }
        ),
        encoding="utf-8",
    )


def test_plugin_loader_prefers_plugin_directory_for_local_top_level_import(tmp_path: Path) -> None:
    """插件使用 ``from config import ...`` 时应加载自身目录中的模块。"""

    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "chat-lens"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config.py").write_text(
        "class ChatLensConfig:\n    source = 'plugin-local'\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "from config import ChatLensConfig\n\n"
        "class ChatLensPlugin:\n"
        "    config_type = ChatLensConfig\n\n"
        "def create_plugin():\n"
        "    return ChatLensPlugin()\n",
        encoding="utf-8",
    )
    (plugin_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "version": "1.0.0",
                "name": "Chat Lens",
                "description": "本地导入测试插件",
                "author": {"name": "MaiBot", "url": "https://example.com/maibot"},
                "license": "GPL-v3.0-or-later",
                "urls": {"repository": "https://example.com/chat-lens"},
                "host_application": {"min_version": "1.0.0", "max_version": "2.0.0"},
                "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"},
                "dependencies": [],
                "capabilities": [],
                "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
                "id": "test.chat-lens",
            }
        ),
        encoding="utf-8",
    )

    loader = PluginLoader(host_version="1.1.0")
    loaded_plugins = loader.discover_and_load([str(plugins_root)])

    assert len(loaded_plugins) == 1
    assert loaded_plugins[0].instance.config_type.source == "plugin-local"
    assert loader.failed_plugins == {}
    assert str(plugin_dir) not in sys.path


def test_plugin_loader_isolates_cached_top_level_modules_between_plugins(tmp_path: Path) -> None:
    """不同插件都使用顶层 ``config`` 时，后加载插件不能复用前一个插件的模块。"""

    plugins_root = tmp_path / "plugins"
    first_plugin_dir = plugins_root / "first-plugin"
    second_plugin_dir = plugins_root / "second-plugin"
    _write_local_import_plugin(first_plugin_dir, "test.first-plugin", "first")
    _write_local_import_plugin(second_plugin_dir, "test.second-plugin", "second")

    previous_config_module = sys.modules.pop("config", None)
    try:
        loader = PluginLoader(host_version="1.1.0")
        loaded_plugins = loader.discover_and_load([str(plugins_root)])

        assert [plugin.instance.config_type.source for plugin in loaded_plugins] == ["first", "second"]
        assert loader.failed_plugins == {}
    finally:
        sys.modules.pop("config", None)
        if previous_config_module is not None:
            sys.modules["config"] = previous_config_module


@pytest.mark.asyncio
async def test_runner_invoke_resolves_runtime_local_import_from_plugin_dir(tmp_path: Path) -> None:
    """运行期回调内 ``import config`` 应解析到插件自身目录，调用结束后恢复 sys.path。"""

    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "chat-lens"
    _write_local_import_plugin(plugin_dir, "test.chat-lens", "plugin-local")

    loader = PluginLoader(host_version="1.1.0")
    loaded_plugins = loader.discover_and_load([str(plugins_root)])
    assert len(loaded_plugins) == 1
    assert str(plugin_dir) not in sys.path

    from src.plugin_runtime.runner.runner_main import PluginRunner

    runner = object.__new__(PluginRunner)
    runner._loader = loader

    previous_config_module = sys.modules.get("config")
    status = await runner._invoke_plugin_callable(
        loaded_plugins[0].instance.get_status,
        plugin_dir=str(plugin_dir),
    )
    assert status == "plugin-local"
    assert str(plugin_dir) not in sys.path
    assert sys.modules.get("config") is previous_config_module
