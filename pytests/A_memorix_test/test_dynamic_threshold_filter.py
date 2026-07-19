from typing import List

from pytest import approx, mark

from src.A_memorix.core.retrieval.dual_path import RetrievalResult
from src.A_memorix.core.retrieval.threshold import DynamicThresholdFilter, ThresholdConfig, ThresholdMethod
from src.config.official_configs import AMemorixConfig


def _build_results(scores: List[float]) -> List[RetrievalResult]:
    return [
        RetrievalResult(
            hash_value=str(index),
            content=str(score),
            score=score,
            result_type="paragraph",
            source="threshold_test",
            metadata={},
        )
        for index, score in enumerate(scores)
    ]


def _gap_filter(*, min_results: int = 0) -> DynamicThresholdFilter:
    return DynamicThresholdFilter(
        ThresholdConfig(
            method=ThresholdMethod.GAP_DETECTION,
            min_threshold=0.0,
            max_threshold=1.0,
            min_results=min_results,
        )
    )


def test_gap_detection_uses_largest_drop_midpoint() -> None:
    threshold_filter = _gap_filter()

    filtered, threshold = threshold_filter.filter(
        _build_results([0.90, 0.88, 0.40, 0.39]),
        return_threshold=True,
    )

    assert threshold == approx(0.64)
    assert [item.score for item in filtered] == [0.90, 0.88]


def test_gap_detection_finds_late_largest_drop() -> None:
    threshold_filter = _gap_filter()

    filtered, threshold = threshold_filter.filter(
        _build_results([0.95, 0.90, 0.89, 0.30, 0.29]),
        return_threshold=True,
    )

    assert threshold == approx(0.595)
    assert [item.score for item in filtered] == [0.95, 0.90, 0.89]


def test_adaptive_threshold_keeps_configured_minimum_after_gap_fix() -> None:
    threshold_filter = DynamicThresholdFilter(ThresholdConfig(min_results=3))

    filtered, threshold = threshold_filter.filter(
        _build_results([0.90, 0.88, 0.40, 0.39]),
        return_threshold=True,
    )

    assert threshold == approx(0.40)
    assert [item.score for item in filtered] == [0.90, 0.88, 0.40]


def test_default_adaptive_threshold_keeps_four_results() -> None:
    threshold_filter = DynamicThresholdFilter()

    filtered, threshold = threshold_filter.filter(
        _build_results([0.90, 0.88, 0.40, 0.39]),
        return_threshold=True,
    )

    assert threshold == approx(0.39)
    assert [item.score for item in filtered] == [0.90, 0.88, 0.40, 0.39]


def test_runtime_and_host_threshold_defaults_are_aligned() -> None:
    runtime_config = ThresholdConfig()
    host_config = AMemorixConfig().threshold

    assert runtime_config.min_threshold == host_config.min_threshold == approx(0.29)
    assert runtime_config.min_results == host_config.min_results == 4
    assert not hasattr(runtime_config, "enable_auto_adjust")
    assert not hasattr(host_config, "enable_auto_adjust")


def test_adaptive_threshold_caps_gap_candidate_at_lower_middle_percentile() -> None:
    threshold_filter = DynamicThresholdFilter(ThresholdConfig(min_results=0))

    filtered, threshold = threshold_filter.filter(
        _build_results([0.90, 0.88, 0.40, 0.39, 0.38, 0.37, 0.36, 0.35, 0.34, 0.33]),
        return_threshold=True,
    )

    assert threshold == approx(0.366)
    assert [item.score for item in filtered] == [0.90, 0.88, 0.40, 0.39, 0.38, 0.37]


@mark.parametrize(
    ("scores", "expected_threshold"),
    [
        ([0.60, 0.60, 0.60, 0.60], 0.60),
        ([0.72], 0.72),
    ],
)
def test_gap_detection_preserves_flat_and_single_score_inputs(
    scores: List[float],
    expected_threshold: float,
) -> None:
    threshold_filter = _gap_filter()

    filtered, threshold = threshold_filter.filter(
        _build_results(scores),
        return_threshold=True,
    )

    assert threshold == approx(expected_threshold)
    assert [item.score for item in filtered] == scores


def test_threshold_is_independent_of_prior_queries() -> None:
    target = _build_results([0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45, 0.40, 0.35])
    high_scores = _build_results([0.99, 0.97, 0.95, 0.93, 0.91, 0.89, 0.87, 0.85, 0.83, 0.81])

    cold_filter = DynamicThresholdFilter()
    cold_results, cold_threshold = cold_filter.filter(target, return_threshold=True)

    warmed_filter = DynamicThresholdFilter()
    for _ in range(10):
        warmed_filter.filter(high_scores)
    warmed_results, warmed_threshold = warmed_filter.filter(target, return_threshold=True)

    assert warmed_threshold == approx(cold_threshold)
    assert [item.hash_value for item in warmed_results] == [item.hash_value for item in cold_results]


def test_threshold_statistics_use_constant_space_and_record_effective_threshold() -> None:
    threshold_filter = DynamicThresholdFilter()

    _filtered, threshold = threshold_filter.filter(
        _build_results([0.90, 0.88, 0.40, 0.39]),
        return_threshold=True,
    )
    statistics = threshold_filter.get_statistics()["statistics"]

    assert not hasattr(threshold_filter, "_threshold_history")
    assert statistics["threshold_count"] == 1
    assert statistics["avg_threshold"] == approx(threshold)
    assert statistics["min_threshold_used"] == approx(threshold)
    assert statistics["max_threshold_used"] == approx(threshold)
