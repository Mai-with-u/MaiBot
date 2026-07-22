from unittest.mock import MagicMock

import pytest

from src.A_memorix.core.retrieval import posterior_graph
from src.A_memorix.core.retrieval.dual_path import RetrievalResult
from src.A_memorix.core.retrieval.posterior_graph import PosteriorGraphConfig


def _result(
    hash_value: str,
    rank_score: float,
    competition_score: float,
    *,
    result_type: str = "paragraph",
) -> RetrievalResult:
    return RetrievalResult(
        hash_value=hash_value,
        content=hash_value,
        score=rank_score,
        result_type=result_type,
        source="test",
        metadata={"competition_score": competition_score},
    )


def _base_results() -> list[RetrievalResult]:
    return [
        _result(
            f"paragraph-{index}",
            1.0 - index * 0.1,
            0.80 - index * 0.01,
        )
        for index in range(10)
    ]


def test_posterior_graph_is_disabled_by_default() -> None:
    assert PosteriorGraphConfig().enabled is False


@pytest.fixture
def retriever() -> MagicMock:
    instance = MagicMock()
    instance.metadata_store.query.return_value = []
    instance._extract_entities.return_value = {}
    return instance


@pytest.fixture(autouse=True)
def deterministic_competition(monkeypatch: pytest.MonkeyPatch) -> None:
    def score(_retriever, candidate, **_kwargs):
        return float(candidate.metadata["competition_score"]), {}

    monkeypatch.setattr(posterior_graph, "_compute_competition_score", score)


def test_no_graph_candidates_preserve_original_top_k(retriever: MagicMock) -> None:
    base = _base_results()
    merged = posterior_graph._competition_merge(
        retriever,
        query="测试问题",
        base_results=base,
        graph_results=[],
        top_k=10,
        cfg=PosteriorGraphConfig(max_graph_slots=2),
    )
    assert [item.hash_value for item in merged] == [item.hash_value for item in base]


def test_rejected_graph_candidate_preserves_original_order(retriever: MagicMock) -> None:
    base = _base_results()
    graph = [_result("relation-low", 0.1, 0.01, result_type="relation")]
    merged = posterior_graph._competition_merge(
        retriever,
        query="测试问题",
        base_results=base,
        graph_results=graph,
        top_k=10,
        cfg=PosteriorGraphConfig(max_graph_slots=2),
    )
    assert [item.hash_value for item in merged] == [item.hash_value for item in base]


def test_graph_winner_replaces_tail_without_shrinking_top_k(retriever: MagicMock) -> None:
    base = _base_results()
    graph = [_result("relation-winner", 0.1, 0.99, result_type="relation")]
    merged = posterior_graph._competition_merge(
        retriever,
        query="测试问题",
        base_results=base,
        graph_results=graph,
        top_k=10,
        cfg=PosteriorGraphConfig(max_graph_slots=2),
    )
    hashes = [item.hash_value for item in merged]
    assert len(hashes) == 10
    assert hashes[:2] == ["paragraph-0", "paragraph-1"]
    assert "relation-winner" in hashes
    assert len(set(hashes)) == 10


def test_graph_replacements_respect_slot_limit(retriever: MagicMock) -> None:
    base = _base_results()
    graph = [_result(f"relation-{index}", 0.1, 1.0 - index * 0.01, result_type="relation") for index in range(4)]
    merged = posterior_graph._competition_merge(
        retriever,
        query="测试问题",
        base_results=base,
        graph_results=graph,
        top_k=10,
        cfg=PosteriorGraphConfig(max_graph_slots=2),
    )
    graph_hashes = [item.hash_value for item in merged if item.hash_value.startswith("relation-")]
    assert len(merged) == 10
    assert len(graph_hashes) == 2
    assert [item.hash_value for item in merged[:2]] == ["paragraph-0", "paragraph-1"]
