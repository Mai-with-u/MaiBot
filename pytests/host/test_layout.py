"""工作区布局判定、注册表与手动迁移；不启动 Bot 进程。"""

from pathlib import Path

import json

import pytest

from src.host import layout as layout_module
from src.host.adopt import adopt, undo
from src.host.config import load_host_config
from src.host.layout import MOVED_MARKER, create_bot, open_workspace


def write_instance(directory: Path, *, nickname: str = "麦麦", message_port: int = 8000) -> None:
    (directory / "config").mkdir(parents=True)
    (directory / "config" / "bot_config.toml").write_text(
        f'[bot]\nnickname = "{nickname}"\n\n[http]\nhost = ["0.0.0.0"]\nport = 18001\n\n'
        f'[webui]\nsecure_cookie = true\nanti_crawler_mode = "strict"\nallowed_ips = "10.0.0.1"\n\n'
        f"[maim_message]\nws_server_port = {message_port}\napi_server_port = 8090\n",
        encoding="utf-8",
    )
    (directory / "config" / "model_config.toml").write_text("", encoding="utf-8")


@pytest.fixture(autouse=True)
def fake_initializer(monkeypatch: pytest.MonkeyPatch) -> None:
    """新 Bot 的配置生成在子进程中进行；测试只需要它写出带默认端口的配置。"""
    monkeypatch.setattr(layout_module, "_run_initializer", lambda directory: write_instance(directory, nickname="new"))


def test_old_instance_is_registered_in_place_without_moving_data(tmp_path: Path) -> None:
    write_instance(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "MaiBot.db").write_bytes(b"large data")
    (tmp_path / "data" / "webui.json").write_text(json.dumps({"access_token": "Old-Pass!word1"}), encoding="utf-8")

    workspace = open_workspace(tmp_path)

    [bot] = workspace.registry.list_bots()
    assert bot.name == "麦麦" and bot.directory == tmp_path and not bot.owned
    assert (tmp_path / "data" / "MaiBot.db").read_bytes() == b"large data"
    assert json.loads((tmp_path / "host" / "webui.json").read_text(encoding="utf-8"))["access_token"] == "Old-Pass!word1"
    assert not (tmp_path / "data" / "webui.json").exists()
    config = load_host_config(tmp_path)
    assert (config.hosts, config.port, config.secure_cookie) == (["0.0.0.0"], 18001, True)
    assert (config.anti_crawler_mode, config.allowed_ips) == ("strict", "10.0.0.1")
    # 再次打开以注册表为准，不会重复登记。
    assert len(open_workspace(tmp_path).registry.list_bots()) == 1


def test_registered_instance_migrates_webui_preference_without_replacing_login(tmp_path: Path) -> None:
    write_instance(tmp_path)
    open_workspace(tmp_path)
    credentials = tmp_path / "host" / "webui.json"
    current_login = json.dumps({"access_token": "Current-Pass!word1"})
    credentials.write_text(current_login, encoding="utf-8")
    legacy = tmp_path / "data" / "webui.json"
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_text(json.dumps({
        "access_token": "Old-Pass!word1",
        "adapter_config_path": "config/adapter.toml",
        "git_mirrors": [],
        "git_mirrors_schema_version": 1,
    }), encoding="utf-8")

    open_workspace(tmp_path)
    open_workspace(tmp_path)

    assert not legacy.exists()
    assert credentials.read_text(encoding="utf-8") == current_login
    assert legacy.with_name("adapter_config_path.txt").read_text(encoding="utf-8") == "config/adapter.toml"


def test_registry_rejects_overlapping_directories(tmp_path: Path) -> None:
    write_instance(tmp_path / "workspace")
    workspace = open_workspace(tmp_path / "workspace")
    with pytest.raises(ValueError, match="已属于"):
        workspace.registry.add_bot("嵌套", tmp_path / "workspace" / "data", owned=False, initialization="initialized")


def test_adopt_move_renames_marks_and_undoes(tmp_path: Path) -> None:
    workspace = open_workspace(tmp_path / "workspace")
    write_instance(tmp_path / "old")
    (tmp_path / "old" / "data").mkdir()
    (tmp_path / "old" / "data" / "MaiBot.db").write_bytes(b"db")

    record = adopt(workspace, tmp_path / "old", "move")

    assert record.owned and record.directory == (tmp_path / "workspace" / "bots" / record.bot_id).resolve()
    assert (record.directory / "data" / "MaiBot.db").read_bytes() == b"db"
    assert (tmp_path / "old" / MOVED_MARKER).read_text(encoding="utf-8") == str(record.directory)
    with pytest.raises(RuntimeError, match="已移动"):
        open_workspace(tmp_path / "old")

    undo(workspace, record.bot_id)
    assert (tmp_path / "old" / "data" / "MaiBot.db").read_bytes() == b"db"
    assert not (tmp_path / "old" / MOVED_MARKER).exists()


def test_interrupted_move_resumes_from_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = open_workspace(tmp_path / "workspace")
    write_instance(tmp_path / "old")
    original_add = workspace.registry.add_bot
    calls = {"count": 0}

    def interrupted_add(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise KeyboardInterrupt
        return original_add(*args, **kwargs)

    monkeypatch.setattr(workspace.registry, "add_bot", interrupted_add)
    with pytest.raises(KeyboardInterrupt):
        adopt(workspace, tmp_path / "old", "move")
    assert (tmp_path / "old" / MOVED_MARKER).is_file()

    record = adopt(workspace, tmp_path / "old", "move")
    assert record.owned and (record.directory / "config" / "bot_config.toml").is_file()


def test_host_settings_migration_preserves_values_and_cleans_only_after_validation(tmp_path, monkeypatch):
    import ssl
    import tomlkit

    workspace = open_workspace(tmp_path)
    [bot] = workspace.registry.list_bots()
    path = bot.directory / 'config' / 'bot_config.toml'
    doc = tomlkit.parse(path.read_text(encoding='utf-8'))
    doc['files'] = {'transfer_base_url': 'https://files.example.com'}
    doc['maim_message'] = {
        'auth_token': ['keep'], 'api_server_use_wss': True,
        'api_server_cert_file': 'cert.pem', 'api_server_key_file': 'key.pem',
    }
    path.write_text(tomlkit.dumps(doc), encoding='utf-8')
    original = path.read_bytes()
    host_path = tmp_path / 'host' / 'config.toml'
    host_original = host_path.read_bytes()
    with pytest.raises(FileNotFoundError):
        open_workspace(tmp_path)
    assert path.read_bytes() == original
    assert host_path.read_bytes() == host_original

    certificates = []
    monkeypatch.setattr(ssl.SSLContext, 'load_cert_chain', lambda self, cert, key: certificates.append((cert, key)))
    migrated = open_workspace(tmp_path)
    assert migrated.config.public_base_url == 'https://files.example.com'
    assert migrated.config.local_base_url().startswith('https://')
    assert certificates == [(str(bot.directory / 'cert.pem'), str(bot.directory / 'key.pem'))]
    cleaned = tomlkit.parse(path.read_text(encoding='utf-8'))
    assert 'transfer_base_url' not in cleaned['files']
    assert dict(cleaned['maim_message']) == {'auth_token': ['keep']}
    assert any(item.read_bytes() == original for item in (tmp_path / 'host' / 'backups').rglob('*.toml'))
    snapshot = path.read_bytes()
    open_workspace(tmp_path)
    assert path.read_bytes() == snapshot


def test_host_settings_migration_rejects_conflicting_bots_without_cleanup(tmp_path):
    import tomlkit

    workspace = open_workspace(tmp_path)
    create_bot(workspace, 'second')
    originals = {}
    for index, bot in enumerate(workspace.registry.list_bots()):
        path = bot.directory / 'config' / 'bot_config.toml'
        doc = tomlkit.parse(path.read_text(encoding='utf-8'))
        doc['files'] = {'transfer_base_url': f'https://files{index}.example.com'}
        path.write_text(tomlkit.dumps(doc), encoding='utf-8')
        originals[path] = path.read_bytes()
    with pytest.raises(ValueError, match='迁移冲突'):
        open_workspace(tmp_path)
    assert all(path.read_bytes() == content for path, content in originals.items())


def test_retention_migration_preserves_old_policy_and_removes_duplicates(tmp_path):
    import tomlkit
    from src.config.shared_settings import merge_retention
    from src.host.workspace_migration import migrate_workspace_config
    from src.host.registry import Registry

    host = tmp_path / "host"
    host.mkdir()
    (host / "config.toml").write_text("[webui]\nport = 8000\n", encoding="utf-8")
    directories = [tmp_path / "a", tmp_path / "b"]
    for index, directory in enumerate(directories):
        (directory / "config").mkdir(parents=True)
        doc = {"visual": {"image_cache_cleanup": {"enabled": index == 0, "image_file_retention_days": 14 + index * 16}},
               "emoji": {"cache_cleanup": {"emoji_file_retention_days": 60}}, "log": {"log_cleanup_days": 90, "console_log_level": "DEBUG"}}
        (directory / "config" / "bot_config.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
    registry = Registry(tmp_path)
    registry.create()
    bots = []
    for directory in directories:
        (directory / "config" / "model_config.toml").write_text("", encoding="utf-8")
        bots.append(registry.add_bot(directory.name, directory, owned=True, initialization="initialized"))
    migrate_workspace_config(tmp_path, bots)
    policy = tomlkit.parse((host / "config.toml").read_text(encoding="utf-8"))["retention"].unwrap()
    assert policy["images"]["enabled"] is False
    assert policy["images"]["image_file_retention_days"] == 30
    assert policy["emoji"]["emoji_file_retention_days"] == 60
    assert policy["logs"]["log_cleanup_days"] == 90
    for directory in directories:
        local = tomlkit.parse((directory / "config" / "bot_config.toml").read_text(encoding="utf-8")).unwrap()
        assert "image_cache_cleanup" not in local.get("visual", {})
        assert "cache_cleanup" not in local.get("emoji", {})
        assert local["log"] == {"console_log_level": "DEBUG"}
        assert merge_retention(local, policy)["log"]["log_cleanup_days"] == 90
    assert len(list((host / "backups").iterdir())) == 1
    before = (host / "config.toml").read_bytes()
    migrate_workspace_config(tmp_path, bots)
    assert (host / "config.toml").read_bytes() == before


@pytest.mark.parametrize("mode", ["register", "move"])
def test_adopt_preserves_source_builtin_plugin_settings(tmp_path, monkeypatch, mode):
    workspace = open_workspace(tmp_path / "workspace")
    source = tmp_path / "old"
    write_instance(source)
    package = tmp_path / "host-package"
    host_config = package / "plugins/built_in/example/config.toml"
    host_config.parent.mkdir(parents=True)
    host_config.write_text("[plugin]\nenabled = true\n", encoding="utf-8")
    monkeypatch.setattr(layout_module, "PACKAGE_ROOT", package)
    old_config = source / "src/plugins/built_in/example/config.toml"
    old_config.parent.mkdir(parents=True)
    old_bytes = b"[plugin]\nenabled = false\n"
    old_config.write_bytes(old_bytes)
    record = adopt(workspace, source, mode)
    target = record.directory / "config/builtin_plugins/example/config.toml"
    assert target.read_bytes() == old_bytes
    # 新配置已被用户修改时，再次调用不能覆盖它，也不能修改共享代码目录。
    target.write_text("[plugin]\nenabled = true\n", encoding="utf-8")
    layout_module.copy_builtin_plugin_configs(record.directory)
    assert target.read_bytes() == host_config.read_bytes()
    assert (record.directory / "src/plugins/built_in/example/config.toml").read_bytes() == old_bytes
