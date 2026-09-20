"""回复分割压缩时的分隔符保留测试（issue #2056）。

`split_into_sentences_w_remove_punctuation` 在提取结果时丢弃分隔符，
而 `_merge_processed_segments_to_max_count` 在段数超 `max_split_num` 时用
`""` 硬拼接，导致「今天去上课了然后回来睡觉」这类无标点无停顿的粘连消息。
"""

from src.chat.utils import utils as chat_utils


def _disable_typo_and_random_merge(monkeypatch) -> None:
    monkeypatch.setattr(chat_utils.global_config.response_post_process, "enable_response_post_process", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "enable", True)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable", False)
    # random() 恒 1.0：概率合并条件 random() < merge_probability 永不成立
    monkeypatch.setattr(chat_utils.random, "random", lambda: 1.0)


def test_compressed_segments_keep_clause_pause(monkeypatch) -> None:
    """issue #2056 的原样复现：5 段压缩到 3 条时，拼接处必须保留停顿。"""

    _disable_typo_and_random_merge(monkeypatch)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "max_split_num", 3)

    text = "今天去上课了，然后回来睡觉，睡醒吃了饭，现在有点无聊，你呢？"
    result = chat_utils.process_llm_response(text)

    assert result == ["今天去上课了 然后回来睡觉", "睡醒吃了饭 现在有点无聊", "你呢？"]


def test_compression_not_triggered_keeps_original_behavior(monkeypatch) -> None:
    """段数不超 max_split_num 时不压缩，行为与原来完全一致（逐段独立发送）。"""

    _disable_typo_and_random_merge(monkeypatch)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "max_split_num", 5)

    text = "今天去上课了，然后回来睡觉，你呢？"
    result = chat_utils.process_llm_response(text)

    assert result == ["今天去上课了", "然后回来睡觉", "你呢？"]


def test_newline_separator_is_preserved_as_newline(monkeypatch) -> None:
    """换行分隔的段在压缩拼接时仍用换行连接，保住段落结构。"""

    _disable_typo_and_random_merge(monkeypatch)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "max_split_num", 1)

    text = "第一行内容\n第二行内容\n第三行内容"
    result = chat_utils.process_llm_response(text)

    assert result == ["第一行内容\n第二行内容\n第三行内容"]
