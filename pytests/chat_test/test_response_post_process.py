"""回复后处理元数据测试。"""

import pytest

from src.chat.utils import utils as chat_utils
from src.chat.utils.utils import ProcessedResponseSegment


class _FixedTypoGenerator:
    """返回固定错别字与纠正内容，隔离词频数据和拼音随机性。"""

    def __init__(self, **_kwargs: object) -> None:
        pass

    def create_typo_sentence(self, sentence: str) -> tuple[str, str]:
        return sentence.replace("今天见", "今田见"), "天"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rule", "llm"])
@pytest.mark.parametrize("config_enabled,call_enabled", [(False, True), (True, False), (False, False)])
async def test_disabled_typo_skips_construction(monkeypatch, mode, config_enabled, call_enabled) -> None:
    def unexpected_constructor(**_kwargs):
        pytest.fail("关闭错别字功能时不应构建生成器")

    async def split_with_llm(_text):
        return [("今天见", "")]

    monkeypatch.setattr(chat_utils, "ChineseTypoGenerator", unexpected_constructor)
    monkeypatch.setattr(chat_utils, "split_text_with_llm", split_with_llm)
    monkeypatch.setattr(chat_utils.global_config.response_post_process, "enable_response_post_process", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "mode", mode)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "enable", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable", config_enabled)

    segments = await chat_utils.process_llm_response_segments_async("今天见", enable_chinese_typo=call_enabled)

    assert segments == [ProcessedResponseSegment("今天见")]


@pytest.mark.asyncio
async def test_llm_split_preserves_typo_correction_metadata(monkeypatch) -> None:
    async def split_with_llm(text):
        return [(text, "")]

    monkeypatch.setattr(chat_utils, "split_text_with_llm", split_with_llm)
    monkeypatch.setattr(chat_utils, "ChineseTypoGenerator", _FixedTypoGenerator)
    monkeypatch.setattr(chat_utils.global_config.response_post_process, "enable_response_post_process", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "mode", "llm")
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "max_split_num", 3)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "max_sentence_num", 3)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable_correction_quote", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "correction_quote_probability", 1.0)
    monkeypatch.setattr(chat_utils.random, "random", lambda: 0.0)

    segments = await chat_utils.process_llm_response_segments_async("今天见，")

    assert segments == [
        ProcessedResponseSegment("今田见，"),
        ProcessedResponseSegment("天", quote_previous=True),
    ]


def test_typo_correction_marks_quote_previous(monkeypatch) -> None:
    monkeypatch.setattr(chat_utils, "ChineseTypoGenerator", _FixedTypoGenerator)
    monkeypatch.setattr(chat_utils.global_config.response_post_process, "enable_response_post_process", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "enable", False)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable_correction_quote", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "correction_quote_probability", 1.0)
    monkeypatch.setattr(chat_utils.random, "random", lambda: 0.0)

    segments = chat_utils.process_llm_response_segments("今天见", enable_splitter=False)

    assert segments == [
        ProcessedResponseSegment("今田见"),
        ProcessedResponseSegment("天", quote_previous=True),
    ]
    assert chat_utils.process_llm_response("今天见", enable_splitter=False) == ["今田见", "天"]


def test_typo_correction_quote_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(chat_utils, "ChineseTypoGenerator", _FixedTypoGenerator)
    monkeypatch.setattr(chat_utils.global_config.response_post_process, "enable_response_post_process", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "enable", False)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable_correction_quote", False)
    monkeypatch.setattr(chat_utils.random, "random", lambda: 0.0)

    segments = chat_utils.process_llm_response_segments("今天见", enable_splitter=False)

    assert segments == [
        ProcessedResponseSegment("今田见"),
        ProcessedResponseSegment("天"),
    ]


def test_typo_correction_quote_respects_probability(monkeypatch) -> None:
    monkeypatch.setattr(chat_utils, "ChineseTypoGenerator", _FixedTypoGenerator)
    monkeypatch.setattr(chat_utils.global_config.response_post_process, "enable_response_post_process", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "enable", False)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable_correction_quote", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "correction_quote_probability", 0.0)
    monkeypatch.setattr(chat_utils.random, "random", lambda: 0.0)

    segments = chat_utils.process_llm_response_segments("今天见", enable_splitter=False)

    assert segments == [
        ProcessedResponseSegment("今田见"),
        ProcessedResponseSegment("天"),
    ]


def test_merge_keeps_typo_correction_at_start_of_quoted_message() -> None:
    segments = [
        ProcessedResponseSegment("今田见"),
        ProcessedResponseSegment("天", quote_previous=True),
        ProcessedResponseSegment("晚点聊"),
    ]

    merged_segments = chat_utils._merge_processed_segments_to_max_count(segments, 2)

    assert merged_segments == [
        ProcessedResponseSegment("今田见"),
        ProcessedResponseSegment("天晚点聊", quote_previous=True),
    ]


def test_split_keeps_trailing_separators(monkeypatch) -> None:
    monkeypatch.setattr(chat_utils.random, "random", lambda: 1.0)  # 分割阶段不做概率合并

    sentence_segments = chat_utils._split_into_sentence_segments("今天去上课了，然后回来睡觉，睡醒吃了饭")

    assert sentence_segments == [
        ("今天去上课了", "，"),
        ("然后回来睡觉", "，"),
        ("睡醒吃了饭", ""),
    ]
    # 兼容旧接口：仅返回句子内容
    assert chat_utils.split_into_sentences_w_remove_punctuation("今天去上课了，然后回来睡觉，睡醒吃了饭") == [
        "今天去上课了",
        "然后回来睡觉",
        "睡醒吃了饭",
    ]


def test_merge_restores_separators_when_compressing() -> None:
    segments = [
        ProcessedResponseSegment("今天去上课了", separator="，"),
        ProcessedResponseSegment("然后回来睡觉", separator="，"),
        ProcessedResponseSegment("睡醒吃了饭", separator="，"),
        ProcessedResponseSegment("现在有点无聊", separator="，"),
        ProcessedResponseSegment("你呢？"),
    ]

    merged_segments = chat_utils._merge_processed_segments_to_max_count(segments, 3)

    assert merged_segments == [
        ProcessedResponseSegment("今天去上课了，然后回来睡觉", separator="，"),
        ProcessedResponseSegment("睡醒吃了饭，现在有点无聊", separator="，"),
        ProcessedResponseSegment("你呢？"),
    ]


def test_merge_restores_separators_around_typo_correction() -> None:
    segments = [
        ProcessedResponseSegment("今田见", separator="，"),
        ProcessedResponseSegment("天", quote_previous=True),
        ProcessedResponseSegment("晚点聊", separator="，"),
        ProcessedResponseSegment("先去吃饭"),
    ]

    merged_segments = chat_utils._merge_processed_segments_to_max_count(segments, 3)

    assert merged_segments == [
        ProcessedResponseSegment("今田见", separator="，"),
        ProcessedResponseSegment("天", quote_previous=True),
        ProcessedResponseSegment("晚点聊，先去吃饭", separator=""),
    ]


def test_compression_restores_punctuation_end_to_end(monkeypatch) -> None:
    monkeypatch.setattr(chat_utils.global_config.response_post_process, "enable_response_post_process", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "enable", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "enable_kaomoji_protection", False)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "max_sentence_num", 10)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "max_split_num", 3)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable", False)
    monkeypatch.setattr(chat_utils.random, "random", lambda: 1.0)  # 分割阶段不做概率合并

    segments = chat_utils.process_llm_response_segments("今天去上课了，然后回来睡觉，睡醒吃了饭，现在有点无聊，你呢？")

    assert [segment.text for segment in segments] == [
        "今天去上课了，然后回来睡觉",
        "睡醒吃了饭，现在有点无聊",
        "你呢？",
    ]
    assert chat_utils.process_llm_response("今天去上课了，然后回来睡觉，睡醒吃了饭，现在有点无聊，你呢？") == [
        "今天去上课了，然后回来睡觉",
        "睡醒吃了饭，现在有点无聊",
        "你呢？",
    ]
