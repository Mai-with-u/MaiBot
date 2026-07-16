"""覆盖 Replyer 把工具调用 XML 复读到用户侧的拦截逻辑。"""

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator


def test_contains_leaked_tool_markup_detects_common_tags() -> None:
    assert BaseMaisakaReplyGenerator._contains_leaked_tool_markup(
        '<tool_use>{"name":"web_search"}</tool_use>'
    )
    assert BaseMaisakaReplyGenerator._contains_leaked_tool_markup("<tool_call>x</tool_call>")
    assert BaseMaisakaReplyGenerator._contains_leaked_tool_markup("<tool result>x</tool result>")


def test_contains_leaked_tool_markup_allows_normal_chat() -> None:
    assert not BaseMaisakaReplyGenerator._contains_leaked_tool_markup("北京今天挺热的")
    assert not BaseMaisakaReplyGenerator._contains_leaked_tool_markup("")
