from pathlib import Path
from typing import Any

import tomlkit

from src.config import config as config_module
from src.config.config import Config, ConfigManager, ModelConfig, load_config_from_file
from src.config.official_configs import ExpressionConfig


def test_expression_schema_exposes_vector_switch_and_defaults() -> None:
    """配置界面使用向量开关，精选默认关闭，优化学习默认开启。"""

    properties = ExpressionConfig.model_json_schema()["properties"]
    field_schema = properties["use_vector_expression"]

    assert "expression_selection_mode" not in properties
    assert field_schema["type"] == "boolean"
    assert field_schema["default"] is True
    assert properties["expression_checked_only"]["default"] is False
    assert properties["expression_self_reflect"]["default"] is True


def test_load_repairs_removed_vector_mode_after_legacy_migration_completed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """升级时移除旧模式、重置精选限制并落盘，同时保留备份。"""

    config_path = tmp_path / "bot_config.toml"
    config_module.generate_new_config_file(Config, config_path, "8.14.39")
    with config_path.open("r", encoding="utf-8") as config_file:
        config_document = tomlkit.load(config_file)
    config_document["expression"]["expression_selection_mode"] = "vector"
    config_document["expression"]["expression_checked_only"] = True
    del config_document["expression"]["use_vector_expression"]
    config_document["a_memorix"]["web"]["import"]["max_files_per_task"] = 321
    with config_path.open("w", encoding="utf-8") as config_file:
        tomlkit.dump(config_document, config_file)
    monkeypatch.setattr(config_module, "should_apply_legacy_migration", lambda _file_name: False)
    reported_changes: list[tuple[list[str], list[str]]] = []

    def capture_config_changes(attribute_data, *_args, **_kwargs) -> None:
        reported_changes.append(
            (
                list(attribute_data.missing_attributes),
                list(attribute_data.redundant_attributes),
            )
        )

    monkeypatch.setattr(config_module, "output_config_changes", capture_config_changes)

    loaded_config, updated = load_config_from_file(Config, config_path, "8.14.58")

    assert updated is True
    assert loaded_config.expression.use_vector_expression is True
    assert loaded_config.expression.expression_checked_only is False
    assert loaded_config.a_memorix.web.import_config.max_files_per_task == 321
    assert reported_changes == [([], [])]
    with config_path.open("r", encoding="utf-8") as config_file:
        repaired_document = tomlkit.load(config_file)
    assert repaired_document["inner"]["version"] == "8.14.58"
    assert "expression_selection_mode" not in repaired_document["expression"]
    assert repaired_document["expression"]["use_vector_expression"] is True
    assert repaired_document["expression"]["expression_checked_only"] is False
    assert repaired_document["a_memorix"]["web"]["import"]["max_files_per_task"] == 321
    assert "import_config" not in repaired_document["a_memorix"]["web"]
    backup_paths = list((tmp_path / "old").glob("bot_config_*.toml"))
    assert len(backup_paths) == 1
    with backup_paths[0].open("r", encoding="utf-8") as backup_file:
        backup_document = tomlkit.load(backup_file)
    assert backup_document["expression"]["expression_selection_mode"] == "vector"


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
