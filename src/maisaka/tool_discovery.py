"""Maisaka 工具发现：完整定义仅通过搜索结果追加到上下文。"""

from copy import deepcopy
from typing import Any, Dict, List, Sequence

from src.core.tooling import ToolSpec


TOOL_LOADED_INSTRUCTION = (
    "本次返回的工具定义已经加载，现在可直接按定义的名称和参数发起工具调用；"
    "无需等待顶层 tools 数组更新，也不要再次搜索已返回的工具。"
)


def build_tool_search_result(
    query: str,
    specs: Sequence[ToolSpec],
    newly_discovered_tool_names: Sequence[str],
) -> Dict[str, Any]:
    """冻结命中定义和加载状态；后续注册表变化不改写已写入的搜索结果。"""
    return {
        "query": query,
        "matched_tool_names": [spec.name for spec in specs],
        "newly_discovered_tool_names": list(newly_discovered_tool_names),
        "status": "loaded" if specs else "not_found",
        "instruction": TOOL_LOADED_INSTRUCTION if specs else "未找到匹配工具，请换用工具名或其他关键词重新搜索。",
        "tools": [
            {
                "name": spec.name,
                "description": spec.build_llm_description(),
                "parameters": deepcopy(spec.parameters_schema) if spec.parameters_schema is not None else {"type": "object", "properties": {}},
            }
            for spec in specs
        ],
    }


def search_tool_specs(specs: Sequence[ToolSpec], query: str, *, limit: int) -> List[ToolSpec]:
    """按工具名、前缀和描述排序，普通聊天和共享 Planner 使用相同的检索规则。"""
    normalized_query = " ".join(query.lower().split()).strip()
    if not normalized_query:
        return []
    query_terms = normalized_query.replace("_", " ").replace("-", " ").split()
    scored = []
    for spec in specs:
        name = spec.name.lower()
        description = spec.description.lower()
        score = 0
        if normalized_query == name:
            score += 1000
        if name.startswith(normalized_query):
            score += 300
        if normalized_query in name:
            score += 200
        if normalized_query in description:
            score += 100
        for term in query_terms:
            if term in name:
                score += 25
            if term in description:
                score += 10
        if score > 0:
            scored.append((score, spec.name, spec))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [spec for _, _, spec in scored[: max(1, limit)]]
