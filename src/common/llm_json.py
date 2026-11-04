"""解析模型返回的 JSON，统一处理 Markdown 代码块包装。"""

from typing import Any, Type, TypeVar

from json_repair import repair_json
from pydantic import BaseModel

import json
import re

_Model = TypeVar("_Model", bound=BaseModel)
_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n(.*?)\r?\n```[ \t]*\Z", re.IGNORECASE | re.DOTALL)


def normalize_llm_json(response: str) -> str:
    """移除完整的 JSON 代码块外壳；不完整的输出保留给解析器报错。"""
    text = response.strip()
    match = _FENCE.fullmatch(text)
    return match.group(1).strip() if match else text


def parse_llm_json(response: str, *, repair: bool = False, allow_code_fence: bool = True) -> Any:
    """解析模型 JSON；可显式启用修复，或禁用代码块兼容以保持严格格式。"""
    text = normalize_llm_json(response) if allow_code_fence else response
    if repair:
        return repair_json(text, return_objects=True)
    return json.loads(text)


def validate_llm_json(response: str, model: Type[_Model]) -> _Model:
    """解析并使用 Pydantic 校验模型输出，保留其原始验证错误。"""
    return model.model_validate_json(normalize_llm_json(response))
