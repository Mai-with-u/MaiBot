# -*- coding: utf-8 -*-
"""
记忆系统工具函数
包含模糊查找、相似度计算等工具函数
"""

import json
import re
from datetime import datetime
from typing import Tuple
from typing import List
from json_repair import repair_json

from src.common.logger import get_logger


logger = get_logger("memory_utils")


def parse_questions_json(response: str) -> Tuple[List[str], List[str]]:
    """解析问题JSON，返回概念列表和问题列表

    Args:
        response: LLM返回的响应

    Returns:
        Tuple[List[str], List[str]]: (概念列表, 问题列表)
    """
    try:
        # 尝试提取JSON（可能包含在```json代码块中）
        json_pattern = r"```json\s*(.*?)\s*```"
        matches = re.findall(json_pattern, response, re.DOTALL)

        if matches:
            json_str = matches[0]
        else:
            # 尝试直接解析整个响应
            json_str = response.strip()

        # 修复可能的JSON错误
        repaired_json = repair_json(json_str)

        # 解析JSON
        parsed = json.loads(repaired_json)

        # 只支持新格式：包含concepts和questions的对象
        if not isinstance(parsed, dict):
            logger.warning(f"解析的JSON不是对象格式: {parsed}")
            return [], []

        concepts_raw = parsed.get("concepts", [])
        questions_raw = parsed.get("questions", [])

        # 确保是列表
        if not isinstance(concepts_raw, list):
            concepts_raw = []
        if not isinstance(questions_raw, list):
            questions_raw = []

        # 确保所有元素都是字符串
        concepts = [c for c in concepts_raw if isinstance(c, str) and c.strip()]
        questions = [q for q in questions_raw if isinstance(q, str) and q.strip()]

        return concepts, questions

    except Exception as e:
        logger.error(f"解析问题JSON失败: {e}, 响应内容: {response[:200]}...")
        return [], []


def parse_datetime_to_timestamp(value: str) -> float:
    """
    接受多种常见格式并转换为时间戳（秒）
    支持示例：
    - 2025-09-29
    - 2025-09-29 00:00:00
    - 2025/09/29 00:00
    - 2025-09-29T00:00:00
    """
    value = value.strip()
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ]
    last_err = None
    for fmt in fmts:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.timestamp()
        except Exception as e:
            last_err = e
    raise ValueError(f"无法解析时间: {value} ({last_err})")
