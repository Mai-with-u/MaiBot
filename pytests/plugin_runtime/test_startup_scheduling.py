from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock, Mock

import asyncio

import pytest

from src.plugin_runtime.protocol.envelope import Envelope, MessageType, RunnerReadyPayload
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.runner import runner_main
from src.plugin_runtime.runner.manifest_validator import ManifestValidator, PluginDependencyDefinition
from src.plugin_runtime.runner.plugin_loader import PluginMeta
from src.plugin_runtime.runner.runner_main import PluginActivationStatus, PluginRunner


def _meta(plugin_id: str, dependencies: List[str] | None = None, *, version_spec: str = ">=1.0.0"):
    return SimpleNamespace(
        plugin_id=plugin_id,
        plugin_dir="unused",
        version="1.0.0",
        config_error="",
        load_error="",
        manifest=SimpleNamespace(
            plugin_dependencies=[
                PluginDependencyDefinition(type="plugin", id=dependency, version_spec=version_spec)
                for dependency in dependencies or []
            ]
        ),
    )


def _runner(monkeypatch, *, failed_imports=None):
    runner = PluginRunner("unused", "test", [])
    runner._loader = SimpleNamespace(
        failed_plugins=failed_imports or {},
        manifest_validator=ManifestValidator(),
        purge_plugin_modules=lambda *args: None,
        set_loaded_plugin=Mock(),
    )
    monkeypatch.setattr(runner, "_prepare_plugin_activation", lambda meta: None)
    monkeypatch.setattr(runner._rpc_client, "send_event", AsyncMock())
    return runner


def _registration_meta(plugin_id="test.demo"):
    manifest = SimpleNamespace(
        version="1.0.0", plugin_type="extension", capabilities=[], plugin_dependency_ids=[], plugin_dependencies=[],
        llm_provider_client_types=[], name="Demo",
    )
    return PluginMeta(plugin_id, plugin_id, "", SimpleNamespace(), manifest)


@pytest.mark.asyncio
async def test_dependency_released_without_waiting_for_unrelated_plugin(monkeypatch):
    runner = _runner(monkeypatch)
    slow_release = asyncio.Event()
    child_started = asyncio.Event()
    finished = set()

    async def activate(meta):
        if meta.plugin_id == "test.slow":
            await slow_release.wait()
        if meta.plugin_id == "test.child":
            assert "test.parent" in finished
            assert "test.slow" not in finished
            child_started.set()
        finished.add(meta.plugin_id)
        return PluginActivationStatus.LOADED

    monkeypatch.setattr(runner, "_activate_plugin", activate)
    task = asyncio.create_task(
        runner._initialize_plugins(
            [
                _meta("test.parent"),
                _meta("test.slow"),
                _meta("test.child", ["test.parent"]),
            ],
            max_concurrency=2,
        )
    )
    try:
        await asyncio.wait_for(child_started.wait(), 1.0)
        assert not task.done()
        slow_release.set()
        ready = await task
        assert ready.loaded_plugins == ["test.parent", "test.slow", "test.child"]
        assert ready.failed_plugins == []
    finally:
        slow_release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_dependencies_do_not_consume_concurrency_slots(monkeypatch):
    runner = _runner(monkeypatch)
    active = 0
    peak = 0

    async def activate(meta):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return PluginActivationStatus.LOADED

    monkeypatch.setattr(runner, "_activate_plugin", activate)
    plugins = [_meta(f"test.root{i}") for i in range(6)]
    plugins += [_meta(f"test.child{i}", [f"test.root{i}"]) for i in range(6)]
    ready = await asyncio.wait_for(runner._initialize_plugins(plugins, max_concurrency=2), 1.0)
    assert peak == 2
    assert len(ready.loaded_plugins) == 12


@pytest.mark.asyncio
async def test_registered_dependency_is_not_available_until_on_load_finishes(monkeypatch):
    runner = _runner(monkeypatch)
    parent_loading = asyncio.Event()
    release_parent = asyncio.Event()
    child_started = asyncio.Event()

    async def bootstrap(meta, **kwargs):
        if meta.plugin_id == "test.child":
            child_started.set()
        return True

    async def on_load(meta):
        if meta.plugin_id == "test.parent":
            parent_loading.set()
            await release_parent.wait()
        return True

    monkeypatch.setattr(runner, "_bootstrap_plugin", bootstrap)
    monkeypatch.setattr(runner, "_register_plugin", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_invoke_plugin_on_load", on_load)
    task = asyncio.create_task(
        runner._initialize_plugins(
            [
                _meta("test.parent"),
                _meta("test.child", ["test.parent"]),
            ]
        )
    )
    try:
        await asyncio.wait_for(parent_loading.wait(), 1.0)
        assert not child_started.is_set()
        release_parent.set()
        ready = await task
        assert child_started.is_set()
        assert ready.loaded_plugins == ["test.parent", "test.child"]
    finally:
        release_parent.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_state", ["failed", "inactive", "config_error", "import_failed"])
async def test_failed_dependency_only_blocks_its_descendants(monkeypatch, parent_state):
    runner = _runner(
        monkeypatch, failed_imports={"test.parent": "import failed"} if parent_state == "import_failed" else {}
    )
    parent = _meta("test.parent")
    parent.config_error = "bad config" if parent_state == "config_error" else ""

    def prepare(meta):
        if meta.plugin_id == "test.parent" and parent_state == "inactive":
            return PluginActivationStatus.INACTIVE
        return None

    async def activate(meta):
        assert meta.plugin_id not in {"test.child", "test.grandchild"}
        if meta.plugin_id == "test.parent" and parent_state == "failed":
            meta.load_error = "on_load failed"
            return PluginActivationStatus.FAILED
        return PluginActivationStatus.LOADED

    monkeypatch.setattr(runner, "_prepare_plugin_activation", prepare)
    monkeypatch.setattr(runner, "_activate_plugin", activate)
    plugins = ([] if parent_state == "import_failed" else [parent]) + [
        _meta("test.child", ["test.parent"]),
        _meta("test.grandchild", ["test.child"]),
        _meta("test.unrelated"),
    ]
    ready = await runner._initialize_plugins(plugins)
    assert "test.unrelated" in ready.loaded_plugins
    blocked = ready.inactive_plugins if parent_state in {"inactive", "config_error"} else ready.failed_plugins
    assert {"test.child", "test.grandchild"}.issubset(blocked)
    assert ready.explicitly_disabled_plugins == (["test.parent"] if parent_state == "inactive" else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("external_version", ["1.5.0", "2.0.0"])
async def test_external_dependency_checks_version_before_activation(monkeypatch, external_version):
    runner = _runner(monkeypatch)
    runner._external_available_plugins = {"other.parent": external_version}
    activate = AsyncMock(return_value=PluginActivationStatus.LOADED)
    monkeypatch.setattr(runner, "_activate_plugin", activate)
    ready = await runner._initialize_plugins([_meta("test.child", ["other.parent"], version_spec="<2.0.0")])
    assert ready.loaded_plugins == (["test.child"] if external_version == "1.5.0" else [])
    assert activate.await_count == (1 if external_version == "1.5.0" else 0)


@pytest.mark.asyncio
async def test_cancelled_startup_collects_all_initialization_tasks(monkeypatch):
    runner = _runner(monkeypatch)
    entered = asyncio.Event()
    active = set()

    async def activate(meta):
        active.add(meta.plugin_id)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(meta.plugin_id)

    monkeypatch.setattr(runner, "_activate_plugin", activate)
    task = asyncio.create_task(
        runner._initialize_plugins(
            [
                _meta("test.parent"),
                _meta("test.child", ["test.parent"]),
                _meta("test.unrelated"),
            ]
        )
    )
    await asyncio.wait_for(entered.wait(), 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not active
    assert not any(t.get_name().startswith("plugin_startup:") for t in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_progress_publishes_results_while_another_plugin_is_loading(monkeypatch):
    runner = _runner(monkeypatch)
    release_slow = asyncio.Event()
    partial_results = asyncio.Event()
    snapshots = []

    async def publish(method, *, payload):
        assert method == "runner.startup_progress"
        snapshots.append(payload)
        if "test.good" in payload["loaded_plugins"] and "test.bad" in payload["failed_plugins"]:
            partial_results.set()

    async def activate(meta):
        if meta.plugin_id == "test.slow":
            await release_slow.wait()
        if meta.plugin_id == "test.bad":
            meta.load_error = "on_load failed with explicit cause"
            return PluginActivationStatus.FAILED
        return PluginActivationStatus.LOADED

    monkeypatch.setattr(runner._rpc_client, "send_event", publish)
    monkeypatch.setattr(runner, "_activate_plugin", activate)
    startup = asyncio.create_task(runner._initialize_plugins([
        _meta("test.slow"), _meta("test.good"), _meta("test.bad"),
    ]))
    try:
        await asyncio.wait_for(partial_results.wait(), 1.0)
        assert not startup.done()
        assert snapshots[-1]["loading_plugins"] == ["test.slow"]
        assert snapshots[-1]["failed_plugin_reasons"]["test.bad"] == "on_load failed with explicit cause"
        release_slow.set()
        await startup
        assert snapshots[-1]["loading_plugins"] == []
    finally:
        release_slow.set()
        await asyncio.gather(startup, return_exceptions=True)


@pytest.mark.asyncio
async def test_on_load_exception_is_kept_in_reported_failure_reason(monkeypatch):
    runner = _runner(monkeypatch)
    meta = _meta("test.bad")
    meta.instance = SimpleNamespace(on_load=AsyncMock(side_effect=ValueError("invalid endpoint")))
    monkeypatch.setattr(runner, "_bootstrap_plugin", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_register_plugin", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_unregister_plugin", AsyncMock())
    monkeypatch.setattr(runner, "_deactivate_plugin", AsyncMock())
    ready = await runner._initialize_plugins([meta])
    assert ready.failed_plugin_reasons["test.bad"] == "on_load 失败: ValueError: invalid endpoint"


@pytest.mark.asyncio
async def test_activation_failure_without_reason_is_exposed(monkeypatch):
    runner = _runner(monkeypatch)
    monkeypatch.setattr(runner, "_activate_plugin", AsyncMock(return_value=PluginActivationStatus.FAILED))
    with pytest.raises(RuntimeError, match="没有记录失败原因"):
        await runner._initialize_plugins([_meta("test.bad")])


@pytest.mark.asyncio
async def test_capability_cleanup_keeps_original_registration_error(monkeypatch):
    runner = _runner(monkeypatch)
    meta = _meta("test.bad")
    meta.instance = SimpleNamespace()

    async def register(meta):
        meta.load_error = "component schema is invalid"
        return False

    monkeypatch.setattr(runner, "_register_plugin", register)
    monkeypatch.setattr(runner, "_sync_plugin_capabilities", AsyncMock(side_effect=[
        None, RPCError(ErrorCode.E_TIMEOUT, "revocation timed out"),
    ]))
    cleanup_log = Mock()
    monkeypatch.setattr(runner_main.logger, "exception", cleanup_log)
    assert await runner._activate_plugin(meta) == PluginActivationStatus.FAILED
    assert meta.load_error == "component schema is invalid"
    cleanup_log.assert_called_once()


@pytest.mark.asyncio
async def test_capability_cleanup_does_not_hide_programming_errors(monkeypatch):
    runner = _runner(monkeypatch)
    monkeypatch.setattr(runner, "_sync_plugin_capabilities", AsyncMock(side_effect=AttributeError("invalid metadata")))
    with pytest.raises(AttributeError, match="invalid metadata"):
        await runner._deactivate_plugin(_meta("test.bad"))


@pytest.mark.asyncio
async def test_default_empty_capabilities_preserve_adapter_type(monkeypatch):
    runner = _runner(monkeypatch)
    meta = _meta("test.adapter")
    meta.plugin_type = "adapter"
    meta.capabilities_required = []
    request = Envelope(request_id=1, message_type=MessageType.REQUEST, method="plugin.bootstrap")
    send = AsyncMock(return_value=request.make_response(payload={"accepted": True}))
    monkeypatch.setattr(runner._rpc_client, "send_request", send)
    await runner._sync_plugin_capabilities(meta)
    assert send.call_args.kwargs["payload"]["plugin_type"] == "adapter"
    await runner._sync_plugin_capabilities(meta, [])
    assert send.call_args.kwargs["payload"]["plugin_type"] == "extension"


@pytest.mark.asyncio
async def test_shutdown_waits_for_initialization_before_unloading(monkeypatch):
    runner = PluginRunner("unused", "test", [])
    meta = _meta("test.slow")
    loaded = []
    entered = asyncio.Event()
    release = asyncio.Event()
    runner._loader = SimpleNamespace(
        discover_and_load=lambda *args, **kwargs: [meta],
        list_plugins=lambda: list(loaded), get_plugin=lambda plugin_id: meta,
    )

    async def initialize(plugins):
        entered.set()
        await release.wait()
        loaded.append(meta.plugin_id)
        return RunnerReadyPayload(loaded_plugins=[meta.plugin_id])

    monkeypatch.setattr(runner._rpc_client, "connect_and_handshake", AsyncMock(return_value=True))
    monkeypatch.setattr(runner._rpc_client, "disconnect", AsyncMock())
    monkeypatch.setattr(runner, "_install_log_handler", Mock())
    monkeypatch.setattr(runner, "_uninstall_log_handler", AsyncMock())
    monkeypatch.setattr(runner, "_register_handlers", Mock())
    monkeypatch.setattr(runner, "_initialize_plugins", initialize)
    monkeypatch.setattr(runner, "_notify_ready", AsyncMock())
    monkeypatch.setattr(runner, "_dump_inflight_debug", AsyncMock())
    unload = AsyncMock(side_effect=lambda *args, **kwargs: loaded.remove(meta.plugin_id))
    monkeypatch.setattr(runner, "_unload_plugin", unload)
    startup = asyncio.create_task(runner.run())
    shutdown = None
    try:
        await asyncio.wait_for(entered.wait(), 1.0)
        request = Envelope(request_id=1, message_type=MessageType.REQUEST, method="plugin.shutdown")
        shutdown = asyncio.create_task(runner._handle_shutdown(request))
        await asyncio.sleep(0)
        unload.assert_not_awaited()
        assert not shutdown.done()
        release.set()
        await asyncio.wait_for(shutdown, 1.0)
        await asyncio.wait_for(startup, 2.0)
        unload.assert_awaited_once_with(meta, reason="runner_shutdown")
        assert not loaded
    finally:
        release.set()
        startup.cancel()
        if shutdown is not None:
            shutdown.cancel()
        await asyncio.gather(startup, *([shutdown] if shutdown is not None else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler, method, payload", [
    ("_handle_reload_plugin", "plugin.reload", {"plugin_id": "test.demo"}),
    ("_handle_reload_plugins", "plugin.reload_batch", {"plugin_ids": ["test.demo"]}),
    ("_handle_unload_plugins", "plugin.unload_batch", {"plugin_ids": ["test.demo"]}),
])
async def test_lifecycle_request_queued_behind_shutdown_is_rejected(monkeypatch, handler, method, payload):
    runner = PluginRunner("unused", "test", [])
    runner._loader = SimpleNamespace(list_plugins=lambda: [])
    monkeypatch.setattr(runner, "_dump_inflight_debug", AsyncMock())
    await runner._lifecycle_lock.acquire()
    shutdown_request = Envelope(request_id=1, message_type=MessageType.REQUEST, method="plugin.shutdown")
    shutdown = asyncio.create_task(runner._handle_shutdown(shutdown_request))
    try:
        await asyncio.sleep(0)
        runner._lifecycle_lock.release()
        # shutdown 已经排队，但锁尚未被它接走；新请求只能在获取锁后判断终止状态。
        request = Envelope(request_id=2, message_type=MessageType.REQUEST, method=method, payload=payload)
        response = await getattr(runner, handler)(request)
        await shutdown
        assert response.error["code"] == ErrorCode.E_SHUTTING_DOWN.value
    finally:
        shutdown.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, success, error_text", [
    ({}, False, "accepted"),
    ({"accepted": 1}, False, "accepted"),
    ({"accepted": False}, False, "未提供原因"),
    ({"accepted": False, "reason": "registration denied"}, False, "registration denied"),
    ({"accepted": True}, True, ""),
])
async def test_registration_requires_explicit_valid_host_confirmation(monkeypatch, payload, success, error_text):
    runner = _runner(monkeypatch)
    meta = _registration_meta()
    monkeypatch.setattr(runner_main, "load_webui_extension", lambda path: None)
    request = Envelope(request_id=1, message_type=MessageType.REQUEST, method="plugin.register_components")
    monkeypatch.setattr(runner._rpc_client, "send_request", AsyncMock(return_value=request.make_response(payload=payload)))
    assert await runner._register_plugin(meta) is success
    if not success:
        assert error_text in meta.load_error


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration", ["components", "providers", "webui", "buttons"])
async def test_invalid_registration_declaration_does_not_abort_other_plugins(monkeypatch, declaration):
    runner = _runner(monkeypatch)
    bad, good = _registration_meta("test.bad"), _registration_meta("test.good")
    monkeypatch.setattr(runner_main, "load_webui_extension", lambda path: None)
    if declaration == "components":
        bad.instance.get_components = Mock(side_effect=ValueError("bad components"))
    elif declaration == "providers":
        bad.manifest.llm_provider_client_types = ["missing-provider"]
    elif declaration == "webui":
        def load_webui(path):
            if path == bad.plugin_dir:
                raise ValueError("bad webui")
            return None

        monkeypatch.setattr(runner_main, "load_webui_extension", load_webui)
    else:
        def read_buttons(instance):
            if instance is bad.instance:
                raise ValueError("bad buttons")
            return []

        monkeypatch.setattr(runner_main, "read_button_declarations", read_buttons)
    request = Envelope(request_id=1, message_type=MessageType.REQUEST, method="plugin.register_components")
    monkeypatch.setattr(runner._rpc_client, "send_request", AsyncMock(return_value=request.make_response(payload={"accepted": True})))
    ready = await runner._initialize_plugins([bad, good])
    assert ready.loaded_plugins == [good.plugin_id]
    assert ready.failed_plugins == [bad.plugin_id]
    assert ready.failed_plugin_reasons[bad.plugin_id] == bad.load_error
    assert bad.load_error
