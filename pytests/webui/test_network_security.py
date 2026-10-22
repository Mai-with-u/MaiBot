from types import ModuleType, SimpleNamespace

import ipaddress
import sys

import pytest

from src.common import network_security


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


def test_validate_public_url_blocks_rfc1918_address_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_webui_url_check_config(monkeypatch, enabled=True)
    monkeypatch.setattr(
        network_security,
        "_resolve_ip_addresses",
        lambda hostname, port: {ipaddress.ip_address("192.168.1.10")},
    )

    with pytest.raises(ValueError, match="禁止访问非公网地址"):
        network_security.validate_public_url("http://model.internal:11434/v1")


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.1.10", "10.0.0.8"])
def test_validate_public_url_allows_configured_private_provider(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
) -> None:
    _install_webui_url_check_config(monkeypatch, enabled=True)
    monkeypatch.setattr(
        network_security,
        "_resolve_ip_addresses",
        lambda hostname, port: {ipaddress.ip_address(address)},
    )

    assert network_security.validate_public_url(
        "http://local-model:11434/v1",
        allow_configured_private_network=True,
    ) == "http://local-model:11434/v1"


def test_validate_public_url_still_blocks_link_local_for_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_webui_url_check_config(monkeypatch, enabled=True)
    monkeypatch.setattr(
        network_security,
        "_resolve_ip_addresses",
        lambda hostname, port: {ipaddress.ip_address("169.254.169.254")},
    )

    with pytest.raises(ValueError, match="禁止访问非公网地址"):
        network_security.validate_public_url(
            "http://metadata.internal/latest",
            allow_configured_private_network=True,
        )


@pytest.mark.parametrize("allow_fake_ip", [False, True])
def test_non_public_error_preserves_dns_results(monkeypatch: pytest.MonkeyPatch, allow_fake_ip: bool) -> None:
    addresses = {"169.254.1.44", "114.117.137.126"}
    monkeypatch.setattr(
        network_security, "_resolve_ip_addresses",
        lambda hostname, port: {ipaddress.ip_address(address) for address in addresses},
    )

    with pytest.raises(network_security.NonPublicAddressError) as error:
        network_security.validate_public_url(
            "https://cnb.cool/Mai-with-u/plugin.git", require_public_network=True, allow_fake_ip=allow_fake_ip,
        )

    assert error.value.hostname == "cnb.cool"
    assert error.value.address == "169.254.1.44"
    assert error.value.resolved_addresses == addresses


def test_download_source_error_explains_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.webui.services import git_mirror_service

    def reject_url(url: str, **kwargs: object) -> str:
        raise network_security.NonPublicAddressError("cnb.cool", "169.254.1.44", {"169.254.1.44"})

    monkeypatch.setattr(git_mirror_service, "validate_public_url", reject_url)

    with pytest.raises(ValueError) as error:
        git_mirror_service._validate_custom_outbound_url("https://cnb.cool/Mai-with-u/plugin.git", "CNB 镜像")

    message = str(error.value)
    for detail in ("CNB 镜像", "cnb.cool", "169.254.1.44", "已阻止下载", "DNS", "下载源设置"):
        assert detail in message
    assert isinstance(error.value.__cause__, network_security.NonPublicAddressError)
