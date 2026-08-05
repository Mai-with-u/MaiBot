import json
import time

import numpy as np
import pytest

from src.chat.replyer.expression_vector_index import ExpressionVectorIndex, _atomic_write_text


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

    assert json.loads(index_path.read_text(encoding="utf-8"))["version"] == 2
    with np.load(vectors_path) as stored_arrays:
        assert np.array_equal(stored_arrays["vectors_0"], vectors)
        assert np.array_equal(stored_arrays["cluster_centers_0"], cluster_centers)
    assert list(tmp_path.glob(".*.tmp")) == []


def test_history_backfill_failure_cooldown_prevents_immediate_restart(tmp_path) -> None:
    """补建任务刚失败时，不应被下一条聊天消息立即再次启动。"""

    vector_index = ExpressionVectorIndex()
    vector_index._history_backfill_last_failure_at = time.monotonic()

    vector_index.ensure_history_backfill_task(
        index_path=str(tmp_path / "expression_vector_index.json"),
    )

    assert vector_index._history_backfill_task is None
