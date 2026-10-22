from types import SimpleNamespace

import asyncio
import threading

import pytest

from src.config.config import config_manager
from src.config.model_configs import APIProvider
from src.llm_models.model_client.base_client import ClientProviderRegistration, ClientRegistry


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(config_manager, "register_reload_callback", lambda callback: None)
    return ClientRegistry()


@pytest.fixture
def provider():
    return APIProvider(
        name="test", client_type="test-initialization", base_url="https://example.invalid", auth_type="none"
    )


@pytest.mark.asyncio
async def test_parallel_requests_share_initialization_and_cancellation_is_local(registry, provider):
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    calls = []
    prepared = []

    def build(config):
        calls.append(threading.get_ident())
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return SimpleNamespace(
            api_provider=config, prepare_sync_resources=lambda: prepared.append(threading.get_ident())
        )

    registry.register_provider(ClientProviderRegistration(provider.client_type, build, builtin=True))
    first = asyncio.create_task(registry.get_client_class_instance_async(provider))
    second = asyncio.create_task(registry.get_client_class_instance_async(provider))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not second.done()
    finally:
        release.set()
    client = await second
    assert await registry.get_client_class_instance_async(provider) is client
    assert calls == prepared
    assert len(calls) == 1 and calls[0] != main_thread
    assert registry.client_instance_cache[(loop, provider.name)] is client


@pytest.mark.asyncio
async def test_reload_cannot_repopulate_cache_with_old_initialization(registry, provider):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    calls = []

    def build(config):
        result = SimpleNamespace(api_provider=config, prepare_sync_resources=lambda: None)
        calls.append(result)
        if len(calls) == 1:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        return result

    registry.register_provider(ClientProviderRegistration(provider.client_type, build, builtin=True))
    old = asyncio.create_task(registry.get_client_class_instance_async(provider))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        registry.clear_client_instance_cache()
        new = await registry.get_client_class_instance_async(provider)
    finally:
        release.set()
    assert await old is not new
    assert await registry.get_client_class_instance_async(provider) is new


@pytest.mark.asyncio
async def test_reload_during_sdk_import_does_not_cache_old_provider(registry, provider, monkeypatch):
    from src.llm_models import model_client

    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    def import_sdk(client_type):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)

    monkeypatch.setattr(model_client, "ensure_client_type_loaded", import_sdk)
    registry.register_provider(ClientProviderRegistration(
        provider.client_type,
        lambda config: SimpleNamespace(api_provider=config, prepare_sync_resources=lambda: None),
        builtin=True,
    ))
    task = asyncio.create_task(registry.get_client_class_instance_async(provider))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        registry.clear_client_instance_cache()
    finally:
        release.set()
    await task
    assert not registry.client_instance_cache


def test_clients_are_not_shared_across_event_loops(registry, provider):
    registry.register_provider(
        ClientProviderRegistration(
            provider.client_type,
            lambda config: SimpleNamespace(api_provider=config, prepare_sync_resources=lambda: None),
            builtin=True,
        )
    )
    first = asyncio.run(registry.get_client_class_instance_async(provider))
    second = asyncio.run(registry.get_client_class_instance_async(provider))
    assert first is not second


@pytest.mark.asyncio
async def test_plugin_factory_stays_on_calling_loop(registry, provider):
    loop = asyncio.get_running_loop()

    def build(config):
        assert asyncio.get_running_loop() is loop
        return SimpleNamespace(api_provider=config)

    registry.register_provider(ClientProviderRegistration(provider.client_type, build, owner_plugin_id="plugin"))
    assert (await registry.get_client_class_instance_async(provider)).api_provider is provider


@pytest.mark.asyncio
async def test_model_balance_reserves_slot_before_initialization_and_releases_on_cancel(monkeypatch):
    from src.config.model_configs import ModelInfo, TaskConfig
    from src.llm_models.utils_model import LLMOrchestrator, TempMethodsLLMUtils, client_registry

    models = {
        name: ModelInfo(name=name, model_type="llm", model_identifier=name, api_provider=name)
        for name in ("first", "second")
    }
    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.task_name = "planner"
    orchestrator.model_for_task = TaskConfig(model_list=list(models), selection_strategy="balance")
    orchestrator.model_usage = {name: (0, 0, 0) for name in models}
    orchestrator._explicit_model_usage = {}
    monkeypatch.setattr(orchestrator, "_refresh_task_config", lambda: None)
    monkeypatch.setattr(TempMethodsLLMUtils, "get_model_info_by_name", models.__getitem__)
    monkeypatch.setattr(TempMethodsLLMUtils, "get_provider_by_name", lambda name: name)
    arrived = asyncio.Queue()
    release = asyncio.Event()

    async def initialize(provider):
        arrived.put_nowait(provider)
        await release.wait()
        return object()

    monkeypatch.setattr(client_registry, "get_client_class_instance_async", initialize)
    first = asyncio.create_task(orchestrator._select_model())
    second = asyncio.create_task(orchestrator._select_model())
    try:
        assert await asyncio.wait_for(arrived.get(), 2) == "first"
        assert await asyncio.wait_for(arrived.get(), 2) == "second"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert orchestrator.model_usage["first"][2] == 0
        assert orchestrator.model_usage["second"][2] == 1
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
