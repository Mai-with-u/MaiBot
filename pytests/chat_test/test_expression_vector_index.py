import numpy as np
import pytest

from src.chat.replyer.expression_vector_index import ExpressionVectorIndex


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
