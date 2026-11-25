from pathlib import Path
from unittest.mock import AsyncMock

import asyncio

from src.host.config import HostConfig
from src.host.layout import Workspace
from src.host.registry import Registry
from src.host.supervisor import BotSupervisor


def test_stopped_bots_skip_dependency_preparation(tmp_path: Path, capsys) -> None:
    registry = Registry(tmp_path)
    registry.create()
    bot = registry.add_bot("Test Bot", tmp_path, owned=False, initialization="initialized")
    registry.set_desired_state(bot.bot_id, "stopped")
    workspace = Workspace(tmp_path, registry, HostConfig())

    async def run() -> None:
        supervisor = BotSupervisor(workspace, public_base_url="http://localhost:8001")
        prepare = AsyncMock(side_effect=AssertionError("停止状态不应准备依赖"))
        supervisor.prepare_dependencies = prepare
        assert await supervisor.start_desired() == []
        prepare.assert_not_awaited()
        assert (await supervisor.status(bot.bot_id))["status"] == "stopped"

    asyncio.run(run())
    assert registry.get_bot(bot.bot_id).desired_state == "stopped"
    output = capsys.readouterr().out
    assert "没有待自动启动的 Bot" in output
    assert f"uv run maibot host start --bot-id {bot.bot_id}" in output


def test_host_only_mode_does_not_resume_previously_running_bots(tmp_path: Path) -> None:
    registry = Registry(tmp_path)
    registry.create()
    first = registry.add_bot("First", tmp_path / "first", owned=False, initialization="initialized")
    second = registry.add_bot("Second", tmp_path / "second", owned=False, initialization="initialized")
    for bot in (first, second):
        registry.set_desired_state(bot.bot_id, "running")
    workspace = Workspace(tmp_path, registry, HostConfig())

    async def run() -> None:
        supervisor = BotSupervisor(workspace, public_base_url="http://localhost:8001", host_only=True)
        started = []

        async def spawn(runtime) -> None:
            started.append(runtime.bot.bot_id)
            runtime.status = "running"
            runtime.settled.set()

        supervisor._spawn = spawn
        assert await supervisor.start_desired() == []
        assert started == []
        assert (await supervisor.start(second.bot_id))["status"] == "running"
        assert await supervisor.start_desired() == []
        assert started == [second.bot_id, second.bot_id]
        assert (await supervisor.status(first.bot_id))["status"] == "stopped"
        assert (await supervisor.stop(second.bot_id))["status"] == "stopped"

    asyncio.run(run())
    assert registry.get_bot(first.bot_id).desired_state == "running"
    assert registry.get_bot(second.bot_id).desired_state == "stopped"
