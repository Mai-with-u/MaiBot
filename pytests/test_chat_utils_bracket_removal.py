import pytest
from src.chat.utils.utils import process_llm_response_segments


def test_preserve_math_formula_brackets():
    """测试数学推导与公式中的算术括号不会被后处理误删"""
    text = (
        "这题讲清楚点哈\n"
        "条件乘开就是ab=a+b，移项变成(a-1)(b-1)=1，令x=a-1,y=b-1，xy=1\n"
        "原式代进去变(x+1)²/x+(y+1)²/y，展开是x+2+1/x+y+2+1/y，合并后2(x+y)+4\n"
        "xy=1所以x+y≥2，最小值就8，当x=y=1即a=b=2取等，这样应该好懂了吧"
    )
    segments = process_llm_response_segments(text)
    combined = "".join(s.text for s in segments)

    assert "(a-1)(b-1)=1" in combined, "因式分解括号被误删"
    assert "(x+1)²/x+(y+1)²/y" in combined, "分式中的平方项括号被误删"
    assert "2(x+y)+4" in combined, "合并项括号未正确保留"


def test_remove_chinese_action_brackets():
    """测试真正的中文动作/心理描写能被准确剔除"""
    text = "（微笑）这道题其实很简单哦！[叹气] 你们怎么都不会呢(抓头)"
    segments = process_llm_response_segments(text)
    combined = "".join(s.text for s in segments)

    assert "微笑" not in combined, "中文动作描写未被剔除"
    assert "叹气" not in combined, "中文动作描写未被剔除"
    assert "抓头" not in combined, "中文动作描写未被剔除"
    assert "这道题其实很简单哦" in combined


def test_mixed_action_and_math_brackets():
    """测试中文动作与数学公式混排在同一行时，动作被删但公式保留"""
    text = "（思考中）我们可以令 (x+1)^2 = 4，从而解出 x [计算完毕]"
    segments = process_llm_response_segments(text)
    combined = "".join(s.text for s in segments)

    assert "(x+1)^2 = 4" in combined, "公式被误伤"
    assert "思考中" not in combined, "动作未被清除"
    assert "计算完毕" not in combined, "动作未被清除"
