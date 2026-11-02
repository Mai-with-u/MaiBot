from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

import json

import pytest

from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.llm_models.payload_content.context_item import get_item_text
from src.maisaka.context.image_attachment import resolve_context_image
from src.maisaka.context.message_id_alias import to_display_message_id
from src.maisaka.context.messages import InternalContextMessage, ToolResultMediaMessage
from src.maisaka.runtime import MaisakaHeartFlowChatting


def runtime():
    return SimpleNamespace(
        _running=True, _chat_history=[], _ensure_background_tasks_running=Mock(),
        _arm_forced_turn_state=Mock(), _queue_proactive_turn=Mock(), _build_proactive_trigger_message=Mock(),
    )


def picture(color):
    output = BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format="PNG")
    return ImageComponent(binary_hash="", binary_data=output.getvalue())


@pytest.mark.asyncio
@pytest.mark.parametrize("visual", [True, False])
async def test_relay_image_references_resolve_the_exact_original_images(visual):
    chat = runtime()
    images = [picture("red"), picture("blue")]
    receipt = MaisakaHeartFlowChatting.enqueue_internal_task(
        chat, source_kind="agent_relay", text="画好了", reason="Agent 传话", images=images,
    )
    message = chat._chat_history[0]
    text = get_item_text(message.to_context_item(enable_visual_message=visual))
    # 从模型实际收到的文本中读取附件参数，再通过正常附件解析入口取图。
    references = json.JSONDecoder().raw_decode(text.split("attach_pic=", 1)[1])[0]
    assert len(references) == 2
    assert references[0]["msg_id"] == to_display_message_id(receipt["task_id"])
    for reference, expected in zip(references, images, strict=True):
        actual = await resolve_context_image(chat, reference["msg_id"], reference["index"])
        assert actual.binary_data == expected.binary_data
    with pytest.raises(ValueError, match="图片序号超出范围"):
        await resolve_context_image(chat, references[0]["msg_id"], 2)


@pytest.mark.asyncio
async def test_svg_relay_is_text_and_fabricated_media_index_is_reported_verbatim():
    chat = runtime()
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"></svg>'
    receipt = MaisakaHeartFlowChatting.enqueue_internal_task(
        chat, source_kind="agent_relay", text=svg, reason="Agent 传话",
    )
    message = chat._chat_history[0]
    assert get_item_text(message.to_context_item()) == svg
    assert not any(isinstance(component, ImageComponent) for component in message.raw_message.components)
    source = "tool_result:bba463584adb453aac2ed7898a1564ae:1"
    with pytest.raises(ValueError) as error:
        await resolve_context_image(chat, source)
    assert source in str(error.value)
    assert "没有找到工具媒体" in str(error.value)
    assert "mhglskb" not in str(error.value)
    with pytest.raises(ValueError, match="该消息共有 0 张图片"):
        await resolve_context_image(chat, to_display_message_id(receipt["task_id"]))


@pytest.mark.asyncio
async def test_emoji_in_message_is_numbered_with_images_and_keeps_emoji_type():
    chat = runtime()
    message_id = "1566716611891699777"
    emoji = EmojiComponent(binary_hash="emoji-hash", binary_data=picture("yellow").binary_data)
    image = picture("purple")
    # 模型在上下文里按顺序看到「表情包、图片」两张图，序号应与之一致
    chat._chat_history.append(InternalContextMessage(
        raw_message=MessageSequence([TextComponent("发这张"), emoji, image]),
        visible_text="发这张", timestamp=datetime.now(), message_id=message_id,
    ))

    resolved_emoji = await resolve_context_image(chat, to_display_message_id(message_id), 0)
    assert type(resolved_emoji) is EmojiComponent
    assert (resolved_emoji.binary_hash, resolved_emoji.binary_data) == (emoji.binary_hash, emoji.binary_data)

    resolved_image = await resolve_context_image(chat, message_id, 1)
    assert type(resolved_image) is ImageComponent
    assert resolved_image.binary_data == image.binary_data

    with pytest.raises(ValueError, match="该消息共有 2 张图片"):
        await resolve_context_image(chat, message_id, 2)


@pytest.mark.asyncio
async def test_actual_tool_media_index_still_resolves():
    chat = runtime()
    image = picture("green")
    source = "tool_result:call_test:1"
    chat._chat_history.append(ToolResultMediaMessage(
        raw_message=MessageSequence([TextComponent("工具图片"), image]),
        visible_text="工具图片", timestamp=datetime.now(), message_id=source, tool_call_id="call_test",
    ))
    resolved = await resolve_context_image(chat, source)
    assert resolved.binary_data == image.binary_data
