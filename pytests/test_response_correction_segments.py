"""纠错消息在回复压缩后仍独立发送。"""

from dataclasses import asdict
from types import SimpleNamespace
from typing import Any, List, Tuple

import pytest

from src.chat.utils import utils
from src.chat.utils.utils import ProcessedResponseSegment, _merge_processed_segments_to_max_count
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.plugins.built_in.chinese_typo import plugin
from src.plugins.built_in.chinese_typo.dictionary_typo import create_dictionary_typo
from src.plugins.built_in.chinese_typo.settings import TypoSettings


def test_dictionary_mode_edits_one_segment_and_keeps_correction_separate(monkeypatch) -> None:
    config = TypoSettings(mode="dictionary", dictionary_probability=1)
    monkeypatch.setattr(plugin.random, "random", lambda: 0.0)
    monkeypatch.setattr(plugin.random, "choice", lambda choices: choices[0])

    def unexpected_rule_generator(**_kwargs: Any) -> None:
        pytest.fail("词库模式调用了规则生成器")

    monkeypatch.setattr(plugin, "ChineseTypoGenerator", unexpected_rule_generator)
    segments = [
        asdict(ProcessedResponseSegment("辛苦", separator="，")),
        asdict(ProcessedResponseSegment("辛苦", separator="。")),
    ]
    result = plugin.apply_typos(segments, config)

    assert result == [
        asdict(ProcessedResponseSegment("幸苦", separator="，")),
        asdict(ProcessedResponseSegment("辛苦", quote_previous=True, is_correction=True)),
        segments[1],
    ]


@pytest.mark.parametrize(
    "sentences,probability",
    [
        ([("辛苦", "")], 0),
        ([("```text", "\n"), ("辛苦", "\n"), ("```", "")], 1),
        ([("``辛苦``", "")], 1),
        ([("《辛苦》", "")], 1),
        ([("https://example.com/辛苦", "")], 1),
        ([("现在", ""), ("再", "")], 1),
        ([("Hello 123", "")], 1),
    ],
)
def test_dictionary_mode_skips_disabled_or_ineligible_text(
    sentences: List[Tuple[str, str]], probability: float
) -> None:
    assert create_dictionary_typo(sentences, probability) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rule", "llm"])
@pytest.mark.parametrize("quote", [False, True])
async def test_correction_stays_separate_through_post_processing(monkeypatch, mode: str, quote: bool) -> None:
    sentences = [(text, "。") for text in ["今天先休息吧", "我也准备睡了", "明天见", "到时候一起吃饭呀"]]

    class TypoGenerator:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def create_typo_sentence(self, sentence: str) -> Tuple[str, str]:
            return ("明天件", "见") if sentence == "明天见" else (sentence, "")

    async def split_with_llm(_text: str):
        return sentences

    monkeypatch.setattr(
        utils,
        "global_config",
        SimpleNamespace(
            response_post_process=SimpleNamespace(enable_response_post_process=True),
            response_splitter=SimpleNamespace(
                mode=mode,
                enable=True,
                enable_kaomoji_protection=True,
                max_length=512,
                max_sentence_num=9,
                max_split_num=3,
            ),
        ),
    )
    monkeypatch.setattr(plugin, "ChineseTypoGenerator", TypoGenerator)
    monkeypatch.setattr(plugin.random, "random", lambda: 0.0)
    monkeypatch.setattr(utils, "_split_into_sentence_segments", lambda _text: sentences)
    monkeypatch.setattr(utils, "split_text_with_llm", split_with_llm)
    instance = plugin.create_plugin()
    instance.set_plugin_config({
        "plugin": {"config_version": "1.0.0"},
        "typo": {"enable_correction_quote": quote},
    })

    async def invoke_hook(name, **kwargs):
        assert name == "maisaka.reply.after_split"
        assert kwargs["session_id"] == "test-session"
        result = await instance.inject_chinese_typos(**kwargs)
        return SimpleNamespace(kwargs=result.get("modified_kwargs", kwargs))

    context = BuiltinToolRuntimeContext(None, SimpleNamespace(session_id="test-session"))
    monkeypatch.setattr(context, "get_runtime_manager", lambda: SimpleNamespace(invoke_hook=invoke_hook))
    original = "今天先休息吧。我也准备睡了。明天见。到时候一起吃饭呀"
    result = await context.post_process_reply_message_items_async(original)
    skipped = await context.post_process_reply_message_items_async(original, enable_chinese_typo=False)
    assert all(not item.quote_previous for item in skipped)
    assert "明天件" not in "".join(item.sequence.components[0].text for item in skipped)

    assert [item.sequence.components[0].text for item in result] == [
        "今天先休息吧。我也准备睡了。明天件",
        "见",
        "到时候一起吃饭呀",
    ]
    assert [segment.quote_previous for segment in result] == [False, quote, False]


@pytest.mark.parametrize("max_count", [1, 3])
def test_multiple_corrections_keep_boundaries_when_limit_is_too_small(max_count: int) -> None:
    segments = [
        ProcessedResponseSegment("明天件", separator="。"),
        ProcessedResponseSegment("见", quote_previous=True, is_correction=True),
        ProcessedResponseSegment("一起次饭", separator="。"),
        ProcessedResponseSegment("吃", quote_previous=True, is_correction=True),
        ProcessedResponseSegment("到时候联系"),
    ]

    assert _merge_processed_segments_to_max_count(segments, max_count) == segments


def test_regular_sentences_still_merge_with_punctuation() -> None:
    segments = [ProcessedResponseSegment(text, separator="。") for text in ["一", "二", "三", "四", "五"]]

    result = _merge_processed_segments_to_max_count(segments, 3)

    assert [segment.text for segment in result] == ["一。二", "三。四", "五"]
    assert not any(segment.quote_previous or segment.is_correction for segment in result)


def test_quote_previous_alone_also_keeps_message_separate() -> None:
    segments = [
        ProcessedResponseSegment("明天件"),
        ProcessedResponseSegment("见", quote_previous=True),
        ProcessedResponseSegment("一起吃饭呀"),
    ]

    assert _merge_processed_segments_to_max_count(segments, 1) == segments
