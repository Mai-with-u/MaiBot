"""会话日程分文件存储、旧数据迁移与读取一致性。"""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock

import base64
import json

import pytest

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.chat.message_receive.chat_manager import chat_manager
from src.desktop_pet.service import desktop_pet_service
from src.desktop_pet.todo_context import build_todo_context
from src.services.schedules.repository import ScheduleRepository, read_schedule_items, schedule_path
from src.services.schedules.service import ScheduleService
from src.services.schedules.store import TodoStore
from src.webui.routers.desktop_pet import _todo_summary


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_manager, "resolve_sessions_by_target", lambda **_: [SimpleNamespace(session_id="owner")])
    monkeypatch.setattr(chat_manager, "get_session_name", lambda sid: "聊天 " + sid)
    monkeypatch.setattr(chat_manager, "get_session_by_session_id", lambda sid: SimpleNamespace(session_id=sid))
    monkeypatch.setattr("src.desktop_pet.chat_tools.is_owner_chat", lambda sid: sid == "owner")
    monkeypatch.setattr(desktop_pet_service, "status", lambda: {"ready": False})


def add(repository: ScheduleRepository, session_id: str, **options: Any) -> Dict[str, Any]:
    return repository.execute({"action": "add", "text": session_id + " 的事项", "options": {
        "session_id": session_id, "session_name": "聊天 " + session_id, "kind": "bot", **options,
    }}, session_id)["item"]


def test_only_changed_session_is_written_and_restart_keeps_items(tmp_path: Path) -> None:
    repository = ScheduleRepository(tmp_path)
    first = add(repository, "group-a")
    second = add(repository, "group-b")
    original = (tmp_path / "group-b.json").read_bytes()
    repository.execute({"action": "complete", "todo_id": first["id"]}, "group-a")
    assert (tmp_path / "group-b.json").read_bytes() == original
    assert {path.name for path in tmp_path.glob("*.json")} == {"group-a.json", "group-b.json"}
    recovered = ScheduleRepository(tmp_path)
    assert recovered.list(session_id="group-a") == []
    assert recovered.list(True, "group-a")[0]["status"] == "completed"
    assert recovered.list(session_id="group-b")[0]["id"] == second["id"]


@pytest.mark.parametrize("action", ["update", "cancel", "complete", "delete", "claim_condition", "finish"])
def test_scoped_command_cannot_access_other_session(tmp_path: Path, action: str) -> None:
    repository = ScheduleRepository(tmp_path)
    item = add(repository, "group-a", condition="测试条件")
    with pytest.raises(ValueError, match="当前聊天中不存在"):
        repository.execute({"action": action, "todo_id": item["id"], "revision": item["revision"], "evidence": "证据"}, "owner")
    assert repository.list(session_id="owner") == []
    assert repository.list(session_id="group-a")[0]["status"] == "pending"


def test_global_window_add_resolves_real_owner(tmp_path: Path, sessions) -> None:
    repository = ScheduleRepository(tmp_path)
    item = repository.execute({"action": "add", "text": "窗口提醒"}, None)["item"]
    assert item["session_id"] == "owner"
    assert json.loads((tmp_path / "owner.json").read_text(encoding="utf-8"))[0] == item
    with pytest.raises(ValueError, match="不能修改日程所属聊天"):
        repository.execute({"action": "update", "todo_id": item["id"], "options": {"session_id": "other"}}, None)


def test_migration_preserves_ids_history_images_and_original_backup(tmp_path: Path, sessions) -> None:
    from PIL import Image

    legacy_path = tmp_path / "plugins" / "sengokucola.desktop-pet" / "todos.json"
    legacy = TodoStore(legacy_path)
    owner = legacy.add("旧主人提醒")
    group = legacy.add("群日程", session_id="group-a", session_name="群聊", kind="bot")
    legacy.done(group["id"])
    stream = BytesIO()
    Image.new("RGB", (1, 1)).save(stream, format="PNG")
    image_id = legacy._store_image("data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii"))
    owner = legacy.update(owner["id"], images=[image_id])
    original = legacy_path.read_bytes()
    directory = tmp_path / "schedules"
    repository = ScheduleRepository(directory, legacy_path)
    assert not legacy_path.exists()
    backup, = legacy_path.parent.glob("todos.json.migrated-*.bak")
    assert backup.read_bytes() == original
    assert repository.list(session_id="owner")[0]["id"] == owner["id"]
    assert repository.list(session_id="owner")[0]["revision"] == owner["revision"]
    assert repository.list(True, "group-a")[0]["status"] == "completed"
    assert (directory / "todo_images" / image_id).read_bytes() == stream.getvalue()
    assert repository.execute({"action": "image", "image_id": image_id}, None)["data_url"].startswith("data:image/png;base64,")
    assert len(ScheduleRepository(directory, legacy_path).list(True)) == 2


def test_interrupted_migration_resumes_without_duplicates_or_overwriting(tmp_path: Path, monkeypatch) -> None:
    legacy_path = tmp_path / "legacy" / "todos.json"
    legacy = TodoStore(legacy_path)
    first = legacy.add("A", session_id="group-a")
    legacy.add("B", session_id="group-b")
    directory = tmp_path / "schedules"
    save = TodoStore._save

    def fail_second(store: TodoStore, items):
        if store._path.name == "group-b.json":
            raise OSError("迁移中断")
        return save(store, items)

    with monkeypatch.context() as patch:
        patch.setattr(TodoStore, "_save", fail_second)
        with pytest.raises(OSError, match="迁移中断"):
            ScheduleRepository(directory, legacy_path)
    assert legacy_path.exists()
    migrated = TodoStore(directory / "group-a.json")
    migrated.done(first["id"])
    repository = ScheduleRepository(directory, legacy_path)
    assert len(repository.list(True)) == 2
    assert repository.list(True, "group-a")[0]["status"] == "completed"
    assert not legacy_path.exists()


def test_missing_owner_fails_without_inventing_id_or_changing_legacy(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "legacy.json"
    TodoStore(path).add("未分配主人事项")
    original = path.read_bytes()
    monkeypatch.setattr(chat_manager, "resolve_sessions_by_target", lambda **_: [])
    directory = tmp_path / "schedules"
    with pytest.raises(ValueError, match="唯一的主人会话"):
        ScheduleRepository(directory, path)
    assert path.read_bytes() == original
    assert not list(directory.glob("*.json"))


@pytest.mark.parametrize("session_id", ["", "../outside", "a/b", "a\\b", "C:outside"])
def test_session_filename_cannot_escape_directory(tmp_path: Path, session_id: str) -> None:
    with pytest.raises(ValueError):
        schedule_path(tmp_path, session_id)


def test_snapshot_and_webui_read_new_files_without_loading_old_storage(tmp_path: Path, monkeypatch, sessions) -> None:
    repository = ScheduleRepository(tmp_path)
    add(repository, "owner")
    add(repository, "group-a")
    monkeypatch.setattr("src.desktop_pet.todo_context.schedules_directory", lambda: tmp_path)
    monkeypatch.setattr("src.desktop_pet.todo_context.is_owner_chat", lambda sid: sid == "owner")
    monkeypatch.setattr("src.desktop_pet.todo_context.load_prompt", lambda name, **args: args["todos"])
    snapshot = json.loads(build_todo_context("owner"))
    assert [item["session_id"] for item in snapshot] == ["owner"]
    assert build_todo_context("group-a") == ""
    monkeypatch.setattr("src.webui.routers.desktop_pet.schedules_directory", lambda: tmp_path)
    assert _todo_summary()["pending"] == 2
    assert len(read_schedule_items(tmp_path)) == 2


@pytest.mark.asyncio
async def test_start_migrates_then_schedules_and_recovery_is_per_session(tmp_path: Path, monkeypatch, sessions) -> None:
    legacy_path = tmp_path / "legacy.json"
    old = TodoStore(legacy_path)
    group = old.add("到期任务", session_id="group-a", kind="bot", due_at="2035-01-01T00:00:00+00:00")
    monkeypatch.setattr("src.services.schedules.store.time.time", lambda: 2100000000.0)
    service = ScheduleService(tmp_path / "schedules", legacy_path)
    service._run = AsyncMock()
    service._refresh_desktop = Mock()
    runtime = SimpleNamespace(enqueue_internal_task=Mock())
    monkeypatch.setattr(heartflow_manager, "_is_session_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(heartflow_manager, "get_or_create_heartflow_chat", AsyncMock(return_value=runtime))
    try:
        await service.start()
        await service.tick()
        await service.tick()
        runtime.enqueue_internal_task.assert_called_once()
        items = (await service.command({"action": "list"}, session_id="group-a"))["items"]
        assert items[0]["id"] == group["id"] and items[0]["status"] == "triggered"
    finally:
        await service.stop()
    recovered = ScheduleRepository(tmp_path / "schedules")
    assert recovered.list(session_id="group-a")[0]["status"] == "triggered"


@pytest.mark.asyncio
async def test_later_session_write_failure_does_not_strand_prior_delivery(tmp_path: Path, monkeypatch, sessions) -> None:
    repository = ScheduleRepository(tmp_path)
    add(repository, "group-a", due_at="2035-01-01T00:00:00+00:00")
    add(repository, "group-b", due_at="2035-01-01T00:00:00+00:00")
    monkeypatch.setattr("src.services.schedules.store.time.time", lambda: 2100000000.0)
    service = ScheduleService(tmp_path)
    service._store = repository
    service._refresh_desktop = Mock()
    runtime = SimpleNamespace(enqueue_internal_task=Mock())
    monkeypatch.setattr(heartflow_manager, "_is_session_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(heartflow_manager, "get_or_create_heartflow_chat", AsyncMock(return_value=runtime))
    save = TodoStore._save

    def fail_second(store: TodoStore, items):
        if store._path.stem == "group-b":
            raise OSError("第二个文件写入失败")
        return save(store, items)

    with monkeypatch.context() as patch:
        patch.setattr(TodoStore, "_save", fail_second)
        with pytest.raises(OSError, match="第二个文件写入失败"):
            await service.tick()
    assert repository.list(session_id="group-a")[0]["status"] == "triggered"
    assert repository.list(session_id="group-b")[0]["status"] == "pending"
    await service.tick()
    assert runtime.enqueue_internal_task.call_count == 2
