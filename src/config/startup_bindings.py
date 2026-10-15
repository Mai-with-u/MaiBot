from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
import os
import sys

import tomlkit

from src.common.paths import get_instance_root


PROJECT_ROOT: Path = get_instance_root()
CONFIG_DIR: Path = PROJECT_ROOT / "config"
BOT_CONFIG_PATH: Path = (CONFIG_DIR / "bot_config.toml").resolve().absolute()


@dataclass(frozen=True)
class BindAddress:
    """启动阶段使用的绑定地址。"""

    hosts: list[str]
    port: int


_DEFAULT_MAIN_BIND_ADDRESS = BindAddress(hosts=["127.0.0.1"], port=8080)
_DEFAULT_WEBUI_BIND_ADDRESS = BindAddress(hosts=["127.0.0.1", "::1"], port=8001)
_IPV4_WILDCARD_HOST = "0.0.0.0"
_IPV6_WILDCARD_HOST = "::"
_WILDCARD_HOSTS = {_IPV4_WILDCARD_HOST, _IPV6_WILDCARD_HOST}


def _as_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    return value if isinstance(value, Mapping) else None


def _normalize_hosts(value: Any, default_hosts: list[str]) -> list[str]:
    if isinstance(value, list):
        hosts = [h.strip() for h in value if isinstance(h, str) and h.strip()]
        return hosts or default_hosts
    if isinstance(value, str):
        h = value.strip()
        return [h] if h else default_hosts
    return default_hosts


def _normalize_port(value: Any, default_port: int) -> int:
    if isinstance(value, bool):
        return default_port

    try:
        normalized_port = int(value)
    except (TypeError, ValueError):
        return default_port

    if normalized_port <= 0 or normalized_port > 65535:
        return default_port
    return normalized_port


def _parse_host_env(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized_value = value.strip()
    return normalized_value or None


def _merge_host(hosts: list[str], host: str) -> list[str]:
    """把额外监听地址合并到列表中，通配地址优先绑定。"""

    if host in hosts:
        return hosts
    if host in _WILDCARD_HOSTS:
        return [host, *hosts]
    return [*hosts, host]


def _is_ipv6_host(host: str) -> bool:
    return ":" in host


def _collapse_hosts_covered_by_wildcards(hosts: list[str]) -> list[str]:
    """移除会被通配监听地址覆盖的同协议族地址。"""

    has_ipv4_wildcard = _IPV4_WILDCARD_HOST in hosts
    has_ipv6_wildcard = _IPV6_WILDCARD_HOST in hosts
    collapsed_hosts: list[str] = []

    for host in hosts:
        if host in collapsed_hosts:
            continue
        if has_ipv4_wildcard and host != _IPV4_WILDCARD_HOST and not _is_ipv6_host(host):
            continue
        if has_ipv6_wildcard and host != _IPV6_WILDCARD_HOST and _is_ipv6_host(host):
            continue
        collapsed_hosts.append(host)

    return collapsed_hosts


def _apply_webui_host_env(address: BindAddress) -> BindAddress:
    webui_host_env = _parse_host_env(os.getenv("WEBUI_HOST"))
    if webui_host_env is None:
        return address
    return BindAddress(hosts=_merge_host(address.hosts, webui_host_env), port=address.port)


def _finalize_webui_bind_address(address: BindAddress) -> BindAddress:
    address_with_env = _apply_webui_host_env(address)
    return BindAddress(
        hosts=_collapse_hosts_covered_by_wildcards(address_with_env.hosts),
        port=address_with_env.port,
    )


def _load_bootstrap_config_dict(config_path: Path = BOT_CONFIG_PATH) -> Dict[str, Any]:
    """读取启动阶段需要的最小配置，不依赖完整 ConfigManager。"""

    if not config_path.exists():
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as file_obj:
            config_data = tomlkit.load(file_obj).unwrap()
    except Exception:
        return {}

    if not isinstance(config_data, dict):
        return {}
    return config_data


def _resolve_bind_address_from_section(
    section: Mapping[str, Any],
    host_key: str,
    port_key: str,
    default_address: BindAddress,
) -> BindAddress:
    return BindAddress(
        hosts=_normalize_hosts(section.get(host_key), default_address.hosts),
        port=_normalize_port(section.get(port_key), default_address.port),
    )


def _get_loaded_global_config() -> Optional[Any]:
    config_module = sys.modules.get("src.config.config")
    if config_module is None:
        return None
    return getattr(config_module, "global_config", None)


def get_startup_main_bind_address(config_path: Path = BOT_CONFIG_PATH) -> BindAddress:
    """读取主程序消息服务绑定地址。"""

    config_data = _load_bootstrap_config_dict(config_path)
    maim_message_config = _as_mapping(config_data.get("maim_message")) or {}
    return _resolve_bind_address_from_section(
        maim_message_config,
        host_key="ws_server_host",
        port_key="ws_server_port",
        default_address=_DEFAULT_MAIN_BIND_ADDRESS,
    )


def get_startup_webui_bind_address(config_path: Path = BOT_CONFIG_PATH) -> BindAddress:
    """读取 WebUI 绑定地址。"""

    config_data = _load_bootstrap_config_dict(config_path)
    webui_config = _as_mapping(config_data.get("webui")) or {}
    address = _resolve_bind_address_from_section(
        webui_config,
        host_key="host",
        port_key="port",
        default_address=_DEFAULT_WEBUI_BIND_ADDRESS,
    )
    return _finalize_webui_bind_address(address)


def resolve_main_bind_address(config_path: Path = BOT_CONFIG_PATH) -> BindAddress:
    """优先读取已初始化的主配置，否则回退到启动阶段配置读取。"""

    global_config = _get_loaded_global_config()
    if global_config is not None:
        return BindAddress(
            hosts=_normalize_hosts(global_config.maim_message.ws_server_host, _DEFAULT_MAIN_BIND_ADDRESS.hosts),
            port=global_config.maim_message.ws_server_port,
        )
    return get_startup_main_bind_address(config_path)


def resolve_webui_bind_address(config_path: Path = BOT_CONFIG_PATH) -> BindAddress:
    """优先读取已初始化的主配置，否则回退到启动阶段配置读取。"""

    global_config = _get_loaded_global_config()
    if global_config is not None:
        address = BindAddress(
            hosts=_normalize_hosts(global_config.webui.host, _DEFAULT_WEBUI_BIND_ADDRESS.hosts),
            port=global_config.webui.port,
        )
        return _finalize_webui_bind_address(address)
    return get_startup_webui_bind_address(config_path)
