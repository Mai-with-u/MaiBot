"""
Tests for CWE-918 SSRF fix in MCPReadResourceTool._is_uri_safe

Validates that the URI validation blocks:
- file:// and other dangerous schemes (gopher, ftp, dict, ldap, etc.)
- http(s):// to private/loopback/link-local/reserved IPs
- Cloud metadata endpoints (169.254.169.254)

And allows:
- http(s):// to public IPs
- Custom MCP schemes (mydb://, postgres://, etc.)
"""

import asyncio
import socket
import ipaddress
from typing import Tuple
from urllib.parse import urlparse

import pytest


# ---------------------------------------------------------------------------
# Reproduce _is_uri_safe exactly as patched in plugin.py so we can test it
# without importing the full plugin (which requires nonebot/src.* deps).
# ---------------------------------------------------------------------------

_DNS_CHECKED_SCHEMES = {"http", "https", "ws", "wss"}
_BLOCKED_SCHEMES = {"file", "ftp", "gopher", "dict", "ldap", "tftp", "netdoc", "jar", "data"}


async def _is_uri_safe(uri: str) -> Tuple[bool, str]:
    try:
        parsed = urlparse(uri)
    except Exception:
        return False, "URI 格式无效"

    scheme = (parsed.scheme or "").lower()

    if scheme in _BLOCKED_SCHEMES:
        return False, f"不允许使用 {scheme}:// 协议"

    if scheme in _DNS_CHECKED_SCHEMES:
        hostname = parsed.hostname or ""
        if not hostname:
            return False, "缺少主机名"
        try:
            loop = asyncio.get_running_loop()
            addrinfos = await loop.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            for _family, _type, _proto, _canonname, sockaddr in addrinfos:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False, f"目标地址 {ip} 属于内网/保留地址段，已拦截"
        except socket.gaierror:
            return False, f"无法解析主机名: {hostname}"
        except Exception as e:
            return False, f"地址检查失败: {e}"

    return True, ""


# ===========================================================================
# Tests: blocked schemes
# ===========================================================================

@pytest.mark.asyncio
class TestBlockedSchemes:
    """Known-dangerous URI schemes must be blocked outright."""

    async def test_file_scheme_blocked(self):
        safe, reason = await _is_uri_safe("file:///etc/passwd")
        assert not safe
        assert "file://" in reason

    async def test_file_scheme_uppercase_blocked(self):
        safe, _ = await _is_uri_safe("FILE:///etc/passwd")
        assert not safe

    async def test_gopher_blocked(self):
        safe, reason = await _is_uri_safe("gopher://evil.com:25/_HELO")
        assert not safe
        assert "gopher://" in reason

    async def test_ftp_blocked(self):
        safe, reason = await _is_uri_safe("ftp://169.254.169.254/")
        assert not safe
        assert "ftp://" in reason

    async def test_dict_blocked(self):
        safe, _ = await _is_uri_safe("dict://evil:11211/stat")
        assert not safe

    async def test_ldap_blocked(self):
        safe, _ = await _is_uri_safe("ldap://internal:389/dc=example")
        assert not safe

    async def test_tftp_blocked(self):
        safe, _ = await _is_uri_safe("tftp://internal/secret.conf")
        assert not safe

    async def test_data_blocked(self):
        safe, _ = await _is_uri_safe("data://text/plain;base64,SGVsbG8=")
        assert not safe

    async def test_netdoc_blocked(self):
        safe, _ = await _is_uri_safe("netdoc:///etc/passwd")
        assert not safe

    async def test_jar_blocked(self):
        safe, _ = await _is_uri_safe("jar:file:///etc/passwd!/")
        assert not safe


# ===========================================================================
# Tests: HTTP/HTTPS internal IP blocking
# ===========================================================================

@pytest.mark.asyncio
class TestHttpInternalBlocked:
    """http(s):// to private/loopback/link-local IPs must be blocked."""

    async def test_localhost_blocked(self):
        safe, _ = await _is_uri_safe("http://localhost/admin")
        assert not safe

    async def test_127_0_0_1_blocked(self):
        safe, _ = await _is_uri_safe("http://127.0.0.1/admin")
        assert not safe

    async def test_192_168_blocked(self):
        safe, _ = await _is_uri_safe("http://192.168.1.1/api")
        assert not safe

    async def test_10_network_blocked(self):
        safe, _ = await _is_uri_safe("http://10.0.0.1/api")
        assert not safe

    async def test_cloud_metadata_via_dns(self):
        """169.254.169.254 (cloud metadata) blocked via DNS resolution."""
        async def mock_getaddrinfo(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('169.254.169.254', 0))]

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        loop.getaddrinfo = mock_getaddrinfo
        try:
            safe, _ = await _is_uri_safe("http://metadata.google.internal/computeMetadata/v1/")
            assert not safe
        finally:
            loop.getaddrinfo = original

    async def test_ipv6_loopback_blocked(self):
        async def mock_getaddrinfo(*args, **kwargs):
            return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('::1', 0, 0, 0))]

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        loop.getaddrinfo = mock_getaddrinfo
        try:
            safe, _ = await _is_uri_safe("http://[::1]/admin")
            assert not safe
        finally:
            loop.getaddrinfo = original

    async def test_ipv4_mapped_ipv6_blocked(self):
        """IPv4-mapped IPv6 (::ffff:127.0.0.1) must be blocked."""
        async def mock_getaddrinfo(*args, **kwargs):
            return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('::ffff:127.0.0.1', 0, 0, 0))]

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        loop.getaddrinfo = mock_getaddrinfo
        try:
            safe, _ = await _is_uri_safe("http://[::ffff:127.0.0.1]/")
            assert not safe
        finally:
            loop.getaddrinfo = original


    async def test_ipv6_link_local_blocked(self):
        """IPv6 link-local (fe80::) must be blocked."""
        async def mock_getaddrinfo(*args, **kwargs):
            return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('fe80::1', 0, 0, 0))]

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        loop.getaddrinfo = mock_getaddrinfo
        try:
            safe, _ = await _is_uri_safe("http://link-local.example/")
            assert not safe
        finally:
            loop.getaddrinfo = original

    async def test_http_missing_hostname(self):
        safe, reason = await _is_uri_safe("http:///path")
        assert not safe
        assert "主机名" in reason

    async def test_dns_failure_blocked(self):
        """Unresolvable hostnames should be blocked."""
        async def mock_getaddrinfo(*args, **kwargs):
            raise socket.gaierror("DNS resolution failed")

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        loop.getaddrinfo = mock_getaddrinfo
        try:
            safe, reason = await _is_uri_safe("https://nonexistent.invalid/path")
            assert not safe
            assert "无法解析" in reason
        finally:
            loop.getaddrinfo = original


# ===========================================================================
# Tests: allowed URIs
# ===========================================================================

@pytest.mark.asyncio
class TestAllowedUris:
    """Public HTTP(S) and custom MCP schemes should be allowed."""

    async def test_public_https_allowed(self):
        async def mock_getaddrinfo(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('8.8.8.8', 0))]

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        loop.getaddrinfo = mock_getaddrinfo
        try:
            safe, reason = await _is_uri_safe("https://api.example.com/data")
            assert safe, f"Public HTTPS should be allowed, got: {reason}"
        finally:
            loop.getaddrinfo = original

    async def test_custom_mcp_scheme_allowed(self):
        """Non-blocked custom schemes (MCP resource protocols) pass through."""
        safe, reason = await _is_uri_safe("mydb://table/row")
        assert safe, f"Custom MCP scheme should be allowed, got: {reason}"

    async def test_postgres_scheme_allowed(self):
        """postgres:// is allowed - security for custom protocols is delegated to MCP servers."""
        safe, reason = await _is_uri_safe("postgres://localhost/mydb")
        assert safe, f"postgres:// MCP resource should be allowed, got: {reason}"

    async def test_sqlite_scheme_allowed(self):
        safe, _ = await _is_uri_safe("sqlite:///path/to/db")
        assert safe


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
