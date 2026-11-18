from pathlib import Path
from types import ModuleType
import importlib.util
import json
import logging
import sys

import numpy as np
import pytest

from src.chat.replyer import expression_vector_storage as storage


@pytest.fixture
def component(monkeypatch):
    hosts = {
        "src.common.async_work": {"run_sync_work": None},
        "src.common.paths": {"get_instance_root": Path.cwd},
        "src.common.logger": {"get_logger": logging.getLogger},
        "src.config.config": {"config_manager": None},
    }
    for name, attributes in hosts.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    name = "src.chat.replyer._storage_test_index"
    spec = importlib.util.spec_from_file_location(name, Path("src/chat/replyer/expression_vector_index.py"))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def fixture_index(path, *, legacy=False, bad=False):
    vectors = np.random.default_rng(42).standard_normal((200, 16)).astype(np.float32)
    if bad:
        vectors[7] = 0
    centers = vectors[:4].copy()
    np.savez(path / "old.npz", vectors=vectors, cluster_centers=centers)
    rows = [dict(id=i+1, situation="context", style="style", count=1, cluster_id=i % 4,
                 vector_index=i, fingerprint=str(i), embedding_profile_marker="p") for i in range(200)]
    payload = dict(version=1 if legacy else 2, vectors_file="old.npz", expressions=rows,
                   embedding_profile_marker="p", embedding_model="test")
    if not legacy:
        payload["embedding_profiles"] = [dict(marker="p", vectors_key="vectors", cluster_centers_key="cluster_centers")]
    index = path / "index.json"
    index.write_text(json.dumps(payload))
    return index, payload, vectors


@pytest.mark.parametrize("legacy", [True, False])
def test_old_formats_roundtrip(component, tmp_path, legacy):
    path, payload, original = fixture_index(tmp_path, legacy=legacy)
    service = component.ExpressionVectorIndex()
    first = service._load_snapshot(path)
    if legacy:
        payload["embedding_profiles"] = [dict(marker="p", vectors_key="vectors", cluster_centers_key="cluster_centers")]
    new_path = service._write_index_files(index_path=path, vectors_path=tmp_path / "old.npz", payload=payload,
                                          profile_vectors=first.profile_vectors,
                                          profile_cluster_centers=first.profile_cluster_centers)
    assert (tmp_path / "old.npz").exists()
    assert new_path.suffix == ".json"
    second = component.ExpressionVectorIndex()._load_snapshot(path)
    assert len(second.expressions) == 200
    assert np.allclose(second.profile_vectors["p"], first.profile_vectors["p"])
    assert not second.profile_vectors["p"].flags.writeable
    assert np.allclose(second.profile_vectors["p"] @ original[0], first.profile_vectors["p"] @ original[0])


def test_small_damage_skips_only_bad_row(component, tmp_path, caplog):
    path, payload, _ = fixture_index(tmp_path, bad=True)
    service = component.ExpressionVectorIndex()
    result = service._load_snapshot(path)
    assert len(result.expressions) == 199
    assert 8 not in {row.id for row in result.expressions}
    state = service._load_mutable_index_state(index_path=path, current_fingerprints={i+1:str(i) for i in range(200)})
    assert len(state.raw_expressions) == 199
    assert len(state.vector_by_expression_id) == 199
    assert caplog.records


def test_large_damage_not_silently_dropped():
    with pytest.raises(ValueError):
        storage.normalize(np.zeros((200, 16), dtype=np.float32), repair=True)


def test_failed_publication_preserves_old_manifest(component, tmp_path):
    path, payload, _ = fixture_index(tmp_path)
    original = path.read_bytes()
    service = component.ExpressionVectorIndex()
    snap = service._load_snapshot(path)
    result = service._write_index_files(index_path=path, vectors_path=tmp_path / "old.npz", payload=payload,
        profile_vectors=snap.profile_vectors, profile_cluster_centers=snap.profile_cluster_centers,
        publish=lambda *_: False)
    assert result is None and path.read_bytes() == original
    assert not list(tmp_path.glob("expression-array-*.npy"))


def test_disk_failure_does_not_publish(monkeypatch, tmp_path):
    def fail(_):
        raise OSError("disk full")
    monkeypatch.setattr(storage.os, "fsync", fail)
    with pytest.raises(OSError):
        storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    assert not list(tmp_path.iterdir())


def test_corrupted_new_shape_rejected(tmp_path):
    path = storage.write_arrays(tmp_path, {"v": np.ones((10, 8), dtype=np.float32)})
    manifest = json.loads(path.read_text())
    manifest["arrays"]["v"]["shape"] = [9, 8]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        storage.load_array(path, "v")


def test_incremental_persistence_restart_and_merge(tmp_path):
    base = np.asarray(storage.normalize(np.random.default_rng(10).normal(size=(200, 16)).astype(np.float32))).copy()
    first = storage.write_arrays(tmp_path, {"vectors": base})
    changed = base.copy()
    changed[3] = -changed[3]
    second = storage.write_arrays(tmp_path, {"vectors": storage.stack_rows(list(changed))}, previous=first)
    a, b = [json.loads(p.read_text())["arrays"]["vectors"] for p in (first, second)]
    assert a["file"] == b["file"] and b["delta_indices"] == [3]
    storage.remove_generation(first, keep=storage.generation_files(second))
    assert np.allclose(np.asarray(storage.load_array(second, "vectors")), changed)
    changed[4] = -changed[4]
    third = storage.write_arrays(tmp_path, {"vectors": changed}, previous=second)
    assert json.loads(third.read_text())["arrays"]["vectors"]["delta_indices"] == [3, 4]
    assert np.allclose(np.asarray(storage.load_array(third, "vectors")), changed)
    changed[:20] *= -1
    fourth = storage.write_arrays(tmp_path, {"vectors": changed}, previous=third)
    entry = json.loads(fourth.read_text())["arrays"]["vectors"]
    assert "delta_file" not in entry and entry["file"] != a["file"]
    assert np.allclose(storage.load_array(fourth, "vectors"), changed)


def test_append_delete_and_timeout_merge(tmp_path):
    base = np.asarray(storage.normalize(np.random.default_rng(10).normal(size=(200, 16)).astype(np.float32))).copy()
    first = storage.write_arrays(tmp_path, {"vectors": base})
    appended = np.vstack([base, base[:1]])
    second = storage.write_arrays(tmp_path, {"vectors": appended}, previous=first)
    assert np.allclose(np.asarray(storage.load_array(second, "vectors")), appended)
    shortened = storage.write_arrays(tmp_path, {"vectors": base[:-1]}, previous=second)
    assert storage.load_array(shortened, "vectors").shape == (199, 16)
    manifest = json.loads(second.read_text())
    manifest["arrays"]["vectors"]["merged_at"] = 0
    second.write_text(json.dumps(manifest))
    merged = storage.write_arrays(tmp_path, {"vectors": appended}, previous=second)
    assert "delta_file" not in json.loads(merged.read_text())["arrays"]["vectors"]


def test_rejected_increment_does_not_delete_shared_base(component, tmp_path):
    path, payload, _ = fixture_index(tmp_path)
    service = component.ExpressionVectorIndex()
    snap = service._load_snapshot(path)
    first = service._write_index_files(index_path=path, vectors_path=tmp_path / "old.npz", payload=payload,
        profile_vectors=snap.profile_vectors, profile_cluster_centers=snap.profile_cluster_centers)
    source = storage.load_array(first, "vectors")
    changed = np.array(source)
    changed[3] *= -1
    assert service._write_index_files(index_path=path, vectors_path=first, payload=payload,
        profile_vectors={"p": changed}, profile_cluster_centers=snap.profile_cluster_centers,
        publish=lambda *_: False) is None
    assert np.allclose(storage.load_array(first, "vectors"), source)


def test_repair_legacy_metadata_preserves_vector_alignment(component, tmp_path):
    path, payload, vectors = fixture_index(tmp_path, legacy=True)
    for row in payload["expressions"]:
        row.pop("vector_index")
    payload["expressions"][7]["id"] = "broken"
    payload["cluster_maintenance"] = {"state": "BOOTSTRAPPING", "changes_since_recluster": 12}
    path.write_text(json.dumps(payload))
    service = component.ExpressionVectorIndex()
    snap = service._load_snapshot(path)
    ninth = next(row for row in snap.expressions if row.id == 9)
    assert ninth.index == 8
    state = service._load_mutable_index_state(index_path=path, current_fingerprints={i+1:str(i) for i in range(200)})
    assert len(state.raw_expressions) == 199
    assert np.allclose(state.vector_by_expression_id[9], vectors[8] / np.linalg.norm(vectors[8]))
    assert state.payload["cluster_maintenance"]["state"] == "BOOTSTRAPPING"


def test_orphan_cleanup_preserves_live_base(tmp_path):
    import os
    base = np.asarray(storage.normalize(np.ones((200, 16), dtype=np.float32)))
    live = storage.write_arrays(tmp_path, {"vectors": base})
    (tmp_path / "index.json").write_text(json.dumps({"vectors_file": live.name}))
    orphan = storage.write_arrays(tmp_path, {"vectors": base})
    os.utime(orphan, (1, 1))
    storage.cleanup_retired(tmp_path)
    assert live.exists() and not orphan.exists()
    assert np.allclose(storage.load_array(live, "vectors"), base)


@pytest.fixture
def migration_backup(tmp_path):
    import os
    index = tmp_path / "index.json"
    old = tmp_path / "index.vectors-legacy.npz"
    old.write_bytes(b"old archive")
    backup = tmp_path / "index.json.pre-npy.bak"
    backup.write_text(json.dumps({"vectors_file": old.name}))
    manifest = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    index.write_text(json.dumps({"vectors_file": manifest.name}))
    os.utime(backup, (1, 1))
    return index, old, backup, manifest


def test_backup_kept_if_new_index_broken(migration_backup):
    index, old, backup, manifest = migration_backup
    manifest.write_text("broken")
    storage.cleanup_migration_backup(index)
    assert old.exists() and backup.exists()


def test_backup_cannot_delete_other_directory(migration_backup, tmp_path):
    index, old, backup, _ = migration_backup
    outside = tmp_path / "other"
    outside.mkdir()
    target = outside / old.name
    target.write_bytes(b"keep")
    backup.write_text(json.dumps({"vectors_file": str(target)}))
    import os
    os.utime(backup, (1, 1))
    storage.cleanup_migration_backup(index)
    assert target.exists() and old.exists() and backup.exists()


def test_backup_kept_while_another_index_uses_it(migration_backup):
    index, old, backup, _ = migration_backup
    (index.parent / "another.json").write_text(json.dumps({"vectors_file": old.name}))
    storage.cleanup_migration_backup(index)
    assert old.exists() and backup.exists()


def test_quarantined_json_does_not_block_retired_cleanup(tmp_path):
    import os
    live = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    (tmp_path / "index.json").write_text(json.dumps({"vectors_file": live.name}))
    quarantine = tmp_path / "index.corrupt-20260805-221129.json"
    quarantine.write_text("")
    retired = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    os.utime(retired, (1, 1))
    storage.cleanup_retired(tmp_path)
    assert not retired.exists()
    assert live.exists() and quarantine.exists()


def test_active_broken_json_still_protects_files(tmp_path):
    import os
    retired = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    os.utime(retired, (1, 1))
    (tmp_path / "index.json").write_text("broken")
    storage.cleanup_retired(tmp_path)
    assert retired.exists()


def test_quarantined_json_does_not_block_backup_expiration(migration_backup):
    index, old, backup, _ = migration_backup
    quarantine = index.parent / "index.corrupt-20260805-221129.json"
    quarantine.write_text("")
    storage.cleanup_migration_backup(index)
    assert not old.exists() and not backup.exists() and quarantine.exists()


def test_retired_scan_throttled_and_retries(monkeypatch, tmp_path):
    import os
    (tmp_path / "index.json").write_text("broken")
    retired = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    os.utime(retired, (1, 1))
    monkeypatch.setattr(storage.time, "monotonic", lambda: 10000)
    storage.cleanup_retired(tmp_path)
    (tmp_path / "index.json").write_text("{}")
    storage.cleanup_retired(tmp_path)
    assert retired.exists()
    monkeypatch.setattr(storage.time, "monotonic", lambda: 13601)
    storage.cleanup_retired(tmp_path)
    assert not retired.exists()


def wait_reclaimed(path):
    lifecycle = storage._mapping_lifecycle
    with lifecycle.condition:
        assert lifecycle.condition.wait_for(lambda: path.resolve() not in lifecycle.pending, timeout=5)
    assert not path.exists()


def test_retirement_waits_for_last_numpy_view(tmp_path):
    manifest = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    array = storage.load_array(manifest, "vectors")
    row = array[0].view(np.ndarray)
    mapping = array._mmap
    storage.retire_generation(manifest)
    del array
    assert manifest.exists() and not mapping.closed
    assert np.isfinite(row).all()
    del row
    wait_reclaimed(manifest)
    assert mapping.closed


def test_query_cpu_work_runs_off_event_loop(component, monkeypatch, tmp_path):
    from types import SimpleNamespace
    import asyncio
    import threading
    path, payload, vectors = fixture_index(tmp_path)
    service = component.ExpressionVectorIndex()
    snapshot = service._load_snapshot(path)
    profile = SimpleNamespace(marker="p", dimension=16, model_name="test", model_identifier="test", api_provider="test")
    async def profile_for_query(**kwargs):
        return profile
    service.get_current_embedding_profile = profile_for_query
    class Client:
        def __init__(self, **kwargs):
            pass
        async def embed_text(self, *args, **kwargs):
            return SimpleNamespace(embedding=vectors[0].tolist(), model_name="test", model_identifier="test", api_provider="test")
    module = ModuleType("src.services.embedding_service")
    module.EmbeddingServiceClient = Client
    monkeypatch.setitem(sys.modules, module.__name__, module)
    owner = threading.get_ident()
    invoked = []
    for name in ("_filter_indexed_expressions", "_score_indexed_candidates"):
        original = getattr(service, name)
        def checked(*args, _original=original, _name=name, **kwargs):
            assert threading.get_ident() != owner
            invoked.append(_name)
            return _original(*args, **kwargs)
        monkeypatch.setattr(service, name, checked)
    # 该测试关注执行线程；使用真实打分/MMR，不依赖 fixture 的手写 fingerprint。
    monkeypatch.setattr(component, "expression_fingerprint", lambda expression_id, *_: str(expression_id - 1))
    result = asyncio.run(service.select_candidates(index_path=str(path), session_id="test", query_text="test",
        scoped_candidates=payload["expressions"], candidate_pool_size=10, cluster_pool_size=2))
    assert len(result) == 10 and len({row["id"] for row in result}) == 10
    assert invoked == ["_filter_indexed_expressions", "_score_indexed_candidates"]
    assert service._snapshot is snapshot


def test_concurrent_readers_share_lifetime(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    manifest = storage.write_arrays(tmp_path, {"vectors": np.ones((100, 8), dtype=np.float32)})
    ready = threading.Barrier(3)
    release = threading.Event()
    def reader():
        array = storage.load_array(manifest, "vectors")
        ready.wait(timeout=5)
        assert release.wait(timeout=5)
        assert np.isfinite(array @ np.ones(8, dtype=np.float32)).all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reader) for _ in range(2)]
        ready.wait(timeout=5)
        storage.retire_generation(manifest)
        assert manifest.exists()
        release.set()
        for future in futures:
            future.result(timeout=5)
    wait_reclaimed(manifest)


def test_retirement_keeps_successor_shared_base(tmp_path):
    base = np.asarray(storage.normalize(np.ones((200, 8), dtype=np.float32)))
    old = storage.write_arrays(tmp_path, {"vectors": base})
    old_array = storage.load_array(old, "vectors")
    changed = base.copy()
    changed[2] *= -1
    new = storage.write_arrays(tmp_path, {"vectors": changed}, previous=old)
    (tmp_path / "index.json").write_text(json.dumps({"vectors_file": new.name}))
    storage.retire_generation(old)
    wait_reclaimed(old)
    assert np.isfinite(old_array).all()
    assert np.allclose(np.asarray(storage.load_array(new, "vectors")), changed)


def test_failed_loading_releases_root_mapping(tmp_path):
    manifest = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    payload = json.loads(manifest.read_text())
    array_path = tmp_path / payload["arrays"]["vectors"]["file"]
    payload["arrays"]["vectors"]["shape"] = [9, 8]
    manifest.write_text(json.dumps(payload))
    try:
        storage.load_array(manifest, "vectors")
    except ValueError:
        pass
    storage.retire_generation(manifest)
    wait_reclaimed(manifest)
    assert not array_path.exists()


def test_finalizer_notification_does_not_wait_for_cleanup_lock():
    import threading
    lifecycle = storage.MappingLifecycle()
    class Mapping:
        def close(self):
            pass
    finished = threading.Event()
    def release():
        lifecycle.release(Path("test"), Mapping())
        finished.set()
    with lifecycle.lock:
        thread = threading.Thread(target=release)
        thread.start()
        assert finished.wait(2)
    thread.join()


def test_cancelled_query_worker_keeps_mapping_until_finished(tmp_path):
    import asyncio
    import threading
    manifest = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    ready, finish = threading.Event(), threading.Event()
    def read():
        array = storage.load_array(manifest, "vectors")
        ready.set()
        assert finish.wait(5)
        assert np.isfinite(array).all()
    async def run():
        task = asyncio.create_task(asyncio.to_thread(read))
        assert await asyncio.to_thread(ready.wait, 5)
        storage.retire_generation(manifest)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manifest.exists()
        finish.set()
    asyncio.run(run())
    wait_reclaimed(manifest)


def test_delta_row_outlives_matrix(tmp_path):
    base = np.asarray(storage.normalize(np.ones((200, 8), dtype=np.float32)))
    original = storage.write_arrays(tmp_path, {"vectors": base})
    changed = base.copy()
    changed[3] *= -1
    manifest = storage.write_arrays(tmp_path, {"vectors": changed}, previous=original)
    matrix = storage.load_array(manifest, "vectors")
    row = matrix[3]
    storage.retire_generation(manifest)
    del matrix
    assert manifest.exists()
    assert np.allclose(row, changed[3])
    del row
    wait_reclaimed(manifest)


def test_retirement_retries_external_file_lock(monkeypatch, tmp_path):
    import threading
    manifest = storage.write_arrays(tmp_path, {"vectors": np.ones((10, 8), dtype=np.float32)})
    attempted = threading.Event()
    original = Path.unlink
    def busy(path, *args, **kwargs):
        if path.suffix == ".npy" and path.parent == tmp_path:
            attempted.set()
            error = PermissionError("external sharing violation")
            error.winerror = 32
            raise error
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", busy)
    storage.retire_generation(manifest)
    assert attempted.wait(5)
    lifecycle = storage._mapping_lifecycle
    with lifecycle.lock:
        assert manifest.resolve() in lifecycle.pending and manifest.exists()
    monkeypatch.setattr(Path, "unlink", original)
    lifecycle.wakeup.set()  # 模拟下一次重试唤醒，不在测试中等待一分钟。
    wait_reclaimed(manifest)


def test_scratch_mapping_waits_for_last_view(monkeypatch):
    monkeypatch.setattr(storage, "BLOCK_BYTES", 4)
    array = storage.scratch((10, 8))
    path = Path(array.filename).resolve()
    row = array[0]
    row[:] = 1
    mapping = array._mmap
    del array
    assert path.exists() and not mapping.closed
    assert np.all(row == 1)
    del row
    lifecycle = storage._mapping_lifecycle
    with lifecycle.condition:
        assert lifecycle.condition.wait_for(lambda: path not in lifecycle.temporary, timeout=5)
    assert mapping.closed and not path.exists()
