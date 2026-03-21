"""提供 Platform IO 层的中心 Broker 管理器。"""

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from src.common.logger import get_logger
from src.platform_io.drivers.base import PlatformIODriver

from .dedupe import MessageDeduplicator
from .outbound_tracker import OutboundTracker
from .route_key_factory import RouteKeyFactory
from .registry import DriverRegistry
from .routing import RouteTable
from .types import DeliveryReceipt, DeliveryStatus, InboundMessageEnvelope, RouteBinding, RouteKey

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

logger = get_logger("platform_io.manager")

InboundDispatcher = Callable[[InboundMessageEnvelope], Awaitable[None]]


class PlatformIOManager:
    """统一协调双路径平台消息 IO 的路由、去重与状态跟踪。

    这个管理器预期会成为 legacy 适配器链路与 plugin 适配器链路之间的
    唯一裁决点。当前地基阶段，它只提供共享状态和 Broker 侧契约，还没有
    真正把生产流量切到新中间层。
    """

    def __init__(self) -> None:
        """初始化 Broker 管理器及其内存状态。"""
        self._driver_registry = DriverRegistry()
        self._route_table = RouteTable()
        self._deduplicator = MessageDeduplicator()
        self._outbound_tracker = OutboundTracker()
        self._inbound_dispatcher: Optional[InboundDispatcher] = None
        self._started = False

    @property
    def is_started(self) -> bool:
        """返回 Broker 当前是否已进入运行态。

        Returns:
            bool: 若 Broker 已启动则返回 ``True``。
        """
        return self._started

    async def start(self) -> None:
        """启动 Broker，并依次启动当前已注册的全部驱动。

        Raises:
            Exception: 当某个驱动启动失败时，异常会继续上抛；已成功启动的驱动
                会被自动回滚停止。
        """
        if self._started:
            return

        started_drivers: List[PlatformIODriver] = []
        try:
            for driver in self._driver_registry.list():
                await driver.start()
                started_drivers.append(driver)
        except Exception:
            for driver in reversed(started_drivers):
                try:
                    await driver.stop()
                except Exception:
                    logger.exception("回滚驱动停止失败: driver_id=%s", driver.driver_id)
            raise

        self._started = True

    async def stop(self) -> None:
        """停止 Broker，并按逆序停止全部已注册驱动。

        停止完成后，会同步清空仅对当前运行周期有效的去重缓存和出站跟踪状态，
        避免下一次启动时继续沿用上一个运行周期的瞬时内存数据。

        Raises:
            RuntimeError: 当一个或多个驱动停止失败时抛出汇总异常。
        """
        if not self._started:
            return

        stop_errors: List[str] = []
        for driver in reversed(self._driver_registry.list()):
            try:
                await driver.stop()
            except Exception as exc:
                stop_errors.append(f"{driver.driver_id}: {exc}")
                logger.exception("驱动停止失败: driver_id=%s", driver.driver_id)

        self._started = False
        self._deduplicator.clear()
        self._outbound_tracker.clear()
        if stop_errors:
            raise RuntimeError(f"部分驱动停止失败: {'; '.join(stop_errors)}")

    async def add_driver(self, driver: PlatformIODriver) -> None:
        """向运行中的 Broker 注册并启动一个驱动。

        如果 Broker 尚未启动，则该方法等价于 ``register_driver()``。

        Args:
            driver: 要添加的驱动实例。

        Raises:
            Exception: 当驱动启动失败时，注册会自动回滚，异常继续上抛。
        """
        self._register_driver_internal(driver)
        if not self._started:
            return

        try:
            await driver.start()
        except Exception:
            self._unregister_driver_internal(driver.driver_id)
            raise

    async def remove_driver(self, driver_id: str) -> Optional[PlatformIODriver]:
        """从运行中的 Broker 停止并移除一个驱动。

        如果 Broker 尚未启动，则该方法等价于 ``unregister_driver()``。

        Args:
            driver_id: 要移除的驱动 ID。

        Returns:
            Optional[PlatformIODriver]: 若驱动存在，则返回被移除的驱动实例。

        Raises:
            Exception: 当 Broker 运行中且驱动停止失败时，异常会继续上抛。
        """
        if not self._started:
            return self.unregister_driver(driver_id)

        driver = self._driver_registry.get(driver_id)
        if driver is None:
            return None

        await driver.stop()
        return self._unregister_driver_internal(driver_id)

    @property
    def driver_registry(self) -> DriverRegistry:
        """返回管理器持有的驱动注册表。

        Returns:
            DriverRegistry: 用于保存全部已注册驱动的注册表。
        """
        return self._driver_registry

    @property
    def route_table(self) -> RouteTable:
        """返回管理器持有的路由绑定表。

        Returns:
            RouteTable: 用于归属解析的路由绑定表。
        """
        return self._route_table

    @property
    def deduplicator(self) -> MessageDeduplicator:
        """返回管理器持有的入站去重器。

        Returns:
            MessageDeduplicator: 用于抑制重复入站的去重器。
        """
        return self._deduplicator

    @property
    def outbound_tracker(self) -> OutboundTracker:
        """返回管理器持有的出站跟踪器。

        Returns:
            OutboundTracker: 用于记录出站 pending 状态与回执的跟踪器。
        """
        return self._outbound_tracker

    def set_inbound_dispatcher(self, dispatcher: InboundDispatcher) -> None:
        """设置统一的入站分发回调。

        Args:
            dispatcher: 接收已通过 Broker 审核的入站封装，并继续送入
                Core 下一处理阶段的异步回调。
        """

        self._inbound_dispatcher = dispatcher

    def clear_inbound_dispatcher(self) -> None:
        """清除当前的入站分发回调。"""
        self._inbound_dispatcher = None

    @property
    def has_inbound_dispatcher(self) -> bool:
        """返回当前是否已经配置入站分发回调。

        Returns:
            bool: 若已经配置入站分发回调则返回 ``True``。
        """
        return self._inbound_dispatcher is not None

    def register_driver(self, driver: PlatformIODriver) -> None:
        """注册驱动，并把它的入站回调挂到 Broker。

        Args:
            driver: 要注册的驱动实例。

        Raises:
            RuntimeError: 当 Broker 已经处于运行态时抛出。此时应改用
                ``add_driver()`` 以保证驱动生命周期和注册状态一致。
        """
        if self._started:
            raise RuntimeError("Broker 运行中不允许直接 register_driver，请改用 add_driver()")

        self._register_driver_internal(driver)

    def _register_driver_internal(self, driver: PlatformIODriver) -> None:
        """执行不带运行态限制的内部驱动注册。

        Args:
            driver: 要注册的驱动实例。
        """
        driver.set_inbound_handler(self.accept_inbound)
        self._driver_registry.register(driver)

    def unregister_driver(self, driver_id: str) -> Optional[PlatformIODriver]:
        """从 Broker 注销一个驱动。

        Args:
            driver_id: 要移除的驱动 ID。

        Returns:
            Optional[PlatformIODriver]: 若驱动存在，则返回被移除的驱动实例。

        Raises:
            RuntimeError: 当 Broker 已经处于运行态时抛出。此时应改用
                ``remove_driver()``，避免驱动停止与路由解绑脱节。
        """
        if self._started:
            raise RuntimeError("Broker 运行中不允许直接 unregister_driver，请改用 remove_driver()")

        return self._unregister_driver_internal(driver_id)

    def _unregister_driver_internal(self, driver_id: str) -> Optional[PlatformIODriver]:
        """执行不带运行态限制的内部驱动注销。

        Args:
            driver_id: 要移除的驱动 ID。

        Returns:
            Optional[PlatformIODriver]: 若驱动存在，则返回被移除的驱动实例。
        """
        removed_driver = self._driver_registry.unregister(driver_id)
        if removed_driver is None:
            return None

        removed_driver.clear_inbound_handler()
        self._route_table.remove_bindings_by_driver(driver_id)
        return removed_driver

    def bind_route(self, binding: RouteBinding, *, replace: bool = False) -> None:
        """为某个路由键绑定驱动。

        Args:
            binding: 要保存的路由绑定。
            replace: 是否允许替换已有的精确 active owner。

        Raises:
            ValueError: 当绑定引用了不存在的驱动，或者绑定与驱动描述不一致时抛出。
        """
        driver = self._driver_registry.get(binding.driver_id)
        if driver is None:
            raise ValueError(f"驱动 {binding.driver_id} 未注册，无法绑定路由")

        self._validate_binding_against_driver(binding, driver)
        self._route_table.bind(binding, replace=replace)

    def unbind_route(self, route_key: RouteKey, driver_id: Optional[str] = None) -> None:
        """移除一个或多个路由绑定。

        Args:
            route_key: 要移除绑定的路由键。
            driver_id: 可选的特定驱动 ID。
        """
        self._route_table.unbind(route_key, driver_id)

    def resolve_driver(self, route_key: RouteKey) -> Optional[PlatformIODriver]:
        """解析某个路由键当前的 active 驱动。

        Args:
            route_key: 要解析的路由键。

        Returns:
            Optional[PlatformIODriver]: 若存在 active 驱动，则返回该驱动实例。
        """
        active_binding = self._route_table.get_active_binding(route_key)
        if active_binding is None:
            return None
        return self._driver_registry.get(active_binding.driver_id)

    @staticmethod
    def build_route_key_from_message(message: "SessionMessage") -> RouteKey:
        """根据 ``SessionMessage`` 构造路由键。

        Args:
            message: 内部会话消息对象。

        Returns:
            RouteKey: 由消息内容提取出的规范化路由键。
        """
        return RouteKeyFactory.from_session_message(message)

    @staticmethod
    def build_route_key_from_message_dict(message_dict: Dict[str, Any]) -> RouteKey:
        """根据消息字典构造路由键。

        Args:
            message_dict: Host 与插件之间传输的消息字典。

        Returns:
            RouteKey: 由消息字典提取出的规范化路由键。
        """
        return RouteKeyFactory.from_message_dict(message_dict)

    async def accept_inbound(self, envelope: InboundMessageEnvelope) -> bool:
        """处理一条由驱动上报的入站封装。

        Args:
            envelope: 由传输驱动产出的入站封装。

        Returns:
            bool: 若消息被接受并继续转发给入站分发器，则返回 ``True``，
            否则返回 ``False``。
        """

        if not self._route_table.accepts_inbound(envelope.route_key, envelope.driver_id):
            logger.info(
                "忽略非 active owner 的入站消息: route=%s driver=%s",
                envelope.route_key,
                envelope.driver_id,
            )
            return False

        if self._inbound_dispatcher is None:
            logger.debug("PlatformIOManager 尚未配置 inbound dispatcher，暂不继续分发")
            return False

        dedupe_key = self._build_inbound_dedupe_key(envelope)
        if dedupe_key is not None:
            if not self._deduplicator.mark_seen(dedupe_key):
                logger.info("忽略重复入站消息: dedupe_key=%s", dedupe_key)
                return False

        await self._inbound_dispatcher(envelope)
        return True

    async def send_message(
        self,
        message: "SessionMessage",
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """通过 Broker 选中的驱动发送一条消息。

        Args:
            message: 要投递的内部会话消息。
            route_key: 本次出站投递选择的路由键。
            metadata: 可选的额外 Broker 侧元数据。

        Returns:
            DeliveryReceipt: 规范化后的出站回执。若路由不存在、驱动缺失，
            或同一消息已存在未完成的出站跟踪，也会返回失败回执而不是抛异常。
        """

        active_binding = self._route_table.get_active_binding(route_key)
        if active_binding is None:
            return DeliveryReceipt(
                internal_message_id=message.message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                error="未找到 active 路由绑定",
            )

        driver = self._driver_registry.get(active_binding.driver_id)
        if driver is None:
            return DeliveryReceipt(
                internal_message_id=message.message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=active_binding.driver_id,
                driver_kind=active_binding.driver_kind,
                error="active 路由绑定对应的驱动不存在",
            )

        try:
            self._outbound_tracker.begin_tracking(
                internal_message_id=message.message_id,
                route_key=route_key,
                driver_id=driver.driver_id,
                metadata=metadata,
            )
        except ValueError as exc:
            return DeliveryReceipt(
                internal_message_id=message.message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=driver.driver_id,
                driver_kind=driver.descriptor.kind,
                error=str(exc),
            )

        try:
            receipt = await driver.send_message(message=message, route_key=route_key, metadata=metadata)
        except Exception as exc:
            receipt = DeliveryReceipt(
                internal_message_id=message.message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=driver.driver_id,
                driver_kind=driver.descriptor.kind,
                error=str(exc),
            )

        self._outbound_tracker.finish_tracking(receipt)
        return receipt

    @staticmethod
    def _build_inbound_dedupe_key(envelope: InboundMessageEnvelope) -> Optional[str]:
        """构造用于入站抑制的去重键。

        Args:
            envelope: 当前正在处理的入站封装。

        Returns:
            Optional[str]: 若可以构造稳定去重键则返回该键，否则返回 ``None``。

        Notes:
            这里仅接受上游显式提供的稳定消息身份，例如 ``dedupe_key``、
            平台侧 ``external_message_id`` 或已经完成规范化的
            ``session_message.message_id``。Broker 不再根据 ``payload`` 内容
            猜测语义去重键，避免把“短时间内两条内容刚好完全相同”的合法消息
            误判为重复入站。
        """
        raw_dedupe_key = envelope.dedupe_key or envelope.external_message_id
        if raw_dedupe_key is None and envelope.session_message is not None:
            raw_dedupe_key = envelope.session_message.message_id
        if raw_dedupe_key is None:
            return None

        normalized_dedupe_key = str(raw_dedupe_key).strip()
        if not normalized_dedupe_key:
            return None

        return f"{envelope.route_key.to_dedupe_scope()}:{normalized_dedupe_key}"

    @staticmethod
    def _validate_binding_against_driver(binding: RouteBinding, driver: PlatformIODriver) -> None:
        """校验路由绑定与驱动描述是否一致。

        Args:
            binding: 待校验的路由绑定。
            driver: 被绑定的驱动实例。

        Raises:
            ValueError: 当绑定类型、平台或更细粒度路由维度与驱动描述冲突时抛出。
        """
        descriptor = driver.descriptor
        if binding.driver_kind != descriptor.kind:
            raise ValueError(
                f"路由绑定的 driver_kind={binding.driver_kind} 与驱动 {driver.driver_id} 的类型 "
                f"{descriptor.kind} 不一致"
            )

        if binding.route_key.platform != descriptor.platform:
            raise ValueError(
                f"路由绑定的平台 {binding.route_key.platform} 与驱动 {driver.driver_id} 的平台 "
                f"{descriptor.platform} 不一致"
            )

        if descriptor.account_id is not None and binding.route_key.account_id not in (None, descriptor.account_id):
            raise ValueError(
                f"路由绑定的 account_id={binding.route_key.account_id} 与驱动 {driver.driver_id} 的 "
                f"account_id={descriptor.account_id} 冲突"
            )

        if descriptor.scope is not None and binding.route_key.scope not in (None, descriptor.scope):
            raise ValueError(
                f"路由绑定的 scope={binding.route_key.scope} 与驱动 {driver.driver_id} 的 "
                f"scope={descriptor.scope} 冲突"
            )


_platform_io_manager: Optional[PlatformIOManager] = None


def get_platform_io_manager() -> PlatformIOManager:
    """返回全局 ``PlatformIOManager`` 单例。

    Returns:
        PlatformIOManager: 进程级共享的 Broker 管理器实例。
    """

    global _platform_io_manager
    if _platform_io_manager is None:
        _platform_io_manager = PlatformIOManager()
    return _platform_io_manager
