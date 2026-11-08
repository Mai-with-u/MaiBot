"""用轻量假 Worker 验证生命周期：就绪、崩溃重启、启动失败、主动重启与逐个启动。"""

from pathlib import Path
from typing import List

import asyncio
import sys
import textwrap

from aiohttp import ClientSession
import pytest

from src.common.ipc import http_connector
from src.host import supervisor as supervisor_module
from src.host.config import HostConfig
from src.host.layout import Workspace
from src.host.registry import BotRecord, Registry
from src.host.supervisor import BotSupervisor


ROOT = Path(__file__).resolve().parents[2]

# FAKE_MODE 由测试写在 Bot 目录的 mode 文件中，每次启动时读取，可在运行中途改变下一次的行为。
FAKE_WORKER = textwrap.dedent("""
    from pathlib import Path
    import asyncio
    import signal
    import sys

    from fastapi import FastAPI
    import uvicorn

    from src.common.ipc import IpcHTTPServer
    from src.common.runtime_context import report_phase, runtime_context

    context = runtime_context()
    mode = Path("mode").read_text(encoding="utf-8").strip() if Path("mode").exists() else "serve"
    starts = Path("starts")
    starts.write_text(str(int(starts.read_text()) + 1 if starts.exists() else 1))
    if mode == "fail":
        raise SystemExit(3)
    if mode == "migrate":
        report_phase("migrating", "数据库迁移 1/1")
        while Path("mode").read_text(encoding="utf-8").strip() == "migrate":
            import time
            time.sleep(0.05)

    app = FastAPI()

    @app.get("/api/whoami")
    def whoami():
        return {"bot_id": context.bot_id}

    server = IpcHTTPServer(uvicorn.Config(app, log_config=None, lifespan="off"), context.http_endpoint)
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))

    async def main():
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        report_phase("ready")
        while not server.should_exit:
            current = Path("mode").read_text(encoding="utf-8").strip() if Path("mode").exists() else "serve"
            if current in {"crash", "restart"}:
                Path("mode").write_text("serve", encoding="utf-8")
                server.should_exit = True
                await task
                raise SystemExit(1 if current == "crash" else 42)
            await asyncio.sleep(0.05)
        await task

    asyncio.run(main())
""")


def fake_command(_bot: BotRecord) -> List[str]:
    return [sys.executable, "-c", FAKE_WORKER]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setattr(supervisor_module, "RESTART_DELAY_MIN", 0.05)
    registry = Registry(tmp_path)
    registry.create()
    return Workspace(root=tmp_path, registry=registry, config=HostConfig())


def add_bot(workspace: Workspace, name: str, mode: str = "serve") -> BotRecord:
    directory = workspace.root / "bots" / name
    (directory / "temp").mkdir(parents=True)
    (directory / "mode").write_text(mode, encoding="utf-8")
    return workspace.registry.add_bot(name, directory, owned=True, initialization="initialized")


def make_supervisor(workspace: Workspace) -> BotSupervisor:
    return BotSupervisor(workspace, public_base_url="http://127.0.0.1:8001", command_factory=fake_command, stop_timeout=10)


async def wait_status(supervisor: BotSupervisor, bot_id: str, *statuses: str, timeout: float = 30) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        snapshot = await supervisor.status(bot_id)
        if snapshot["status"] in statuses:
            return snapshot
        if asyncio.get_running_loop().time() > deadline:
            log = Path(snapshot["log_path"])
            pytest.fail(f"状态停留在 {snapshot}\n{log.read_text(encoding='utf-8') if log.exists() else ''}")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_bot_becomes_running_only_after_ready_and_is_reachable(workspace: Workspace) -> None:
    bot = add_bot(workspace, "one")
    supervisor = make_supervisor(workspace)
    try:
        assert (await supervisor.start(bot.bot_id))["status"] == "starting"
        with pytest.raises(RuntimeError):
            supervisor.proxy_endpoint(bot.bot_id)
        await wait_status(supervisor, bot.bot_id, "running")
        async with ClientSession(connector=http_connector(supervisor.proxy_endpoint(bot.bot_id))) as client:
            async with client.get("http://bot/api/whoami") as response:
                assert await response.json() == {"bot_id": bot.bot_id}
        stopped = await supervisor.stop(bot.bot_id)
        assert stopped["status"] == "stopped" and stopped["exit_code"] == 0
        assert workspace.registry.get_bot(bot.bot_id).desired_state == "stopped"
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_crash_after_ready_restarts_with_backoff(workspace: Workspace) -> None:
    bot = add_bot(workspace, "crashy")
    log_dir = bot.directory / "logs"
    log_dir.mkdir()
    (log_dir / "app_test.log.jsonl").write_text("{}\n", encoding="utf-8")
    supervisor = make_supervisor(workspace)
    try:
        await supervisor.start(bot.bot_id)
        snapshot = await wait_status(supervisor, bot.bot_id, "running")
        for log_file in log_dir.iterdir():
            log_file.unlink()
        assert Path(snapshot["log_path"]).is_file()
        (bot.directory / "mode").write_text("crash", encoding="utf-8")
        await wait_status(supervisor, bot.bot_id, "failed")
        await wait_status(supervisor, bot.bot_id, "running")
        assert (bot.directory / "starts").read_text() == "2"
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_repeated_crashes_enter_crash_loop(workspace: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor_module, "CRASH_LIMIT", 2)
    bot = add_bot(workspace, "loop")
    supervisor = make_supervisor(workspace)
    try:
        await supervisor.start(bot.bot_id)
        for _ in range(2):
            await wait_status(supervisor, bot.bot_id, "running")
            (bot.directory / "mode").write_text("crash", encoding="utf-8")
            await wait_status(supervisor, bot.bot_id, "failed", "crash_loop")
        snapshot = await wait_status(supervisor, bot.bot_id, "crash_loop")
        assert "已停止自动重启" in snapshot["error"]
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_startup_failure_is_not_retried(workspace: Workspace) -> None:
    bot = add_bot(workspace, "broken", mode="fail")
    supervisor = make_supervisor(workspace)
    try:
        await supervisor.start(bot.bot_id)
        snapshot = await wait_status(supervisor, bot.bot_id, "failed")
        assert snapshot["exit_code"] == 3 and snapshot["retry_at"] is None
        await asyncio.sleep(0.3)
        assert (bot.directory / "starts").read_text() == "1"
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_worker_restart_request_respawns_immediately(workspace: Workspace) -> None:
    bot = add_bot(workspace, "restarts")
    supervisor = make_supervisor(workspace)
    try:
        await supervisor.start(bot.bot_id)
        await wait_status(supervisor, bot.bot_id, "running")
        (bot.directory / "mode").write_text("restart", encoding="utf-8")
        while (bot.directory / "starts").read_text() != "2":
            await asyncio.sleep(0.05)
        await wait_status(supervisor, bot.bot_id, "running")
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_desired_bots_start_one_after_another(workspace: Workspace) -> None:
    first = add_bot(workspace, "first", mode="migrate")
    second = add_bot(workspace, "second")
    stopped = add_bot(workspace, "stopped")
    workspace.registry.set_desired_state(stopped.bot_id, "stopped")
    supervisor = make_supervisor(workspace)
    try:
        starter = asyncio.create_task(supervisor.start_desired())
        snapshot = await wait_status(supervisor, first.bot_id, "migrating")
        assert snapshot["detail"] == "数据库迁移 1/1"
        await asyncio.sleep(0.3)
        assert not (second.directory / "starts").exists()
        (first.directory / "mode").write_text("serve", encoding="utf-8")
        assert await starter == []
        await wait_status(supervisor, second.bot_id, "running")
        assert not (stopped.directory / "starts").exists()
    finally:
        await supervisor.shutdown()
    # 关闭宿主不改变期望状态，下次启动时恢复。
    assert workspace.registry.get_bot(first.bot_id).desired_state == "running"
    assert (first.directory / "temp" / "worker-console.log").is_file()


@pytest.mark.asyncio
async def test_plugin_dependencies_install_while_all_bots_are_stopped(workspace: Workspace, monkeypatch) -> None:
    from src.host import maintenance as maintenance_module
    from src.host.maintenance import HostMaintenance

    running = add_bot(workspace, "running")
    stopped = add_bot(workspace, "stopped")
    workspace.registry.set_desired_state(stopped.bot_id, "stopped")
    supervisor = make_supervisor(workspace)
    reports = {running.directory: [{"requirement": "pkg>=1", "package": "pkg", "plugin_ids": ["a.plugin"]}],
               stopped.directory: [{"requirement": "pkg>=1", "package": "pkg", "plugin_ids": ["b.plugin"]}]}
    monkeypatch.setattr(maintenance_module, "_analyze_bot", lambda directory, shared_catalog=None: reports[directory])
    installed = []

    async def install(self, requirements):
        # 安装期间没有任何 Bot 在运行，也不能被启动。
        assert all(item["pid"] is None for item in await supervisor.statuses())
        with pytest.raises(RuntimeError, match="维护"):
            await supervisor.start(running.bot_id)
        installed.append([(item.requirement_text, item.plugin_ids) for item in requirements])
        for items in reports.values():
            for item in items:
                item.update(installed_version="1.0", satisfied=True)
        return True, ""

    monkeypatch.setattr(maintenance_module.PluginDependencyPipeline, "install_requirements", install)
    async def resolve(self, requirements):
        assert (await supervisor.status(running.bot_id))["status"] == "running"
        return True, ""
    monkeypatch.setattr(maintenance_module.PluginDependencyPipeline, "resolve_requirements", resolve)
    try:
        await supervisor.start(running.bot_id)
        await wait_status(supervisor, running.bot_id, "running")
        maintenance = HostMaintenance(supervisor)
        result = await maintenance.install_plugin_dependencies()
        assert result["installed"] and installed == [[("pkg>=1", tuple(sorted([
            f"running ({running.bot_id}) / a.plugin", f"stopped ({stopped.bot_id}) / b.plugin",
        ])))]]
        # 维护结束后按期望状态恢复：期望运行的重新启动，期望停止的保持停止。
        await wait_status(supervisor, running.bot_id, "running")
        assert (running.directory / "starts").read_text() == "2"
        assert not (stopped.directory / "starts").exists()
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_start_prepares_dependencies_before_worker_and_preserves_stopped_bots(workspace, monkeypatch):
    from src.host import maintenance as maintenance_module
    from src.host.maintenance import HostMaintenance

    bot = add_bot(workspace, "automatic")
    stopped = add_bot(workspace, "stopped")
    workspace.registry.set_desired_state(stopped.bot_id, "stopped")
    supervisor = make_supervisor(workspace)
    installed = False
    calls = []

    def analyze(*args, **kwargs):
        return [{"requirement": "pkg>=1", "package": "pkg", "plugin_ids": ["plugin"],
                 "installed_version": "1.0" if installed else None, "satisfied": installed}]

    async def resolve(*args):
        calls.append("resolve")
        return True, ""

    async def install(*args):
        nonlocal installed
        assert not (bot.directory / "starts").exists()
        assert all(item["pid"] is None for item in await supervisor.statuses())
        calls.append("install")
        installed = True
        return True, ""

    monkeypatch.setattr(maintenance_module, "_analyze_bot", analyze)
    monkeypatch.setattr(maintenance_module.PluginDependencyPipeline, "resolve_requirements", resolve)
    monkeypatch.setattr(maintenance_module.PluginDependencyPipeline, "install_requirements", install)
    HostMaintenance(supervisor)
    try:
        assert await supervisor.start_desired() == []
        await wait_status(supervisor, bot.bot_id, "running")
        assert calls == ["resolve", "install"]
        assert (bot.directory / "starts").read_text() == "1"
        assert not (stopped.directory / "starts").exists()
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_clone_temporarily_stops_source_and_restores_it(workspace: Workspace) -> None:
    from src.host.maintenance import HostMaintenance

    source = add_bot(workspace, 'template')
    (source.directory / 'config').mkdir()
    (source.directory / 'config' / 'bot_config.toml').write_text('[bot]\nnickname="template"\n', encoding='utf-8')
    supervisor = make_supervisor(workspace)
    maintenance = HostMaintenance(supervisor)
    try:
        await supervisor.start(source.bot_id)
        await wait_status(supervisor, source.bot_id, 'running')
        result = await maintenance.manage_bot(source.bot_id, 'clone', 'copy')
        await wait_status(supervisor, source.bot_id, 'running')
        assert int((source.directory / 'starts').read_text()) == 2
        assert (await supervisor.status(result['bot_id']))['desired_state'] == 'stopped'
        await maintenance.manage_bot(result['bot_id'], 'archive')
        assert len(workspace.registry.list_bots()) == 1
    finally:
        await supervisor.shutdown()
