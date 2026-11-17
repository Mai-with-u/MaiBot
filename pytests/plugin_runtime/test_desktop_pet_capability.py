from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import json

import pytest

from src.desktop_pet import service as pet_module
from src.desktop_pet.service import DesktopPetService
from src.plugin_runtime.capabilities import desktop_pet
from src.plugin_runtime.host.authorization import AuthorizationManager
from src.plugin_runtime.host.capability_service import CapabilityService
from src.plugin_runtime.protocol.envelope import Envelope, MessageType


def capability_service(granted):
    authorization = AuthorizationManager()
    authorization.register_plugin("test.pet", granted)
    service = CapabilityService(authorization)
    for action in ("status", "show", "hide", "say", "set_thinking"):
        service.register_capability(f"desktop_pet.{action}", desktop_pet.control)
    return service


def request(action, args):
    return Envelope(
        request_id=1, message_type=MessageType.REQUEST, plugin_id="test.pet", method="cap.call",
        payload={"capability": f"desktop_pet.{action}", "args": args},
    )


@pytest.mark.asyncio
async def test_capability_permissions_and_validation(monkeypatch):
    control = AsyncMock(return_value={"ready": True})
    monkeypatch.setattr(desktop_pet.desktop_pet_service, "control", control)
    denied = await capability_service([]).handle_capability_request(request("say", {"text": "hi"}))
    assert denied.error["code"] == "E_CAPABILITY_DENIED"
    control.assert_not_awaited()

    for action, args in [
        ("say", {"text": " "}), ("say", {"text": "x" * 2001}),
        ("say", {"text": "hi", "script": "alert(1)"}), ("show", {"enabled": True}),
        ("set_thinking", {"active": "false"}),
    ]:
        invalid = await capability_service([f"desktop_pet.{action}"]).handle_capability_request(request(action, args))
        assert invalid.error is not None
    control.assert_not_awaited()
    result = await desktop_pet.control("test.pet", "desktop_pet.say", {"text": "  提醒  "})
    assert result == {"success": True, "result": {"ready": True}}
    control.assert_awaited_once_with("say", text="提醒")


@pytest.mark.asyncio
async def test_unavailable_window_is_not_reported_as_success():
    service = DesktopPetService()
    assert (await service.control("status"))["ready"] is False
    with pytest.raises(RuntimeError, match="尚未就绪"):
        await service.control("show")


@pytest.mark.asyncio
async def test_restart_stops_ready_pet_before_starting_new_worker(monkeypatch):
    service = DesktopPetService()
    service._ready = True
    service._closing = False
    service._active_mode = "local"
    monkeypatch.setattr(pet_module.global_config.desktop_pet, "enabled", True)
    monkeypatch.setattr(pet_module.global_config.desktop_pet, "mode", "local")
    monkeypatch.setattr(pet_module.sys, "platform", "win32")
    events = []

    async def stop():
        events.append("stop")
        service._ready = False
        service._closing = True

    async def run():
        events.append("start")

    monkeypatch.setattr(service, "_stop", stop)
    monkeypatch.setattr(service, "_run", run)
    await service._reload(force_restart=True)
    await service._worker
    assert events == ["stop", "start"]
    assert not service._closing
    assert service._active_mode == "local"


@pytest.mark.asyncio
async def test_restart_rejects_remote_mode_without_stopping_it(monkeypatch):
    service = DesktopPetService()
    stop = AsyncMock()
    monkeypatch.setattr(service, "_stop", stop)
    monkeypatch.setattr(pet_module.global_config.desktop_pet, "enabled", True)
    monkeypatch.setattr(pet_module.global_config.desktop_pet, "mode", "remote")
    with pytest.raises(RuntimeError, match="启用本机桌宠"):
        await service._reload(force_restart=True)
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_page_reload_ready_preserves_hidden_window_and_background_tasks(monkeypatch):
    service = DesktopPetService()
    service._ready = True
    service._visible = False
    dispatch = AsyncMock()
    todos = AsyncMock()
    apply_skin = AsyncMock()
    monkeypatch.setattr(service, "_dispatch_hook", dispatch)
    monkeypatch.setattr(service, "_run_todos", todos)
    monkeypatch.setattr(service, "apply_skin", apply_skin)
    monkeypatch.setattr(service, "_buttons_changed", lambda: None)
    monkeypatch.setattr(service, "_restart_proactive_wake", lambda: None)
    monkeypatch.setattr(service, "_publish_screen_state", AsyncMock())
    monkeypatch.setattr(service, "_publish_exploration_state", AsyncMock())

    service._handle_event({"type": "ready"})
    await asyncio.gather(*service._notifications)

    assert service._ready
    assert not service._visible
    dispatch.assert_not_awaited()
    todos.assert_not_awaited()
    apply_skin.assert_awaited_once()


@pytest.mark.asyncio
async def test_window_interactions_dispatch_observe_hooks(monkeypatch):
    service = DesktopPetService()
    dispatch = AsyncMock()
    monkeypatch.setattr(service, "_dispatch_hook", dispatch)
    service._handle_event({"type": "clicked"})
    service._handle_event({"type": "drag_started", "gesture_id": "one", "offset_x": 2, "offset_y": 30, "scale": 1.5})
    service._handle_event({"type": "drag_ended", "gesture_id": "one"})
    await asyncio.gather(*service._notifications)
    assert [call.args[0] for call in dispatch.await_args_list] == ["clicked", "drag_started", "drag_ended"]
    assert dispatch.await_args_list[1].kwargs == {"gesture_id": "one", "offset_x": 2, "offset_y": 30, "scale": 1.5}
    assert dispatch.await_args_list[2].kwargs == {"gesture_id": "one"}


@pytest.mark.asyncio
async def test_control_waits_for_ack_and_preserves_visibility_on_failure():
    service = DesktopPetService()
    service._ready = service._visible = True
    service._closing = False
    commands = asyncio.Queue()
    service._process = SimpleNamespace(
        stdin=SimpleNamespace(write=lambda data: commands.put_nowait(json.loads(data)), drain=AsyncMock()),
        returncode=None,
    )
    task = asyncio.create_task(service.control("hide"))
    command = await asyncio.wait_for(commands.get(), 1)
    assert not task.done() and service.status()["visible"]
    service._handle_event({"type": "ack", "id": command["id"]})
    assert (await task)["visible"] is False

    task = asyncio.create_task(service.control("show"))
    command = await asyncio.wait_for(commands.get(), 1)
    service._handle_event({"type": "ack", "id": command["id"], "error": "window failed"})
    with pytest.raises(RuntimeError, match="window failed"):
        await task
    assert not service.status()["visible"] and not service._pending


@pytest.mark.asyncio
async def test_ack_timeout_cleans_pending_commands(monkeypatch):
    service = DesktopPetService()
    service._ready = True
    service._closing = False
    service._process = SimpleNamespace(stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock()))
    monkeypatch.setattr(pet_module, "COMMAND_TIMEOUT", 0.01)
    with pytest.raises(TimeoutError):
        await service.control("hide")
    assert not service._pending


@pytest.mark.asyncio
async def test_plugin_notice_does_not_use_chat_reply_or_relay(monkeypatch):
    service = DesktopPetService()
    command = AsyncMock()
    monkeypatch.setattr(service, "_command", command)
    await service.control("say", text="<script>hello</script>")
    command.assert_awaited_once_with({"type": "notice", "parts": [{"kind": "text", "text": "<script>hello</script>"}]})


@pytest.mark.asyncio
async def test_only_internal_owner_reply_is_displayed(monkeypatch):
    service = DesktopPetService()
    service._ready = True
    service._closing = False
    service._visible = True
    command = AsyncMock()
    monkeypatch.setattr(service, "_command", command)
    def message(platform, target, group=None):
        return {"platform": platform, "message_id": "reply-1", "message_info": {
            "additional_config": {"platform_io_target_user_id": target}, "group_info": group,
        }, "raw_message": [{"type": "text", "data": "你好"}]}
    await service.notify_reply(message("webui", "owner"))
    await service.notify_reply(message("internal", "someone"))
    await service.notify_reply(message("internal", "owner", {"group_id": "g"}))
    assert not service._notifications
    await service.notify_reply(message("internal", "owner"))
    await asyncio.gather(*service._notifications)
    command.assert_awaited_once_with({"type": "reply", "parts": [{"kind": "text", "text": "你好"}],
                                    "message_id": "reply-1", "sound": pet_module.global_config.desktop_pet.message_sound})

    # 窗口隐藏时，主人会话的发言先主动显示窗口再出气泡。
    service._visible = False
    control = AsyncMock()
    monkeypatch.setattr(service, "control", control)
    await service.notify_reply(message("internal", "owner"))
    await asyncio.gather(*service._notifications)
    control.assert_awaited_once_with("show")
    assert command.await_count == 2


@pytest.mark.asyncio
async def test_shutdown_terminates_a_stuck_window(monkeypatch):
    service = DesktopPetService()
    worker = asyncio.create_task(asyncio.sleep(100))
    service._worker = worker
    closed = []
    service._process = SimpleNamespace(stdin=SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(pet_module, "STOP_TIMEOUT", 0.01)
    await service.stop()
    assert worker.cancelled() and closed == [True] and service._closing


@pytest.mark.asyncio
async def test_spawn_failure_is_observable_and_does_not_crash_host(monkeypatch):
    service = DesktopPetService()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("cannot spawn")))
    # CREATE_NO_WINDOW is Windows-only; the host still imports and reports status on other systems.
    monkeypatch.setattr(pet_module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    await service._run()
    assert service.status()["error"] == "cannot spawn"
    assert not service.status()["running"] and not service.status()["ready"]
