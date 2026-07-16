from src.config.config_base import AttributeData
from src.config.config_upgrade_hooks import apply_config_upgrade_hooks
from src.config.official_configs import PersonalityConfig


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
