from src.config.legacy_migration import migrate_legacy_bind_env_to_bot_config_dict


def test_legacy_env_bindings_are_migrated_when_fields_missing_or_default(monkeypatch):
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "22345")
    monkeypatch.setenv("WEBUI_HOST", "192.168.1.8")
    monkeypatch.setenv("WEBUI_PORT", "19001")

    payload = {
        "maim_message": {
            "ws_server_host": "127.0.0.1",
            "ws_server_port": 8000,
        },
        "webui": {},
    }

    result = migrate_legacy_bind_env_to_bot_config_dict(payload)

    assert result.migrated is True
    assert payload["maim_message"]["ws_server_host"] == "0.0.0.0"
    assert payload["maim_message"]["ws_server_port"] == 22345
    assert payload["webui"]["host"] == "192.168.1.8"
    assert payload["webui"]["port"] == 19001


def test_legacy_env_bindings_do_not_override_explicit_config(monkeypatch):
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "22345")
    monkeypatch.setenv("WEBUI_HOST", "192.168.1.8")
    monkeypatch.setenv("WEBUI_PORT", "19001")

    payload = {
        "maim_message": {
            "ws_server_host": "10.1.1.1",
            "ws_server_port": 30000,
        },
        "webui": {
            "host": "10.1.1.2",
            "port": 30001,
        },
    }

    result = migrate_legacy_bind_env_to_bot_config_dict(payload)

    assert result.migrated is False
    assert payload["maim_message"]["ws_server_host"] == "10.1.1.1"
    assert payload["maim_message"]["ws_server_port"] == 30000
    assert payload["webui"]["host"] == "10.1.1.2"
    assert payload["webui"]["port"] == 30001
