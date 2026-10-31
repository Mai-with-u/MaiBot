from src.maisaka.builtin_tool.reply import strip_emoji_placeholders


def test_strip_emoji_placeholders_removes_fabricated_tag() -> None:
    assert strip_emoji_placeholders("[表情包: 傲娇,捂耳,不听不听]") == ""
    assert strip_emoji_placeholders("好啦好啦[表情包：开心] 知道了") == "好啦好啦 知道了"
    assert strip_emoji_placeholders("[表情包]") == ""


def test_strip_emoji_placeholders_keeps_normal_text() -> None:
    assert strip_emoji_placeholders("今天吃[火锅]吧") == "今天吃[火锅]吧"
