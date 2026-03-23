"""发送服务回归测试。"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.chat.message_receive.chat_manager import BotChatSession
from src.services import send_service


class _FakePlatformIOManager:
    """用于测试的 Platform IO 管理器假对象。"""

    def __init__(self, delivery_batch: Any) -> None:
        """初始化假 Platform IO 管理器。

        Args:
            delivery_batch: 发送时返回的批量回执。
        """
        self._delivery_batch = delivery_batch
        self.ensure_calls = 0
        self.sent_messages: List[Dict[str, Any]] = []

    async def ensure_send_pipeline_ready(self) -> None:
        """记录发送管线准备调用次数。"""
        self.ensure_calls += 1

    def build_route_key_from_message(self, message: Any) -> Any:
        """根据消息构造假的路由键。

        Args:
            message: 待发送的内部消息对象。

        Returns:
            Any: 简化后的路由键对象。
        """
        del message
        return SimpleNamespace(platform="qq")

    async def send_message(self, message: Any, route_key: Any, metadata: Dict[str, Any]) -> Any:
        """记录发送请求并返回预设回执。

        Args:
            message: 待发送的内部消息对象。
            route_key: 本次发送使用的路由键。
            metadata: 发送元数据。

        Returns:
            Any: 预设的批量发送回执。
        """
        self.sent_messages.append(
            {
                "message": message,
                "route_key": route_key,
                "metadata": metadata,
            }
        )
        return self._delivery_batch


def _build_target_stream() -> BotChatSession:
    """构造一个最小可用的目标会话对象。

    Returns:
        BotChatSession: 测试用会话对象。
    """
    return BotChatSession(
        session_id="test-session",
        platform="qq",
        user_id="target-user",
        group_id=None,
    )


@pytest.mark.asyncio
async def test_text_to_stream_delegates_to_platform_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """send service 应将发送职责统一交给 Platform IO。"""
    fake_manager = _FakePlatformIOManager(
        delivery_batch=SimpleNamespace(
            has_success=True,
            sent_receipts=[SimpleNamespace(driver_id="plugin.qq.sender")],
            failed_receipts=[],
            route_key=SimpleNamespace(platform="qq"),
        )
    )
    stored_messages: List[Any] = []

    monkeypatch.setattr(send_service, "get_platform_io_manager", lambda: fake_manager)
    monkeypatch.setattr(send_service, "get_bot_account", lambda platform: "bot-qq")
    monkeypatch.setattr(
        send_service._chat_manager,
        "get_session_by_session_id",
        lambda stream_id: _build_target_stream() if stream_id == "test-session" else None,
    )
    monkeypatch.setattr(
        send_service.MessageUtils,
        "store_message_to_db",
        lambda message: stored_messages.append(message),
    )

    result = await send_service.text_to_stream(text="你好", stream_id="test-session")

    assert result is True
    assert fake_manager.ensure_calls == 1
    assert len(fake_manager.sent_messages) == 1
    assert fake_manager.sent_messages[0]["metadata"] == {"show_log": False}
    assert len(stored_messages) == 1


@pytest.mark.asyncio
async def test_text_to_stream_returns_false_when_platform_io_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Platform IO 批量发送全部失败时，应直接向上返回失败。"""
    fake_manager = _FakePlatformIOManager(
        delivery_batch=SimpleNamespace(
            has_success=False,
            sent_receipts=[],
            failed_receipts=[
                SimpleNamespace(
                    driver_id="plugin.qq.sender",
                    status="failed",
                    error="network error",
                )
            ],
            route_key=SimpleNamespace(platform="qq"),
        )
    )

    monkeypatch.setattr(send_service, "get_platform_io_manager", lambda: fake_manager)
    monkeypatch.setattr(send_service, "get_bot_account", lambda platform: "bot-qq")
    monkeypatch.setattr(
        send_service._chat_manager,
        "get_session_by_session_id",
        lambda stream_id: _build_target_stream() if stream_id == "test-session" else None,
    )

    result = await send_service.text_to_stream(text="发送失败", stream_id="test-session")

    assert result is False
    assert fake_manager.ensure_calls == 1
    assert len(fake_manager.sent_messages) == 1
