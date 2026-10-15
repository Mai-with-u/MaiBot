from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Dict, List
import base64

from PIL import Image

import pytest

from src.chat.replyer import retro_prompt
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.chat.replyer.retro_prompt import (
    RETRO_GROUP_LIGHT_PROMPT,
    RETRO_GROUP_PROMPT,
    RETRO_PRIVATE_PROMPT,
    RETRO_PRIVATE_SELF_PROMPT,
    SHORT_REPLY_STYLE,
)
from src.common.i18n import set_locale
from src.common.data_models.message_component_data_model import (
    AtComponent,
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    TextComponent,
    VoiceComponent,
)
from src.common.prompt_i18n import (
    PROMPTS_ROOT,
    clear_prompt_cache,
    extract_prompt_placeholders,
    list_prompt_templates,
    load_prompt,
)
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextImagePart, ContextItemBuilder, ContextTextPart, RoleType
from src.maisaka.context.messages import ModelOutputContextMessage
from src.maisaka.context.planner_messages import build_session_backed_text_message

RETRO_LOCALES = ("zh-CN", "en-US", "ja-JP")

GROUP_PLACEHOLDERS = {
    "bot_name",
    "dialogue_prompt",
    "expression_habits_block",
    "extra_info_block",
    "keywords_reaction_prompt",
    "planner_reasoning",
    "reply_style",
    "reply_target_block",
    "time_block",
}

PRIVATE_PLACEHOLDERS = (GROUP_PLACEHOLDERS - {"bot_name"}) | {"sender_name"}
"""私聊模板用对话者名字，且不重复标注 bot 名字（人设块里已经有）。"""

SUPPLIED_PLACEHOLDERS = GROUP_PLACEHOLDERS | PRIVATE_PLACEHOLDERS
"""复古模板上下文必须覆盖到的全部占位符。"""


@pytest.fixture(autouse=True)
def reset_prompt_cache(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(global_config.experimental.attention_drift, "enabled", False)
    set_locale("zh-CN")
    clear_prompt_cache()
    yield
    clear_prompt_cache()
    set_locale("zh-CN")


def build_retro_generator(*, is_group_session: bool) -> BaseMaisakaReplyGenerator:
    """构造只带复古组装所需字段的 replyer 实例。"""

    generator = object.__new__(BaseMaisakaReplyGenerator)
    generator.chat_stream = SimpleNamespace(session_id="session-1", is_group_session=is_group_session)
    generator._load_prompt = load_prompt
    return generator


def build_history_message(text: str) -> Any:
    return build_session_backed_text_message(
        speaker_name="小明",
        text=text,
        timestamp=datetime(2026, 1, 1, 12, 30, 0),
        source_kind="user",
        message_id="m-1",
    )


def build_fake_target_message() -> Any:
    user_info = SimpleNamespace(user_id="u-1", user_cardname="小明", user_nickname="小明")
    return SimpleNamespace(platform="qq", message_info=SimpleNamespace(user_info=user_info))


def build_original_history(components: List[Any], *, message_id: str = "m-1", cached_text: str = "") -> Any:
    message = build_history_message(cached_text)
    message.message_id = message_id
    message.raw_message = MessageSequence(components)
    message.original_message = build_fake_target_message()
    message.original_message.raw_message = MessageSequence(components)
    message.original_message.processed_plain_text = cached_text
    message.original_message.timestamp = message.timestamp
    message.original_message.message_id = message_id
    message.original_message.is_notify = False
    return message


@pytest.mark.parametrize("component,expected", [
    (TextComponent("踢了麦麦"), "踢了麦麦"),
    (ImageComponent(binary_hash="image", content="[图片：内存条截图]"), "[图片：内存条截图]"),
    (ImageComponent(binary_hash="pending-image"), "[图片，识别中.....]"),
    (ImageComponent(binary_hash="long-image", content="[图片：" + "详细描述" * 80 + "]"), "[图片：" + "详细描述" * 80 + "]"),
    (EmojiComponent(binary_hash="emoji", content="[表情包: 疑惑]"), "[表情包: 疑惑]"),
    (VoiceComponent(binary_hash="voice", content="[语音: 我在测试]"), "[语音: 我在测试]"),
    (AtComponent(target_user_id="u-2", target_user_nickname="小红"), "@小红"),
])
def test_retro_renders_components_when_original_text_cache_is_empty(component: Any, expected: str) -> None:
    generator = build_retro_generator(is_group_session=True)
    message = build_original_history([component])
    normal_items = generator._build_history_messages([message], enable_visual_message=False)
    assert "".join(read_item_text(item) for item in normal_items) == expected
    assert generator._build_retro_dialogue_block([message]) == f"[12:30:00] 小明说：{expected}"
    assert message.original_message.processed_plain_text == ""


def test_retro_quote_uses_current_original_image_description() -> None:
    generator = build_retro_generator(is_group_session=True)
    target = build_original_history(
        [ImageComponent(binary_hash="image", content="[图片：最新的数据格式截图]")],
        message_id="image-1",
        cached_text="[image]",
    )
    quote = ReplyComponent(
        target_message_id="image-1", target_message_content="[image]", target_message_sender_nickname="旧昵称"
    )
    reply = build_original_history([quote, TextComponent("记得看看数据格式喵")], message_id="reply-1")
    dialogue = generator._build_retro_dialogue_block([target, reply])
    assert "小明[回复了小明的消息: [图片：最新的数据格式截图]] 说：记得看看数据格式喵" in dialogue
    assert "[image]" not in dialogue
    assert "旧昵称" not in dialogue
    assert quote.target_message_content == "[image]"


def test_retro_quote_keeps_saved_content_outside_history() -> None:
    generator = build_retro_generator(is_group_session=True)
    quote = ReplyComponent(
        target_message_id="old-1", target_message_content="原消息内容", target_message_sender_cardname="小红"
    )
    reply = build_original_history([quote, TextComponent("同意")])
    assert generator._build_retro_dialogue_block([reply]) == "[12:30:00] 小明[回复了小红的消息: 原消息内容] 说：同意"


@pytest.mark.parametrize("target_in_history", [False, True])
def test_retro_reply_target_uses_same_quote_format(
    target_in_history: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = build_retro_generator(is_group_session=True)
    monkeypatch.setattr(retro_prompt, "is_bot_self", lambda platform, user_id: False)
    target = build_original_history(
        [ImageComponent(binary_hash="image", content="[图片：最新截图]")], message_id="image-1"
    )
    quote = ReplyComponent(
        target_message_id="image-1", target_message_sender_nickname="小红", target_message_content="原消息内容"
    )
    reply = build_original_history([quote, TextComponent("@麦麦 报销麦麦")], message_id="reply-1")
    history = [target, reply] if target_in_history else [reply]
    expected_reference = "[回复了小明的消息: [图片：最新截图]]" if target_in_history else "[回复了小红的消息: 原消息内容]"
    assert generator._build_retro_reply_target_block(reply.original_message, history) == (
        f"现在小明{expected_reference} 说：@麦麦 报销麦麦。引起了你的注意"
    )


def test_retro_does_not_emit_truly_empty_history_lines() -> None:
    generator = build_retro_generator(is_group_session=True)
    assert generator._build_retro_dialogue_block([build_original_history([TextComponent("")])]) == ""


@pytest.mark.parametrize("visual_enabled,max_images", [(False, 1), (True, 1), (True, 0)])
@pytest.mark.parametrize("image_source", ["user", "guided_reply"])
def test_retro_visual_request_keeps_original_image(
    visual_enabled: bool, max_images: int, image_source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)
    monkeypatch.setattr(global_config.experimental, "replyer_retro_prompt", True)
    monkeypatch.setattr(global_config.visual, "max_image_num", max_images)
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2)).save(image_bytes, format="PNG")
    image_message = build_original_history([
        ImageComponent(binary_hash="image", binary_data=image_bytes.getvalue(), content="[图片：测试原图]"),
    ], message_id="image-1")
    image_message.source_kind = image_source
    reply = build_original_history([
        ReplyComponent(target_message_id="image-1"), TextComponent("这张图什么意思"),
    ], message_id="reply-1")
    items = generator._build_request_messages(
        chat_history=[image_message, reply], reply_message=None, reply_reason="解释图片",
        enable_visual_message=visual_enabled,
    )
    assert [item.role for item in items] == [RoleType.System, RoleType.User]
    images = [part for item in items for part in item.parts if isinstance(part, ContextImagePart)]
    assert len(images) == (1 if visual_enabled and max_images else 0)
    if images:
        normal_items = [image_message.to_context_item(enable_visual_message=True)]
        normal_images = [part for item in normal_items for part in item.parts if isinstance(part, ContextImagePart)]
        assert images == normal_images
        image_index = next(i for i, part in enumerate(items[1].parts) if isinstance(part, ContextImagePart))
        assert "[图片]" in items[1].parts[image_index - 1].text
        assert "这张图什么意思" not in items[1].parts[image_index - 1].text
        assert "这张图什么意思" in items[1].parts[image_index + 1].text
    assert isinstance(items[1].parts[-1], ContextTextPart)
    assert "解释图片" in items[1].parts[-1].text
    text = read_item_text(items[1])
    expected_content = "[图片]" if images else "[图片：测试原图]"
    assert f"[回复了小明的消息: {expected_content}]" in text
    assert f"小明[回复了小明的消息: {expected_content}] 说：这张图什么意思" in text
    if images:
        assert "测试原图" not in text
    assert "[发言内容]" not in text
    image_speaker = f"{global_config.bot.nickname}(你)" if image_source == "guided_reply" else "小明"
    assert text.count(f"[12:30:00] {image_speaker}说：{expected_content}") == 1
    assert image_message.original_message.raw_message.components[0].content == "[图片：测试原图]"


@pytest.mark.parametrize("locale", RETRO_LOCALES)
def test_retro_images_stay_between_history_messages_in_single_user(
    locale: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)
    set_locale(locale)
    monkeypatch.setattr(global_config.visual, "max_image_num", 2)
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2)).save(image_bytes, format="PNG")
    history = [
        build_history_message("FIRST_MESSAGE"),
        build_original_history([
            ImageComponent(binary_hash="image-1", binary_data=image_bytes.getvalue(), content="FIRST_IMAGE"),
        ], message_id="image-1"),
        build_history_message("MIDDLE_MESSAGE"),
        build_original_history([
            ImageComponent(binary_hash="image-2", binary_data=image_bytes.getvalue(), content="SECOND_IMAGE"),
        ], message_id="image-2"),
        build_history_message("LAST_MESSAGE"),
    ]
    items = generator._build_retro_request_messages(
        chat_history=history, reply_message=None, reply_reason="FINAL_REPLY_REFERENCE", enable_visual_message=True,
    )
    assert [item.role for item in items] == [RoleType.System, RoleType.User]
    parts = items[1].parts
    assert [type(part) for part in parts] == [
        ContextTextPart, ContextImagePart, ContextTextPart, ContextImagePart, ContextTextPart,
    ]
    assert parts[0].text.index("FIRST_MESSAGE") < parts[0].text.index("[图片]")
    assert parts[2].text.index("MIDDLE_MESSAGE") < parts[2].text.index("[图片]")
    assert parts[4].text.index("LAST_MESSAGE") < parts[4].text.index("FINAL_REPLY_REFERENCE")
    text = read_item_text(items[1])
    for marker in ("FIRST_MESSAGE", "MIDDLE_MESSAGE", "LAST_MESSAGE"):
        assert text.count(marker) == 1
    assert "FIRST_IMAGE" not in text
    assert "SECOND_IMAGE" not in text


@pytest.mark.parametrize("component_type", [ImageComponent, EmojiComponent])
@pytest.mark.parametrize("max_images", [0, 1, 2])
@pytest.mark.parametrize("has_original", [False, True])
def test_retro_visual_descriptions_follow_actual_media_quota(
    component_type: Any, max_images: int, has_original: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)
    monkeypatch.setattr(retro_prompt, "is_bot_self", lambda platform, user_id: False)
    monkeypatch.setattr(global_config.visual, "max_image_num", max_images)
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2)).save(image_bytes, format="PNG")
    # 同一发言中重复的图片数据仍分别占配额，描述不能按 hash 一并删除。
    message = build_original_history([
        TextComponent("看看这两张"),
        component_type(binary_hash="old", binary_data=image_bytes.getvalue(), content="OLD_DESCRIPTION"),
        component_type(binary_hash="new", binary_data=image_bytes.getvalue(), content="NEW_DESCRIPTION"),
        component_type(binary_hash="missing", content="MISSING_DESCRIPTION"),
    ])
    target = message.original_message
    if not has_original:
        message.original_message = None
        message.visible_text = "[小明]看看这两张OLD_DESCRIPTIONNEW_DESCRIPTIONMISSING_DESCRIPTION"
    items = generator._build_retro_request_messages(
        chat_history=[message], reply_message=target, reply_reason="回答",
        enable_visual_message=True,
    )
    text = read_item_text(items[1])
    assert "看看这两张" in text
    assert ("OLD_DESCRIPTION" in text) == (max_images < 2)
    assert ("NEW_DESCRIPTION" in text) == (max_images == 0)
    assert "MISSING_DESCRIPTION" in text
    assert sum(isinstance(part, ContextImagePart) for part in items[1].parts) == max_images
    assert message.raw_message.components[1].content == "OLD_DESCRIPTION"
    assert message.raw_message.components[2].content == "NEW_DESCRIPTION"


@pytest.mark.parametrize("max_images", [1, 2, 3, 4, 5])
def test_retro_resizes_latest_three_images_across_messages(
    max_images: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)
    monkeypatch.setattr(global_config.visual, "max_image_num", max_images)
    output = BytesIO()
    Image.new("RGB", (1600, 800), "red").save(output, format="PNG")
    original_bytes = output.getvalue()
    # 最后一条发言含两张图，不能把“最新三张”误算成“最新三条发言”。
    components = [
        ImageComponent(binary_hash=f"image-{index}", binary_data=original_bytes, content=f"DESCRIPTION_{index}")
        for index in range(5)
    ]
    history = [
        build_original_history([component], message_id=f"message-{index}")
        for index, component in enumerate(components[:3])
    ]
    history.append(build_original_history(components[3:], message_id="message-3"))
    items = generator._build_retro_request_messages(
        chat_history=history, reply_message=None, reply_reason="回答", enable_visual_message=True,
    )
    images = [part for part in items[1].parts if isinstance(part, ContextImagePart)]
    assert len(images) == max_images
    for index, part in enumerate(images):
        expected_side = 128 if index < max_images - 3 else 640
        with Image.open(BytesIO(base64.b64decode(part.image_base64))) as image:
            assert image.size == (expected_side, expected_side // 2)
    text = read_item_text(items[1])
    for index, component in enumerate(components):
        assert component.binary_data == original_bytes
        assert component.content == f"DESCRIPTION_{index}"
        assert (component.content in text) == (index < 5 - max_images)


@pytest.mark.parametrize("size,expected_size", [((400, 1200), (213, 640)), ((1, 1200), (1, 640))])
def test_retro_resize_keeps_transparency_and_portrait_ratio(
    size: Any, expected_size: Any,
) -> None:
    output = BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 0)).save(output, format="PNG")
    part = ContextImagePart("png", base64.b64encode(output.getvalue()).decode("ascii"))
    resized = retro_prompt._resize_retro_image(part, 640)
    assert resized.image_format == "png"
    with Image.open(BytesIO(base64.b64decode(resized.image_base64))) as image:
        assert image.size == expected_size
        assert image.getpixel((0, 0))[3] == 0


def read_item_text(item: Any) -> str:
    return "".join(part.text for part in item.parts if isinstance(part, ContextTextPart))


def isolate_retro_blocks(generator: BaseMaisakaReplyGenerator, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离注意事项块和关键词反应块，避免测试依赖聊天流与关键词配置。"""

    monkeypatch.setattr(generator, "_build_group_chat_attention_block", lambda session_id: "")
    monkeypatch.setattr(generator, "_build_keyword_reaction_prompt", lambda **kwargs: "")


@pytest.mark.parametrize(
    ("locale", "heading"),
    [("zh-CN", "注意力漂移风格："), ("en-US", "Attention drift style:"), ("ja-JP", "注意ドリフトスタイル：")],
)
def test_retro_replyer_attention_drift_follows_live_config(
    monkeypatch: pytest.MonkeyPatch, locale: str, heading: str
) -> None:
    set_locale(locale)
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)

    for enabled in (False, True, False):
        monkeypatch.setattr(global_config.experimental.attention_drift, "enabled", enabled)
        items = generator._build_retro_request_messages(
            chat_history=[], reply_message=None, reply_reason="测试回复原因"
        )
        assert read_item_text(items[0]).count(heading) == int(enabled)


def test_retro_request_messages_fill_single_template(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)

    items = generator._build_retro_request_messages(
        chat_history=[build_history_message("晚上吃什么")],
        reply_message=None,
        reply_reason="小明在问晚饭",
        expression_habits="【表达习惯参考】当被问吃什么时可以用随便来表达。",
        reply_requirements="这次请直接回答吃什么。",
        stream_id="session-1",
        think_level=1,
        reply_tool_args={},
    )

    assert [item.role for item in items] == [RoleType.System, RoleType.User]

    prompt = read_item_text(items[1])
    # 所有占位符都必须被填满，模板里不允许残留花括号
    assert "{" not in prompt
    assert "现在请你读读之前的聊天记录，把握当前的话题" in prompt
    assert "【表达习惯参考】当被问吃什么时可以用随便来表达。" in prompt
    assert "这次请直接回答吃什么。" in prompt
    assert "小明在问晚饭" in prompt
    assert "[12:30:00] 小明说：晚上吃什么" in prompt


@pytest.mark.parametrize("locale", RETRO_LOCALES)
@pytest.mark.parametrize(
    "prompt_name", [RETRO_GROUP_PROMPT, RETRO_GROUP_LIGHT_PROMPT, RETRO_PRIVATE_PROMPT, RETRO_PRIVATE_SELF_PROMPT]
)
def test_retro_moves_persona_and_guidelines_to_system(
    locale: str, prompt_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)
    set_locale(locale)
    monkeypatch.setattr(generator, "_select_retro_prompt_name", lambda **kwargs: prompt_name)
    monkeypatch.setattr(generator, "_build_personality_prompt", lambda: "PERSONA_BLOCK")
    monkeypatch.setattr(generator, "_build_group_chat_attention_block", lambda session_id: "GUIDELINES_BLOCK")
    monkeypatch.setattr(generator, "_select_temporary_reply_style", lambda: "REPLY_STYLE_BLOCK")
    items = generator._build_retro_request_messages(
        chat_history=[build_history_message("HISTORY_BLOCK")],
        reply_message=None,
        reply_reason="REASON_BLOCK",
    )

    assert [item.role for item in items] == [RoleType.System, RoleType.User]
    system_prompt = read_item_text(items[0])
    user_prompt = read_item_text(items[1])
    assert system_prompt == "PERSONA_BLOCK\n\nGUIDELINES_BLOCK"
    assert "PERSONA_BLOCK" not in user_prompt
    assert "GUIDELINES_BLOCK" not in user_prompt
    assert "REPLY_STYLE_BLOCK" not in system_prompt
    assert user_prompt.count("REPLY_STYLE_BLOCK") == 1
    assert user_prompt.index("HISTORY_BLOCK") < user_prompt.index("REASON_BLOCK")
    assert user_prompt.index("REASON_BLOCK") < user_prompt.index("REPLY_STYLE_BLOCK")


@pytest.mark.parametrize("locale", RETRO_LOCALES)
@pytest.mark.parametrize("is_group_session,think_level", [(True, 1), (True, 0), (False, 1)])
def test_retro_keeps_sent_bot_messages_and_discards_planner_output(
    locale: str, is_group_session: bool, think_level: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = build_retro_generator(is_group_session=is_group_session)
    isolate_retro_blocks(generator, monkeypatch)
    set_locale(locale)
    bot_reply = build_session_backed_text_message(
        speaker_name=global_config.bot.nickname,
        text="我想吃面",
        timestamp=datetime(2026, 1, 1, 12, 30, 1),
        source_kind="guided_reply",
        message_id="bot-1",
    )
    assistant_output = ModelOutputContextMessage(
        output_item=ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("历史模型输出").build()
    )
    chat_history = [
        build_history_message("晚上吃什么"),
        bot_reply,
        assistant_output,
        build_history_message("那就吃面"),
    ]
    keyword_history: List[Any] = []

    def capture_keyword_history(**kwargs: Any) -> str:
        keyword_history.extend(kwargs["chat_history"])
        return ""

    monkeypatch.setattr(generator, "_build_keyword_reaction_prompt", capture_keyword_history)
    items = generator._build_retro_request_messages(
        chat_history=chat_history,
        reply_message=None,
        reply_reason="一起吃面",
        think_level=think_level,
    )

    assert [item.role for item in items] == [RoleType.System, RoleType.User]
    prompt = read_item_text(items[1])
    assert prompt.index("晚上吃什么") < prompt.index("我想吃面") < prompt.index("那就吃面")
    assert "一起吃面" in prompt
    assert f"{global_config.bot.nickname}(你)说：我想吃面" in prompt
    assert "历史模型输出" not in prompt
    assert keyword_history == [chat_history[0], bot_reply, chat_history[-1]]
    dialogue = generator._build_retro_dialogue_block(chat_history)
    assert f"{global_config.bot.nickname}(你)说：我想吃面" in dialogue
    assert "历史模型输出" not in dialogue
    assert "我想吃面" in generator._render_retro_history_line(bot_reply)
    assert generator._render_retro_history_line(assistant_output) == ""


@pytest.mark.parametrize(
    ("is_group_session", "think_level", "reply_tool_args", "is_self_target", "expected_prompt_name"),
    [
        (True, 1, {}, False, RETRO_GROUP_PROMPT),
        (True, 0, {}, False, RETRO_GROUP_LIGHT_PROMPT),
        (True, 1, {"reply_style": SHORT_REPLY_STYLE}, False, RETRO_GROUP_LIGHT_PROMPT),
        (False, 1, {}, False, RETRO_PRIVATE_PROMPT),
        (False, 1, {}, True, RETRO_PRIVATE_SELF_PROMPT),
    ],
)
def test_select_retro_prompt_name(
    monkeypatch: pytest.MonkeyPatch,
    is_group_session: bool,
    think_level: int,
    reply_tool_args: Dict[str, Any],
    is_self_target: bool,
    expected_prompt_name: str,
) -> None:
    generator = build_retro_generator(is_group_session=is_group_session)
    monkeypatch.setattr(retro_prompt, "is_bot_self", lambda platform, user_id: is_self_target)
    reply_message = build_fake_target_message() if is_self_target else None

    prompt_name = generator._select_retro_prompt_name(
        reply_message=reply_message,
        stream_id="session-1",
        think_level=think_level,
        reply_tool_args=reply_tool_args,
    )

    assert prompt_name == expected_prompt_name


def test_build_request_messages_switches_to_retro_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)

    monkeypatch.setattr(global_config.experimental, "replyer_retro_prompt", True)
    retro_items: List[Any] = generator._build_request_messages(
        chat_history=[build_history_message("晚上吃什么")],
        reply_message=None,
        reply_reason="小明在问晚饭",
        think_level=0,
        reply_tool_args={},
    )

    assert [item.role for item in retro_items] == [RoleType.System, RoleType.User]
    # think_level=0 应命中群聊轻量模板，并带上当前思考
    retro_prompt_text = read_item_text(retro_items[1])
    assert "现在请你读读之前的聊天记录，然后给出日常且口语化的回复" in retro_prompt_text
    assert "小明在问晚饭" in retro_prompt_text

    monkeypatch.setattr(global_config.experimental, "replyer_retro_prompt", False)
    normal_items: List[Any] = generator._build_request_messages(
        chat_history=[build_history_message("晚上吃什么")],
        reply_message=None,
        reply_reason="小明在问晚饭",
        think_level=0,
        reply_tool_args={},
    )

    assert len(normal_items) > 1
    assert normal_items[0].role == RoleType.System
    assert normal_items[-1].role == RoleType.User


@pytest.mark.parametrize("locale", RETRO_LOCALES)
@pytest.mark.parametrize("retro_mode", [False, True])
def test_reply_reference_takes_priority_over_planner_content(
    locale: str, retro_mode: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)
    set_locale(locale)
    monkeypatch.setattr(global_config.experimental, "replyer_retro_prompt", retro_mode)

    items = generator._build_request_messages(
        chat_history=[build_history_message("晚上吃什么")],
        reply_message=None,
        reply_reason="PLANNER_VISIBLE_BODY",
        think_level=0,
        reply_tool_args={"reply_reference": "REPLY_REFERENCE_BODY"},
    )

    text = "\n".join(read_item_text(item) for item in items)
    assert "REPLY_REFERENCE_BODY" in text
    assert "PLANNER_VISIBLE_BODY" not in text
    assert (
        generator._build_reply_reference_message("PLANNER_VISIBLE_BODY", "REPLY_REFERENCE_BODY")
        == "REPLY_REFERENCE_BODY"
    )
    assert generator._build_reply_reference_message("PLANNER_VISIBLE_BODY", " \n ") == "PLANNER_VISIBLE_BODY"
    assert generator._build_reply_reference_message("", "") == ""


def test_retro_templates_are_localized_consistently() -> None:
    expected_names = [
        RETRO_GROUP_PROMPT,
        RETRO_GROUP_LIGHT_PROMPT,
        RETRO_PRIVATE_PROMPT,
        RETRO_PRIVATE_SELF_PROMPT,
    ]
    private_names = {RETRO_PRIVATE_PROMPT, RETRO_PRIVATE_SELF_PROMPT}

    for locale in RETRO_LOCALES:
        prompt_templates = list_prompt_templates(locale=locale)
        for prompt_name in expected_names:
            prompt_path = PROMPTS_ROOT / locale / f"{prompt_name}.prompt"
            assert prompt_path.is_file()

            template_info = prompt_templates[prompt_name]
            assert template_info.path == prompt_path
            assert template_info.metadata.display_name
            assert template_info.metadata.description

            placeholders = extract_prompt_placeholders(prompt_path.read_text(encoding="utf-8"))
            expected_placeholders = PRIVATE_PLACEHOLDERS if prompt_name in private_names else GROUP_PLACEHOLDERS
            assert placeholders == expected_placeholders


def test_retro_template_context_covers_every_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = build_retro_generator(is_group_session=True)
    isolate_retro_blocks(generator, monkeypatch)

    template_context = generator._build_retro_template_context(
        chat_history=[build_history_message("晚上吃什么")],
        reply_message=None,
        reply_reason="",
        reply_reference="",
        expression_habits="",
        reply_requirements="",
    )

    # 模板上下文的键必须覆盖所有模板用到的占位符，否则加载模板时会缺参
    assert set(template_context) == SUPPLIED_PLACEHOLDERS
