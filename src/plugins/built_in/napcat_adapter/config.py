"""NapCat 内置适配器配置解析。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Set, Tuple
from urllib.parse import urlparse

from napcat_adapter.constants import (
    DEFAULT_ACTION_TIMEOUT_SEC,
    DEFAULT_CHAT_LIST_TYPE,
    DEFAULT_HEARTBEAT_INTERVAL_SEC,
    DEFAULT_NAPCAT_HOST,
    DEFAULT_NAPCAT_PORT,
    DEFAULT_RECONNECT_DELAY_SEC,
    SUPPORTED_CONFIG_VERSION,
)


@dataclass(frozen=True)
class NapCatPluginOptions:
    """插件级配置。"""

    enabled: bool = False
    config_version: str = ""

    def should_connect(self) -> bool:
        """判断当前配置下是否应当启动连接。

        Returns:
            bool: 若插件连接已启用，则返回 ``True``。
        """
        return self.enabled


@dataclass(frozen=True)
class NapCatServerConfig:
    """NapCat 正向 WebSocket 连接配置。"""

    host: str = DEFAULT_NAPCAT_HOST
    port: int = DEFAULT_NAPCAT_PORT
    token: str = ""
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SEC
    reconnect_delay_sec: float = DEFAULT_RECONNECT_DELAY_SEC
    action_timeout_sec: float = DEFAULT_ACTION_TIMEOUT_SEC
    connection_id: str = ""

    def build_ws_url(self) -> str:
        """构造正向 WebSocket 地址。

        Returns:
            str: 供适配器作为客户端连接的 NapCat WebSocket 地址。
        """
        return f"ws://{self.host}:{self.port}"


@dataclass(frozen=True)
class NapCatChatConfig:
    """聊天名单配置。"""

    group_list_type: str = DEFAULT_CHAT_LIST_TYPE
    group_list: Set[str] = field(default_factory=set)
    private_list_type: str = DEFAULT_CHAT_LIST_TYPE
    private_list: Set[str] = field(default_factory=set)
    ban_user_id: Set[str] = field(default_factory=set)


@dataclass(frozen=True)
class NapCatFilterConfig:
    """消息过滤配置。"""

    ignore_self_message: bool = True


@dataclass(frozen=True)
class NapCatPluginSettings:
    """NapCat 插件完整配置。"""

    plugin: NapCatPluginOptions = field(default_factory=NapCatPluginOptions)
    napcat_server: NapCatServerConfig = field(default_factory=NapCatServerConfig)
    chat: NapCatChatConfig = field(default_factory=NapCatChatConfig)
    filters: NapCatFilterConfig = field(default_factory=NapCatFilterConfig)

    @classmethod
    def from_mapping(cls, raw_config: Mapping[str, Any], logger: Any) -> "NapCatPluginSettings":
        """从 Runner 注入的原始配置字典解析插件配置。

        Args:
            raw_config: Runner 注入的原始配置内容。
            logger: 插件日志对象。

        Returns:
            NapCatPluginSettings: 规范化后的插件配置。
        """
        plugin_section = _as_mapping(raw_config.get("plugin"))
        server_section = _as_mapping(raw_config.get("napcat_server"))
        legacy_connection_section = _as_mapping(raw_config.get("connection"))
        chat_section = _as_mapping(raw_config.get("chat"))
        filters_section = _as_mapping(raw_config.get("filters"))

        if not server_section and legacy_connection_section:
            logger.warning("NapCat 适配器检测到旧版 [connection] 配置段，请尽快迁移到 [napcat_server]")
            server_section = legacy_connection_section

        legacy_host, legacy_port = _read_legacy_host_port(server_section, legacy_connection_section, logger)
        parsed_host = _read_string(server_section, "host") or legacy_host or DEFAULT_NAPCAT_HOST
        parsed_port = _read_positive_int(
            mapping=server_section,
            key="port",
            default=legacy_port or DEFAULT_NAPCAT_PORT,
            logger=logger,
            setting_name="napcat_server.port",
        )

        return cls(
            plugin=NapCatPluginOptions(
                enabled=_read_bool(plugin_section, "enabled", False),
                config_version=_read_string(plugin_section, "config_version"),
            ),
            napcat_server=NapCatServerConfig(
                host=parsed_host,
                port=parsed_port,
                token=_read_string(server_section, "token") or _read_string(server_section, "access_token"),
                heartbeat_interval=_read_positive_float(
                    mapping=server_section,
                    key="heartbeat_interval",
                    default=_read_positive_float(
                        mapping=server_section,
                        key="heartbeat_sec",
                        default=DEFAULT_HEARTBEAT_INTERVAL_SEC,
                        logger=logger,
                        setting_name="napcat_server.heartbeat_interval",
                    ),
                    logger=logger,
                    setting_name="napcat_server.heartbeat_interval",
                ),
                reconnect_delay_sec=_read_positive_float(
                    mapping=server_section,
                    key="reconnect_delay_sec",
                    default=DEFAULT_RECONNECT_DELAY_SEC,
                    logger=logger,
                    setting_name="napcat_server.reconnect_delay_sec",
                ),
                action_timeout_sec=_read_positive_float(
                    mapping=server_section,
                    key="action_timeout_sec",
                    default=DEFAULT_ACTION_TIMEOUT_SEC,
                    logger=logger,
                    setting_name="napcat_server.action_timeout_sec",
                ),
                connection_id=_read_string(server_section, "connection_id"),
            ),
            chat=NapCatChatConfig(
                group_list_type=_read_list_mode(
                    mapping=chat_section,
                    key="group_list_type",
                    default=DEFAULT_CHAT_LIST_TYPE,
                    logger=logger,
                    setting_name="chat.group_list_type",
                ),
                group_list=_read_string_set(chat_section, "group_list"),
                private_list_type=_read_list_mode(
                    mapping=chat_section,
                    key="private_list_type",
                    default=DEFAULT_CHAT_LIST_TYPE,
                    logger=logger,
                    setting_name="chat.private_list_type",
                ),
                private_list=_read_string_set(chat_section, "private_list"),
                ban_user_id=_read_string_set(chat_section, "ban_user_id"),
            ),
            filters=NapCatFilterConfig(
                ignore_self_message=_read_bool(filters_section, "ignore_self_message", True),
            ),
        )

    def should_connect(self) -> bool:
        """判断当前配置下是否应当启动连接。

        Returns:
            bool: 若插件连接已启用，则返回 ``True``。
        """
        return self.plugin.should_connect()

    def validate(self, logger: Any) -> bool:
        """校验当前配置是否满足启动连接的前提条件。

        Args:
            logger: 插件日志对象。

        Returns:
            bool: 若配置满足启动连接的前提条件，则返回 ``True``。
        """
        config_version = self.plugin.config_version
        if not config_version:
            logger.error(
                f"NapCat 适配器配置缺少 plugin.config_version，当前插件要求版本 {SUPPORTED_CONFIG_VERSION}"
            )
            return False

        if config_version != SUPPORTED_CONFIG_VERSION:
            logger.error(
                "NapCat 适配器配置版本不兼容: "
                f"当前为 {config_version}，当前插件要求 {SUPPORTED_CONFIG_VERSION}"
            )
            return False

        if not self.napcat_server.host:
            logger.warning("NapCat 适配器已启用，但 napcat_server.host 为空")
            return False

        if self.napcat_server.port <= 0:
            logger.warning("NapCat 适配器已启用，但 napcat_server.port 不是正整数")
            return False

        return True


def _as_mapping(value: Any) -> Dict[str, Any]:
    """将任意值安全转换为字典。

    Args:
        value: 待转换的值。

    Returns:
        Dict[str, Any]: 若原值是映射，则返回普通字典；否则返回空字典。
    """
    return dict(value) if isinstance(value, Mapping) else {}


def _read_bool(mapping: Mapping[str, Any], key: str, default: bool) -> bool:
    """安全读取布尔配置值。

    Args:
        mapping: 待读取的配置字典。
        key: 目标键名。
        default: 读取失败时的默认值。

    Returns:
        bool: 解析后的布尔值。
    """
    value = mapping.get(key, default)
    return value if isinstance(value, bool) else default


def _read_string(mapping: Mapping[str, Any], key: str) -> str:
    """安全读取字符串配置值。

    Args:
        mapping: 待读取的配置字典。
        key: 目标键名。

    Returns:
        str: 去除首尾空白后的字符串值。
    """
    value = mapping.get(key)
    return "" if value is None else str(value).strip()


def _read_positive_float(
    mapping: Mapping[str, Any],
    key: str,
    default: float,
    logger: Any,
    setting_name: str,
) -> float:
    """安全读取正浮点数配置值。

    Args:
        mapping: 待读取的配置字典。
        key: 目标键名。
        default: 读取失败时的默认值。
        logger: 插件日志对象。
        setting_name: 用于日志输出的完整配置名。

    Returns:
        float: 合法的正浮点数；否则返回默认值。
    """
    value = mapping.get(key, default)
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)

    if key in mapping:
        logger.warning(f"NapCat 适配器配置项取值无效，已回退到默认值: {setting_name}={value!r}，默认值为 {default}")
    return default


def _read_positive_int(
    mapping: Mapping[str, Any],
    key: str,
    default: int,
    logger: Any,
    setting_name: str,
) -> int:
    """安全读取正整数配置值。

    Args:
        mapping: 待读取的配置字典。
        key: 目标键名。
        default: 读取失败时的默认值。
        logger: 插件日志对象。
        setting_name: 用于日志输出的完整配置名。

    Returns:
        int: 合法的正整数；否则返回默认值。
    """
    value = mapping.get(key, default)
    if isinstance(value, int) and value > 0:
        return value

    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)

    if key in mapping:
        logger.warning(f"NapCat 适配器配置项取值无效，已回退到默认值: {setting_name}={value!r}，默认值为 {default}")
    return default


def _read_list_mode(
    mapping: Mapping[str, Any],
    key: str,
    default: str,
    logger: Any,
    setting_name: str,
) -> str:
    """安全读取名单模式配置值。

    Args:
        mapping: 待读取的配置字典。
        key: 目标键名。
        default: 读取失败时的默认值。
        logger: 插件日志对象。
        setting_name: 用于日志输出的完整配置名。

    Returns:
        str: 合法的名单模式字符串。
    """
    value = mapping.get(key, default)
    if isinstance(value, str):
        normalized_value = value.strip()
        if normalized_value in {"whitelist", "blacklist"}:
            return normalized_value

    if key in mapping:
        logger.warning(f"NapCat 适配器配置项取值无效，已回退到默认值: {setting_name}={value!r}，默认值为 {default}")
    return default


def _read_string_set(mapping: Mapping[str, Any], key: str) -> Set[str]:
    """安全读取字符串集合配置值。

    Args:
        mapping: 待读取的配置字典。
        key: 目标键名。

    Returns:
        Set[str]: 规范化后的字符串集合。
    """
    value = mapping.get(key, [])
    if not isinstance(value, list):
        return set()

    normalized_values: Set[str] = set()
    for item in value:
        item_text = "" if item is None else str(item).strip()
        if item_text:
            normalized_values.add(item_text)
    return normalized_values


def _read_legacy_host_port(
    server_section: Mapping[str, Any],
    legacy_connection_section: Mapping[str, Any],
    logger: Any,
) -> Tuple[str, Optional[int]]:
    """从旧版 ``ws_url`` 配置中提取主机与端口。

    Args:
        server_section: 新版 ``napcat_server`` 配置段。
        legacy_connection_section: 旧版 ``connection`` 配置段。
        logger: 插件日志对象。

    Returns:
        Tuple[str, Optional[int]]: 解析到的主机与端口；若未找到，则返回空主机与 ``None``。
    """
    legacy_ws_url = _read_string(server_section, "ws_url") or _read_string(legacy_connection_section, "ws_url")
    if not legacy_ws_url:
        return "", None

    parsed_url = urlparse(legacy_ws_url)
    parsed_host = parsed_url.hostname or ""
    parsed_port = parsed_url.port

    logger.warning(
        "NapCat 适配器检测到旧版 ws_url 配置，已临时兼容解析，请尽快迁移到 napcat_server.host/port"
    )
    if parsed_url.path not in {"", "/"}:
        logger.warning("NapCat 适配器旧版 ws_url 包含路径，新的 napcat_server 配置不会保留该路径")

    return parsed_host, parsed_port
