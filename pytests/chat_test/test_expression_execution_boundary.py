"""慢表达工作不能阻塞真实插件 RPC 握手，取消也不能提前释放索引写锁。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import json
import ssl
import threading

import numpy as np
import pytest

from src.chat.replyer import expression_vector_index as index_module
from src.chat.replyer.expression_vector_index import (
    CLUSTER_STATE_STABLE,
    ExpressionEmbeddingProfile,
    ExpressionHistoryBackfillSelection,
    ExpressionVectorIndex,
    ExpressionVectorIndexUpsertItem,
    expression_fingerprint,
)
from src.plugin_runtime.host.rpc_server import RPCServer
from src.plugin_runtime.runner.rpc_client import RPCClient
from src.plugin_runtime.transport.tcp import TCPTransportServer
from src.services.embedding_service import EmbeddingServiceClient


async def handshake_while_blocked(monkeypatch, owner, name, operation):
    """把实际同步步骤暂停到握手成功之后，而不是仅断言调用了 to_thread。"""
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    original = getattr(owner, name)

    def slow_step(*args, **kwargs):
        assert threading.get_ident() != main_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "同步步骤阻止了握手或测试未释放工作线程"
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, slow_step)
    transport = TCPTransportServer()
    server = RPCServer(transport, host_version="test")
    await server.start()
    client = RPCClient(transport.get_address(), server.session_token)
    work = asyncio.create_task(operation())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert await asyncio.wait_for(client.connect_and_handshake(), 2)
        assert not work.done(), "必须在慢表达步骤尚未结束时完成握手"
        # 构建过程中重连也必须成功，不能只靠调整首次启动顺序通过测试。
        await client.disconnect()
        client = RPCClient(transport.get_address(), server.session_token)
        assert await asyncio.wait_for(client.connect_and_handshake(), 2)
        assert not work.done(), "必须在慢表达步骤尚未结束时完成重连"
    finally:
        release.set()
        try:
            await work
        finally:
            await client.disconnect()
            await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["certificates", "sdk_resources"])
async def test_cold_openai_client_allows_handshake_and_reconnect(monkeypatch, stage):
    from src.config.config import config_manager
    from src.config.model_configs import APIProvider
    from src.llm_models.model_client.base_client import ClientProviderRegistration, ClientRegistry
    from src.llm_models.model_client.openai_client import OpenaiClient

    monkeypatch.setattr(config_manager, "register_reload_callback", lambda callback: None)
    registry = ClientRegistry()
    provider = APIProvider(name="cold-openai", base_url="https://example.invalid", auth_type="none")
    registry.register_provider(ClientProviderRegistration("openai", OpenaiClient, builtin=True))
    # 本测试不发送模型请求；证书读取用可控阻塞替代，避免依赖机器证书数量和磁盘速度。
    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", lambda *args, **kwargs: None)
    owner, name = (
        (ssl.SSLContext, "load_verify_locations")
        if stage == "certificates"
        else (OpenaiClient, "prepare_sync_resources")
    )

    async def initialize():
        client = await registry.get_client_class_instance_async(provider)
        try:
            assert "embeddings" in client.client.__dict__
            assert "responses" in client.client.__dict__
        finally:
            await client.client.close()

    await handshake_while_blocked(monkeypatch, owner, name, initialize)


@pytest.fixture
def index_case(tmp_path, monkeypatch):
    profile = ExpressionEmbeddingProfile(
        marker="test-profile",
        model_name="test-model",
        model_identifier="test-model",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    index = ExpressionVectorIndex()
    path = tmp_path / "index.json"
    item = ExpressionVectorIndexUpsertItem(
        id=1,
        situation="情景",
        style="表达",
        count=1,
        session_id="session",
        checked=False,
        modified_by="",
    )
    monkeypatch.setattr(index, "get_current_embedding_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(
        index,
        "_load_current_expression_fingerprints",
        lambda: {
            item.id: expression_fingerprint(item.id, item.situation, item.style),
        },
    )
    monkeypatch.setattr(
        EmbeddingServiceClient,
        "embed_texts",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    embedding=[1.0, 0.0],
                    model_name=profile.model_name,
                    model_identifier=profile.model_identifier,
                    api_provider=profile.api_provider,
                )
            ]
        ),
    )
    return index, path, item, profile


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["normalize", "serialize_failure", "cluster", "load_index"])
async def test_index_work_allows_handshake_and_reconnect(index_case, monkeypatch, stage):
    index, path, item, profile = index_case
    await index.upsert_expressions(index_path=str(path), expressions=[item])

    if stage == "normalize":
        owner, name = index_module, "l2_normalize"
    elif stage == "serialize_failure":
        # 已有索引的一批向量全失败分支：过去只把写文件搬走，JSON 仍在主循环生成。
        monkeypatch.setattr(
            index,
            "_embed_expression_items",
            AsyncMock(
                return_value=(
                    [],
                    np.empty((0, 2), dtype=np.float32),
                    [(item, ValueError("invalid embedding"))],
                )
            ),
        )
        owner, name = index_module.json, "dumps"
    elif stage == "cluster":
        owner, name = index, "_rebuild_profile_arrays"
    else:
        owner, name = index_module, "_load_npz_array"

    async def update():
        await index.upsert_expressions(index_path=str(path), expressions=[item], force_recluster=stage == "cluster")

    await handshake_while_blocked(monkeypatch, owner, name, update)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["sample_count"] == 1
    assert payload["embedding_profile_marker"] == profile.marker


@pytest.mark.asyncio
async def test_history_backfill_finalization_allows_handshake_and_reconnect(index_case, monkeypatch):
    from src.config.config import global_config

    index, path, item, profile = index_case
    monkeypatch.setattr(global_config.expression, "use_vector_expression", True)
    batches = iter(
        [
            ExpressionHistoryBackfillSelection(items=[item], deferred_count=0, isolated_count=0),
            ExpressionHistoryBackfillSelection(items=[], deferred_count=0, isolated_count=0),
            ExpressionHistoryBackfillSelection(items=[], deferred_count=0, isolated_count=0),
        ]
    )
    monkeypatch.setattr(index, "_load_history_backfill_items", lambda **kwargs: next(batches))
    monkeypatch.setattr(index, "_calculate_history_backfill_interval", lambda **kwargs: 0)
    await handshake_while_blocked(
        monkeypatch,
        index,
        "_finalize_bootstrap",
        lambda: index._run_history_backfill_loop(index_path=str(path)),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cluster_maintenance"]["state"] == CLUSTER_STATE_STABLE


@pytest.mark.asyncio
async def test_cancelled_update_keeps_write_lock_until_worker_finishes(index_case, monkeypatch):
    index, path, item, profile = index_case
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    original = index._commit_expression_batch

    def blocked_commit(**kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(index, "_commit_expression_batch", blocked_commit)
    task = asyncio.create_task(index.upsert_expressions(index_path=str(path), expressions=[item]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # 多次取消也不能提前放开索引写锁。
        await asyncio.sleep(0)
        assert index._update_lock.locked()
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not index._update_lock.locked()
    assert json.loads(path.read_text(encoding="utf-8"))["sample_count"] == 1


@pytest.mark.asyncio
async def test_config_change_during_index_build_does_not_publish_old_generation(index_case, monkeypatch):
    index, path, item, profile = index_case
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    original = index._write_index_files

    def blocked_write(**kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(index, "_write_index_files", blocked_write)
    task = asyncio.create_task(index.upsert_expressions(index_path=str(path), expressions=[item]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        index.request_history_backfill(index_path=str(path), enabled=False)
    finally:
        release.set()
    assert await task is None
    assert not path.exists()
