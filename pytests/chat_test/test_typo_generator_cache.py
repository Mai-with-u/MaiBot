"""共享词频数据不应改变实例参数或在查询时增长。"""

from functools import cache
from unittest.mock import Mock

from src.chat.utils import typo_generator as typo


def test_instances_reuse_data_but_keep_independent_parameters(monkeypatch) -> None:
    build_pinyin = Mock(return_value={"tian1": ["天"]})
    load_frequency = Mock(return_value={"天": 100.0})
    monkeypatch.setattr(typo.ChineseTypoGenerator, "_create_pinyin_dict", staticmethod(cache(build_pinyin)))
    monkeypatch.setattr(typo.ChineseTypoGenerator, "_load_or_create_char_frequency", staticmethod(cache(load_frequency)))

    first = typo.ChineseTypoGenerator(error_rate=0.1, tone_error_rate=0.0)
    second = typo.ChineseTypoGenerator(error_rate=0.9, tone_error_rate=0.0)

    build_pinyin.assert_called_once_with()
    load_frequency.assert_called_once_with()
    assert first.pinyin_dict is second.pinyin_dict
    assert first.char_frequency is second.char_frequency
    assert (first.error_rate, second.error_rate) == (0.1, 0.9)
    assert first._get_similar_frequency_chars("天", "missing") is None
    assert second.pinyin_dict == {"tian1": ["天"]}
