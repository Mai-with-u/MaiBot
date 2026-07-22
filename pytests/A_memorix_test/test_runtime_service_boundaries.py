from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import asyncio
import numpy as np
import pytest

from src.A_memorix.core.retrieval import RetrievalResult
from src.A_memorix.core.runtime import sdk_memory_kernel as kernel_module
from src.A_memorix.core.runtime.sdk_memory_kernel import KernelSearchRequest, SDKMemoryKernel
from src.A_memorix.core.runtime.services import memory_maintenance_service
from src.A_memorix.core.runtime.services.v5_admin_service import MemoryV5AdminService
from src.A_memorix.core.storage.graph_store import GraphStore
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.memory_lifecycle_policy import RelationLifecyclePolicy


def test_v5_status_skips_relation_removed_after_hash_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    class MetadataStoreStub:
        def get_memory_status_summary(self, now: float) -> Dict[str, int]:
            del now
            return {}

        def get_relation_status_batch(self, hashes: List[str]) -> Dict[str, Dict[str, Any]]:
            assert hashes == ["removed-relation"]
            return {}

        def get_relation(self, hash_value: str) -> None:
            assert hash_value == "removed-relation"
            return None

    kernel = SimpleNamespace(
        metadata_store=MetadataStoreStub(),
        _cfg=lambda key, default: default,
        _last_maintenance_at=None,
        _maintenance_service=SimpleNamespace(
            _relation_lifecycle_policy=lambda: RelationLifecyclePolicy(),
        ),
    )
    service = MemoryV5AdminService(kernel)
    monkeypatch.setattr(service, "_resolve_relation_hashes", lambda target: ["removed-relation"])
    monkeypatch.setattr(service, "_resolve_deleted_relation_hashes", lambda target: [])

    result = service._memory_v5_status(target="removed-relation")

    assert result["items"] == []
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_graph_admin_delete_node_uses_kernel_patched_delete_action(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    kernel.graph_store = object()  # type: ignore[assignment]
    calls: list[dict[str, Any]] = []

    async def fake_initialize() -> None:
        return None

    async def fake_execute_delete_action(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "success": True,
            "deleted_entity_count": 1,
            "deleted_count": 1,
            "marker": "kernel-patched",
        }

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_execute_delete_action", fake_execute_delete_action)

    result = await kernel.memory_graph_admin(action="delete_node", name="Alice")

    assert calls == [
        {
            "mode": "entity",
            "selector": {"query": "Alice"},
            "requested_by": "memory_graph_admin",
            "reason": "graph_delete_node",
        }
    ]
    assert result["success"] is True
    assert result["deleted"] is True
    assert result["marker"] == "kernel-patched"


def test_graph_admin_rename_rehashes_relations_and_invalidates_vectors(tmp_path: Path) -> None:
    metadata_store = MetadataStore(data_dir=tmp_path / "metadata")
    metadata_store.connect()
    try:
        paragraph_hash = metadata_store.add_paragraph("Alice 喜欢 Bob", source="test")
        old_entity_hash = metadata_store.add_entity("Alice", vector_index=12, source_paragraph=paragraph_hash)
        metadata_store.add_entity("Bob", source_paragraph=paragraph_hash)
        old_relation_hash = metadata_store.add_relation(
            "Alice",
            "喜欢",
            "Bob",
            vector_index=34,
            source_paragraph=paragraph_hash,
        )
        deleted_vector_ids: list[str] = []
        vector_store = SimpleNamespace(
            delete=lambda ids: deleted_vector_ids.extend(ids) or len(ids),
        )
        kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
        kernel.metadata_store = metadata_store
        kernel.graph_store = GraphStore(data_dir=tmp_path / "graph")
        kernel.vector_store = vector_store  # type: ignore[assignment]
        kernel._persist = lambda: None  # type: ignore[method-assign]

        result = kernel._rename_node("Alice", "Carol")

        assert result["success"] is True
        new_relation_hash = metadata_store.compute_relation_hash("Carol", "喜欢", "Bob")
        assert metadata_store.get_relation(old_relation_hash) is None
        relation = metadata_store.get_relation(new_relation_hash)
        assert relation is not None
        assert relation["subject"] == "Carol"
        assert relation["vector_index"] is None
        assert relation["vector_state"] == "none"
        assert metadata_store.get_entity(old_entity_hash) is None
        new_entity = metadata_store.get_entity(result["entity_hash"])
        assert new_entity is not None
        assert new_entity["vector_index"] is None
        paragraph_relations = metadata_store.get_paragraph_relations(paragraph_hash)
        assert [item["hash"] for item in paragraph_relations] == [new_relation_hash]
        assert old_entity_hash in deleted_vector_ids
        assert old_relation_hash in deleted_vector_ids
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_correction_admin_preview_uses_kernel_patched_preview_action(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    captured: dict[str, Any] = {}

    async def fake_initialize() -> None:
        return None

    async def fake_preview_fuzzy_modify_action(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "marker": "kernel-patched"}

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_preview_fuzzy_modify_action", fake_preview_fuzzy_modify_action)

    result = await kernel.memory_correction_admin(
        action="preview",
        request_text="把颜色改成绿色",
        scope="person_profile",
        person_id="person-1",
        limit=3,
        requested_by="pytest",
        reason="boundary-test",
    )

    assert result == {"success": True, "marker": "kernel-patched"}
    assert captured == {
        "request_text": "把颜色改成绿色",
        "scope": "person_profile",
        "person_id": "person-1",
        "person_keyword": "",
        "chat_id": "",
        "limit": 3,
        "requested_by": "pytest",
        "reason": "boundary-test",
    }


@pytest.mark.asyncio
async def test_legacy_fuzzy_modify_admin_alias_uses_correction_admin_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    calls: list[dict[str, Any]] = []

    async def fake_memory_correction_admin(*, action: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"action": action, **kwargs})
        return {"success": True, "marker": "legacy-alias"}

    monkeypatch.setattr(kernel, "memory_correction_admin", fake_memory_correction_admin)

    result = await kernel.memory_fuzzy_modify_admin(action="get", plan_id="fuzzy-1")

    assert result == {"success": True, "marker": "legacy-alias"}
    assert calls == [{"action": "get", "plan_id": "fuzzy-1"}]


@pytest.mark.asyncio
async def test_kernel_delete_admin_compat_methods_delegate_to_delete_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    class FakeDeleteAdminService:
        def _selector_dict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("_selector_dict", args, kwargs))
            return {"selector": args[0]}

        async def _preview_delete_action(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("_preview_delete_action", args, kwargs))
            return {"success": True, "kwargs": kwargs}

    monkeypatch.setattr(kernel, "_delete_admin_service", FakeDeleteAdminService())

    assert kernel._selector_dict({"hashes": ["rel-1"]}) == {"selector": {"hashes": ["rel-1"]}}
    preview = await kernel._preview_delete_action(mode="relation", selector={"hashes": ["rel-1"]})

    assert preview == {"success": True, "kwargs": {"mode": "relation", "selector": {"hashes": ["rel-1"]}}}
    assert calls == [
        ("_selector_dict", ({"hashes": ["rel-1"]},), {}),
        ("_preview_delete_action", (), {"mode": "relation", "selector": {"hashes": ["rel-1"]}}),
    ]


@pytest.mark.asyncio
async def test_kernel_service_compat_methods_delegate_to_bound_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    class FakeFeedbackService:
        def _build_feedback_task_detail(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("feedback.detail", args, kwargs))
            return {"service": "feedback", "args": args}

        async def _process_feedback_task(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("feedback.process", args, kwargs))
            return {"service": "feedback", "kwargs": kwargs}

    class FakeGraphAdminService:
        def _serialize_graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("graph.serialize", args, kwargs))
            return {"service": "graph", "kwargs": kwargs}

    class FakeCorrectionAdminService:
        def _normalize_fuzzy_modify_plan(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("correction.normalize", args, kwargs))
            return {"service": "correction", "args": args}

        async def _apply_fuzzy_modify_plan(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("correction.apply", args, kwargs))
            return {"service": "correction", "kwargs": kwargs}

    monkeypatch.setattr(kernel, "_feedback_service", FakeFeedbackService())
    monkeypatch.setattr(kernel, "_graph_admin_service", FakeGraphAdminService())
    monkeypatch.setattr(kernel, "_correction_admin_service", FakeCorrectionAdminService())

    assert kernel._build_feedback_task_detail({"task_id": "feedback-1"}) == {
        "service": "feedback",
        "args": ({"task_id": "feedback-1"},),
    }
    assert await kernel._process_feedback_task(task_id="feedback-1") == {
        "service": "feedback",
        "kwargs": {"task_id": "feedback-1"},
    }
    assert kernel._serialize_graph(limit=3) == {"service": "graph", "kwargs": {"limit": 3}}
    assert kernel._normalize_fuzzy_modify_plan({"action": "replace"}) == {
        "service": "correction",
        "args": ({"action": "replace"},),
    }
    assert await kernel._apply_fuzzy_modify_plan(plan_id="plan-1") == {
        "service": "correction",
        "kwargs": {"plan_id": "plan-1"},
    }
    assert calls == [
        ("feedback.detail", ({"task_id": "feedback-1"},), {}),
        ("feedback.process", (), {"task_id": "feedback-1"}),
        ("graph.serialize", (), {"limit": 3}),
        ("correction.normalize", ({"action": "replace"},), {}),
        ("correction.apply", (), {"plan_id": "plan-1"}),
    ]


@pytest.mark.asyncio
async def test_kernel_service_compat_methods_remain_instance_patchable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})

    def patched_graph_serializer(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"patched": True, "args": args, "kwargs": kwargs}

    async def patched_feedback_processor(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"patched": True, "args": args, "kwargs": kwargs}

    monkeypatch.setattr(kernel, "_serialize_graph", patched_graph_serializer)
    monkeypatch.setattr(kernel, "_process_feedback_task", patched_feedback_processor)

    assert kernel._serialize_graph(limit=2) == {"patched": True, "args": (), "kwargs": {"limit": 2}}
    assert await kernel._process_feedback_task(task_id="feedback-1") == {
        "patched": True,
        "args": (),
        "kwargs": {"task_id": "feedback-1"},
    }


@pytest.mark.asyncio
async def test_search_memory_uses_kernel_patched_chat_scope_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.retriever = object()
    kernel.episode_retriever = object()  # type: ignore[assignment]
    kernel.aggregate_query_service = object()  # type: ignore[assignment]
    captured: dict[str, Any] = {}

    async def fake_initialize() -> None:
        return None

    async def fake_search_execution_for_chat_scope(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            success=True,
            error="",
            chat_filtered=False,
            results=[
                RetrievalResult(
                    hash_value="paragraph-1",
                    content="当前群聊提到绿色围巾。",
                    score=0.9,
                    result_type="paragraph",
                    source="paragraph_search",
                    metadata={"chat_id": "session-current"},
                )
            ],
        )

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_search_execution_for_chat_scope", fake_search_execution_for_chat_scope)

    result = await kernel.search_memory(
        KernelSearchRequest(
            query="围巾",
            limit=1,
            mode="search",
            chat_id="session-current",
            respect_filter=False,
        )
    )

    assert [item["hash"] for item in result["hits"]] == ["paragraph-1"]
    assert captured["caller"] == "sdk_memory_kernel"
    assert captured["query_type"] == "search"
    assert captured["query"] == "围巾"


@pytest.mark.asyncio
async def test_vector_runtime_service_keeps_saved_original_method_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    original_encode = kernel._encode_and_add_rebuild_vectors
    patched_calls = 0

    async def patched_encode(**kwargs: Any) -> tuple[int, int, str, list[str], list[str]]:
        nonlocal patched_calls
        patched_calls += 1
        return await original_encode(**kwargs)

    monkeypatch.setattr(kernel, "_encode_and_add_rebuild_vectors", patched_encode)

    result = await kernel._vector_runtime_service._encode_and_add_rebuild_vectors(
        items=[("paragraph-1", "用于测试的段落")],
        batch_size=1,
    )

    assert patched_calls == 1
    assert result == (
        0,
        1,
        "vector_runtime_components_missing",
        [],
        ["paragraph-1"],
    )


def test_runtime_config_service_builds_runtime_bundle_from_kernel_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={"runtime": {"existing": True}})
    kernel.vector_store = object()  # type: ignore[assignment]
    kernel.paragraph_vector_store = object()  # type: ignore[assignment]
    kernel.graph_vector_store = object()  # type: ignore[assignment]
    kernel.graph_store = object()  # type: ignore[assignment]
    kernel.metadata_store = object()  # type: ignore[assignment]
    kernel.embedding_manager = object()
    kernel.sparse_index = object()  # type: ignore[assignment]
    kernel.relation_write_service = object()  # type: ignore[assignment]

    monkeypatch.setattr(kernel, "_dual_vector_pools_enabled", lambda: True)

    runtime_config = kernel._runtime_config_service._build_runtime_config()

    assert runtime_config["runtime"] == {"existing": True, "vector_pools_ready": True}
    assert runtime_config["vector_store"] is kernel.vector_store
    assert runtime_config["paragraph_vector_store"] is kernel.paragraph_vector_store
    assert runtime_config["graph_vector_store"] is kernel.graph_vector_store
    assert runtime_config["plugin_instance"] is kernel._runtime_facade


@pytest.mark.asyncio
async def test_runtime_config_service_apply_tuning_uses_kernel_patched_runtime_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={"retrieval": {"old": 1}})
    calls: list[tuple[str, Any]] = []

    def fake_build_search_runtime(**kwargs: Any) -> SimpleNamespace:
        calls.append(("build", kwargs))
        return SimpleNamespace(
            ready=True,
            error="",
            retriever="retriever",
            threshold_filter="threshold",
            sparse_index="sparse",
        )

    def fake_refresh_runtime_dependents(*, preserve_managers: bool = True) -> None:
        calls.append(("refresh", preserve_managers))

    def fake_apply_runtime_sparse_mode() -> None:
        calls.append(("sparse", None))

    monkeypatch.setattr(kernel_module, "build_search_runtime", fake_build_search_runtime)
    monkeypatch.setattr(kernel, "_refresh_runtime_dependents", fake_refresh_runtime_dependents)
    monkeypatch.setattr(kernel, "_apply_runtime_sparse_mode", fake_apply_runtime_sparse_mode)

    result = await kernel._runtime_config_service.apply_retrieval_tuning_profile(
        {"retrieval": {"new": 2}},
        validate=True,
    )

    assert result == {
        "success": True,
        "runtime_rebuilt": True,
        "validation_passed": True,
        "error": "",
    }
    assert kernel.config["retrieval"] == {"old": 1, "new": 2}
    assert kernel.retriever == "retriever"
    assert kernel.threshold_filter == "threshold"
    assert kernel.sparse_index == "sparse"
    assert calls[0][0] == "build"
    assert calls[0][1]["owner_tag"] == "sdk_kernel_tuning_apply"
    assert calls[1:] == [("refresh", True), ("sparse", None)]


@pytest.mark.asyncio
async def test_runtime_config_service_clears_sparse_index_when_rebuild_disables_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.sparse_index = object()  # type: ignore[assignment]
    monkeypatch.setattr(
        kernel_module,
        "build_search_runtime",
        lambda **kwargs: SimpleNamespace(
            ready=True,
            error="",
            retriever="retriever",
            threshold_filter="threshold",
            sparse_index=None,
        ),
    )
    monkeypatch.setattr(kernel, "_refresh_runtime_dependents", lambda **kwargs: None)
    monkeypatch.setattr(kernel, "_apply_runtime_sparse_mode", lambda: None)

    result = await kernel._runtime_config_service.apply_retrieval_tuning_profile({}, validate=True)

    assert result["success"] is True
    assert kernel.sparse_index is None


def test_chat_filter_service_uses_kernel_patched_filter_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config={"filter": {"enabled": True, "mode": "whitelist", "chats": ["stream:allowed"]}},
    )
    calls: list[dict[str, Any]] = []

    def fake_chat_filter_config_allows(
        filter_config: dict[str, Any],
        *,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
        default_when_empty: bool = True,
    ) -> bool:
        calls.append(
            {
                "filter_config": filter_config,
                "stream_id": stream_id,
                "group_id": group_id,
                "user_id": user_id,
                "default_when_empty": default_when_empty,
            }
        )
        return False

    monkeypatch.setattr(kernel, "_chat_filter_config_allows", fake_chat_filter_config_allows)

    result = kernel._chat_filter_service.is_chat_enabled(
        stream_id="stream-1",
        group_id="group-1",
        user_id="user-1",
    )

    assert result is False
    assert calls == [
        {
            "filter_config": {"enabled": True, "mode": "whitelist", "chats": ["stream:allowed"]},
            "stream_id": "stream-1",
            "group_id": "group-1",
            "user_id": "user-1",
            "default_when_empty": True,
        }
    ]


def test_embedding_state_service_uses_kernel_patched_sparse_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    sparse_calls: list[dict[str, Any]] = []

    def fake_apply_runtime_sparse_mode() -> None:
        sparse_calls.append(dict(kernel._embedding_degraded))

    monkeypatch.setattr(kernel, "_apply_runtime_sparse_mode", fake_apply_runtime_sparse_mode)

    kernel._embedding_state_service._set_embedding_degraded(
        active=True,
        reason="probe failed",
        checked_at=12.5,
    )

    assert sparse_calls == [
        {
            "active": True,
            "reason": "probe failed",
            "since": 12.5,
            "last_check": 12.5,
        }
    ]
    assert kernel._embedding_degraded["active"] is True


@pytest.mark.asyncio
async def test_embedding_recover_uses_kernel_patched_recovery_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    calls: list[tuple[str, Any]] = []
    report = {"ok": True, "checked_at": 123.0, "message": "ok"}

    async def fake_refresh_runtime_self_check(*, sample_text: str) -> dict[str, Any]:
        calls.append(("self_check", sample_text))
        return report

    def fake_apply_self_check_dimension_result(actual_report: dict[str, Any]) -> str:
        calls.append(("dimension", actual_report))
        return ""

    def fake_set_embedding_degraded(**kwargs: Any) -> None:
        calls.append(("degraded", kwargs))

    async def fake_run_paragraph_backfill_once(**kwargs: Any) -> dict[str, Any]:
        calls.append(("backfill", kwargs))
        return {"success": True, "processed": 2}

    monkeypatch.setattr(kernel, "_refresh_runtime_self_check", fake_refresh_runtime_self_check)
    monkeypatch.setattr(kernel, "_apply_self_check_dimension_result", fake_apply_self_check_dimension_result)
    monkeypatch.setattr(kernel, "_set_embedding_degraded", fake_set_embedding_degraded)
    monkeypatch.setattr(kernel, "_paragraph_vector_backfill_enabled", lambda: True)
    monkeypatch.setattr(kernel, "_paragraph_vector_backfill_batch_size", lambda: 7)
    monkeypatch.setattr(kernel, "_paragraph_vector_backfill_max_retry", lambda: 3)
    monkeypatch.setattr(kernel, "_run_paragraph_backfill_once", fake_run_paragraph_backfill_once)

    result = await kernel._embedding_state_service._recover_embedding_once(sample_text="probe text")

    assert result == {
        "success": True,
        "recovered": True,
        "report": report,
        "backfill": {"success": True, "processed": 2},
    }
    assert calls == [
        ("self_check", "probe text"),
        ("dimension", report),
        ("degraded", {"active": False, "checked_at": 123.0}),
        (
            "backfill",
            {
                "limit": 7,
                "max_retry": 3,
                "trigger": "embedding_recovered",
            },
        ),
    ]


def test_dual_vector_reload_uses_kernel_patched_state_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVectorStore:
        def __init__(self, name: str) -> None:
            self.name = name
            self.loaded = False
            self.warmed = False

        def has_data(self) -> bool:
            return False

        def load(self) -> None:
            self.loaded = True

        def warmup_index(self, *, force_train: bool = False) -> None:
            self.warmed = bool(force_train)

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    calls: list[tuple[str, Any]] = []
    ready_results = [False, True]

    def fake_dual_vector_ready(*, expected_dimension: int | None = None) -> bool:
        calls.append(("ready", expected_dimension))
        return ready_results.pop(0)

    def fake_try_recover_dual_ready_manifest() -> bool:
        calls.append(("recover", None))
        return True

    def fake_make_vector_store(data_dir: Path, *, dimension: int | None = None) -> FakeVectorStore:
        del dimension
        calls.append(("make", Path(data_dir).name))
        return FakeVectorStore(Path(data_dir).name)

    monkeypatch.setattr(kernel, "_current_embedding_status_dimension", lambda: 8)
    monkeypatch.setattr(kernel, "_dual_vector_ready", fake_dual_vector_ready)
    monkeypatch.setattr(kernel, "_try_recover_dual_ready_manifest", fake_try_recover_dual_ready_manifest)
    monkeypatch.setattr(kernel, "_make_vector_store", fake_make_vector_store)

    result = kernel._dual_vector_state_service._reload_dual_vector_stores_from_disk()

    assert result is True
    assert kernel._dual_vector_pools_ready is True
    assert calls == [
        ("ready", 8),
        ("recover", None),
        ("ready", 8),
        ("make", "paragraph"),
        ("make", "graph"),
    ]
    assert kernel.paragraph_vector_store is not None
    assert kernel.graph_vector_store is not None


def test_dual_manifest_recover_uses_kernel_patched_recovery_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeVectorStore:
        def __init__(self, name: str, num_vectors: int) -> None:
            self.name = name
            self.num_vectors = num_vectors
            self.loaded = False

        def has_data(self) -> bool:
            return True

        def load(self) -> None:
            self.loaded = True

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    kernel.relation_vectors_enabled = True
    paragraph_dir = tmp_path / "vectors" / "paragraph"
    graph_dir = tmp_path / "vectors" / "graph"
    paragraph_dir.mkdir(parents=True)
    graph_dir.mkdir(parents=True)
    calls: list[tuple[str, Any]] = []
    captured_manifest: dict[str, Any] = {}

    def fake_make_vector_store(data_dir: Path, *, dimension: int | None = None) -> FakeVectorStore:
        del dimension
        name = Path(data_dir).name
        calls.append(("make", name))
        return FakeVectorStore(name, 2 if name == "paragraph" else 3)

    def fake_stored_vectors_compatible(store: FakeVectorStore) -> bool:
        calls.append(("compatible", store.name))
        return True

    def fake_count_vector_rebuild_targets() -> dict[str, int]:
        calls.append(("count", None))
        return {"paragraphs": 2, "entities": 1, "relations": 2}

    def fake_write_dual_vector_ready_manifest(**kwargs: Any) -> None:
        calls.append(("write", None))
        captured_manifest.update(kwargs)

    monkeypatch.setattr(kernel, "_dual_vector_pools_config_enabled", lambda: True)
    monkeypatch.setattr(kernel, "_dual_vector_ready_manifest_path", lambda: tmp_path / "vectors" / "dual_ready.json")
    monkeypatch.setattr(kernel, "_paragraph_vector_dir", lambda: paragraph_dir)
    monkeypatch.setattr(kernel, "_graph_vector_dir", lambda: graph_dir)
    monkeypatch.setattr(kernel, "_make_vector_store", fake_make_vector_store)
    monkeypatch.setattr(kernel, "_stored_vectors_compatible_with_current_embedding", fake_stored_vectors_compatible)
    monkeypatch.setattr(kernel, "_count_vector_rebuild_targets", fake_count_vector_rebuild_targets)
    monkeypatch.setattr(kernel, "_write_dual_vector_ready_manifest", fake_write_dual_vector_ready_manifest)

    result = kernel._dual_vector_state_service._try_recover_dual_ready_manifest()

    assert result is True
    assert calls == [
        ("make", "paragraph"),
        ("make", "graph"),
        ("compatible", "paragraph"),
        ("compatible", "graph"),
        ("count", None),
        ("write", None),
    ]
    assert captured_manifest["stats"] == {
        "paragraphs": {"done": 2, "failed": 0},
        "entities": {"done": 1, "failed": 0},
        "relations": {"done": 2, "failed": 0},
    }


def test_dual_migration_start_uses_kernel_patched_state_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    calls: list[str] = []
    kernel._dual_vector_auto_migration_attempted = False
    kernel._background_stopping = False

    def fake_dual_vector_pools_config_enabled() -> bool:
        calls.append("config")
        return True

    def fake_dual_vector_pools_enabled() -> bool:
        calls.append("ready")
        return False

    monkeypatch.setattr(kernel, "_dual_vector_pools_config_enabled", fake_dual_vector_pools_config_enabled)
    monkeypatch.setattr(kernel, "_dual_vector_pools_enabled", fake_dual_vector_pools_enabled)

    result = kernel._dual_vector_migration_service._should_start_dual_vector_auto_migration()

    assert result is True
    assert calls == ["config", "ready"]


def test_dual_migration_update_uses_kernel_patched_progress_normalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel._dual_vector_auto_migration_status.update(
        {
            "running": True,
            "stage": "old",
            "progress": {"total": 10, "processed": 1},
            "updated_at": None,
        }
    )
    calls: list[dict[str, Any]] = []

    def fake_normalize(
        progress: dict[str, Any] | None = None,
        *,
        now: float | None = None,
        explicit_processed: bool = False,
        completed: bool = False,
        success: bool = False,
    ) -> dict[str, Any]:
        calls.append(
            {
                "progress": dict(progress or {}),
                "now": now,
                "explicit_processed": explicit_processed,
                "completed": completed,
                "success": success,
            }
        )
        return {"normalized": True, "processed": int((progress or {}).get("processed", 0))}

    monkeypatch.setattr(kernel, "_normalize_dual_vector_auto_migration_progress", fake_normalize)

    kernel._dual_vector_migration_service._update_dual_vector_auto_migration_stage("paragraphs_done", processed=5)

    assert len(calls) == 1
    assert calls[0]["progress"] == {"total": 10, "processed": 5}
    assert calls[0]["explicit_processed"] is True
    assert calls[0]["completed"] is False
    assert calls[0]["success"] is False
    assert isinstance(calls[0]["now"], float)
    assert kernel._dual_vector_auto_migration_status["stage"] == "paragraphs_done"
    assert kernel._dual_vector_auto_migration_status["progress"] == {"normalized": True, "processed": 5}
    assert kernel._dual_vector_auto_migration_status["updated_at"] == calls[0]["now"]


def test_vector_delete_service_uses_payload_tokens_and_kernel_patched_dual_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVectorStore:
        def __init__(self, name: str) -> None:
            self.name = name
            self.deleted: list[list[str]] = []

        def delete(self, ids: list[str]) -> int:
            self.deleted.append(list(ids))
            return len(ids)

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    legacy_store = FakeVectorStore("legacy")
    paragraph_store = FakeVectorStore("paragraph")
    graph_store = FakeVectorStore("graph")
    kernel.vector_store = legacy_store  # type: ignore[assignment]
    kernel.paragraph_vector_store = paragraph_store  # type: ignore[assignment]
    kernel.graph_vector_store = graph_store  # type: ignore[assignment]
    calls: list[tuple[str, Any]] = []

    def fake_graph_vector_id(item_type: str, hash_value: str) -> str:
        calls.append(("graph_id", (item_type, hash_value)))
        return f"patched:{item_type}:{hash_value}"

    monkeypatch.setattr(kernel, "_dual_vector_pools_enabled", lambda: True)
    monkeypatch.setattr(kernel, "_graph_vector_id", fake_graph_vector_id)

    deleted = kernel._vector_delete_service._delete_vectors_by_type(
        paragraph_hashes=["p1", "p2"],
        entity_hashes=["e1"],
        relation_hashes=["r1", "r2"],
    )

    assert deleted == 10
    assert legacy_store.deleted == [["p1", "p2", "e1", "r1", "r2"]]
    assert paragraph_store.deleted == [["p1", "p2"]]
    assert graph_store.deleted == [["patched:entity:e1", "patched:relation:r1", "patched:relation:r2"]]
    assert calls == [
        ("graph_id", ("entity", "e1")),
        ("graph_id", ("relation", "r1")),
        ("graph_id", ("relation", "r2")),
    ]


def test_runtime_dependency_refresh_relation_write_uses_kernel_patched_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRelationWriteService:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    kernel.graph_store = object()  # type: ignore[assignment]
    kernel.vector_store = object()  # type: ignore[assignment]
    kernel.embedding_manager = object()
    graph_vector_store = object()
    captured: dict[str, Any] = {}
    calls: list[str] = []

    def fake_graph_vector_store() -> object:
        calls.append("graph_vector_store")
        return graph_vector_store

    def fake_dual_vector_pools_enabled() -> bool:
        calls.append("dual_ready")
        return True

    monkeypatch.setattr(kernel_module, "RelationWriteService", FakeRelationWriteService)
    monkeypatch.setattr(kernel, "_graph_vector_store", fake_graph_vector_store)
    monkeypatch.setattr(kernel, "_dual_vector_pools_enabled", fake_dual_vector_pools_enabled)

    kernel._runtime_dependency_service._refresh_relation_write_service()

    assert calls == ["graph_vector_store", "dual_ready"]
    assert captured["metadata_store"] is kernel.metadata_store
    assert captured["graph_store"] is kernel.graph_store
    assert captured["vector_store"] is kernel.vector_store
    assert captured["graph_vector_store"] is graph_vector_store
    assert captured["embedding_manager"] is kernel.embedding_manager
    assert captured["use_typed_relation_ids"] is True


def test_runtime_dependency_persist_uses_kernel_patched_save_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.vector_store = object()  # type: ignore[assignment]
    kernel.paragraph_vector_store = object()  # type: ignore[assignment]
    kernel.graph_vector_store = object()  # type: ignore[assignment]
    saved: list[object] = []
    dual_states = [False, True, True]

    monkeypatch.setattr(kernel, "_vector_rebuild_status", lambda: {"vector_rebuild_required": False})
    monkeypatch.setattr(kernel, "_dual_vector_pools_enabled", lambda: dual_states.pop(0))
    monkeypatch.setattr(kernel, "_save_vector_store", lambda store: saved.append(store))

    kernel._runtime_dependency_service._persist()

    assert saved == [kernel.vector_store, kernel.paragraph_vector_store, kernel.graph_vector_store]


def test_runtime_dependency_refresh_dependents_uses_kernel_patched_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeImportTaskManager:
        def __init__(self, facade: Any) -> None:
            events.append(("ImportTaskManager", {"facade": facade}))

        def is_write_blocked(self) -> bool:
            return False

    class FakeRetrievalTuningManager:
        def __init__(self, facade: Any, *, import_write_blocked_provider: Any) -> None:
            events.append(
                (
                    "RetrievalTuningManager",
                    {
                        "facade": facade,
                        "import_write_blocked_provider": import_write_blocked_provider,
                    },
                )
            )

    def fake_factory(name: str) -> type:
        class FakeDependency:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = dict(kwargs)
                events.append((name, self.kwargs))

        return FakeDependency

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    kernel.graph_store = object()  # type: ignore[assignment]
    kernel.vector_store = object()  # type: ignore[assignment]
    kernel.paragraph_vector_store = object()  # type: ignore[assignment]
    kernel.graph_vector_store = object()  # type: ignore[assignment]
    kernel.embedding_manager = object()
    kernel.sparse_index = object()  # type: ignore[assignment]
    kernel.retriever = object()
    runtime_config = {"runtime": "patched"}
    events: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(kernel, "_build_runtime_config", lambda: runtime_config)
    monkeypatch.setattr(kernel_module, "EpisodeRetrievalService", fake_factory("EpisodeRetrievalService"))
    monkeypatch.setattr(kernel_module, "AggregateQueryService", fake_factory("AggregateQueryService"))
    monkeypatch.setattr(kernel_module, "PersonProfileService", fake_factory("PersonProfileService"))
    monkeypatch.setattr(kernel_module, "EpisodeSegmentationService", fake_factory("EpisodeSegmentationService"))
    monkeypatch.setattr(kernel_module, "EpisodeService", fake_factory("EpisodeService"))
    monkeypatch.setattr(kernel_module, "SummaryImporter", fake_factory("SummaryImporter"))
    monkeypatch.setattr(kernel_module, "ImportTaskManager", FakeImportTaskManager)
    monkeypatch.setattr(kernel_module, "RetrievalTuningManager", FakeRetrievalTuningManager)

    kernel._runtime_dependency_service._refresh_runtime_dependents(preserve_managers=False)

    assert [name for name, _ in events] == [
        "EpisodeRetrievalService",
        "AggregateQueryService",
        "PersonProfileService",
        "EpisodeSegmentationService",
        "EpisodeService",
        "SummaryImporter",
        "ImportTaskManager",
        "RetrievalTuningManager",
    ]
    assert events[0][1]["metadata_store"] is kernel.metadata_store
    assert events[0][1]["retriever"] is kernel.retriever
    assert events[1][1]["plugin_config"] is runtime_config
    assert events[2][1]["paragraph_vector_store"] is kernel.paragraph_vector_store
    assert events[2][1]["graph_vector_store"] is kernel.graph_vector_store
    assert events[4][1]["segmentation_service"] is kernel.episode_segmentation_service
    assert events[5][1]["plugin_config"] is runtime_config
    assert events[6][1]["facade"] is kernel._runtime_facade
    assert events[7][1]["facade"] is kernel._runtime_facade
    assert events[7][1]["import_write_blocked_provider"] == kernel.import_task_manager.is_write_blocked


@pytest.mark.asyncio
async def test_ingest_service_uses_kernel_patched_write_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMetadataStore:
        def __init__(self) -> None:
            self.external_refs: list[dict[str, Any]] = []

        def get_external_memory_ref(self, external_id: str) -> None:
            del external_id
            return None

        def add_paragraph(self, **kwargs: Any) -> str:
            self.paragraph = kwargs
            return "paragraph-1"

        def add_entity(self, *, name: str, source_paragraph: str) -> str:
            return f"entity:{name}:{source_paragraph}"

        def upsert_external_memory_ref(self, **kwargs: Any) -> None:
            self.external_refs.append(kwargs)

    class FakeVectorStore:
        def __init__(self) -> None:
            self.ids: list[str] = []

        def __contains__(self, item: str) -> bool:
            return item in self.ids

        def add(self, vectors: Any = None, ids: list[str] | None = None, **kwargs: Any) -> int:
            del vectors, kwargs
            self.ids.extend(ids or [])
            return len(ids or [])

    class FakeEmbeddingManager:
        async def encode(self, text: Any) -> np.ndarray:
            del text
            return np.ones((1, 4), dtype=np.float32)

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    kernel.vector_store = FakeVectorStore()  # type: ignore[assignment]
    kernel.graph_store = object()  # type: ignore[assignment]
    kernel.embedding_manager = FakeEmbeddingManager()
    kernel.relation_write_service = object()  # type: ignore[assignment]
    original_write = kernel._write_paragraph_vector_or_enqueue
    original_entity = kernel._ensure_entity_vector
    write_calls: list[dict[str, Any]] = []
    entity_calls: list[dict[str, Any]] = []

    async def fake_initialize() -> None:
        return None

    async def patched_write(**kwargs: Any) -> dict[str, Any]:
        write_calls.append(kwargs)
        return await original_write(**kwargs)

    async def patched_entity(entity: dict[str, Any]) -> bool:
        entity_calls.append(entity)
        return await original_entity(entity)

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_persist", lambda *args, **kwargs: None)
    monkeypatch.setattr(kernel, "_write_paragraph_vector_or_enqueue", patched_write)
    monkeypatch.setattr(kernel, "_ensure_entity_vector", patched_entity)

    result = await kernel._ingest_service.ingest_text(
        external_id="external-1",
        source_type="manual",
        text="Alice 喜欢绿茶",
        entities=["Alice"],
    )

    assert result["stored_ids"] == ["paragraph-1"]
    assert write_calls == [
        {
            "paragraph_hash": "paragraph-1",
            "content": "Alice 喜欢绿茶",
            "context": "ingest_text",
        }
    ]
    assert entity_calls == [{"hash": "entity:Alice:paragraph-1", "name": "Alice"}]


@pytest.mark.asyncio
async def test_profile_admin_uses_kernel_patched_evidence_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    kernel.person_profile_service = object()  # type: ignore[assignment]
    captured: dict[str, Any] = {}

    async def fake_initialize() -> None:
        return None

    async def fake_profile_evidence_admin(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "marker": "kernel-patched"}

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_profile_evidence_admin", fake_profile_evidence_admin)

    result = await kernel.memory_profile_admin(
        action="evidence",
        person_id="person-1",
        limit=7,
        force_refresh=True,
    )

    assert result == {"success": True, "marker": "kernel-patched"}
    assert captured == {
        "person_id": "person-1",
        "person_keyword": "",
        "limit": 7,
        "force_refresh": True,
    }


@pytest.mark.asyncio
async def test_episode_admin_uses_kernel_patched_source_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    captured: dict[str, Any] = {}

    async def fake_initialize() -> None:
        return None

    async def fake_process_episode_source_rebuild_batch(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"processed": 2, "failed": 0}

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(
        kernel,
        "process_episode_source_rebuild_batch",
        fake_process_episode_source_rebuild_batch,
    )

    result = await kernel.memory_episode_admin(action="process_sources", limit=3, max_retry=4)

    assert result == {"success": True, "processed": 2, "failed": 0}
    assert captured == {"limit": 3, "max_retry": 4, "max_wait_seconds": 0.0}


@pytest.mark.asyncio
async def test_v5_admin_uses_kernel_patched_relation_action_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMetadataStore:
        def __init__(self) -> None:
            self.operations: list[dict[str, Any]] = []

        def record_v5_operation(self, **kwargs: Any) -> dict[str, Any]:
            self.operations.append(kwargs)
            return {"operation_id": "v5-op-1", **kwargs}

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    resolve_calls: list[str] = []
    action_calls: list[dict[str, Any]] = []

    async def fake_initialize() -> None:
        return None

    def fake_resolve_relation_hashes(target: str) -> list[str]:
        resolve_calls.append(target)
        return ["relation-1"]

    def fake_apply_v5_relation_action(**kwargs: Any) -> dict[str, Any]:
        action_calls.append(kwargs)
        return {"success": True, "detail": "kernel-patched"}

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_resolve_relation_hashes", fake_resolve_relation_hashes)
    monkeypatch.setattr(kernel, "_apply_v5_relation_action", fake_apply_v5_relation_action)

    result = await kernel.memory_v5_admin(
        action="weaken",
        target="Alice",
        strength=2.0,
        reason="boundary-test",
        updated_by="pytest",
    )

    assert result["success"] is True
    assert result["detail"] == "kernel-patched"
    assert resolve_calls == ["Alice"]
    assert action_calls == [{"action": "weaken", "hashes": ["relation-1"], "strength": 2.0}]
    assert kernel.metadata_store.operations == [
        {
            "action": "weaken",
            "target": "Alice",
            "resolved_hashes": ["relation-1"],
            "reason": "boundary-test",
            "updated_by": "pytest",
            "result": {"success": True, "detail": "kernel-patched"},
        }
    ]


@pytest.mark.asyncio
async def test_v5_restore_relation_service_routes_to_durable_outbox_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    calls: list[dict[str, Any]] = []

    async def fake_restore_deleted_relations(
        hashes: list[str],
        *,
        requested_by: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        calls.append(
            {
                "hashes": hashes,
                "requested_by": requested_by,
                "reason": reason,
            }
        )
        return {
            "success": True,
            "operation_id": "restore-test",
            "status": "restored",
            "restored_hashes": ["relation-1"],
            "restored_count": 1,
            "failures": [{"hash": "missing-relation", "error": "relation 不存在"}],
        }

    monkeypatch.setattr(
        kernel._delete_admin_service,
        "restore_deleted_relations",
        fake_restore_deleted_relations,
    )

    result = await kernel._v5_admin_service._restore_relation_hashes(
        ["relation-1", "", "missing-relation"],
        requested_by="pytest",
        reason="outbox-boundary",
    )

    assert result == {
        "success": True,
        "operation_id": "restore-test",
        "status": "restored",
        "restored_hashes": ["relation-1"],
        "restored_count": 1,
        "failures": [{"hash": "missing-relation", "error": "relation 不存在"}],
    }
    assert calls == [
        {
            "hashes": ["relation-1", "", "missing-relation"],
            "requested_by": "pytest",
            "reason": "outbox-boundary",
        }
    ]


@pytest.mark.asyncio
async def test_maintain_memory_uses_v5_service_relation_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    resolve_calls: list[str] = []
    action_calls: list[dict[str, Any]] = []

    async def fake_initialize() -> None:
        return None

    def fake_resolve_relation_hashes(target: str) -> list[str]:
        resolve_calls.append(target)
        return ["relation-1", "relation-2"]

    def fake_apply_v5_relation_action(**kwargs: Any) -> dict[str, Any]:
        action_calls.append(dict(kwargs))
        return {"success": True, "detail": "reinforce 2 条关系"}

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_persist", lambda: None)
    monkeypatch.setattr(kernel, "_resolve_relation_hashes", fake_resolve_relation_hashes)
    monkeypatch.setattr(kernel, "_apply_v5_relation_action", fake_apply_v5_relation_action)

    result = await kernel.maintain_memory(action="reinforce", target="Alice")

    assert result == {"success": True, "detail": "reinforce 2 条关系"}
    assert resolve_calls == ["Alice"]
    assert action_calls == [
        {
            "action": "reinforce",
            "hashes": ["relation-1", "relation-2"],
        }
    ]


@pytest.mark.asyncio
async def test_import_admin_uses_import_tuning_service_manager_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeImportManager:
        def __init__(self) -> None:
            self.limits: list[int] = []

        async def list_tasks(self, *, limit: int) -> list[dict[str, Any]]:
            self.limits.append(limit)
            return [{"task_id": "import-task-1"}]

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    manager = FakeImportManager()
    kernel.import_task_manager = manager  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(kernel, "initialize", fake_initialize)

    result = await kernel.memory_import_admin(action="list", limit=3)

    assert result == {"success": True, "items": [{"task_id": "import-task-1"}], "count": 1}
    assert manager.limits == [3]


@pytest.mark.asyncio
async def test_tuning_admin_uses_import_tuning_service_manager_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTuningManager:
        def __init__(self) -> None:
            self.called = False

        def get_runtime_settings(self) -> dict[str, Any]:
            self.called = True
            return {"enabled": True}

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    manager = FakeTuningManager()
    kernel.retrieval_tuning_manager = manager  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(kernel, "initialize", fake_initialize)

    result = await kernel.memory_tuning_admin(action="settings")

    assert result == {"success": True, "settings": {"enabled": True}}
    assert manager.called is True


def test_memory_stats_uses_kernel_patched_backfill_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMetadataStore:
        def get_statistics(self) -> dict[str, Any]:
            return {
                "paragraph_count": 3,
                "relation_count": 4,
                "stale_paragraph_mark_count": 1,
                "person_profile_refresh_pending_count": 2,
                "person_profile_refresh_failed_count": 5,
            }

        def query(self, sql: str) -> list[dict[str, int]]:
            if "FROM episodes" in sql:
                return [{"c": 6}]
            if "FROM person_profile_snapshots" in sql:
                return [{"c": 7}]
            raise AssertionError(f"unexpected sql: {sql}")

        def get_episode_source_rebuild_summary(self) -> dict[str, Any]:
            return {"counts": {"pending": 9, "running": 10, "failed": 11}}

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    backfill_calls = 0

    def fake_paragraph_vector_backfill_counts() -> dict[str, int]:
        nonlocal backfill_calls
        backfill_calls += 1
        return {"pending": 12, "failed": 13}

    monkeypatch.setattr(kernel, "_paragraph_vector_backfill_counts", fake_paragraph_vector_backfill_counts)

    result = kernel.memory_stats()

    assert result["paragraphs"] == 3
    assert result["relations"] == 4
    assert result["episodes"] == 6
    assert result["profiles"] == 7
    assert "episode_pending" not in result
    assert result["episode_rebuild_pending"] == 30
    assert result["paragraph_vector_backfill_pending"] == 12
    assert result["paragraph_vector_backfill_failed"] == 13
    assert backfill_calls == 1


@pytest.mark.asyncio
async def test_episode_admin_rebuild_uses_kernel_patched_rebuild_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    captured: list[list[str]] = []

    async def fake_initialize() -> None:
        return None

    async def fake_rebuild_episodes_for_sources(sources: list[str]) -> dict[str, Any]:
        captured.append(list(sources))
        return {"rebuilt": 1, "items": [{"source": "source-1"}], "failures": [], "sources": ["source-1"]}

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "rebuild_episodes_for_sources", fake_rebuild_episodes_for_sources)

    result = await kernel.memory_episode_admin(action="rebuild", sources=["source-1"])

    assert result == {
        "success": True,
        "rebuilt": 1,
        "items": [{"source": "source-1"}],
        "failures": [],
        "sources": ["source-1"],
    }
    assert captured == [["source-1"]]


@pytest.mark.asyncio
async def test_episode_admin_reports_superseded_rebuild_as_unfinished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    async def fake_rebuild_episodes_for_sources(sources: list[str]) -> dict[str, Any]:
        assert sources == ["source-1"]
        return {
            "rebuilt": 0,
            "failed": 0,
            "failures": [],
            "unfinished": 1,
            "unfinished_items": [{"source": "source-1", "reason": "superseded"}],
            "sources": ["source-1"],
        }

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "rebuild_episodes_for_sources", fake_rebuild_episodes_for_sources)

    result = await kernel.memory_episode_admin(action="rebuild", sources=["source-1"])

    assert result["success"] is False
    assert result["unfinished"] == 1
    assert result["unfinished_items"] == [{"source": "source-1", "reason": "superseded"}]


@pytest.mark.asyncio
async def test_rebuild_episodes_for_sources_enqueues_and_uses_source_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMetadataStore:
        def __init__(self) -> None:
            self.enqueued: list[dict[str, Any]] = []

        def enqueue_episode_source_rebuild(self, source: str, **kwargs: Any) -> None:
            self.enqueued.append({"source": source, **kwargs})

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    async def fake_process_source_batch(**kwargs: Any) -> dict[str, Any]:
        assert kwargs == {
            "sources": ["source-ok", "source-fail"],
            "limit": 2,
            "max_wait_seconds": 0.0,
        }
        return {
            "rebuilt": 1,
            "items": [{"source": "source-ok", "episode_count": 2}],
            "failures": [{"source": "source-fail", "error": "rebuild failed"}],
        }

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "process_episode_source_rebuild_batch", fake_process_source_batch)

    result = await kernel.rebuild_episodes_for_sources(["source-ok", "source-fail"])

    assert result["rebuilt"] == 1
    assert result["items"] == [{"source": "source-ok", "episode_count": 2}]
    assert result["failures"] == [{"source": "source-fail", "error": "rebuild failed"}]
    assert result["sources"] == ["source-ok", "source-fail"]
    assert kernel.metadata_store.enqueued == [
        {"source": "source-ok", "reason": "episode_admin_rebuild", "debounce_seconds": 0.0},
        {"source": "source-fail", "reason": "episode_admin_rebuild", "debounce_seconds": 0.0},
    ]


@pytest.mark.asyncio
async def test_source_admin_list_uses_metadata_rebuild_block_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMetadataStore:
        def __init__(self) -> None:
            self.checked_sources: list[str] = []

        def get_all_sources(self) -> list[dict[str, Any]]:
            return [
                {"source": "source-a", "count": 1},
                {"source": "source-b", "count": 2},
            ]

        def is_episode_source_query_blocked(self, source: str) -> bool:
            self.checked_sources.append(source)
            return source == "source-b"

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    metadata_store = FakeMetadataStore()
    kernel.metadata_store = metadata_store  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(kernel, "initialize", fake_initialize)

    result = await kernel.memory_source_admin(action="list")

    assert result == {
        "success": True,
        "items": [
            {"source": "source-a", "count": 1, "episode_rebuild_blocked": False},
            {"source": "source-b", "count": 2, "episode_rebuild_blocked": True},
        ],
        "count": 2,
    }
    assert metadata_store.checked_sources == ["source-a", "source-b"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "kwargs", "expected_selector", "expected_reason"),
    [
        (
            "delete",
            {"source": "source-1", "requested_by": "pytest", "reason": "boundary-delete"},
            {"sources": ["source-1"]},
            "boundary-delete",
        ),
        (
            "batch_delete",
            {"sources": ["source-1", "source-2"], "requested_by": "pytest", "reason": "boundary-batch"},
            {"sources": ["source-1", "source-2"]},
            "boundary-batch",
        ),
    ],
)
async def test_source_admin_delete_actions_use_kernel_patched_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    kwargs: dict[str, Any],
    expected_selector: dict[str, list[str]],
    expected_reason: str,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = object()  # type: ignore[assignment]
    captured_delete: list[dict[str, Any]] = []
    manifest_results: list[dict[str, Any]] = []

    async def fake_initialize() -> None:
        return None

    async def fake_execute_delete_action(**delete_kwargs: Any) -> dict[str, Any]:
        captured_delete.append(delete_kwargs)
        return {"success": True, "sources": expected_selector["sources"], "marker": "delete-patched"}

    async def fake_invalidate_import_manifest_for_sources(result: dict[str, Any]) -> None:
        manifest_results.append(result)
        result["manifest_invalidation"] = {"success": True}

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_execute_delete_action", fake_execute_delete_action)
    monkeypatch.setattr(kernel, "_invalidate_import_manifest_for_sources", fake_invalidate_import_manifest_for_sources)

    result = await kernel.memory_source_admin(action=action, **kwargs)

    assert captured_delete == [
        {
            "mode": "source",
            "selector": expected_selector,
            "requested_by": "pytest",
            "reason": expected_reason,
        }
    ]
    assert manifest_results == [result]
    assert result == {
        "success": True,
        "sources": expected_selector["sources"],
        "marker": "delete-patched",
        "manifest_invalidation": {"success": True},
    }


@pytest.mark.asyncio
async def test_episode_source_batch_preserves_processing_error_when_failure_marking_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMetadataStore:
        def __init__(self) -> None:
            self.claimed = False

        def claim_episode_source_rebuild_batch(self, **kwargs: Any) -> list[dict[str, Any]]:
            del kwargs
            if self.claimed:
                return []
            self.claimed = True
            return [
                {
                    "source": "chat_summary:source-1",
                    "lease_token": "lease-1",
                    "claimed_revision": 1,
                    "retry_count": 0,
                }
            ]

        def fail_episode_source_rebuild(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            raise RuntimeError("failure marking failed")

    class FakeEpisodeService:
        @staticmethod
        def generation_signature() -> dict[str, Any]:
            return {"generation": "test"}

        @staticmethod
        def generation_hash(signature: dict[str, Any]) -> str:
            assert signature == {"generation": "test"}
            return "generation-test"

        async def plan_source_rebuild(self, source: str, **kwargs: Any) -> dict[str, Any]:
            del source, kwargs
            raise ValueError("primary episode failure")

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    kernel.episode_service = FakeEpisodeService()  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(kernel, "initialize", fake_initialize)

    result = await kernel._ingest_service.process_episode_source_rebuild_batch()

    assert result["failed"] == 1
    assert result["failures"] == [
        {"source": "chat_summary:source-1", "error": "primary episode failure"}
    ]


@pytest.mark.asyncio
async def test_episode_source_batch_rejects_zero_attempt_budget() -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})

    with pytest.raises(ValueError, match="max_retry 必须至少为1"):
        await kernel._ingest_service.process_episode_source_rebuild_batch(max_retry=0)


@pytest.mark.asyncio
async def test_episode_source_worker_reports_unpublished_cas_result_as_unfinished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMetadataStore:
        def __init__(self) -> None:
            self.claimed = False

        def claim_episode_source_rebuild_batch(self, **kwargs: Any) -> list[dict[str, Any]]:
            del kwargs
            if self.claimed:
                return []
            self.claimed = True
            return [
                {
                    "source": "chat_summary:source-1",
                    "lease_token": "lease-1",
                    "claimed_revision": 1,
                    "retry_count": 0,
                }
            ]

        def publish_episode_source_rebuild(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "source": "chat_summary:source-1",
                "published": False,
                "superseded": True,
                "episode_count": 0,
            }

    class FakeEpisodeService:
        @staticmethod
        def generation_signature() -> dict[str, Any]:
            return {"generation": "test"}

        @staticmethod
        def generation_hash(signature: dict[str, Any]) -> str:
            assert signature == {"generation": "test"}
            return "generation-test"

        async def plan_source_rebuild(self, source: str, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            await asyncio.sleep(0)
            return {
                "source": source,
                "payloads": [],
                "episode_count": 0,
                "fallback_count": 0,
            }

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    kernel.episode_service = FakeEpisodeService()  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(kernel, "initialize", fake_initialize)

    result = await kernel._ingest_service.process_episode_source_rebuild_batch(limit=1)

    assert result["rebuilt"] == 0
    assert result["superseded"] == 1
    assert result["unfinished"] == 1
    assert result["unfinished_items"] == [
        {"source": "chat_summary:source-1", "reason": "superseded"}
    ]


@pytest.mark.asyncio
async def test_episode_source_worker_reports_scoped_source_that_cannot_be_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMetadataStore:
        @staticmethod
        def claim_episode_source_rebuild_batch(**kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs["sources"] == ["chat_summary:source-1"]
            return []

    class FakeEpisodeService:
        @staticmethod
        def generation_signature() -> dict[str, Any]:
            return {"generation": "test"}

        @staticmethod
        def generation_hash(signature: dict[str, Any]) -> str:
            assert signature == {"generation": "test"}
            return "generation-test"

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = FakeMetadataStore()  # type: ignore[assignment]
    kernel.episode_service = FakeEpisodeService()  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(kernel, "initialize", fake_initialize)

    result = await kernel._ingest_service.process_episode_source_rebuild_batch(
        sources=["chat_summary:source-1"],
        limit=1,
    )

    assert result["rebuilt"] == 0
    assert result["unfinished"] == 1
    assert result["unfinished_items"] == [
        {"source": "chat_summary:source-1", "reason": "not_claimed"}
    ]


@pytest.mark.asyncio
async def test_episode_source_worker_heartbeat_prevents_live_lease_reclaim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowEpisodeService:
        @staticmethod
        def generation_signature() -> dict[str, Any]:
            return {"generation": "heartbeat-test"}

        @staticmethod
        def generation_hash(signature: dict[str, Any]) -> str:
            assert signature == {"generation": "heartbeat-test"}
            return "heartbeat-generation"

        async def plan_source_rebuild(self, source: str, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            await asyncio.sleep(1.25)
            return {
                "source": source,
                "payloads": [],
                "episode_count": 0,
                "fallback_count": 0,
                "group_count": 0,
                "paragraph_count": 0,
                "generation_hash": "heartbeat-generation",
            }

    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    source = "chat_summary:heartbeat-worker"
    store.enqueue_episode_source_rebuild(source, reason="heartbeat", debounce_seconds=0.0)
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    kernel.metadata_store = store
    kernel.episode_service = SlowEpisodeService()  # type: ignore[assignment]

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_persist", lambda: None)
    try:
        worker = asyncio.create_task(
            kernel._ingest_service.process_episode_source_rebuild_batch(
                limit=1,
                lease_seconds=1.0,
                max_wait_seconds=0.0,
            )
        )
        await asyncio.sleep(1.05)
        competing_claim = store.claim_episode_source_rebuild_batch(
            generation_hash="heartbeat-generation",
            limit=1,
            lease_seconds=1.0,
            max_wait_seconds=0.0,
        )
        result = await worker

        assert competing_claim == []
        assert result["rebuilt"] == 1
        assert result["unfinished"] == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_maintenance_cycle_uses_kernel_patched_phase_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGraphStore:
        def __init__(self) -> None:
            self.decay_factors: list[float] = []

        def decay(self, factor: float) -> None:
            self.decay_factors.append(factor)

    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config={"memory": {"half_life_hours": 4.0}},
    )
    graph_store = FakeGraphStore()
    kernel.graph_store = graph_store  # type: ignore[assignment]
    kernel.metadata_store = object()  # type: ignore[assignment]
    calls: list[str] = []
    persist_calls = 0

    async def fake_process_freeze_and_prune() -> None:
        calls.append("freeze")

    async def fake_orphan_gc_phase() -> None:
        calls.append("orphan")

    def fake_persist() -> None:
        nonlocal persist_calls
        persist_calls += 1

    monkeypatch.setattr(memory_maintenance_service.time, "time", lambda: 123.0)
    monkeypatch.setattr(kernel, "_process_freeze_and_prune", fake_process_freeze_and_prune)
    monkeypatch.setattr(kernel, "_orphan_gc_phase", fake_orphan_gc_phase)
    monkeypatch.setattr(kernel, "_persist", fake_persist)

    await kernel._run_memory_maintenance_cycle(interval_hours=2.0)

    assert graph_store.decay_factors == []
    assert calls == ["freeze", "orphan"]
    assert kernel._last_maintenance_at == 123.0
    assert persist_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("configured_interval", "expected_sleep"), [(None, 3600.0), (0, 60.0)])
async def test_memory_maintenance_loop_handles_none_without_overwriting_zero(
    monkeypatch: pytest.MonkeyPatch,
    configured_interval: float | None,
    expected_sleep: float,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config={"memory": {"base_decay_interval_hours": configured_interval}},
    )
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        kernel._background_stopping = True

    monkeypatch.setattr(memory_maintenance_service.asyncio, "sleep", fake_sleep)

    await kernel._maintenance_service._memory_maintenance_loop()

    assert sleep_calls == [expected_sleep]


@pytest.mark.asyncio
async def test_memory_maintenance_freeze_prune_uses_delete_coordinator_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGraphStore:
        def __init__(self) -> None:
            self.prune_operations: list[list[tuple[str, str, str]]] = []
            self.save_calls = 0

        def prune_relation_hashes(self, operations: list[tuple[str, str, str]]) -> None:
            self.prune_operations.append(list(operations))

        def save(self) -> None:
            self.save_calls += 1

    class FakeMetadataStore:
        def __init__(self) -> None:
            self.prune_cutoffs: list[float] = []
            self.projection_claimed = False

        def evaluate_due_relation_lifecycles(self, **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
            assert kwargs["now"] == 10_000.0
            return {
                "frozen": [
                    {
                        "hash": "relation-active",
                        "subject": "alice",
                        "object": "bob",
                    }
                ],
                "scheduled": [],
            }

        def get_decay_prune_candidate_rows(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.prune_cutoffs.append(float(kwargs["cutoff_time"]))
            return [
                {
                    "hash": "expired-relation",
                    "subject": "alice",
                    "object": "bob",
                    "expected_lifecycle_revision": 3,
                    "expected_retention_strength": 0.05,
                    "expected_retention_anchor_at": 0.0,
                    "expected_inactive_since": 100.0,
                    "expected_inactive_reason": "decay",
                    "expected_is_inactive": True,
                    "expected_is_permanent": False,
                    "expected_is_pinned": False,
                    "expected_protected_until": None,
                }
            ]

        def claim_relation_graph_projection_jobs(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert int(kwargs["limit"]) > 0
            if self.projection_claimed:
                return []
            self.projection_claimed = True
            return [
                {
                    "relation_hash": "relation-active",
                    "subject": "alice",
                    "object": "bob",
                    "job_revision": 1,
                    "lease_token": "projection-lease",
                }
            ]

        def count_claimable_relation_graph_projection_jobs(self) -> int:
            return 0 if self.projection_claimed else 1

        def authorize_relation_graph_projection_jobs(
            self,
            jobs: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            return [{**jobs[0], "authoritative_active": False}]

        def fail_relation_graph_projection_jobs(self, *args: Any, **kwargs: Any) -> int:
            del args, kwargs
            return 0

        def complete_relation_graph_projection_jobs(self, jobs: list[dict[str, Any]]) -> int:
            return len(jobs)

    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config={"memory": {"prune_threshold": 0.2, "freeze_duration_hours": 2.0}},
    )
    graph_store = FakeGraphStore()
    metadata_store = FakeMetadataStore()
    kernel.graph_store = graph_store  # type: ignore[assignment]
    kernel.metadata_store = metadata_store  # type: ignore[assignment]
    delete_calls: list[dict[str, Any]] = []

    async def fake_execute_delete_action(**kwargs: Any) -> dict[str, Any]:
        delete_calls.append(dict(kwargs))
        return {"success": True}

    monkeypatch.setattr(memory_maintenance_service.time, "time", lambda: 10_000.0)
    monkeypatch.setattr(kernel._delete_admin_service, "_execute_delete_action", fake_execute_delete_action)

    await kernel._process_freeze_and_prune()

    assert metadata_store.prune_cutoffs == [2_800.0]
    assert graph_store.prune_operations == [
        [("alice", "bob", "relation-active")],
    ]
    assert graph_store.save_calls == 1
    assert delete_calls == [
        {
            "mode": "relation",
            "selector": {
                "hashes": ["expired-relation"],
                "expected_relation_states": {
                    "expired-relation": {
                        "expected_lifecycle_revision": 3,
                        "expected_retention_strength": 0.05,
                        "expected_retention_anchor_at": 0.0,
                        "expected_inactive_since": 100.0,
                        "expected_inactive_reason": "decay",
                        "expected_is_inactive": True,
                        "expected_is_permanent": False,
                        "expected_is_pinned": False,
                        "expected_protected_until": None,
                    }
                },
            },
            "requested_by": "memory_lifecycle",
            "reason": "lifecycle_decay_archive",
        }
    ]


@pytest.mark.asyncio
async def test_memory_maintenance_orphan_gc_uses_delete_coordinator_boundary() -> None:
    class FakeGraphStore:
        def get_isolated_nodes(self, *, include_inactive: bool) -> list[str]:
            assert include_inactive is True
            return ["orphan-node"]

    class FakeMetadataStore:
        def __init__(self) -> None:
            self.entity_gc_requests: list[dict[str, Any]] = []
            self.expiration_limits: list[int] = []

        def get_entity_gc_candidates(self, isolated: list[str], *, retention_seconds: float) -> list[str]:
            self.entity_gc_requests.append(
                {
                    "isolated": list(isolated),
                    "retention_seconds": retention_seconds,
                }
            )
            return ["entity-soft-delete"]

        def get_expired_paragraph_hashes(self, *, limit: int) -> list[str]:
            self.expiration_limits.append(limit)
            return ["paragraph-soft-delete"]

    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config={
            "memory": {
                "orphan": {
                    "enable_soft_delete": True,
                    "entity_retention_days": 2.0,
                    "sweep_grace_hours": 4.0,
                }
            }
        },
    )
    graph_store = FakeGraphStore()
    metadata_store = FakeMetadataStore()
    kernel.graph_store = graph_store  # type: ignore[assignment]
    kernel.metadata_store = metadata_store  # type: ignore[assignment]
    delete_calls: list[dict[str, Any]] = []
    purge_calls: list[dict[str, Any]] = []

    async def fake_execute_delete_action(**kwargs: Any) -> dict[str, Any]:
        delete_calls.append(kwargs)
        return {"success": True}

    async def fake_purge_deleted_memory(**kwargs: Any) -> dict[str, Any]:
        purge_calls.append(kwargs)
        return {"success": True}

    kernel._delete_admin_service = SimpleNamespace(  # type: ignore[assignment]
        _execute_delete_action=fake_execute_delete_action,
        _purge_deleted_memory=fake_purge_deleted_memory,
    )

    await kernel._orphan_gc_phase()

    assert metadata_store.entity_gc_requests == [
        {
            "isolated": ["orphan-node"],
            "retention_seconds": 172_800.0,
        }
    ]
    assert metadata_store.expiration_limits == [1000]
    assert delete_calls == [
        {
            "mode": "entity",
            "selector": {"hashes": ["entity-soft-delete"]},
            "requested_by": "memory_maintenance",
            "reason": "entity_orphan_expired",
        },
        {
            "mode": "paragraph",
            "selector": {"hashes": ["paragraph-soft-delete"]},
            "requested_by": "memory_maintenance",
            "reason": "paragraph_explicit_expiration",
        },
    ]
    assert purge_calls == [{"grace_hours": 4.0, "limit": 1000}]


@pytest.mark.asyncio
async def test_background_start_uses_kernel_patched_task_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    registrations: list[tuple[str, Any]] = []

    def fake_ensure_background_task(name: str, factory: Any) -> None:
        registrations.append((name, factory))

    monkeypatch.setattr(kernel, "_ensure_background_task", fake_ensure_background_task)
    monkeypatch.setattr(kernel, "_should_start_dual_vector_auto_migration", lambda: True)

    await kernel._start_background_tasks()

    assert kernel._background_stopping is False
    assert [name for name, _ in registrations] == [
        "auto_save",
        "episode_materialization",
        "embedding_probe",
        "paragraph_vector_backfill",
        "vector_index_training",
        "memory_maintenance",
        "storage_cleanup",
        "person_profile_refresh",
        "person_profile_refresh_queue",
        "feedback_correction",
        "feedback_correction_reconcile",
        "dual_vector_auto_migration",
    ]
    assert all(callable(factory) for _, factory in registrations)


@pytest.mark.asyncio
async def test_runtime_vector_training_uses_hidden_threshold_and_persists_trained_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVectorStore:
        def __init__(self, count: int) -> None:
            self.count = count
            self.trained = False
            self.thresholds: list[int] = []
            self.warmup_calls = 0

        def needs_training(self, runtime_threshold: int) -> bool:
            self.thresholds.append(runtime_threshold)
            return not self.trained and self.count >= runtime_threshold

        def warmup_index(self, *, force_train: bool) -> dict[str, Any]:
            assert force_train is True
            self.warmup_calls += 1
            self.trained = True
            return {
                "ok": True,
                "trained": True,
                "bin_count": self.count,
                "duration_ms": 1.25,
                "error": None,
            }

    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config={"embedding": {"runtime_train_threshold": 256}},
    )
    store = FakeVectorStore(count=255)
    kernel.vector_store = store  # type: ignore[assignment]
    saved: list[FakeVectorStore] = []
    monkeypatch.setattr(kernel, "_dual_vector_pools_enabled", lambda: False)
    monkeypatch.setattr(kernel, "_save_vector_store", lambda value: saved.append(value))

    assert await kernel._train_runtime_vector_indexes_once() == {}

    store.count = 256
    summary = await kernel._train_runtime_vector_indexes_once()

    assert summary["single"]["trained"] is True
    assert store.thresholds == [256, 256]
    assert store.warmup_calls == 1
    assert saved == [store]


@pytest.mark.asyncio
async def test_runtime_vector_training_targets_effective_dual_pools_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVectorStore:
        def __init__(self, name: str, ready: bool) -> None:
            self.name = name
            self.ready = ready
            self.warmup_calls = 0

        def needs_training(self, runtime_threshold: int) -> bool:
            assert runtime_threshold == 256
            return self.ready

        def warmup_index(self, *, force_train: bool) -> dict[str, Any]:
            assert force_train is True
            self.warmup_calls += 1
            return {
                "ok": True,
                "trained": True,
                "bin_count": 256,
                "duration_ms": 1.0,
                "error": None,
            }

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    legacy_store = FakeVectorStore("legacy", ready=True)
    paragraph_store = FakeVectorStore("paragraph", ready=True)
    graph_store = FakeVectorStore("graph", ready=False)
    kernel.vector_store = legacy_store  # type: ignore[assignment]
    kernel.paragraph_vector_store = paragraph_store  # type: ignore[assignment]
    kernel.graph_vector_store = graph_store  # type: ignore[assignment]
    saved: list[FakeVectorStore] = []
    monkeypatch.setattr(kernel, "_dual_vector_pools_enabled", lambda: True)
    monkeypatch.setattr(kernel, "_save_vector_store", lambda value: saved.append(value))

    summary = await kernel._train_runtime_vector_indexes_once()

    assert set(summary) == {"paragraph"}
    assert legacy_store.warmup_calls == 0
    assert paragraph_store.warmup_calls == 1
    assert graph_store.warmup_calls == 0
    assert saved == [paragraph_store]


@pytest.mark.asyncio
async def test_background_stop_cancels_tasks_and_clears_registry() -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    cancelled: list[str] = []

    async def wait_forever() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append("sample")
            raise

    task = asyncio.create_task(wait_forever(), name="A_Memorix.sample")
    kernel._background_tasks["sample"] = task
    await asyncio.sleep(0)

    await kernel._stop_background_tasks()

    assert kernel._background_stopping is True
    assert kernel._background_tasks == {}
    assert cancelled == ["sample"]


def test_search_hit_processing_uses_kernel_patched_filter_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    calls: list[str] = []

    def fake_filter_episode_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls.append("episode")
        return [{**hit, "episode": True} for hit in hits]

    def fake_filter_active_relation_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls.append("active")
        return [{**hit, "active": True} for hit in hits]

    def fake_filter_current_effective_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls.append("effective")
        return [{**hit, "effective": True} for hit in hits]

    monkeypatch.setattr(kernel, "_filter_episode_hits", fake_filter_episode_hits)
    monkeypatch.setattr(kernel, "_filter_active_relation_hits", fake_filter_active_relation_hits)
    monkeypatch.setattr(kernel, "_filter_current_effective_hits", fake_filter_current_effective_hits)

    result = kernel._filter_user_visible_hits([{"hash": "hit-1", "type": "paragraph"}])

    assert calls == ["episode", "active", "effective"]
    assert result == [
        {
            "hash": "hit-1",
            "type": "paragraph",
            "episode": True,
            "active": True,
            "effective": True,
        }
    ]


@pytest.mark.asyncio
async def test_request_dedup_service_shares_inflight_request() -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def executor() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"success": True}

    first_task = asyncio.create_task(kernel._request_dedup_service.execute_request_with_dedup("same-request", executor))
    await started.wait()
    second_task = asyncio.create_task(
        kernel._request_dedup_service.execute_request_with_dedup("same-request", executor)
    )
    await asyncio.sleep(0)
    release.set()

    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert first_result == (False, {"success": True})
    assert second_result == (True, {"success": True})
    assert calls == 1
    assert kernel._request_dedup_tasks == {}


@pytest.mark.asyncio
async def test_request_dedup_waiter_cancellation_does_not_cancel_shared_request() -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    started = asyncio.Event()
    release = asyncio.Event()

    async def executor() -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"success": True}

    owner = asyncio.create_task(kernel._request_dedup_service.execute_request_with_dedup("same-request", executor))
    await started.wait()
    waiter = asyncio.create_task(kernel._request_dedup_service.execute_request_with_dedup("same-request", executor))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert "same-request" in kernel._request_dedup_tasks
    release.set()
    assert await owner == (False, {"success": True})
    await asyncio.sleep(0)
    assert kernel._request_dedup_tasks == {}


@pytest.mark.asyncio
async def test_summary_service_uses_payload_source_and_kernel_patched_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSummaryImporter:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def import_from_stream(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(
                success=True,
                detail="ok",
                paragraph_hash="summary-hash",
                source="",
            )

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    importer = FakeSummaryImporter()
    initialize_calls = 0
    persist_calls = 0

    async def fake_initialize() -> None:
        nonlocal initialize_calls
        initialize_calls += 1

    def fake_persist() -> None:
        nonlocal persist_calls
        persist_calls += 1

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_persist", fake_persist)
    kernel.summary_importer = importer  # type: ignore[assignment]

    result = await kernel._summary_service.summarize_chat_stream(
        chat_id="session-1",
        context_length=8,
        include_personality=True,
        time_end=123.0,
        metadata={"kind": "chat_summary"},
    )

    assert initialize_calls == 1
    assert importer.calls == [
        {
            "stream_id": "session-1",
            "context_length": 8,
            "include_personality": True,
            "time_end": 123.0,
            "metadata": {"kind": "chat_summary"},
        }
    ]
    assert persist_calls == 1
    assert result == {
        "success": True,
        "detail": "ok",
        "episode_source": "chat_summary:session-1",
        "stored_ids": ["summary-hash"],
    }
