from datetime import datetime

import pytest

from src.common.database.database_model import MaisakaReplyEffect
from src.webui.routers.reply_effects import (
    ReplyEffectComparisonGroup,
    _aggregate_version_group,
    _select_comparison_rows,
    _welch_comparison,
)


def _build_summary_row(effect_id: str, evaluation_version: int) -> MaisakaReplyEffect:
    return MaisakaReplyEffect(
        effect_id=effect_id,
        session_id="session-1",
        status="finalized",
        created_at=datetime(2026, 8, 9),
        model_name="model-a",
        request_fingerprint="request-a",
        prompt_fingerprint="prompt-a",
        evaluation_version=evaluation_version,
    )


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


def test_version_aggregate_exposes_evaluation_version() -> None:
    aggregate = _aggregate_version_group(
        [_build_summary_row("effect-v3", 3)],
        model_name="model-a",
        prompt_fingerprint="prompt-a",
        evaluation_version=3,
        collapse_models=False,
        collapse_versions=False,
    )

    assert aggregate["evaluation_version"] == 3
    assert aggregate["evaluation_versions"] == [3]
    assert aggregate["name"].endswith("评估标准 v3")


def test_comparison_selection_does_not_mix_evaluation_versions() -> None:
    rows = [_build_summary_row("effect-v2", 2), _build_summary_row("effect-v3", 3)]
    group = ReplyEffectComparisonGroup(
        name="model-a · prompt-a · 评估标准 v3",
        model_names=["model-a"],
        prompt_fingerprints=["prompt-a"],
        evaluation_versions=[3],
    )

    selected = _select_comparison_rows(rows, group)

    assert [row.effect_id for row in selected] == ["effect-v3"]
