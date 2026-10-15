from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from watchfiles import Change

import pytest

from src.chat.heart_flow.heartflow_manager import HeartflowManager
from src.config.config import ConfigManager
from src.config.file_watcher import FileChange
from src.platform_io.adapter_policy import AdapterPolicyManager


@pytest.mark.asyncio
async def test_policy_change_closes_blocked_runtimes_and_keeps_allowed(tmp_path: Path, monkeypatch):
    manager = HeartflowManager()
    chats = {}
    for name, platform, is_group in (
        ("blocked_group", "qq", True),
        ("blocked_private", "qq", False),
        ("allowed", "qq", True),
        ("local", "maisaka_cli", False),
        ("webui", "webui", True),
    ):
        chat = SimpleNamespace(
            chat_stream=SimpleNamespace(
                platform=platform,
                account_id="bot",
                scope="main",
                is_group_session=is_group,
                group_id=name,
                user_id=name,
            ),
            stop=AsyncMock(),
        )
        chats[name] = chat
        manager.heartflow_chat_list[name] = chat
        manager._touch_chat(name)
    platform_manager = SimpleNamespace(
        ensure_send_pipeline_ready=AsyncMock(),
        is_target_allowed=AsyncMock(side_effect=lambda route, *, chat_type, target_id: target_id == "allowed"),
    )
    monkeypatch.setattr("src.chat.heart_flow.heartflow_manager.get_platform_io_manager", lambda: platform_manager)
    monkeypatch.setattr("src.chat.heart_flow.heartflow_manager.heartflow_manager", manager)
    policy = AdapterPolicyManager(tmp_path / "adapter_policy.toml")
    monkeypatch.setattr("src.platform_io.adapter_policy.get_adapter_policy_manager", lambda: policy)
    config = ConfigManager()
    config.bot_config_path = tmp_path / "bot_config.toml"
    config.model_config_path = tmp_path / "model_config.toml"

    await config._handle_file_changes([FileChange(Change.modified, policy.policy_path.resolve())])

    assert set(manager.heartflow_chat_list) == {"allowed", "local", "webui"}
    assert set(manager._chat_last_active_at) == {"allowed", "local", "webui"}
    chats["blocked_group"].stop.assert_awaited_once()
    chats["blocked_private"].stop.assert_awaited_once()
    for name in ("allowed", "local", "webui"):
        chats[name].stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_session_cannot_recreate_runtime(monkeypatch):
    manager = HeartflowManager()
    session = SimpleNamespace(
        platform="qq", account_id="bot", scope="main", is_group_session=True, group_id="blocked", user_id=None
    )
    platform_manager = SimpleNamespace(
        ensure_send_pipeline_ready=AsyncMock(), is_target_allowed=AsyncMock(return_value=False)
    )
    constructor = Mock()
    monkeypatch.setattr("src.chat.heart_flow.heartflow_manager.get_platform_io_manager", lambda: platform_manager)
    monkeypatch.setattr(
        "src.chat.heart_flow.heartflow_manager.chat_manager.get_session_by_session_id", lambda session_id: session
    )
    monkeypatch.setattr("src.chat.heart_flow.heartflow_manager.MaisakaHeartFlowChatting", constructor)

    with pytest.raises(PermissionError):
        await manager.get_or_create_heartflow_chat("session")

    constructor.assert_not_called()
    assert not manager.heartflow_chat_list

    platform_manager.is_target_allowed.return_value = True
    constructor.return_value.start = AsyncMock()
    runtime = await manager.get_or_create_heartflow_chat("session")
    assert manager.heartflow_chat_list["session"] is runtime
    runtime.start.assert_awaited_once()
