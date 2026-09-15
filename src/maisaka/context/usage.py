"""MaiSaka 请求上下文用量统计。

按提示词分段测量文本占用，供监控面板展示上下文构成。这里只统计可测量的字符数与条目数：
真正的 token 总量由模型返回，前端按各分段字符占比换算出分段 token，避免在本地引入第二套
tokenizer 造成与模型口径不一致。

图片分段以 base64 形式进入请求，其字符数不代表模型侧 token 开销，因此不计入字符统计。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence
import json

from src.llm_models.payload_content.context_item import (
    ContextItem,
    FunctionCallItem,
    SystemMessageItem,
    get_item_text,
)
from src.llm_models.payload_content.tool_option import normalize_tool_options

SECTION_SYSTEM_PROMPT = "system_prompt"
SECTION_MESSAGES = "messages"
SECTION_INSTANT_NOTICE = "instant_notice"
SECTION_MCP_TOOLS = "mcp_tools"
SECTION_BUILTIN_TOOLS = "builtin_tools"
SECTION_PLUGIN_TOOLS = "plugin_tools"
SECTION_OTHER_TOOLS = "other_tools"

# 分段展示顺序，与前端面板的行顺序保持一致
SECTION_ORDER: tuple[str, ...] = (
    SECTION_SYSTEM_PROMPT,
    SECTION_MESSAGES,
    SECTION_INSTANT_NOTICE,
    SECTION_MCP_TOOLS,
    SECTION_BUILTIN_TOOLS,
    SECTION_PLUGIN_TOOLS,
    SECTION_OTHER_TOOLS,
)

_TOOL_SECTION_BY_PROVIDER_TYPE: Dict[str, str] = {
    "builtin": SECTION_BUILTIN_TOOLS,
    "mcp": SECTION_MCP_TOOLS,
    "plugin": SECTION_PLUGIN_TOOLS,
}


@dataclass(frozen=True, slots=True)
class ContextSectionUsage:
    """单个提示词分段的字符占用。"""

    key: str
    chars: int
    count: int


def resolve_tool_definition_name(definition: Any) -> str:
    """读取工具定义名称，兼容内部定义与 OpenAI function 结构。"""

    tool_options = normalize_tool_options([definition])
    if not tool_options:
        return ""
    return tool_options[0].name


def measure_tool_definition_chars(definition: Any) -> int:
    """测量单个工具定义实际发送给模型的字符数。"""

    tool_options = normalize_tool_options([definition])
    if not tool_options:
        return 0
    schema = tool_options[0].to_openai_function_schema()
    return len(json.dumps(schema, ensure_ascii=False))


def measure_item_chars(item: ContextItem) -> int:
    """测量单个上下文 Item 的可见文本字符数。"""

    chars = len(get_item_text(item))
    if isinstance(item, FunctionCallItem):
        # 工具调用参数同样会进入请求，get_item_text 不覆盖这部分内容
        chars += len(item.tool_call.func_name) + len(item.tool_call.args_json.decode("utf-8"))
    return chars


def _accumulate(sections: Dict[str, List[int]], key: str, chars: int, count: int) -> None:
    """累加某个分段的字符数与条目数。"""

    if chars <= 0 and count <= 0:
        return
    entry = sections.setdefault(key, [0, 0])
    entry[0] += chars
    entry[1] += count


def measure_request_sections(
    items: Sequence[ContextItem],
    *,
    history_item_count: int,
    tool_definitions: Sequence[Any] = (),
    tool_provider_types: Sequence[str] = (),
) -> List[ContextSectionUsage]:
    """测量一次模型请求的提示词分段占用。

    Args:
        items: 最终发送给模型的 Context Items（系统提示词位于首位）。
        history_item_count: 紧随系统提示词之后属于历史上下文的消息条数。
        tool_definitions: 本轮候选工具定义。
        tool_provider_types: 与 ``tool_definitions`` 一一对应的工具来源类型。

    Returns:
        List[ContextSectionUsage]: 按 ``SECTION_ORDER`` 排列且仅包含非空分段的用量列表。
    """

    sections: Dict[str, List[int]] = {}

    system_item_count = 1 if items and isinstance(items[0], SystemMessageItem) else 0
    for item in items[:system_item_count]:
        _accumulate(sections, SECTION_SYSTEM_PROMPT, measure_item_chars(item), 1)

    # 历史条数以构建时的边界为准；hook 改写 items 后按实际长度收敛，避免越界分类
    available_history_count = max(len(items) - system_item_count, 0)
    resolved_history_count = min(max(history_item_count, 0), available_history_count)
    history_items = items[system_item_count : system_item_count + resolved_history_count]
    for item in history_items:
        _accumulate(sections, SECTION_MESSAGES, measure_item_chars(item), 1)

    for item in items[system_item_count + resolved_history_count :]:
        _accumulate(sections, SECTION_INSTANT_NOTICE, measure_item_chars(item), 1)

    for index, definition in enumerate(tool_definitions):
        provider_type = tool_provider_types[index] if index < len(tool_provider_types) else ""
        section_key = _TOOL_SECTION_BY_PROVIDER_TYPE.get(provider_type, SECTION_OTHER_TOOLS)
        _accumulate(sections, section_key, measure_tool_definition_chars(definition), 1)

    return [
        ContextSectionUsage(key=key, chars=chars, count=count)
        for key in SECTION_ORDER
        for chars, count in (sections.get(key, [0, 0]),)
        if chars > 0 or count > 0
    ]
