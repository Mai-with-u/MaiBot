"""Platform IO 入站去重策略测试。"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.manager import PlatformIOManager
from src.platform_io.types import DeliveryReceipt, DeliveryStatus, DriverDescriptor, DriverKind, InboundMessageEnvelope, RouteBinding, RouteKey


def _build_envelope(
    *,
    dedupe_key: str | None = None,
    external_message_id: str | None = None,
    session_message_id: str | None = None,
    payload: Optional[Dict[str, Any]] = None,
) -> InboundMessageEnvelope:
    """构造测试用入站信封。

    Args:
        dedupe_key: 显式去重键。
        external_message_id: 平台侧消息 ID。
        session_message_id: 规范化消息对象上的消息 ID。
        payload: 原始载荷。

    Returns:
        InboundMessageEnvelope: 测试用入站消息信封。
    """
    session_message = None
    if session_message_id is not None:
        session_message = SimpleNamespace(message_id=session_message_id)

    return InboundMessageEnvelope(
        route_key=RouteKey(platform="qq", account_id="10001", scope="main"),
        driver_id="plugin.napcat",
        driver_kind=DriverKind.PLUGIN,
        dedupe_key=dedupe_key,
        external_message_id=external_message_id,
        session_message=session_message,
        payload=payload,
    )


class _StubPlatformIODriver(PlatformIODriver):
    """测试用 Platform IO 驱动。"""

    async def send_message(
        self,
        message: Any,
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """返回一个固定的成功回执。

        Args:
            message: 待发送的消息对象。
            route_key: 本次发送使用的路由键。
            metadata: 额外发送元数据。

        Returns:
            DeliveryReceipt: 固定的成功回执。
        """
        return DeliveryReceipt(
            internal_message_id=str(getattr(message, "message_id", "stub-message-id")),
            route_key=route_key,
            status=DeliveryStatus.SENT,
            driver_id=self.driver_id,
            driver_kind=self.descriptor.kind,
        )


def _build_manager() -> PlatformIOManager:
    """构造带有最小 active owner 的 Broker 管理器。

    Returns:
        PlatformIOManager: 已注册测试驱动并绑定活动路由的 Broker。
    """
    manager = PlatformIOManager()
    driver = _StubPlatformIODriver(
        DriverDescriptor(
            driver_id="plugin.napcat",
            kind=DriverKind.PLUGIN,
            platform="qq",
            account_id="10001",
            scope="main",
        )
    )
    manager.register_driver(driver)
    manager.bind_route(
        RouteBinding(
            route_key=RouteKey(platform="qq", account_id="10001", scope="main"),
            driver_id=driver.driver_id,
            driver_kind=driver.descriptor.kind,
        )
    )
    return manager


class TestPlatformIODedupe:
    """Platform IO 去重测试。"""

    @pytest.mark.asyncio
    async def test_accept_inbound_dedupes_by_external_message_id(self) -> None:
        """相同平台消息 ID 的重复入站应被抑制。"""
        manager = _build_manager()
        accepted_envelopes: List[InboundMessageEnvelope] = []

        async def dispatcher(envelope: InboundMessageEnvelope) -> None:
            """记录被成功接收的入站消息。

            Args:
                envelope: 被 Broker 接受的入站消息。
            """
            accepted_envelopes.append(envelope)

        manager.set_inbound_dispatcher(dispatcher)

        first_envelope = _build_envelope(
            external_message_id="msg-1",
            payload={"message": "hello"},
        )
        second_envelope = _build_envelope(
            external_message_id="msg-1",
            payload={"message": "hello"},
        )

        assert await manager.accept_inbound(first_envelope) is True
        assert await manager.accept_inbound(second_envelope) is False
        assert len(accepted_envelopes) == 1

    @pytest.mark.asyncio
    async def test_accept_inbound_without_stable_identity_does_not_guess_duplicate(self) -> None:
        """缺少稳定身份时，不应仅凭 payload 内容猜测重复消息。"""
        manager = _build_manager()
        accepted_envelopes: List[InboundMessageEnvelope] = []

        async def dispatcher(envelope: InboundMessageEnvelope) -> None:
            """记录被成功接收的入站消息。

            Args:
                envelope: 被 Broker 接受的入站消息。
            """
            accepted_envelopes.append(envelope)

        manager.set_inbound_dispatcher(dispatcher)

        first_envelope = _build_envelope(payload={"message": "same-payload"})
        second_envelope = _build_envelope(payload={"message": "same-payload"})

        assert await manager.accept_inbound(first_envelope) is True
        assert await manager.accept_inbound(second_envelope) is True
        assert len(accepted_envelopes) == 2

    def test_build_inbound_dedupe_key_prefers_explicit_identity(self) -> None:
        """去重键应只来自显式或稳定的技术身份。"""
        explicit_envelope = _build_envelope(dedupe_key="dedupe-1", external_message_id="msg-1")
        session_message_envelope = _build_envelope(session_message_id="session-1")
        payload_only_envelope = _build_envelope(payload={"message": "hello"})

        assert PlatformIOManager._build_inbound_dedupe_key(explicit_envelope) == "qq:10001:main:dedupe-1"
        assert PlatformIOManager._build_inbound_dedupe_key(session_message_envelope) == "qq:10001:main:session-1"
        assert PlatformIOManager._build_inbound_dedupe_key(payload_only_envelope) is None
