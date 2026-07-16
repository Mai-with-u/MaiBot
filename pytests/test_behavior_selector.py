from src.learners.behavior_scenario import BehaviorScenarioProfile, BehaviorScenarioTagCluster
from src.learners.behavior_selector import BehaviorPatternSelector


def _build_scenario_profile() -> BehaviorScenarioProfile:
    return BehaviorScenarioProfile(
        summary="用户正在认真询问技术问题",
        tag_clusters=[
            BehaviorScenarioTagCluster(kind="domain", tags=["技术排障"]),
            BehaviorScenarioTagCluster(kind="attitude", tags=["认真求助"]),
        ],
        confidence=0.9,
    )


def test_build_group_reference_text_distinguishes_learning_types() -> None:
    reference_text = BehaviorPatternSelector._build_group_reference_text(
        behaviors=[
            {
                "id": 11,
                "learning_type": "observed_behavior",
                "action": "群友先确认问题范围",
                "outcome": "提问者补充了信息",
            },
            {
                "id": 22,
                "learning_type": "self_reflection",
                "action": "先询问一个关键配置项",
                "outcome": "用户给出了配置细节",
            },
        ],
        scenario_profile=_build_scenario_profile(),
    )

    assert "麦麦的过往经验" in reference_text
    assert "麦麦过去采用的做法：先询问一个关键配置项" in reference_text
    assert "当时观察到的结果：用户给出了配置细节" in reference_text
    assert "其他人的互动经验" in reference_text
    assert "观察到的互动方式：群友先确认问题范围" in reference_text
    assert "可以在适合麦麦当前身份、关系和情境时灵活借鉴" in reference_text
    assert "behavior_id：11" in reference_text
    assert "behavior_id：22" in reference_text


def test_build_group_reference_text_allows_adapting_observed_behavior() -> None:
    reference_text = BehaviorPatternSelector._build_group_reference_text(
        behaviors=[
            {
                "id": 33,
                "learning_type": "observed_behavior",
                "action": "使用熟人间的夸张吐槽回应",
                "outcome": "群友继续玩梗",
            }
        ],
        scenario_profile=_build_scenario_profile(),
    )

    assert "麦麦的过往经验" not in reference_text
    assert "其他人的互动经验" in reference_text
    assert "灵活借鉴" in reference_text
    assert "麦麦过去采用的做法" not in reference_text


def test_build_group_reference_text_skips_unknown_learning_type(caplog) -> None:
    reference_text = BehaviorPatternSelector._build_group_reference_text(
        behaviors=[
            {
                "id": 44,
                "learning_type": "unexpected_type",
                "action": "未知来源行为",
                "outcome": "未知结果",
            }
        ],
        scenario_profile=_build_scenario_profile(),
    )

    assert "未知来源行为" not in reference_text
    assert "学习类型未知" in caplog.text
