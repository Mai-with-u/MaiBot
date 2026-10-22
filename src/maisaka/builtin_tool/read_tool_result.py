"""read_tool_result 内置工具：取回被整理过的工具原始结果。"""

from typing import Any, List, Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.tool_result_store import StoredToolResult, tool_result_store

from .context import BuiltinToolRuntimeContext

READ_TOOL_RESULT_TOOL_NAME = "read_tool_result"
# 一次读取的默认长度与上限，保持在超长结果整理的门槛之内，避免读回来的内容又是一大段
READ_DEFAULT_MAX_CHARS = 2000
READ_MAX_CHARS_LIMIT = 2800
# 按关键词查找时，每处命中前后各带多少字的上下文，以及最多返回几处
KEYWORD_CONTEXT_CHARS = 250
KEYWORD_MAX_MATCHES = 5


def get_tool_spec() -> ToolSpec:
    """获取 read_tool_result 工具声明。"""

    return ToolSpec(
        name=READ_TOOL_RESULT_TOOL_NAME,
        description=(
            "取回某次工具调用保存的原始结果。联网工具预览或超长结果的整理结论会附有编号（如 r3）；"
            "当你需要其中没有的细节时，用这个编号分段读取原文，或用 keyword 直接查找相关片段。"
            "已有内容够用时不需要调用。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "result_id": {
                    "type": "string",
                    "description": "工具返回的原始结果编号，例如 r3。",
                },
                "keyword": {
                    "type": "string",
                    "description": "可选。只返回原文中包含该关键词的片段及其前后文；不填则按 offset 顺序读取。",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "可选。从原文第几个字开始读，默认 0；上一次读取会告诉你下一段的 offset。",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": READ_MAX_CHARS_LIMIT,
                    "description": f"可选。本次最多读取多少字，默认 {READ_DEFAULT_MAX_CHARS}。",
                },
            },
            "required": ["result_id"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_by_offset(stored_result: StoredToolResult, offset: int, max_chars: int) -> str:
    """按偏移顺序读取一段原文。"""

    total_chars = len(stored_result.content)
    start = min(max(offset, 0), total_chars)
    end = min(start + max_chars, total_chars)
    header = f"[{stored_result.result_id} · {stored_result.tool_name} 原始结果 · 共 {total_chars} 字 · 本段 {start}–{end}]"
    if start >= total_chars:
        return f"{header}\n（offset 已超出原文末尾，没有更多内容。）"
    remaining_chars = total_chars - end
    footer = (
        f"\n（后面还有 {remaining_chars} 字，用 offset={end} 继续读取。）" if remaining_chars > 0 else "\n（已到原文末尾。）"
    )
    return f"{header}\n{stored_result.content[start:end]}{footer}"


def _read_by_keyword(stored_result: StoredToolResult, keyword: str) -> str:
    """返回原文中包含关键词的片段。"""

    content = stored_result.content
    lowered_content = content.lower()
    lowered_keyword = keyword.lower()
    snippets: List[str] = []
    total_matches = 0
    search_from = 0
    last_snippet_end = -1
    while True:
        match_index = lowered_content.find(lowered_keyword, search_from)
        if match_index < 0:
            break
        total_matches += 1
        search_from = match_index + len(lowered_keyword)
        # 落在上一个片段范围内的命中不再单独成段
        if match_index < last_snippet_end or len(snippets) >= KEYWORD_MAX_MATCHES:
            continue
        start = max(match_index - KEYWORD_CONTEXT_CHARS, 0)
        end = min(match_index + len(keyword) + KEYWORD_CONTEXT_CHARS, len(content))
        last_snippet_end = end
        snippets.append(f"--- 位置 {start}–{end} ---\n{content[start:end]}")

    header = f"[{stored_result.result_id} · {stored_result.tool_name} 原始结果 · 共 {len(content)} 字 · 关键词“{keyword}”命中 {total_matches} 处]"
    if not snippets:
        return f"{header}\n（原文中没有这个关键词，可以换一个词，或不填 keyword 按 offset 顺序读取。）"
    footer = (
        f"\n（只显示了前 {len(snippets)} 个片段；可以用 offset 读取指定位置附近的原文。）"
        if total_matches > len(snippets)
        else ""
    )
    return f"{header}\n" + "\n".join(snippets) + footer


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 read_tool_result。"""

    del context
    result_id = str(invocation.arguments.get("result_id") or "").strip()
    if not result_id:
        return tool_ctx.build_failure_result(invocation.tool_name, "read_tool_result 需要提供 result_id。")

    stored_result = tool_result_store.get(str(tool_ctx.runtime.session_id), result_id)
    if stored_result is None:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"没有找到编号为 {result_id} 的原始结果：它可能已经过期。需要这部分内容时请重新调用原来的工具。",
        )

    keyword = str(invocation.arguments.get("keyword") or "").strip()
    if keyword:
        return tool_ctx.build_success_result(invocation.tool_name, _read_by_keyword(stored_result, keyword))

    offset = _coerce_int(invocation.arguments.get("offset"), 0)
    max_chars = min(max(_coerce_int(invocation.arguments.get("max_chars"), READ_DEFAULT_MAX_CHARS), 1), READ_MAX_CHARS_LIMIT)
    return tool_ctx.build_success_result(invocation.tool_name, _read_by_offset(stored_result, offset, max_chars))
