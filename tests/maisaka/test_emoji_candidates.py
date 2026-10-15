from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import asyncio

from PIL import Image
import pytest

from src.common.data_models.message_component_data_model import EmojiComponent, TextComponent
from src.common.utils import image_path
from src.core.tooling import ToolInvocation
from src.llm_models.payload_content.context_item import ContextImagePart, ContextItemMeta, UserMessageItem
from src.maisaka.builtin_tool import get_all_builtin_tool_specs
from src.maisaka.builtin_tool import show_emoji_list as tool
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.builtin_tool.reply import get_tool_spec
from src.maisaka.visual.message_limiter import limit_latest_images_in_messages


@pytest.fixture
def candidate_context(monkeypatch, tmp_path):
    from src.config.config import global_config

    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", True)
    monkeypatch.setattr(image_path, "PROJECT_ROOT", tmp_path)
    image_bytes = BytesIO()
    Image.new("RGB", (24, 24), "red").save(image_bytes, format="PNG")
    path = tmp_path / "emoji.png"
    path.write_bytes(image_bytes.getvalue())
    emoji = SimpleNamespace(file_hash=sha256(image_bytes.getvalue()).hexdigest(), full_path=path, description="开心")
    monkeypatch.setattr(tool.emoji_manager, "emojis", [emoji])
    monkeypatch.setattr(tool.emoji_manager, "update_emoji_usage", lambda selected: True)
    monkeypatch.setattr(tool, "resolve_enable_visual_planner", lambda: True)
    return BuiltinToolRuntimeContext(SimpleNamespace(), SimpleNamespace(_chat_history=[]))


@pytest.mark.asyncio
async def test_multiple_collages_keep_unique_indices_and_old_selection(candidate_context):
    results = await asyncio.gather(*[
        tool.handle_tool(candidate_context, ToolInvocation("show_emoji_list", call_id=str(index)))
        for index in range(3)
    ])
    messages = [result.post_history_messages[0] for result in results]
    indices = [next(iter(message.emoji_hashes)) for message in messages]
    assert len(set(indices)) == 3
    candidate_context.runtime._chat_history.extend(messages)
    items = await candidate_context.post_process_reply_message_items_async(
        "你好", {"attach_emoji": indices[0]}, skip_post_process=True,
    )
    assert len(items) == 2
    assert all(isinstance(component, TextComponent) for component in items[0].sequence.components)
    assert len(items[1].sequence.components) == 1
    assert isinstance(items[1].sequence.components[0], EmojiComponent)
    assert items[1].sequence.components[0].binary_hash == messages[0].emoji_hashes[indices[0]]
    assert not items[1].quote_previous
    # 历史复制后仍携带相同的编号映射；裁剪并重新展示也不能复用已发出的编号。
    copied = BuiltinToolRuntimeContext(SimpleNamespace(), SimpleNamespace(_chat_history=list(messages)))
    assert (await copied._resolve_emoji_attachment(indices[0])).binary_hash == messages[0].emoji_hashes[indices[0]]
    candidate_context.runtime._chat_history.clear()
    result = await tool.handle_tool(candidate_context, ToolInvocation("show_emoji_list"))
    assert next(iter(result.post_history_messages[0].emoji_hashes)) > max(indices)


@pytest.mark.asyncio
async def test_collage_append_preserves_request_prefix(candidate_context):
    first = (await tool.handle_tool(candidate_context, ToolInvocation("show_emoji_list"))).post_history_messages[0]
    old_item = first.to_context_item()
    ordinary_image = UserMessageItem(meta=ContextItemMeta.create(), parts=(old_item.parts[1],))
    old_request = limit_latest_images_in_messages(
        [ordinary_image, old_item], max_image_num=1, preserved_item_ids={old_item.meta.item_id},
    )
    second = (await tool.handle_tool(candidate_context, ToolInvocation("show_emoji_list"))).post_history_messages[0]
    new_item = second.to_context_item()
    new_request = limit_latest_images_in_messages(
        [ordinary_image, first.to_context_item(), new_item],
        max_image_num=1,
        preserved_item_ids={old_item.meta.item_id, new_item.meta.item_id},
    )
    assert new_request[:len(old_request)] == old_request
    assert first.to_context_item() is old_item
    # 普通图片预算为零也不能删除已进入历史的选择图。
    limited = limit_latest_images_in_messages(
        new_request, max_image_num=0, preserved_item_ids={old_item.meta.item_id, new_item.meta.item_id},
    )
    assert limited[1:] == [old_item, new_item]
    assert not any(isinstance(part, ContextImagePart) for part in limited[0].parts)
    assert not any(isinstance(part, ContextImagePart) for part in first.to_context_item(False).parts)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["开心", "1", True, 0, -1, 1.5, 99999999])
async def test_invalid_selection_fails_without_random_fallback(candidate_context, invalid):
    with pytest.raises(ValueError):
        await candidate_context._resolve_emoji_attachment(invalid)


@pytest.mark.asyncio
async def test_removed_emoji_fails(candidate_context, monkeypatch):
    result = await tool.handle_tool(candidate_context, ToolInvocation("show_emoji_list"))
    message = result.post_history_messages[0]
    candidate_context.runtime._chat_history.append(message)
    monkeypatch.setattr(tool.emoji_manager, "emojis", [])
    with pytest.raises(ValueError, match="已不可用"):
        await candidate_context._resolve_emoji_attachment(next(iter(message.emoji_hashes)))


@pytest.mark.asyncio
async def test_empty_library_and_nonvisual_planner(candidate_context, monkeypatch):
    monkeypatch.setattr(tool, "resolve_enable_visual_planner", lambda: False)
    result = await tool.handle_tool(candidate_context, ToolInvocation("show_emoji_list"))
    assert not result.success and "视觉" in result.error_message
    monkeypatch.setattr(tool, "resolve_enable_visual_planner", lambda: True)
    monkeypatch.setattr(tool.emoji_manager, "emojis", [])
    result = await tool.handle_tool(candidate_context, ToolInvocation("show_emoji_list"))
    assert not result.success and not result.post_history_messages


def test_tool_visibility_and_integer_schema(monkeypatch):
    from src.config.config import global_config

    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", True)
    names = {spec.name for spec in get_all_builtin_tool_specs()}
    assert "show_emoji_list" in names and "send_emoji" not in names
    assert get_tool_spec().parameters_schema["properties"]["attach_emoji"]["type"] == "integer"
    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", False)
    names = {spec.name for spec in get_all_builtin_tool_specs()}
    assert "send_emoji" in names and "show_emoji_list" not in names
