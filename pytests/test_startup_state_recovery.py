from unittest.mock import AsyncMock

import json

import pytest

from src.agent.config import AgentConfig
from src.agent.service import AgentService
from src.common import atomic_file
from src.config.config import CONFIG_VERSION, Config, write_config_to_file
from src.services.schedules.service import ScheduleService
from src.services.schedules.store import TodoStore


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [bytes(35), b"{broken", b"\xff", b"{}", b"[null]"])
async def test_corrupt_todos_do_not_block_service_start(tmp_path, monkeypatch, contents):
    path = tmp_path / "todos.json"
    path.write_bytes(contents)
    service = ScheduleService(path)
    monkeypatch.setattr(service, "_run", AsyncMock())
    try:
        await service.start()
        assert service._worker is not None
        assert service._store.list() == []
        backup, = tmp_path.glob("todos.json.corrupt-*")
        assert backup.read_bytes() == contents
        item = service._store.add("恢复后的提醒")
        assert TodoStore(path).list()[0]["id"] == item["id"]
    finally:
        await service.stop()


def test_todo_failed_commit_keeps_disk_and_memory(tmp_path, monkeypatch):
    path = tmp_path / "todos.json"
    store = TodoStore(path)
    store.add("原提醒")
    original = path.read_bytes()
    items = store.list()

    def fail_commit(source, target):
        raise OSError("commit interrupted")

    monkeypatch.setattr(atomic_file, "replace_file", fail_commit)
    with pytest.raises(OSError, match="commit interrupted"):
        store.add("新提醒")
    assert path.read_bytes() == original
    assert store.list() == items


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [bytes(35), b"{broken", b"\xff", b"[]", b"{}",
                                      b'{"paused": "false", "error": ""}',
                                      b'{"paused": false, "error": "", "manual_pause": null}'])
async def test_corrupt_agent_runtime_does_not_block_start(tmp_path, monkeypatch, contents):
    path = tmp_path / "runtime.json"
    path.write_bytes(contents)
    service = AgentService(tmp_path)
    monkeypatch.setattr(AgentService, "config", property(lambda self: AgentConfig(enabled=True)))
    agent = AsyncMock()
    monkeypatch.setattr("src.agent.service.AcpAgent", lambda owner: agent)
    monkeypatch.setattr(service, "_run", AsyncMock())
    try:
        await service._reload()
        assert service.agent is agent
        assert service.paused and service._manual_pause
        assert service.error
        agent.load.assert_awaited_once()
        backup, = tmp_path.glob("runtime.json.corrupt-*")
        assert backup.read_bytes() == contents
        assert AgentService(tmp_path)._load_runtime_state()["manual_pause"] is True
    finally:
        await service.stop()


@pytest.mark.parametrize("state", [
    {"paused": False, "error": ""},
    {"paused": True, "error": "旧故障"},
    {"paused": True, "error": "", "manual_pause": True},
])
def test_valid_agent_runtime_is_preserved(tmp_path, state):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert AgentService(tmp_path)._load_runtime_state() == state
    assert not list(tmp_path.glob("*.corrupt-*"))


@pytest.mark.parametrize("failure", ["commit", "serialize"])
def test_raw_bot_config_failure_preserves_original(tmp_path, monkeypatch, failure):
    from fastapi import HTTPException
    from src.webui.routers import config as config_routes

    path = tmp_path / "bot_config.toml"
    write_config_to_file(Config(), path, CONFIG_VERSION)
    original = path.read_bytes()
    monkeypatch.setattr(config_routes, "CONFIG_DIR", str(tmp_path))

    def fail(*args, **kwargs):
        raise OSError("save interrupted")

    if failure == "commit":
        monkeypatch.setattr(config_routes, "shared_settings_path", lambda: None)
        monkeypatch.setattr(atomic_file, "replace_file", fail)
    else:
        monkeypatch.setattr(config_routes, "shared_settings_path", lambda: tmp_path)
        monkeypatch.setattr(config_routes, "strip_shared_settings", fail)
    with pytest.raises(HTTPException) as caught:
        config_routes.update_bot_config_raw(original.decode("utf-8"))
    assert caught.value.status_code == 500
    assert path.read_bytes() == original
