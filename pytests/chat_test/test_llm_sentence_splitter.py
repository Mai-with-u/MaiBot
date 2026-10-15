"""LLM 断句器测试。"""

import pytest

from src.chat.utils import llm_sentence_splitter


class _Response:
    response = '["今天去上课了，", "然后回来睡觉"]'


class _Client:
    def __init__(self, **_kwargs: object) -> None:
        pass

    async def generate_response(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


def _client_returning(payload: str) -> type[_Client]:
    """构造固定返回指定内容的 LLM 客户端替身。"""

    class _FixedClient(_Client):
        async def generate_response(self, *_args: object, **_kwargs: object) -> _Response:
            response = _Response()
            response.response = payload
            return response

    return _FixedClient


@pytest.mark.asyncio
async def test_llm_splitter_preserves_original_text(monkeypatch) -> None:
    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _Client)

    assert await llm_sentence_splitter.split_text_with_llm("今天去上课了，然后回来睡觉") == [
        ("今天去上课了", "，"),
        ("然后回来睡觉", ""),
    ]


@pytest.mark.asyncio
async def test_llm_splitter_rejects_rewritten_text(monkeypatch) -> None:
    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _client_returning('["改写后的文本"]'))

    with pytest.raises(ValueError, match="未完整保留原文"):
        await llm_sentence_splitter.split_text_with_llm("原始文本")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,payload,index,returned",
    [
        ("喵啊", '["喵哦"]', 1, "喵哦"),
        ("原始文本", '["原始"]', 2, "原始"),
        ("原始", '["原始文本"]', 2, "原始文本"),
    ],
)
async def test_llm_splitter_reports_first_difference(monkeypatch, text, payload, index, returned) -> None:
    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _client_returning(payload))

    with pytest.raises(ValueError, match="未完整保留原文") as exc_info:
        await llm_sentence_splitter.split_text_with_llm(text)

    message = str(exc_info.value)
    assert f"首个差异位置={index}（从 0 开始）" in message
    assert f"原文长度={len(text)}，结果长度={len(returned)}" in message
    assert f"原文片段={text!r}" in message
    assert f"结果片段={returned!r}" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", ["no_split", '"no_split"', "```json\nno_split\n```", '["no_split"]', '```json\n["no_split"]\n```']
)
async def test_llm_splitter_keeps_text_when_model_declines(monkeypatch, payload: str) -> None:
    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _client_returning(payload))

    assert await llm_sentence_splitter.split_text_with_llm("今天还挺好的") == [("今天还挺好的", "")]


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
async def test_llm_splitter_accepts_newline_changes(monkeypatch, newline: str) -> None:
    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _client_returning('["？", "恋爱企划"]'))

    assert await llm_sentence_splitter.split_text_with_llm(f"？{newline}恋爱企划") == [("？", ""), ("恋爱企划", "")]

    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _client_returning('["？\\n", "恋爱企划"]'))

    assert await llm_sentence_splitter.split_text_with_llm("？恋爱企划") == [("？", ""), ("恋爱企划", "")]


@pytest.mark.asyncio
async def test_llm_splitter_rejects_marker_mixed_with_text(monkeypatch) -> None:
    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _client_returning('["no_split", "晚安喵"]'))

    with pytest.raises(ValueError, match="未完整保留原文"):
        await llm_sentence_splitter.split_text_with_llm("晚安喵")


@pytest.mark.asyncio
async def test_llm_splitter_rejects_unknown_string_payload(monkeypatch) -> None:
    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _client_returning('"不需要拆分"'))

    with pytest.raises(ValueError, match="不是合法 JSON 数组"):
        await llm_sentence_splitter.split_text_with_llm("今天还挺好的")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,payload,expected",
    [
        ("修什么修 修坏了就完了", '["修什么修", "修坏了就完了"]', [("修什么修", ""), ("修坏了就完了", "")]),
        ("我key的model。好的", '["我 key 的 model。", "好的"]', [("我key的model。", ""), ("好的", "")]),
        ("喵～你好，世界。", '["喵~", "你好,世界."]', [("喵～", ""), ("你好，世界。", "")]),
        ("a  b，  c d", '["a b，", "c  d"]', [("a  b", "，  "), ("c d", "")]),
    ],
)
async def test_llm_splitter_ignores_whitespace_changes(monkeypatch, text, payload, expected) -> None:
    monkeypatch.setattr(llm_sentence_splitter, "LLMServiceClient", _client_returning(payload))

    assert await llm_sentence_splitter.split_text_with_llm(text) == expected
