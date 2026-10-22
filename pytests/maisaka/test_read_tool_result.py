from types import SimpleNamespace
from typing import Any

import pytest

from src.config.config import global_config, model_config
from src.core.tooling import ToolInvocation
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.builtin_tool import get_all_builtin_tool_specs, read_tool_result
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.tool_result_condenser import condense_tool_result, should_condense_tool_result
from src.maisaka.tool_result_store import MAX_STORED_RESULTS_PER_SESSION, ToolResultStore, tool_result_store
from src.web_search.config import WebSearchTaskConfig


def _tool_ctx(session_id: str) -> BuiltinToolRuntimeContext:
    return BuiltinToolRuntimeContext(SimpleNamespace(), SimpleNamespace(session_id=session_id))


def _invocation(**arguments: Any) -> ToolInvocation:
    return ToolInvocation(tool_name="read_tool_result", arguments=arguments, call_id="call-read")


def test_store_assigns_short_ids_per_session_and_evicts_oldest() -> None:
    store = ToolResultStore()

    assert store.save("chat-a", "tool", "一") == "r1"
    assert store.save("chat-a", "tool", "二") == "r2"
    assert store.save("chat-b", "tool", "别的聊天") == "r1"
    assert store.get("chat-a", "r2").content == "二"
    assert store.get("chat-b", "r2") is None

    for index in range(MAX_STORED_RESULTS_PER_SESSION):
        store.save("chat-a", "tool", f"新结果{index}")
    assert store.get("chat-a", "r1") is None
    assert store.get("chat-a", f"r{MAX_STORED_RESULTS_PER_SESSION + 2}") is not None


@pytest.mark.asyncio
async def test_condensed_result_tells_planner_how_to_fetch_the_original() -> None:
    original = "插件A 仓库 https://example.com/a\n" * 200

    async def run_sub_agent(**kwargs: Any) -> Any:
        del kwargs
        return SimpleNamespace(content="共 200 个插件，代表：插件A。")

    runtime = SimpleNamespace(session_id="chat-condense", log_prefix="[test]", run_sub_agent=run_sub_agent)
    tool_call = ToolCall(call_id="call-1", func_name="maibot_plugin_list", args={})

    history_content = await condense_tool_result(runtime, tool_call, original, "想推荐插件")

    result_id = history_content.split("原始结果已保存为 ")[1].split("。")[0]
    assert history_content.splitlines() == [
        f'<压缩后信息；原始结果已保存为 {result_id}。需要上面没有的细节时，调用 read_tool_result(result_id="{result_id}") 分段读取原文，或加 keyword 参数直接查找。>',
        "共 200 个插件，代表：插件A。",
    ]
    assert tool_result_store.get("chat-condense", result_id).content == original


@pytest.mark.asyncio
async def test_read_tool_result_pages_through_original_by_offset() -> None:
    result_id = tool_result_store.save("chat-read", "maibot_release_info", "甲" * 2000 + "乙" * 1500)
    tool_ctx = _tool_ctx("chat-read")

    first = await read_tool_result.handle_tool(tool_ctx, _invocation(result_id=result_id))
    second = await read_tool_result.handle_tool(tool_ctx, _invocation(result_id=result_id, offset=2000, max_chars=99999))

    assert first.success
    assert f"[{result_id} · maibot_release_info 原始结果 · 共 3500 字 · 本段 0–2000]" in first.content
    assert "后面还有 1500 字，用 offset=2000 继续读取" in first.content
    assert "乙" * 1500 in second.content
    assert "已到原文末尾" in second.content
    # 读回来的内容不会再触发整理
    assert not should_condense_tool_result("read_tool_result", "x" * 99999, success=True)


@pytest.mark.asyncio
async def test_read_tool_result_finds_keyword_snippets() -> None:
    content = "无关内容。" * 300 + "Pixiv 搜图插件 仓库 https://example.com/pixiv" + "无关内容。" * 300
    result_id = tool_result_store.save("chat-keyword", "maibot_plugin_list", content)
    tool_ctx = _tool_ctx("chat-keyword")

    found = await read_tool_result.handle_tool(tool_ctx, _invocation(result_id=result_id, keyword="pixiv"))
    missing = await read_tool_result.handle_tool(tool_ctx, _invocation(result_id=result_id, keyword="不存在的词"))

    assert "命中 2 处" in found.content
    assert "https://example.com/pixiv" in found.content
    assert len(found.content) < 1200
    assert "原文中没有这个关键词" in missing.content


@pytest.mark.asyncio
async def test_read_tool_result_reports_expired_ids() -> None:
    result = await read_tool_result.handle_tool(_tool_ctx("chat-empty"), _invocation(result_id="r99"))

    assert not result.success
    assert "重新调用原来的工具" in result.error_message


def test_read_tool_result_is_exposed_with_the_condense_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_config.model_task_config, "web_search", WebSearchTaskConfig())
    monkeypatch.setattr(global_config.capabilities, "tool_result_condense", False)
    assert "read_tool_result" not in {tool_spec.name for tool_spec in get_all_builtin_tool_specs()}

    monkeypatch.setattr(global_config.capabilities, "tool_result_condense", True)
    assert "read_tool_result" in {tool_spec.name for tool_spec in get_all_builtin_tool_specs()}


def test_web_results_can_be_read_without_condense(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.capabilities, "tool_result_condense", False)
    monkeypatch.setattr(model_config.model_task_config, "web_search", WebSearchTaskConfig(model_list=["step"]))
    assert "read_tool_result" in {tool_spec.name for tool_spec in get_all_builtin_tool_specs()}


def test_web_preview_skips_condense_but_raw_mcp_result_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.capabilities, "tool_result_condense_threshold", 3000)
    assert not should_condense_tool_result("web_search", "x" * 5000, success=True, already_previewed=True)
    assert should_condense_tool_result("web_search", "x" * 5000, success=True)
