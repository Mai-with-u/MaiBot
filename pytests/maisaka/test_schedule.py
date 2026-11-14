"""日程持久化、聊天隔离、桌宠共用存储及到期唤醒。"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, Mock

import asyncio
import json

import pytest

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.chat.message_receive.chat_manager import chat_manager
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.desktop_pet.service import DesktopPetService, desktop_pet_service
from src.desktop_pet.todo_proxy import TodoProxy
from src.maisaka.builtin_tool.schedule import handle_tool
from src.maisaka.context.messages import InternalContextMessage
from src.maisaka.focus.group_runtime import FocusGroupRuntime
from src.services.schedules.service import ScheduleService
from src.services.schedules.store import TodoStore


@pytest.fixture
def setup_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = [1000.0]
    monkeypatch.setattr("src.services.schedules.store.time.time", lambda: clock[0])
    service = ScheduleService(tmp_path / "todos.json")
    service._store = TodoStore(tmp_path / "todos.json")
    service._refresh_desktop = Mock()
    runtime = SimpleNamespace(enqueue_internal_task=Mock())
    sessions = {sid: SimpleNamespace(session_id=sid, platform="qq", group_id=sid, user_id="")
                for sid in ("group-a", "group-b")}
    monkeypatch.setattr(chat_manager, "get_session_by_session_id", sessions.get)
    monkeypatch.setattr(chat_manager, "get_session_name", lambda sid: "群 " + sid)
    monkeypatch.setattr(desktop_pet_service, "status", lambda: {"ready": False})
    monkeypatch.setattr(heartflow_manager, "_is_session_allowed",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(heartflow_manager, "get_or_create_heartflow_chat",
                        AsyncMock(return_value=runtime))
    return service, clock, runtime


async def add(service: ScheduleService, session_id: str = "group-a", **options: Any) -> Dict[str, Any]:
    result = await service.command({"action": "add", "text": "提醒交作业", "options": {
        "kind": "bot", "delay_seconds": 60, "session_id": session_id, "session_name": "作业群", **options,
    }}, session_id=session_id)
    return result["item"]


@pytest.mark.asyncio
async def test_due_task_wakes_target_once_without_desktop(setup_service) -> None:
    service, clock, runtime = setup_service
    item = await add(service)
    await service.tick()
    runtime.enqueue_internal_task.assert_not_called()
    clock[0] += 60
    await service.tick()
    await service.tick()
    runtime.enqueue_internal_task.assert_called_once()
    args = runtime.enqueue_internal_task.call_args.kwargs
    assert args["source_kind"] == "schedule"
    assert item["id"] in args["text"] and "提醒交作业" in args["text"]
    heartflow_manager.get_or_create_heartflow_chat.assert_awaited_once_with("group-a")
    assert (await service.command({"action": "list"}))["items"][0]["status"] == "triggered"
    await service.command({"action": "complete", "todo_id": item["id"]}, session_id="group-a")
    assert (await service.command({"action": "list"}))["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["update", "cancel", "complete"])
async def test_other_chat_cannot_see_or_change_tasks(setup_service, action: str) -> None:
    service, _, _ = setup_service
    item = await add(service)
    assert (await service.command({"action": "list"}, session_id="group-b"))["items"] == []
    with pytest.raises(ValueError, match="当前聊天中不存在"):
        await service.command({"action": action, "todo_id": item["id"], "text": "篡改"}, session_id="group-b")
    assert (await service.command({"action": "list"}, session_id="group-a"))["items"][0]["text"] == "提醒交作业"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "complete", "update", "delete"])
async def test_claimed_task_revision_is_invalidated_before_dispatch(setup_service, action: str) -> None:
    service, clock, runtime = setup_service
    item = await add(service)
    clock[0] += 60
    claimed = (await service.command({"action": "claim_due"}))["items"][0]
    command = {"action": action, "todo_id": item["id"]}
    if action == "update":
        command["options"] = {"delay_seconds": 120}
    await service.command(command)
    await service.dispatch(claimed)
    await service.command({"action": "finish", "todo_id": claimed["id"], "revision": claimed["revision"]})
    runtime.enqueue_internal_task.assert_not_called()
    if action == "update":
        assert (await service.command({"action": "list"}))["items"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_restart_recovers_claimed_task_and_keeps_triggered_task(setup_service) -> None:
    service, clock, runtime = setup_service
    first = await add(service)
    clock[0] += 60
    await service.tick()
    second = await add(service)
    clock[0] += 60
    await service.command({"action": "claim_due"})
    recovered = ScheduleService(service._path)
    recovered._store = TodoStore(service._path)
    recovered._refresh_desktop = Mock()
    await recovered.tick()
    assert runtime.enqueue_internal_task.call_count == 2
    latest = runtime.enqueue_internal_task.call_args.kwargs["text"]
    assert second["id"] in latest and first["id"] not in latest


@pytest.mark.asyncio
async def test_delivery_failure_is_recorded_without_repeated_attempts(setup_service, monkeypatch) -> None:
    service, clock, runtime = setup_service
    item = await add(service)
    clock[0] += 60
    monkeypatch.setattr(heartflow_manager, "_is_session_allowed",
                        AsyncMock(return_value=False))
    await service.tick()
    await service.tick()
    saved = (await service.command({"action": "list"}))["items"][0]
    assert saved["id"] == item["id"] and saved["status"] == "failed"
    assert "禁止唤醒" in saved["error"]
    runtime.enqueue_internal_task.assert_not_called()


@pytest.mark.asyncio
async def test_user_reminder_stays_pending_until_desktop_is_online(setup_service, monkeypatch) -> None:
    service, clock, runtime = setup_service
    await service.command({"action": "add", "text": "喝水", "options": {"delay_seconds": 10}})
    clock[0] += 10
    await service.tick()
    assert (await service.command({"action": "list"}))["items"][0]["status"] == "pending"
    control = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(desktop_pet_service, "status", lambda: {"ready": True})
    monkeypatch.setattr(desktop_pet_service, "control", control)
    monkeypatch.setattr(desktop_pet_service, "_command", send)
    await service.tick()
    control.assert_awaited_once_with("show")
    send.assert_awaited_once()
    runtime.enqueue_internal_task.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_items_and_desktop_commands_share_the_schedule_store(setup_service, monkeypatch) -> None:
    service, clock, runtime = setup_service
    await service.command({"action": "add", "text": "旧桌宠事项", "options": {"kind": "bot", "delay_seconds": 5}})
    owner = SimpleNamespace(session_id="owner")
    monkeypatch.setattr(chat_manager, "resolve_sessions_by_target", lambda **_: [owner])
    clock[0] += 5
    await service.tick()
    assert "旧桌宠事项" in runtime.enqueue_internal_task.call_args.kwargs["text"]
    monkeypatch.setattr("src.desktop_pet.chat_tools.is_owner_chat", lambda sid: sid == "owner")
    owner_items = await service.command({"action": "list"}, session_id="owner")
    assert owner_items["items"][0]["text"] == "旧桌宠事项"
    await service.command({"action": "complete", "todo_id": owner_items["items"][0]["id"]}, session_id="owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"action": "add", "text": "没有时间"},
    {"action": "add", "text": "过去", "due_at": "1970-01-01T00:00:00+00:00"},
    {"action": "add", "text": "冲突", "delay_seconds": 60, "due_at": "2030-01-01T00:00:00+08:00"},
    {"action": "add", "text": "非法时间", "delay_seconds": True},
])
async def test_schedule_tool_rejects_invalid_plans(setup_service, monkeypatch, arguments) -> None:
    service, _, _ = setup_service
    monkeypatch.setattr("src.maisaka.builtin_tool.schedule.schedule_service", service)

    async def local(coro):
        return await coro

    monkeypatch.setattr("src.maisaka.builtin_tool.schedule.run_on_main_loop", local)
    ctx = SimpleNamespace(runtime=SimpleNamespace(session_id="group-a"),
                          build_failure_result=lambda name, error: SimpleNamespace(success=False, error=error))
    result = await handle_tool(ctx, SimpleNamespace(arguments=arguments))
    assert not result.success
    assert (await service.command({"action": "list"}))["items"] == []


@pytest.mark.asyncio
async def test_window_proxy_uses_host_store_instead_of_second_writer(setup_service) -> None:
    service, _, _ = setup_service
    loop = asyncio.get_running_loop()
    tasks: List[asyncio.Task[Any]] = []

    async def handle(event: Dict[str, Any]) -> None:
        result = await service.command(event["command"])
        proxy.receive({"request_id": event["request_id"], "result": result})

    def emit(event: Dict[str, Any]) -> None:
        loop.call_soon_threadsafe(lambda: tasks.append(asyncio.create_task(handle(event))))

    proxy = TodoProxy(emit)
    item = await asyncio.to_thread(proxy.add, "桌宠添加", kind="bot", delay_seconds=60)
    assert (await service.command({"action": "list"}))["items"][0]["id"] == item["id"]
    await service.command({"action": "update", "todo_id": item["id"], "text": "主程序修改"})
    assert (await asyncio.to_thread(proxy.list))[0]["text"] == "主程序修改"
    await asyncio.to_thread(proxy.done, item["id"])
    assert await asyncio.to_thread(proxy.list) == []
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_initial_todo_list_can_complete_before_window_ready(setup_service, monkeypatch) -> None:
    schedule, _, _ = setup_service
    item = await add(schedule)
    monkeypatch.setattr("src.desktop_pet.service.schedule_service", schedule)
    host = DesktopPetService()
    host._closing = False
    host._ready = False
    loop = asyncio.get_running_loop()
    tasks: List[asyncio.Task[Any]] = []

    class Pipe:
        def write(self, payload: bytes) -> None:
            command = json.loads(payload)
            assert command["type"] == "todo_response"
            proxy.receive(command)
            host._handle_event({"type": "ack", "id": command["id"]})

        async def drain(self) -> None:
            pass

    host._process = SimpleNamespace(stdin=Pipe())

    def emit(event: Dict[str, Any]) -> None:
        loop.call_soon_threadsafe(lambda: tasks.append(asyncio.create_task(host._local_todo(event))))

    proxy = TodoProxy(emit)
    items = await asyncio.wait_for(asyncio.to_thread(proxy.list), timeout=2)
    assert items[0]["id"] == item["id"]
    assert not host._ready
    with pytest.raises(RuntimeError, match="尚未就绪"):
        await host._command({"type": "show"})
    await asyncio.gather(*tasks)


def test_store_keeps_existing_json_and_attachments_directory(tmp_path: Path) -> None:
    path = tmp_path / "todos.json"
    path.write_text(json.dumps([{"id": "old", "text": "旧提醒", "created_at": 1}]), encoding="utf-8")
    store = TodoStore(path)
    item = store.list()[0]
    assert item["session_id"] == "" and item["kind"] == "user"
    store.done("old")
    assert json.loads(path.read_text(encoding="utf-8"))[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_service_starts_without_desktop_and_stops_its_worker(tmp_path: Path) -> None:
    service = ScheduleService(tmp_path / "todos.json")
    checked = asyncio.Event()

    async def tick() -> None:
        checked.set()

    service.tick = tick
    try:
        await service.start()
        await asyncio.wait_for(checked.wait(), timeout=1)
        first_worker = service._worker
        await service.start()
        assert service._worker is first_worker
    finally:
        await service.stop()
    assert service._worker is None


def test_schedule_arrival_wakes_focus_group_timed_wait() -> None:
    group = object.__new__(FocusGroupRuntime)
    group._remember = Mock(return_value=True)
    group._wait_until = 2000
    group._finish_wait = Mock()
    group._queue_entry = Mock()
    group._ensure_worker = Mock()
    group._schedule_checkpoint = Mock()
    group._event = asyncio.Event()
    message = InternalContextMessage(
        raw_message=MessageSequence([TextComponent("日程到期")]), timestamp=datetime.now(),
        visible_text="日程到期", message_id="schedule:1", source_kind="schedule",
    )
    runtime = SimpleNamespace(session_id="group-a", session_name="作业群", _chat_history=[message])
    trigger = SimpleNamespace(message_id="schedule:1", processed_plain_text="日程任务")
    group.register_proactive(runtime, trigger)
    group._finish_wait.assert_called_once_with("作业群 的日程到期")
    assert group._event.is_set()
