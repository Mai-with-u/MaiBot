"""定义 Platform IO 中间层共享的核心类型。

本模块放置路由、驱动、入站与出站等规范化数据结构，供 Broker
层在 legacy 适配器链路和 plugin 适配器链路之间复用。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage


class DriverKind(str, Enum):
    """底层收发驱动类型枚举。"""

    LEGACY = "legacy"
    PLUGIN = "plugin"


class RouteMode(str, Enum):
    """路由归属模式枚举。"""

    ACTIVE = "active"
    SHADOW = "shadow"
    DISABLED = "disabled"


class DeliveryStatus(str, Enum):
    """统一出站回执状态枚举。"""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    DROPPED = "dropped"


@dataclass(frozen=True, slots=True)
class RouteKey:
    """用于 Platform IO 路由决策的唯一键。

    路由解析会按照“从最具体到最宽泛”的顺序进行回退，这样同一平台
    后续就能自然支持按账号、自定义 scope 等更细粒度的归属控制。

    Attributes:
        platform: 平台名称，例如 ``qq``。
        account_id: 机器人账号 ID 或 self ID，用于区分同平台多身份。
        scope: 额外路由作用域，预留给未来的连接实例、租户或子通道等维度。
    """

    platform: str
    account_id: Optional[str] = None
    scope: Optional[str] = None

    def __post_init__(self) -> None:
        """规范化并校验路由键字段。

        Raises:
            ValueError: 当 ``platform`` 规范化后为空时抛出。
        """
        platform = str(self.platform).strip()
        account_id = str(self.account_id).strip() if self.account_id is not None else None
        scope = str(self.scope).strip() if self.scope is not None else None

        if not platform:
            raise ValueError("RouteKey.platform 不能为空")

        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "account_id", account_id or None)
        object.__setattr__(self, "scope", scope or None)

    def resolution_order(self) -> List["RouteKey"]:
        """返回从最具体到最宽泛的路由匹配顺序。

        Returns:
            List[RouteKey]: 按回退优先级排序的候选路由键列表。
        """

        keys: List[RouteKey] = [self]

        if self.account_id is not None and self.scope is not None:
            keys.append(RouteKey(platform=self.platform, account_id=self.account_id, scope=None))
            keys.append(RouteKey(platform=self.platform, account_id=None, scope=self.scope))
        elif self.account_id is not None:
            keys.append(RouteKey(platform=self.platform, account_id=None, scope=None))
        elif self.scope is not None:
            keys.append(RouteKey(platform=self.platform, account_id=None, scope=None))

        default_key = RouteKey(platform=self.platform, account_id=None, scope=None)
        if default_key not in keys:
            keys.append(default_key)

        return keys

    def to_dedupe_scope(self) -> str:
        """生成跨驱动共享的去重作用域字符串。

        Returns:
            str: 用于入站消息去重的稳定文本作用域键。
        """

        account_id = self.account_id or "*"
        scope = self.scope or "*"
        return f"{self.platform}:{account_id}:{scope}"


@dataclass(frozen=True, slots=True)
class DriverDescriptor:
    """描述一个已注册的 Platform IO 驱动。

    Attributes:
        driver_id: Broker 层内全局唯一的驱动标识。
        kind: 驱动实现类型，例如 legacy 或 plugin。
        platform: 驱动负责的平台名称。
        account_id: 可选的账号 ID 或 self ID。
        scope: 可选的额外路由作用域。
        plugin_id: 当驱动来自插件适配器时，对应的插件 ID。
        metadata: 预留给路由策略或观测能力的额外驱动元数据。
    """

    driver_id: str
    kind: DriverKind
    platform: str
    account_id: Optional[str] = None
    scope: Optional[str] = None
    plugin_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """规范化并校验驱动描述字段。

        Raises:
            ValueError: 当 ``driver_id`` 或 ``platform`` 规范化后为空时抛出。
        """
        driver_id = str(self.driver_id).strip()
        platform = str(self.platform).strip()
        plugin_id = str(self.plugin_id).strip() if self.plugin_id is not None else None

        if not driver_id:
            raise ValueError("DriverDescriptor.driver_id 不能为空")
        if not platform:
            raise ValueError("DriverDescriptor.platform 不能为空")

        object.__setattr__(self, "driver_id", driver_id)
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "plugin_id", plugin_id or None)

    @property
    def route_key(self) -> RouteKey:
        """构造该驱动默认代表的路由键。

        Returns:
            RouteKey: 当前驱动描述对应的规范化路由键。
        """
        return RouteKey(platform=self.platform, account_id=self.account_id, scope=self.scope)


@dataclass(frozen=True, slots=True)
class RouteBinding:
    """表示一条从路由键到驱动的归属绑定关系。

    Attributes:
        route_key: 该绑定覆盖的路由键。
        driver_id: 拥有或旁路观察该路由的驱动 ID。
        driver_kind: 绑定驱动的类型。
        mode: 绑定模式，例如 active owner 或 shadow observer。
        priority: 当同模式下存在多条绑定时使用的相对优先级。
        metadata: 预留给未来路由策略的额外绑定元数据。
    """

    route_key: RouteKey
    driver_id: str
    driver_kind: DriverKind
    mode: RouteMode = RouteMode.ACTIVE
    priority: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """规范化并校验绑定字段。

        Raises:
            ValueError: 当 ``driver_id`` 规范化后为空时抛出。
        """
        driver_id = str(self.driver_id).strip()
        if not driver_id:
            raise ValueError("RouteBinding.driver_id 不能为空")
        object.__setattr__(self, "driver_id", driver_id)


@dataclass(slots=True)
class InboundMessageEnvelope:
    """封装一次由驱动产出的规范化入站消息。

    Attributes:
        route_key: 该入站消息解析出的路由键。
        driver_id: 产出该消息的驱动 ID。
        driver_kind: 产出该消息的驱动类型。
        external_message_id: 可选的平台侧消息 ID，用于去重。
        dedupe_key: 可选的显式去重键。当外部消息没有稳定 ``message_id`` 时，
            可由上游驱动提供稳定的技术性幂等键。若这里为空，中间层仅会继续
            回退到 ``external_message_id`` 或 ``session_message.message_id``，
            不会再根据 ``payload`` 内容猜测语义去重键。
        session_message: 可选的、已经完成规范化的 ``SessionMessage`` 对象。
        payload: 可选的原始字典载荷，供延迟转换或调试使用。
        metadata: 额外入站元数据，例如连接信息或追踪上下文。
    """

    route_key: RouteKey
    driver_id: str
    driver_kind: DriverKind
    external_message_id: Optional[str] = None
    dedupe_key: Optional[str] = None
    session_message: Optional["SessionMessage"] = None
    payload: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryReceipt:
    """表示一次出站投递尝试的统一结果。

    Attributes:
        internal_message_id: Broker 跟踪的内部 ``SessionMessage.message_id``。
        route_key: 本次投递使用的路由键。
        status: 规范化后的投递状态。
        driver_id: 实际处理该投递的驱动 ID，可为空。
        driver_kind: 实际处理该投递的驱动类型，可为空。
        external_message_id: 驱动或适配器返回的平台侧消息 ID，可为空。
        error: 投递失败时的错误信息，可为空。
        metadata: 预留给回执、时间戳或平台特有信息的额外元数据。
    """

    internal_message_id: str
    route_key: RouteKey
    status: DeliveryStatus
    driver_id: Optional[str] = None
    driver_kind: Optional[DriverKind] = None
    external_message_id: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
