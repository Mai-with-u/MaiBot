from src.config.legacy_migration import try_migrate_legacy_bot_config_dict


def test_legacy_learning_list_with_numeric_fourth_column_is_migrated():
    payload = {
        "expression": {
            "learning_list": [
                ["qq:123456:group", "enable", "disable", "0.5"],
                ["", "disable", "enable", "0.1"],
            ]
        }
    }

    result = try_migrate_legacy_bot_config_dict(payload)

    assert result.migrated is True
    assert "expression.learning_list" in result.reason
    assert result.data["expression"]["learning_list"] == [
        {
            "platform": "qq",
            "item_id": "123456",
            "rule_type": "group",
            "use_expression": True,
            "enable_learning": False,
            "enable_jargon_learning": False,
        },
        {
            "platform": "",
            "item_id": "",
            "rule_type": "group",
            "use_expression": False,
            "enable_learning": True,
            "enable_jargon_learning": False,
        },
    ]
