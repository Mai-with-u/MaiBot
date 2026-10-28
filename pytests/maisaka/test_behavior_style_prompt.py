import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.i18n import use_locale
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.maisaka.chat_loop_service import MaisakaChatLoopService


def test_planner_context_includes_personality_and_behavior_style(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.personality, "personality", "测试核心人格。")
    monkeypatch.setattr(global_config.personality, "behavior_style", "这段行为风格只应提供给 Planner。")

    service = MaisakaChatLoopService()
    prompt_context = service.build_prompt_template_context()

    assert prompt_context["behavior_style"] == "这段行为风格只应提供给 Planner。"
    assert "identity" not in prompt_context
    assert prompt_context["personality"] == "测试核心人格。"


def test_replyer_keeps_using_personality(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.personality, "personality", "测试核心人格。")
    monkeypatch.setattr(global_config.personality, "behavior_style", "这段行为风格只应提供给 Planner。")

    generator = object.__new__(BaseMaisakaReplyGenerator)
    prompt = generator._build_personality_prompt()

    assert "测试核心人格。" in prompt
    assert "这段行为风格只应提供给 Planner。" not in prompt


@pytest.mark.parametrize("is_group_chat", [True, False])
@pytest.mark.parametrize("prompt_name", ["maisaka_chat", "maisaka_chat_focus", "maisaka_replyer"])
@pytest.mark.parametrize(
    ("locale", "heading"),
    [("zh-CN", "注意力漂移风格："), ("en-US", "Attention drift style:"), ("ja-JP", "注意ドリフトスタイル：")],
)
def test_attention_drift_follows_live_config_in_planner_and_replyer(
    monkeypatch: pytest.MonkeyPatch, prompt_name: str, locale: str, heading: str, is_group_chat: bool
) -> None:
    if prompt_name == "maisaka_replyer":
        generator = object.__new__(BaseMaisakaReplyGenerator)
        generator._load_prompt = load_prompt
        monkeypatch.setattr(
            "src.chat.replyer.maisaka_generator_base.get_chat_type_and_target_info",
            lambda session_id: (is_group_chat, None),
        )
        monkeypatch.setattr(generator, "_build_group_chat_attention_block", lambda session_id: "")
        monkeypatch.setattr(generator, "_build_personality_prompt", lambda: "测试人设")
        monkeypatch.setattr(generator, "_select_reply_style", lambda: "测试回复风格")

        def build_prompt() -> str:
            return generator._build_system_prompt(None, "测试回复原因", stream_id="test-session")
    else:
        service = MaisakaChatLoopService(is_group_chat=is_group_chat)
        monkeypatch.setattr(service, "_get_chat_prompt_name", lambda: prompt_name)
        build_prompt = service._build_chat_system_prompt

    config = (
        global_config.experimental.group_attention_drift
        if is_group_chat
        else global_config.experimental.private_attention_drift
    )
    other_config = (
        global_config.experimental.private_attention_drift
        if is_group_chat
        else global_config.experimental.group_attention_drift
    )
    monkeypatch.setattr(other_config, "enabled", True)
    with use_locale(locale):
        monkeypatch.setattr(config, "enabled", False)
        baseline = build_prompt()
        if prompt_name == "maisaka_replyer":
            assert "测试人设" in baseline
        assert heading not in baseline
        monkeypatch.setattr(config, "enabled", True)
        assert build_prompt().count(heading) == 1
        monkeypatch.setattr(config, "enabled", False)
        assert build_prompt() == baseline


@pytest.mark.parametrize("prompt_name", ["maisaka_chat", "maisaka_chat_focus"])
@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_planner_personality_precedes_behavior_style(
    monkeypatch: pytest.MonkeyPatch, prompt_name: str, locale: str
) -> None:
    monkeypatch.setattr(global_config.personality, "personality", "核心人格测试标记")
    monkeypatch.setattr(global_config.personality, "behavior_style", "行为风格测试标记")
    service = MaisakaChatLoopService()
    monkeypatch.setattr(service, "_get_chat_prompt_name", lambda: prompt_name)

    with use_locale(locale):
        prompt = service._build_chat_system_prompt()

    assert prompt.index("核心人格测试标记") < prompt.index("行为风格测试标记")
