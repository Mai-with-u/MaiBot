from typing import Any, Dict, List, Optional, Tuple

from json_repair import repair_json

import json
import re

from src.common.logger import get_logger
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

from .expression_style_utils import normalize_expression_style_for_learning

logger = get_logger("expression_utils")

judge_llm = LLMServiceClient(task_name="learner", request_type="expression.check")


def _normalize_repair_json_result(repaired_result: Any) -> str:
    """将 `repair_json` 的返回结果统一转换为字符串。"""
    if isinstance(repaired_result, str):
        return repaired_result
    if isinstance(repaired_result, tuple) and repaired_result:
        first_item = repaired_result[0]
        if isinstance(first_item, str):
            return first_item
        return json.dumps(first_item, ensure_ascii=False)
    raise TypeError(f"repair_json 返回了无法处理的结果类型: {type(repaired_result)}")


def _strip_markdown_code_fence(text: str) -> str:
    """移除 LLM 可能附带的 Markdown 代码块包裹。"""
    raw = text.strip()
    if match := re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL):
        return match[1].strip()
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    return raw.strip()


def _extract_json_object_candidate(text: str) -> str:
    """尽量从文本中提取首个 JSON 对象片段。"""
    start_index = text.find("{")
    end_index = text.rfind("}")
    if start_index != -1 and end_index != -1 and start_index < end_index:
        return text[start_index : end_index + 1].strip()
    return text.strip()


def _extract_reason_from_text(text: str) -> Optional[str]:
    """从格式不完整的 JSON 文本中兜底提取 reason 字段。"""
    reason_key_match = re.search(r'["“”]?reason["“”]?\s*:\s*', text, re.IGNORECASE)
    if reason_key_match is None:
        return None

    value_text = text[reason_key_match.end() :].strip()
    if not value_text:
        return None

    if value_text.endswith("}"):
        value_text = value_text[:-1].rstrip()
    if value_text.endswith(","):
        value_text = value_text[:-1].rstrip()
    if not value_text:
        return None

    if value_text[0] in {'"', "'", "“", "”", "‘", "’"}:
        value_text = value_text[1:]
        while value_text and value_text[-1] in {'"', "'", "“", "”", "‘", "’"}:
            value_text = value_text[:-1].rstrip()

    return value_text.strip() or None


def _normalize_reason_text(reason: Any) -> str:
    """清理解析后 reason 中残留的包裹引号。"""
    normalized_reason = str(reason).strip()

    if len(normalized_reason) >= 2 and normalized_reason[0] == normalized_reason[-1]:
        if normalized_reason[0] in {'"', "'", "“", "”", "‘", "’"}:
            normalized_reason = normalized_reason[1:-1].strip()

    if normalized_reason.endswith('"') and normalized_reason.count('"') % 2 == 1:
        normalized_reason = normalized_reason[:-1].rstrip()
    if normalized_reason.endswith("'") and normalized_reason.count("'") % 2 == 1:
        normalized_reason = normalized_reason[:-1].rstrip()
    if normalized_reason.endswith('"') and not normalized_reason.startswith('"'):
        normalized_reason = normalized_reason[:-1].rstrip()
    if normalized_reason.endswith("'") and not normalized_reason.startswith("'"):
        normalized_reason = normalized_reason[:-1].rstrip()

    return normalized_reason


def parse_evaluation_response(response: str) -> Dict[str, Any]:
    """解析表达方式评估结果，兼容不完全合法的 JSON。"""
    raw = _strip_markdown_code_fence(response)
    if not raw:
        raise ValueError("LLM 响应为空")

    parse_candidates = [raw]
    json_candidate = _extract_json_object_candidate(raw)
    if json_candidate and json_candidate not in parse_candidates:
        parse_candidates.append(json_candidate)

    for candidate in parse_candidates:
        parsed = _try_parse(candidate)
        if isinstance(parsed, dict):
            if "reason" in parsed:
                parsed["reason"] = _normalize_reason_text(parsed["reason"])
            return parsed

        fixed_candidate = fix_chinese_quotes_in_json(candidate)
        if fixed_candidate != candidate:
            parsed = _try_parse(fixed_candidate)
            if isinstance(parsed, dict):
                if "reason" in parsed:
                    parsed["reason"] = _normalize_reason_text(parsed["reason"])
                return parsed

    suitable_match = re.search(r'["“”]?suitable["“”]?\s*:\s*(true|false)', raw, re.IGNORECASE)
    reason = _extract_reason_from_text(json_candidate or raw)
    if suitable_match is None or reason is None:
        raise ValueError(f"无法解析 LLM 响应为评估结果 JSON: {response}")

    return {
        "suitable": suitable_match.group(1).lower() == "true",
        "reason": _normalize_reason_text(reason),
    }


async def check_expression_suitability(
    situation: str,
    style: str,
    *,
    session_id: str = "",
) -> Tuple[bool, str, Optional[str]]:
    """
    执行单次 LLM 评估。

    Args:
        situation: 情景
        style: 风格

    Returns:
        (suitable, reason, error) 元组，如果出错则 suitable 为 False，error 包含错误信息
    """
    base_criteria = [
        "表达方式或言语风格是否与使用条件或使用情景匹配",
        "允许部分语法错误或口语化或缺省出现",
        "表达方式不能太过特指，需要具有泛用性",
        "一般不涉及具体的人名或名称",
    ]

    criteria_list = "\n".join([f"{i + 1}. {criterion}" for i, criterion in enumerate(base_criteria)])

    prompt_template = prompt_manager.get_prompt("expression_evaluation")
    prompt_template.add_context("situation", situation)
    prompt_template.add_context("style", style)
    prompt_template.add_context("criteria_list", criteria_list)

    prompt = await prompt_manager.render_prompt(prompt_template)

    logger.debug(f"优化表达 场景={situation!r} 表达={style!r}")

    generation_result = await judge_llm.generate_response(
        prompt=prompt,
        session_id=session_id,
    )
    response = generation_result.response

    logger.debug(f"评估结果: {response}")

    try:
        evaluation = parse_evaluation_response(response)
    except Exception as e:
        return False, f"评估表达方式时发生错误: {e}", str(e)

    try:
        suitable = bool(evaluation.get("suitable", False))
        reason = _normalize_reason_text(evaluation.get("reason", "未提供理由"))
        logger.debug(f"评估结果: {'通过' if suitable else '不通过'}")
        return suitable, reason, None
    except Exception as e:
        return False, f"评估结果格式错误: {e}", str(e)


def fix_chinese_quotes_in_json(text: str) -> str:
    """使用状态机修复 JSON 字符串值中的中文引号。"""
    result: List[str] = []
    in_string = False
    escape_next = False

    for char in text:
        if escape_next:
            result.append(char)
            escape_next = False
            continue

        if char == "\\":
            result.append(char)
            escape_next = True
            continue

        if char == '"':
            in_string = not in_string
            result.append(char)
            continue

        if in_string and char in ["“", "”"]:
            result.append('\\"')
            continue

        result.append(char)

    return "".join(result)


def parse_expression_response(response: str) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str]]]:
    """
    解析 LLM 返回的表达方式总结和黑话 JSON，提取两个列表。

    Returns:
        第一个列表是表达方式 (situation, style, source_id)
        第二个列表是黑话 (content, source_id)
    """
    if not response:
        return [], []

    raw = _strip_markdown_code_fence(response)

    parsed = _try_parse(raw)
    if parsed is None:
        fixed = fix_chinese_quotes_in_json(raw)
        parsed = _try_parse(fixed)
    if parsed is None:
        logger.error(f"处理后的 JSON 字符串（前500字符）：{raw[:500]}")
        return [], []

    if isinstance(parsed, dict):
        parsed_list = [parsed]
    elif isinstance(parsed, list):
        parsed_list = parsed
    else:
        logger.error(f"表达风格解析结果类型异常: {type(parsed)}, 内容: {parsed}")
        return [], []

    expressions: List[Tuple[str, str, str]] = []
    jargon_entries: List[Tuple[str, str]] = []

    for item in parsed_list:
        if not isinstance(item, dict):
            continue

        situation = str(item.get("situation", "")).strip()
        style = normalize_expression_style_for_learning(str(item.get("style", "")).strip())
        source_id = str(item.get("source_id", "")).strip()

        if situation and style and source_id:
            expressions.append((situation, style, source_id))
            continue

        content = str(item.get("content", "")).strip()
        if content and source_id:
            jargon_entries.append((content, source_id))

    return expressions, jargon_entries


def parse_jargon_response(response: str) -> List[Tuple[str, str]]:
    """解析独立黑话学习 LLM 响应，提取 (content, source_id) 列表。"""

    _, jargon_entries = parse_expression_response(response)
    return jargon_entries


def is_single_char_jargon(content: str) -> bool:
    """判断是否是单字黑话（单个汉字、英文或数字）。"""
    if not content or len(content) != 1:
        return False

    char = content[0]
    return (
        "\u4e00" <= char <= "\u9fff"
        or "a" <= char <= "z"
        or "A" <= char <= "Z"
        or "0" <= char <= "9"
    )


def _try_parse(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        try:
            repaired = _normalize_repair_json_result(repair_json(text))
            return json.loads(repaired)
        except Exception:
            return None
