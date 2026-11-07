"""更新与 Bot 管理的状态转换；更新执行器使用替身，不修改真实仓库。"""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import asyncio

from src.host.bot_operations import archive_bot, clone_bot, recover_archives
from src.host.config import HostConfig
from src.host.layout import Workspace
from src.host.maintenance import HostMaintenance
from src.host.registry import Registry
from src.host.supervisor import BotSupervisor


@pytest.fixture
def workspace(tmp_path):
    registry = Registry(tmp_path)
    registry.create()
    return Workspace(tmp_path, registry, HostConfig())


def source_bot(workspace, owned=True):
    identifier = 'a' * 32
    directory = workspace.root / 'bots' / identifier if owned else workspace.root / 'external'
    for section in ('config', 'plugins', 'data', 'logs'):
        (directory / section).mkdir(parents=True)
    (directory / 'config' / 'bot_config.toml').write_text('[bot]\nnickname="source"\n', encoding='utf-8')
    (directory / 'plugins' / 'example.py').write_text('# plugin', encoding='utf-8')
    (directory / 'data' / 'history.db').write_bytes(b'history')
    return workspace.registry.add_bot('source', directory, owned=owned, initialization='initialized', bot_id=identifier)


@pytest.mark.asyncio
async def test_automatic_dependency_requests_are_serial_and_failure_requires_retry(workspace, monkeypatch):
    from src.host import maintenance as maintenance_module

    source_bot(workspace)
    supervisor = BotSupervisor(workspace, public_base_url='http://localhost')
    maintenance = HostMaintenance(supervisor)
    monkeypatch.setattr(maintenance_module, '_analyze_bot', lambda *args, **kwargs: [
        {'requirement': 'missing>=1', 'package': 'missing', 'plugin_ids': ['plugin'],
         'installed_version': None, 'satisfied': False},
    ])
    entered, release = asyncio.Event(), asyncio.Event()

    async def resolve(*args):
        entered.set()
        await release.wait()
        return False, 'incompatible dependencies'

    resolver = AsyncMock(side_effect=resolve)
    monkeypatch.setattr(maintenance_module.PluginDependencyPipeline, 'resolve_requirements', resolver)
    stop = AsyncMock()
    monkeypatch.setattr(supervisor, 'enter_maintenance', stop)
    maintenance.queue_plugin_dependencies()
    await entered.wait()
    maintenance.queue_plugin_dependencies()
    release.set()
    await maintenance.finish()
    assert resolver.await_count == 1
    assert 'incompatible dependencies' in maintenance.dependency_error
    assert await supervisor.start_desired()
    assert (await supervisor.status('a' * 32))['status'] == 'failed'
    with pytest.raises(RuntimeError, match='incompatible dependencies'):
        await maintenance.prepare_plugin_dependencies()
    assert resolver.await_count == 1
    with pytest.raises(RuntimeError, match='incompatible dependencies'):
        await maintenance.install_plugin_dependencies()
    assert resolver.await_count == 2
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_dependency_request_is_acknowledged_before_maintenance(workspace, monkeypatch):
    from src.host.control import HostControl
    from unittest.mock import Mock
    import json

    bot = source_bot(workspace)
    control = HostControl(BotSupervisor(workspace, public_base_url='http://localhost'))
    queued = Mock()
    monkeypatch.setattr(control.maintenance, 'queue_plugin_dependencies', queued)

    async def send(payload):
        queued.assert_not_called()
        assert json.loads(payload) == {'ok': True, 'result': {'status': 'pending'}}

    connection = SimpleNamespace(
        recv_frame=AsyncMock(return_value=json.dumps({
            'action': 'prepare_plugin_dependencies', 'bot_id': bot.bot_id,
        }).encode()),
        send_frame=send,
    )
    await control.handle(connection)
    queued.assert_called_once()


def test_template_clone_is_stopped_and_archive_preserves_data(workspace):
    source = source_bot(workspace)
    clone = clone_bot(workspace, source, 'copy')
    assert clone.desired_state == 'stopped'
    assert (clone.directory / 'config' / 'bot_config.toml').read_bytes() == (source.directory / 'config' / 'bot_config.toml').read_bytes()
    assert not list((clone.directory / 'data').iterdir())
    assert (clone.directory / 'plugins' / 'example.py').is_file()
    renamed = workspace.registry.rename_bot(source.bot_id, 'renamed')
    assert renamed.directory == source.directory and renamed.name == 'renamed'
    result = archive_bot(workspace, source)
    assert (Path(result['directory']) / 'data' / 'history.db').read_bytes() == b'history'
    assert not source.directory.exists()
    assert [bot.bot_id for bot in workspace.registry.list_bots()] == [clone.bot_id]


def test_external_archive_only_unregisters_and_interrupted_move_recovers(workspace, monkeypatch):
    external = source_bot(workspace, owned=False)
    archive_bot(workspace, external)
    assert (external.directory / 'data' / 'history.db').is_file()
    source = source_bot(workspace)
    remove = workspace.registry.remove_bot
    monkeypatch.setattr(workspace.registry, 'remove_bot', lambda _: (_ for _ in ()).throw(RuntimeError('interrupted')))
    with pytest.raises(RuntimeError, match='interrupted'):
        archive_bot(workspace, source)
    monkeypatch.setattr(workspace.registry, 'remove_bot', remove)
    recover_archives(workspace)
    assert not workspace.registry.list_bots()
    assert (workspace.host_directory / 'archives' / source.bot_id / 'instance' / 'data' / 'history.db').is_file()


@pytest.mark.asyncio
async def test_update_preflight_then_stop_preserves_desired_states(workspace, monkeypatch):
    from src.common import package_update, self_update
    source = source_bot(workspace)
    supervisor = BotSupervisor(workspace, public_base_url='http://localhost')
    maintenance = HostMaintenance(supervisor)
    events = []
    monkeypatch.setattr(package_update, 'ensure_other_instances_stopped', lambda *args: None)
    monkeypatch.setattr(self_update, 'request_update', lambda *args: events.append('request'))
    stop = supervisor.enter_maintenance
    async def enter():
        events.append('stop')
        await stop()
    monkeypatch.setattr(supervisor, 'enter_maintenance', enter)
    await maintenance.prepare_update('a' * 40)
    assert events == ['request', 'stop'] and maintenance.update_requested
    assert workspace.registry.get_bot(source.bot_id).desired_state == 'running'
    with pytest.raises(RuntimeError, match='维护'):
        await supervisor.start(source.bot_id)
    with pytest.raises(RuntimeError):
        await maintenance.manage_bot(source.bot_id, 'archive')
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_failed_stop_cancels_pending_update(workspace, monkeypatch):
    from src.common import package_update, self_update
    supervisor = BotSupervisor(workspace, public_base_url='http://localhost')
    maintenance = HostMaintenance(supervisor)
    cleared = []
    monkeypatch.setattr(package_update, 'ensure_other_instances_stopped', lambda *args: None)
    monkeypatch.setattr(self_update, 'request_update', lambda *args: None)
    monkeypatch.setattr(self_update, 'clear_pending_update', lambda *args: cleared.append(True))
    monkeypatch.setattr(supervisor, 'enter_maintenance', AsyncMock(side_effect=RuntimeError('stop failed')))
    with pytest.raises(RuntimeError, match='stop failed'):
        await maintenance.prepare_update('a' * 40)
    assert cleared == [True] and not maintenance.update_requested


def test_updater_restarts_only_after_success_or_safe_rollback(workspace, monkeypatch):
    from src.common import self_update
    from src.host import updater
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(workspace.root))
    monkeypatch.setenv("MAIBOT_PACKAGE_RUNNER", "1")
    monkeypatch.setattr(updater, 'register_host_instance', lambda _: nullcontext())
    monkeypatch.setattr(updater, 'FileLock', lambda _: nullcontext())
    monkeypatch.setattr(updater, 'ensure_other_instances_stopped', lambda *args: None)
    monkeypatch.setattr(updater, 'PACKAGED_INSTALL', False)
    result = SimpleNamespace(success=True, rollback_error='', stage='done', rolled_back=False)
    monkeypatch.setattr(self_update, 'apply_pending_update', lambda *args: result)
    assert updater.main(workspace.root) == 0
    result.success = False
    result.rollback_error = 'rollback failed'
    with pytest.raises(RuntimeError, match='回滚未完成'):
        updater.main(workspace.root)
    result.rollback_error = ''
    result.rolled_back = True
    assert updater.main(workspace.root) == 0


@pytest.mark.asyncio
async def test_dependency_preflight_includes_other_bots_installed_constraints(workspace, monkeypatch):
    from src.host import maintenance as maintenance_module

    first = source_bot(workspace)
    second = clone_bot(workspace, first, 'other')
    reports = {
        first.directory: [{'requirement': 'shared<2', 'package': 'shared', 'plugin_ids': ['existing'], 'installed_version': '1.5', 'satisfied': True}],
        second.directory: [{'requirement': 'new-plugin>=1', 'package': 'new-plugin', 'plugin_ids': ['new'], 'installed_version': None, 'satisfied': False}],
    }
    monkeypatch.setattr(maintenance_module, '_analyze_bot', lambda directory, shared_catalog=None: reports[directory])
    supervisor = BotSupervisor(workspace, public_base_url='http://localhost')
    stop = AsyncMock()
    monkeypatch.setattr(supervisor, 'enter_maintenance', stop)
    resolver = AsyncMock(return_value=(False, 'new-plugin requires shared>=2'))
    installer = AsyncMock()
    monkeypatch.setattr(maintenance_module.PluginDependencyPipeline, 'resolve_requirements', resolver)
    monkeypatch.setattr(maintenance_module.PluginDependencyPipeline, 'install_requirements', installer)
    with pytest.raises(RuntimeError, match='依赖联合解析失败') as error:
        await HostMaintenance(supervisor).install_plugin_dependencies()
    assert {item.requirement_text for item in resolver.call_args.args[0]} == {'shared<2', 'new-plugin>=1'}
    assert first.bot_id in str(error.value) and second.bot_id in str(error.value)
    stop.assert_not_awaited()
    installer.assert_not_awaited()


@pytest.mark.asyncio
async def test_installed_incompatible_dependency_is_reported_without_stopping(workspace, monkeypatch):
    from src.host import maintenance as maintenance_module

    bot = source_bot(workspace)
    monkeypatch.setattr(maintenance_module, '_analyze_bot', lambda *args, **kwargs: [
        {'requirement': 'shared>=2', 'package': 'shared', 'plugin_ids': ['plugin'], 'installed_version': '1.5', 'satisfied': False},
    ])
    supervisor = BotSupervisor(workspace, public_base_url='http://localhost')
    stop = AsyncMock()
    monkeypatch.setattr(supervisor, 'enter_maintenance', stop)
    maintenance = HostMaintenance(supervisor)
    report = await maintenance.analyze_plugin_dependencies()
    assert bot.bot_id in report['conflicts'][0] and not report['requirements']
    with pytest.raises(RuntimeError, match='共享环境冲突'):
        await maintenance.install_plugin_dependencies()
    stop.assert_not_awaited()
