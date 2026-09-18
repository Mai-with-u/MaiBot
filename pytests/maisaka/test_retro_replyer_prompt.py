from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List

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
from src.common.prompt_i18n import (
    PROMPTS_ROOT,
    clear_prompt_cache,
    extract_prompt_placeholders,
    list_prompt_templates,
    load_prompt,
)
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextTextPart, RoleType
from src.maisaka.context.planner_messages import build_session_backed_text_message

RETRO_LOCALES = ("zh-CN", "en-US", "ja-JP")

GROUP_PLACEHOLDERS = {
    "bot_name",
    "dialogue_prompt",
    "expression_habits_block",
    "extra_info_block",
    "group_chat_attention_block",
    "identity",
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
def reset_prompt_cache() -> Any:
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
    generator._enable_visual_message = False
    generator._replyer_mode = "text"
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


def read_item_text(item: Any) -> str:
    return "".join(part.text for part in item.parts if isinstance(part, ContextTextPart))


def isolate_retro_blocks(generator: BaseMaisakaReplyGenerator, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离注意事项块和关键词反应块，避免测试依赖聊天流与关键词配置。"""

    monkeypatch.setattr(generator, "_build_group_chat_attention_block", lambda session_id: "")
    monkeypatch.setattr(generator, "_build_keyword_reaction_prompt", lambda **kwargs: "")


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

    assert len(items) == 1
    assert items[0].role == RoleType.User

    prompt = read_item_text(items[0])
    # 所有占位符都必须被填满，模板里不允许残留花括号
    assert "{" not in prompt
    assert "现在请你读读之前的聊天记录，把握当前的话题" in prompt
    assert "【表达习惯参考】当被问吃什么时可以用随便来表达。" in prompt
    assert "以下是你在回复时需要参考的信息" in prompt
    assert "这次请直接回答吃什么。" in prompt
    assert "你的想法是：小明在问晚饭" in prompt
    assert "[12:30:00] 小明说：晚上吃什么" in prompt


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

    assert len(retro_items) == 1
    assert retro_items[0].role == RoleType.User
    # think_level=0 应命中群聊轻量模板，并带上当前思考
    retro_prompt_text = read_item_text(retro_items[0])
    assert "现在请你读读之前的聊天记录，然后给出日常且口语化的回复" in retro_prompt_text
    assert "你的想法是：小明在问晚饭" in retro_prompt_text

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
        expression_habits="",
        reply_requirements="",
        stream_id="session-1",
    )

    # 模板上下文的键必须覆盖所有模板用到的占位符，否则加载模板时会缺参
    assert set(template_context) == SUPPLIED_PLACEHOLDERS
