import pytest

from src.webui.routers.reply_effects import _welch_comparison


def test_welch_comparison_detects_clear_mean_difference() -> None:
    result = _welch_comparison(
        [9.0, 10.0, 10.0, 11.0, 10.0],
        [19.0, 20.0, 20.0, 21.0, 20.0],
        alpha=0.05,
    )

    assert result["sufficient"] is True
    assert result["significant"] is True
    assert result["p_value"] < 0.05
    assert result["mean_difference"] == pytest.approx(-10.0)
    assert result["confidence_interval"][1] < 0
    assert result["hedges_g"] < 0


def test_welch_comparison_handles_identical_constant_samples() -> None:
    result = _welch_comparison([10.0, 10.0, 10.0], [10.0, 10.0, 10.0], alpha=0.05)

    assert result["sufficient"] is True
    assert result["significant"] is False
    assert result["p_value"] == 1.0
    assert result["confidence_interval"] == [0.0, 0.0]
    assert result["hedges_g"] == 0.0


def test_welch_comparison_requires_two_samples_in_each_group() -> None:
    result = _welch_comparison([10.0], [20.0, 21.0], alpha=0.05)

    assert result["sufficient"] is False
    assert result["p_value"] is None
    assert result["significant"] is False
    assert result["reason"] == "两组都至少需要 2 个有效样本"
