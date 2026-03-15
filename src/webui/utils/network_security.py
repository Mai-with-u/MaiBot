from typing import Iterable

import ipaddress
import socket

from urllib.parse import urlparse


def _resolve_ip_addresses(hostname: str, port: int) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        address_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析主机名: {hostname}") from exc

    resolved_addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for _, _, _, _, sockaddr in address_infos:
        host_address = sockaddr[0]
        if not isinstance(host_address, str):
            continue

        raw_ip = host_address.split("%", 1)[0]
        resolved_addresses.add(ipaddress.ip_address(raw_ip))

    if not resolved_addresses:
        raise ValueError(f"无法解析主机名: {hostname}")

    return resolved_addresses


def _is_forbidden_ip_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_private,
            address.is_reserved,
            address.is_unspecified,
            getattr(address, "is_site_local", False),
        )
    )


def validate_public_url(url: str, allowed_schemes: Iterable[str] = ("https",)) -> str:
    normalized_url = url.strip()
    if not normalized_url:
        raise ValueError("URL 不能为空")

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

    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("不允许访问本地主机")

    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise ValueError("URL 端口非法") from exc

    for address in _resolve_ip_addresses(parsed.hostname, port):
        if _is_forbidden_ip_address(address):
            raise ValueError(f"禁止访问非公网地址: {address}")

    return normalized_url