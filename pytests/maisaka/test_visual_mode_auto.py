from types import SimpleNamespace
from typing import Dict, List

import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.config.model_configs import ModelInfo
from src.maisaka.visual import mode_utils


@pytest.mark.parametrize(
    ("model_names", "visual_flags", "expected"),
    [
        (["vision"], {"vision": True}, True),
        (["text"], {"text": False}, False),
        (["vision", "text"], {"vision": True, "text": False}, False),
        (["missing"], {}, False),
        ([], {}, False),
    ],
)
def test_planner_automatically_resolves_task_visual_capability(
    monkeypatch: pytest.MonkeyPatch,
    model_names: List[str],
    visual_flags: Dict[str, bool],
    expected: bool,
) -> None:
    model_config = SimpleNamespace(
        model_task_config=SimpleNamespace(planner=SimpleNamespace(model_list=model_names)),
        models=[SimpleNamespace(name=name, visual=visual) for name, visual in visual_flags.items()],
    )
    monkeypatch.setattr(mode_utils.config_manager, "get_model_config", lambda: model_config)

    assert mode_utils.resolve_enable_visual_planner() is expected


def test_replyer_uses_current_model_visual_capability() -> None:
    generator = object.__new__(BaseMaisakaReplyGenerator)
    vision_model = ModelInfo(name="vision", model_identifier="vision", api_provider="test", visual=True)
    text_model = ModelInfo(name="text", model_identifier="text", api_provider="test", visual=False)

    # 同一生成器切换模型时，图片输入应跟随本次模型能力变化。
    assert generator._resolve_enable_visual_message(vision_model) is True
    assert generator._resolve_enable_visual_message(text_model) is False
    assert generator._resolve_enable_visual_message(vision_model) is True
    assert generator._resolve_enable_visual_message(None) is False
