from types import ModuleType, SimpleNamespace

import ipaddress
import sys

import pytest

from src.webui.utils import network_security


def _install_webui_url_check_config(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    config_module = ModuleType("src.config.config")
    config_module.global_config = SimpleNamespace(
        webui=SimpleNamespace(enforce_public_outbound_url=enabled),
    )
    monkeypatch.setitem(sys.modules, "src.config.config", config_module)


def test_validate_public_url_allows_localhost_when_public_check_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_webui_url_check_config(monkeypatch, enabled=False)

    def fail_resolve(hostname: str, port: int):
        raise AssertionError(f"关闭公网校验时不应解析地址: {hostname}:{port}")

    monkeypatch.setattr(network_security, "_resolve_ip_addresses", fail_resolve)

    assert network_security.validate_public_url("http://localhost:1234/v1") == "http://localhost:1234/v1"


def test_validate_public_url_blocks_private_address_when_public_check_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_webui_url_check_config(monkeypatch, enabled=True)
    monkeypatch.setattr(
        network_security,
        "_resolve_ip_addresses",
        lambda hostname, port: {ipaddress.ip_address("127.0.0.1")},
    )

    with pytest.raises(ValueError, match="禁止访问非公网地址"):
        network_security.validate_public_url("https://example.com:8443/v1")
