import pytest

import src.learners.behavior_selector as behavior_selector
from src.learners.behavior_scenario import BehaviorScenarioProfile, BehaviorScenarioTagCluster
from src.learners.behavior_selector import BehaviorPatternSelector


def test_profile_tag_match_bonus_ranks_intent_matched_candidate_first(monkeypatch: pytest.MonkeyPatch) -> None:
    selector = BehaviorPatternSelector()
    profile = BehaviorScenarioProfile(
        summary="用户询问角色设定并期待轻松回应",
        tag_clusters=[BehaviorScenarioTagCluster(kind="need", tags=["轻松角色互动"])],
        confidence=0.9,
    )
    monkeypatch.setattr(
        behavior_selector,
        "build_profile_tag_mapping",
        lambda _profile: {"need:tc_role_play": 1.0},
    )

    candidates = [
        {
            "id": 1,
            "count": 1,
            "score": 0.0,
            "success_count": 0,
            "failure_count": 0,
            "activation_count": 0,
            "learning_type": "observed_behavior",
            "profile_tag_distribution": [{"tag": "need:tc_generic_chat", "probability": 1.0}],
        },
        {
            "id": 2,
            "count": 1,
            "score": 0.0,
            "success_count": 0,
            "failure_count": 0,
            "activation_count": 0,
            "learning_type": "observed_behavior",
            "profile_tag_distribution": [{"tag": "need:tc_role_play", "probability": 1.0}],
        },
    ]

    ranked_candidates = selector._rank_candidates_by_scene_cluster(
        candidates,
        scene_cluster_scores={1: 1.0, 2: 1.0},
        scenario_profile=profile,
        max_count=2,
    )

    assert [candidate["id"] for candidate in ranked_candidates] == [2, 1]
    assert ranked_candidates[0]["profile_tag_match_score"] == 1.0
    assert ranked_candidates[0]["behavior_retrieval_score"] > ranked_candidates[1]["behavior_retrieval_score"]
