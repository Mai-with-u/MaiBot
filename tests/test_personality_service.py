from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import sys

import pytest
import tomlkit

from src.config.official_configs import ChatReplyStyleConfig, PersonalityConfig
from src.config.personality_service import persist_switch, switch_profile

def make_config(tmp_path):
    path = tmp_path / "bot_config.toml"
    path.write_text('''# preserve me
[bot]
nickname = "unchanged"
[personality]
personality = "edited current"
behavior_style = "current behavior"
reply_style = "current reply"
multiple_reply_style = ["current alternate"]
multiple_probability = 0.2
selected_profile = "old"
[[personality.profiles]]
name = "old"
personality = "old saved"
behavior_style = "old behavior"
reply_style = "old reply"
group_chat_prompt = "old group"
private_chat_prompts = "old private"
[[personality.profiles]]
name = "new"
personality = "new identity"
behavior_style = "new behavior"
reply_style = "new reply"
multiple_reply_style = ["new alternate"]
multiple_probability = 0.7
group_chat_prompt = "new group"
private_chat_prompts = "new private"
[chat.reply_style]
group_chat_prompt = "current group"
private_chat_prompts = "current private"

''', encoding="utf-8")
    return path


def test_switch_preserves_current_edits_and_other_sections(tmp_path):
    path = make_config(tmp_path)
    assert persist_switch(path, "new")
    data = tomlkit.parse(path.read_text(encoding="utf-8"))
    assert data["bot"]["nickname"] == "unchanged"
    assert path.read_text(encoding="utf-8").startswith("# preserve me")
    assert data["personality"]["personality"] == "new identity"
    assert data["personality"]["multiple_probability"] == 0.7
    assert data["personality"]["profiles"][0]["personality"] == "edited current"
    assert data["chat"]["reply_style"]["group_chat_prompt"] == "new group"
    assert data["chat"]["reply_style"]["private_chat_prompts"] == "new private"
    assert data["personality"]["profiles"][0]["group_chat_prompt"] == "current group"
    assert persist_switch(path, "old")
    assert tomlkit.parse(path.read_text(encoding="utf-8"))["personality"]["personality"] == "edited current"
    restored = tomlkit.parse(path.read_text(encoding="utf-8"))
    assert restored["chat"]["reply_style"]["group_chat_prompt"] == "current group"
    assert restored["chat"]["reply_style"]["private_chat_prompts"] == "current private"


def test_missing_or_same_profile_does_not_write(tmp_path):
    path = make_config(tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="missing"):
        persist_switch(path, "missing")
    assert not persist_switch(path, "old")
    assert path.read_bytes() == original


def test_unnamed_current_profile_is_retained(tmp_path):
    path = make_config(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace('selected_profile = "old"', 'selected_profile = ""'), encoding="utf-8")
    persist_switch(path, "new")
    profiles = tomlkit.parse(path.read_text(encoding="utf-8"))["personality"]["profiles"]
    assert len(profiles) == 3
    assert profiles[-1]["personality"] == "edited current"


def test_reload_failure_is_reported_and_retry_can_succeed(tmp_path, monkeypatch):
    path = make_config(tmp_path)
    manager = SimpleNamespace(bot_config_path=path, reload_config=AsyncMock(return_value=False))
    runtime = SimpleNamespace(personality=PersonalityConfig(), chat=SimpleNamespace(reply_style=ChatReplyStyleConfig()))
    monkeypatch.setitem(sys.modules, "src.config.config", SimpleNamespace(config_manager=manager, global_config=runtime))
    with pytest.raises(RuntimeError, match="热重载失败"):
        asyncio.run(switch_profile("new"))
    assert tomlkit.parse(path.read_text(encoding="utf-8"))["personality"]["selected_profile"] == "new"

    async def reload(**kwargs):
        runtime.personality = PersonalityConfig(**tomlkit.parse(path.read_text(encoding="utf-8"))["personality"].unwrap())
        runtime.chat.reply_style = ChatReplyStyleConfig(**tomlkit.parse(path.read_text(encoding="utf-8"))["chat"]["reply_style"].unwrap())
        return True

    manager.reload_config.side_effect = reload
    result = asyncio.run(switch_profile("new"))
    assert result["changed"] is False
    assert result["current"]["name"] == "new"
    assert result["current"]["personality"] == "new identity"
    assert result["current"]["group_chat_prompt"] == "new group"
    assert result["current"]["private_chat_prompts"] == "new private"
    manager.reload_config.assert_awaited_with(changed_scopes=["bot"], changed_sections=["personality", "chat.reply_style"])


def test_explicit_empty_prompt_clears(tmp_path):
    path = make_config(tmp_path)
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace('group_chat_prompt = "new group"', 'group_chat_prompt = ""'), encoding="utf-8")
    persist_switch(path, "new")
    assert tomlkit.parse(path.read_text(encoding="utf-8"))["chat"]["reply_style"]["group_chat_prompt"] == ""


def test_capability_requires_explicit_permission():
    from src.plugin_runtime.host.authorization import AuthorizationManager
    from src.plugin_runtime.host.capability_service import CapabilityService
    from src.plugin_runtime.protocol.envelope import Envelope, MessageType

    authorization = AuthorizationManager()
    authorization.register_plugin("test", ["personality.list"])
    service = CapabilityService(authorization)
    switch = AsyncMock()
    service.register_capability("personality.switch", switch)
    request = Envelope(
        request_id=1, message_type=MessageType.REQUEST, method="cap.call", plugin_id="test",
        payload={"capability": "personality.switch", "args": {"name": "new"}},
    )
    response = asyncio.run(service.handle_capability_request(request))
    assert "E_CAPABILITY_DENIED" in str(response.model_dump())
    switch.assert_not_awaited()
