"""MaiSaka 上下文分段用量统计测试。"""

from typing import List

from src.llm_models.payload_content.context_item import (
    ContextItem,
    ContextItemBuilder,
    ContextItemMeta,
    ContextToolCall,
    FunctionCallItem,
    RoleType,
)
from src.maisaka.context.usage import (
    SECTION_BUILTIN_TOOLS,
    SECTION_INSTANT_NOTICE,
    SECTION_MCP_TOOLS,
    SECTION_MESSAGES,
    SECTION_OTHER_TOOLS,
    SECTION_PLUGIN_TOOLS,
    SECTION_SYSTEM_PROMPT,
    measure_request_sections,
)


def _build_item(role: RoleType, text: str) -> ContextItem:
    """构造一条指定角色的文本 Item。"""

    return ContextItemBuilder().set_role(role).add_text_content(text).build()


def _build_request_items(system_prompt: str, history_texts: List[str], notice_texts: List[str]) -> List[ContextItem]:
    """按系统提示词、历史消息、即时提示的顺序构造请求 Item 列表。"""

    items: List[ContextItem] = [_build_item(RoleType.System, system_prompt)]
    items.extend(_build_item(RoleType.User, text) for text in history_texts)
    items.extend(_build_item(RoleType.User, text) for text in notice_texts)
    return items


def _section_map(sections) -> dict[str, tuple[int, int]]:
    """把分段列表转换为 key -> (chars, count) 便于断言。"""

    return {section.key: (section.chars, section.count) for section in sections}


def test_message_sections_follow_history_boundary() -> None:
    items = _build_request_items(
        system_prompt="系统提示词",
        history_texts=["历史一", "历史二"],
        notice_texts=["当前时间"],
    )

    sections = measure_request_sections(items, history_item_count=2)

    assert [section.key for section in sections] == [
        SECTION_SYSTEM_PROMPT,
        SECTION_MESSAGES,
        SECTION_INSTANT_NOTICE,
    ]
    assert _section_map(sections) == {
        SECTION_SYSTEM_PROMPT: (5, 1),
        SECTION_MESSAGES: (6, 2),
        SECTION_INSTANT_NOTICE: (4, 1),
    }


def test_history_boundary_is_clamped_to_available_items() -> None:
    items = _build_request_items(system_prompt="系统", history_texts=["历史一"], notice_texts=["时间"])

    # hook 可能改写 items 数量；越界的历史条数按实际长度收敛，剩余条目全部并入历史分段
    sections = measure_request_sections(items, history_item_count=10)

    assert _section_map(sections) == {
        SECTION_SYSTEM_PROMPT: (2, 1),
        SECTION_MESSAGES: (5, 2),
    }


def test_tool_definitions_are_grouped_by_provider_type() -> None:
    items = _build_request_items(system_prompt="系统", history_texts=[], notice_texts=[])
    definitions = [
        {"name": "query_memory", "description": "查询记忆", "parameters_schema": {"type": "object"}},
        {"name": "mcp_search", "description": "MCP 搜索", "parameters_schema": {"type": "object"}},
        {"name": "plugin_echo", "description": "插件回声", "parameters_schema": {"type": "object"}},
        {"name": "unknown_tool", "description": "来源未知", "parameters_schema": {"type": "object"}},
    ]

    sections = measure_request_sections(
        items,
        history_item_count=0,
        tool_definitions=definitions,
        tool_provider_types=["builtin", "mcp", "plugin", ""],
    )

    section_map = _section_map(sections)
    assert list(section_map) == [
        SECTION_SYSTEM_PROMPT,
        SECTION_MCP_TOOLS,
        SECTION_BUILTIN_TOOLS,
        SECTION_PLUGIN_TOOLS,
        SECTION_OTHER_TOOLS,
    ]
    for key in (SECTION_MCP_TOOLS, SECTION_BUILTIN_TOOLS, SECTION_PLUGIN_TOOLS, SECTION_OTHER_TOOLS):
        chars, count = section_map[key]
        assert chars > 0
        assert count == 1


def test_tool_call_arguments_are_counted_for_function_call_items() -> None:
    tool_call = ContextToolCall(
        call_id="call-1",
        func_name="query_memory",
        args_json=b'{"query": "MaiBot"}',
    )
    items: List[ContextItem] = [
        _build_item(RoleType.System, "系统"),
        FunctionCallItem(
            meta=ContextItemMeta.create(logical_turn_id="turn-1"),
            tool_call=tool_call,
        ),
    ]

    sections = measure_request_sections(items, history_item_count=1)

    assert _section_map(sections)[SECTION_MESSAGES] == (
        len("query_memory") + len('{"query": "MaiBot"}'),
        1,
    )


def test_empty_sections_are_skipped() -> None:
    items = _build_request_items(system_prompt="系统", history_texts=[], notice_texts=[])

    sections = measure_request_sections(items, history_item_count=0)

    assert [section.key for section in sections] == [SECTION_SYSTEM_PROMPT]
