"""宿主配置：对外 HTTP 地址、Cookie 与公开流量防护；Bot 只监听进程间通道。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal
from urllib.parse import urlsplit

import os
import tomllib

import tomlkit


DEFAULT_HOSTS = ["127.0.0.1", "::1"]
DEFAULT_PORT = 8001
_WILDCARD_HOSTS = {"0.0.0.0", "::"}
_ANTI_CRAWLER_MODES = ("false", "strict", "loose", "basic")

AntiCrawlerMode = Literal["false", "strict", "loose", "basic"]


@dataclass(frozen=True)
class HostConfig:
    webui_enabled: bool = True
    hosts: List[str] = field(default_factory=lambda: list(DEFAULT_HOSTS))
    port: int = DEFAULT_PORT
    secure_cookie: bool = False
    anti_crawler_mode: AntiCrawlerMode = "basic"
    allowed_ips: str = "127.0.0.1"
    trusted_proxies: str = ""
    trust_xff: bool = False
    public_base_url: str = ""
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    def advertised_base_url(self) -> str:
        """所有 Bot 共用的文件、预览等公开 URL 来源。"""
        return self.public_base_url or self.local_base_url()

    def bind_hosts(self) -> List[str]:
        """合并 HTTP_HOST（兼容旧的 WEBUI_HOST）环境变量；Docker 依赖它监听容器网卡。"""
        hosts = list(self.hosts)
        # HTTP_HOST 优先；即使为空也不再读取 WEBUI_HOST，空值表示不追加地址。
        value = os.environ["HTTP_HOST"] if "HTTP_HOST" in os.environ else os.environ.get("WEBUI_HOST", "")
        extra = value.strip()
        if extra and extra not in hosts:
            hosts = [extra, *hosts] if extra in _WILDCARD_HOSTS else [*hosts, extra]
        # 通配地址已覆盖同协议族的其他地址，重复绑定会因端口占用失败。
        result: List[str] = []
        for host in hosts:
            ipv6 = ":" in host
            if host not in _WILDCARD_HOSTS and ("::" if ipv6 else "0.0.0.0") in hosts:
                continue
            if host not in result:
                result.append(host)
        return result

    def local_base_url(self) -> str:
        """本机访问宿主的地址，用于日志、登录链接和文件传输的默认地址。"""
        hosts = self.bind_hosts()
        if any(host in {"0.0.0.0", "127.0.0.1"} for host in hosts):
            host = "127.0.0.1"
        elif any(host in {"::", "::1"} for host in hosts):
            host = "[::1]"
        else:
            host = f"[{hosts[0]}]" if ":" in hosts[0] else hosts[0]
        scheme = "https" if self.ssl_certfile else "http"
        return f"{scheme}://{host}:{self.port}"


def _validate(data: Dict[str, Any], source: Path) -> HostConfig:
    webui = data.get("webui", {})
    if not isinstance(webui, dict):
        raise ValueError(f"{source}: webui 必须是一个配置节")
    enabled = webui.get("enabled", True)
    hosts = webui.get("hosts", DEFAULT_HOSTS)
    port = webui.get("port", DEFAULT_PORT)
    secure_cookie = webui.get("secure_cookie", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"{source}: webui.enabled 必须是布尔值")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(host, str) and host.strip() for host in hosts):
        raise ValueError(f"{source}: webui.hosts 必须是非空的地址列表")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f"{source}: webui.port 必须是 1 到 65535 的整数")
    if not isinstance(secure_cookie, bool):
        raise ValueError(f"{source}: webui.secure_cookie 必须是布尔值")
    anti_crawler_mode = webui.get("anti_crawler_mode", "basic")
    if anti_crawler_mode not in _ANTI_CRAWLER_MODES:
        raise ValueError(f"{source}: webui.anti_crawler_mode 必须是 {' / '.join(_ANTI_CRAWLER_MODES)} 之一")
    allowed_ips = webui.get("allowed_ips", "127.0.0.1")
    trusted_proxies = webui.get("trusted_proxies", "")
    if not isinstance(allowed_ips, str) or not isinstance(trusted_proxies, str):
        raise ValueError(f"{source}: webui.allowed_ips 与 webui.trusted_proxies 必须是逗号分隔的字符串")
    trust_xff = webui.get("trust_xff", False)
    if not isinstance(trust_xff, bool):
        raise ValueError(f"{source}: webui.trust_xff 必须是布尔值")
    public_base_url = webui.get("public_base_url", "")
    ssl_certfile = webui.get("ssl_certfile", "")
    ssl_keyfile = webui.get("ssl_keyfile", "")
    if not isinstance(ssl_certfile, str) or not isinstance(ssl_keyfile, str) or bool(ssl_certfile) != bool(ssl_keyfile):
        raise ValueError(f"{source}: ssl_certfile 与 ssl_keyfile 必须同时填写证书和私钥路径")
    if not isinstance(public_base_url, str):
        raise ValueError(f"{source}: webui.public_base_url 必须是字符串")
    public_base_url = public_base_url.strip().rstrip("/")
    if public_base_url:
        parsed = urlsplit(public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{source}: public_base_url 必须是无凭证、查询参数和片段的 HTTP(S) 地址")
    return HostConfig(
        webui_enabled=enabled, hosts=[host.strip() for host in hosts], port=port, secure_cookie=secure_cookie,
        anti_crawler_mode=anti_crawler_mode, allowed_ips=allowed_ips, trusted_proxies=trusted_proxies, trust_xff=trust_xff,
        public_base_url=public_base_url,
        ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile,
    )


def config_path(root: Path) -> Path:
    return root / "host" / "config.toml"


def load_host_config(root: Path) -> HostConfig:
    path = config_path(root)
    with path.open("rb") as stream:
        return _validate(tomllib.load(stream), path)


def save_host_config(root: Path, config: HostConfig) -> None:
    document = tomlkit.document()
    webui = tomlkit.table()
    webui.add(tomlkit.comment("对外 WebUI 与文件传输地址；所有 Bot 共用此地址"))
    webui.add("enabled", config.webui_enabled)
    webui.add("hosts", config.hosts)
    webui.add("port", config.port)
    webui.add(tomlkit.comment("外部客户端可访问的宿主 HTTP(S) 地址，所有 Bot 共用；留空使用本机地址"))
    webui.add("public_base_url", config.public_base_url)
    webui.add("ssl_certfile", config.ssl_certfile)
    webui.add("ssl_keyfile", config.ssl_keyfile)
    webui.add("secure_cookie", config.secure_cookie)
    webui.add(tomlkit.comment("防爬虫模式：false 关闭 / basic 只记录 / loose 宽松拦截 / strict 严格拦截"))
    webui.add("anti_crawler_mode", config.anti_crawler_mode)
    webui.add(tomlkit.comment("不受防爬虫限制的 IP，逗号分隔，支持 CIDR 与通配符"))
    webui.add("allowed_ips", config.allowed_ips)
    webui.add(tomlkit.comment("信任的反向代理 IP；仅当 trust_xff 开启且直连地址在此列表中时采用 X-Forwarded-For"))
    webui.add("trusted_proxies", config.trusted_proxies)
    webui.add("trust_xff", config.trust_xff)
    document.add("webui", webui)
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(tomlkit.dumps(document))


def config_from_bot(bot_config_path: Path) -> HostConfig:
    """旧用户首次进入宿主时沿用原 Bot 的对外地址；只读取，不改写原配置。"""
    with bot_config_path.open("rb") as stream:
        data = tomllib.load(stream)
    http = data.get("http", {})
    webui = data.get("webui", {})
    if not isinstance(http, dict) or not isinstance(webui, dict):
        raise ValueError(f"{bot_config_path}: http 与 webui 必须是配置节")
    hosts = http.get("host", DEFAULT_HOSTS)
    return _validate({"webui": {
        "enabled": webui.get("enabled", True),
        "hosts": [hosts] if isinstance(hosts, str) else hosts,
        "port": http.get("port", DEFAULT_PORT),
        "public_base_url": data.get("files", {}).get("transfer_base_url", ""),
        "secure_cookie": webui.get("secure_cookie", False),
        "anti_crawler_mode": webui.get("anti_crawler_mode", "basic"),
        "allowed_ips": webui.get("allowed_ips", "127.0.0.1"),
        "trusted_proxies": webui.get("trusted_proxies", ""),
        "trust_xff": webui.get("trust_xff", False),
    }}, bot_config_path)
