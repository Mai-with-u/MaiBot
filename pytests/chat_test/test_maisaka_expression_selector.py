from src.chat.replyer.maisaka_expression_selector import (
    MAX_SELECTED_EXPRESSIONS,
    MaisakaExpressionSelector,
)


def test_expression_selector_prompt_and_parser_share_five_item_limit() -> None:
    """提示词与结果解析应统一允许最多五条表达。"""

    selector = MaisakaExpressionSelector()
    candidates = [
        {
            "id": expression_id,
            "situation": f"情景 {expression_id}",
            "style": f"风格 {expression_id}",
        }
        for expression_id in range(1, 7)
    ]

    prompt = selector._build_selector_prompt(candidates=candidates)
    selected_ids = selector._parse_selected_ids(
        '{"selected_ids":[1,2,3,4,5,6]}',
        candidates,
    )

    assert MAX_SELECTED_EXPRESSIONS == 5
    assert f"选择 0 到 {MAX_SELECTED_EXPRESSIONS} 条" in prompt
    assert selected_ids == [1, 2, 3, 4, 5]


def test_expression_query_includes_available_intent() -> None:
    """唯一向量模式应始终把表达意图用于召回。"""

    query_text = MaisakaExpressionSelector._build_expression_query_text(
        "自然接话",
        {
            "expression_intent": {"scene": "轻松调侃", "tone": "活泼"},
            "reply_reference": "回应对方刚才的玩笑",
        },
    )

    assert "表达场景：轻松调侃" in query_text
    assert "期望语气：活泼" in query_text
    assert "回复信息参考：\n回应对方刚才的玩笑" in query_text
