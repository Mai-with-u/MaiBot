import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

import src.chat.replyer.expression_vector_index as vector_index_module

from src.chat.replyer.expression_vector_index import (
    CLUSTER_STATE_BOOTSTRAPPING,
    CLUSTER_STATE_STABLE,
    EMBEDDING_ITEM_FAILURE_ISOLATION_ATTEMPTS,
    FULL_RECLUSTER_CHANGE_RATIO,
    ExpressionEmbeddingProfile,
    ExpressionHistoryBackfillSelection,
    ExpressionVectorIndex,
    ExpressionVectorIndexUpsertItem,
    _atomic_write_text,
    expression_fingerprint,
)


def test_run_kmeans_repairs_empty_clusters_for_identical_vectors() -> None:
    """相同向量产生空簇时，应稳定拆分标签且不遗漏任何簇。"""

    vectors = np.array([[1.0, 0.0]] * 4, dtype=np.float32)

    first_labels = ExpressionVectorIndex._run_kmeans(vectors, cluster_count=3)
    second_labels = ExpressionVectorIndex._run_kmeans(vectors, cluster_count=3)

    assert np.array_equal(first_labels, second_labels)
    assert np.all(np.bincount(first_labels, minlength=3) > 0)


def test_repair_empty_cluster_does_not_take_single_member() -> None:
    """修复空簇时，不应迁移另一个簇的唯一成员。"""

    labels = np.array([0, 1, 1, 1], dtype=np.int32)
    similarities = np.array(
        [
            [-1.0, -1.0, -1.0],
            [0.0, 0.4, 0.0],
            [0.0, 0.5, 0.0],
            [0.0, 0.6, 0.0],
        ],
        dtype=np.float32,
    )

    repaired_labels = ExpressionVectorIndex._repair_empty_cluster_labels(
        labels,
        similarities,
        cluster_count=3,
    )

    assert repaired_labels[0] == 0
    assert np.all(np.bincount(repaired_labels, minlength=3) > 0)


def test_run_kmeans_rejects_more_clusters_than_samples() -> None:
    """簇数超过样本数时，应直接暴露无法满足的聚类约束。"""

    vectors = np.array([[1.0, 0.0]], dtype=np.float32)

    with pytest.raises(ValueError, match="聚类数量超过样本数量"):
        ExpressionVectorIndex._run_kmeans(vectors, cluster_count=2)


def test_incremental_update_assigns_changed_expression_to_nearest_cluster() -> None:
    """增量更新只重分配变化表达，并根据最终标签更新聚类中心。"""

    marker = "profile-marker"
    raw_expressions = [
        {
            "id": 1,
            "embedding_profile_marker": marker,
            "embedding_model": "test-model",
            "embedding_dimension": 2,
            "cluster_id": 0,
        },
        {
            "id": 2,
            "embedding_profile_marker": marker,
            "embedding_model": "test-model",
            "embedding_dimension": 2,
            "cluster_id": 1,
        },
        {
            "id": 3,
            "embedding_profile_marker": marker,
            "embedding_model": "test-model",
            "embedding_dimension": 2,
            "cluster_id": 0,
        },
    ]
    vector_by_expression_id = {
        1: np.array([1.0, 0.0], dtype=np.float32),
        2: np.array([-1.0, 0.0], dtype=np.float32),
        3: np.array([-0.9, 0.1], dtype=np.float32),
    }
    vector_index = ExpressionVectorIndex()

    _, cluster_centers, _ = vector_index._update_profile_arrays_incrementally(
        raw_expressions=raw_expressions,
        vector_by_expression_id=vector_by_expression_id,
        previous_profile_cluster_centers={
            marker: np.array([[1.0, 0.0], [-1.0, 0.0]], dtype=np.float32)
        },
        changed_expression_ids={3},
    )

    assert [item["cluster_id"] for item in raw_expressions] == [0, 1, 1]
    assert cluster_centers[marker].shape == (2, 2)


def test_full_recluster_is_due_at_five_percent_changes() -> None:
    """累计变化达到总表达数的 5% 时才触发全量重聚类。"""

    below_threshold = int(10_000 * FULL_RECLUSTER_CHANGE_RATIO) - 1
    at_threshold = int(10_000 * FULL_RECLUSTER_CHANGE_RATIO)

    assert (
        ExpressionVectorIndex._resolve_recluster_reason(
            force_recluster=False,
            cluster_state=CLUSTER_STATE_STABLE,
            can_update_incrementally=True,
            changes_since_recluster=below_threshold,
            total_count=10_000,
        )
        == ""
    )
    assert (
        ExpressionVectorIndex._resolve_recluster_reason(
            force_recluster=False,
            cluster_state=CLUSTER_STATE_STABLE,
            can_update_incrementally=True,
            changes_since_recluster=at_threshold,
            total_count=10_000,
        )
        == "change_ratio"
    )
    assert (
        ExpressionVectorIndex._resolve_recluster_reason(
            force_recluster=False,
            cluster_state=CLUSTER_STATE_BOOTSTRAPPING,
            can_update_incrementally=True,
            changes_since_recluster=at_threshold,
            total_count=10_000,
        )
        == ""
    )


def test_cluster_state_only_migrates_missing_legacy_state() -> None:
    """旧索引缺少状态时进入初建，已存在但非法的状态必须直接报错。"""

    assert (
        ExpressionVectorIndex._resolve_cluster_state({}, profile_marker="profile-marker")
        == CLUSTER_STATE_BOOTSTRAPPING
    )
    assert (
        ExpressionVectorIndex._resolve_cluster_state(
            {
                "cluster_maintenance": {
                    "state": CLUSTER_STATE_STABLE,
                    "profile_marker": "old-profile",
                }
            },
            profile_marker="new-profile",
        )
        == CLUSTER_STATE_BOOTSTRAPPING
    )
    with pytest.raises(ValueError, match="聚类状态非法"):
        ExpressionVectorIndex._resolve_cluster_state(
            {
                "cluster_maintenance": {
                    "state": "BROKEN",
                    "profile_marker": "profile-marker",
                }
            },
            profile_marker="profile-marker",
        )


def test_fresh_recluster_does_not_reuse_temporary_cluster_centers(monkeypatch) -> None:
    """历史回填最终正式聚类应重新初始化，不继承临时聚类中心的顺序偏差。"""

    marker = "profile-marker"
    raw_expressions = [
        {
            "id": 1,
            "embedding_profile_marker": marker,
            "embedding_model": "test-model",
            "embedding_dimension": 2,
        },
        {
            "id": 2,
            "embedding_profile_marker": marker,
            "embedding_model": "test-model",
            "embedding_dimension": 2,
        },
    ]
    vector_index = ExpressionVectorIndex()
    captured_initial_centers = []

    def fake_run_kmeans(_vectors, *, cluster_count, initial_centers=None, **_kwargs):
        captured_initial_centers.append(initial_centers)
        return np.arange(cluster_count, dtype=np.int32)

    monkeypatch.setattr(vector_index, "_run_kmeans", fake_run_kmeans)
    vector_index._rebuild_profile_arrays(
        raw_expressions=raw_expressions,
        vector_by_expression_id={
            1: np.array([1.0, 0.0], dtype=np.float32),
            2: np.array([0.0, 1.0], dtype=np.float32),
        },
        previous_profile_cluster_centers={
            marker: np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        },
        reuse_previous_centers=False,
    )

    assert captured_initial_centers == [None]


@pytest.mark.asyncio
async def test_bootstrap_finalization_is_fresh_and_only_runs_once(
    tmp_path,
    monkeypatch,
) -> None:
    """追平复查应从零正式聚类并切换稳定态，后续空扫描不再重建。"""

    marker = "profile-marker"
    profile = ExpressionEmbeddingProfile(
        marker=marker,
        model_name="test-model",
        model_identifier="test-identifier",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    index_path = tmp_path / "expression_vector_index.json"
    vectors_path = tmp_path / "expression_vector_index.npz"
    raw_expressions = []
    fingerprints = {}
    for expression_id in (1, 2):
        situation = f"情景 {expression_id}"
        style = f"表达 {expression_id}"
        fingerprint = expression_fingerprint(expression_id, situation, style)
        fingerprints[expression_id] = fingerprint
        raw_expressions.append(
            {
                "id": expression_id,
                "situation": situation,
                "style": style,
                "count": 1,
                "session_id": "session",
                "checked": False,
                "modified_by": "",
                "fingerprint": fingerprint,
                "embedding_profile_marker": marker,
                "embedding_model": profile.model_name,
                "embedding_dimension": 2,
                "vector_index": expression_id - 1,
                "cluster_id": expression_id - 1,
            }
        )
    payload = {
        "version": 2,
        "embedding_model": profile.model_name,
        "embedding_profile_marker": marker,
        "embedding_dimension": 2,
        "embedding_profiles": [
            {
                "marker": marker,
                "embedding_model": profile.model_name,
                "embedding_dimension": 2,
                "expression_count": 2,
                "cluster_count": 2,
                "vectors_key": "vectors_0",
                "cluster_centers_key": "cluster_centers_0",
            }
        ],
        "sample_count": 2,
        "expressions": raw_expressions,
        "cluster_maintenance": {
            "state": CLUSTER_STATE_BOOTSTRAPPING,
            "profile_marker": marker,
            "changes_since_recluster": 2,
            "changed_expression_ids": [1, 2],
        },
    }
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    ExpressionVectorIndex._write_index_files(
        index_path=index_path,
        vectors_path=vectors_path,
        payload=payload,
        profile_vectors={marker: vectors},
        profile_cluster_centers={marker: vectors.copy()},
    )
    vector_index = ExpressionVectorIndex()
    captured_initial_centers = []

    def fake_load_history_backfill_items(**_kwargs):
        return ExpressionHistoryBackfillSelection(
            items=[],
            deferred_count=0,
            isolated_count=0,
        )

    def fake_run_kmeans(_vectors, *, cluster_count, initial_centers=None, **_kwargs):
        captured_initial_centers.append(initial_centers)
        return np.arange(cluster_count, dtype=np.int32)

    monkeypatch.setattr(vector_index, "_load_history_backfill_items", fake_load_history_backfill_items)
    monkeypatch.setattr(vector_index, "_load_current_expression_fingerprints", lambda: fingerprints)
    monkeypatch.setattr(vector_index, "_run_kmeans", fake_run_kmeans)

    assert await vector_index._finalize_bootstrap_if_ready(
        index_path=index_path,
        profile=profile,
    )
    stored_payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert captured_initial_centers == [None]
    assert stored_payload["cluster_maintenance"]["state"] == CLUSTER_STATE_STABLE
    assert stored_payload["cluster_maintenance"]["changes_since_recluster"] == 0

    monkeypatch.setattr(
        vector_index,
        "_rebuild_profile_arrays",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("稳定状态不应再全量聚类")),
    )
    assert await vector_index._finalize_bootstrap_if_ready(
        index_path=index_path,
        profile=profile,
    )


@pytest.mark.asyncio
async def test_upsert_uses_incremental_assignment_before_five_percent(
    tmp_path,
    monkeypatch,
) -> None:
    """在线写入未达到 5% 阈值时，不应调用全量 k-means。"""

    from src.services.embedding_service import EmbeddingServiceClient

    marker = "profile-marker"
    profile = ExpressionEmbeddingProfile(
        marker=marker,
        model_name="test-model",
        model_identifier="test-identifier",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    index_path = tmp_path / "expression_vector_index.json"
    vectors_path = tmp_path / "expression_vector_index.npz"
    raw_expressions = []
    vectors = []
    fingerprints = {}
    for expression_id in range(1, 101):
        situation = f"情景 {expression_id}"
        style = f"表达 {expression_id}"
        cluster_id = 0 if expression_id <= 50 else 1
        raw_expressions.append(
            {
                "id": expression_id,
                "situation": situation,
                "style": style,
                "count": 1,
                "session_id": "session",
                "checked": False,
                "modified_by": "",
                "fingerprint": expression_fingerprint(expression_id, situation, style),
                "embedding_profile_marker": marker,
                "embedding_model": profile.model_name,
                "embedding_dimension": 2,
                "vector_index": expression_id - 1,
                "cluster_id": cluster_id,
            }
        )
        vectors.append([1.0, 0.0] if cluster_id == 0 else [-1.0, 0.0])
        fingerprints[expression_id] = expression_fingerprint(expression_id, situation, style)
    payload = {
        "version": 2,
        "embedding_model": profile.model_name,
        "embedding_profile_marker": marker,
        "embedding_dimension": 2,
        "embedding_profiles": [
            {
                "marker": marker,
                "embedding_model": profile.model_name,
                "embedding_dimension": 2,
                "expression_count": 100,
                "cluster_count": 2,
                "vectors_key": "vectors_0",
                "cluster_centers_key": "cluster_centers_0",
            }
        ],
        "sample_count": 100,
        "expressions": raw_expressions,
        "cluster_maintenance": {
            "state": CLUSTER_STATE_STABLE,
            "profile_marker": marker,
            "changes_since_recluster": 0,
            "changed_expression_ids": [],
        },
    }
    ExpressionVectorIndex._write_index_files(
        index_path=index_path,
        vectors_path=vectors_path,
        payload=payload,
        profile_vectors={marker: np.asarray(vectors, dtype=np.float32)},
        profile_cluster_centers={
            marker: np.array([[1.0, 0.0], [-1.0, 0.0]], dtype=np.float32)
        },
    )
    fingerprints[101] = expression_fingerprint(101, "新情景", "新表达")
    vector_index = ExpressionVectorIndex()

    async def fake_get_current_embedding_profile(**_kwargs):
        return profile

    async def fake_embed_texts(_self, _texts, **_kwargs):
        return [
            SimpleNamespace(
                embedding=[-0.9, 0.1],
                model_name=profile.model_name,
                model_identifier=profile.model_identifier,
                api_provider=profile.api_provider,
            )
        ]

    def fail_full_recluster(*_args, **_kwargs):
        raise AssertionError("未达到 5% 阈值时不应执行全量重聚类")

    monkeypatch.setattr(vector_index, "get_current_embedding_profile", fake_get_current_embedding_profile)
    monkeypatch.setattr(vector_index, "_load_current_expression_fingerprints", lambda: fingerprints)
    monkeypatch.setattr(EmbeddingServiceClient, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(vector_index, "_rebuild_profile_arrays", fail_full_recluster)

    result = await vector_index.upsert_expressions(
        index_path=str(index_path),
        expressions=[
            ExpressionVectorIndexUpsertItem(
                id=101,
                situation="新情景",
                style="新表达",
                count=1,
                session_id="session",
                checked=False,
                modified_by="",
            )
        ],
    )

    assert result is not None
    assert result.reclustered is False
    assert result.changes_since_recluster == 1
    stored_payload = json.loads(index_path.read_text(encoding="utf-8"))
    stored_expression = next(
        item for item in stored_payload["expressions"] if item["id"] == 101
    )
    assert stored_expression["cluster_id"] == 1
    assert stored_payload["cluster_maintenance"]["state"] == CLUSTER_STATE_STABLE
    assert stored_payload["cluster_maintenance"]["changes_since_recluster"] == 1
    assert stored_payload["cluster_maintenance"]["changed_expression_ids"] == [101]


@pytest.mark.asyncio
async def test_history_backfill_uses_uniform_upserts_then_finalizes_after_empty_scan(
    tmp_path,
    monkeypatch,
) -> None:
    """回填批次与新增共用普通 upsert，空扫描后才单独确认稳定化。"""

    from src.config.config import global_config

    vector_index = ExpressionVectorIndex()
    profile = SimpleNamespace(marker="profile-marker")
    pending_batches = [
        ExpressionHistoryBackfillSelection(
            items=[SimpleNamespace(id=index) for index in range(200)],
            deferred_count=0,
            isolated_count=0,
        ),
        ExpressionHistoryBackfillSelection(
            items=[SimpleNamespace(id=200)],
            deferred_count=0,
            isolated_count=0,
        ),
        ExpressionHistoryBackfillSelection(
            items=[],
            deferred_count=0,
            isolated_count=0,
        ),
    ]
    update_calls = []
    finalize_calls = []

    async def fake_get_current_embedding_profile(**_kwargs):
        return profile

    def fake_load_history_backfill_items(**_kwargs):
        return pending_batches.pop(0)

    async def fake_upsert_expressions(**kwargs):
        update_calls.append(kwargs)
        return SimpleNamespace(
            batch_count=len(kwargs["expressions"]),
            failed_count=0,
            isolated_count=0,
            reclustered=False,
        )

    async def fake_finalize_bootstrap_if_ready(**kwargs):
        finalize_calls.append(kwargs)
        return True

    monkeypatch.setattr(
        global_config.expression,
        "expression_selection_mode",
        "vector",
    )
    monkeypatch.setattr(vector_index, "get_current_embedding_profile", fake_get_current_embedding_profile)
    monkeypatch.setattr(vector_index, "_load_history_backfill_items", fake_load_history_backfill_items)
    monkeypatch.setattr(vector_index, "upsert_expressions", fake_upsert_expressions)
    monkeypatch.setattr(vector_index, "_finalize_bootstrap_if_ready", fake_finalize_bootstrap_if_ready)
    monkeypatch.setattr(vector_index, "_calculate_history_backfill_interval", lambda **_kwargs: 0.0)

    await vector_index._run_history_backfill_loop(
        index_path=str(tmp_path / "expression_vector_index.json"),
    )

    assert len(update_calls) == 2
    assert len(update_calls[0]["expressions"]) == 200
    assert set(update_calls[0]) == {"index_path", "expressions"}
    assert len(update_calls[1]["expressions"]) == 1
    assert set(update_calls[1]) == {"index_path", "expressions"}
    assert len(finalize_calls) == 1


@pytest.mark.asyncio
async def test_history_backfill_continues_when_locked_recheck_finds_new_item(
    tmp_path,
    monkeypatch,
) -> None:
    """外层扫描认为追平后，锁内复查发现并发新增时应继续统一 upsert。"""

    from src.config.config import global_config

    vector_index = ExpressionVectorIndex()
    profile = SimpleNamespace(marker="profile-marker")
    selections = [
        ExpressionHistoryBackfillSelection(items=[], deferred_count=0, isolated_count=0),
        ExpressionHistoryBackfillSelection(
            items=[SimpleNamespace(id=101)],
            deferred_count=0,
            isolated_count=0,
        ),
        ExpressionHistoryBackfillSelection(items=[], deferred_count=0, isolated_count=0),
    ]
    finalize_results = iter((False, True))
    update_calls = []

    async def fake_get_current_embedding_profile(**_kwargs):
        return profile

    def fake_load_history_backfill_items(**_kwargs):
        return selections.pop(0)

    async def fake_upsert_expressions(**kwargs):
        update_calls.append(kwargs)
        return SimpleNamespace(
            batch_count=1,
            failed_count=0,
            isolated_count=0,
            reclustered=False,
        )

    async def fake_finalize_bootstrap_if_ready(**_kwargs):
        return next(finalize_results)

    monkeypatch.setattr(global_config.expression, "expression_selection_mode", "vector")
    monkeypatch.setattr(vector_index, "get_current_embedding_profile", fake_get_current_embedding_profile)
    monkeypatch.setattr(vector_index, "_load_history_backfill_items", fake_load_history_backfill_items)
    monkeypatch.setattr(vector_index, "upsert_expressions", fake_upsert_expressions)
    monkeypatch.setattr(vector_index, "_finalize_bootstrap_if_ready", fake_finalize_bootstrap_if_ready)
    monkeypatch.setattr(vector_index, "_calculate_history_backfill_interval", lambda **_kwargs: 0.0)

    await vector_index._run_history_backfill_loop(
        index_path=str(tmp_path / "expression_vector_index.json"),
    )

    assert len(update_calls) == 1
    assert [item.id for item in update_calls[0]["expressions"]] == [101]


def test_corrupt_generated_index_is_treated_as_missing(tmp_path) -> None:
    """损坏的生成索引应进入明确重建路径，不能反复触发 JSONDecodeError。"""

    index_path = tmp_path / "expression_vector_index.json"
    index_path.write_bytes(b"\x00" * 1024)
    vector_index = ExpressionVectorIndex()

    assert vector_index._load_persisted_embedding_profile(index_path) is None
    assert vector_index._load_raw_index_expressions(index_path) == {}
    assert vector_index._load_snapshot(index_path) is None


def test_atomic_write_text_replaces_content_without_leaving_temporary_file(tmp_path) -> None:
    """JSON 索引写入应使用唯一临时文件，并且只暴露完整的新内容。"""

    index_path = tmp_path / "expression_vector_index.json"
    index_path.write_text("old", encoding="utf-8")

    _atomic_write_text(index_path, '{"version": 2}')

    assert json.loads(index_path.read_text(encoding="utf-8")) == {"version": 2}
    assert list(tmp_path.glob(f".{index_path.name}.*.tmp")) == []


def test_write_index_files_uses_complete_json_and_npz_replacements(tmp_path) -> None:
    """索引元数据和向量文件写完后均应可立即完整读取。"""

    index_path = tmp_path / "expression_vector_index.json"
    vectors_path = tmp_path / "expression_vector_index.npz"
    marker = "profile-marker"
    payload = {
        "version": 2,
        "embedding_profiles": [
            {
                "marker": marker,
                "vectors_key": "vectors_0",
                "cluster_centers_key": "cluster_centers_0",
            }
        ],
    }
    vectors = np.array([[1.0, 0.0]], dtype=np.float32)
    cluster_centers = np.array([[1.0, 0.0]], dtype=np.float32)

    ExpressionVectorIndex._write_index_files(
        index_path=index_path,
        vectors_path=vectors_path,
        payload=payload,
        profile_vectors={marker: vectors},
        profile_cluster_centers={marker: cluster_centers},
    )

    stored_payload = json.loads(index_path.read_text(encoding="utf-8"))
    committed_vectors_path = tmp_path / stored_payload["vectors_file"]

    assert stored_payload["version"] == 2
    assert committed_vectors_path != vectors_path
    assert not vectors_path.exists()
    with np.load(committed_vectors_path) as stored_arrays:
        assert np.array_equal(stored_arrays["vectors_0"], vectors)
        assert np.array_equal(stored_arrays["cluster_centers_0"], cluster_centers)
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_index_files_keeps_previous_generation_when_manifest_commit_fails(
    tmp_path,
    monkeypatch,
) -> None:
    """JSON 清单提交失败时，旧向量代仍需保持完整可用。"""

    index_path = tmp_path / "expression_vector_index.json"
    vectors_path = tmp_path / "expression_vector_index.npz"
    index_path.write_text('{"version": 1}', encoding="utf-8")
    vectors_path.write_bytes(b"previous-vectors")
    marker = "profile-marker"
    payload = {
        "version": 2,
        "embedding_profiles": [
            {
                "marker": marker,
                "vectors_key": "vectors_0",
                "cluster_centers_key": "cluster_centers_0",
            }
        ],
    }

    def fail_manifest_commit(_path, _content):
        raise OSError("manifest commit failed")

    monkeypatch.setattr(vector_index_module, "_atomic_write_text", fail_manifest_commit)

    with pytest.raises(OSError, match="manifest commit failed"):
        ExpressionVectorIndex._write_index_files(
            index_path=index_path,
            vectors_path=vectors_path,
            payload=payload,
            profile_vectors={marker: np.array([[1.0, 0.0]], dtype=np.float32)},
            profile_cluster_centers={
                marker: np.array([[1.0, 0.0]], dtype=np.float32)
            },
        )

    assert index_path.read_text(encoding="utf-8") == '{"version": 1}'
    assert vectors_path.read_bytes() == b"previous-vectors"
    assert list(tmp_path.glob("expression_vector_index.vectors-*.npz")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


def test_history_backfill_failure_cooldown_prevents_immediate_restart(tmp_path) -> None:
    """补建任务刚失败时，不应被下一条聊天消息立即再次启动。"""

    vector_index = ExpressionVectorIndex()
    vector_index._history_backfill_last_failure_at = time.monotonic()

    vector_index.ensure_history_backfill_task(
        index_path=str(tmp_path / "expression_vector_index.json"),
    )

    assert vector_index._history_backfill_task is None


@pytest.mark.asyncio
async def test_embed_expression_items_keeps_successes_when_one_item_fails(monkeypatch) -> None:
    """单条嵌入失败时，同批成功项应继续返回且不触发整批探针。"""

    from src.services.embedding_service import EmbeddingServiceClient

    profile = ExpressionEmbeddingProfile(
        marker="profile-marker",
        model_name="test-model",
        model_identifier="test-identifier",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    items = [
        ExpressionVectorIndexUpsertItem(
            id=1,
            situation="成功情景",
            style="成功表达",
            count=1,
            session_id="session",
            checked=False,
            modified_by="",
        ),
        ExpressionVectorIndexUpsertItem(
            id=2,
            situation="失败情景",
            style="失败表达",
            count=1,
            session_id="session",
            checked=False,
            modified_by="",
        ),
    ]

    async def fake_embed_texts(_self, _texts, **_kwargs):
        return [
            SimpleNamespace(
                embedding=[1.0, 0.0],
                model_name=profile.model_name,
                model_identifier=profile.model_identifier,
                api_provider=profile.api_provider,
            ),
            ValueError("局部坏数据"),
        ]

    async def fail_probe(*_args, **_kwargs):
        raise AssertionError("已有成功项时不应执行整批失败探针")

    monkeypatch.setattr(EmbeddingServiceClient, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(EmbeddingServiceClient, "embed_text", fail_probe)

    successful_items, vectors, failures = await ExpressionVectorIndex()._embed_expression_items(
        items=items,
        profile=profile,
        session_id="session",
    )

    assert [item.id for item in successful_items] == [1]
    assert vectors.shape == (1, 2)
    assert [item.id for item, _ in failures] == [2]


@pytest.mark.asyncio
async def test_embed_expression_items_probes_before_treating_all_failures_as_local(monkeypatch) -> None:
    """整批都失败时，固定探针成功才能将其记为局部坏数据。"""

    from src.services.embedding_service import EmbeddingServiceClient

    profile = ExpressionEmbeddingProfile(
        marker="profile-marker",
        model_name="test-model",
        model_identifier="test-identifier",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    item = ExpressionVectorIndexUpsertItem(
        id=1,
        situation="坏情景",
        style="坏表达",
        count=1,
        session_id="session",
        checked=False,
        modified_by="",
    )

    async def fake_embed_texts(_self, _texts, **_kwargs):
        return [ValueError("局部坏数据")]

    async def fake_probe(_self, _text, **_kwargs):
        return SimpleNamespace(
            embedding=[1.0, 0.0],
            model_name=profile.model_name,
            model_identifier=profile.model_identifier,
            api_provider=profile.api_provider,
        )

    monkeypatch.setattr(EmbeddingServiceClient, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(EmbeddingServiceClient, "embed_text", fake_probe)

    successful_items, vectors, failures = await ExpressionVectorIndex()._embed_expression_items(
        items=[item],
        profile=profile,
        session_id="session",
    )

    assert successful_items == []
    assert vectors.shape == (0, 2)
    assert [failed_item.id for failed_item, _ in failures] == [1]


@pytest.mark.asyncio
async def test_embed_expression_items_does_not_isolate_provider_wide_failure(monkeypatch) -> None:
    """表达与固定探针同时失败时，应暴露 Provider 故障而不记为坏数据。"""

    from src.services.embedding_service import EmbeddingServiceClient

    profile = ExpressionEmbeddingProfile(
        marker="profile-marker",
        model_name="test-model",
        model_identifier="test-identifier",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    item = ExpressionVectorIndexUpsertItem(
        id=1,
        situation="情景",
        style="表达",
        count=1,
        session_id="session",
        checked=False,
        modified_by="",
    )

    async def fake_embed_texts(_self, _texts, **_kwargs):
        return [RuntimeError("Provider 暂时不可用")]

    async def fail_probe(_self, _text, **_kwargs):
        raise RuntimeError("Provider 暂时不可用")

    monkeypatch.setattr(EmbeddingServiceClient, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(EmbeddingServiceClient, "embed_text", fail_probe)

    with pytest.raises(RuntimeError, match="Provider 暂时不可用"):
        await ExpressionVectorIndex()._embed_expression_items(
            items=[item],
            profile=profile,
            session_id="session",
        )


@pytest.mark.asyncio
async def test_upsert_discards_embedding_that_became_stale_while_waiting_for_lock(
    tmp_path,
    monkeypatch,
) -> None:
    """表达在 embedding 完成后又被修改时，旧结果不应覆盖并发新增的最新内容。"""

    from src.services.embedding_service import EmbeddingServiceClient

    profile = ExpressionEmbeddingProfile(
        marker="profile-marker",
        model_name="test-model",
        model_identifier="test-identifier",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    stale_item = ExpressionVectorIndexUpsertItem(
        id=1,
        situation="旧情景",
        style="旧表达",
        count=1,
        session_id="session",
        checked=False,
        modified_by="",
    )
    vector_index = ExpressionVectorIndex()
    index_path = tmp_path / "expression_vector_index.json"

    async def fake_get_current_embedding_profile(**_kwargs):
        return profile

    async def fake_embed_texts(_self, _texts, **_kwargs):
        return [
            SimpleNamespace(
                embedding=[1.0, 0.0],
                model_name=profile.model_name,
                model_identifier=profile.model_identifier,
                api_provider=profile.api_provider,
            )
        ]

    monkeypatch.setattr(vector_index, "get_current_embedding_profile", fake_get_current_embedding_profile)
    monkeypatch.setattr(
        vector_index,
        "_load_current_expression_fingerprints",
        lambda: {1: expression_fingerprint(1, "新情景", "新表达")},
    )
    monkeypatch.setattr(EmbeddingServiceClient, "embed_texts", fake_embed_texts)

    result = await vector_index.upsert_expressions(
        index_path=str(index_path),
        expressions=[stale_item],
    )

    assert result is not None
    assert result.requested_count == 1
    assert result.batch_count == 0
    assert not index_path.exists()
    assert not (tmp_path / "expression_vector_index.embedding-failures.json").exists()


def test_embedding_failure_records_isolate_then_clear_after_success() -> None:
    """反复失败项应进入长冷却，但一旦成功就立即清除隔离记录。"""

    profile = ExpressionEmbeddingProfile(
        marker="profile-marker",
        model_name="test-model",
        model_identifier="test-identifier",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    item = ExpressionVectorIndexUpsertItem(
        id=1,
        situation="坏情景",
        style="坏表达",
        count=1,
        session_id="session",
        checked=False,
        modified_by="",
    )
    fingerprint = expression_fingerprint(item.id, item.situation, item.style)
    payload = {}

    for _ in range(EMBEDDING_ITEM_FAILURE_ISOLATION_ATTEMPTS):
        records, isolated_count = ExpressionVectorIndex._merge_embedding_failure_records(
            payload=payload,
            profile=profile,
            current_fingerprints={item.id: fingerprint},
            successful_expression_ids=set(),
            failures=[(item, ValueError("无法计算"))],
        )

    assert records[0]["attempts"] == EMBEDDING_ITEM_FAILURE_ISOLATION_ATTEMPTS
    assert records[0]["isolated"] is True
    assert isolated_count == 1

    records, isolated_count = ExpressionVectorIndex._merge_embedding_failure_records(
        payload=payload,
        profile=profile,
        current_fingerprints={item.id: fingerprint},
        successful_expression_ids={item.id},
        failures=[],
    )

    assert records == []
    assert isolated_count == 0


@pytest.mark.asyncio
async def test_first_all_failed_batch_persists_standalone_failure_records(
    tmp_path,
    monkeypatch,
) -> None:
    """主索引尚未建立时整批局部失败，也应持久化冷却状态以继续处理后续正常项。"""

    from src.services.embedding_service import EmbeddingServiceClient

    profile = ExpressionEmbeddingProfile(
        marker="profile-marker",
        model_name="test-model",
        model_identifier="test-identifier",
        api_provider="test-provider",
        dimension=2,
        revision=1,
        probe_embeddings=((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    )
    item = ExpressionVectorIndexUpsertItem(
        id=1,
        situation="坏情景",
        style="坏表达",
        count=1,
        session_id="session",
        checked=False,
        modified_by="",
    )
    index_path = tmp_path / "expression_vector_index.json"
    vector_index = ExpressionVectorIndex()

    async def fake_get_current_embedding_profile(**_kwargs):
        return profile

    async def fake_embed_texts(_self, _texts, **_kwargs):
        return [ValueError("局部坏数据")]

    async def fake_probe(_self, _text, **_kwargs):
        return SimpleNamespace(
            embedding=[1.0, 0.0],
            model_name=profile.model_name,
            model_identifier=profile.model_identifier,
            api_provider=profile.api_provider,
        )

    monkeypatch.setattr(vector_index, "get_current_embedding_profile", fake_get_current_embedding_profile)
    monkeypatch.setattr(
        vector_index,
        "_load_current_expression_fingerprints",
        lambda: {item.id: expression_fingerprint(item.id, item.situation, item.style)},
    )
    monkeypatch.setattr(EmbeddingServiceClient, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(EmbeddingServiceClient, "embed_text", fake_probe)

    result = await vector_index.upsert_expressions(
        index_path=str(index_path),
        expressions=[item],
    )

    failures_path = tmp_path / "expression_vector_index.embedding-failures.json"
    failure_payload = json.loads(failures_path.read_text(encoding="utf-8"))
    assert result is not None
    assert result.batch_count == 0
    assert result.failed_count == 1
    assert not index_path.exists()
    assert failure_payload["embedding_failures"][0]["expression_id"] == item.id

    async def fake_successful_embed_texts(_self, _texts, **_kwargs):
        return [
            SimpleNamespace(
                embedding=[1.0, 0.0],
                model_name=profile.model_name,
                model_identifier=profile.model_identifier,
                api_provider=profile.api_provider,
            )
        ]

    monkeypatch.setattr(EmbeddingServiceClient, "embed_texts", fake_successful_embed_texts)
    recovered_result = await vector_index.upsert_expressions(
        index_path=str(index_path),
        expressions=[item],
    )

    recovered_payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert recovered_result is not None
    assert recovered_result.batch_count == 1
    assert recovered_result.failed_count == 0
    assert recovered_payload["embedding_failures"] == []
    assert recovered_payload["cluster_maintenance"]["state"] == CLUSTER_STATE_BOOTSTRAPPING
    assert not failures_path.exists()
