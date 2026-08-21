from typing import Iterable, Set
from urllib.parse import urlparse

import ipaddress
import socket


def _resolve_ip_addresses(hostname: str, port: int) -> Set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        address_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析主机名: {hostname}") from exc

    resolved_addresses: Set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for _, _, _, _, sockaddr in address_infos:
        host_address = sockaddr[0]
        if not isinstance(host_address, str):
            continue

        raw_ip = host_address.split("%", 1)[0]
        resolved_addresses.add(ipaddress.ip_address(raw_ip))

    if not resolved_addresses:
        raise ValueError(f"无法解析主机名: {hostname}")

    return resolved_addresses


def _is_unsafe_ip_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断地址是否在任何 WebUI 出站场景中都不应访问。"""
    return any(
        (
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _is_non_public_ip_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断地址是否不属于普通公网目标。"""
    return address.is_private or address.is_loopback or _is_unsafe_ip_address(address)


def _should_enforce_public_network(require_public_network: bool | None) -> bool:
    if require_public_network is not None:
        return require_public_network

    try:
        from src.config.config import global_config

        return global_config.webui.enforce_public_outbound_url
    except (AttributeError, ImportError, RuntimeError):
        return True


def validate_public_url(
    url: str,
    allowed_schemes: Iterable[str] = ("http", "https"),
    require_public_network: bool | None = None,
    allow_configured_private_network: bool = False,
) -> str:
    """校验 WebUI 出站 URL。

    已保存的模型厂商配置可显式允许回环或私网目标，但链路本地、组播、
    保留地址和未指定地址始终禁止。任意 URL 入口仍只允许公网目标。
    """
    normalized_url = url.strip()
    if not normalized_url:
        raise ValueError("URL 不能为空")

    if "://" not in normalized_url:
        normalized_url = "http://" + normalized_url
    parsed = urlparse(normalized_url)
    allowed_scheme_set = {scheme.lower() for scheme in allowed_schemes}
    if parsed.scheme.lower() not in allowed_scheme_set:
        allowed = ", ".join(sorted(allowed_scheme_set))
        raise ValueError(f"仅允许以下协议: {allowed}")

    if not parsed.hostname or not parsed.netloc:
        raise ValueError("URL 缺少有效的主机名")

    if parsed.username or parsed.password:
        raise ValueError("URL 不允许内嵌认证信息")

    if parsed.fragment:
        raise ValueError("URL 不允许包含片段")

    enforce_public_network = _should_enforce_public_network(require_public_network)

    if (
        enforce_public_network
        and not allow_configured_private_network
        and parsed.hostname.lower() in {"localhost", "localhost.localdomain"}
    ):
        raise ValueError("不允许访问本地主机")

    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise ValueError("URL 端口非法") from exc

    if enforce_public_network:
        for address in _resolve_ip_addresses(parsed.hostname, port):
            is_forbidden = (
                _is_unsafe_ip_address(address)
                if allow_configured_private_network
                else _is_non_public_ip_address(address)
            )
            if is_forbidden:
                raise ValueError(f"禁止访问非公网地址: {address}")

    return normalized_url
