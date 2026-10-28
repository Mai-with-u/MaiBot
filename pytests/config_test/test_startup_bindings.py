from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from src.config.legacy_migration import migrate_legacy_bind_env_to_bot_config_dict
from src.config.startup_bindings import (
    BindAddress,
    get_startup_main_bind_address,
    get_startup_http_bind_address,
    resolve_main_bind_address,
    resolve_http_bind_address,
)


def test_startup_bindings_use_defaults_when_config_file_missing(tmp_path: Path):
    missing_path = tmp_path / "missing_bot_config.toml"

    assert get_startup_main_bind_address(missing_path) == BindAddress(hosts=["127.0.0.1"], port=8080)
    assert get_startup_http_bind_address(missing_path) == BindAddress(hosts=["127.0.0.1", "::1"], port=8001)


def test_startup_bindings_can_read_addresses_from_bot_config(tmp_path: Path):
    config_path = tmp_path / "bot_config.toml"
    config_path.write_text(
        """
[inner]
version = "8.3.1"

[maim_message]
ws_server_host = "0.0.0.0"
ws_server_port = 22345

[http]
host = ["192.168.1.9"]
port = 18001
""".strip(),
        encoding="utf-8",
    )

    assert get_startup_main_bind_address(config_path) == BindAddress(hosts=["0.0.0.0"], port=22345)
    assert get_startup_http_bind_address(config_path) == BindAddress(hosts=["192.168.1.9"], port=18001)


def test_resolve_bindings_prefer_initialized_global_config(monkeypatch):
    fake_config_module = SimpleNamespace(
        global_config=SimpleNamespace(
            maim_message=SimpleNamespace(ws_server_host="10.0.0.2", ws_server_port=32000),
            http=SimpleNamespace(host=["10.0.0.3"], port=32001),
        )
    )

    monkeypatch.setitem(sys.modules, "src.config.config", fake_config_module)

    assert resolve_main_bind_address() == BindAddress(hosts=["10.0.0.2"], port=32000)
    assert resolve_http_bind_address() == BindAddress(hosts=["10.0.0.3"], port=32001)


@pytest.mark.parametrize(
    ("http_host", "expected_hosts"),
    [
        (None, ["0.0.0.0", "::1"]),
        ("192.168.1.8", ["127.0.0.1", "::1", "192.168.1.8"]),
        ("", ["127.0.0.1", "::1"]),
    ],
)
def test_http_host_env_precedence_for_bootstrap_and_loaded_config(tmp_path, monkeypatch, http_host, expected_hosts):
    monkeypatch.setenv("WEBUI_HOST", "0.0.0.0")
    if http_host is None:
        monkeypatch.delenv("HTTP_HOST", raising=False)
    else:
        monkeypatch.setenv("HTTP_HOST", http_host)

    expected = BindAddress(hosts=expected_hosts, port=8001)
    assert get_startup_http_bind_address(tmp_path / "missing.toml") == expected

    http_config = SimpleNamespace(host=["127.0.0.1", "::1"], port=8001)
    monkeypatch.setitem(
        sys.modules, "src.config.config", SimpleNamespace(global_config=SimpleNamespace(http=http_config))
    )
    assert resolve_http_bind_address() == expected
    assert http_config.host == ["127.0.0.1", "::1"]


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
