import tomlkit

from src.webui.utils.toml_utils import save_toml_with_format


def test_save_toml_with_format_serializes_nested_inline_table(tmp_path) -> None:
    config_path = tmp_path / "bot_config.toml"
    config_path.write_text("[plugin]\ncommand_permissions = {}\n", encoding="utf-8")

    save_toml_with_format(
        {
            "plugin": {
                "command_permissions": {
                    "core.clear": {
                        "allow_users": ["qq:111111111", "qq:222222222"],
                        "allow_chats": [],
                    }
                }
            }
        },
        str(config_path),
    )

    saved_config = tomlkit.loads(config_path.read_text(encoding="utf-8"))
    assert saved_config["plugin"]["command_permissions"] == {
        "core.clear": {
            "allow_users": ["qq:111111111", "qq:222222222"],
            "allow_chats": [],
        }
    }
