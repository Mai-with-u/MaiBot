from pathlib import Path
from types import SimpleNamespace

import asyncio
import numpy as np
import pytest

from src.A_memorix.core.strategies.base import ChunkContext, KnowledgeType, ProcessedChunk, SourceInfo
from src.A_memorix.core.strategies.factual import FactualStrategy
from src.A_memorix.core.strategies.narrative import NarrativeStrategy
from src.A_memorix.core.storage.knowledge_types import ImportStrategy
from src.A_memorix.core.utils.web_import_manager import (
    ImportChunkRecord,
    ImportFileRecord,
    ImportTaskManager,
    ImportTaskRecord,
)


class _DummyMetadataStore:
    def __init__(self) -> None:
        self.paragraphs: list[dict[str, object]] = []
        self.entities: list[str] = []
        self.relations: list[tuple[str, str, str]] = []
        self.paragraph_backfills: list[tuple[str, str]] = []
        self.relation_vector_states: list[tuple[str, str, str | None, bool]] = []

    def add_paragraph(self, **kwargs):
        self.paragraphs.append(dict(kwargs))
        return f"paragraph-{len(self.paragraphs)}"

    def add_entity(self, *, name: str, source_paragraph: str = "") -> str:
        del source_paragraph
        self.entities.append(name)
        return f"entity-{name}"

    def add_relation(self, *, subject: str, predicate: str, obj: str, **kwargs) -> str:
        del kwargs
        self.relations.append((subject, predicate, obj))
        return f"relation-{len(self.relations)}"

    def set_relation_vector_state(
        self,
        rel_hash: str,
        state: str,
        error: str | None = None,
        bump_retry: bool = False,
    ) -> None:
        self.relation_vector_states.append((rel_hash, state, error, bump_retry))

    def enqueue_paragraph_vector_backfill(self, paragraph_hash: str, *, error: str = "") -> None:
        self.paragraph_backfills.append((paragraph_hash, error))

    def get_live_paragraphs_by_source(self, source: str):
        return [
            paragraph
            for paragraph in self.paragraphs
            if paragraph.get("source") == source and not paragraph.get("is_deleted")
        ]


class _DummyGraphStore:
    def __init__(self) -> None:
        self.nodes: list[list[str]] = []
        self.edges: list[list[tuple[str, str]]] = []

    def add_nodes(self, nodes):
        self.nodes.append(list(nodes))

    def add_edges(self, edges, relation_hashes=None):
        del relation_hashes
        self.edges.append(list(edges))


class _DummyVectorStore:
    def __init__(self) -> None:
        self.dimension = 4
        self.ids: list[str] = []
        self.add_count = 0
        self.save_count = 0
        self.load_count = 0

    def __contains__(self, item: str) -> bool:
        return item in self.ids

    def add(self, vectors, ids):
        if vectors.shape[1] != self.dimension:
            raise ValueError(f"Dimension mismatch: {vectors.shape[1]} vs {self.dimension}")
        added = 0
        for item in ids:
            if item in self.ids:
                continue
            self.ids.append(item)
            added += 1
        self.add_count += added
        return added

    def save(self) -> None:
        self.save_count += 1

    def load(self) -> None:
        self.load_count += 1

    def has_data(self) -> bool:
        return bool(self.ids)


class _DummyEmbeddingManager:
    def __init__(self, *, delay: float = 0.0, fail_for: str = "", dimension: int = 4) -> None:
        self.delay = delay
        self.fail_for = fail_for
        self.dimension = dimension
        self.inflight = 0
        self.max_inflight = 0
        self.calls: list[str] = []

    async def encode(self, text: str) -> np.ndarray:
        self.calls.append(text)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail_for and self.fail_for in text:
                raise RuntimeError("embedding failed")
        finally:
            self.inflight -= 1
        return np.ones(self.dimension, dtype=np.float32)


def _build_manager(
    *,
    embedding_manager: _DummyEmbeddingManager | None = None,
    relation_vectorization_enabled: bool = False,
    vector_pool_mode: str = "single",
) -> tuple[ImportTaskManager, _DummyMetadataStore]:
    metadata_store = _DummyMetadataStore()
    config = {
        "retrieval.relation_vectorization": {
            "enabled": relation_vectorization_enabled,
            "write_on_import": relation_vectorization_enabled,
        },
        "retrieval.vector_pools.mode": vector_pool_mode,
    }
    graph_store = _DummyGraphStore()
    legacy_vector_store = _DummyVectorStore()
    paragraph_vector_store = _DummyVectorStore()
    graph_vector_store = _DummyVectorStore()
    plugin = SimpleNamespace(
        metadata_store=metadata_store,
        graph_store=graph_store,
        vector_store=legacy_vector_store,
        paragraph_vector_store=paragraph_vector_store,
        graph_vector_store=graph_vector_store,
        embedding_manager=embedding_manager or _DummyEmbeddingManager(),
        relation_write_service=None,
        get_config=lambda key, default=None: config.get(key, default),
        _is_embedding_degraded=lambda: False,
        _allow_metadata_only_write=lambda: True,
    )
    manager = ImportTaskManager(plugin)
    return manager, metadata_store


def _build_progress_task(task_id: str, total_chunks: int = 2) -> ImportTaskRecord:
    file_record = ImportFileRecord(
        file_id="file-1",
        name="demo.txt",
        source_kind="paste",
        input_mode="text",
        total_chunks=total_chunks,
        chunks=[
            ImportChunkRecord(chunk_id=f"chunk-{index}", index=index, chunk_type="text")
            for index in range(total_chunks)
        ],
    )
    return ImportTaskRecord(task_id=task_id, source="paste", params={}, files=[file_record])


def _build_chunk(data) -> ProcessedChunk:
    return ProcessedChunk(
        type=KnowledgeType.FACTUAL,
        source=SourceInfo(file="demo.txt", offset_start=0, offset_end=4),
        chunk=ChunkContext(chunk_id="chunk-1", index=0, text="Alice 持有地图"),
        data=data,
    )


def _test_manifest_path(name: str) -> Path:
    path = Path("temp") / "web_import_manager_tests" / name
    if path.exists():
        path.unlink()
    return path


def test_import_params_include_configurable_chunk_windows() -> None:
    manager, _ = _build_manager()

    params = manager._normalize_common_import_params({}, default_dedupe="content_hash")

    assert params["narrative_window_size"] == 1600
    assert params["narrative_overlap"] == 400
    assert params["factual_target_size"] == 1200

    customized = manager._normalize_common_import_params(
        {
            "narrative_window_size": 2400,
            "narrative_overlap": 600,
            "factual_target_size": 1400,
        },
        default_dedupe="content_hash",
    )

    assert customized["narrative_window_size"] == 2400
    assert customized["narrative_overlap"] == 600
    assert customized["factual_target_size"] == 1400


def test_import_strategy_uses_configurable_chunk_windows() -> None:
    manager, _ = _build_manager()

    narrative = manager._instantiate_strategy(
        "demo.txt",
        strategy=ImportStrategy.NARRATIVE,
        import_params={"narrative_window_size": 2400, "narrative_overlap": 600},
    )
    factual = manager._instantiate_strategy(
        "demo.txt",
        strategy=ImportStrategy.FACTUAL,
        import_params={"factual_target_size": 1400},
    )

    assert isinstance(narrative, NarrativeStrategy)
    assert narrative.window_size == 2400
    assert narrative.overlap == 600
    assert isinstance(factual, FactualStrategy)
    assert factual.target_size == 1400


def test_narrative_split_progresses_with_high_overlap_and_newline_backoff() -> None:
    manager, _ = _build_manager()
    narrative = manager._instantiate_strategy(
        "demo.txt",
        strategy=ImportStrategy.NARRATIVE,
        import_params={"narrative_window_size": 200, "narrative_overlap": 199},
    )
    assert isinstance(narrative, NarrativeStrategy)

    text = "第一段内容" * 30 + "\n" + "第二段内容" * 30 + "\n" + "第三段内容" * 30

    chunks = narrative.split(text)
    offsets = [chunk.source.offset_start for chunk in chunks]

    assert chunks
    assert len(chunks) < len(text)
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets)


def test_manifest_hit_requires_existing_live_source() -> None:
    manager, metadata_store = _build_manager()
    manager._manifest_path = _test_manifest_path("manifest_hit.json")
    manager._manifest_cache = None
    file_record = ImportFileRecord(file_id="file-1", name="demo.txt", source_kind="paste", input_mode="text")
    content_hash = "abc123"
    manager._save_manifest(
        {
            "hash:abc123": {
                "hash": content_hash,
                "imported": True,
                "name": "demo.txt",
                "source_kind": "paste",
            }
        }
    )

    assert manager._is_manifest_hit(file_record, content_hash, "content_hash") is False
    assert manager._load_manifest() == {}

    metadata_store.paragraphs.append({"source": "web_import:demo.txt", "is_deleted": 0})
    manager._save_manifest(
        {
            "hash:abc123": {
                "hash": content_hash,
                "imported": True,
                "name": "demo.txt",
                "source_kind": "paste",
            }
        }
    )

    assert manager._is_manifest_hit(file_record, content_hash, "content_hash") is True


@pytest.mark.asyncio
async def test_invalidate_manifest_for_sources_matches_recorded_imported_sources() -> None:
    manager, _ = _build_manager()
    manager._manifest_path = _test_manifest_path("invalidate_sources.json")
    manager._manifest_cache = None
    manager._save_manifest(
        {
            "hash:abc123": {
                "hash": "abc123",
                "imported": True,
                "name": "custom.json",
                "source_kind": "upload",
                "sources": ["custom:knowledge"],
            },
            "hash:def456": {
                "hash": "def456",
                "imported": True,
                "name": "other.txt",
                "source_kind": "paste",
            },
        }
    )

    result = await manager.invalidate_manifest_for_sources(["custom:knowledge"])

    assert result["removed_count"] == 1
    assert result["removed_keys"] == ["hash:abc123"]
    assert "hash:def456" in manager._load_manifest()


@pytest.mark.asyncio
async def test_persist_processed_chunk_rejects_non_object_before_paragraph_write() -> None:
    manager, metadata_store = _build_manager()
    file_record = SimpleNamespace(source_path="", source_kind="paste", name="demo.txt")

    with pytest.raises(ValueError, match="分块抽取结果 必须返回 JSON 对象"):
        await manager._persist_processed_chunk(file_record, _build_chunk(["bad"]))

    assert metadata_store.paragraphs == []


@pytest.mark.asyncio
async def test_chunk_terminal_progress_uses_successful_chunks_only() -> None:
    manager, _ = _build_manager()

    task = _build_progress_task("task-fail-then-complete")
    manager._tasks[task.task_id] = task

    await manager._set_chunk_failed(task.task_id, "file-1", "chunk-0", "boom")
    await manager._set_chunk_completed(task.task_id, "file-1", "chunk-1")

    file_record = task.files[0]
    assert file_record.done_chunks == 1
    assert file_record.failed_chunks == 1
    assert file_record.progress == pytest.approx(0.5)
    assert task.progress == pytest.approx(0.5)

    reverse_task = _build_progress_task("task-complete-then-fail")
    manager._tasks[reverse_task.task_id] = reverse_task

    await manager._set_chunk_completed(reverse_task.task_id, "file-1", "chunk-0")
    await manager._set_chunk_failed(reverse_task.task_id, "file-1", "chunk-1", "boom")

    reverse_file = reverse_task.files[0]
    assert reverse_file.done_chunks == 1
    assert reverse_file.failed_chunks == 1
    assert reverse_file.progress == pytest.approx(0.5)
    assert reverse_task.progress == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_cancelled_chunks_do_not_increase_file_progress() -> None:
    manager, _ = _build_manager()
    task = _build_progress_task("task-cancelled-progress", total_chunks=3)
    manager._tasks[task.task_id] = task

    await manager._set_chunk_completed(task.task_id, "file-1", "chunk-0")
    await manager._set_chunk_cancelled(task.task_id, "file-1", "chunk-1", "任务已取消")

    file_record = task.files[0]
    assert file_record.done_chunks == 1
    assert file_record.cancelled_chunks == 1
    assert file_record.progress == pytest.approx(1 / 3)
    assert task.progress == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_persist_processed_chunk_skips_invalid_nested_items() -> None:
    manager, metadata_store = _build_manager()
    file_record = SimpleNamespace(source_path="", source_kind="paste", name="demo.txt")

    await manager._persist_processed_chunk(
        file_record,
        _build_chunk(
            {
                "triples": [{"subject": "Alice", "predicate": "持有", "object": "地图"}, ["bad"]],
                "relations": [{"subject": "Alice", "predicate": "", "object": "地图"}],
                "entities": ["Alice", {"name": "地图"}, ["bad"]],
            }
        ),
    )

    assert len(metadata_store.paragraphs) == 1
    assert set(metadata_store.entities) >= {"Alice", "地图"}
    assert metadata_store.relations == [("Alice", "持有", "地图")]


@pytest.mark.asyncio
async def test_persist_processed_chunk_writes_chat_id_metadata() -> None:
    manager, metadata_store = _build_manager()
    file_record = SimpleNamespace(source_path="", source_kind="paste", name="demo.txt")

    await manager._persist_processed_chunk(
        file_record,
        _build_chunk({"entities": ["Alice"]}),
        metadata={"chat_id": "session-1"},
    )

    assert metadata_store.paragraphs[0]["metadata"] == {"chat_id": "session-1"}
    assert metadata_store.paragraphs[0]["source"] == "web_import:demo.txt"


@pytest.mark.asyncio
async def test_persist_processed_chunk_does_not_hold_storage_lock_during_embedding() -> None:
    embedding_manager = _DummyEmbeddingManager(delay=0.05)
    manager, metadata_store = _build_manager(embedding_manager=embedding_manager)
    file_record = SimpleNamespace(source_path="", source_kind="paste", name="demo.txt")

    await asyncio.gather(
        manager._persist_processed_chunk(
            file_record,
            ProcessedChunk(
                type=KnowledgeType.FACTUAL,
                source=SourceInfo(file="demo.txt", offset_start=0, offset_end=4),
                chunk=ChunkContext(chunk_id="chunk-1", index=0, text="第一段事实"),
                data={},
            ),
        ),
        manager._persist_processed_chunk(
            file_record,
            ProcessedChunk(
                type=KnowledgeType.FACTUAL,
                source=SourceInfo(file="demo.txt", offset_start=5, offset_end=9),
                chunk=ChunkContext(chunk_id="chunk-2", index=1, text="第二段事实"),
                data={},
            ),
        ),
    )

    assert len(metadata_store.paragraphs) == 2
    assert embedding_manager.max_inflight == 2


@pytest.mark.asyncio
async def test_paragraph_vector_write_is_idempotent_after_concurrent_encode() -> None:
    embedding_manager = _DummyEmbeddingManager(delay=0.01)
    manager, _ = _build_manager(embedding_manager=embedding_manager)

    results = await asyncio.gather(
        manager._write_paragraph_vector_or_enqueue(
            paragraph_hash="paragraph-same",
            content="同一段落内容",
            context="pytest",
        ),
        manager._write_paragraph_vector_or_enqueue(
            paragraph_hash="paragraph-same",
            content="同一段落内容",
            context="pytest",
        ),
    )

    assert manager.plugin.vector_store.ids == ["paragraph-same"]
    assert manager.plugin.vector_store.add_count == 1
    assert {result["detail"] for result in results} <= {
        "",
        "vector_already_exists_after_encode",
        "vector_already_exists",
    }


@pytest.mark.asyncio
async def test_dual_pool_paragraph_vector_write_uses_paragraph_store() -> None:
    manager, _ = _build_manager(vector_pool_mode="dual")

    result = await manager._write_paragraph_vector_or_enqueue(
        paragraph_hash="paragraph-dual",
        content="双池段落内容",
        context="pytest",
    )

    assert result["vector_written"] is True
    assert manager.plugin.paragraph_vector_store.ids == ["paragraph-dual"]
    assert manager.plugin.vector_store.ids == []
    assert manager.plugin.graph_vector_store.ids == []


@pytest.mark.asyncio
async def test_relation_vector_failure_keeps_metadata_and_marks_failed() -> None:
    manager, metadata_store = _build_manager(
        embedding_manager=_DummyEmbeddingManager(fail_for="关系是持有"),
        relation_vectorization_enabled=True,
    )

    relation_hash = await manager._add_relation("Alice", "持有", "地图", source_paragraph="paragraph-1")

    assert relation_hash == "relation-1"
    assert metadata_store.relations == [("Alice", "持有", "地图")]
    assert ("relation-1", "pending", None, False) in metadata_store.relation_vector_states
    assert metadata_store.relation_vector_states[-1] == ("relation-1", "failed", "embedding failed", True)


@pytest.mark.asyncio
async def test_relation_vector_value_error_marks_failed_when_vector_missing() -> None:
    manager, metadata_store = _build_manager(
        embedding_manager=_DummyEmbeddingManager(dimension=5),
        relation_vectorization_enabled=True,
    )

    relation_hash = await manager._add_relation("Alice", "持有", "地图", source_paragraph="paragraph-1")

    assert relation_hash == "relation-1"
    assert "relation-1" not in manager.plugin.vector_store
    assert metadata_store.relation_vector_states[-1][0] == "relation-1"
    assert metadata_store.relation_vector_states[-1][1] == "failed"
    assert metadata_store.relation_vector_states[-1][3] is True
    assert "Dimension mismatch" in str(metadata_store.relation_vector_states[-1][2])


@pytest.mark.asyncio
async def test_dual_pool_import_writes_graph_vectors_to_graph_store() -> None:
    manager, metadata_store = _build_manager(
        relation_vectorization_enabled=True,
        vector_pool_mode="dual",
    )
    file_record = SimpleNamespace(source_path="", source_kind="paste", name="demo.txt")

    await manager._persist_processed_chunk(
        file_record,
        ProcessedChunk(
            type=KnowledgeType.FACTUAL,
            source=SourceInfo(file="demo.txt", offset_start=0, offset_end=4),
            chunk=ChunkContext(chunk_id="chunk-1", index=0, text="Alice 持有地图"),
            data={
                "triples": [{"subject": "Alice", "predicate": "持有", "object": "地图"}],
                "entities": ["线索"],
            },
        ),
    )

    assert metadata_store.paragraphs[0]["content"] == "Alice 持有地图"
    assert manager.plugin.vector_store.ids == []
    assert manager.plugin.paragraph_vector_store.ids == ["paragraph-1"]
    assert set(manager.plugin.graph_vector_store.ids) == {
        "entity:entity-Alice",
        "entity:entity-地图",
        "entity:entity-线索",
        "relation:relation-1",
    }
    assert metadata_store.relation_vector_states[-1] == ("relation-1", "ready", None, False)


@pytest.mark.asyncio
async def test_high_concurrency_persist_processed_chunks_keep_all_writes_consistent() -> None:
    chunk_count = 60
    relations_per_chunk = 2
    entities_per_chunk = 5
    embedding_manager = _DummyEmbeddingManager(delay=0.001)
    manager, metadata_store = _build_manager(
        embedding_manager=embedding_manager,
        relation_vectorization_enabled=True,
    )
    file_record = SimpleNamespace(source_path="", source_kind="paste", name="stress.txt")

    async def persist(index: int) -> None:
        await manager._persist_processed_chunk(
            file_record,
            ProcessedChunk(
                type=KnowledgeType.FACTUAL,
                source=SourceInfo(file="stress.txt", offset_start=index * 10, offset_end=index * 10 + 9),
                chunk=ChunkContext(chunk_id=f"chunk-{index}", index=index, text=f"第 {index} 段高并发事实"),
                data={
                    "triples": [
                        {"subject": f"subject-{index}-a", "predicate": "关联", "object": f"object-{index}-a"},
                    ],
                    "relations": [
                        {"subject": f"subject-{index}-b", "predicate": "包含", "object": f"object-{index}-b"},
                    ],
                    "entities": [f"marker-{index}"],
                },
            ),
        )

    await asyncio.wait_for(
        asyncio.gather(*(persist(index) for index in range(chunk_count))),
        timeout=15,
    )

    vector_ids = set(manager.plugin.vector_store.ids)
    ready_states = [state for _, state, _, _ in metadata_store.relation_vector_states if state == "ready"]
    failed_states = [state for _, state, _, _ in metadata_store.relation_vector_states if state == "failed"]

    assert len(metadata_store.paragraphs) == chunk_count
    assert len(metadata_store.relations) == chunk_count * relations_per_chunk
    assert len(manager.plugin.graph_store.edges) == chunk_count * relations_per_chunk
    assert len({paragraph["source"] for paragraph in metadata_store.paragraphs}) == 1
    assert len(vector_ids) == chunk_count * (1 + entities_per_chunk + relations_per_chunk)
    assert len(ready_states) == chunk_count * relations_per_chunk
    assert failed_states == []
    assert metadata_store.paragraph_backfills == []
    assert embedding_manager.max_inflight > 1
