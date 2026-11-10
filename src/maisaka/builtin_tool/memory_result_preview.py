"""限制记忆工具首次返回的正文，原文沿用工具结果存储按需读取。"""

from src.maisaka.tool_result_store import tool_result_store
from src.services.memory_service import MemorySearchResult

MEMORY_RESULT_MAX_CHARS = 2800


def build_memory_result_preview(
    result: MemorySearchResult, content: str, *, session_id: str, limit: int,
) -> str:
    """保留各命中的短正文，并提供同会话内完整原文的读取编号。"""
    if len(content) <= MEMORY_RESULT_MAX_CHARS or result.to_dict().get("purpose", "memory") != "memory":
        return content

    result_id = tool_result_store.save(session_id, "query_memory", content)
    header = (
        f"以下为记忆片段预览，共 {len(result.hits[:limit])} 条；片段被截短，不代表完整事实。\n"
        f'完整原文已保存为 {result_id}，需要细节时调用 read_tool_result(result_id="{result_id}")，'
        "可加 keyword 查找或用 offset 分段读取。\n"
    )
    budget = MEMORY_RESULT_MAX_CHARS - len(header)
    count = len(result.hits[:limit])
    if count:
        # 给每条命中分配正文预算，避免首条长记忆挤掉其余命中。
        per_hit = max(1, budget // count - 16)
        preview = result.to_text(limit=limit, max_content_chars=per_hit)
    else:
        preview = content
    return header + preview[:budget]
