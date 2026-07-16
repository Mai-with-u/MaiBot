import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.config.config import global_config
from src.maisaka.chat_loop_service import MaisakaChatLoopService


def test_planner_context_uses_behavior_style_instead_of_personality(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.personality, "personality", "这段人格只应提供给 Replyer。")
    monkeypatch.setattr(global_config.personality, "behavior_style", "这段行为风格只应提供给 Planner。")

    service = MaisakaChatLoopService()
    prompt_context = service.build_prompt_template_context()

    assert prompt_context["behavior_style"] == "这段行为风格只应提供给 Planner。"
    assert "identity" not in prompt_context
    assert "这段人格只应提供给 Replyer。" not in prompt_context.values()


def test_replyer_keeps_using_personality(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.personality, "personality", "这段人格只应提供给 Replyer。")
    monkeypatch.setattr(global_config.personality, "behavior_style", "这段行为风格只应提供给 Planner。")

    generator = object.__new__(BaseMaisakaReplyGenerator)
    prompt = generator._build_personality_prompt()

    assert "这段人格只应提供给 Replyer。" in prompt
    assert "这段行为风格只应提供给 Planner。" not in prompt
