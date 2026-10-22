"""基于 LLM 的回复断句。"""

from typing import List, Tuple

import json

from src.common.logger import get_logger
from src.services.llm_service import LLMServiceClient

logger = get_logger("llm_sentence_splitter")

_NO_SPLIT_MARKER = "no_split"


_SPLITTER_PROMPT = """
请不要思考，快速地把下面的回复进行断句，按自然语义拆成若干条适合逐条发送的消息。
要求：
1. 只能返回 JSON 字符串数组，例如 ["第一句。", "第二句？"]，不要 Markdown、解释或代码块。
2. 只能在原文已有字符之间选择切分位置，不能改写文本、增删或调整顺序。
3. 不要把括号、引号、颜文字或代码从中间拆开。如果是单独的句子，不要在在结尾留逗号。
4. 如果是完整代码，公式，或者整理阅读更合适的文段，不需要拆分，返回"no_split"。
5. 如果是本身比较短，或者表意清晰的句子，不需要拆分，返回"no_split"。

原文：
"""


def _normalize_json_payload(payload: str) -> str:
    """去除模型偶尔添加的 Markdown JSON 代码块包装。"""
    normalized = payload.strip()
    if normalized.startswith("```json") and normalized.endswith("```"):
        return normalized[7:-3].strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        return normalized[3:-3].strip()
    return normalized


def _is_no_split_marker(payload: str) -> bool:
    """判断模型返回的是否为「不拆分」标记，兼容是否被写成 JSON 字符串。"""
    return payload.strip().strip('"').strip() == _NO_SPLIT_MARKER


_PUNCTUATION_TABLE = {
    **{code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)},  # 全角 ASCII（含，。！？～等）
    0x3000: 0x20,
    0x3002: ord("."),
    0x3001: ord(","),
    0x2018: ord("'"),
    0x2019: ord("'"),
    0x201C: ord('"'),
    0x201D: ord('"'),
    0x3010: ord("["),
    0x3011: ord("]"),
    0x300A: ord("<"),
    0x300B: ord(">"),
}


def _strip_whitespace(text: str) -> str:
    """去除全部空白字符，并把中英文标点按一一对应归一，用于宽松比较。"""
    return "".join(text.split()).translate(_PUNCTUATION_TABLE)


def _map_sentences_to_original(text: str, sentences: List[str]) -> List[str]:
    """按各句的非空白字符数，把切分位置映射回原文，丢弃句间空白。"""
    counts = [len(_strip_whitespace(sentence)) for sentence in sentences]
    counts = [count for count in counts if count > 0]
    result: List[str] = []
    start = 0
    consumed = 0
    target = 0
    for index, count in enumerate(counts):
        target += count
        if index == len(counts) - 1:
            result.append(text[start:])
            break
        end = start
        while consumed < target:
            if not text[end].isspace():
                consumed += 1
            end += 1
        sentence_end = end
        while end < len(text) and text[end].isspace():
            end += 1
        # 段尾逗号后的空白要保留，供分隔符还原；其他句间空白在逐条发送时无意义，直接丢弃。
        result.append(text[start : end if text[sentence_end - 1] in "，," else sentence_end])
        start = end
    return result


def _extract_trailing_comma(sentence: str) -> Tuple[str, str]:
    """将段尾逗号移入分隔符，逐条发送时省略，压缩合并时恢复。"""
    content = sentence.rstrip("，, \t\r\n")
    separator = sentence[len(content) :]
    if content.strip() and ("，" in separator or "," in separator):
        return content, separator
    return sentence, ""


async def split_text_with_llm(text: str) -> List[Tuple[str, str]]:
    """调用 LLM 断句，并返回兼容规则断句器的句子元组。"""
    if not text:
        return []

    client = LLMServiceClient(task_name="fast_model", request_type="response.splitter")
    result = await client.generate_response(_SPLITTER_PROMPT + text)
    payload = _normalize_json_payload(result.response)

    # 模型判断不需要拆分时只返回标记，此时作为单独一段，并同样处理段尾逗号
    if _is_no_split_marker(payload):
        return [_extract_trailing_comma(text)]

    try:
        sentences = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM 断句结果不是合法 JSON 数组") from exc

    if isinstance(sentences, str):
        raise ValueError("LLM 断句结果不是合法 JSON 数组")

    if not isinstance(sentences, list) or not sentences or not all(isinstance(item, str) for item in sentences):
        raise ValueError("LLM 断句结果必须是非空字符串数组")
    if sentences == [_NO_SPLIT_MARKER]:
        return [_extract_trailing_comma(text)]

    # 模型常自行增删空格（如中英文间补空格），校验时忽略全部空白，仅要求其余字符完整保留。
    comparison_text = _strip_whitespace(text)
    reconstructed_text = _strip_whitespace("".join(sentences))
    if reconstructed_text != comparison_text:
        # 显示首处差异；repr 让标点差异可见。
        mismatch_index = next(
            (
                index
                for index, (original, returned) in enumerate(zip(comparison_text, reconstructed_text, strict=False))
                if original != returned
            ),
            min(len(comparison_text), len(reconstructed_text)),
        )
        context_start = max(0, mismatch_index - 20)
        context_end = mismatch_index + 21
        raise ValueError(
            "LLM 断句结果未完整保留原文（忽略空白后）："
            f"首个差异位置={mismatch_index}（从 0 开始），"
            f"原文长度={len(comparison_text)}，结果长度={len(reconstructed_text)}，"
            f"原文片段={comparison_text[context_start:context_end]!r}，"
            f"结果片段={reconstructed_text[context_start:context_end]!r}"
        )

    # 按切分点还原为原文片段，避免模型改动的空白进入最终消息；再分离段尾逗号。
    original_sentences = _map_sentences_to_original(text, sentences)
    return [_extract_trailing_comma(sentence) for sentence in original_sentences]
