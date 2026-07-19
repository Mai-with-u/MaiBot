from pathlib import Path
from random import Random

import pytest

from src.A_memorix.core.runtime.services import memory_maintenance_service, v5_admin_service
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
    threshold_crossing_at,
)
from src.config.official_configs import AMemorixMemoryEvolutionConfig


def _policy(*, access_cooldown_seconds: float = 3600.0) -> RelationLifecyclePolicy:
    return RelationLifecyclePolicy(
        half_life_hours=24.0,
        freeze_threshold=0.1,
        revive_threshold=0.15,
        access_alpha=0.05,
        access_cooldown_seconds=access_cooldown_seconds,
        reinforce_alpha=0.5,
        weaken_alpha=0.5,
    )


def _metadata_store(tmp_path: Path) -> MetadataStore:
    store = MetadataStore(data_dir=tmp_path / "metadata")
    store.connect()
    return store


def test_wall_clock_decay_is_idempotent_and_downtime_equivalent() -> None:
    policy = _policy()
    state = RelationLifecycleState(strength=1.0, anchor_at=0.0)
    now = 72.0 * 3600.0

    direct = retention_at(state, now=now, policy=policy)
    repeated_reads = [retention_at(state, now=now, policy=policy) for _ in range(1000)]
    decision_a = evaluate_lifecycle(state, now=now, policy=policy)
    decision_b = evaluate_lifecycle(state, now=now, policy=policy)

    assert direct == pytest.approx(0.125, abs=1e-15)
    assert max(abs(item - direct) for item in repeated_reads) == 0.0
    assert decision_a == decision_b
    assert decision_a.next_lifecycle_at == pytest.approx(24.0 * 3600.0 * 3.321928094887362)

    at_threshold = evaluate_lifecycle(
        state,
        now=threshold_crossing_at(strength=1.0, anchor_at=0.0, policy=policy),
        policy=policy,
    )
    assert at_threshold.is_inactive is True
    assert at_threshold.inactive_reason == "decay"


def test_lifecycle_formula_matches_randomized_closed_form() -> None:
    policy = _policy()
    rng = Random(20260715)
    maximum_error = 0.0

    for _ in range(5000):
        strength = rng.random()
        anchor = rng.uniform(0.0, 30.0 * 86400.0)
        elapsed = rng.uniform(0.0, 180.0 * 86400.0)
        state = RelationLifecycleState(strength=strength, anchor_at=anchor)
        expected = strength * 2.0 ** (-(elapsed / 3600.0) / policy.half_life_hours)
        actual = retention_at(state, now=anchor + elapsed, policy=policy)
        maximum_error = max(maximum_error, abs(actual - expected))

    assert maximum_error < 1e-15


def test_continuous_decay_has_semigroup_property() -> None:
    policy = _policy()
    rng = Random(20260716)
    maximum_error = 0.0

    for _ in range(5000):
        strength = rng.random()
        anchor = rng.uniform(0.0, 30.0 * 86400.0)
        middle = anchor + rng.uniform(0.0, 30.0 * 86400.0)
        end = middle + rng.uniform(0.0, 180.0 * 86400.0)
        direct = retention_at(
            RelationLifecycleState(strength=strength, anchor_at=anchor),
            now=end,
            policy=policy,
        )
        middle_strength = retention_at(
            RelationLifecycleState(strength=strength, anchor_at=anchor),
            now=middle,
            policy=policy,
        )
        segmented = retention_at(
            RelationLifecycleState(strength=middle_strength, anchor_at=middle),
            now=end,
            policy=policy,
        )
        maximum_error = max(maximum_error, abs(direct - segmented))

    assert maximum_error < 1e-15


@pytest.mark.parametrize(
    "overrides",
    [
        {"half_life_hours": 0.0},
        {"freeze_threshold": 0.0},
        {"freeze_threshold": 1.0},
        {"freeze_threshold": 0.2, "revive_threshold": 0.2},
        {"access_alpha": -0.01},
        {"access_cooldown_seconds": -1.0},
        {"access_cooldown_seconds": float("nan")},
        {"reinforce_alpha": 1.01},
        {"weaken_alpha": float("nan")},
    ],
)
def test_invalid_lifecycle_configuration_is_rejected(overrides: dict[str, float]) -> None:
    values = {
        "half_life_hours": 24.0,
        "freeze_threshold": 0.1,
        "revive_threshold": 0.15,
        "access_alpha": 0.05,
        "access_cooldown_seconds": 3600.0,
        "reinforce_alpha": 0.5,
        "weaken_alpha": 0.5,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        RelationLifecyclePolicy(**values)


def test_official_lifecycle_config_rejects_invalid_cooldown_and_threshold_order() -> None:
    valid = AMemorixMemoryEvolutionConfig(
        prune_threshold=0.1,
        revive_threshold=0.15,
        access_reinforcement_cooldown_minutes=60.0,
    )
    assert valid.access_reinforcement_cooldown_minutes == 60.0

    with pytest.raises(ValueError):
        AMemorixMemoryEvolutionConfig(access_reinforcement_cooldown_minutes=-1.0)
    with pytest.raises(ValueError):
        AMemorixMemoryEvolutionConfig(prune_threshold=0.2, revive_threshold=0.2)
    with pytest.raises(ValueError):
        AMemorixMemoryEvolutionConfig(prune_threshold=0.0)


def test_access_and_explicit_reinforcement_are_bounded_and_extend_lifetime() -> None:
    policy = _policy(access_cooldown_seconds=0.0)
    state = RelationLifecycleState(strength=1.0, anchor_at=0.0)
    now = 72.0 * 3600.0
    original_due = threshold_crossing_at(strength=1.0, anchor_at=0.0, policy=policy)

    accessed = apply_lifecycle_event(
        state,
        RelationLifecycleEvent.ACCESS,
        now=now,
        policy=policy,
    )
    reinforced = apply_lifecycle_event(
        state,
        RelationLifecycleEvent.REINFORCE,
        now=now,
        policy=policy,
    )

    assert accessed.strength == pytest.approx(0.16875)
    assert reinforced.strength == pytest.approx(0.5625)
    assert accessed.next_lifecycle_at is not None and accessed.next_lifecycle_at > original_due
    assert reinforced.next_lifecycle_at is not None and reinforced.next_lifecycle_at > accessed.next_lifecycle_at

    saturated = state
    for index in range(1000):
        decision = apply_lifecycle_event(
            saturated,
            RelationLifecycleEvent.ACCESS,
            now=float(index),
            policy=policy,
        )
        saturated = RelationLifecycleState(
            strength=decision.strength,
            anchor_at=decision.anchor_at,
            is_inactive=decision.is_inactive,
            inactive_since=decision.inactive_since,
            inactive_reason=decision.inactive_reason,
        )
    assert 0.0 <= saturated.strength <= 1.0


def test_lifecycle_hysteresis_prevents_threshold_oscillation() -> None:
    policy = _policy(access_cooldown_seconds=0.0)
    inactive = RelationLifecycleState(
        strength=0.08,
        anchor_at=0.0,
        is_inactive=True,
        inactive_since=0.0,
        inactive_reason="decay",
    )

    first_access = apply_lifecycle_event(
        inactive,
        RelationLifecycleEvent.ACCESS,
        now=0.0,
        policy=policy,
    )
    second_access = apply_lifecycle_event(
        RelationLifecycleState(
            strength=first_access.strength,
            anchor_at=first_access.anchor_at,
            is_inactive=first_access.is_inactive,
            inactive_since=first_access.inactive_since,
            inactive_reason=first_access.inactive_reason,
        ),
        RelationLifecycleEvent.ACCESS,
        now=0.0,
        policy=policy,
    )

    assert first_access.strength == pytest.approx(0.126)
    assert first_access.is_inactive is True
    assert second_access.strength == pytest.approx(0.1697)
    assert second_access.is_inactive is False

    manually_frozen = RelationLifecycleState(
        strength=1.0,
        anchor_at=0.0,
        is_inactive=True,
        inactive_since=0.0,
        inactive_reason="manual_freeze",
    )
    revived_without_score_change = apply_lifecycle_event(
        manually_frozen,
        RelationLifecycleEvent.ACCESS,
        now=0.0,
        policy=policy,
    )
    assert revived_without_score_change.strength == 1.0
    assert revived_without_score_change.is_inactive is False
    assert revived_without_score_change.changed is True


def test_access_cooldown_bounds_high_frequency_reinforcement() -> None:
    policy = _policy()
    no_cooldown_policy = RelationLifecyclePolicy(
        half_life_hours=24.0,
        freeze_threshold=0.1,
        revive_threshold=0.15,
        access_alpha=0.05,
        access_cooldown_seconds=0.0,
        reinforce_alpha=0.5,
        weaken_alpha=0.5,
    )
    start = 72.0 * 3600.0
    event_times = [start + (59.0 * 60.0 * index / 9999.0) for index in range(10_000)]

    def run_events(event_policy: RelationLifecyclePolicy) -> tuple[RelationLifecycleState, int]:
        state = RelationLifecycleState(strength=1.0, anchor_at=0.0)
        changed = 0
        for event_time in event_times:
            decision = apply_lifecycle_event(
                state,
                RelationLifecycleEvent.ACCESS,
                now=event_time,
                policy=event_policy,
            )
            changed += int(decision.changed)
            state = RelationLifecycleState(
                strength=decision.strength,
                anchor_at=decision.anchor_at,
                is_inactive=decision.is_inactive,
                inactive_since=decision.inactive_since,
                inactive_reason=decision.inactive_reason,
                last_access_reinforced_at=decision.last_access_reinforced_at,
            )
        return state, changed

    cooled_state, cooled_changes = run_events(policy)
    unrestricted_state, unrestricted_changes = run_events(no_cooldown_policy)
    end = event_times[-1]
    cooled_score = retention_at(cooled_state, now=end, policy=policy)
    unrestricted_score = retention_at(unrestricted_state, now=end, policy=no_cooldown_policy)

    assert cooled_changes == 1
    assert unrestricted_changes == 10_000
    expected_cooled_score = 0.16875 * 2.0 ** (-(59.0 / 60.0) / 24.0)
    assert cooled_score == pytest.approx(expected_cooled_score, abs=1e-15)
    assert unrestricted_score > 0.999

    at_boundary = apply_lifecycle_event(
        cooled_state,
        RelationLifecycleEvent.ACCESS,
        now=start + policy.access_cooldown_seconds,
        policy=policy,
    )
    assert at_boundary.changed is True
    assert at_boundary.last_access_reinforced_at == pytest.approx(start + 3600.0)

    overdue_during_cooldown = apply_lifecycle_event(
        RelationLifecycleState(
            strength=1.0,
            anchor_at=0.0,
            last_access_reinforced_at=80.0 * 3600.0 - 60.0,
        ),
        RelationLifecycleEvent.ACCESS,
        now=80.0 * 3600.0,
        policy=policy,
    )
    assert overdue_during_cooldown.is_inactive is True
    assert overdue_during_cooldown.inactive_reason == "decay"


def test_new_evidence_link_is_idempotent_in_real_sqlite(tmp_path: Path) -> None:
    store = _metadata_store(tmp_path)
    first_paragraph = store.add_paragraph("第一条独立证据", source="source-a")
    relation_hash = store.add_relation(
        "Alice",
        "knows",
        "Bob",
        confidence=0.73,
        source_paragraph=first_paragraph,
    )
    first = store.get_relation_status_batch([relation_hash])[relation_hash]
    assert first["confidence"] == pytest.approx(0.73)

    assert store.link_paragraph_relation(first_paragraph, relation_hash) is False
    duplicate = store.get_relation_status_batch([relation_hash])[relation_hash]
    assert duplicate["reinforcement_count"] == first["reinforcement_count"] == 1
    assert duplicate["lifecycle_revision"] == first["lifecycle_revision"] == 1

    second_paragraph = store.add_paragraph("第二条独立证据", source="source-a")
    assert store.link_paragraph_relation(second_paragraph, relation_hash) is True
    second = store.get_relation_status_batch([relation_hash])[relation_hash]
    relation = store.get_relation(relation_hash) or {}

    assert second["reinforcement_count"] == 2
    assert second["lifecycle_revision"] == 2
    assert second["retention_strength"] == 1.0
    assert relation["confidence"] == pytest.approx(0.73)
    store.close()


def test_unsetting_permanence_reenters_lifecycle_queue_once(tmp_path: Path) -> None:
    store = _metadata_store(tmp_path)
    relation_hash = store.add_relation("Alice", "owns", "Map")
    before = store.get_relation_status_batch([relation_hash])[relation_hash]

    assert store.set_permanence(relation_hash, "relation", True) is True
    pinned = store.get_relation_status_batch([relation_hash])[relation_hash]
    assert pinned["is_pinned"] is True
    assert pinned["next_lifecycle_at"] is None
    assert pinned["lifecycle_revision"] == before["lifecycle_revision"] + 1
    assert store.set_permanence(relation_hash, "relation", True) is False

    assert store.set_permanence(relation_hash, "relation", False) is True
    active = store.get_relation_status_batch([relation_hash])[relation_hash]
    assert active["is_pinned"] is False
    assert active["next_lifecycle_at"] == pytest.approx(active["retention_anchor_at"])
    assert active["lifecycle_revision"] == pinned["lifecycle_revision"] + 1
    assert store.set_permanence(relation_hash, "relation", False) is False
    store.close()


def test_unpinning_reenters_lifecycle_queue_once(tmp_path: Path) -> None:
    store = _metadata_store(tmp_path)
    relation_hash = store.add_relation("Alice", "visits", "Library")
    before = store.get_relation_status_batch([relation_hash])[relation_hash]

    store.update_relations_protection([relation_hash], is_pinned=True)
    pinned = store.get_relation_status_batch([relation_hash])[relation_hash]
    assert pinned["is_pinned"] is True
    assert pinned["next_lifecycle_at"] is None
    assert pinned["lifecycle_revision"] == before["lifecycle_revision"] + 1
    store.update_relations_protection([relation_hash], is_pinned=True)
    assert store.get_relation_status_batch([relation_hash])[relation_hash] == pinned

    store.update_relations_protection([relation_hash], is_pinned=False)
    unpinned = store.get_relation_status_batch([relation_hash])[relation_hash]
    assert unpinned["is_pinned"] is False
    assert unpinned["next_lifecycle_at"] == pytest.approx(unpinned["retention_anchor_at"])
    assert unpinned["lifecycle_revision"] == pinned["lifecycle_revision"] + 1
    store.update_relations_protection([relation_hash], is_pinned=False)
    assert store.get_relation_status_batch([relation_hash])[relation_hash] == unpinned
    store.close()


def test_access_event_updates_retention_without_changing_confidence(tmp_path: Path) -> None:
    store = _metadata_store(tmp_path)
    relation_hash = store.add_relation("Alice", "visits", "Paris", confidence=0.61)
    conn = store.get_connection()
    conn.execute(
        """
        UPDATE relations
        SET retention_strength = 1.0,
            retention_anchor_at = 0.0,
            next_lifecycle_at = NULL,
            lifecycle_revision = 0
        WHERE hash = ?
        """,
        (relation_hash,),
    )
    conn.commit()

    transitions = store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.ACCESS,
        policy=_policy(),
        now=72.0 * 3600.0,
    )
    status = store.get_relation_status_batch([relation_hash])[relation_hash]
    relation = store.get_relation(relation_hash) or {}

    assert transitions[0]["retention_score"] == pytest.approx(0.16875)
    assert status["retention_strength"] == pytest.approx(0.16875)
    assert status["lifecycle_revision"] == 1
    assert relation["access_count"] == 1
    assert relation["last_accessed"] == pytest.approx(72.0 * 3600.0)
    assert relation["last_reinforced"] is None
    assert status["last_access_reinforced_at"] == pytest.approx(72.0 * 3600.0)
    assert relation["confidence"] == pytest.approx(0.61)
    store.close()


def test_access_cooldown_records_exposure_without_repeated_lifecycle_writes(tmp_path: Path) -> None:
    store = _metadata_store(tmp_path)
    relation_hash = store.add_relation("Alice", "reads", "Atlas", confidence=0.64)
    conn = store.get_connection()
    conn.execute(
        """
        UPDATE relations
        SET retention_strength = 1.0,
            retention_anchor_at = 0.0,
            next_lifecycle_at = 0.0,
            lifecycle_revision = 0,
            access_count = 0,
            last_accessed = NULL,
            last_access_reinforced_at = NULL
        WHERE hash = ?
        """,
        (relation_hash,),
    )
    conn.commit()
    now = 72.0 * 3600.0

    for _ in range(100):
        store.apply_relation_lifecycle_event(
            [relation_hash],
            event=RelationLifecycleEvent.ACCESS,
            policy=_policy(),
            now=now,
        )
    cooled = store.get_relation_status_batch([relation_hash])[relation_hash]
    relation = store.get_relation(relation_hash) or {}

    assert relation["access_count"] == 100
    assert relation["last_accessed"] == pytest.approx(now)
    assert cooled["retention_strength"] == pytest.approx(0.16875)
    assert cooled["lifecycle_revision"] == 1
    assert cooled["last_access_reinforced_at"] == pytest.approx(now)

    store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.ACCESS,
        policy=_policy(),
        now=now + 3600.0,
    )
    boundary = store.get_relation_status_batch([relation_hash])[relation_hash]
    assert boundary["lifecycle_revision"] == 2
    assert boundary["last_access_reinforced_at"] == pytest.approx(now + 3600.0)
    store.close()


def test_relation_tombstone_round_trip_preserves_lifecycle_state(tmp_path: Path) -> None:
    store = _metadata_store(tmp_path)
    relation_hash = store.add_relation("Alice", "trusts", "Bob", confidence=0.82)
    store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.REINFORCE,
        policy=_policy(),
        now=48.0 * 3600.0,
    )
    store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.ACCESS,
        policy=_policy(),
        now=49.0 * 3600.0,
    )
    before = store.get_relation_status_batch([relation_hash])[relation_hash]

    assert store.backup_and_delete_relations([relation_hash]) == 1
    deleted = store.get_deleted_relation(relation_hash) or {}
    assert deleted["retention_strength"] == pytest.approx(before["retention_strength"])
    assert deleted["retention_anchor_at"] == pytest.approx(before["retention_anchor_at"])
    assert deleted["next_lifecycle_at"] == pytest.approx(before["next_lifecycle_at"])
    assert deleted["reinforcement_count"] == before["reinforcement_count"]
    assert deleted["lifecycle_revision"] == before["lifecycle_revision"]
    assert deleted["last_access_reinforced_at"] == before["last_access_reinforced_at"]

    assert store.restore_relation(relation_hash) is not None
    restored = store.get_relation_status_batch([relation_hash])[relation_hash]
    for field in (
        "retention_strength",
        "retention_anchor_at",
        "next_lifecycle_at",
        "reinforcement_count",
        "inactive_reason",
        "last_access_reinforced_at",
    ):
        assert restored[field] == before[field]
    assert restored["lifecycle_revision"] == before["lifecycle_revision"] + 1
    store.close()


def test_relation_status_snapshot_restores_access_cooldown_state(tmp_path: Path) -> None:
    store = _metadata_store(tmp_path)
    relation_hash = store.add_relation("Alice", "maps", "Archive", confidence=0.76)
    store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.ACCESS,
        policy=_policy(),
        now=48.0 * 3600.0,
    )
    before = store.get_relation_status_batch([relation_hash])[relation_hash]
    store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.FORGET,
        policy=_policy(),
        now=49.0 * 3600.0,
    )
    mutated = store.get_relation_status_batch([relation_hash])[relation_hash]

    restored = store.restore_relation_status_from_snapshot(relation_hash, before)

    assert restored is not None
    for field, expected in before.items():
        if field != "lifecycle_revision":
            assert restored[field] == expected
    assert restored["lifecycle_revision"] == max(
        before["lifecycle_revision"],
        mutated["lifecycle_revision"],
    ) + 1
    store.close()


@pytest.mark.asyncio
async def test_relation_lifecycle_is_isolated_per_hash_and_maintenance_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _metadata_store(tmp_path)
    graph = GraphStore(data_dir=tmp_path / "graph")
    relation_hashes = [
        store.add_relation("shared-source", f"predicate-{index}", "shared-target")
        for index in range(10)
    ]
    graph.add_edges(
        [("shared-source", "shared-target") for _ in relation_hashes],
        relation_hashes=relation_hashes,
    )

    now = 80.0 * 3600.0
    conn = store.get_connection()
    conn.execute(
        """
        UPDATE relations
        SET retention_strength = 1.0,
            retention_anchor_at = 0.0,
            next_lifecycle_at = 0.0,
            lifecycle_revision = 0,
            is_pinned = 0,
            is_permanent = 0,
            is_inactive = 0,
            inactive_since = NULL,
            inactive_reason = NULL
        """
    )
    conn.execute("UPDATE relations SET is_pinned = 1 WHERE hash = ?", (relation_hashes[0],))
    conn.commit()

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={
            "memory": {
                "half_life_hours": 24.0,
                "prune_threshold": 0.1,
                "revive_threshold": 0.15,
                "freeze_duration_hours": 24.0,
            }
        },
    )
    kernel.metadata_store = store
    kernel.graph_store = graph
    monkeypatch.setattr(memory_maintenance_service.time, "time", lambda: now)

    await kernel._process_freeze_and_prune()
    first_statuses = store.get_relation_status_batch(relation_hashes)
    first_revisions = {
        hash_value: int(status["lifecycle_revision"])
        for hash_value, status in first_statuses.items()
    }
    remaining_hashes = set(graph.get_relation_hashes_for_edge("shared-source", "shared-target"))

    await kernel._process_freeze_and_prune()
    second_statuses = store.get_relation_status_batch(relation_hashes)
    second_revisions = {
        hash_value: int(status["lifecycle_revision"])
        for hash_value, status in second_statuses.items()
    }
    lifecycle_before_rebuild = {
        hash_value: {
            key: status[key]
            for key in (
                "is_inactive",
                "inactive_since",
                "inactive_reason",
                "retention_strength",
                "retention_anchor_at",
                "next_lifecycle_at",
                "lifecycle_revision",
            )
        }
        for hash_value, status in second_statuses.items()
    }
    kernel._rebuild_graph_from_metadata()
    rebuilt_statuses = store.get_relation_status_batch(relation_hashes)
    lifecycle_after_rebuild = {
        hash_value: {
            key: status[key]
            for key in (
                "is_inactive",
                "inactive_since",
                "inactive_reason",
                "retention_strength",
                "retention_anchor_at",
                "next_lifecycle_at",
                "lifecycle_revision",
            )
        }
        for hash_value, status in rebuilt_statuses.items()
    }

    assert first_statuses[relation_hashes[0]]["is_inactive"] is False
    assert sum(bool(first_statuses[item]["is_inactive"]) for item in relation_hashes[1:]) == 9
    assert remaining_hashes == {relation_hashes[0]}
    assert first_revisions == second_revisions
    assert first_revisions[relation_hashes[0]] == 0
    assert {first_revisions[item] for item in relation_hashes[1:]} == {1}
    assert lifecycle_after_rebuild == lifecycle_before_rebuild
    assert set(graph.get_relation_hashes_for_edge("shared-source", "shared-target")) == {relation_hashes[0]}
    store.close()


def test_v5_reinforce_changes_retention_without_mutating_confidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _metadata_store(tmp_path)
    graph = GraphStore(data_dir=tmp_path / "graph")
    relation_hash = store.add_relation("Alice", "remembers", "Bob", confidence=0.67)
    graph.add_edges([("Alice", "Bob")], relation_hashes=[relation_hash])
    conn = store.get_connection()
    conn.execute(
        """
        UPDATE relations
        SET retention_strength = 1.0,
            retention_anchor_at = 0.0,
            next_lifecycle_at = 0.0,
            lifecycle_revision = 0
        WHERE hash = ?
        """,
        (relation_hash,),
    )
    conn.commit()

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
    now = 72.0 * 3600.0
    monkeypatch.setattr(v5_admin_service.time, "time", lambda: now)
    monkeypatch.setattr(kernel, "_persist", lambda: None)

    result = kernel._apply_v5_relation_action(
        action="reinforce",
        hashes=[relation_hash],
        strength=1.0,
    )
    relation = store.get_relation(relation_hash) or {}
    status = store.get_relation_status_batch([relation_hash])[relation_hash]

    assert result["success"] is True
    assert result["retention_scores"][relation_hash] == pytest.approx(0.5625)
    assert relation["confidence"] == pytest.approx(0.67)
    assert status["retention_strength"] == pytest.approx(0.5625)
    assert status["lifecycle_revision"] == 2
    assert status["last_reinforced"] == pytest.approx(now)
    store.close()
