"""迁移默认配置自动覆盖，定制值仍须处理冲突。"""

import pytest
import tomlkit

from src.config.official_configs import LearningItem
from src.services import chat_migration_config as configuration


@pytest.mark.parametrize(
    "target_rule,automatic",
    [
        ("", True),
        ('{platform="qq",item_id="B",type="group",use=true,learn=true}', True),
        ('{platform="qq",item_id="B",type="group",use=true,learn=false}', False),
    ],
)
def test_default_target_is_overwritten_but_custom_target_requires_choice(tmp_path, monkeypatch, target_rule, automatic):
    monkeypatch.setattr(configuration, "rule_schema", lambda parts: LearningItem.model_json_schema())
    path = tmp_path / "config.toml"
    rules = ['{platform="qq",item_id="A",type="group",use=false,learn=true}']
    if target_rule:
        rules.append(target_rule)
    path.write_text("[expression]\nlearning_list=[" + ",".join(rules) + "]", encoding="utf-8")
    state = configuration.snapshot(path, {"platform": "qq", "group_id": "A"}, {"platform": "qq", "group_id": "B"})
    section = state["sections"][0]
    assert section["automatic"] is automatic
    if not automatic:
        with pytest.raises(ValueError, match="冲突"):
            configuration.render(state, {})
    rendered = configuration.render(state, {} if automatic else {section["id"]: "source"})
    result = tomlkit.parse(rendered).unwrap()["expression"]["learning_list"]
    assert result[0]["item_id"] == "A"
    assert result[1] == {"platform": "qq", "item_id": "B", "type": "group", "use": False, "learn": True}


def test_current_global_default_is_respected():
    rules = [
        {"platform": "", "item_id": "", "type": "group", "learn": False, "use": True},
        {"platform": "qq", "item_id": "B", "type": "group", "learn": False, "use": True},
    ]
    assert configuration.uses_defaults(
        rules[1:], rules, {"platform": "qq", "type": "group"}, LearningItem.model_json_schema()
    )
