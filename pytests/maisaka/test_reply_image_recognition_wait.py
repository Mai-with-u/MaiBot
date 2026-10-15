"""视觉规划器与纯文本回复器之间的识图等待回归测试。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat.replyer import maisaka_generator_base as replyer_module
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
from src.common.data_models.message_component_data_model import ImageComponent, MessageSequence
from src.maisaka import reasoning_engine as engine_module
from src.maisaka.context.messages import SessionBackedMessage
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.visual import chat_history_refresher


class StopModelRequest(BaseException):
    """请求构造完成即停止，避免测试调用模型及后处理。"""


@pytest.mark.asyncio
@pytest.mark.parametrize("planner_visual,replyer_visual", [(True, False), (True, True), (False, False)])
async def test_actual_replyer_model_controls_refresh(monkeypatch, planner_visual, replyer_visual):
    generator = object.__new__(BaseMaisakaReplyGenerator)
    generator.request_type = "maisaka.replyer"
    generator.chat_stream = None
    history = [SessionBackedMessage(MessageSequence([]), "识别中", datetime.now())]
    built_texts = []

    async def refresh(messages):
        # 模拟刷新器替换上下文对象，验证实际请求不再使用刷新前的 filtered_history。
        messages[0] = SessionBackedMessage(MessageSequence([]), "识别完成", datetime.now())
        return 1

    refresher = AsyncMock(side_effect=refresh)

    async def generate_response_with_context(*, context_factory, options):
        await context_factory(None, SimpleNamespace(visual=replyer_visual))
        raise StopModelRequest

    generator.express_model = SimpleNamespace(
        task_name="replyer", generate_response_with_context=generate_response_with_context
    )
    monkeypatch.setattr(replyer_module, "resolve_enable_visual_planner", lambda: planner_visual)
    monkeypatch.setattr(generator, "_build_reply_context", AsyncMock(return_value=MaisakaReplyContext()))
    monkeypatch.setattr(generator, "_build_reply_requirements", lambda *args: "")
    monkeypatch.setattr(generator, "_invoke_before_model_request_hook", AsyncMock(side_effect=lambda **kw: kw["request_messages"]))
    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: SimpleNamespace(
        invoke_hook=AsyncMock(return_value=SimpleNamespace(kwargs={}))
    ))

    def build_request_messages(**kwargs):
        built_texts.append(kwargs["chat_history"][0].visible_text)
        return []

    monkeypatch.setattr(generator, "_build_request_messages", build_request_messages)
    with pytest.raises(StopModelRequest):
        await generator.generate_reply_with_context(chat_history=history, image_recognition_refresher=refresher)

    needs_refresh = planner_visual and not replyer_visual
    assert refresher.await_count == int(needs_refresh)
    assert built_texts[-1] == ("识别完成" if needs_refresh else "识别中")


@pytest.mark.asyncio
async def test_completed_description_replaces_placeholder_in_context(monkeypatch):
    image = ImageComponent(binary_data=b"image", binary_hash="test-image", content="")
    history = [SessionBackedMessage(MessageSequence([image]), "[图片，识别中.....]", datetime.now())]
    original_id = history[0].context_item_id
    monkeypatch.setattr(chat_history_refresher, "_lookup_cached_image_description", lambda image_hash: "一只猫")
    unused_builder = AsyncMock()

    count = await chat_history_refresher.refresh_chat_history_visual_placeholders(
        chat_history=history,
        build_history_message=unused_builder,
        build_visible_text=lambda message, source_kind: "",
    )

    assert count == 1
    assert image.content == "[图片：一只猫]"
    assert history[0].visible_text == "[图片：一只猫]"
    assert history[0].context_item_id == original_id
    unused_builder.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_seconds,complete_after_poll", [(0.0, False), (0.5, True), (0.5, False)])
async def test_text_replyer_reuses_planner_wait_and_final_refresh(monkeypatch, wait_seconds, complete_after_poll):
    engine = object.__new__(MaisakaReasoningEngine)
    engine._runtime = SimpleNamespace(_chat_history=["runtime-history"])
    reply_history = []
    clock = [0.0]
    refresh_times = []

    async def refresh_once(history):
        assert history is reply_history
        refresh_times.append(clock[0])
        return 1

    async def sleep(seconds):
        assert 0 < seconds <= 0.2
        clock[0] += seconds

    monkeypatch.setattr(engine, "_refresh_chat_history_visual_placeholders_once", refresh_once)
    monkeypatch.setattr(engine_module, "resolve_enable_visual_planner", lambda: True)
    monkeypatch.setattr(engine_module.global_config.visual, "wait_image_recognize_max_time", wait_seconds)
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(engine_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(engine_module, "has_pending_image_recognition", lambda history: (
        not complete_after_poll or clock[0] < 0.2
    ))

    assert engine._resolve_image_recognition_wait_seconds() == 0
    assert engine._resolve_image_recognition_wait_seconds(wait_for_text_model=True) == wait_seconds
    count = await engine._refresh_chat_history_visual_placeholders(
        chat_history=reply_history, wait_for_text_model=True
    )
    assert count == len(refresh_times)
    if wait_seconds == 0:
        assert refresh_times == [0.0]
    else:
        assert clock[0] == pytest.approx(0.2 if complete_after_poll else wait_seconds)
        assert refresh_times[-1] == refresh_times[-2]  # 结束后仍刷新一次，捕获刚完成的识图结果。
