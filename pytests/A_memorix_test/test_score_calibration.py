from typing import Dict

import pytest

from src.A_memorix.core.retrieval.score_calibration import calibrate_score_map, fuse_score_maps, ordered_ids


def _score_maps(semantic_scale: float = 1.0, semantic_offset: float = 0.0) -> Dict[str, Dict[str, float]]:
    semantic = {"a": 0.82, "b": 0.74, "c": 0.65}
    return {
        "semantic": {
            key: semantic_scale * value + semantic_offset
            for key, value in semantic.items()
        },
        "sparse": {"a": 0.2, "b": 1.0, "c": 0.0},
        "graph": {"a": 0.8, "b": 0.1, "c": 0.5},
    }


def test_none_calibration_preserves_current_linear_fusion() -> None:
    final, calibrated = fuse_score_maps(
        _score_maps(),
        {"semantic": 0.65, "sparse": 0.20, "graph": 0.15},
        method="none",
    )

    assert calibrated["semantic"]["a"] == pytest.approx(0.82)
    assert final["a"] == pytest.approx(0.65 * 0.82 + 0.20 * 0.2 + 0.15 * 0.8)


@pytest.mark.parametrize(
    "method",
    [
        "minmax",
        "rank_percentile",
        "robust_sigmoid",
        "semantic_minmax",
        "semantic_graph_rrf",
        "semantic_rank_percentile",
        "semantic_robust_sigmoid",
        "semantic_rrf",
        "semantic_sparse_rrf",
        "semantic_softmax",
        "softmax",
        "weighted_rrf",
    ],
)
def test_calibrated_fusion_is_invariant_to_positive_affine_semantic_scale(method: str) -> None:
    weights = {"semantic": 0.65, "sparse": 0.20, "graph": 0.15}
    baseline, _ = fuse_score_maps(_score_maps(), weights, method=method)
    transformed, _ = fuse_score_maps(
        _score_maps(semantic_scale=0.35, semantic_offset=0.42),
        weights,
        method=method,
    )

    assert list(ordered_ids(baseline)) == list(ordered_ids(transformed))


def test_id_ties_receive_the_same_rank_calibration() -> None:
    calibrated = calibrate_score_map({"a": 0.8, "b": 0.8, "c": 0.2}, "rank_percentile")

    assert calibrated["a"] == calibrated["b"] == pytest.approx(1.0)
    assert calibrated["c"] == pytest.approx(0.0)


def test_invalid_calibration_method_is_rejected() -> None:
    with pytest.raises(ValueError, match="不支持的分数校准方法"):
        fuse_score_maps({}, {}, method="silent_fallback")
