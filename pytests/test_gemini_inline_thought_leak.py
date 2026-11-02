"""Gemini 思考草稿泄漏剥离与发送阻断回归测试（#2362）。

覆盖三个层面：
1. `gemini_client` 只剥离已闭合的思考块，未闭合的原样留在正文里；
2. `gemini_output.normalize_gemini_reply` 对未闭合思考块直接抛错阻断发送；
3. 历史消息只清洗 `AssistantMessageItem`，用户消息与工具回执保持原文。
"""

import base64
import io
import json
import time

import pytest
from google.genai.types import Candidate, Content, GenerateContentResponse, Part

from src.chat.replyer.gemini_output import GeminiReplyFormatError, normalize_gemini_reply
from src.llm_models.model_client.gemini_client import (
    _build_stream_api_response,
    _convert_messages,
    _default_normal_response_parser,
)
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextImagePart,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    ReasoningItem,
    SystemMessageItem,
    UserMessageItem,
)


def _make_response(text: str) -> GenerateContentResponse:
    """构造只含一个文本 Part 的 Gemini 响应。

    Args:
        text: 模型返回的正文文本。

    Returns:
        GenerateContentResponse: 可直接喂给解析器的响应对象。
    """
    return GenerateContentResponse(
        candidates=[
            Candidate(
                content=Content(
                    role="model",
                    parts=[Part.from_text(text=text)],
                )
            )
        ]
    )


def _assistant_text(api_resp) -> str:
    """从统一响应里取出助手消息正文。

    Args:
        api_resp: `_default_normal_response_parser` 或 `_build_stream_api_response` 的返回值。

    Returns:
        str: 助手消息正文文本。
    """
    items = [item for item in api_resp.output_items if isinstance(item, AssistantMessageItem)]
    assert len(items) == 1
    return items[0].parts[0].text


CLOSED_VARIANTS = (
    ("[:thought\n用户问几点睡，简短傲娇回答。\n:]少乱叫宝宝啦，看情况困了就睡", "少乱叫宝宝啦，看情况困了就睡"),
    ("[_thought\n构思：选14字回复\n]_这还差不多，勉强再陪你水两句喵", "这还差不多，勉强再陪你水两句喵"),
    ("⌊thought\n思考过程：调侃面包人当夜宵。⌋看着怪香的，抓一个抹点果酱吃", "看着怪香的，抓一个抹点果酱吃"),
    ("<thought>内部草稿</thought>正常回复内容", "正常回复内容"),
    ("⌊thought Plan a greeting.⌋Hello", "Hello"),
)


def test_normal_response_strips_closed_thought_variants() -> None:
    """非流式解析应剥离四种已闭合变体，含英文句号后闭合的 `⌋`。"""
    for raw_text, expected_body in CLOSED_VARIANTS:
        api_resp, _ = _default_normal_response_parser(
            _make_response(raw_text),
            logical_turn_id="turn-closed",
        )
        assert _assistant_text(api_resp) == expected_body


def test_stream_response_strips_closed_and_keeps_unclosed() -> None:
    """流式响应剥离已闭合块；未闭合块原样保留，交由 replyer 阻断。"""
    closed = "[:thought\n草稿\n:]干净回复"
    closed_resp = _build_stream_api_response(
        io.StringIO(closed),
        io.StringIO(""),
        [],
        None,
        "turn-stream-closed",
    )
    assert _assistant_text(closed_resp) == "干净回复"

    unclosed = "[:thought 还没写完的草稿"
    unclosed_resp = _build_stream_api_response(
        io.StringIO(unclosed),
        io.StringIO(""),
        [],
        None,
        "turn-stream-unclosed",
    )
    assert _assistant_text(unclosed_resp) == unclosed
    reasoning_items = [item for item in unclosed_resp.output_items if isinstance(item, ReasoningItem)]
    assert reasoning_items == []


def test_normalize_gemini_reply_strips_closed_and_blocks_unclosed() -> None:
    """发送前只剥离有明确闭合边界的块，未闭合一律抛错阻断。"""
    assert normalize_gemini_reply("[_thought 草稿 ]_正文") == "正文"
    assert normalize_gemini_reply("⌊thought Plan a greeting.⌋Hello") == "Hello"

    for unclosed in ("[:thought 草稿", "[_thought 草稿", "⌊thought 草稿"):
        with pytest.raises(GeminiReplyFormatError):
            normalize_gemini_reply(unclosed)


def test_normalize_gemini_reply_blocks_issue_2362_samples() -> None:
    """#2362 的 `⌊Thinking Process:` 缺少 `⌈` 分界时必须阻断发送。"""
    sample = (
        "⌊Thinking Process:\n\n"
        "1.  **Analyze the Request:**\n"
        "    *   Do not reuse inner monologue `⌊...⌉`.\n"
        "2.  **Drafting:**\n"
        '    *   "抬手按住被揉乱的发梢。" - fits well!'
    )
    with pytest.raises(GeminiReplyFormatError):
        normalize_gemini_reply(sample)


def test_normalize_gemini_reply_keeps_plain_monologue() -> None:
    """普通角色旁白 `⌊...⌉` 不是思考块，必须原样保留。"""
    monologue = "⌊既然你都发话了……⌉噗，连你平时都这么干，那我以后有好玩的吐槽可就光明正大顺手丢出来啦。"
    assert normalize_gemini_reply(monologue) == monologue


def test_convert_messages_only_scrubs_assistant_history() -> None:
    """只清洗模型历史；用户消息与工具回执里的字面量必须完整保留。"""
    call_id = "call-reply-1"
    literal = "<thought>示例</thought>"
    tool_output_json = json.dumps(
        {"success": True, "content": f"已发送回复{literal}", "other": "]_UNRELATED"},
        ensure_ascii=False,
    )
    contents, _ = _convert_messages(
        [
            SystemMessageItem(meta=ContextItemMeta.create(), parts=(ContextTextPart("system"),)),
            UserMessageItem(
                meta=ContextItemMeta.create(),
                parts=(ContextTextPart(f"{literal}普通群友手打的示例"),),
            ),
            AssistantMessageItem(
                meta=ContextItemMeta.create(),
                parts=(
                    ContextTextPart("[:thought\n跨分片草稿"),
                    ContextTextPart("第二段:]干净的历史回复"),
                ),
            ),
            FunctionCallItem(
                meta=ContextItemMeta.create(logical_turn_id="turn-1"),
                tool_call=ContextToolCall.create(call_id=call_id, func_name="reply"),
            ),
            FunctionCallOutputItem(
                meta=ContextItemMeta.create(logical_turn_id="turn-1"),
                call_id=call_id,
                output=tool_output_json,
                tool_name="reply",
            ),
        ]
    )

    user_text = contents[0].parts[0].text
    model_text = contents[1].parts[0].text
    func_resp = contents[2].parts[0].function_response.response

    assert user_text == f"{literal}普通群友手打的示例"
    assert model_text == "干净的历史回复"
    assert func_resp["content"] == f"已发送回复{literal}"
    assert func_resp["other"] == "]_UNRELATED"


# 嵌套 XML 草稿：只认第一个闭标签会把后半段草稿当正文放出去。
NESTED_XML = "<thought>OUTER<thought>INNER</thought>MORE_SECRET</thought>ANSWER"


def test_nested_xml_thought_matches_pre_send_check() -> None:
    """嵌套 `<thought>` 必须整体剥完，客户端与发送前检查都只留 `ANSWER`。"""
    assert normalize_gemini_reply(NESTED_XML) == "ANSWER"

    normal_resp, _ = _default_normal_response_parser(_make_response(NESTED_XML), logical_turn_id="turn-nested")
    assert _assistant_text(normal_resp) == "ANSWER"

    stream_resp = _build_stream_api_response(io.StringIO(NESTED_XML), io.StringIO(""), [], None, "turn-nested-stream")
    assert _assistant_text(stream_resp) == "ANSWER"


def test_thought_process_header_is_left_to_pre_send_check() -> None:
    """`⌊Thought Process:` 不是内联草稿，客户端不得抢先剥离，必须由发送前检查阻断。"""
    sample = "⌊Thought Process: SECRET⌋MORE_SECRET"
    with pytest.raises(GeminiReplyFormatError):
        normalize_gemini_reply(sample)

    normal_resp, _ = _default_normal_response_parser(_make_response(sample), logical_turn_id="turn-process")
    assert _assistant_text(normal_resp) == sample
    with pytest.raises(GeminiReplyFormatError):
        normalize_gemini_reply(_assistant_text(normal_resp))


@pytest.mark.parametrize("variant", ["< thought>SECRET", "<_thought>SECRET", "<-thought>SECRET"])
def test_spaced_and_prefixed_unclosed_xml_is_blocked(variant: str) -> None:
    """`< thought>` / `<_thought>` / `<-thought>` 的未闭合块同样必须阻断发送。

    Args:
        variant: 待验证的 XML 变体样本。
    """
    with pytest.raises(GeminiReplyFormatError):
        normalize_gemini_reply(variant)

    normal_resp, _ = _default_normal_response_parser(_make_response(variant), logical_turn_id="turn-variant")
    assert _assistant_text(normal_resp) == variant
    with pytest.raises(GeminiReplyFormatError):
        normalize_gemini_reply(_assistant_text(normal_resp))


def _fake_image_part() -> ContextImagePart:
    """构造一个占位图片片段，避免测试依赖真实图片文件。

    Returns:
        ContextImagePart: 仅含 16 字节占位数据的图片片段。
    """
    return ContextImagePart(
        image_format="png",
        image_base64=base64.b64encode(b"fake-image-bytes").decode(),
    )


def test_convert_messages_scrubs_thought_across_image_part() -> None:
    """模型历史的未闭合草稿跨图片累积，闭合后只留干净正文，图片位置不变。"""
    contents, _ = _convert_messages(
        [
            AssistantMessageItem(
                meta=ContextItemMeta.create(),
                parts=(
                    ContextTextPart("[:thought SECRET"),
                    _fake_image_part(),
                    ContextTextPart("MORE_SECRET:]ANSWER"),
                ),
            )
        ]
    )

    parts = contents[0].parts
    assert parts[0].inline_data is not None
    assert parts[0].inline_data.mime_type == "image/png"
    assert parts[1].text == "ANSWER"
    assert "SECRET" not in "".join(part.text or "" for part in parts)


def test_convert_messages_keeps_user_thought_across_image_part() -> None:
    """用户消息跨图片的草稿字面量必须完整保留，顺序不变。"""
    contents, _ = _convert_messages(
        [
            UserMessageItem(
                meta=ContextItemMeta.create(),
                parts=(
                    ContextTextPart("[:thought SECRET"),
                    _fake_image_part(),
                    ContextTextPart("MORE_SECRET:]ANSWER"),
                ),
            )
        ]
    )

    parts = contents[0].parts
    assert parts[0].text == "[:thought SECRET"
    assert parts[1].inline_data is not None
    assert parts[2].text == "MORE_SECRET:]ANSWER"


def test_convert_messages_keeps_unclosed_thought_at_item_end() -> None:
    """整条消息都无法闭合时不得重排图文，严格保持 `[文本, 图片]` 原始顺序。"""
    contents, _ = _convert_messages(
        [
            AssistantMessageItem(
                meta=ContextItemMeta.create(),
                parts=(ContextTextPart("[:thought SECRET"), _fake_image_part()),
            )
        ]
    )

    parts = contents[0].parts
    assert parts[0].text == "[:thought SECRET"
    assert parts[1].inline_data is not None


@pytest.mark.parametrize(
    "crossed",
    [
        "<thought><analysis>SECRET</thought>MORE_SECRET</analysis>ANSWER",
        "<thought><think>SECRET</thought>MORE_SECRET</think>ANSWER",
        "<analysis><thought>SECRET</analysis>MORE_SECRET</thought>ANSWER",
    ],
)
def test_crossed_xml_tags_are_left_to_pre_send_check(crossed: str) -> None:
    """交叉 XML 标签属于配对错误，客户端不得剥离开标签，必须原样交给发送前阻断。

    Args:
        crossed: 交叉配对错误的合成样本。
    """
    with pytest.raises(GeminiReplyFormatError):
        normalize_gemini_reply(crossed)

    normal_resp, _ = _default_normal_response_parser(_make_response(crossed), logical_turn_id="turn-crossed")
    assert _assistant_text(normal_resp) == crossed
    stream_resp = _build_stream_api_response(io.StringIO(crossed), io.StringIO(""), [], None, "turn-crossed-stream")
    assert _assistant_text(stream_resp) == crossed


@pytest.mark.parametrize(
    ("raw_text", "expected_body"),
    [
        ("[:thought:]Hello", "Hello"),
        ("[_thought]_Hi", "Hi"),
        ("⌊thought⌋Plain", "Plain"),
        ("<thought></thought>Pure", "Pure"),
    ],
)
def test_empty_closed_thought_block_is_stripped(raw_text: str, expected_body: str) -> None:
    """空思考块同样算已命中，正文取闭合符之后的部分。

    Args:
        raw_text: 空闭合块样本。
        expected_body: 期望得到的正文。
    """
    normal_resp, _ = _default_normal_response_parser(_make_response(raw_text), logical_turn_id="turn-empty")
    assert _assistant_text(normal_resp) == expected_body

    stream_resp = _build_stream_api_response(io.StringIO(raw_text), io.StringIO(""), [], None, "turn-empty-stream")
    assert _assistant_text(stream_resp) == expected_body


def test_closed_block_longer_than_32kb_is_still_stripped() -> None:
    """去掉长度硬限后，超长闭合思考块也必须能剥离。"""
    long_draft = "[:thought " + "铺" * 40000 + ":]正文还在"
    normal_resp, _ = _default_normal_response_parser(_make_response(long_draft), logical_turn_id="turn-long")
    assert _assistant_text(normal_resp) == "正文还在"


@pytest.mark.parametrize("prefix", ["[", "⌊", "【", "<"])
def test_open_label_regex_is_linear(prefix: str) -> None:
    """恶意长空白串不得触发二次方回溯（16 KB 需远低于 20 ms）。

    Args:
        prefix: 四种思考开标签的首字符。
    """
    payload = prefix + " " * 16000 + "x"
    started = time.perf_counter()
    _default_normal_response_parser(_make_response(payload), logical_turn_id="turn-redos")
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 20, f"耗时 {elapsed_ms:.2f} ms，疑似回溯"
