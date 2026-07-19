from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncio
import pytest

from src.A_memorix.core.runtime import sdk_memory_kernel as kernel_module
from src.A_memorix.core.runtime.sdk_memory_kernel import KernelSearchRequest, SDKMemoryKernel
from src.A_memorix.core.runtime.services import memory_maintenance_service, memory_search_service
from src.A_memorix.core.utils.search_execution_service import SearchExecutionResult


def _assert_in_order(events: list[str], expected: list[str]) -> None:
    cursor = 0
    for event in events:
        if cursor < len(expected) and event == expected[cursor]:
            cursor += 1
    assert cursor == len(expected), events


@pytest.mark.asyncio
async def test_runtime_lifecycle_initialize_preserves_startup_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeEmbeddingManager:
        pass

    class FakeVectorStore:
        def __init__(self, name: str) -> None:
            self.name = name

        def has_data(self) -> bool:
            events.append(f"{self.name}:has_data")
            return self.name == "vectors"

        def load(self) -> None:
            events.append(f"{self.name}:load")

        def warmup_index(self, *, force_train: bool) -> None:
            events.append(f"{self.name}:warmup:{force_train}")

    class FakeGraphStore:
        def __init__(self, **kwargs: Any) -> None:
            events.append(f"graph_store:{kwargs['matrix_format']}")
            self.num_nodes = 0
            self.num_edges = 0

        def has_data(self) -> bool:
            events.append("graph_has_data")
            return True

        def load(self) -> None:
            events.append("graph_load")

        def clear(self) -> None:
            events.append("graph_clear")

        def save(self) -> None:
            events.append("graph_save")

    class FakeMetadataStore:
        def __init__(self, **kwargs: Any) -> None:
            events.append("metadata_store")

        def connect(self) -> None:
            events.append("metadata_connect")

        def close(self) -> None:
            events.append("metadata_close")

        def query(self, sql: str) -> list[dict[str, Any]]:
            del sql
            return []

        def reset_relation_graph_projection_leases(self) -> int:
            return 0

        def count_claimable_relation_graph_projection_jobs(self) -> int:
            return 0

        def claim_relation_graph_projection_jobs(
            self,
            *,
            limit: int,
            lease_seconds: float,
        ) -> list[dict[str, Any]]:
            del limit, lease_seconds
            return []

    class FakeSparseBM25Config:
        def __init__(self, **kwargs: Any) -> None:
            events.append("sparse_config")
            self.enabled = bool(kwargs.get("enabled", False))

    class FakeSparseBM25Index:
        def __init__(self, *, metadata_store: Any, config: FakeSparseBM25Config) -> None:
            events.append("sparse_index")
            self.config = config

        def warmup(self) -> dict[str, Any]:
            events.append("sparse_warmup")
            return {"ok": True, "backend": "fake", "doc_count": 1, "duration_ms": 0.0}

    class FakeImportTaskManager:
        def __init__(self, facade: Any) -> None:
            events.append("import_task_manager")

        def is_write_blocked(self) -> bool:
            return False

    class FakeRetrievalTuningManager:
        def __init__(self, facade: Any, *, import_write_blocked_provider: Any) -> None:
            events.append("retrieval_tuning_manager")

    monkeypatch.setattr(kernel_module, "run_startup_format_migration", lambda data_dir: events.append("migration"))
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: events.append("embedding_adapter") or FakeEmbeddingManager(),
    )
    monkeypatch.setattr(kernel_module, "GraphStore", FakeGraphStore)
    monkeypatch.setattr(kernel_module, "MetadataStore", FakeMetadataStore)
    monkeypatch.setattr(kernel_module, "SparseBM25Config", FakeSparseBM25Config)
    monkeypatch.setattr(kernel_module, "SparseBM25Index", FakeSparseBM25Index)
    monkeypatch.setattr(
        kernel_module,
        "build_search_runtime",
        lambda **kwargs: (
            events.append("build_runtime")
            or SimpleNamespace(
                ready=True,
                error="",
                retriever="runtime-retriever",
                threshold_filter="runtime-threshold",
                sparse_index="runtime-sparse-index",
            )
        ),
    )
    monkeypatch.setattr(kernel_module, "ImportTaskManager", FakeImportTaskManager)
    monkeypatch.setattr(kernel_module, "RetrievalTuningManager", FakeRetrievalTuningManager)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={
            "storage": {"data_dir": str(tmp_path / "memory")},
            "embedding": {"dimension": 512},
            "graph": {"sparse_matrix_format": "csc"},
            "retrieval": {"sparse": {"enabled": True}},
        },
    )
    monkeypatch.setattr(kernel, "_stored_vector_dimension", lambda: 128)
    monkeypatch.setattr(
        kernel,
        "_make_vector_store",
        lambda data_dir, *, dimension=None: (
            events.append(f"make_vector_store:{Path(data_dir).name}:{dimension}")
            or FakeVectorStore(Path(data_dir).name)
        ),
    )
    monkeypatch.setattr(kernel, "_dual_vector_pools_config_enabled", lambda: False)
    monkeypatch.setattr(kernel, "_refresh_relation_write_service", lambda: events.append("refresh_relation_write"))
    monkeypatch.setattr(kernel, "_apply_runtime_sparse_mode", lambda: events.append("apply_sparse_mode"))
    monkeypatch.setattr(
        kernel,
        "_refresh_runtime_dependents",
        lambda *, preserve_managers=True: events.append(f"refresh_runtime_dependents:{preserve_managers}"),
    )
    monkeypatch.setattr(kernel, "_mark_startup_self_check_deferred", lambda: events.append("startup_deferred"))

    async def fake_start_background_tasks() -> None:
        events.append("background_start")

    monkeypatch.setattr(kernel, "_start_background_tasks", fake_start_background_tasks)

    original_projection_reconcile = (
        memory_maintenance_service.MemoryMaintenanceService._reconcile_relation_graph_projection_jobs
    )

    def fail_startup_projection(*args: Any, **kwargs: Any) -> dict[str, int]:
        del args, kwargs
        events.append("projection_reconcile_failed")
        raise OSError("injected startup projection failure")

    monkeypatch.setattr(
        memory_maintenance_service.MemoryMaintenanceService,
        "_reconcile_relation_graph_projection_jobs",
        fail_startup_projection,
    )
    with pytest.raises(OSError, match="injected startup projection failure"):
        await kernel.initialize()
    assert kernel._initialized is False
    assert "projection_reconcile_failed" in events
    assert "sparse_config" not in events

    monkeypatch.setattr(
        memory_maintenance_service.MemoryMaintenanceService,
        "_reconcile_relation_graph_projection_jobs",
        original_projection_reconcile,
    )
    events.clear()

    await kernel.initialize()

    assert kernel._initialized is True
    assert kernel.embedding_dimension == 128
    assert kernel.retriever == "runtime-retriever"
    assert kernel.threshold_filter == "runtime-threshold"
    assert kernel.sparse_index == "runtime-sparse-index"
    _assert_in_order(
        events,
        [
            "migration",
            "embedding_adapter",
            "metadata_connect",
            "sparse_warmup",
            "vectors:load",
            "vectors:warmup:True",
            "refresh_relation_write",
            "build_runtime",
            "apply_sparse_mode",
            "refresh_runtime_dependents:True",
            "import_task_manager",
            "retrieval_tuning_manager",
            "startup_deferred",
            "background_start",
        ],
    )

    events.clear()
    await kernel.initialize()

    assert events == ["apply_sparse_mode", "background_start"]
    kernel.import_task_manager = None
    kernel.retrieval_tuning_manager = None
    monkeypatch.setattr(kernel, "_persist", lambda: None)
    await kernel.shutdown()


@pytest.mark.asyncio
async def test_runtime_lifecycle_shutdown_preserves_cleanup_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeManager:
        def __init__(self, name: str) -> None:
            self.name = name

        async def shutdown(self) -> None:
            events.append(f"{self.name}_shutdown")

    class FakeMetadataStore:
        def close(self) -> None:
            events.append("metadata_close")

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={"storage": {"data_dir": str(tmp_path / "memory")}},
    )
    kernel._runtime_writer_lock.acquire()
    kernel.import_task_manager = FakeManager("import")  # type: ignore[assignment]
    kernel.retrieval_tuning_manager = FakeManager("tuning")  # type: ignore[assignment]
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    kernel._initialized = True
    kernel._request_dedup_tasks = {"request": object()}  # type: ignore[assignment]
    kernel._runtime_facade._runtime_self_check_report = {"status": "stale"}
    kernel._background_tasks = {"task": object()}  # type: ignore[assignment]
    kernel._active_person_timestamps = {"person-1": 1.0}
    kernel._embedding_degraded = {"active": True, "reason": "test", "since": 1.0, "last_check": 2.0}

    async def fake_stop_background_tasks() -> None:
        events.append("stop_background")

    monkeypatch.setattr(kernel, "_stop_background_tasks", fake_stop_background_tasks)
    monkeypatch.setattr(kernel, "_persist", lambda: events.append("persist"))

    await kernel.shutdown()

    assert events == ["stop_background", "import_shutdown", "tuning_shutdown", "persist", "metadata_close"]
    assert kernel._initialized is False
    assert kernel._request_dedup_tasks == {}
    assert kernel._runtime_facade._runtime_self_check_report == {}
    assert kernel._background_tasks == {}
    assert kernel._active_person_timestamps == {}
    assert kernel._embedding_degraded == {
        "active": False,
        "reason": "",
        "since": None,
        "last_check": None,
    }


def test_runtime_lifecycle_close_rejects_initialized_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeMetadataStore:
        def close(self) -> None:
            events.append("metadata_close")

    kernel = SDKMemoryKernel(plugin_root=tmp_path, config={})
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    kernel._initialized = True
    monkeypatch.setattr(kernel, "_persist", lambda: events.append("persist"))

    with pytest.raises(RuntimeError, match=r"await shutdown\(\)"):
        kernel.close()

    assert events == []
    assert kernel._initialized is True


@pytest.mark.asyncio
async def test_data_directory_allows_only_one_active_sdk_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"storage": {"data_dir": str(tmp_path / "shared-memory")}}
    first = SDKMemoryKernel(plugin_root=tmp_path, config=config)
    second = SDKMemoryKernel(plugin_root=tmp_path, config=config)

    async def initialize_first() -> None:
        first._initialized = True

    async def initialize_second() -> None:
        second._initialized = True

    monkeypatch.setattr(
        first,
        "_initialize_with_writer_lock",
        initialize_first,
        raising=False,
    )
    monkeypatch.setattr(
        second,
        "_initialize_with_writer_lock",
        initialize_second,
        raising=False,
    )

    await first.initialize()
    assert first._runtime_writer_lock.held is True
    with pytest.raises(RuntimeError, match="已有活动写者"):
        await second.initialize()
    assert second._initialized is False
    assert second._runtime_writer_lock.held is False

    await first.shutdown()
    await second.initialize()
    assert second._runtime_writer_lock.held is True
    await second.shutdown()


@pytest.mark.asyncio
async def test_concurrent_initialize_constructs_runtime_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={"storage": {"data_dir": str(tmp_path / "memory")}},
    )
    construction_count = 0
    refresh_count = 0

    async def initialize_once() -> None:
        nonlocal construction_count, refresh_count
        if kernel._initialized:
            refresh_count += 1
            return
        construction_count += 1
        await asyncio.sleep(0.01)
        kernel._initialized = True

    monkeypatch.setattr(
        kernel,
        "_initialize_with_writer_lock",
        initialize_once,
        raising=False,
    )

    await asyncio.gather(kernel.initialize(), kernel.initialize())

    assert construction_count == 1
    assert refresh_count == 1
    assert kernel._runtime_writer_lock.held is True
    await kernel.shutdown()


@pytest.mark.asyncio
async def test_failed_initialize_discards_writable_references_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"storage": {"data_dir": str(tmp_path / "shared-memory")}}
    failed = SDKMemoryKernel(plugin_root=tmp_path, config=config)
    replacement = SDKMemoryKernel(plugin_root=tmp_path, config=config)
    metadata_close_calls = 0
    graph_save_calls = 0

    class FakeMetadataStore:
        def close(self) -> None:
            nonlocal metadata_close_calls
            metadata_close_calls += 1

    class FakeGraphStore:
        def save(self) -> None:
            nonlocal graph_save_calls
            graph_save_calls += 1

    async def fail_initialization() -> None:
        failed.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
        failed.graph_store = FakeGraphStore()  # type: ignore[assignment]
        raise OSError("injected initialization failure")

    async def initialize_replacement() -> None:
        replacement._initialized = True

    monkeypatch.setattr(
        failed,
        "_initialize_with_writer_lock",
        fail_initialization,
        raising=False,
    )
    monkeypatch.setattr(
        replacement,
        "_initialize_with_writer_lock",
        initialize_replacement,
        raising=False,
    )

    with pytest.raises(OSError, match="injected initialization failure"):
        await failed.initialize()

    assert metadata_close_calls == 1
    assert graph_save_calls == 0
    assert failed.metadata_store is None
    assert failed.graph_store is None
    assert failed._runtime_writer_lock.held is False
    failed.close()
    assert graph_save_calls == 0

    await replacement.initialize()
    await replacement.shutdown()


@pytest.mark.asyncio
async def test_persist_failure_invalidates_old_writer_before_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"storage": {"data_dir": str(tmp_path / "shared-memory")}}
    kernel = SDKMemoryKernel(plugin_root=tmp_path, config=config)
    successor = SDKMemoryKernel(plugin_root=tmp_path, config=config)
    metadata_close_calls = 0

    class FakeMetadataStore:
        def close(self) -> None:
            nonlocal metadata_close_calls
            metadata_close_calls += 1

    kernel._runtime_writer_lock.acquire()
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    kernel.graph_store = object()  # type: ignore[assignment]
    kernel._initialized = True

    def fail_persist() -> None:
        raise OSError("injected persist failure")

    monkeypatch.setattr(kernel, "_persist", fail_persist)

    with pytest.raises(OSError, match="injected persist failure"):
        await kernel.shutdown()

    assert metadata_close_calls == 1
    assert kernel._initialized is False
    assert kernel.metadata_store is None
    assert kernel.graph_store is None
    assert kernel.vector_store is None
    assert kernel._runtime_writer_lock.held is False
    kernel.close()

    successor._runtime_writer_lock.acquire()
    assert successor._runtime_writer_lock.held is True
    successor._runtime_writer_lock.release()


@pytest.mark.asyncio
async def test_shutdown_waits_for_initialize_and_close_fails_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={"storage": {"data_dir": str(tmp_path / "memory")}},
    )
    initialize_started = asyncio.Event()
    allow_initialize = asyncio.Event()
    events: list[str] = []

    async def paused_initialize() -> None:
        events.append("initialize_started")
        initialize_started.set()
        await allow_initialize.wait()
        kernel._initialized = True
        events.append("initialize_finished")

    async def stop_background_tasks() -> None:
        events.append("shutdown_started")

    monkeypatch.setattr(
        kernel,
        "_initialize_with_writer_lock",
        paused_initialize,
        raising=False,
    )
    monkeypatch.setattr(kernel, "_stop_background_tasks", stop_background_tasks)

    initialize_task = asyncio.create_task(kernel.initialize())
    await initialize_started.wait()
    with pytest.raises(RuntimeError, match="正在初始化或异步关闭"):
        kernel.close()

    shutdown_task = asyncio.create_task(kernel.shutdown())
    await asyncio.sleep(0)
    assert shutdown_task.done() is False
    assert events == ["initialize_started"]

    allow_initialize.set()
    await asyncio.gather(initialize_task, shutdown_task)

    assert events == [
        "initialize_started",
        "initialize_finished",
        "shutdown_started",
    ]
    assert kernel._runtime_writer_lock.held is False
    assert kernel._initialized is False


@pytest.mark.asyncio
async def test_plugin_config_update_awaits_kernel_shutdown() -> None:
    from src.A_memorix.plugin import AMemorixPlugin

    events: list[str] = []

    class FakeKernel:
        async def shutdown(self) -> None:
            events.append("shutdown")

        def close(self) -> None:
            raise AssertionError("配置更新不应同步关闭运行时")

    plugin = object.__new__(AMemorixPlugin)
    plugin._plugin_config = {"old": True}
    plugin._kernel = FakeKernel()  # type: ignore[assignment]
    plugin._kernel_lock = asyncio.Lock()

    await plugin.on_config_update("self", {"new": True}, "test-version")

    assert events == ["shutdown"]
    assert plugin._plugin_config == {"new": True}
    assert plugin._kernel is None


@pytest.mark.asyncio
async def test_plugin_concurrent_get_kernel_initializes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.A_memorix import plugin as plugin_module

    initialize_started = asyncio.Event()
    allow_initialize = asyncio.Event()
    created_kernels: list[Any] = []

    class FakeKernel:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            created_kernels.append(self)

        async def initialize(self) -> None:
            initialize_started.set()
            await allow_initialize.wait()

    monkeypatch.setattr(plugin_module, "SDKMemoryKernel", FakeKernel)
    plugin = plugin_module.AMemorixPlugin()

    first_task = asyncio.create_task(plugin._get_kernel())
    await initialize_started.wait()
    second_task = asyncio.create_task(plugin._get_kernel())
    await asyncio.sleep(0)

    assert len(created_kernels) == 1

    allow_initialize.set()
    first_kernel, second_kernel = await asyncio.gather(first_task, second_task)

    assert first_kernel is second_kernel
    assert created_kernels == [first_kernel]


@pytest.mark.asyncio
async def test_search_execution_once_preserves_request_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_execute(**kwargs: Any) -> SearchExecutionResult:
        captured.update(kwargs)
        request = kwargs["request"]
        return SearchExecutionResult(
            success=True,
            query_type=request.query_type,
            query=request.query,
            top_k=request.top_k or 0,
            time_from=request.time_from,
            time_to=request.time_to,
            person=request.person,
            source=request.source,
            results=[],
        )

    monkeypatch.setattr(memory_search_service.SearchExecutionService, "execute", fake_execute)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={"retrieval": {"enable_ppr": False}},
    )
    kernel.retriever = object()
    kernel.threshold_filter = object()
    request = KernelSearchRequest(
        query="绿茶",
        chat_id="session-1",
        group_id="group-1",
        user_id="user-1",
        person_id="person-1",
    )

    result = await kernel._search_execution_once(
        caller="boundary-test",
        query_type="time",
        query="绿茶",
        top_k=7,
        request=request,
        plugin_config={"memory": {"enabled": True}},
        source="chat_summary:session-1",
        time_from="2026-01-01",
        time_to="2026-01-02",
        enforce_chat_filter=True,
    )

    execution_request = captured["request"]
    assert captured["retriever"] is kernel.retriever
    assert captured["threshold_filter"] is kernel.threshold_filter
    assert captured["plugin_config"] == {"memory": {"enabled": True}}
    assert captured["enforce_chat_filter"] is True
    assert "reinforce_access" not in captured
    assert execution_request.caller == "boundary-test"
    assert execution_request.stream_id == "session-1"
    assert execution_request.group_id == "group-1"
    assert execution_request.user_id == "user-1"
    assert execution_request.query_type == "time"
    assert execution_request.query == "绿茶"
    assert execution_request.top_k == 7
    assert execution_request.time_from == "2026-01-01"
    assert execution_request.time_to == "2026-01-02"
    assert execution_request.person == "person-1"
    assert execution_request.source == "chat_summary:session-1"
    assert execution_request.use_threshold is True
    assert execution_request.enable_ppr is False
    assert result.success is True
    assert result.query_type == "time"
