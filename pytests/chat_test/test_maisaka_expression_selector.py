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
