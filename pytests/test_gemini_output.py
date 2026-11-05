import pytest

from src.chat.replyer.gemini_output import GeminiReplyFormatError, normalize_gemini_reply


def test_thought_only_with_quoted_marker_is_rejected():
    text = '''[:thought
The user asks for a reply.
Make sure not to leak any '[:thought' syntax into the output!
E.g., "才不上当呢" or "才不给你看"
Let's make it concise and natural: "女孩子的心思哪能随便给你看"
]'''
    with pytest.raises(GeminiReplyFormatError, match="仅包含思考内容"):
        normalize_gemini_reply(text)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_bracket_thought_preserves_only_body(newline):
    text = newline.join([
        "  [:THOUGHT", "草稿中有 [选项] 和 '[:thought'，都不是边界。", "  ]  ", "正式回复",
    ])
    assert normalize_gemini_reply(text) == "正式回复"


@pytest.mark.parametrize("text", [
    "[:thought\n没有结束标记",
    "[:thought\n草稿引用 [选项] 后继续分析",
    "[:thought 草稿] 无法可靠确定的正文",
])
def test_unreliable_bracket_boundary_is_rejected(text):
    with pytest.raises(GeminiReplyFormatError, match="缺少可靠闭合边界"):
        normalize_gemini_reply(text)


@pytest.mark.parametrize("text", [
    "正文提到 [:thought 内容 ]",
    "`[:thought 内容 ]` 是一个示例",
    "[:thoughtful 不是思考标签]",
])
def test_ordinary_mentions_are_unchanged(text):
    assert normalize_gemini_reply(text) == text


def test_consecutive_mixed_thought_blocks():
    text = "[:thought\n草稿一\n]\n<think>草稿二</think>\n[:thought\n草稿三\n]\n正式回复"
    assert normalize_gemini_reply(text) == "正式回复"
    with pytest.raises(GeminiReplyFormatError, match="仅包含思考内容"):
        normalize_gemini_reply("<think>草稿</think>\n[:thought\n更多草稿\n]")
