from types import SimpleNamespace

import pytest

from src.chat.replyer.expression_vector_index import (
    ExpressionVectorIndex,
    build_embedding_profile_from_probe_results,
)


def _result(
    vector: list[float],
    *,
    name: str = "embedding",
    identifier: str = "vendor/model",
    provider: str = "provider",
) -> SimpleNamespace:
    return SimpleNamespace(
        embedding=list(vector),
        model_name=name,
        model_identifier=identifier,
        api_provider=provider,
    )


def test_profile_marker_ignores_numeric_jitter() -> None:
    baseline = [_result([0.1, 0.2]), _result([0.2, 0.3]), _result([0.3, 0.4])]
    jittered = [
        _result([0.1014, 0.1991]),
        _result([0.1989, 0.3012]),
        _result([0.2992, 0.4011]),
    ]

    baseline_profile = build_embedding_profile_from_probe_results(baseline)
    jittered_profile = build_embedding_profile_from_probe_results(jittered)

    assert baseline_profile.marker == jittered_profile.marker
    assert baseline_profile.model_name == "embedding"
    assert baseline_profile.model_identifier == "vendor/model"
    assert baseline_profile.api_provider == "provider"
    assert baseline_profile.dimension == 2


@pytest.mark.parametrize(
    "changed_results",
    [
        [_result([0.1, 0.2], name="other-name")] * 3,
        [_result([0.1, 0.2], identifier="vendor/other")] * 3,
        [_result([0.1, 0.2], provider="other-provider")] * 3,
        [_result([0.1, 0.2, 0.3])] * 3,
    ],
)
def test_profile_marker_changes_with_backend_identity(changed_results: list[SimpleNamespace]) -> None:
    baseline = [_result([0.1, 0.2])] * 3

    baseline_profile = build_embedding_profile_from_probe_results(baseline)
    changed_profile = build_embedding_profile_from_probe_results(changed_results)

    assert baseline_profile.marker != changed_profile.marker


@pytest.mark.parametrize(
    ("results", "message"),
    [
        (
            [_result([0.1, 0.2]), _result([0.1, 0.2], name="other-name"), _result([0.1, 0.2])],
            "模型不一致",
        ),
        (
            [_result([0.1, 0.2]), _result([0.1, 0.2], identifier="vendor/other"), _result([0.1, 0.2])],
            "模型标识不一致",
        ),
        (
            [_result([0.1, 0.2]), _result([0.1, 0.2], provider="other-provider"), _result([0.1, 0.2])],
            "Provider 不一致",
        ),
        (
            [_result([0.1, 0.2]), _result([0.1, 0.2, 0.3]), _result([0.1, 0.2])],
            "维度不一致",
        ),
    ],
)
def test_profile_rejects_mixed_probe_results(results: list[SimpleNamespace], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_embedding_profile_from_probe_results(results)


@pytest.mark.parametrize(
    ("attribute", "message"),
    [
        ("model_name", "模型不一致"),
        ("model_identifier", "模型标识不一致"),
        ("api_provider", "Provider 不一致"),
    ],
)
def test_profile_rejects_blank_backend_identity(attribute: str, message: str) -> None:
    results = [_result([0.1, 0.2]) for _ in range(3)]
    setattr(results[1], attribute, "")

    with pytest.raises(ValueError, match=message):
        build_embedding_profile_from_probe_results(results)


@pytest.mark.parametrize(
    ("attribute", "changed_value"),
    [
        ("model_name", "other-name"),
        ("model_identifier", "vendor/other"),
        ("api_provider", "other-provider"),
        ("embedding", [0.1, 0.2, 0.3]),
    ],
)
def test_embedding_result_must_match_current_profile(attribute: str, changed_value: object) -> None:
    profile = build_embedding_profile_from_probe_results([_result([0.1, 0.2])] * 3)
    result = _result([0.1, 0.2])
    setattr(result, attribute, changed_value)

    with pytest.raises(ValueError, match="embedding profile 与当前标定不一致"):
        ExpressionVectorIndex._validate_embedding_result_profile(result, profile, usage="test")
