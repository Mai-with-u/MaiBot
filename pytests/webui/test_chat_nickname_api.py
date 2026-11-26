from pathlib import Path

import os

import pytest
import tomlkit

from src.common.atomic_file import write_private
from src.config.config import CONFIG_VERSION, Config, ConfigManager, read_bot_config_snapshot, write_config_to_file
from src.webui.routers import config as config_routes
from src.webui.routers.chat import routes, service


def _read_runtime_version(nickname: str, qq: str | None) -> str:
    chat_info = routes.get_chat_info()
    session_info = service.build_session_info_message("session", "user", "Alice", virtual_config=None)
    version = chat_info["bot_nickname_version"]
    assert isinstance(version, str) and version.isdecimal()
    assert session_info["bot_nickname_version"] == version
    assert chat_info["bot_name"] == session_info["bot_name"] == nickname
    assert chat_info["bot_qq"] == session_info.get("bot_qq") == qq
    return version


@pytest.mark.asyncio
async def test_config_and_runtime_share_the_loaded_file_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ConfigManager()
    manager.bot_config_path = tmp_path / "bot_config.toml"
    manager.model_config_path = tmp_path / "model_config.toml"
    monkeypatch.setattr(config_routes, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(service, "config_manager", manager)

    original = Config()
    original.bot.nickname = "小满"
    original.bot.qq_account = "0"
    initial_version = write_config_to_file(original, manager.bot_config_path, "0.0.0")
    manager.initialize()
    original_config = config_routes.get_bot_config()
    original_version = original_config["bot_nickname_version"]
    assert original_version != initial_version
    assert original_config["config"]["inner"]["version"] == CONFIG_VERSION
    assert original_config["config"]["bot"]["nickname"] == "小满"
    assert _read_runtime_version("小满", None) == original_version

    original_mtime = manager.bot_config_path.stat().st_mtime_ns
    updated_config = original_config["config"].unwrap()
    updated_config["bot"].update(nickname="春分", qq_account="12345")
    assert config_routes.update_bot_config(updated_config)["success"] is True
    saved_mtime = original_mtime - 1_000_000_000
    os.utime(manager.bot_config_path, ns=(saved_mtime, saved_mtime))
    saved_config = config_routes.get_bot_config()
    saved_version = saved_config["bot_nickname_version"]
    assert saved_config["config"]["bot"]["nickname"] == "春分"
    assert int(saved_version) > int(original_version)
    assert _read_runtime_version("小满", None) == original_version

    assert await manager.reload_config(changed_scopes=["bot"]) is True
    assert _read_runtime_version("春分", "12345") == saved_version
    assert config_routes.get_bot_config()["bot_nickname_version"] == saved_version


@pytest.mark.asyncio
async def test_config_and_runtime_share_versions_through_a_linked_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "physical-config"
    config_dir.mkdir()
    linked_config_dir = tmp_path / "config"
    if os.name == "nt":
        from _winapi import CreateJunction

        CreateJunction(str(config_dir), str(linked_config_dir))
        assert linked_config_dir.is_junction()
    else:
        linked_config_dir.symlink_to(config_dir, target_is_directory=True)

    manager = ConfigManager()
    manager.bot_config_path = (linked_config_dir / "bot_config.toml").resolve()
    manager.model_config_path = (linked_config_dir / "model_config.toml").resolve()
    monkeypatch.setattr(config_routes, "CONFIG_DIR", linked_config_dir)
    monkeypatch.setattr(service, "config_manager", manager)

    original = Config()
    original.bot.nickname = "小满"
    original.bot.qq_account = "0"
    write_config_to_file(original, manager.bot_config_path, CONFIG_VERSION)
    manager.initialize()
    original_config = config_routes.get_bot_config()
    original_version = original_config["bot_nickname_version"]
    assert original_config["config"]["bot"]["nickname"] == "小满"
    assert _read_runtime_version("小满", None) == original_version

    original_toml = manager.bot_config_path.read_text(encoding="utf-8")
    renamed_toml = original_toml.replace('nickname = "小满"', 'nickname = "春分"', 1)
    assert config_routes.update_bot_config_raw(renamed_toml)["success"] is True
    assert await manager.reload_config(changed_scopes=["bot"]) is True
    renamed_version = _read_runtime_version("春分", None)
    assert int(renamed_version) > int(original_version)

    # 只有热加载读到中间版本。下一次 GET 读到的内容与第一次完全相同。
    assert config_routes.update_bot_config_raw(original_toml)["success"] is True
    restored_config = config_routes.get_bot_config()
    restored_version = restored_config["bot_nickname_version"]
    assert restored_config["config"]["bot"]["nickname"] == "小满"
    assert int(restored_version) > int(renamed_version)
    assert _read_runtime_version("春分", None) == renamed_version

    assert await manager.reload_config(changed_scopes=["bot"]) is True
    assert _read_runtime_version("小满", None) == restored_version


def test_snapshot_version_stays_with_the_bytes_read_before_a_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "bot_config.toml"
    write_private(config_path, '[bot]\nnickname = "小满"\n')
    _, original_version = read_bot_config_snapshot(config_path)
    parse = tomlkit.parse

    def parse_after_replace(content: str):
        write_private(config_path, '[bot]\nnickname = "春分"\n')
        return parse(content)

    with monkeypatch.context() as context:
        context.setattr(tomlkit, "parse", parse_after_replace)
        document, version = read_bot_config_snapshot(config_path)
    assert document["bot"]["nickname"] == "小满"
    assert version == original_version
    latest_document, latest_version = read_bot_config_snapshot(config_path)
    assert latest_document["bot"]["nickname"] == "春分"
    assert int(latest_version) > int(version)
