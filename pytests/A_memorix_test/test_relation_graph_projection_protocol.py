from pathlib import Path
from threading import Event, Thread
from typing import Any, Dict, List, Set, Tuple

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.graph_store import GraphStore
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.memory_lifecycle_policy import (
    RelationLifecycleEvent,
    RelationLifecyclePolicy,
    RelationLifecycleState,
    apply_lifecycle_event,
    evaluate_lifecycle,
    retention_at,
)


def _policy(*, access_cooldown_seconds: float = 0.0) -> RelationLifecyclePolicy:
    return RelationLifecyclePolicy(
        half_life_hours=24.0,
        freeze_threshold=0.1,
        revive_threshold=0.15,
        access_alpha=0.05,
        access_cooldown_seconds=access_cooldown_seconds,
        reinforce_alpha=0.5,
        weaken_alpha=0.5,
    )


def _runtime(tmp_path: Path) -> Tuple[SDKMemoryKernel, MetadataStore, GraphStore]:
    store = MetadataStore(data_dir=tmp_path / "metadata")
    store.connect()
    graph = GraphStore(data_dir=tmp_path / "graph")
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={
            "memory": {
                "half_life_hours": 24.0,
                "prune_threshold": 0.1,
                "revive_threshold": 0.15,
            }
        },
    )
    kernel.metadata_store = store
    kernel.graph_store = graph
    return kernel, store, graph


def _projection_jobs(store: MetadataStore) -> List[Dict[str, Any]]:
    return store.query(
        """
        SELECT relation_hash, desired_active, desired_lifecycle_revision,
               job_revision, status, attempt_count, lease_token
        FROM relation_graph_projection_jobs
        ORDER BY relation_hash
        """
    )


def _disk_hashes(graph_dir: Path, subject: str, obj: str) -> Set[str]:
    graph = GraphStore(data_dir=graph_dir)
    assert graph.has_data()
    graph.load()
    return set(graph.get_relation_hashes_for_edge(subject, obj))


def _flip_inactive(store: MetadataStore, relation_hash: str) -> None:
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE relations
            SET is_inactive = 1,
                inactive_since = retention_anchor_at,
                inactive_reason = 'test',
                lifecycle_revision = lifecycle_revision + 1
            WHERE hash = ?
            """,
            (relation_hash,),
        )


def test_24_hour_clock_rollback_avoids_artificial_half_loss() -> None:
    policy = _policy()
    state = RelationLifecycleState(strength=0.5, anchor_at=86400.0)

    reinforced = apply_lifecycle_event(
        state,
        RelationLifecycleEvent.REINFORCE,
        now=0.0,
        policy=policy,
    )
    recovered_score = retention_at(
        RelationLifecycleState(
            strength=reinforced.strength,
            anchor_at=reinforced.anchor_at,
        ),
        now=86400.0,
        policy=policy,
    )
    old_reanchored_score = reinforced.strength * 0.5

    assert reinforced.anchor_at == 86400.0
    assert reinforced.strength == pytest.approx(0.75)
    assert recovered_score == pytest.approx(0.75)
    assert old_reanchored_score == pytest.approx(0.375)
    assert recovered_score / old_reanchored_score == pytest.approx(2.0)

    frozen = evaluate_lifecycle(
        RelationLifecycleState(strength=0.05, anchor_at=86400.0),
        now=0.0,
        policy=policy,
    )
    assert frozen.is_inactive is True
    assert frozen.inactive_since == 86400.0


def test_inactive_since_is_a_logical_clock_lower_bound() -> None:
    policy = _policy()
    inactive = RelationLifecycleState(
        strength=0.2,
        anchor_at=0.0,
        is_inactive=True,
        inactive_since=86400.0,
        inactive_reason="decay",
    )

    accessed = apply_lifecycle_event(
        inactive,
        RelationLifecycleEvent.ACCESS,
        now=0.0,
        policy=policy,
    )
    evaluated = evaluate_lifecycle(
        RelationLifecycleState(
            strength=0.2,
            anchor_at=0.0,
            inactive_since=86400.0,
        ),
        now=0.0,
        policy=policy,
    )

    assert accessed.anchor_at == 86400.0
    assert accessed.strength == pytest.approx(0.145)
    assert accessed.is_inactive is True
    assert accessed.inactive_since == 86400.0
    assert evaluated.is_inactive is True
    assert evaluated.inactive_since == 86400.0
    # 旧逻辑先错误复活到0.24，一天后只剩0.12，比正确值少17.24%。
    assert (0.145 - 0.12) / 0.145 == pytest.approx(0.1724137931)


def test_sqlite_access_and_evidence_respect_inactive_since_clock(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path / "metadata")
    store.connect()
    relation_hash = store.add_relation("冻结时钟", "约束", "回拨事件")
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE relations
            SET retention_strength = 0.2,
                retention_anchor_at = 0.0,
                next_lifecycle_at = NULL,
                is_inactive = 1,
                inactive_since = 86400.0,
                inactive_reason = 'decay',
                last_accessed = NULL,
                last_reinforced = NULL,
                last_access_reinforced_at = NULL,
                lifecycle_revision = 0
            WHERE hash = ?
            """,
            (relation_hash,),
        )
        connection.execute("DELETE FROM relation_graph_projection_jobs")

    store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.ACCESS,
        policy=_policy(),
        now=0.0,
    )
    accessed = store.get_relation(relation_hash) or {}
    assert accessed["retention_strength"] == pytest.approx(0.145)
    assert accessed["retention_anchor_at"] == 86400.0
    assert accessed["last_accessed"] == 86400.0
    assert accessed["is_inactive"] == 1
    assert _projection_jobs(store) == []

    with store.transaction(immediate=True) as connection:
        store._observe_relation_evidence(
            [relation_hash],
            observed_at=0.0,
            cursor=connection.cursor(),
        )
    observed = store.get_relation(relation_hash) or {}
    assert observed["retention_strength"] == 1.0
    assert observed["retention_anchor_at"] == 86400.0
    assert observed["next_lifecycle_at"] == 86400.0
    assert observed["last_reinforced"] == 86400.0
    assert observed["is_inactive"] == 0
    assert _projection_jobs(store)[0]["desired_active"] == 1
    store.close()


def test_sqlite_lifecycle_and_evidence_timestamps_never_regress(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path / "metadata")
    store.connect()
    relation_hash = store.add_relation("时钟用户", "访问", "未来锚点")
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE relations
            SET retention_strength = 0.5,
                retention_anchor_at = 86400.0,
                next_lifecycle_at = 90000.0,
                last_accessed = 90000.0,
                last_reinforced = 95000.0,
                last_access_reinforced_at = 92000.0,
                lifecycle_revision = 0
            WHERE hash = ?
            """,
            (relation_hash,),
        )

    store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.ACCESS,
        policy=_policy(),
        now=0.0,
    )
    accessed = store.get_relation(relation_hash) or {}
    assert accessed["retention_anchor_at"] == 95000.0
    assert accessed["last_accessed"] == 95000.0
    assert accessed["last_reinforced"] == 95000.0
    assert accessed["last_access_reinforced_at"] == 95000.0

    with store.transaction(immediate=True) as connection:
        store._observe_relation_evidence(
            [relation_hash],
            observed_at=0.0,
            cursor=connection.cursor(),
        )
    observed = store.get_relation(relation_hash) or {}
    assert observed["retention_anchor_at"] == 95000.0
    assert observed["next_lifecycle_at"] == 95000.0
    assert observed["last_reinforced"] == 95000.0

    store.mark_relations_active([relation_hash])
    store.mark_relations_inactive([relation_hash], inactive_since=0.0)
    inactive = store.get_relation(relation_hash) or {}
    assert inactive["inactive_since"] == 95000.0

    future_access = 4_102_444_800.0
    with store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE relations SET last_accessed = ? WHERE hash = ?",
            (future_access, relation_hash),
        )
    assert store.record_access(relation_hash, "relation") is True
    store.update_relation_timestamp(relation_hash)
    assert (store.get_relation(relation_hash) or {})["last_accessed"] == future_access
    store.close()


def test_snapshot_restore_reads_revision_inside_immediate_transaction(
    tmp_path: Path,
) -> None:
    primary = MetadataStore(data_dir=tmp_path / "metadata")
    primary.connect()
    relation_hash = primary.add_relation("恢复事务", "保护", "新事件")
    snapshot = primary.get_relation_status_batch([relation_hash])[relation_hash]
    concurrent = MetadataStore(data_dir=tmp_path / "metadata")
    concurrent.connect()
    started = Event()
    finished = Event()
    restored: List[Dict[str, Any]] = []
    errors: List[BaseException] = []

    def restore_snapshot() -> None:
        started.set()
        try:
            result = concurrent.restore_relation_status_from_snapshot(
                relation_hash,
                snapshot,
            )
            if result is not None:
                restored.append(result)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = Thread(target=restore_snapshot)
    with primary.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE relations SET lifecycle_revision = 50 WHERE hash = ?",
            (relation_hash,),
        )
        worker.start()
        assert started.wait(timeout=5.0)
        assert finished.wait(timeout=0.2) is False
    worker.join(timeout=5.0)

    assert worker.is_alive() is False
    assert errors == []
    assert restored[0]["lifecycle_revision"] == 51
    primary.close()
    concurrent.close()


def test_tombstone_restore_reads_generation_inside_immediate_transaction(
    tmp_path: Path,
) -> None:
    primary = MetadataStore(data_dir=tmp_path / "metadata")
    primary.connect()
    relation_hash = primary.add_relation("墓碑事务", "保护", "新世代")
    assert primary.backup_and_delete_relations([relation_hash]) == 1
    assert primary.add_relation("墓碑事务", "保护", "新世代") == relation_hash
    concurrent = MetadataStore(data_dir=tmp_path / "metadata")
    concurrent.connect()
    started = Event()
    finished = Event()
    restored: List[Dict[str, Any]] = []
    errors: List[BaseException] = []

    def restore_tombstone() -> None:
        started.set()
        try:
            result = concurrent.restore_relation(relation_hash)
            if result is not None:
                restored.append(result)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = Thread(target=restore_tombstone)
    with primary.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE relations SET lifecycle_revision = 50 WHERE hash = ?",
            (relation_hash,),
        )
        worker.start()
        assert started.wait(timeout=5.0)
        assert finished.wait(timeout=0.2) is False
    worker.join(timeout=5.0)

    assert worker.is_alive() is False
    assert errors == []
    assert restored[0]["lifecycle_revision"] == 51
    primary.close()
    concurrent.close()


def test_projection_trigger_only_tracks_actual_active_state_changes_and_clear_all(
    tmp_path: Path,
) -> None:
    store = MetadataStore(data_dir=tmp_path / "metadata")
    store.connect()
    relation_hash = store.add_relation("触发器", "只跟踪", "活跃态")
    assert _projection_jobs(store) == []

    with store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE relations SET confidence = 0.8 WHERE hash = ?",
            (relation_hash,),
        )
        connection.execute(
            "UPDATE relations SET is_inactive = 1, lifecycle_revision = 1 WHERE hash = ?",
            (relation_hash,),
        )
    first = _projection_jobs(store)
    assert len(first) == 1
    assert first[0]["desired_active"] == 0
    assert first[0]["job_revision"] == 1

    with store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE relations SET lifecycle_revision = 2 WHERE hash = ?",
            (relation_hash,),
        )
    assert _projection_jobs(store) == first

    with store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE relations SET is_inactive = 0, lifecycle_revision = 3 WHERE hash = ?",
            (relation_hash,),
        )
    final = _projection_jobs(store)
    assert len(final) == 1
    assert final[0]["desired_active"] == 1
    assert final[0]["desired_lifecycle_revision"] == 3
    assert final[0]["job_revision"] == 2

    store.clear_all()
    assert _projection_jobs(store) == []
    store.close()


def test_unchanged_active_access_creates_no_projection_job_or_graph_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("访问用户", "读取", "活跃关系")
    graph.add_edges([("访问用户", "活跃关系")], relation_hashes=[relation_hash])
    graph.save()
    anchor = float((store.get_relation(relation_hash) or {})["retention_anchor_at"])
    save_calls = 0
    original_save = graph.save

    def save_spy() -> None:
        nonlocal save_calls
        save_calls += 1
        original_save()

    monkeypatch.setattr(graph, "save", save_spy)
    transitions = kernel._maintenance_service.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.ACCESS,
        now=anchor + 1.0,
    )

    assert transitions[0]["is_inactive"] is False
    assert _projection_jobs(store) == []
    assert save_calls == 0
    store.close()


def test_new_evidence_revival_is_queued_and_background_reconcile_converges(
    tmp_path: Path,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("证据用户", "关联", "复活目标")
    graph.add_edges([("证据用户", "复活目标")], relation_hashes=[relation_hash])
    graph.save()
    store.mark_relations_inactive([relation_hash], reason="test")
    kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    assert _disk_hashes(tmp_path / "graph", "证据用户", "复活目标") == set()

    paragraph_hash = store.add_paragraph("新的独立证据", source="projection-test")
    assert store.link_paragraph_relation(paragraph_hash, relation_hash) is True
    jobs = _projection_jobs(store)
    assert len(jobs) == 1
    assert jobs[0]["desired_active"] == 1

    kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "证据用户", "复活目标") == {
        relation_hash
    }
    store.close()


def test_save_failure_is_durable_and_retry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("保存失败", "仍可", "重试")
    graph.add_edges([("保存失败", "重试")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)
    original_save = graph.save
    attempts = 0

    def fail_once() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected graph save failure")
        original_save()

    monkeypatch.setattr(graph, "save", fail_once)
    with pytest.raises(OSError, match="injected graph save failure"):
        kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    failed = _projection_jobs(store)
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"
    assert failed[0]["attempt_count"] == 1
    assert _disk_hashes(tmp_path / "graph", "保存失败", "重试") == {relation_hash}

    kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    assert attempts == 2
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "保存失败", "重试") == set()
    store.close()


def test_hard_exit_before_save_is_recovered_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("硬退出", "发生于", "保存前")
    graph.add_edges([("硬退出", "保存前")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)

    def hard_exit() -> None:
        raise SystemExit("injected hard exit")

    monkeypatch.setattr(graph, "save", hard_exit)
    with pytest.raises(SystemExit, match="injected hard exit"):
        kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    running = _projection_jobs(store)
    assert len(running) == 1
    assert running[0]["status"] == "running"
    assert _disk_hashes(tmp_path / "graph", "硬退出", "保存前") == {relation_hash}

    store.close()
    recovered_store = MetadataStore(data_dir=tmp_path / "metadata")
    recovered_store.connect()
    recovered_graph = GraphStore(data_dir=tmp_path / "graph")
    recovered_graph.load()
    kernel.metadata_store = recovered_store
    kernel.graph_store = recovered_graph
    kernel._maintenance_service._reconcile_relation_graph_projection_jobs(
        reset_leases=True,
    )
    assert _projection_jobs(recovered_store) == []
    assert _disk_hashes(tmp_path / "graph", "硬退出", "保存前") == set()
    recovered_store.close()


def test_hard_exit_after_save_before_cas_is_replayed_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("硬退出", "发生于", "CAS前")
    graph.add_edges([("硬退出", "CAS前")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)
    def hard_exit_before_cas(*args, **kwargs) -> int:
        del args, kwargs
        raise SystemExit("injected post-save hard exit")

    monkeypatch.setattr(
        store,
        "complete_relation_graph_projection_jobs",
        hard_exit_before_cas,
    )
    with pytest.raises(SystemExit, match="injected post-save hard exit"):
        kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    assert _projection_jobs(store)[0]["status"] == "running"
    assert _disk_hashes(tmp_path / "graph", "硬退出", "CAS前") == set()

    store.close()
    recovered_store = MetadataStore(data_dir=tmp_path / "metadata")
    recovered_store.connect()
    recovered_graph = GraphStore(data_dir=tmp_path / "graph")
    recovered_graph.load()
    kernel.metadata_store = recovered_store
    kernel.graph_store = recovered_graph
    kernel._maintenance_service._reconcile_relation_graph_projection_jobs(
        reset_leases=True,
    )
    assert _projection_jobs(recovered_store) == []
    assert _disk_hashes(tmp_path / "graph", "硬退出", "CAS前") == set()
    recovered_store.close()


def test_higher_revision_survives_old_completion_cas(tmp_path: Path) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("版本CAS", "保护", "新状态")
    graph.add_edges([("版本CAS", "新状态")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)

    claimed = store.claim_relation_graph_projection_jobs(limit=10, lease_seconds=300.0)
    authorized = store.authorize_relation_graph_projection_jobs(claimed)
    assert len(authorized) == 1
    graph.prune_relation_hashes([("版本CAS", "新状态", relation_hash)])
    graph.save()
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE relations
            SET is_inactive = 0,
                inactive_since = NULL,
                inactive_reason = NULL,
                lifecycle_revision = lifecycle_revision + 1
            WHERE hash = ?
            """,
            (relation_hash,),
        )

    assert store.complete_relation_graph_projection_jobs(authorized) == 0
    newer = _projection_jobs(store)
    assert len(newer) == 1
    assert newer[0]["desired_active"] == 1
    assert newer[0]["job_revision"] == 2
    assert newer[0]["status"] == "pending"

    kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "版本CAS", "新状态") == {
        relation_hash
    }
    store.close()


def test_partial_authorization_never_leaves_silent_running_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hashes = [
        store.add_relation("部分授权", f"关系-{index}", f"目标-{index}")
        for index in range(2)
    ]
    graph.add_edges(
        [("部分授权", f"目标-{index}") for index in range(2)],
        relation_hashes=relation_hashes,
    )
    graph.save()
    for relation_hash in relation_hashes:
        _flip_inactive(store, relation_hash)
    original_authorize = store.authorize_relation_graph_projection_jobs

    def authorize_first(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return original_authorize(jobs)[:1]

    monkeypatch.setattr(
        store,
        "authorize_relation_graph_projection_jobs",
        authorize_first,
    )
    kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    remaining = _projection_jobs(store)
    assert len(remaining) == 1
    assert remaining[0]["status"] == "failed"
    assert all(item["status"] != "running" for item in remaining)

    monkeypatch.setattr(
        store,
        "authorize_relation_graph_projection_jobs",
        original_authorize,
    )
    kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    assert _projection_jobs(store) == []
    store.close()


def test_startup_authoritative_rebuild_removes_ghost_edges(tmp_path: Path) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("权威重建", "保留", "真实边")
    graph.add_edges(
        [("权威重建", "真实边"), ("幽灵节点", "幽灵目标")],
        relation_hashes=[relation_hash, "ghost-relation-hash"],
    )
    graph.save()
    with store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE relations SET is_inactive = 1, lifecycle_revision = 1 WHERE hash = ?",
            (relation_hash,),
        )
        connection.execute(
            "UPDATE relations SET is_inactive = 0, lifecycle_revision = 2 WHERE hash = ?",
            (relation_hash,),
        )

    kernel._maintenance_service._reconcile_relation_graph_projection_jobs(
        reset_leases=True,
    )
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "权威重建", "真实边") == {
        relation_hash
    }
    assert _disk_hashes(tmp_path / "graph", "幽灵节点", "幽灵目标") == set()
    store.close()


def test_startup_authoritative_rebuild_removes_ghost_edges_with_empty_queue(
    tmp_path: Path,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("空队列重建", "保留", "真实边")
    graph.add_edges(
        [("空队列重建", "真实边"), ("空队列幽灵", "幽灵目标")],
        relation_hashes=[relation_hash, "empty-queue-ghost-hash"],
    )
    graph.save()
    assert _projection_jobs(store) == []

    result = kernel._maintenance_service._reconcile_relation_graph_projection_jobs(
        reset_leases=True,
    )

    assert result == {"claimed": 0, "completed": 0, "saved_batches": 1}
    assert _disk_hashes(tmp_path / "graph", "空队列重建", "真实边") == {
        relation_hash
    }
    assert _disk_hashes(tmp_path / "graph", "空队列幽灵", "幽灵目标") == set()
    store.close()


def test_revision_change_before_save_rebuilds_authoritative_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("保存前", "版本", "发生变化")
    graph.add_edges([("保存前", "发生变化")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)
    original_prune = graph.prune_relation_hashes
    changed = False

    def prune_and_change_revision(
        operations: List[Tuple[str, str, str]],
    ) -> None:
        nonlocal changed
        original_prune(operations)
        if changed:
            return
        changed = True
        with store.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE relations
                SET is_inactive = 0,
                    inactive_since = NULL,
                    inactive_reason = NULL,
                    lifecycle_revision = lifecycle_revision + 1
                WHERE hash = ?
                """,
                (relation_hash,),
            )

    monkeypatch.setattr(graph, "prune_relation_hashes", prune_and_change_revision)
    result = kernel._maintenance_service._reconcile_relation_graph_projection_jobs()

    assert result == {"claimed": 2, "completed": 1, "saved_batches": 1}
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "保存前", "发生变化") == {
        relation_hash
    }
    store.close()


def test_late_old_save_after_new_worker_completion_is_repaired_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("旧写者", "不能覆盖", "新快照")
    graph.add_edges([("旧写者", "新快照")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)
    original_save = graph.save
    injected = False

    def save_after_new_worker_completed() -> None:
        nonlocal injected
        if not injected:
            injected = True
            with store.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE relations
                    SET is_inactive = 0,
                        inactive_since = NULL,
                        inactive_reason = NULL,
                        lifecycle_revision = lifecycle_revision + 1
                    WHERE hash = ?
                    """,
                    (relation_hash,),
                )
            worker_claim = store.claim_relation_graph_projection_jobs(
                limit=10,
                lease_seconds=300.0,
            )
            worker_authorized = store.authorize_relation_graph_projection_jobs(
                worker_claim
            )
            assert len(worker_authorized) == 1
            worker_graph = GraphStore(data_dir=tmp_path / "graph")
            worker_graph.load()
            worker_graph.add_edges(
                [("旧写者", "新快照")],
                relation_hashes=[relation_hash],
            )
            worker_graph.save()
            assert (
                store.complete_relation_graph_projection_jobs(worker_authorized) == 1
            )
        # 新写者已经CAS完成且删除队列后，旧写者才把失活快照落盘。
        original_save()

    monkeypatch.setattr(graph, "save", save_after_new_worker_completed)
    result = kernel._maintenance_service._reconcile_relation_graph_projection_jobs()

    assert result == {"claimed": 2, "completed": 1, "saved_batches": 2}
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "旧写者", "新快照") == {
        relation_hash
    }
    store.close()


def test_same_sdk_projection_workers_are_serialized_across_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("实例锁", "覆盖", "完整临界区")
    graph.add_edges([("实例锁", "完整临界区")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)
    original_save = graph.save
    first_save_entered = Event()
    allow_first_save = Event()
    second_started = Event()
    second_finished = Event()
    errors: List[BaseException] = []
    save_calls = 0

    def blocking_first_save() -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            first_save_entered.set()
            if not allow_first_save.wait(timeout=5.0):
                raise TimeoutError("等待首个投影保存超时")
        original_save()

    def run_first() -> None:
        try:
            kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
        except BaseException as exc:
            errors.append(exc)

    def run_second() -> None:
        second_started.set()
        try:
            kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
        except BaseException as exc:
            errors.append(exc)
        finally:
            second_finished.set()

    monkeypatch.setattr(graph, "save", blocking_first_save)
    first = Thread(target=run_first)
    first.start()
    assert first_save_entered.wait(timeout=5.0)
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE relations
            SET is_inactive = 0,
                inactive_since = NULL,
                inactive_reason = NULL,
                lifecycle_revision = lifecycle_revision + 1
            WHERE hash = ?
            """,
            (relation_hash,),
        )

    second = Thread(target=run_second)
    second.start()
    assert second_started.wait(timeout=5.0)
    assert second_finished.wait(timeout=0.2) is False
    allow_first_save.set()
    first.join(timeout=5.0)
    second.join(timeout=5.0)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert errors == []
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "实例锁", "完整临界区") == {
        relation_hash
    }
    store.close()


def test_5000_projection_jobs_batch_500_publish_one_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relations = [
        ("批量投影", f"关系-{index}", f"目标-{index}")
        for index in range(5000)
    ]
    relation_hashes = store.add_relations_batch(relations)
    graph.add_edges(
        [(subject, obj) for subject, _, obj in relations],
        relation_hashes=relation_hashes,
    )
    graph.save()
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE relations
            SET is_inactive = 1,
                inactive_since = retention_anchor_at,
                inactive_reason = 'batch-test',
                lifecycle_revision = lifecycle_revision + 1
            """
        )
    original_save = graph.save
    save_calls = 0

    def save_spy() -> None:
        nonlocal save_calls
        save_calls += 1
        original_save()

    monkeypatch.setattr(graph, "save", save_spy)
    result = kernel._maintenance_service._reconcile_relation_graph_projection_jobs(
        batch_size=500,
    )

    assert result == {"claimed": 5000, "completed": 5000, "saved_batches": 1}
    assert save_calls == 1
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "批量投影", "目标-0") == set()
    assert _disk_hashes(tmp_path / "graph", "批量投影", "目标-4999") == set()
    store.close()


def test_bounded_pre_save_churn_keeps_intent_and_repairs_memory_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("持续变化", "保存前", "有界退出")
    graph.add_edges([("持续变化", "有界退出")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)
    original_authorize = store.authorize_relation_graph_projection_jobs
    authorize_calls = 0

    def flip_authoritative_state() -> None:
        with store.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT is_inactive FROM relations WHERE hash = ?",
                (relation_hash,),
            ).fetchone()
            next_inactive = 0 if bool(row["is_inactive"]) else 1
            connection.execute(
                """
                UPDATE relations
                SET is_inactive = ?,
                    inactive_since = CASE WHEN ? = 1 THEN retention_anchor_at ELSE NULL END,
                    inactive_reason = CASE WHEN ? = 1 THEN 'churn' ELSE NULL END,
                    lifecycle_revision = lifecycle_revision + 1
                WHERE hash = ?
                """,
                (next_inactive, next_inactive, next_inactive, relation_hash),
            )

    def authorize_with_pre_save_churn(
        jobs: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        nonlocal authorize_calls
        authorize_calls += 1
        if authorize_calls in {3, 6, 9}:
            flip_authoritative_state()
        return original_authorize(jobs)

    monkeypatch.setattr(
        store,
        "authorize_relation_graph_projection_jobs",
        authorize_with_pre_save_churn,
    )
    with pytest.raises(RuntimeError, match="保存前持续变化"):
        kernel._maintenance_service._reconcile_relation_graph_projection_jobs()

    pending = _projection_jobs(store)
    assert authorize_calls == 9
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert set(
        graph.get_relation_hashes_for_edge("持续变化", "有界退出")
    ) == {relation_hash}

    monkeypatch.setattr(
        store,
        "authorize_relation_graph_projection_jobs",
        original_authorize,
    )
    kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    assert _projection_jobs(store) == []
    assert _disk_hashes(tmp_path / "graph", "持续变化", "有界退出") == {
        relation_hash
    }
    store.close()


def test_bounded_cas_churn_repairs_disk_and_keeps_retry_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, store, graph = _runtime(tmp_path)
    relation_hash = store.add_relation("持续冲突", "CAS", "权威恢复")
    graph.add_edges([("持续冲突", "权威恢复")], relation_hashes=[relation_hash])
    graph.save()
    _flip_inactive(store, relation_hash)
    original_authorize = store.authorize_relation_graph_projection_jobs
    authorize_calls = 0

    def flip_authoritative_state() -> None:
        with store.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT is_inactive FROM relations WHERE hash = ?",
                (relation_hash,),
            ).fetchone()
            next_inactive = 0 if bool(row["is_inactive"]) else 1
            connection.execute(
                """
                UPDATE relations
                SET is_inactive = ?,
                    inactive_since = CASE WHEN ? = 1 THEN retention_anchor_at ELSE NULL END,
                    inactive_reason = CASE WHEN ? = 1 THEN 'churn' ELSE NULL END,
                    lifecycle_revision = lifecycle_revision + 1
                WHERE hash = ?
                """,
                (next_inactive, next_inactive, next_inactive, relation_hash),
            )

    def authorize_with_post_check_churn(
        jobs: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        nonlocal authorize_calls
        authorize_calls += 1
        authorized = original_authorize(jobs)
        if authorize_calls in {3, 6, 9}:
            flip_authoritative_state()
        return authorized

    monkeypatch.setattr(
        store,
        "authorize_relation_graph_projection_jobs",
        authorize_with_post_check_churn,
    )
    with pytest.raises(RuntimeError, match="CAS 持续冲突"):
        kernel._maintenance_service._reconcile_relation_graph_projection_jobs()

    pending = _projection_jobs(store)
    assert authorize_calls == 9
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert _disk_hashes(tmp_path / "graph", "持续冲突", "权威恢复") == {
        relation_hash
    }
    assert set(
        graph.get_relation_hashes_for_edge("持续冲突", "权威恢复")
    ) == {relation_hash}

    monkeypatch.setattr(
        store,
        "authorize_relation_graph_projection_jobs",
        original_authorize,
    )
    kernel._maintenance_service._reconcile_relation_graph_projection_jobs()
    assert _projection_jobs(store) == []
    store.close()
