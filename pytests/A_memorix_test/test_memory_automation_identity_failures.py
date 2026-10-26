"""身份登记不能阻断独立队列；有限重试后仍需暴露错误。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest

from src.services import memory_flow_service as flow


@pytest.fixture
def automation(monkeypatch):
    monkeypatch.setattr(
        flow,
        "global_config",
        SimpleNamespace(a_memorix=SimpleNamespace(plugin=SimpleNamespace(enabled=True))),
    )
    monkeypatch.setattr(flow.asyncio, "sleep", AsyncMock())
    service = flow.MemoryAutomationService.__new__(flow.MemoryAutomationService)
    service._started = True
    service.image_writeback = SimpleNamespace(enqueue=AsyncMock())
    service.fact_writeback = SimpleNamespace(enqueue=AsyncMock())
    service.chat_summary_writeback = SimpleNamespace(enqueue=AsyncMock())
    message = SimpleNamespace(
        platform="qq",
        message_info=SimpleNamespace(
            user_info=SimpleNamespace(user_id="123", user_nickname="原昵称", user_cardname="群昵称"),
            group_info=None,
        ),
    )
    return service, message


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["incoming", "sent"])
@pytest.mark.parametrize("failure", ["result", "exception"])
async def test_identity_failure_still_enqueues_and_reports_error(automation, monkeypatch, direction, failure):
    service, message = automation
    admin = AsyncMock(return_value={"success": False, "reason": "kernel_not_ready"})
    if failure == "exception":
        admin.side_effect = RuntimeError("kernel_not_ready")
    monkeypatch.setattr(flow.memory_service, "profile_admin", admin)
    callback = service.on_incoming_message if direction == "incoming" else service.on_message_sent
    with pytest.raises(ExceptionGroup) as caught:
        await callback(message)
    assert "kernel_not_ready" in str(caught.value.exceptions[0])
    assert admin.await_count == 3
    assert service.image_writeback.enqueue.await_count == (direction == "incoming")
    assert service.fact_writeback.enqueue.await_count == (direction == "sent")
    assert service.chat_summary_writeback.enqueue.await_count == (direction == "sent")


@pytest.mark.asyncio
async def test_transient_identity_failure_retries_same_snapshot_without_reenqueuing(automation, monkeypatch):
    service, message = automation
    calls = []

    async def observe(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            message.message_info.user_info.user_nickname = "后来的昵称"
            return {"success": False, "message": "initializing"}
        return {"success": True}

    monkeypatch.setattr(flow.memory_service, "profile_admin", observe)
    await service.on_message_sent(message)
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[1]["aliases"] == ["原昵称", "群昵称"]
    service.fact_writeback.enqueue.assert_awaited_once_with(message)
    service.chat_summary_writeback.enqueue.assert_awaited_once_with(message)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["platform", "user_id"])
async def test_identity_rejects_missing_real_id_without_calling_admin(automation, monkeypatch, field):
    _, message = automation
    if field == "platform":
        message.platform = ""
    else:
        message.message_info.user_info.user_id = ""
    admin = AsyncMock()
    monkeypatch.setattr(flow.memory_service, "profile_admin", admin)
    with pytest.raises(ValueError, match="platform/user_id"):
        await flow.MemoryAutomationService._observe_sender_identity(message)
    admin.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_failure_does_not_prevent_other_tasks_and_stays_visible(automation, monkeypatch):
    service, message = automation
    admin = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(flow.memory_service, "profile_admin", admin)
    service.fact_writeback.enqueue.side_effect = RuntimeError("person queue failed")
    with pytest.raises(ExceptionGroup) as caught:
        await service.on_message_sent(message)
    assert str(caught.value.exceptions[0]) == "person queue failed"
    admin.assert_awaited_once()
    service.chat_summary_writeback.enqueue.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_identity_cancellation_is_propagated_without_retry(automation, monkeypatch):
    service, message = automation
    admin = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(flow.memory_service, "profile_admin", admin)
    with pytest.raises(asyncio.CancelledError):
        await service.on_message_sent(message)
    admin.assert_awaited_once()
