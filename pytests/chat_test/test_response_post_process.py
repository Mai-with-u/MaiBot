"""回复后处理元数据测试。"""

from src.chat.utils import utils as chat_utils
from src.chat.utils.utils import ProcessedResponseSegment


class _FixedTypoGenerator:
    """返回固定错别字与纠正内容，隔离词频数据和拼音随机性。"""

    def __init__(self, **_kwargs: object) -> None:
        pass

    def create_typo_sentence(self, _sentence: str) -> tuple[str, str]:
        return "今田见", "天"


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
