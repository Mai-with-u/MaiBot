from pathlib import Path

import pytest

from src.common.i18n import use_locale
from src.common.prompt_i18n import extract_prompt_placeholders, load_prompt, load_prompt_template
from src.config.config import global_config
from src.maisaka import chat_loop_service as service_module
from src.maisaka.chat_loop_service import MaisakaChatLoopService


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
@pytest.mark.parametrize("is_group", [True, False])
@pytest.mark.parametrize("passive", [True, False])
@pytest.mark.parametrize("focus", [True, False])
def test_planner_mode_selects_matching_template_and_turn_instruction(monkeypatch, locale, is_group, passive, focus):
    monkeypatch.setattr(service_module.focus_mode_manager, "is_enabled_for_session", lambda *args, **kwargs: focus)
    service = MaisakaChatLoopService(session_id="test", is_group_chat=is_group)
    service.passive_replyer_override = passive
    expected = "planner_focus" if focus else f"planner_{'passive' if passive else 'default'}_{'group' if is_group else 'private'}"
    instruction = "planner_turn_focus" if focus else "planner_turn_passive" if passive else "planner_turn_action"
    with use_locale(locale):
        assert service._get_chat_prompt_name() == expected
        template = load_prompt_template(expected, locale=locale, include_custom=False)
        assert template.locale == locale
        assert service._build_planner_final_user_reminder() == load_prompt(
            instruction, bot_name=global_config.bot.nickname.strip(),
        ).strip()


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
@pytest.mark.parametrize("old_name,new_name", [
    ("maisaka_chat", "planner_default_group"),
    ("maisaka_chat_passive", "planner_passive_group"),
    ("maisaka_chat_private", "planner_default_private"),
    ("maisaka_chat_private_passive", "planner_passive_private"),
    ("maisaka_chat_focus", "planner_focus"),
])
def test_renamed_planners_keep_existing_custom_templates(tmp_path: Path, locale: str, old_name: str, new_name: str):
    template = load_prompt_template(new_name, locale=locale, include_custom=False).template
    directory = tmp_path / locale
    directory.mkdir()
    (directory / f"{old_name}.prompt").write_text(template + "\nexisting-custom-content", encoding="utf-8")
    values = {key: f"material-{key}" for key in extract_prompt_placeholders(template)}
    result = load_prompt(new_name, locale=locale, custom_prompts_root=tmp_path, **values)
    assert "existing-custom-content" in result
    assert result == load_prompt(old_name, locale=locale, custom_prompts_root=tmp_path, **values)
