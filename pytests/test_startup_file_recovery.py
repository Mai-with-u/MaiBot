from contextlib import closing
from io import open as io_open
from pathlib import Path
from unittest.mock import Mock

import asyncio
import json
import os
import sqlite3

import pytest
import tomlkit
from fastapi.testclient import TestClient

from src.common import atomic_file
from src.common.json_storage import _open_json_reader
from src.config.config import CONFIG_VERSION, Config, write_config_to_file
from src.host.authentication import HostCredentials
from src.host.config import HostConfig, save_host_config
from src.host.config_transaction import commit_config_files
from src.host.layout import Workspace, open_workspace
from src.host.maintenance import HostMaintenance
from src.host.registry import BotRecord, Registry
from src.host.sessions import HostSessions
from src.host.supervisor import BotSupervisor
from src.host.webui import HostWebUI
from src.host.workspace_migration import migrate_workspace_config


def test_config_commit_failure_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "bot_config.toml"
    write_config_to_file(Config(), path, CONFIG_VERSION)
    original = path.read_bytes()
    monkeypatch.setattr(atomic_file, "_replace_once", Mock(side_effect=OSError("commit interrupted")))

    with pytest.raises(OSError, match="commit interrupted"):
        write_config_to_file(Config(), path, CONFIG_VERSION)

    assert path.read_bytes() == original
    backup, = (tmp_path / "old").glob("*.toml")
    assert backup.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows 文件共享语义")
def test_atomic_write_with_windows_shared_delete_reader(tmp_path):
    path = tmp_path / "settings.txt"
    atomic_file.write_private(path, "旧配置")

    with io_open(path, "r", encoding="utf-8", opener=_open_json_reader) as reader:
        atomic_file.write_private(path, "新配置")
        assert reader.read() == "旧配置"

    assert path.read_text(encoding="utf-8") == "新配置"


def test_config_transaction_rolls_back_when_second_replace_fails(tmp_path, monkeypatch):
    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"
    first.write_text("first = 1", encoding="utf-8")
    second.write_text("second = 1", encoding="utf-8")
    replace_once = atomic_file._replace_once
    failed = False

    def fail_once(source, target):
        nonlocal failed
        if target == second and not failed:
            failed = True
            raise OSError("second commit interrupted")
        replace_once(source, target)

    monkeypatch.setattr(atomic_file, "_replace_once", fail_once)
    with pytest.raises(OSError, match="second commit interrupted"):
        commit_config_files(tmp_path, {first: "first = 2", second: "second = 2"})

    assert first.read_text(encoding="utf-8") == "first = 1"
    assert second.read_text(encoding="utf-8") == "second = 1"
    assert not (tmp_path / "model-config-transaction.json").exists()


@pytest.mark.parametrize("contents", [b"{broken", b"\xff", b"[]", None])
def test_corrupt_config_transaction_does_not_block_workspace_startup(tmp_path, contents):
    save_host_config(tmp_path, HostConfig())
    Registry(tmp_path).create()
    current = tmp_path / "current.toml"
    current.write_text("current = 2", encoding="utf-8")
    if contents is None:
        contents = json.dumps({str(current): "current = 1", str(tmp_path / "other.toml"): 42}).encode("utf-8")
    journal = tmp_path / "host" / "model-config-transaction.json"
    journal.write_bytes(contents)

    assert open_workspace(tmp_path).root == tmp_path
    assert current.read_text(encoding="utf-8") == "current = 2"
    assert not journal.exists()
    (backup,) = journal.parent.glob(f"{journal.name}.corrupt-*")
    assert backup.read_bytes() == contents


def test_corrupt_sessions_are_preserved_and_can_be_recreated(tmp_path):
    host = tmp_path / "host"
    credentials = HostCredentials(host / "webui.json")
    credentials.update("ValidPassword!123")
    path = host / "sessions.sqlite3"
    path.write_bytes(bytes(4096))

    sessions = HostSessions(tmp_path, credentials=credentials)
    session = sessions.create_session()

    assert HostSessions(tmp_path, credentials=credentials).valid(session)
    backup, = host.glob("sessions.sqlite3.corrupt-*")
    assert backup.read_bytes() == bytes(4096)


@pytest.mark.parametrize("columns", ["digest TEXT PRIMARY KEY", "expires_at REAL NOT NULL"])
def test_missing_session_column_keeps_webui_login_available(tmp_path, columns):
    host = tmp_path / "host"
    credentials = HostCredentials(host / "webui.json")
    password = "ValidPassword!123"
    credentials.update(password)
    saved_credentials = credentials.path.read_bytes()
    path = host / "sessions.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"CREATE TABLE sessions ({columns})")
    original = path.read_bytes()

    config = HostConfig(anti_crawler_mode="false")
    workspace = Workspace(tmp_path, Registry(tmp_path), config)
    supervisor = BotSupervisor(workspace, public_base_url=config.local_base_url())
    webui = HostWebUI(supervisor, HostMaintenance(supervisor), asyncio.Event())
    client = TestClient(webui.app)

    assert client.post("/api/webui/auth/verify", json={"token": password}).json()["valid"] is True
    assert client.get("/api/webui/auth/check").json()["authenticated"] is True
    assert credentials.path.read_bytes() == saved_credentials
    (backup,) = host.glob("sessions.sqlite3.corrupt-*")
    assert backup.read_bytes() == original


def test_legacy_session_table_keeps_existing_login(tmp_path):
    host = tmp_path / "host"
    credentials = HostCredentials(host / "webui.json")
    credentials.update("ValidPassword!123")
    session = HostSessions(tmp_path, credentials=credentials).create_session()
    with closing(sqlite3.connect(host / "sessions.sqlite3")) as db, db:
        db.execute("ALTER TABLE sessions DROP COLUMN origin")

    restored = HostSessions(tmp_path, credentials=credentials)

    assert restored.valid(session)
    assert restored.login_origin(session) is None
    assert not list(host.glob("sessions.sqlite3.corrupt-*"))


@pytest.mark.parametrize("legacy", [False, True])
def test_corrupt_credentials_leave_public_http_routes_available(tmp_path, legacy):
    host = tmp_path / "host"
    host.mkdir()
    config = HostConfig(anti_crawler_mode="false")
    registry = Registry(tmp_path)
    path = (tmp_path / "data" if legacy else host) / "webui.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(bytes(32))
    if legacy:
        save_host_config(tmp_path, config)
        registry.create()
        registry.add_bot("MaiBot", tmp_path, owned=False, initialization="initialized")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        for filename in ("bot_config.toml", "model_config.toml"):
            (config_dir / filename).write_text("[inner]\n", encoding="utf-8")
        workspace = open_workspace(tmp_path)
    else:
        workspace = Workspace(tmp_path, registry, config)
    supervisor = BotSupervisor(workspace, public_base_url=config.local_base_url())
    webui = HostWebUI(supervisor, HostMaintenance(supervisor), asyncio.Event())
    client = TestClient(webui.app)

    assert client.get("/api/host/mode").status_code == 503
    assert client.get("/api/host/bots").status_code == 404
    assert client.get("/api/files/bots/invalid/transfers/bad").status_code == 422
    assert path.read_bytes() == bytes(32)
    if legacy:
        assert not (host / "webui.json").exists()


@pytest.mark.parametrize("broken_snapshot", [bytes(32), b"api_providers = []\n"])
def test_broken_model_snapshot_does_not_block_workspace_migration(tmp_path, broken_snapshot):
    save_host_config(tmp_path, HostConfig())
    config = tmp_path / "config"
    versions = config / "versions" / "model"
    versions.mkdir(parents=True)
    (config / "bot_config.toml").write_text("[inner]\n", encoding="utf-8")
    (config / "model_config.toml").write_text("[inner]\n", encoding="utf-8")
    snapshot = versions / "broken.toml"
    snapshot.write_bytes(broken_snapshot)
    valid = versions / "valid.toml"
    valid.write_text(
        '[inner]\nversion = "1.17.25"\n[[api_providers]]\nname = "历史供应商"\n',
        encoding="utf-8",
    )
    bot = BotRecord("a" * 32, "MaiBot", tmp_path, False, "", "initialized", None, "running", "on-failure")

    migrate_workspace_config(tmp_path, [bot])

    catalog = tomlkit.parse((tmp_path / "host" / "model_catalog.toml").read_text(encoding="utf-8"))
    assert [provider["name"] for provider in catalog["api_providers"]] == ["历史供应商"]
    assert "api_providers" not in tomlkit.parse(valid.read_text(encoding="utf-8"))
    assert snapshot.read_bytes() == broken_snapshot
