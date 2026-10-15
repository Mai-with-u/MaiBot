"""错别字数据共享及关闭功能时的回归测试。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import builtins
import pytest

from src.chat.utils import typo_generator, utils as chat_utils


def test_concurrent_generators_share_data_without_changing_parameters(monkeypatch, tmp_path) -> None:
    generator_type = typo_generator.ChineseTypoGenerator
    cached_loaders = (
        generator_type._create_pinyin_dict,
        generator_type._load_or_create_char_frequency,
        generator_type._load_word_frequencies,
    )
    for loader in cached_loaders:
        loader.cache_clear()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "depends-data").mkdir()
    (tmp_path / "depends-data/char_frequency.json").write_text('{"一": 10}', encoding="utf-8")
    reads = {"char_frequency.json": 0, "dict.txt": 0}

    def counted_open(file, *args, **kwargs):
        name = Path(file).name
        if name in reads:
            reads[name] += 1
        return builtins.open(file, *args, **kwargs)

    monkeypatch.setattr(typo_generator, "open", counted_open, raising=False)

    def build_and_query(rate):
        generator = generator_type(error_rate=rate)
        return generator, generator._get_word_homophones("一声")

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(build_and_query, [0.1, 0.2, 0.3, 0.4]))
        first, candidates = results[0]
        for (generator, words), rate in zip(results, [0.1, 0.2, 0.3, 0.4], strict=True):
            assert generator.pinyin_dict is first.pinyin_dict
            assert generator.char_frequency is first.char_frequency
            assert generator.error_rate == rate
            assert words == candidates
        assert reads == {"char_frequency.json": 1, "dict.txt": 1}
        assert generator_type._create_pinyin_dict.cache_info().misses == 1
        first.tone_error_rate = 0
        first._get_similar_frequency_chars("一", "unknown")
        assert "unknown" not in first.pinyin_dict
    finally:
        for loader in cached_loaders:
            loader.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rule", "llm"])
@pytest.mark.parametrize("global_enabled, call_enabled", [(False, True), (True, False), (False, False)])
async def test_disabled_typo_skips_construction(monkeypatch, mode, global_enabled, call_enabled) -> None:
    def unexpected_construction(**_kwargs):
        pytest.fail("关闭错别字功能时不应构造生成器")

    async def fixed_split(_text):
        return [("今天见", "")]

    monkeypatch.setattr(chat_utils, "ChineseTypoGenerator", unexpected_construction)
    monkeypatch.setattr(chat_utils, "split_text_with_llm", fixed_split)
    monkeypatch.setattr(chat_utils.global_config.response_post_process, "enable_response_post_process", True)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "mode", mode)
    monkeypatch.setattr(chat_utils.global_config.response_splitter, "enable", False)
    monkeypatch.setattr(chat_utils.global_config.chinese_typo, "enable", global_enabled)

    sync_segments = chat_utils.process_llm_response_segments("今天见", enable_chinese_typo=call_enabled)
    async_segments = await chat_utils.process_llm_response_segments_async("今天见", enable_chinese_typo=call_enabled)

    assert [segment.text for segment in sync_segments] == ["今天见"]
    assert async_segments == sync_segments
