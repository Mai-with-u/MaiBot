from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image

import asyncio
import json
import sys

import pytest

from src.agent import messaging, session as session_module
from src.common.data_models.message_component_data_model import ImageComponent
from src.agent.config import AgentConfig
from src.computer.bridge import SandboxBridge
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolInvocation
from src.agent import service as service_module
from src.agent.acp_agent import AcpAgent
from src.agent.provider import AgentToolProvider
from src.agent.service import AgentService


@pytest.fixture
def service(tmp_path, monkeypatch):
    config = AgentConfig(enabled=True, connection="custom", acp_command=sys.executable,
                                acp_args=[str(Path(__file__).with_name("fake_acp.py"))], acp_timeout=5,
                                acp_env={"ACP_TEST_LOG": str(tmp_path / "protocol.jsonl")})
    settings = SimpleNamespace(agent=config,
                               bot=SimpleNamespace(nickname="麦麦"), personality=SimpleNamespace(personality="探索者"))
    for module in [service_module, session_module]:
        monkeypatch.setattr(module, "global_config", settings)
    bridge = SandboxBridge(tmp_path / "runtime", tmp_path / "computer" / "workspace", config)
    monkeypatch.setattr(bridge, "restore", AsyncMock(return_value=False))
    return AgentService(tmp_path / "agent", bridge)


def image_bytes():
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def test_failed_resume_preserves_error_and_never_closes_unowned_session(service):
    async def scenario():
        service.config.acp_env["ACP_TEST_REJECT_RESUME"] = "1"
        agent = AcpAgent(service)
        service.agent = agent
        await agent.load()
        agent.backend_state = {"session_id": "missing-session"}
        await agent.enqueue("keep this queued")
        with pytest.raises(RuntimeError, match="original resume failure"):
            await agent.run_turn()
        assert len(agent.inbox) == 1 and agent.pending is None
        assert agent.connection is None and agent.mcp is None
        log = [json.loads(line) for line in (service.root.parent / "protocol.jsonl").read_text().splitlines()]
        assert [row["method"] for row in log] == ["initialize", "session/resume"]
        await service.stop()

    asyncio.run(scenario())


def test_desktop_state_is_scoped_to_host_manifest(service, tmp_path):
    service.config.connection = "dsh_desktop"
    service.config.desktop_manifest = str(tmp_path / "test-host.json")
    test_host = AcpAgent(service)
    service.config.desktop_manifest = str(tmp_path / "desktop-host.json")
    desktop_host = AcpAgent(service)
    assert test_host.state_key != desktop_host.state_key
    assert desktop_host.state_key == AcpAgent(service).state_key


def test_real_acp_tools_images_and_resume_without_computer(service, monkeypatch):
    async def scenario():
        assert service.bridge.sandbox_id is None
        tell = AsyncMock(return_value={"delivered": True, "queued": True})
        monkeypatch.setattr(messaging, "tell", tell)
        agent = AcpAgent(service)
        service.agent = agent
        await agent.load()
        await agent.enqueue("TOOLS", [ImageComponent(binary_hash="", binary_data=image_bytes())])
        try:
            await agent.run_turn()
            assert not agent.inbox and agent.pending is None
            tell.assert_awaited_once_with(service, service.agent_id, "result", "chat", ["result.png"])
            assert "external reply" in str(agent.history.events())
            session_id = agent.backend_state["session_id"]
            await service.stop()
            restored = AcpAgent(service)
            service.agent = restored
            await restored.load()
            await restored.enqueue("RESUME")
            await restored.run_turn()
            assert restored.backend_state["session_id"] == session_id
            log = [json.loads(line) for line in (service.root.parent / "protocol.jsonl").read_text().splitlines()]
            assert sum(item["method"] == "session/new" for item in log) == 1
            assert any(item["method"] == "session/resume" for item in log)
            assert any(item.get("has_image") for item in log)
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_computer_tools_are_optional_and_controls_never_exposed_to_chat(service):
    async def scenario():
        provider = AgentToolProvider(service)
        ordinary = ToolAvailabilityContext(session_id="chat")
        trusted = ToolAvailabilityContext(session_id=service.agent_id)
        assert {tool.name for tool in await provider.list_tools(ordinary)} == {"agent_talk", "agent_status"}
        assert len(await provider.list_tools(trusted)) == 4
        service.config.computer_tools = True
        tools = {tool.name for tool in await provider.list_tools(trusted)}
        assert len(tools) == 8 and "computer_observe" in tools
        assert "computer_control" not in tools and "computer_talk" not in tools
        assert not {"agent_rest", "agent_note"} & tools
        service.agent = AcpAgent(service)
        result = await provider.invoke(ToolInvocation("computer_observe", {}), ToolExecutionContext(session_id="chat"))
        assert not result.success
    asyncio.run(scenario())


def test_pause_cancels_external_prompt_without_touching_computer(service):
    async def scenario():
        await service._reload()
        agent = service.require_agent()
        await agent.enqueue("WAIT")
        for _ in range(100):
            if agent.state == "thinking":
                break
            await asyncio.sleep(0.02)
        try:
            assert agent.state == "thinking"
            await service.control("pause")
            assert service.paused and agent.pending is None
            await agent.enqueue("queued while paused")
            assert agent.inbox
            await service.control("resume")
            for _ in range(100):
                if not agent.inbox and agent.pending is None:
                    break
                await asyncio.sleep(0.02)
            assert not agent.inbox and not service.paused
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_external_images_relay_to_planner_without_sandbox(service, monkeypatch):
    from src.chat.heart_flow.heartflow_manager import heartflow_manager

    async def scenario():
        agent = AcpAgent(service)
        service.agent = agent
        await agent.load()
        pixels = image_bytes()
        image = ImageComponent(binary_hash="", binary_data=pixels)
        monkeypatch.setattr(messaging, "resolve_chat", Mock())
        monkeypatch.setattr(messaging.chat_manager, "get_session_name", Mock(return_value="测试聊天"))
        monkeypatch.setattr(messaging, "source_images", AsyncMock(return_value={"message": [image]}))
        runtime = SimpleNamespace(enqueue_internal_task=Mock(return_value={"queued": True}))
        monkeypatch.setattr(heartflow_manager, "get_or_create_heartflow_chat", AsyncMock(return_value=runtime))
        inbound = await messaging.tell(service, "chat", "input", images=["message"])
        assert inbound["queued"] and agent.inbox
        path = inbound["images"][0]["path"]
        assert Path(path).read_bytes() == pixels
        outbound = await messaging.tell(service, service.agent_id, "result", "chat", [path])
        assert outbound["queued"]
        call = runtime.enqueue_internal_task.call_args.kwargs
        assert call["source_kind"] == "agent_relay" and call["images"][0].binary_data == pixels
    asyncio.run(scenario())


def test_host_switch_shares_queue_history_but_keeps_execution_state_separate(service, tmp_path):
    async def scenario():
        service.paused = True
        await service._reload()
        acp = service.require_agent()
        old_task = service._task
        acp.backend_state = {"session_id": "saved-acp-session"}
        await acp.enqueue("shared task")
        await acp.history.append(await acp.record("step", []))
        service.config.connection = "dsh_desktop"
        service.config.desktop_manifest = str(tmp_path / "desktop.json")
        await service._reload()
        desktop = service.require_agent()
        try:
            assert old_task.done() and isinstance(desktop, AcpAgent)
            assert len(desktop.inbox) == 1 and len(desktop.history.records) == 1
            assert desktop.backend_state == {} and desktop.pending is None
            desktop.pending = {"input_ids": ["uncertain-desktop-input"]}
            service.config.connection = "custom"
            await service._reload()
            restored = service.require_agent()
            assert isinstance(restored, AcpAgent)
            assert restored.pending is None and len(restored.inbox) == 1
            assert restored.backend_state["session_id"] == "saved-acp-session"
            state = json.loads((service.root / "state.json").read_text(encoding="utf-8"))
            assert state["backends"][desktop.state_key]["pending"]["input_ids"] == ["uncertain-desktop-input"]
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_reload_cancels_active_acp_before_replacing_connection(service):
    async def scenario():
        await service._reload()
        previous = service.require_agent()
        await previous.enqueue("WAIT")
        for _ in range(150):
            if previous.state == "thinking":
                break
            await asyncio.sleep(0.02)
        try:
            assert previous.state == "thinking"
            previous_task = service._task
            service.config.acp_timeout += 1
            await service._reload()
            assert previous_task.done() and previous.connection is None and previous.pending is None
            assert isinstance(service.require_agent(), AcpAgent) and service.agent is not previous
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_desktop_rejects_empty_actions_without_starting_sandbox(tmp_path, monkeypatch):
    async def scenario():
        bridge = SandboxBridge(tmp_path / "runtime", tmp_path / "workspace", AgentConfig())
        start = AsyncMock()
        monkeypatch.setattr(bridge, "_start", start)
        with pytest.raises(ValueError, match="computer_observe"):
            await bridge.perform([])
        start.assert_not_awaited()
    asyncio.run(scenario())
