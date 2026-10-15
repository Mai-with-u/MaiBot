from src.config.config_base import AttributeData
from src.config.config_upgrade_hooks import apply_config_upgrade_hooks
from src.config.official_configs import PersonalityConfig


def test_removed_expression_selection_mode_upgrade_hook():
    config_data = {"expression": {"expression_selection_mode": "vector"}}

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.39",
        new_ver="8.14.40",
    )

    assert result.migrated is True
    assert result.data["expression"]["expression_selection_mode"] == "vector_intent"
    assert result.reason == "8.14.40:expression.expression_selection_mode"


def test_removed_expression_selection_mode_hook_preserves_supported_modes():
    config_data = {"expression": {"expression_selection_mode": "legacy"}}

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.39",
        new_ver="8.14.40",
    )

    assert result.migrated is False
    assert result.data["expression"]["expression_selection_mode"] == "legacy"


def test_expression_defaults_reset_once_and_remove_old_mode():
    config_data = {
        "expression": {
            "expression_checked_only": True,
            "expression_self_reflect": True,
            "expression_selection_mode": "legacy",
        },
    }
    result = apply_config_upgrade_hooks(config_data, "bot_config.toml", "8.14.57", "8.14.58")

    assert result.migrated is True
    assert result.data["expression"] == {
        "expression_checked_only": False,
        "expression_self_reflect": True,
        "use_vector_expression": True,
    }

    # 跨版本只重置一次，用户随后设置的开关不能在同版本加载时被覆盖。
    result.data["expression"]["expression_checked_only"] = True
    result.data["expression"]["use_vector_expression"] = False
    reloaded = apply_config_upgrade_hooks(result.data, "bot_config.toml", "8.14.58", "8.14.58")
    assert reloaded.migrated is False
    assert reloaded.data["expression"]["expression_checked_only"] is True
    assert reloaded.data["expression"]["use_vector_expression"] is False


def test_split_chat_config_sections_upgrade_hook():
    config_data = {
        "chat": {
            "talk_value": 0.4,
            "private_talk_value": 0.8,
            "reply_trigger_mode": "frequency",
            "enable_talk_value_rules": True,
            "talk_value_rules": [
                {
                    "platform": "",
                    "item_id": "",
                    "rule_type": "group",
                    "time": "*",
                    "value": 0.5,
                }
            ],
            "enable_reply_quote": False,
            "group_chat_prompt": "group prompt",
            "private_chat_prompts": "private prompt",
            "chat_prompts": [],
            "max_context_size": 40,
        }
    }

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.18",
        new_ver="8.14.19",
    )

    chat_config = result.data["chat"]
    assert result.migrated is True
    assert chat_config["max_context_size"] == 40
    assert chat_config["reply_timing"]["talk_value"] == 0.4
    assert chat_config["reply_timing"]["private_talk_value"] == 0.8
    assert chat_config["reply_timing"]["reply_trigger_mode"] == "frequency"
    assert chat_config["reply_timing"]["enable_talk_value_rules"] is True
    assert chat_config["reply_timing"]["talk_value_rules"][0]["value"] == 0.5
    assert chat_config["reply_style"]["enable_reply_quote"] is False
    assert chat_config["reply_style"]["group_chat_prompt"] == "group prompt"
    assert chat_config["reply_style"]["private_chat_prompts"] == "private prompt"
    assert chat_config["reply_style"]["chat_prompts"] == []
    assert "talk_value" not in chat_config
    assert "group_chat_prompt" not in chat_config


def test_behavior_style_upgrade_copies_existing_personality_exactly():
    config_data = {"personality": {"personality": "  原有的人格配置\n保持原样  "}}

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.28",
        new_ver="8.14.29",
    )

    assert result.migrated is True
    assert result.data["personality"]["behavior_style"] == "  原有的人格配置\n保持原样  "
    assert result.reason == "8.14.29:personality.behavior_style"


def test_behavior_style_upgrade_runs_before_config_defaults_are_created():
    original_personality = "用户升级前的人格，也是 Planner 原本使用的值"
    config_data = {"personality": {"personality": original_personality}}
    assert "behavior_style" not in config_data["personality"]

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.28",
        new_ver="8.14.29",
    )
    parsed_personality = PersonalityConfig.from_dict(AttributeData(), result.data["personality"])

    assert PersonalityConfig().behavior_style != original_personality
    assert result.data["personality"]["behavior_style"] == original_personality
    assert parsed_personality.behavior_style == original_personality


def test_behavior_style_upgrade_preserves_explicit_value():
    config_data = {
        "personality": {
            "personality": "原有人格",
            "behavior_style": "用户已填写的行为风格",
        }
    }

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.28",
        new_ver="8.14.29",
    )

    assert result.migrated is False
    assert result.data["personality"]["behavior_style"] == "用户已填写的行为风格"


def test_behavior_style_upgrade_only_applies_to_bot_config():
    config_data = {"personality": {"personality": "原有人格"}}

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="model_config.toml",
        old_ver="8.14.28",
        new_ver="8.14.29",
    )

    assert result.migrated is False
    assert "behavior_style" not in result.data["personality"]


def test_behavior_style_upgrade_only_applies_when_crossing_target_version():
    config_data = {"personality": {"personality": "原有人格"}}

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.29",
        new_ver="8.14.30",
    )

    assert result.migrated is False
    assert "behavior_style" not in result.data["personality"]


def test_behavior_style_upgrade_does_not_create_missing_personality_section():
    result = apply_config_upgrade_hooks(
        {},
        config_name="bot_config.toml",
        old_ver="8.14.28",
        new_ver="8.14.29",
    )

    assert result.migrated is False
    assert "personality" not in result.data


def test_removed_reply_necessity_trigger_mode_upgrade_hook():
    config_data = {"chat": {"reply_timing": {"reply_trigger_mode": "reply_necessity"}}}

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.53",
        new_ver="8.14.54",
    )

    assert result.migrated is True
    assert result.data["chat"]["reply_timing"]["reply_trigger_mode"] == "dynamic"
    assert result.reason == "8.14.54:chat.reply_timing.reply_trigger_mode"


def test_removed_reply_necessity_trigger_mode_hook_preserves_frequency_mode():
    config_data = {"chat": {"reply_timing": {"reply_trigger_mode": "frequency"}}}

    result = apply_config_upgrade_hooks(
        config_data,
        config_name="bot_config.toml",
        old_ver="8.14.53",
        new_ver="8.14.54",
    )

    assert result.migrated is False
    assert result.data["chat"]["reply_timing"]["reply_trigger_mode"] == "frequency"


def _learning_rule(platform: str, item_id: str, rule_type: str, use: bool) -> dict:
    return {"platform": platform, "item_id": item_id, "type": rule_type, "use": use, "learn": True}


def _upgrade_learning_list(learning_list: list[dict]) -> list[dict]:
    result = apply_config_upgrade_hooks(
        {"expression": {"learning_list": learning_list}},
        config_name="bot_config.toml",
        old_ver="8.14.59",
        new_ver="8.14.60",
    )
    return result.data["expression"]["learning_list"]


def test_wildcard_learning_rule_replaces_same_scope_default():
    # 旧逻辑里通配压过平台兜底，不论书写顺序，合并后保留通配那条的开关。
    assert _upgrade_learning_list(
        [
            _learning_rule("qq", "", "group", False),
            _learning_rule("qq", "*", "group", True),
            _learning_rule("qq", "123", "group", False),
            _learning_rule("", "", "group", False),
        ]
    ) == [
        _learning_rule("qq", "", "group", True),
        _learning_rule("qq", "123", "group", False),
        _learning_rule("", "", "group", False),
    ]


def test_global_wildcard_learning_rule_keeps_other_chat_type_behavior():
    # *:* 只对自己的聊天类型生效，另一类型原本落到留空默认，拆成两条默认规则保持不变。
    assert _upgrade_learning_list(
        [
            _learning_rule("", "", "group", False),
            _learning_rule("*", "*", "group", True),
        ]
    ) == [
        _learning_rule("", "", "group", True),
        _learning_rule("", "", "private", False),
    ]

    # 没有留空默认时，另一类型原本未命中任何规则，即使用与学习都开启。
    assert _upgrade_learning_list([_learning_rule("*", "*", "private", False)]) == [
        _learning_rule("", "", "group", True),
        _learning_rule("", "", "private", False),
    ]


def test_learning_rule_merge_hook_drops_dead_defaults_and_skips_clean_config():
    # 留空默认只有第一条生效；半边留空的通配从不命中。
    assert _upgrade_learning_list(
        [
            _learning_rule("", "", "group", False),
            _learning_rule("", "", "private", True),
            _learning_rule("*", "", "group", True),
        ]
    ) == [_learning_rule("", "", "group", False)]

    clean_config = {
        "expression": {"learning_list": [_learning_rule("", "", "group", True), _learning_rule("qq", "", "group", True)]},
        "jargon": {"learning_list": [_learning_rule("qq", "*", "group", True)]},
    }
    result = apply_config_upgrade_hooks(clean_config, "bot_config.toml", "8.14.59", "8.14.60")
    assert result.reason == "8.14.60:jargon.learning_list"
    assert result.data["jargon"]["learning_list"] == [_learning_rule("qq", "", "group", True)]
