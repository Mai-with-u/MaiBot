"""提供 Platform IO 的插件适配器驱动实现。"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol

from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.types import DeliveryReceipt, DeliveryStatus, DriverDescriptor, DriverKind, RouteKey

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage


class _AdapterSupervisorProtocol(Protocol):
    """适配器驱动依赖的 Supervisor 最小协议。"""

    async def invoke_adapter(
        self,
        plugin_id: str,
        method_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Any:
        """调用适配器插件专用方法。"""


class PluginPlatformDriver(PlatformIODriver):
    """面向适配器插件链路的 Platform IO 驱动。"""

    def __init__(
        self,
        driver_id: str,
        platform: str,
        supervisor: _AdapterSupervisorProtocol,
        send_method: str = "send_to_platform",
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
        plugin_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """初始化一个插件适配器驱动。

        Args:
            driver_id: Broker 内的唯一驱动 ID。
            platform: 该适配器负责的平台名称。
            supervisor: 持有该适配器插件的 Supervisor。
            send_method: 出站发送时要调用的插件方法名。
            account_id: 可选的账号 ID 或 self ID。
            scope: 可选的额外路由作用域。
            plugin_id: 拥有该适配器实现的插件 ID。
            metadata: 可选的额外驱动元数据。
        """
        descriptor = DriverDescriptor(
            driver_id=driver_id,
            kind=DriverKind.PLUGIN,
            platform=platform,
            account_id=account_id,
            scope=scope,
            plugin_id=plugin_id,
            metadata=metadata or {},
        )
        super().__init__(descriptor)
        self._supervisor = supervisor
        self._send_method = send_method

    async def send_message(
        self,
        message: "SessionMessage",
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """通过适配器插件发送消息。

        Args:
            message: 要投递的内部会话消息。
            route_key: Broker 为本次投递选择的路由键。
            metadata: 本次出站投递可选的 Broker 侧元数据。

        Returns:
            DeliveryReceipt: 由驱动返回的规范化回执。
        """
        from src.plugin_runtime.host.message_utils import PluginMessageUtils

        plugin_id = self.descriptor.plugin_id or ""
        if not plugin_id:
            return DeliveryReceipt(
                internal_message_id=message.message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=self.driver_id,
                driver_kind=self.descriptor.kind,
                error="插件适配器驱动缺少 plugin_id",
            )

        try:
            message_dict = PluginMessageUtils._session_message_to_dict(message)
            response = await self._supervisor.invoke_adapter(
                plugin_id=plugin_id,
                method_name=self._send_method,
                args={
                    "message": message_dict,
                    "route": {
                        "platform": route_key.platform,
                        "account_id": route_key.account_id,
                        "scope": route_key.scope,
                    },
                    "metadata": metadata or {},
                },
                timeout_ms=30000,
            )
        except Exception as exc:
            return DeliveryReceipt(
                internal_message_id=message.message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=self.driver_id,
                driver_kind=self.descriptor.kind,
                error=str(exc),
            )

        return self._build_receipt(message.message_id, route_key, response)

    def _build_receipt(self, internal_message_id: str, route_key: RouteKey, response: Any) -> DeliveryReceipt:
        """将适配器调用响应归一化为出站回执。

        Args:
            internal_message_id: 内部消息 ID。
            route_key: 本次投递的路由键。
            response: Supervisor 返回的 RPC 响应对象。

        Returns:
            DeliveryReceipt: 标准化后的出站回执。
        """
        if getattr(response, "error", None):
            error = response.error.get("message", "适配器发送失败")
            return DeliveryReceipt(
                internal_message_id=internal_message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=self.driver_id,
                driver_kind=self.descriptor.kind,
                error=error,
            )

        payload = getattr(response, "payload", {})
        invoke_success = bool(payload.get("success", False)) if isinstance(payload, dict) else False
        if not invoke_success:
            return DeliveryReceipt(
                internal_message_id=internal_message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=self.driver_id,
                driver_kind=self.descriptor.kind,
                error=str(payload.get("result", "适配器发送失败")) if isinstance(payload, dict) else "适配器发送失败",
            )

        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            if result.get("success") is False:
                return DeliveryReceipt(
                    internal_message_id=internal_message_id,
                    route_key=route_key,
                    status=DeliveryStatus.FAILED,
                    driver_id=self.driver_id,
                    driver_kind=self.descriptor.kind,
                    error=str(result.get("error", "适配器发送失败")),
                    metadata=result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {},
                )
            external_message_id = str(result.get("external_message_id") or result.get("message_id") or "") or None
            return DeliveryReceipt(
                internal_message_id=internal_message_id,
                route_key=route_key,
                status=DeliveryStatus.SENT,
                driver_id=self.driver_id,
                driver_kind=self.descriptor.kind,
                external_message_id=external_message_id,
                metadata=result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {},
            )

        if isinstance(result, str) and result.strip():
            return DeliveryReceipt(
                internal_message_id=internal_message_id,
                route_key=route_key,
                status=DeliveryStatus.SENT,
                driver_id=self.driver_id,
                driver_kind=self.descriptor.kind,
                external_message_id=result.strip(),
            )

        return DeliveryReceipt(
            internal_message_id=internal_message_id,
            route_key=route_key,
            status=DeliveryStatus.SENT,
            driver_id=self.driver_id,
            driver_kind=self.descriptor.kind,
        )
