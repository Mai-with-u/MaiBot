from typing import Any

from src.config import config as config_module
from src.config.config import Config, ConfigManager, ModelConfig
from src.config.official_configs import ExpressionConfig


def test_expression_selection_schema_exposes_only_supported_modes() -> None:
    """配置界面不应继续展示已移除的 vector 模式。"""

    field_schema = ExpressionConfig.model_json_schema()["properties"]["expression_selection_mode"]

    assert field_schema["enum"] == ["legacy", "vector_intent"]
    assert field_schema["options"] == ["legacy", "vector_intent"]


def test_normalize_loaded_config_maps_removed_vector_mode() -> None:
    """旧版 vector 配置应在校验前迁移为 vector_intent。"""

    source_config = {"expression": {"expression_selection_mode": "vector"}}

    normalized = config_module._normalize_loaded_bot_config_dict(source_config)

    assert normalized["expression"]["expression_selection_mode"] == "vector_intent"
    assert source_config["expression"]["expression_selection_mode"] == "vector"


def test_initialize_upgrades_bot_and_model_config_without_exit(monkeypatch):
    manager = ConfigManager()
    loaded_config_classes: list[type[Any]] = []
    warnings: list[Any] = []

    def fake_load_config_from_file(config_class, config_path, new_ver, override_repr=False):
        loaded_config_classes.append(config_class)
        return object(), True

    monkeypatch.setattr(config_module, "load_config_from_file", fake_load_config_from_file)
    monkeypatch.setattr(ConfigManager, "_warn_if_vlm_not_configured", lambda self, model_config: warnings.append(model_config))

    manager.initialize()

    assert loaded_config_classes == [Config, ModelConfig]
    assert warnings == [manager.model_config]
