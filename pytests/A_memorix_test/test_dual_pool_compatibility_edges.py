from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from src.A_memorix.core.runtime import sdk_memory_kernel as kernel_module
from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.utils.person_profile_service import PersonProfileService
from src.A_memorix.core.utils.relation_write_service import RelationWriteService
from src.A_memorix.core.utils.runtime_self_check import run_embedding_runtime_self_check
from src.A_memorix.core.utils.summary_importer import SummaryImporter


class _DummyVectorStore:
    def __init__(self, dimension: int = 4) -> None:
        self.dimension = dimension
        self.ids: list[str] = []
        self._deleted_ids: set[str] = set()

    @property
    def num_vectors(self) -> int:
        return sum(item not in self._deleted_ids for item in self.ids)

    def __contains__(self, item: str) -> bool:
        return item in self.ids and item not in self._deleted_ids

    def add(self, vectors: np.ndarray, ids: list[str]) -> int:
        if vectors.shape[1] != self.dimension:
            raise ValueError(f"Dimension mismatch: {vectors.shape[1]} vs {self.dimension}")
        tombstoned = [item for item in ids if item in self._deleted_ids]
        if tombstoned:
            raise ValueError("向量 ID 已被删除，请先调用 restore() 恢复")
        added = 0
        for item in ids:
            if item not in self.ids:
                self.ids.append(item)
                added += 1
        return added

    def delete(self, ids: list[str]) -> int:
        deleted = 0
        for item in ids:
            if item in self.ids and item not in self._deleted_ids:
                self._deleted_ids.add(item)
                deleted += 1
        return deleted

    def restore(self, ids: list[str]) -> int:
        restored = 0
        for item in dict.fromkeys(ids):
            if item in self.ids and item in self._deleted_ids:
                self._deleted_ids.remove(item)
                restored += 1
        return restored

    def has_data(self) -> bool:
        return bool(self.ids)


class _DummyEmbeddingManager:
    def __init__(self, dimension: int = 4) -> None:
        self.default_dimension = dimension
        self.calls: list[Any] = []

    async def _detect_dimension(self) -> int:
        return self.default_dimension

    async def encode(self, text: Any) -> np.ndarray:
        self.calls.append(text)
        if isinstance(text, (list, tuple)):
            return np.ones((len(text), self.default_dimension), dtype=np.float32)
        return np.ones(self.default_dimension, dtype=np.float32)

    async def encode_batch(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.ones((len(texts), self.default_dimension), dtype=np.float32)

    def get_requested_dimension(self) -> int:
        return self.default_dimension


class _DummyMetadataStore:
    def __init__(self) -> None:
        self.entities: list[tuple[str, str]] = []
        self.relations: list[tuple[str, str, str]] = []

    def add_paragraph(self, **kwargs: Any) -> str:
        del kwargs
        return "paragraph-1"

    def enqueue_paragraph_vector_backfill(self, paragraph_hash: str, *, error: str = "") -> None:
        del paragraph_hash, error

    def add_entity(self, name: str, source_paragraph: str = "", **kwargs: Any) -> str:
        del kwargs
        self.entities.append((name, source_paragraph))
        return f"entity-{name}"

    def add_entities_batch(
        self,
        names: list[str],
        source_paragraph: str = "",
        **kwargs: Any,
    ) -> list[str]:
        return [self.add_entity(name, source_paragraph, **kwargs) for name in names]

    def add_relation(self, *, subject: str, predicate: str, obj: str, **kwargs: Any) -> str:
        del kwargs
        self.relations.append((subject, predicate, obj))
        return f"relation-{len(self.relations)}"

    def add_relations_batch(
        self,
        relations: list[tuple[str, str, str]],
        **kwargs: Any,
    ) -> list[str]:
        return [
            self.add_relation(subject=subject, predicate=predicate, obj=obj, **kwargs)
            for subject, predicate, obj in relations
        ]

    def set_relation_vector_state(
        self,
        rel_hash: str,
        state: str,
        error: str | None = None,
        bump_retry: bool = False,
    ) -> None:
        del rel_hash, state, error, bump_retry

    @staticmethod
    def get_relation_status_batch(relation_hashes: list[str]) -> dict[str, dict[str, bool]]:
        return {relation_hash: {"is_inactive": False} for relation_hash in relation_hashes}

    @staticmethod
    def transaction(*, immediate: bool = False):
        del immediate
        return nullcontext()


class _DummyGraphStore:
    def __init__(self) -> None:
        self.nodes: list[list[str]] = []
        self.edges: list[list[tuple[str, str]]] = []

    def add_nodes(self, nodes: list[str]) -> None:
        self.nodes.append(list(nodes))

    def add_edges(self, edges: list[tuple[str, str]], relation_hashes: list[str] | None = None) -> None:
        del relation_hashes
        self.edges.append(list(edges))

    @staticmethod
    def batch_update():
        return nullcontext()


class _DualReadyPlugin:
    def __init__(self) -> None:
        self.enabled = True

    def _dual_vector_pools_enabled(self) -> bool:
        return self.enabled

    @staticmethod
    def get_config(key: str, default: Any = None) -> Any:
        if key == "embedding.fallback.allow_metadata_only_write":
            return True
        return default


@pytest.mark.asyncio
async def test_summary_importer_writes_entities_to_graph_pool_when_dual_ready() -> None:
    metadata_store = _DummyMetadataStore()
    graph_store = _DummyGraphStore()
    single_store = _DummyVectorStore()
    graph_vector_store = _DummyVectorStore()
    embedding_manager = _DummyEmbeddingManager()
    importer = SummaryImporter(
        vector_store=single_store,
        graph_store=graph_store,
        metadata_store=metadata_store,  # type: ignore[arg-type]
        embedding_manager=embedding_manager,  # type: ignore[arg-type]
        plugin_config={
            "plugin_instance": _DualReadyPlugin(),
            "graph_vector_store": graph_vector_store,
            "runtime": {"vector_pools_ready": True},
            "retrieval": {
                "vector_pools": {"mode": "dual"},
                "relation_vectorization": {"enabled": False},
            },
        },
    )

    await importer._execute_import(
        summary="测试用户喜欢蓝色围巾。",
        entities=["测试用户", "蓝色围巾"],
        relations=[],
        stream_id="stream-1",
    )

    assert graph_store.nodes == [["测试用户", "蓝色围巾"]]
    assert metadata_store.entities == [
        ("测试用户", "paragraph-1"),
        ("蓝色围巾", "paragraph-1"),
    ]
    assert set(graph_vector_store.ids) == {
        "entity:entity-测试用户",
        "entity:entity-蓝色围巾",
    }
    assert single_store.ids == ["paragraph-1"]
    assert embedding_manager.calls == [
        "测试用户喜欢蓝色围巾。",
        ["测试用户", "蓝色围巾"],
    ]


@pytest.mark.asyncio
async def test_relation_write_service_batches_metadata_graph_and_vectors() -> None:
    metadata_store = _DummyMetadataStore()
    graph_store = _DummyGraphStore()
    legacy_store = _DummyVectorStore()
    graph_vector_store = _DummyVectorStore()
    embedding_manager = _DummyEmbeddingManager()
    service = RelationWriteService(
        metadata_store=metadata_store,
        graph_store=graph_store,
        vector_store=legacy_store,
        embedding_manager=embedding_manager,
        graph_vector_store=graph_vector_store,
        use_typed_relation_ids=True,
    )

    results = await service.upsert_relations_with_vectors(
        [
            ("Alice", "持有", "地图"),
            ("Bob", "居住于", "广州"),
        ],
        source_paragraph="paragraph-1",
    )

    assert metadata_store.relations == [
        ("Alice", "持有", "地图"),
        ("Bob", "居住于", "广州"),
    ]
    assert graph_store.edges == [[("Alice", "地图"), ("Bob", "广州")]]
    assert graph_vector_store.ids == ["relation:relation-1", "relation:relation-2"]
    assert legacy_store.ids == []
    assert embedding_manager.calls == [
        [
            "Alice 持有 地图\nAlice和地图的关系是持有",
            "Bob 居住于 广州\nBob和广州的关系是居住于",
        ]
    ]
    assert [result.vector_state for result in results] == ["ready", "ready"]


def test_person_profile_fallback_retriever_uses_dual_vector_pools_when_ready() -> None:
    service = PersonProfileService(
        metadata_store=_DummyMetadataStore(),  # type: ignore[arg-type]
        graph_store=_DummyGraphStore(),  # type: ignore[arg-type]
        vector_store=_DummyVectorStore(),  # type: ignore[arg-type]
        paragraph_vector_store=_DummyVectorStore(),  # type: ignore[arg-type]
        graph_vector_store=_DummyVectorStore(),  # type: ignore[arg-type]
        embedding_manager=_DummyEmbeddingManager(),  # type: ignore[arg-type]
        plugin_config={
            "runtime": {"vector_pools_ready": True},
            "retrieval": {
                "vector_pools": {"mode": "dual", "graph_top_k": 32},
                "sparse": {"enabled": False},
            },
        },
    )

    assert service.retriever is not None
    assert service.retriever.config.vector_pools.mode == "dual"
    assert service.retriever.config.vector_pools.graph_top_k == 32


@pytest.mark.asyncio
async def test_runtime_self_check_reports_dual_pool_snapshot() -> None:
    paragraph_store = _DummyVectorStore()
    graph_store = _DummyVectorStore()
    paragraph_store.add(np.ones((1, 4), dtype=np.float32), ["paragraph-1"])
    graph_store.add(np.ones((1, 4), dtype=np.float32), ["entity:entity-1"])

    report = await run_embedding_runtime_self_check(
        config={
            "embedding": {"dimension": 4},
            "runtime": {"vector_pools_ready": True},
            "retrieval": {"vector_pools": {"mode": "dual"}},
        },
        vector_store=_DummyVectorStore(),
        paragraph_vector_store=paragraph_store,
        graph_vector_store=graph_store,
        embedding_manager=_DummyEmbeddingManager(),
    )

    assert report["ok"] is True
    assert report["vector_pools"]["configured_mode"] == "dual"
    assert report["vector_pools"]["effective_mode"] == "dual"
    assert report["vector_pools"]["paragraph_pool"]["num_vectors"] == 1
    assert report["vector_pools"]["graph_pool"]["num_vectors"] == 1


@pytest.mark.asyncio
async def test_runtime_admin_config_exposes_vector_pool_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _DummyEmbeddingManager(dimension=4)
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kwargs: fake_embedding_manager)

    data_dir = tmp_path / "a_memorix_data"
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config={
            "storage": {"data_dir": str(data_dir.resolve())},
            "advanced": {"enable_auto_save": False},
            "embedding": {
                "dimension": 4,
                "paragraph_vector_backfill": {"enabled": False},
            },
            "retrieval": {
                "vector_pools": {"mode": "dual"},
                "relation_vectorization": {"enabled": False},
                "sparse": {"enabled": False},
                "enable_ppr": False,
            },
        },
    )

    try:
        config = await kernel.memory_runtime_admin(action="get_config")

        assert config["vector_pools_ready"] is False
        assert config["vector_pools_effective_mode"] == "single"
        assert config["vector_pools"]["configured_mode"] == "dual"
        assert config["vector_pools"]["paragraph_pool"]["available"] is True
    finally:
        await kernel.shutdown()
