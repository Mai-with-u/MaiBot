from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

from PIL import Image
import pytest

from src.desktop_pet import service as pet_module
from src.desktop_pet.screen import DesktopScreenshot
from src.desktop_pet.service import DesktopPetService


SHOT = DesktopScreenshot(b"test-png", "2026-11-26T12:00:00+00:00", 320, 180, "screen-hash")
MARKER = "SCREEN_TEST_MARKER_517"


@pytest.fixture
def screen(monkeypatch):
    from src.prompt.prompt_manager import prompt_manager

    service = DesktopPetService()
    service._ready, service._closing = True, False
    runtime = Mock()
    monkeypatch.setattr(pet_module.global_config.desktop_pet, "screen_read_enabled", True)
    monkeypatch.setattr(service, "_resolve_owner_runtime", AsyncMock(return_value=runtime), raising=False)
    monkeypatch.setattr(service, "_publish_screen_state", AsyncMock())
    monkeypatch.setattr(prompt_manager, "get_prompt", Mock(return_value="Observe screen"))
    monkeypatch.setattr(prompt_manager, "render_prompt", AsyncMock(return_value="Observe screen"))
    return service, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("visual", [False, True])
async def test_screen_dispatch_is_ready_for_planner(screen, monkeypatch, visual):
    from src.chat.image_system import image_manager as image_module

    service, runtime = screen
    monkeypatch.setattr(pet_module, "resolve_enable_visual_planner", lambda: visual, raising=False)
    describe = AsyncMock(return_value=MARKER)
    monkeypatch.setattr(image_module.image_manager, "describe_image", describe, raising=False)
    await service._deliver_screen(SHOT, frame_times=[SHOT.captured_at])

    runtime.enqueue_desktop_screen.assert_called_once()
    text, image = runtime.enqueue_desktop_screen.call_args.args
    assert SHOT.captured_at in text
    assert "attach_pic" not in text
    if visual:
        describe.assert_not_awaited()
        assert image.binary_data == SHOT.data
    else:
        describe.assert_awaited_once()
        assert MARKER in text
        assert image is None


@pytest.mark.asyncio
async def test_stopping_during_caption_discards_late_result(screen, monkeypatch):
    from src.chat.image_system import image_manager as image_module

    service, runtime = screen
    monkeypatch.setattr(pet_module, "resolve_enable_visual_planner", lambda: False, raising=False)
    started, release = asyncio.Event(), asyncio.Event()

    async def describe(*args, **kwargs):
        started.set()
        await release.wait()
        return MARKER

    monkeypatch.setattr(image_module.image_manager, "describe_image", describe, raising=False)
    delivery = asyncio.create_task(service._deliver_screen(SHOT))
    await asyncio.wait_for(started.wait(), timeout=1)
    await service._stop_screen_reader()
    release.set()
    await delivery
    runtime.enqueue_desktop_screen.assert_not_called()


@pytest.mark.asyncio
async def test_empty_caption_does_not_wake_planner(screen, monkeypatch):
    from src.chat.image_system import image_manager as image_module

    service, runtime = screen
    monkeypatch.setattr(pet_module, "resolve_enable_visual_planner", lambda: False, raising=False)
    monkeypatch.setattr(image_module.image_manager, "describe_image", AsyncMock(return_value=""), raising=False)
    with pytest.raises(RuntimeError, match="未返回画面描述"):
        await service._deliver_screen(SHOT)
    runtime.enqueue_desktop_screen.assert_not_called()


@pytest.mark.asyncio
async def test_screen_context_is_bounded_and_separate_from_chat_history(monkeypatch):
    from src.maisaka.runtime import MaisakaHeartFlowChatting
    from src.maisaka.visual.chat_history_refresher import has_pending_image_recognition

    runtime = object.__new__(MaisakaHeartFlowChatting)
    runtime.session_id = "owner-test"
    runtime._internal_turn_queue = asyncio.Queue()
    runtime._desktop_screen_observations = []
    runtime._desktop_screen_wake_queued = False
    runtime._chat_history = []
    runtime._prepare_task_enqueue = Mock()
    runtime._build_proactive_trigger_message = Mock()
    monkeypatch.setattr(pet_module.global_config.desktop_pet, "screen_read_enabled", True)
    for caption in ("old", "previous", MARKER):
        runtime.enqueue_desktop_screen(caption, None)

    assert runtime._internal_turn_queue.qsize() == 1
    assert runtime._internal_turn_queue.get_nowait() == "desktop_pet_screen"
    assert [obs.visible_text for obs in runtime.desktop_screen_observations()] == ["previous", MARKER]
    assert runtime._chat_history == []
    assert not has_pending_image_recognition(runtime._chat_history)
    assert runtime.consume_desktop_screen_wake() is runtime._build_proactive_trigger_message.return_value

    runtime.enqueue_desktop_screen("new", None)
    assert runtime._internal_turn_queue.get_nowait() == "desktop_pet_screen"
    runtime.clear_desktop_screen()
    assert runtime.consume_desktop_screen_wake() is None
    runtime._focus_group = object()
    with pytest.raises(RuntimeError, match="共享 Focus"):
        runtime.enqueue_desktop_screen(MARKER, None)
    assert runtime.desktop_screen_observations() == ()


@pytest.mark.asyncio
async def test_private_screen_caption_uses_vlm_without_image_storage_or_failure_snapshot(monkeypatch):
    from src.chat.image_system import image_manager as image_module
    from src.llm_models import request_snapshot
    from src.llm_models.request_privacy import request_snapshots_suppressed, suppress_request_snapshots
    from src.prompt.prompt_manager import prompt_manager

    operation = AsyncMock(return_value=SimpleNamespace(response=f" {MARKER} "))
    monkeypatch.setattr(image_module, "_is_vlm_task_configured", lambda: True)
    monkeypatch.setattr(image_module.vlm, "generate_response_for_image", operation)
    monkeypatch.setattr(prompt_manager, "get_prompt", Mock(return_value="caption"))
    monkeypatch.setattr(prompt_manager, "render_prompt", AsyncMock(return_value="caption"))
    save = AsyncMock(side_effect=AssertionError("desktop screenshots must not be saved"))
    monkeypatch.setattr(image_module.image_manager, "ensure_image_saved", save)

    with suppress_request_snapshots():
        assert MARKER in await image_module.image_manager.describe_image(SHOT.data, "png")
        result = await asyncio.to_thread(
            request_snapshot.save_failed_request_snapshot,
            api_provider=None, client_type="test", error=RuntimeError("test"),
            internal_request={"request_items": [MARKER]}, model_info=None,
            operation="test", provider_request={},
        )
        assert result is None
    assert not request_snapshots_suppressed()
    operation.assert_awaited_once()
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("observation_has_image, visual, expected", [
    (False, False, "caption"), (True, True, "image"), (True, False, "none"),
])
async def test_planner_consumes_private_observation_without_preview_or_hook_payload(
    monkeypatch, observation_has_image, visual, expected,
):
    from src.common.data_models.llm_service_data_models import LLMResponseResult
    from src.common.data_models.message_component_data_model import ImageComponent, MessageSequence, TextComponent
    from src.llm_models.payload_content.context_item import ContextImagePart, ContextTextPart
    from src.llm_models.request_privacy import request_snapshots_suppressed
    from src.maisaka import chat_loop_service
    from src.maisaka.context.messages import InternalContextMessage

    components = [TextComponent(MARKER)]
    if observation_has_image:
        buffer = BytesIO()
        Image.new("RGB", (16, 16), "red").save(buffer, format="PNG")
        components.append(ImageComponent(binary_hash=SHOT.digest, binary_data=buffer.getvalue()))
    observation = InternalContextMessage(
        raw_message=MessageSequence(components), visible_text=MARKER, timestamp=datetime.now(),
        source_kind="desktop_pet_screen",
    )

    async def model(context_factory, **kwargs):
        parts = [part for item in context_factory(None) for part in item.parts]
        has_marker = any(isinstance(part, ContextTextPart) and MARKER in part.text for part in parts)
        has_image = any(isinstance(part, ContextImagePart) for part in parts)
        assert has_marker is (expected != "none")
        assert has_image is (expected == "image")
        assert request_snapshots_suppressed() is (expected != "none")
        return LLMResponseResult()

    async def hook(name, **kwargs):
        assert MARKER not in repr(kwargs)
        return SimpleNamespace(kwargs=kwargs)

    service = chat_loop_service.MaisakaChatLoopService(session_id="owner-test", is_group_chat=False)
    monkeypatch.setattr(service, "_resolve_enable_visual_message", lambda _: visual)
    monkeypatch.setattr(service, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))
    monkeypatch.setattr(service, "_get_llm_chat_client", lambda _: SimpleNamespace(generate_response_with_context=model))
    preview = Mock(return_value=SimpleNamespace(preview_access=SimpleNamespace(preview_web_uri=None), panel=None))
    monkeypatch.setattr(chat_loop_service.PromptCLIVisualizer, "build_prompt_section_result", preview)
    monkeypatch.setattr(pet_module.global_config.debug, "save_maisaka_reasoning", True)
    result = await service.chat_loop_step(
        [], tool_definitions=[], system_prompt="Test Planner", screen_observations=lambda: (observation,),
    )
    if expected != "none":
        preview.assert_not_called()
        assert result.private_screen
        assert result.request_messages == []
        assert service.planner_request_snapshot is None
    else:
        assert not result.private_screen
        preview.assert_called_once()


@pytest.mark.asyncio
async def test_private_planner_progress_contains_only_metadata(monkeypatch):
    from src.maisaka import reasoning_engine
    from src.maisaka.chat_loop_service import ChatResponse
    from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType

    output = ContextItemBuilder().set_role(RoleType.Assistant).add_text_content(MARKER).build()
    response = ChatResponse(
        output_items=(output,), request_messages=[], selected_history_count=0, tool_count=0,
        prompt_tokens=12, built_message_count=1, completion_tokens=2, total_tokens=14, private_screen=True,
    )
    runtime = SimpleNamespace(session_id="owner-test", _monitor_run_id="test-run")
    engine = reasoning_engine.MaisakaReasoningEngine(runtime)
    event = AsyncMock()
    monkeypatch.setattr(reasoning_engine, "emit_planner_snapshot", event)
    await engine._emit_planner_progress(
        SimpleNamespace(cycle_id=1),
        SimpleNamespace(response=response, planner_duration_ms=15, planner_started_at=0, planner_ended_at=1),
        tools=[{"tool_name": "reply", "tool_args": {"content": MARKER}}],
    )
    payload = event.call_args.kwargs
    assert payload["planner_content"] is None
    assert payload["tools"] is None
    assert MARKER not in repr(payload)
    assert payload["planner_prompt_tokens"] == 12
