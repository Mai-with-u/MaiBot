from pathlib import Path
from typing import List

import importlib
import sys


BUILT_IN_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins" / "built_in"
if str(BUILT_IN_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(BUILT_IN_PLUGIN_ROOT))

NapCatPluginSettings = importlib.import_module("napcat_adapter.config").NapCatPluginSettings


class DummyLogger:
    """用于测试的轻量日志对象。"""

    def __init__(self) -> None:
        """初始化测试日志对象。"""
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def warning(self, message: str) -> None:
        """记录警告日志。

        Args:
            message: 待记录的日志内容。
        """
        self.warnings.append(message)

    def error(self, message: str) -> None:
        """记录错误日志。

        Args:
            message: 待记录的日志内容。
        """
        self.errors.append(message)


def test_parse_new_napcat_server_config() -> None:
    logger = DummyLogger()
    settings = NapCatPluginSettings.from_mapping(
        {
            "plugin": {"enabled": True, "config_version": "0.1.0"},
            "napcat_server": {
                "host": "localhost",
                "port": 8095,
                "token": "secret",
                "heartbeat_interval": 45,
                "reconnect_delay_sec": 7,
                "action_timeout_sec": 18,
                "connection_id": "main",
            },
        },
        logger,
    )

    assert settings.should_connect() is True
    assert settings.napcat_server.host == "localhost"
    assert settings.napcat_server.port == 8095
    assert settings.napcat_server.token == "secret"
    assert settings.napcat_server.heartbeat_interval == 45.0
    assert settings.napcat_server.reconnect_delay_sec == 7.0
    assert settings.napcat_server.action_timeout_sec == 18.0
    assert settings.napcat_server.connection_id == "main"
    assert settings.napcat_server.build_ws_url() == "ws://localhost:8095"
    assert settings.validate(logger) is True


def test_parse_legacy_connection_ws_url_fallback() -> None:
    logger = DummyLogger()
    settings = NapCatPluginSettings.from_mapping(
        {
            "plugin": {"enabled": True, "config_version": "0.1.0"},
            "connection": {
                "ws_url": "ws://127.0.0.1:3001",
                "access_token": "legacy-token",
                "heartbeat_sec": 35,
                "action_timeout_sec": 12,
            },
        },
        logger,
    )

    assert settings.napcat_server.host == "127.0.0.1"
    assert settings.napcat_server.port == 3001
    assert settings.napcat_server.token == "legacy-token"
    assert settings.napcat_server.heartbeat_interval == 35.0
    assert settings.napcat_server.action_timeout_sec == 12.0
    assert settings.validate(logger) is True
    assert logger.warnings
