from contextlib import asynccontextmanager
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
@pytest.mark.parametrize("configured_seconds", [None, 37])
async def test_screen_caption_uses_configured_timeout(screen, monkeypatch, configured_seconds):
    from src.chat.image_system import image_manager as image_module
    from src.desktop_pet.config import DesktopPetConfig

    service, runtime = screen
    settings = pet_module.global_config.desktop_pet
    if configured_seconds is not None:
        monkeypatch.setattr(
            pet_module,
            "global_config",
            SimpleNamespace(
                desktop_pet=settings.model_copy(update={"screen_description_timeout_seconds": configured_seconds}),
                visual=pet_module.global_config.visual,
            ),
        )
    actual_timeouts = []

    @asynccontextmanager
    async def timeout(seconds):
        actual_timeouts.append(seconds)
        yield

    monkeypatch.setattr(pet_module, "resolve_enable_visual_planner", lambda: False)
    monkeypatch.setattr(pet_module.asyncio, "timeout", timeout)
    monkeypatch.setattr(image_module.image_manager, "describe_image", AsyncMock(return_value=MARKER))
    await service._deliver_screen(SHOT)

    assert actual_timeouts == [45 if configured_seconds is None else configured_seconds]
    assert DesktopPetConfig().screen_description_timeout_seconds == 45
    runtime.enqueue_desktop_screen.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_result", [TimeoutError(), "", OSError()])
async def test_failed_caption_drops_only_one_batch(screen, monkeypatch, first_result):
    from src.chat.image_system import image_manager as image_module

    service, runtime = screen
    settings = pet_module.global_config.desktop_pet
    monkeypatch.setattr(
        pet_module,
        "global_config",
        SimpleNamespace(
            desktop_pet=settings.model_copy(update={"screen_read_interval_seconds": 0}),
            visual=pet_module.global_config.visual,
        ),
    )
    monkeypatch.setattr(pet_module, "resolve_enable_visual_planner", lambda: False)
    monkeypatch.setattr(service, "capture_screen", AsyncMock(return_value=SHOT))
    monkeypatch.setattr(pet_module, "compose_screen_timeline", lambda frames: frames[-1])
    timeline = SimpleNamespace(changed=True, frames=[SHOT], add=Mock(), delivered=Mock())
    timeline.delivered.side_effect = lambda: setattr(timeline, "changed", timeline.delivered.call_count < 2)
    monkeypatch.setattr(pet_module, "ScreenTimeline", lambda: timeline)
    warning = Mock()
    monkeypatch.setattr(pet_module.logger, "warning", warning)
    describe = AsyncMock(side_effect=[first_result, MARKER])
    monkeypatch.setattr(image_module.image_manager, "describe_image", describe)
    delivered = asyncio.Event()
    runtime.enqueue_desktop_screen.side_effect = lambda *_: delivered.set()

    await service._screen_action("start")
    try:
        await asyncio.wait_for(delivered.wait(), timeout=1)
        assert service._screen_reading()
        assert not service._screen_error
        warning.assert_called_once()
        assert describe.await_count == 2
        runtime.enqueue_desktop_screen.assert_called_once()
        assert MARKER in runtime.enqueue_desktop_screen.call_args.args[0]
    finally:
        await service._stop_screen_reader()


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
            api_provider=None,
            client_type="test",
            error=RuntimeError("test"),
            internal_request={"request_items": [MARKER]},
            model_info=None,
            operation="test",
            provider_request={},
        )
        assert result is None
    assert not request_snapshots_suppressed()
    operation.assert_awaited_once()
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observation_has_image, visual, expected",
    [
        (False, False, "caption"),
        (True, True, "image"),
        (True, False, "none"),
    ],
)
async def test_planner_consumes_private_observation_without_preview_or_hook_payload(
    monkeypatch,
    observation_has_image,
    visual,
    expected,
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
        raw_message=MessageSequence(components),
        visible_text=MARKER,
        timestamp=datetime.now(),
        source_kind="desktop_pet_screen",
    )

    on_private_screen = Mock()

    async def model(context_factory, **kwargs):
        items = context_factory(None)
        parts = [part for item in items for part in item.parts]
        has_marker = any(isinstance(part, ContextTextPart) and MARKER in part.text for part in parts)
        has_image = any(isinstance(part, ContextImagePart) for part in parts)
        assert has_marker is (expected != "none")
        assert has_image is (expected == "image")
        assert request_snapshots_suppressed() is (expected != "none")
        assert on_private_screen.call_count == (0 if expected == "none" else 1)
        snapshot = SimpleNamespace(copy_with=lambda **changes: SimpleNamespace(**changes), context_items=items)
        return LLMResponseResult(request_snapshot=snapshot)

    async def hook(name, **kwargs):
        assert MARKER not in repr(kwargs)
        return SimpleNamespace(kwargs=kwargs)

    service = chat_loop_service.MaisakaChatLoopService(session_id="owner-test", is_group_chat=False)
    monkeypatch.setattr(service, "_resolve_enable_visual_message", lambda _: visual)
    monkeypatch.setattr(service, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))
    monkeypatch.setattr(
        service, "_get_llm_chat_client", lambda _: SimpleNamespace(generate_response_with_context=model)
    )
    preview = Mock(return_value=SimpleNamespace(preview_access=SimpleNamespace(preview_web_uri=None), panel=None))
    monkeypatch.setattr(chat_loop_service.PromptCLIVisualizer, "build_prompt_section_result", preview)
    monkeypatch.setattr(pet_module.global_config.debug, "save_maisaka_reasoning", True)
    result = await service.chat_loop_step(
        [],
        tool_definitions=[],
        system_prompt="Test Planner",
        screen_observations=lambda: (observation,),
        on_private_screen=on_private_screen,
    )
    if expected != "none":
        preview.assert_not_called()
        assert result.private_screen
        assert result.request_messages == []
        assert service.planner_request_snapshot is not None
        history_parts = [part for item in service.planner_request_snapshot.request.context_items for part in item.parts]
        assert any(isinstance(part, ContextTextPart) and MARKER in part.text for part in history_parts)
        assert any(isinstance(part, ContextImagePart) for part in history_parts) is (expected == "image")
        assert result.private_reply_snapshot is not None
        assert MARKER not in repr(result.private_reply_snapshot.request.context_items)
        assert not any(
            isinstance(part, ContextImagePart)
            for item in result.private_reply_snapshot.request.context_items
            for part in item.parts
        )
    else:
        assert not result.private_screen
        preview.assert_called_once()


@pytest.mark.asyncio
async def test_private_planner_progress_contains_only_tool_names_and_metadata(monkeypatch):
    from src.maisaka import reasoning_engine
    from src.maisaka.chat_loop_service import ChatResponse
    from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType

    output = ContextItemBuilder().set_role(RoleType.Assistant).add_text_content(MARKER).build()
    response = ChatResponse(
        output_items=(output,),
        request_messages=[],
        selected_history_count=0,
        tool_count=0,
        prompt_tokens=12,
        built_message_count=1,
        completion_tokens=2,
        total_tokens=14,
        private_screen=True,
    )
    runtime = SimpleNamespace(session_id="owner-test", _monitor_run_id="test-run")
    engine = reasoning_engine.MaisakaReasoningEngine(runtime)
    event = AsyncMock()
    monkeypatch.setattr(reasoning_engine, "emit_planner_snapshot", event)
    await engine._emit_planner_progress(
        SimpleNamespace(cycle_id=1),
        SimpleNamespace(
            response=response,
            planner_duration_ms=15,
            planner_started_at=0,
            planner_ended_at=1,
            private_screen_tool_names=["wait", "reply"],
        ),
        tools=[{"tool_name": "reply", "tool_args": {"content": MARKER}}],
        active_tool_call_id="secret-call-id",
    )
    payload = event.call_args.kwargs
    assert payload["planner_content"] is None
    assert payload["tools"] is None
    assert payload["private_screen_tool_names"] == ["wait", "reply"]
    assert payload["active_tool_call_id"] == ""
    assert MARKER not in repr(payload)
    assert payload["planner_prompt_tokens"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("private_screen", [False, True])
async def test_interrupt_retains_request_privacy_without_screen_observations(monkeypatch, private_screen):
    from src.llm_models.exceptions import ReqAbortException
    from src.maisaka import reasoning_engine

    runtime = SimpleNamespace(
        _chat_history=[],
        _has_pending_messages=lambda: False,
        _update_stage_status=Mock(),
        log_prefix="[test]",
    )
    engine = reasoning_engine.MaisakaReasoningEngine(runtime)
    partial = SimpleNamespace(display_text=lambda: MARKER, model_name="test")
    result = await engine._handle_planner_interrupt(
        exc=ReqAbortException("interrupted", partial_output=partial),
        round_index=0,
        round_text="",
        current_stage_started_at=0,
        action_tool_count=0,
        private_screen=private_screen,
    )
    assert result.response.private_screen is private_screen
    assert (MARKER in result.response.content) is not private_screen
    assert bool(runtime._chat_history) is not private_screen


@pytest.mark.asyncio
@pytest.mark.parametrize("has_snapshot", [False, True])
async def test_history_compaction_waits_for_planner_snapshot(monkeypatch, has_snapshot):
    from src.maisaka import reasoning_engine

    old, recent = SimpleNamespace(count_in_context=True), object()
    planner_snapshot = object() if has_snapshot else None
    request_state = SimpleNamespace(planner_request_snapshot=planner_snapshot, last_planner_prompt_tokens=60)
    runtime = SimpleNamespace(
        _chat_history=[old, recent],
        _max_context_size=2,
        _max_context_tokens=None,
        _chat_loop_service=request_state,
        _get_pending_wait_tool_call_ids=set,
        _monitor_run_id="run",
        session_id="test",
        _log_history_trimmed=Mock(),
        _trigger_trimmed_history_learning=AsyncMock(),
    )
    monkeypatch.setattr(
        reasoning_engine,
        "process_chat_history_after_cycle",
        Mock(
            return_value=SimpleNamespace(
                changed_count=1,
                removed_messages=[old],
                history=[recent],
                remaining_context_count=1,
            )
        ),
    )
    monkeypatch.setattr(reasoning_engine, "emit_flow_step", AsyncMock())
    summary = AsyncMock(return_value=None)
    monkeypatch.setattr(reasoning_engine, "build_context_summary", summary)
    engine = reasoning_engine.MaisakaReasoningEngine(runtime)
    await engine._post_process_chat_history_after_cycle(SimpleNamespace(cycle_id=1, time_records={}))
    if has_snapshot:
        assert runtime._chat_history == [recent]
        assert request_state.last_planner_prompt_tokens == 0
        summary.assert_awaited_once_with(
            [old],
            planner_snapshot=planner_snapshot,
            previous_summary=None,
            recent_messages=[recent],
            session_id="test",
        )
    else:
        assert runtime._chat_history == [old, recent]
        assert request_state.last_planner_prompt_tokens == 60
        summary.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_snapshot", [True, False])
async def test_private_planner_body_can_send_reply(monkeypatch, with_snapshot):
    from src.llm_models.request_privacy import request_snapshots_suppressed
    from src.maisaka import reasoning_engine

    target = SimpleNamespace(message_id="owner-message")
    safe_snapshot = object() if with_snapshot else None
    response = SimpleNamespace(private_screen=True, private_reply_snapshot=safe_snapshot, prompt_html_uri=None)
    runtime = SimpleNamespace(
        session_id="owner-test",
        chat_stream=SimpleNamespace(is_group_session=False),
        _update_stage_status=Mock(),
    )
    engine = reasoning_engine.MaisakaReasoningEngine(runtime)
    monkeypatch.setattr(reasoning_engine, "BuiltinToolRuntimeContext", Mock())
    monkeypatch.setattr(engine, "_emit_planner_progress", AsyncMock())
    supplement = AsyncMock()
    monkeypatch.setattr(reasoning_engine, "generate_reply_supplement", supplement)

    async def send(_ctx, _arguments, *, call_id, target_message, supplement_runner):
        assert target_message is target
        assert request_snapshots_suppressed()
        if with_snapshot:
            await supplement_runner()
        else:
            assert supplement_runner is None
        return SimpleNamespace(success=True, metadata={}, get_history_content=lambda: "sent")

    execute = AsyncMock(side_effect=send)
    monkeypatch.setattr(reasoning_engine, "execute_reply", execute)
    state = reasoning_engine.CycleRuntimeState(response=response, reply_target_message=target)
    await engine._reply_from_planner_content(MARKER, SimpleNamespace(time_records={}), state)
    assert state.cycle_end.reason == "planner_reply_end"
    assert state.private_screen_tool_names == ["reply"]
    execute.assert_awaited_once()
    if with_snapshot:
        supplement.assert_awaited_once_with(safe_snapshot, MARKER, "owner-test", False)
    else:
        supplement.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["planner.progress", "planner.finalized"])
@pytest.mark.parametrize("names", [None, [], ["wait", "reply"]])
async def test_monitor_serializes_private_round_without_details(monkeypatch, event_type, names):
    from src.maisaka.monitor import events

    broadcast = AsyncMock()
    monkeypatch.setattr(events, "_broadcast", broadcast)
    await events.emit_planner_snapshot(
        event_type=event_type,
        session_id="owner-test",
        cycle_id=1,
        run_id="test-run",
        planner_request_messages=[],
        planner_selected_history_count=0,
        planner_tool_count=0,
        planner_content=MARKER,
        planner_reasoning=MARKER,
        planner_tool_calls=[],
        planner_native_tool_calls=[],
        planner_prompt_tokens=1,
        planner_completion_tokens=1,
        planner_total_tokens=2,
        planner_duration_ms=12,
        planner_prompt_html_uri=MARKER,
        tools=[{"tool_name": "wait", "tool_args": {"reason": MARKER}}],
        active_tool_call_id=MARKER,
        end_detail=MARKER,
        private_screen_tool_names=names,
    )
    event, payload = broadcast.call_args.args
    assert event == event_type
    if names is None:
        assert "private_screen_tool_names" not in payload
        assert payload["planner"]["content"] == MARKER
        assert payload["tools"][0]["tool_args"] == {"reason": MARKER}
    else:
        assert payload["private_screen_tool_names"] == names
        assert MARKER not in repr(payload)
        assert payload["tools"] == []
