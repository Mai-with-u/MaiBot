from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.A_memorix.core.storage.graph_store import GraphStore
from src.A_memorix.core.storage.knowledge_types import KnowledgeType
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.storage.vector_store import VectorStore
from src.A_memorix.core.utils.hash import compute_hash, normalize_text
from src.A_memorix.core.utils.quantization import QuantizationType


RUN_MIGRATION_PERF_TESTS = str(os.getenv("A_MEMORIX_MIGRATION_PERF", "")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _process_rss_bytes() -> int:
    proc_statm = Path("/proc/self/statm")
    if proc_statm.exists():
        try:
            rss_pages = int(proc_statm.read_text(encoding="utf-8").split()[1])
            return rss_pages * int(os.sysconf("SC_PAGE_SIZE"))
        except Exception:
            return 0
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        psapi = ctypes.WinDLL("Psapi.dll")
        kernel32 = ctypes.WinDLL("Kernel32.dll")
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        ok = get_process_memory_info(
            get_current_process(),
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.WorkingSetSize) if ok else 0
    except Exception:
        return 0


class _Probe:
    def __init__(self, name: str) -> None:
        self.name = name
        self.wall_ms = 0.0
        self.cpu_ms = 0.0
        self.rss_delta_mb = 0.0

    def __enter__(self) -> "_Probe":
        self._start_wall = time.perf_counter()
        self._start_cpu = time.process_time()
        self._start_rss = _process_rss_bytes()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.wall_ms = (time.perf_counter() - self._start_wall) * 1000.0
        self.cpu_ms = (time.process_time() - self._start_cpu) * 1000.0
        self.rss_delta_mb = (_process_rss_bytes() - self._start_rss) / 1024 / 1024

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "wall_ms": self.wall_ms,
            "cpu_ms": self.cpu_ms,
            "rss_delta_mb": self.rss_delta_mb,
        }


class _DeterministicEmbeddingManager:
    def __init__(self, dimension: int = 32) -> None:
        self.dimension = int(dimension)
        self.calls = 0
        self.total_texts = 0
        self.batch_sizes = []

    async def encode_batch(self, texts, batch_size=None, num_workers=None):
        del batch_size, num_workers
        items = list(texts or [])
        self.calls += 1
        self.total_texts += len(items)
        self.batch_sizes.append(len(items))
        return np.vstack([self._vector(text) for text in items]).astype(np.float32)

    async def encode(self, text):
        if isinstance(text, list):
            return await self.encode_batch(text)
        vectors = await self.encode_batch([text])
        return vectors[0]

    def _vector(self, text: str) -> np.ndarray:
        raw = compute_hash(str(text or "")).encode("utf-8")
        seed = int(raw[:8], 16)
        rng = np.random.default_rng(seed)
        vec = rng.random(self.dimension, dtype=np.float32)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec


class _NoopRelationWriteService:
    async def ensure_relation_vector(self, **kwargs):
        del kwargs
        return SimpleNamespace(vector_state="none", vector_written=False)


def _load_migration_module():
    script_dir = Path(__file__).resolve().parents[2] / "src" / "A_memorix" / "scripts"
    script_path = script_dir / "migrate_maibot_memory.py"
    script_dir_text = str(script_dir)
    module_name = "maibot_migration_script_test_module"
    original_sys_path = list(sys.path)
    previous_module = sys.modules.get(module_name)
    if script_dir_text not in sys.path:
        sys.path.insert(0, script_dir_text)
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_sys_path
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    module.compute_hash = lambda text: f"hash:{text}"
    module.normalize_text = lambda text: " ".join(str(text).split())
    return module


def _load_real_migration_module():
    module = _load_migration_module()
    module.compute_hash = compute_hash
    module.normalize_text = normalize_text
    module.VectorStore = VectorStore
    module.GraphStore = GraphStore
    module.MetadataStore = MetadataStore
    module.KnowledgeType = KnowledgeType
    module.QuantizationType = QuantizationType
    module.SparseMatrixFormat = __import__(
        "src.A_memorix.core.storage.graph_store",
        fromlist=["SparseMatrixFormat"],
    ).SparseMatrixFormat
    return module


def _create_chat_history_db(db_path: Path, row_count: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE chat_history (
            id INTEGER PRIMARY KEY,
            chat_id TEXT NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL NOT NULL,
            participants TEXT NOT NULL,
            theme TEXT NOT NULL,
            keywords TEXT NOT NULL,
            summary TEXT NOT NULL
        )
        """
    )
    rows = []
    base_ts = 1_800_000_000.0
    for index in range(1, row_count + 1):
        stream = f"group-{index % 9}"
        participants = [f"成员{index % 17}", f"成员{(index + 3) % 17}"]
        theme = f"迁移主题{index % 37}"
        keywords = [
            f"物品{index % 53}",
            f"地点{index % 29}",
            f"标记{index % 11}",
        ]
        summary = f"第 {index} 条迁移性能样本，{participants[0]} 记录 {theme}，涉及 {'、'.join(keywords)}。"
        rows.append(
            (
                index,
                stream,
                base_ts + index * 60,
                base_ts + index * 60 + 30,
                json.dumps(participants, ensure_ascii=False),
                theme,
                json.dumps(keywords, ensure_ascii=False),
                summary,
            )
        )
    conn.executemany(
        """
        INSERT INTO chat_history
        (id, chat_id, start_time, end_time, participants, theme, keywords, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def _migration_args(source_db: Path, target_dir: Path, *, commit_window_rows: int = 512) -> SimpleNamespace:
    return SimpleNamespace(
        source_db=str(source_db),
        target_data_dir=str(target_dir),
        reset_state=True,
        start_id=None,
        end_id=None,
        read_batch_size=256,
        commit_window_rows=commit_window_rows,
        embed_batch_size=128,
        entity_embed_batch_size=256,
        embed_workers=1,
        max_errors=0,
        log_every=100_000,
        dry_run=False,
        verify_only=False,
        time_from=None,
        time_to=None,
        stream_id=[],
        group_id=[],
        user_id=[],
        yes=True,
        preview_limit=3,
        no_resume=True,
    )


def _setup_runner(module, source_db: Path, target_dir: Path, *, commit_window_rows: int = 512):
    runner = module.MigrationRunner(_migration_args(source_db, target_dir, commit_window_rows=commit_window_rows))
    runner.plugin_config = {
        "embedding": {
            "batch_size": 128,
            "dimension": 32,
            "model_name": "fake",
            "quantization_type": "int8",
        },
        "graph": {"sparse_matrix_format": "csr"},
        "retrieval": {"relation_vectorization": {"enabled": False, "write_on_import": False}},
    }
    runner.embed_workers = 1
    runner.vector_store = VectorStore(
        dimension=32,
        quantization_type=QuantizationType.INT8,
        data_dir=target_dir / "vectors",
        buffer_size=256,
    )
    runner.graph_store = GraphStore(data_dir=target_dir / "graph")
    runner.metadata_store = MetadataStore(data_dir=target_dir / "metadata")
    runner.metadata_store.connect()
    if runner.vector_store.has_data():
        runner.vector_store.load()
    if runner.graph_store.has_data():
        runner.graph_store.load()
    runner.embedding_manager = _DeterministicEmbeddingManager(dimension=32)
    runner.relation_write_service = _NoopRelationWriteService()
    runner.source_db.connect()
    runner.selection = runner._build_selection_filter()
    runner.filter_fingerprint = module._json_hash(runner.selection.fingerprint_payload())
    runner.source_db_fingerprint = {"sha1": "test"}
    runner.source_db_fingerprint_hash = "test"
    runner.state = runner._new_state(last_committed_id=0)
    return runner


def _close_runner(runner) -> None:
    try:
        runner.vector_store.save()
    except Exception:
        pass
    try:
        runner.graph_store.save()
    except Exception:
        pass
    try:
        runner.metadata_store.close()
    except Exception:
        pass
    try:
        runner.source_db.close()
    except Exception:
        pass


async def _run_migration_algorithm(module, source_db: Path, target_dir: Path, *, commit_window_rows: int = 512):
    runner = _setup_runner(module, source_db, target_dir, commit_window_rows=commit_window_rows)
    try:
        await runner._migrate(start_after_id=0)
        return runner
    except Exception:
        _close_runner(runner)
        raise


async def _run_naive_algorithm(module, source_db: Path, target_dir: Path):
    runner = _setup_runner(module, source_db, target_dir, commit_window_rows=1)
    try:
        rows = []
        for batch in runner.source_db.iter_rows(runner.selection, batch_size=1, start_after_id=0):
            rows.extend(batch)
        for row in rows:
            mapped = runner._map_row(row)
            paragraph_hash = runner.metadata_store.add_paragraph(
                content=mapped.content,
                source=mapped.source,
                metadata={},
                knowledge_type=KnowledgeType.NARRATIVE.value,
                time_meta=mapped.time_meta,
            )
            paragraph_embedding = await runner.embedding_manager.encode(mapped.content)
            runner.vector_store.add(paragraph_embedding.reshape(1, -1), [paragraph_hash])

            entities = list(mapped.entities)
            for subject, _predicate, obj in mapped.relations:
                entities.extend([subject, obj])
            seen_entities = {}
            for name in entities:
                token = str(name or "").strip()
                if token:
                    seen_entities.setdefault(token.lower(), token)
            for name in seen_entities.values():
                entity_hash = runner.metadata_store.add_entity(name=name, source_paragraph=paragraph_hash)
                entity_embedding = await runner.embedding_manager.encode(name)
                runner.vector_store.add(entity_embedding.reshape(1, -1), [entity_hash])
                runner.graph_store.add_nodes([name])

            edge_pairs = []
            relation_hashes = []
            for subject, predicate, obj in mapped.relations:
                rel_hash = runner.metadata_store.add_relation(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    confidence=1.0,
                    source_paragraph=paragraph_hash,
                )
                edge_pairs.append((subject, obj))
                relation_hashes.append(rel_hash)
            if edge_pairs:
                runner.graph_store.add_edges(edge_pairs, relation_hashes=relation_hashes)
            runner.stats["scanned_rows"] += 1
            runner.stats["valid_rows"] += 1
            runner.stats["migrated_rows"] += 1

        runner.vector_store.save()
        runner.graph_store.save()
        return runner
    except Exception:
        _close_runner(runner)
        raise


def _store_counts(runner) -> dict:
    conn = runner.metadata_store.get_connection()
    return {
        "paragraphs": runner.metadata_store.count_paragraphs(include_deleted=True),
        "entities": runner.metadata_store.count_entities(),
        "relations": runner.metadata_store.count_relations(include_deleted=True),
        "paragraph_entities": conn.execute("SELECT COUNT(*) FROM paragraph_entities").fetchone()[0],
        "paragraph_relations": conn.execute("SELECT COUNT(*) FROM paragraph_relations").fetchone()[0],
        "vectors": runner.vector_store.num_vectors,
        "graph_nodes": runner.graph_store.num_nodes,
        "graph_edges": runner.graph_store.num_edges,
    }


class _FakeVectorStore:
    def __contains__(self, value: str) -> bool:
        del value
        return False


class _Row(dict):
    def __getitem__(self, key: str):
        return dict.__getitem__(self, key)


def test_parse_list_field_accepts_legacy_dirty_values(tmp_path: Path) -> None:
    module = _load_migration_module()
    runner = module.MigrationRunner(
        SimpleNamespace(source_db=str(tmp_path / "source.db"), target_data_dir=str(tmp_path / "target"))
    )

    assert runner._parse_json_list_field("", "participants", 1) == []
    assert runner._parse_json_list_field("['Alice', 'Bob']", "participants", 1) == ["Alice", "Bob"]
    assert runner._parse_json_list_field('{"name": "Alice"}', "participants", 1) == ["Alice"]
    assert runner._parse_json_list_field("Alice,Bob", "participants", 1) == ["Alice", "Bob"]
    assert runner.stats["coerced_list_fields"] >= 3


def test_current_schema_is_aliased_for_preview_and_rows(tmp_path: Path) -> None:
    module = _load_migration_module()
    db_path = tmp_path / "current.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE chat_history (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            start_timestamp TEXT,
            end_timestamp TEXT,
            query_count INTEGER NOT NULL,
            query_forget_count INTEGER NOT NULL,
            original_messages TEXT NOT NULL,
            participants TEXT NOT NULL,
            theme TEXT NOT NULL,
            keywords TEXT NOT NULL,
            summary TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            group_id TEXT,
            user_id TEXT,
            platform TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO chat_history
        (id, session_id, start_timestamp, end_timestamp, query_count, query_forget_count,
         original_messages, participants, theme, keywords, summary)
        VALUES (1, 'session-1', '2026-04-01 12:00:00', '2026-04-01 12:05:00',
                0, 0, 'original', '["Alice"]', '计划', '地图,咖啡', '讨论周末计划')
        """
    )
    conn.execute(
        "INSERT INTO chat_sessions (id, session_id, group_id, user_id, platform) VALUES (1, 'session-1', 'group-1', 'user-1', 'qq')"
    )
    conn.commit()
    conn.close()

    source = module.SourceDB(db_path)
    source.connect()
    try:
        stream_ids = source.resolve_stream_ids([], ["group-1"], [])
        assert stream_ids == ["session-1"]

        selection = module.SelectionFilter(None, None, stream_ids, True, None, None, None, None)
        preview = source.preview(selection, preview_limit=1)
        assert preview.total == 1
        assert preview.samples[0]["chat_id"] == "session-1"

        rows = next(source.iter_rows(selection, batch_size=10, start_after_id=0))
        runner = module.MigrationRunner(
            SimpleNamespace(source_db=str(db_path), target_data_dir=str(tmp_path / "target"))
        )
        runner.vector_store = _FakeVectorStore()
        mapped = runner._map_row(rows[0])
        assert mapped.chat_id == "session-1"
        assert mapped.time_meta["event_time_start"] > 0
        assert "Alice" in mapped.entities
        assert "地图" in mapped.entities
    finally:
        source.close()


def test_max_errors_defaults_to_no_abort() -> None:
    module = _load_migration_module()

    args = module.build_parser().parse_args([])

    assert args.max_errors == 0


def test_max_errors_rejects_negative_value() -> None:
    module = _load_migration_module()

    try:
        module.build_parser().parse_args(["--max-errors", "-1"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("--max-errors must reject negative values")


def test_same_content_from_different_streams_keeps_same_hash_for_idempotency(tmp_path: Path) -> None:
    module = _load_migration_module()
    runner = module.MigrationRunner(
        SimpleNamespace(source_db=str(tmp_path / "source.db"), target_data_dir=str(tmp_path / "target"))
    )
    runner.vector_store = _FakeVectorStore()
    base = {
        "start_time": 1700000000,
        "end_time": 1700000100,
        "participants": '["Alice"]',
        "theme": "同一主题",
        "keywords": '["k"]',
        "summary": "同一摘要",
    }

    row1 = _Row(id=1, chat_id="stream-a", **base)
    row2 = _Row(
        id=2,
        chat_id="stream-b",
        start_time=1800000000,
        end_time=1800000100,
        **{k: v for k, v in base.items() if k not in {"start_time", "end_time"}},
    )

    assert runner._map_row(row1).paragraph_hash == runner._map_row(row2).paragraph_hash


def test_migration_window_algorithm_is_idempotent_and_stable(tmp_path: Path) -> None:
    module = _load_real_migration_module()
    source_db = tmp_path / "source.db"
    _create_chat_history_db(source_db, row_count=80)

    runner = asyncio.run(
        _run_migration_algorithm(
            module,
            source_db,
            tmp_path / "target",
            commit_window_rows=32,
        )
    )
    try:
        first_counts = _store_counts(runner)
        first_embedding_calls = runner.embedding_manager.calls

        assert first_counts["paragraphs"] == 80
        assert first_counts["relations"] > 0
        assert first_counts["paragraph_entities"] > 0
        assert first_counts["vectors"] == first_counts["paragraphs"] + first_counts["entities"]
        assert runner.stats["windows_committed"] == 3
        assert first_embedding_calls < first_counts["vectors"]
        lifecycle_revisions = runner.metadata_store.get_connection().execute(
            "SELECT lifecycle_revision FROM relations"
        ).fetchall()
        assert {int(row[0]) for row in lifecycle_revisions} == {0}
    finally:
        _close_runner(runner)

    # 断点/重复运行应当保持段落、关系和实体计数稳定，不重复累计 mention。
    rerun = asyncio.run(
        _run_migration_algorithm(
            module,
            source_db,
            tmp_path / "target",
            commit_window_rows=32,
        )
    )
    try:
        second_counts = _store_counts(rerun)
        assert second_counts == first_counts
        assert rerun.stats["skipped_existing_rows"] == 80
        assert rerun.embedding_manager.total_texts == 0
    finally:
        _close_runner(rerun)


def test_migration_window_algorithm_rolls_back_failed_metadata_window(tmp_path: Path) -> None:
    module = _load_real_migration_module()
    source_db = tmp_path / "source.db"
    _create_chat_history_db(source_db, row_count=24)
    runner = _setup_runner(module, source_db, tmp_path / "target", commit_window_rows=24)

    class FailingCursor:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, parameters=()):
            return self.inner.execute(sql, parameters)

        def executemany(self, sql, seq_of_parameters):
            if "INSERT OR IGNORE INTO relations" in str(sql):
                raise sqlite3.OperationalError("forced relation insert failure")
            return self.inner.executemany(sql, seq_of_parameters)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    class FailingConnection:
        def __init__(self, inner):
            self.inner = inner

        def cursor(self):
            return FailingCursor(self.inner.cursor())

        def __getattr__(self, name):
            return getattr(self.inner, name)

    real_conn = runner.metadata_store._conn
    runner.metadata_store._conn = FailingConnection(real_conn)
    try:
        with pytest.raises(sqlite3.OperationalError):
            rows = next(runner.source_db.iter_rows(runner.selection, 24, 0))
            asyncio.run(runner._commit_window([runner._map_row(row) for row in rows], 24))

        runner.metadata_store._conn = real_conn
        counts = _store_counts(runner)
        assert counts["paragraphs"] == 0
        assert counts["entities"] == 0
        assert counts["relations"] == 0
        assert counts["paragraph_entities"] == 0
        assert counts["paragraph_relations"] == 0
        assert counts["vectors"] == 0
    finally:
        runner.metadata_store._conn = real_conn
        _close_runner(runner)


@pytest.mark.skipif(
    not RUN_MIGRATION_PERF_TESTS,
    reason="设置 A_MEMORIX_MIGRATION_PERF=1 后才运行迁移算法性能对比",
)
def test_migration_window_algorithm_improves_import_efficiency(tmp_path: Path) -> None:
    module = _load_real_migration_module()
    row_count = int(os.getenv("A_MEMORIX_MIGRATION_PERF_ROWS", "900"))
    source_db = tmp_path / "source.db"
    _create_chat_history_db(source_db, row_count=row_count)

    with _Probe("naive_per_row") as naive_probe:
        naive_runner = asyncio.run(_run_naive_algorithm(module, source_db, tmp_path / "target_naive"))
    try:
        naive_counts = _store_counts(naive_runner)
        naive_embedding_calls = naive_runner.embedding_manager.calls
        naive_embedding_texts = naive_runner.embedding_manager.total_texts
    finally:
        _close_runner(naive_runner)

    with _Probe("migration_window") as batch_probe:
        batch_runner = asyncio.run(
            _run_migration_algorithm(
                module,
                source_db,
                tmp_path / "target_batch",
                commit_window_rows=256,
            )
        )
    try:
        batch_counts = _store_counts(batch_runner)
        batch_embedding_calls = batch_runner.embedding_manager.calls
        batch_embedding_texts = batch_runner.embedding_manager.total_texts
        batch_stats = dict(batch_runner.stats)
    finally:
        _close_runner(batch_runner)

    report = {
        "row_count": row_count,
        "naive": {
            "probe": naive_probe.to_dict(),
            "counts": naive_counts,
            "embedding_calls": naive_embedding_calls,
            "embedding_texts": naive_embedding_texts,
        },
        "migration_window": {
            "probe": batch_probe.to_dict(),
            "counts": batch_counts,
            "embedding_calls": batch_embedding_calls,
            "embedding_texts": batch_embedding_texts,
            "stats": batch_stats,
        },
        "speedup": naive_probe.wall_ms / max(1.0, batch_probe.wall_ms),
        "embedding_call_reduction": naive_embedding_calls / max(1, batch_embedding_calls),
    }
    report_path = Path(os.getenv("A_MEMORIX_MIGRATION_PERF_REPORT", "temp/a_memorix_migration_perf_report.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nA_MEMORIX_MIGRATION_PERF_REPORT=" + json.dumps(report, ensure_ascii=False, indent=2))

    assert batch_counts["paragraphs"] == row_count
    assert batch_counts == naive_counts
    assert batch_probe.wall_ms < naive_probe.wall_ms
    assert batch_embedding_calls < naive_embedding_calls
    assert report["speedup"] >= 1.2
    assert report["embedding_call_reduction"] >= 4.0
