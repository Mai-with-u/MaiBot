"""提供 Platform IO 路由键的统一提取与构造能力。

这层的目标不是直接接入具体消息链，而是先把“未来接线时用什么字段构造
RouteKey”约定下来，避免 legacy 和 plugin 两条链路各自发明一套隐式规则。
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from .types import RouteKey

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage


class RouteKeyFactory:
    """统一构造 ``RouteKey`` 的工厂。

    当前约定会优先从消息字典顶层、``message_info``、``additional_config`` 或传入 metadata 中提取
    以下字段：

    - account_id: ``platform_io_account_id`` / ``account_id`` / ``self_id`` / ``bot_account``
    - scope: ``platform_io_scope`` / ``route_scope`` / ``adapter_scope`` / ``connection_id``

    这样即使上游主链暂时还没有正式的 ``self_id`` 字段，中间层也能先统一
    约定提取口径，等具体消息链接入时直接复用。
    """

    ACCOUNT_ID_KEYS = (
        "platform_io_account_id",
        "account_id",
        "self_id",
        "bot_account",
    )
    SCOPE_KEYS = (
        "platform_io_scope",
        "route_scope",
        "adapter_scope",
        "connection_id",
    )

    @classmethod
    def from_platform(
        cls,
        platform: str,
        *,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RouteKey:
        """根据平台名和可选 metadata 构造 ``RouteKey``。

        Args:
            platform: 平台名称。
            account_id: 显式传入的账号 ID；若为空，则尝试从 metadata 提取。
            scope: 显式传入的路由作用域；若为空，则尝试从 metadata 提取。
            metadata: 可选的元数据字典。

        Returns:
            RouteKey: 构造出的规范化路由键。
        """
        extracted_account_id, extracted_scope = cls.extract_components(metadata)
        return RouteKey(
            platform=platform,
            account_id=account_id or extracted_account_id,
            scope=scope or extracted_scope,
        )

    @classmethod
    def from_message_dict(cls, message_dict: Dict[str, Any]) -> RouteKey:
        """从消息字典中提取 ``RouteKey``。

        Args:
            message_dict: Host 与插件之间传输的消息字典。

        Returns:
            RouteKey: 构造出的规范化路由键。

        Raises:
            ValueError: 当消息字典缺少有效 ``platform`` 字段时抛出。
        """
        platform = str(message_dict.get("platform") or "").strip()
        if not platform:
            raise ValueError("消息字典缺少有效的 platform 字段，无法构造 RouteKey")

        message_info = message_dict.get("message_info", {})
        additional_config = {}
        if isinstance(message_info, dict):
            raw_additional_config = message_info.get("additional_config", {})
            if isinstance(raw_additional_config, dict):
                additional_config = raw_additional_config

        explicit_account_id, explicit_scope = cls.extract_components(message_dict)
        message_info_account_id, message_info_scope = cls.extract_components(message_info)
        metadata_account_id, metadata_scope = cls.extract_components(additional_config)
        return RouteKey(
            platform=platform,
            account_id=explicit_account_id or message_info_account_id or metadata_account_id,
            scope=explicit_scope or message_info_scope or metadata_scope,
        )

    @classmethod
    def from_session_message(cls, message: "SessionMessage") -> RouteKey:
        """从 ``SessionMessage`` 中提取 ``RouteKey``。

        Args:
            message: 内部会话消息对象。

        Returns:
            RouteKey: 构造出的规范化路由键。
        """
        additional_config = message.message_info.additional_config or {}
        metadata = additional_config if isinstance(additional_config, dict) else {}
        return cls.from_platform(message.platform, metadata=metadata)

    @classmethod
    def extract_components(cls, mapping: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        """从任意字典中提取 ``account_id`` 与 ``scope``。

        Args:
            mapping: 待提取的字典；若为空或不是字典，则返回空结果。

        Returns:
            Tuple[Optional[str], Optional[str]]: ``(account_id, scope)``。
        """
        if not mapping or not isinstance(mapping, dict):
            return None, None

        account_id = cls._pick_string(mapping, cls.ACCOUNT_ID_KEYS)
        scope = cls._pick_string(mapping, cls.SCOPE_KEYS)
        return account_id, scope

    @staticmethod
    def _pick_string(mapping: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[str]:
        """按优先级从字典里挑选第一个有效字符串。

        Args:
            mapping: 待查询的字典。
            keys: 按优先级排列的候选键名。

        Returns:
            Optional[str]: 第一个规范化后非空的字符串值；若不存在则返回 ``None``。
        """
        for key in keys:
            value = mapping.get(key)
            if value is None:
                continue
            normalized = str(value).strip()
            if normalized:
                return normalized
        return None
