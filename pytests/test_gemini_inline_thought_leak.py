"""Gemini 客户端内联思考草稿泄漏剥离与上下文清洗回归测试（#2362）。"""

import io
import json

from google.genai.types import Candidate, Content, GenerateContentResponse, Part

from src.llm_models.model_client.gemini_client import (
    _build_stream_api_response,
    _convert_messages,
    _default_normal_response_parser,
)
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
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


def test_normal_response_strips_closed_thought_variants() -> None:
    samples = [
        (
            "[:thought\n用户问几点睡，简短傲娇回答。\n:]少乱叫宝宝啦，看情况困了就睡",
            "少乱叫宝宝啦，看情况困了就睡",
        ),
        (
            "[_thought\n构思：选14字回复\n]_这还差不多，勉强再陪你水两句喵",
            "这还差不多，勉强再陪你水两句喵",
        ),
        (
            "⌊thought\n思考过程：调侃面包人当夜宵。⌋看着怪香的，抓一个抹点果酱吃",
            "看着怪香的，抓一个抹点果酱吃",
        ),
        (
            "<thought>内部草稿</thought>正常回复内容",
            "正常回复内容",
        ),
    ]
    for raw_text, expected_body in samples:
        api_resp, _ = _default_normal_response_parser(
            _make_response(raw_text),
            logical_turn_id="turn-closed",
        )
        reasoning_items = [item for item in api_resp.output_items if isinstance(item, ReasoningItem)]
        assistant_items = [item for item in api_resp.output_items if isinstance(item, AssistantMessageItem)]
        assert len(reasoning_items) == 1
        assert len(assistant_items) == 1
        assert assistant_items[0].parts[0].text == expected_body


def test_issue_2362_floor_thinking_process_samples() -> None:
    case_01 = (
        "⌊Thinking Process:\n\n"
        "1.  **Analyze the Request:**\n"
        "    *   Do not reuse inner monologue `⌊...⌉`. Short, casual, spoken style.\n"
        "2.  **Drafting:**\n"
        '    *   "抬手按住被揉乱的发梢。喂喂，手下留情呀，再这么搓下去我这头卷发可都要起静电炸毛啦。" '
        "(42 chars) - fits well!"
        "抬手按住被揉乱的发梢。喂喂，手下留情呀，再这么搓下去我这头卷发可都要起静电炸毛啦。"
    )
    case_02 = (
        "⌊Thinking Process:\n\n"
        "1. **Analyze the input:**\n"
        "   - Guidance: Short, casual, spoken style.\n"
        "3. **Check constraints:**\n"
        "   - No `⌊...⌉`, no `<reject>`, no markdown, natural spoken tone, under 50 words."
        "诶，怎么还往下转移阵地了，我的脸又不是面团，再捏下去可要收你点心费咯。"
    )

    resp_01, _ = _default_normal_response_parser(_make_response(case_01), logical_turn_id="turn-2362-1")
    assistant_01 = [item for item in resp_01.output_items if isinstance(item, AssistantMessageItem)]
    assert len(assistant_01) == 1
    assert (
        assistant_01[0].parts[0].text
        == "抬手按住被揉乱的发梢。喂喂，手下留情呀，再这么搓下去我这头卷发可都要起静电炸毛啦。"
    )

    resp_02, _ = _default_normal_response_parser(_make_response(case_02), logical_turn_id="turn-2362-2")
    assistant_02 = [item for item in resp_02.output_items if isinstance(item, AssistantMessageItem)]
    assert len(assistant_02) == 1
    assert assistant_02[0].parts[0].text == "诶，怎么还往下转移阵地了，我的脸又不是面团，再捏下去可要收你点心费咯。"


def test_stream_response_preserves_normal_monologue_and_strips_unclosed_tail() -> None:
    normal_monologue = "⌊既然你都发话了……⌉噗，连你平时都这么干，那我以后有好玩的吐槽可就光明正大顺手丢出来啦。"
    normal_resp = _build_stream_api_response(
        io.StringIO(normal_monologue),
        io.StringIO(""),
        [],
        None,
        "turn-normal",
    )
    assistant_items = [item for item in normal_resp.output_items if isinstance(item, AssistantMessageItem)]
    assert len(assistant_items) == 1
    assert assistant_items[0].parts[0].text == normal_monologue

    unclosed_with_transition = (
        "[:thought\nThinking Process:\nDrafting reply...\nLet's go with this!少起哄啦，刚才那是输入法抽风喵"
    )
    leaked_resp = _build_stream_api_response(
        io.StringIO(unclosed_with_transition),
        io.StringIO(""),
        [],
        None,
        "turn-unclosed",
    )
    leaked_assistant = [item for item in leaked_resp.output_items if isinstance(item, AssistantMessageItem)]
    leaked_reasoning = [item for item in leaked_resp.output_items if isinstance(item, ReasoningItem)]
    assert len(leaked_reasoning) == 1
    assert len(leaked_assistant) == 1
    assert leaked_assistant[0].parts[0].text == "少起哄啦，刚才那是输入法抽风喵"


def test_convert_messages_scrubs_history_across_assistant_user_and_tool_json() -> None:
    call_id = "call-reply-1"
    tool_output_json = json.dumps(
        {
            "success": True,
            "content": '已发送回复"[_thought\n内部草稿\n]_谁是ai啦，我可是正经猫娘群友"',
            "other": "]_UNRELATED",
        },
        ensure_ascii=False,
    )
    contents, _ = _convert_messages(
        [
            SystemMessageItem(meta=ContextItemMeta.create(), parts=(ContextTextPart("system"),)),
            UserMessageItem(
                meta=ContextItemMeta.create(),
                parts=(
                    ContextTextPart(
                        '<message msg_id="1" is_self_message="true">\n'
                        "[_thought\n内部草稿\n]_谁是ai啦，我可是正经猫娘群友"
                    ),
                ),
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

    assert "_thought" not in user_text
    assert "谁是ai啦，我可是正经猫娘群友" in user_text
    assert model_text == "干净的历史回复"
    assert func_resp["success"] is True
    assert func_resp["content"] == '已发送回复"谁是ai啦，我可是正经猫娘群友"'
    assert func_resp["other"] == "]_UNRELATED"
