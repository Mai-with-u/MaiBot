"""只从适配器解析的 HTTP 地址流式下载，并在实际连接时校验 DNS。"""

from typing import AsyncIterator, List
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from aiohttp.resolver import ThreadedResolver

import ipaddress
import socket


def public_address(host: str) -> bool:
    address = ipaddress.ip_address(host)
    return address.is_global and not address.is_multicast and not address.is_reserved


def validate_url(url: str, trusted_origins: List[str]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("附件下载只接受不含凭证的 HTTP(S) 地址")
    origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    trusted = origin in {value.rstrip("/").lower() for value in trusted_origins}
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return trusted
    if not trusted and not public_address(str(address)):
        raise PermissionError("附件下载禁止访问内网地址；管理员可配置精确的受信任来源")
    return trusted


class PublicResolver(ThreadedResolver):
    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        results = await super().resolve(host, port, family)
        if any(not public_address(item["host"]) for item in results):
            raise PermissionError("附件地址解析到非公网 IP")
        return results


async def download_chunks(url: str, trusted_origins: List[str], timeout: int) -> AsyncIterator[bytes]:
    # 每次重定向都重新校验来源；同一 resolver 的结果直接用于连接，避免二次 DNS 查询。
    for redirect in range(4):
        trusted = validate_url(url, trusted_origins)
        resolver = ThreadedResolver() if trusted else PublicResolver()
        async with ClientSession(connector=TCPConnector(resolver=resolver), trust_env=False,
                                 timeout=ClientTimeout(total=timeout), auto_decompress=False) as client:
            async with client.get(url, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    if redirect == 3 or "Location" not in response.headers:
                        raise ValueError("附件下载重定向过多或缺少目标")
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                async for chunk in response.content.iter_chunked(256 * 1024):
                    yield chunk
                return
