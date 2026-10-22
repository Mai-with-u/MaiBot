from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from io import BytesIO
from unittest.mock import patch
import base64

from PIL import Image
import pytest

from src.common.data_models.message_component_data_model import EmojiComponent, ImageComponent, MessageSequence, TextComponent
from src.llm_models.payload_content.context_item import ContextItemBuilder, ContextItemMeta, ContextImagePart
from src.maisaka.context import messages as context
from src.maisaka.visual.deferred_images import (
    append_context_image,
    defer_context_image_encoding,
    transform_context_image,
)
from src.maisaka.visual.message_limiter import limit_latest_images_in_messages


def image_bytes(fmt="PNG"):
    output = BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, format=fmt)
    return output.getvalue()


def history(fmt="PNG"):
    data = image_bytes(fmt)
    return [context.ContentContextMessage(
        raw_message=MessageSequence([TextComponent("before"), ImageComponent(binary_hash="", binary_data=data),
                                     ImageComponent(binary_hash="", binary_data=data), TextComponent("after")]),
        visible_text="message", timestamp=datetime(2026, 1, 1),
    ) for _ in range(3)]


@pytest.mark.parametrize("limit", [0, 1, 2, 6, 10])
@pytest.mark.parametrize("visual", [True, False])
@pytest.mark.parametrize("fmt", ["PNG", "BMP"])
def test_identical_items_and_only_retained_images_encoded(limit, visual, fmt):
    entries = history(fmt)
    def build():
        items = [entry.to_context_item(enable_visual_message=visual) for entry in entries]
        return limit_latest_images_in_messages(items, max_image_num=limit)
    expected = build()
    optimized = defer_context_image_encoding(lambda items: items)(build)
    with patch.object(context, "_encode_image_for_context", wraps=context._encode_image_for_context) as encode:
        actual = optimized()
    assert actual == expected
    assert encode.call_count == (min(limit, 6) if visual else 0)
    assert all(entry.raw_message.components[1].binary_data for entry in entries)


def test_emoji_resize_is_skipped_when_discarded():
    entry = context.ContentContextMessage(
        raw_message=MessageSequence([EmojiComponent(binary_data=image_bytes(), binary_hash="deferred-test")]),
        visible_text="emoji", timestamp=datetime(2026, 1, 1),
    )
    def build():
        return limit_latest_images_in_messages([entry.to_context_item(limit_emoji_size=True)], max_image_num=0)
    expected = build()
    with patch.object(context.emoji_image_cache, "get", side_effect=AssertionError("discarded emoji encoded")):
        assert defer_context_image_encoding(lambda items: items)(build)() == expected


def test_preserved_images_and_existing_items_keep_identity():
    fixed = ContextItemBuilder().add_image_content("png", base64.b64encode(image_bytes()).decode()).build()
    def build():
        builder = ContextItemBuilder()
        append_context_image(builder, lambda: ("jpeg", "aGVsbG8="))
        return limit_latest_images_in_messages([fixed, builder.build()], max_image_num=0,
                                               preserved_item_ids={fixed.meta.item_id})
    result = defer_context_image_encoding(lambda items: items)(build)()
    assert result[0] is fixed
    assert not any(isinstance(p, ContextImagePart) for p in result[1].parts)


def test_scope_restored_after_failure_and_between_threads():
    def build(value):
        builder = ContextItemBuilder().set_meta(ContextItemMeta.create(item_id=value))
        append_context_image(builder, lambda: ("png", value))
        return [builder.build()]
    optimized = defer_context_image_encoding(lambda items: items)(build)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(optimized, ["aA==", "Yg==", "Yw==", "ZA=="]))
    assert [items[0].parts[0].image_base64 for items in values] == ["aA==", "Yg==", "Yw==", "ZA=="]
    @defer_context_image_encoding(lambda items: items)
    def failing():
        append_context_image(ContextItemBuilder(), lambda: ("png", "aA=="))
        raise ValueError("expected")
    with pytest.raises(ValueError, match="expected"):
        failing()
    assert build("aA==")[0].parts[0].image_base64 == "aA=="


def test_planner_uses_limit_before_encoding(monkeypatch):
    from src.config.config import global_config
    from src.maisaka.chat_loop_service import MaisakaChatLoopService
    entries = history()
    monkeypatch.setattr(global_config.visual, "max_image_num", 2)
    service = MaisakaChatLoopService(chat_system_prompt="system")
    with patch.object(context, "_encode_image_for_context", wraps=context._encode_image_for_context) as encode:
        actual, count = service._build_request_messages(entries, enable_visual_message=True)
    assert encode.call_count == 2
    expected, expected_count = service._build_request_messages.__wrapped__(service, entries, enable_visual_message=True)
    assert count == expected_count
    # Generated system/time item identities vary per build; historical items must match exactly.
    assert actual[1:count+1] == expected[1:expected_count+1]
    assert [item.parts for item in actual] == [item.parts for item in expected]


def test_summary_matches_eager_output(monkeypatch):
    from src.config.config import global_config
    from src.maisaka.memory.mid_term import _build_summary_prompt_messages
    monkeypatch.setattr(global_config.visual, "max_image_num", 2)
    entries = history()
    kwargs = dict(instruction_prompt="summary", enable_visual_message=True)
    expected = _build_summary_prompt_messages.__wrapped__(entries, **kwargs)
    with patch.object(context, "_encode_image_for_context", wraps=context._encode_image_for_context) as encode:
        actual = _build_summary_prompt_messages(entries, **kwargs)
    assert encode.call_count == 2
    assert actual[1:] == expected[1:]
    assert actual[0].parts == expected[0].parts


def test_summary_early_return_preserves_existing_content(monkeypatch):
    from src.config.config import global_config
    from src.maisaka.memory import mid_term
    monkeypatch.setattr(global_config.visual, "max_image_num", 0)
    monkeypatch.setattr(mid_term, "MAX_SUMMARY_INPUT_CHARS", 1)
    entries = history()
    kwargs = dict(instruction_prompt="summary", enable_visual_message=True)
    expected = mid_term._build_summary_prompt_messages.__wrapped__(entries, **kwargs)
    actual = mid_term._build_summary_prompt_messages(entries, **kwargs)
    assert [item.parts for item in actual] == [item.parts for item in expected]


def test_replyer_matches_eager_output(monkeypatch):
    from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
    from src.config.config import global_config
    monkeypatch.setattr(global_config.visual, "max_image_num", 2)
    monkeypatch.setattr(global_config.debug, "replyer_retro_prompt", False)
    service = BaseMaisakaReplyGenerator(llm_client_cls=lambda **kwargs: None, load_prompt_func=lambda *args, **kwargs: "prompt")
    # 使用通用图文夹具隔离 IM 来源检查，仍走真实历史构建、筛选和编码链路。
    monkeypatch.setattr(service, "_is_replyer_filtered_history_message", lambda message: False)
    for name in ("_build_keyword_reaction_prompt", "_build_system_prompt", "_build_final_user_message",
                 "_build_temporary_reply_style_message", "_select_temporary_reply_style",
                 "_build_reply_reference_message", "_build_requested_reply_style_message"):
        monkeypatch.setattr(service, name, lambda *args, **kwargs: "fixed")
    entries = history()
    kwargs = dict(reply_message=None, reply_reason="reason", enable_visual_message=True)
    expected = service._build_request_messages.__wrapped__(service, entries, **kwargs)
    with patch.object(context, "_encode_image_for_context", wraps=context._encode_image_for_context) as encode:
        actual = service._build_request_messages(entries, **kwargs)
    assert encode.call_count == 2
    assert [item.parts for item in actual] == [item.parts for item in expected]


@pytest.mark.parametrize("limit", [0, 2, 5])
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_retro_replyer_resizes_only_retained_images(monkeypatch, limit, mode):
    from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
    from src.config.config import global_config

    monkeypatch.setattr(global_config.visual, "max_image_num", limit)
    monkeypatch.setattr(global_config.debug, "replyer_retro_prompt", True)
    service = object.__new__(BaseMaisakaReplyGenerator)
    # 仅隔离模板和 IM 来源依赖，保留实际入口、图片筛选、缩放及延迟编码链路。
    monkeypatch.setattr(service, "_is_replyer_filtered_history_message", lambda message: False)
    monkeypatch.setattr(service, "_select_retro_prompt_name", lambda **kwargs: "test")
    monkeypatch.setattr(service, "_build_personality_prompt", lambda: "persona")
    monkeypatch.setattr(service, "_build_retro_template_context", lambda **kwargs: {})
    monkeypatch.setattr(service, "_load_prompt", lambda name, **kwargs: kwargs["dialogue_prompt"] + "\nreply", raising=False)
    monkeypatch.setattr(service, "_render_retro_history_line", lambda message, history_by_id: message.visible_text)
    output = BytesIO()
    Image.new(mode, (800, 400), (255, 0, 0, 128) if mode == "RGBA" else "red").save(output, format="PNG")
    data = output.getvalue()
    entries = [context.ContentContextMessage(
        raw_message=MessageSequence([
            TextComponent("before"), ImageComponent(binary_hash="", binary_data=data),
            EmojiComponent(binary_hash="", binary_data=data), TextComponent("after"),
        ]),
        visible_text="message", timestamp=datetime(2026, 1, 1),
    ) for _ in range(3)]
    kwargs = dict(reply_message=None, reply_reason="reason", enable_visual_message=True)
    expected = service._build_request_messages.__wrapped__(service, entries, **kwargs)
    with patch.object(context, "_encode_image_for_context", wraps=context._encode_image_for_context) as encode:
        actual = service._build_request_messages(entries, **kwargs)
    assert encode.call_count == limit
    assert [item.parts for item in actual] == [item.parts for item in expected]
    parts = [part for item in actual for part in item.parts if isinstance(part, ContextImagePart)]
    assert len(parts) == limit
    for index, part in enumerate(parts):
        with Image.open(BytesIO(base64.b64decode(part.image_base64, validate=True))) as image:
            max_side = 128 if index < limit - 3 else 640
            assert image.size == (max_side, max_side // 2)
            assert image.mode == mode
            if mode == "RGBA":
                assert image.getpixel((0, 0))[3] == 128
    assert all(entry.raw_message.components[1].binary_data == data for entry in entries)
    assert all(isinstance(entry.raw_message.components[2], EmojiComponent) for entry in entries)


@pytest.mark.parametrize("limit", [0, 3])
def test_deferred_image_variants_keep_source_and_skip_discarded_work(limit):
    from src.chat.replyer.retro_prompt import _resize_retro_image

    data = image_bytes()
    with (
        patch.object(context, "_encode_image_for_context", wraps=context._encode_image_for_context) as encode,
        patch("src.chat.replyer.retro_prompt._resize_retro_image", wraps=_resize_retro_image) as resize,
    ):
        @defer_context_image_encoding(lambda items: items)
        def build():
            builder = ContextItemBuilder()
            append_context_image(builder, lambda: encode(data, "png"))
            original = builder.build().parts[0]
            for max_side in (4, 2):
                part = transform_context_image(original, partial(resize, max_side=max_side))
                builder.add_image_content(part.image_format, part.image_base64)
            return limit_latest_images_in_messages([builder.build()], max_image_num=limit)

        result = build()
    assert encode.call_count == limit
    assert resize.call_count == (2 if limit else 0)
    parts = [part for item in result for part in item.parts if isinstance(part, ContextImagePart)]
    sizes = []
    for part in parts:
        with Image.open(BytesIO(base64.b64decode(part.image_base64, validate=True))) as image:
            sizes.append(image.size)
    assert sizes == ([(8, 8), (4, 4), (2, 2)] if limit else [])
