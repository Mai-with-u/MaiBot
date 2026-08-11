from datetime import datetime

import pytest

from src.common.database.database_model import MaisakaReplyEffect
from src.webui.routers.reply_effects import (
    ReplyEffectComparisonGroup,
    _aggregate,
    _aggregate_version_group,
    _row_summary,
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


def test_aggregate_excludes_no_information_records_from_confidence() -> None:
    no_information = _build_summary_row("no-information", 4)
    no_information.response_score = 0.0
    no_information.reception_score = None
    no_information.conversation_score = 0.0
    no_information.confidence = 0.0
    with_evidence = _build_summary_row("with-evidence", 4)
    with_evidence.response_score = 50.0
    with_evidence.reception_score = None
    with_evidence.conversation_score = 20.0
    with_evidence.confidence = 0.8

    aggregate = _aggregate([no_information, with_evidence])

    assert aggregate["confidence"] == 0.8
    assert aggregate["confidence_count"] == 1


def test_incomplete_historical_record_has_no_scores() -> None:
    row = _build_summary_row("incomplete", 3)
    row.response_score = 60.0
    row.reception_score = 50.0
    row.conversation_score = 40.0
    row.confidence = 0.21
    row.record_json = (
        '{"status":"finalized","finalize_reason":"runtime_stop",'
        '"reply":{"reply_text":"观察被中断"},'
        '"scores":{"response_score":60,"reception_score":50,'
        '"conversation_score":40,"confidence":0.21}}'
    )

    summary = _row_summary(row)

    assert summary["status"] == "incomplete"
    assert summary["response_score"] is None
    assert summary["reception_score"] is None
    assert summary["conversation_score"] is None
    assert summary["confidence"] is None


def test_version_aggregate_exposes_evaluation_version() -> None:
    aggregate = _aggregate_version_group(
        [_build_summary_row("effect-v5", 5)],
        model_name="model-a",
        prompt_fingerprint="prompt-a",
        evaluation_version=5,
        collapse_models=False,
        collapse_versions=False,
    )

    assert aggregate["evaluation_version"] == 5
    assert aggregate["evaluation_versions"] == [5]
    assert aggregate["name"].endswith("评估标准 v5")


def test_comparison_selection_does_not_mix_evaluation_versions() -> None:
    rows = [_build_summary_row("effect-v4", 4), _build_summary_row("effect-v5", 5)]
    group = ReplyEffectComparisonGroup(
        name="model-a · prompt-a · 评估标准 v5",
        model_names=["model-a"],
        prompt_fingerprints=["prompt-a"],
        evaluation_versions=[5],
    )

    selected = _select_comparison_rows(rows, group)

    assert [row.effect_id for row in selected] == ["effect-v5"]
