"""常规回复的图片附件、可选 @ 与表情发送模式。"""

from contextlib import contextmanager
from datetime import datetime
from threading import get_ident
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.chat.utils.utils import ProcessedResponseSegment
from src.common.data_models.message_component_data_model import (
    AtComponent,
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.common.data_models.reply_generation_data_models import LLMCompletionResult, ReplyGenerationResult
from src.common.prompt_i18n import list_prompt_templates, load_prompt
from src.config.config import global_config
from src.core.tooling import ToolInvocation
from src.llm_models.payload_content.context_item import ContextItemMeta, ContextTextPart, UserMessageItem
from src.maisaka.builtin_tool import build_builtin_tool_handlers, get_all_builtin_tool_specs
from src.maisaka.builtin_tool import context as context_module
from src.maisaka.builtin_tool import reply as reply_tool
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.context.emoji_candidates import EmojiCandidateMessage
from src.maisaka.context.messages import SessionBackedMessage


@pytest.fixture
def reply_context(monkeypatch):
    monkeypatch.setattr(global_config.chat, "enable_reply_at", True)
    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", False)
    user = SimpleNamespace(user_id="user-1", user_nickname="小明", user_cardname="")
    source = SimpleNamespace(
        message_id="msg-1",
        message_info=SimpleNamespace(user_info=user),
        raw_message=MessageSequence([
            TextComponent("图片"),
            ImageComponent(binary_hash="image-0", binary_data=b"first"),
            ImageComponent(binary_hash="image-1", binary_data=b"second"),
        ]),
    )
    runtime = SimpleNamespace(
        _chat_history=[],
        find_source_message_by_id=lambda message_id: source if message_id == "msg-1" else None,
        session_id="session-1",
        chat_stream=SimpleNamespace(platform="qq"),
        log_prefix="test",
        _update_stage_status=lambda *args: None,
        record_planner_reply=lambda: None,
    )
    return BuiltinToolRuntimeContext(SimpleNamespace(), runtime)


@pytest.mark.parametrize("enable_at", [False, True])
@pytest.mark.parametrize("new_emoji", [False, True])
def test_reply_tools_follow_independent_switches(reply_context, monkeypatch, enable_at, new_emoji):
    monkeypatch.setattr(global_config.chat, "enable_reply_at", enable_at)
    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", new_emoji)
    names = {spec.name for spec in get_all_builtin_tool_specs()}
    schema = reply_tool.get_tool_spec().parameters_schema
    properties = schema["properties"]
    assert schema["required"] == ["msg_id", "reply_reference"]
    assert properties["reply_reference"]["minLength"] == 1
    assert "attach_pic" in properties
    assert ("attach_at" in properties) == enable_at
    assert ("attach_emoji" in properties) == new_emoji
    assert ("show_emoji_list" in names) == new_emoji
    assert ("send_emoji" in names) != new_emoji
    assert "send_image" not in names
    assert "send_image" not in build_builtin_tool_handlers(reply_context)


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_args", [
    {},
    {"reply_reference": None},
    {"reply_reference": ""},
    {"reply_reference": " \n\t "},
    {"reply_reference": 123},
    {"reply_reference": False},
    {"reply_reference": []},
    {"reply_reference": {}},
])
async def test_reply_requires_nonempty_reference_before_generation(reply_context, monkeypatch, reference_args):
    generator = AsyncMock()
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generator)
    result = await reply_tool.handle_tool(
        reply_context,
        ToolInvocation("reply", arguments={"msg_id": "msg-1", **reference_args}, reasoning="不能代替必填参考"),
    )
    assert not result.success
    assert "reply_reference" in result.error_message
    generator.assert_not_called()


@pytest.mark.asyncio
async def test_reply_sends_selected_picture_separately_from_at_text(reply_context, monkeypatch):
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(return_value=(
        True, ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="看这张图")),
    )))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", lambda **kwargs: generator)
    monkeypatch.setattr(reply_tool, "_invoke_before_post_process_hook", AsyncMock(return_value=(
        "看这张图", {"skip_post_process": True},
    )))
    sender = AsyncMock(return_value=SimpleNamespace(message_id="sent-1"))
    monkeypatch.setattr(reply_tool.send_service, "_send_to_target_with_message", sender)
    arguments = {
        "msg_id": "msg-1", "reply_reference": "  展示第二张图片  ",
        "attach_at": ["msg-1"], "attach_pic": [{"msg_id": "msg-1", "index": 1}],
    }

    result = await reply_tool.handle_tool(reply_context, ToolInvocation("reply", arguments=arguments))

    assert result.success
    assert sender.await_count == 2
    components = sender.await_args_list[0].kwargs["message_sequence"].components
    assert isinstance(components[0], AtComponent) and components[0].target_user_id == "user-1"
    assert not any(isinstance(component, ImageComponent) for component in components)
    image_components = sender.await_args_list[1].kwargs["message_sequence"].components
    assert len(image_components) == 1
    assert isinstance(image_components[0], ImageComponent) and image_components[0].binary_data == b"second"
    assert generator.generate_reply_with_context.call_args.kwargs["reply_tool_args"] == {
        "reply_reference": "展示第二张图片",
        "attach_at": ["msg-1"], "attach_pic": [{"msg_id": "msg-1", "index": 1}],
    }
    assert generator.generate_reply_with_context.call_args.kwargs["reply_reason"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "description,expected_text",
    [("开心,得意,比心", "[表情包: 开心,得意,比心]"), ("  ", "[表情包]")],
)
async def test_reply_attached_emoji_renders_like_inbound_emoji(
    reply_context, monkeypatch, tmp_path, description, expected_text
):
    """附带表情的文本表示必须是 [表情包: 描述]，裸描述会作为自身历史回灌并被模型模仿进正文。"""

    from src.common.utils import image_path as image_path_module
    from src.emoji_system.emoji_manager import emoji_manager

    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", True)
    monkeypatch.setattr(image_path_module, "PROJECT_ROOT", tmp_path)
    emoji_file = tmp_path / "emoji.gif"
    emoji_file.write_bytes(b"emoji-bytes")
    selected_emoji = SimpleNamespace(file_hash="emoji-hash", description=description, full_path=emoji_file)
    monkeypatch.setattr(
        emoji_manager, "get_emoji_by_hash", lambda emoji_hash: selected_emoji if emoji_hash == "emoji-hash" else None
    )
    monkeypatch.setattr(emoji_manager, "update_emoji_usage", lambda emoji: None)
    timestamp = datetime.now()
    candidate_item = UserMessageItem(
        meta=ContextItemMeta.create(timestamp=timestamp),
        parts=(ContextTextPart("表情包选择图"),),
    )
    reply_context.runtime._chat_history.append(
        EmojiCandidateMessage(
            item=candidate_item,
            emoji_hashes={1: "emoji-hash"},
            visible_text="表情包选择图",
            timestamp=timestamp,
        )
    )
    reply_result = ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="任务都完成啦"))
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(return_value=(True, reply_result)))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", lambda **kwargs: generator)
    monkeypatch.setattr(
        reply_tool,
        "_invoke_before_post_process_hook",
        AsyncMock(return_value=("任务都完成啦", {"skip_post_process": True})),
    )
    sender = AsyncMock(return_value=SimpleNamespace(message_id="sent-1"))
    monkeypatch.setattr(reply_tool.send_service, "_send_to_target_with_message", sender)

    result = await reply_tool.handle_tool(
        reply_context,
        ToolInvocation("reply", arguments={"msg_id": "msg-1", "reply_reference": "庆祝任务完成", "attach_emoji": 1}),
    )

    assert result.success
    assert [call.kwargs["processed_plain_text"] for call in sender.call_args_list] == ["任务都完成啦", expected_text]
    # 发往平台的仍是表情图片二进制，content 只是该表情的文本表示
    emoji_component = sender.call_args_list[1].kwargs["message_sequence"].components[0]
    assert isinstance(emoji_component, EmojiComponent)
    assert emoji_component.binary_data == b"emoji-bytes"
    assert emoji_component.content == expected_text
    assert result.structured_content["reply_text"] == f"任务都完成啦{expected_text}"


@pytest.mark.asyncio
async def test_reply_keeps_async_split_and_quote_metadata_with_picture(reply_context, monkeypatch):
    splitter = AsyncMock(return_value=[ProcessedResponseSegment("今田见"), ProcessedResponseSegment("天", True)])
    monkeypatch.setattr(context_module, "process_llm_response_segments_async", splitter)
    items = await reply_context.post_process_reply_message_items_async(
        "今天见", {"attach_pic": [{"msg_id": "msg-1", "index": 0}]},
    )
    splitter.assert_awaited_once()
    assert [item.quote_previous for item in items] == [False, True, False]
    assert len(items[0].sequence.components) == 1
    assert all(isinstance(component, TextComponent) for component in items[1].sequence.components)
    assert len(items[2].sequence.components) == 1
    assert items[2].sequence.components[0].binary_data == b"first"


@pytest.mark.asyncio
async def test_picture_from_tool_media_history(reply_context):
    reply_context.runtime._chat_history.append(SessionBackedMessage(
        raw_message=MessageSequence([ImageComponent(binary_hash="tool-image", binary_data=b"tool-result")]),
        visible_text="工具图片",
        timestamp=datetime.now(),
        message_id="tool_result:call_x:1",
        source_kind="tool_media",
    ))
    items = await reply_context.post_process_reply_message_items_async(
        "找到图片了", {"attach_pic": [{"media_index": "tool_result:call_x:1", "index": 0}]},
        skip_post_process=True,
    )
    assert len(items) == 2
    assert isinstance(items[0].sequence.components[0], TextComponent)
    assert len(items[1].sequence.components) == 1
    assert items[1].sequence.components[0].binary_data == b"tool-result"


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [-1, 2, "invalid"])
async def test_bad_picture_index_fails(reply_context, index):
    with pytest.raises(ValueError, match="图片序号"):
        await reply_context._resolve_image_attachment({"msg_id": "msg-1", "index": index})


@pytest.mark.asyncio
async def test_unreadable_picture_does_not_shift_indices(reply_context, monkeypatch):
    source = reply_context.runtime.find_source_message_by_id("msg-1")
    first_image = source.raw_message.components[1]
    first_image.binary_data = b""
    monkeypatch.setattr(first_image, "load_image_binary", AsyncMock(side_effect=ValueError("图片数据损坏")))
    with pytest.raises(ValueError, match="图片数据损坏"):
        await reply_context._resolve_image_attachment({"msg_id": "msg-1", "index": 0})
    second_image = await reply_context._resolve_image_attachment({"msg_id": "msg-1", "index": 1})
    assert second_image.binary_data == b"second"


@pytest.mark.asyncio
async def test_picture_database_lookup_runs_off_event_loop(monkeypatch, tmp_path):
    from src.common.database import database
    from src.common.utils import image_path as image_path_module

    monkeypatch.setattr(image_path_module, "PROJECT_ROOT", tmp_path)
    image_path = tmp_path / "picture.png"
    image_path.write_bytes(b"stored-image")
    loop_thread = get_ident()
    lookup_threads = []

    @contextmanager
    def fake_db_session():
        lookup_threads.append(get_ident())
        yield SimpleNamespace(exec=lambda statement: SimpleNamespace(first=lambda: SimpleNamespace(full_path=image_path)))

    monkeypatch.setattr(database, "get_db_session", fake_db_session)
    image = ImageComponent(binary_hash="stored-image")
    await image.load_image_binary()
    assert image.binary_data == b"stored-image"
    assert lookup_threads and all(thread_id != loop_thread for thread_id in lookup_threads)


@pytest.mark.asyncio
@pytest.mark.parametrize("argument,config,field", [
    ({"attach_at": ["msg-1"]}, "chat", "enable_reply_at"),
    ({"attach_emoji": 1}, "emoji", "use_new_send_logic"),
])
async def test_disabled_attachment_rejected_before_generation(reply_context, monkeypatch, argument, config, field):
    monkeypatch.setattr(getattr(global_config, config), field, False)
    generator = AsyncMock()
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generator)
    result = await reply_tool.handle_tool(
        reply_context, ToolInvocation("reply", arguments={"msg_id": "msg-1", "reply_reference": "测试附件", **argument})
    )
    assert not result.success
    generator.assert_not_called()
    with pytest.raises(ValueError):
        await reply_context.post_process_reply_message_items_async("你好", argument, skip_post_process=True)


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP", "ko"])
def test_attachment_prompt_covers_mentions_and_media(reply_context, locale):
    generator = object.__new__(BaseMaisakaReplyGenerator)
    generator._load_prompt = lambda name, **kwargs: load_prompt(name, locale=locale, **kwargs)
    arguments: Dict[str, Any] = {"attach_pic": [{"msg_id": "msg-1"}], "attach_at": ["msg-1"], "attach_emoji": 26}
    prompt = generator._build_reply_attachment_prompt(
        chat_history=[], reply_message=reply_context.runtime.find_source_message_by_id("msg-1"), reply_tool_args=arguments,
    )
    assert "26" in prompt and "@小明" in prompt and "URL" in prompt
    assert "{attachments}" not in prompt
    metadata = list_prompt_templates(locale=locale)["reply_attachments"].metadata
    assert metadata.display_name and metadata.description
