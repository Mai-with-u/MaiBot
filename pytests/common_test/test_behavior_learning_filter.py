from types import SimpleNamespace

from src.learners.behavior_learner import BehaviorCandidate, BehaviorLearner
from src.learners.behavior_pattern_store import ACTOR_MAIBOT_SELF, LEARNING_SELF_REFLECTION


def _message(text: str):
    return SimpleNamespace(processed_plain_text=text)


def test_filter_behavior_candidates_keeps_reusable_strategy() -> None:
    learner = BehaviorLearner("session-a")
    candidate = BehaviorCandidate(
        action="先承认信息不足，再追问一个关键配置点",
        outcome="对方补充配置细节，排查方向变得明确",
        source_ids=["1", "2"],
        actor_type=ACTOR_MAIBOT_SELF,
        learning_type=LEARNING_SELF_REFLECTION,
    )

    result = learner._filter_behavior_candidates([candidate], [_message("麦麦追问配置"), _message("用户补充路径")])

    assert len(result.candidates) == 1
    assert result.skipped_reasons == {}


def test_filter_behavior_candidates_does_not_keyword_block_query_result() -> None:
    learner = BehaviorLearner("session-a")
    candidate = BehaviorCandidate(
        action="当用户查询Token趋势命令帮助时，返回完整的命令用法、别名和参数说明",
        outcome="用户立即使用带参数的具体命令，并得到按版本分组的趋势数据",
        source_ids=["1"],
        actor_type=ACTOR_MAIBOT_SELF,
        learning_type=LEARNING_SELF_REFLECTION,
    )

    result = learner._filter_behavior_candidates([candidate], [_message("麦麦返回命令帮助")])

    assert len(result.candidates) == 1
    assert result.skipped_reasons == {}


def test_filter_behavior_candidates_does_not_keyword_block_vague_outcome() -> None:
    learner = BehaviorLearner("session-a")
    candidate = BehaviorCandidate(
        action="用简短调侃回应群友的玩梗请求",
        outcome="氛围活跃",
        source_ids=["1"],
        actor_type=ACTOR_MAIBOT_SELF,
        learning_type=LEARNING_SELF_REFLECTION,
    )

    result = learner._filter_behavior_candidates([candidate], [_message("麦麦调侃回应")])

    assert len(result.candidates) == 1
    assert result.skipped_reasons == {}
