import pytest

from src.config.config import global_config
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.tool_result_condenser import build_tool_result_condense_task, should_condense_tool_result


def test_condenser_only_targets_long_successful_query_results() -> None:
    long_result = "x" * global_config.capabilities.tool_result_condense_threshold

    assert should_condense_tool_result("maibot_release_info", long_result, success=True)
    assert not should_condense_tool_result("maibot_release_info", long_result[:-1], success=True)
    assert not should_condense_tool_result("maibot_release_info", long_result, success=False)
    assert not should_condense_tool_result("view_forward_message", long_result, success=True)


def test_condense_task_is_self_contained() -> None:
    tool_call = ToolCall(call_id="call-1", func_name="maibot_plugin_list", args={"keyword": "pixiv"})

    task_text = build_tool_result_condense_task(tool_call, "插件A\n插件B", "想找一个 pixiv 相关的插件推荐给群友")

    assert "工具名称：maibot_plugin_list" in task_text
    assert '"keyword": "pixiv"' in task_text
    assert "想找一个 pixiv 相关的插件推荐给群友" in task_text
    assert "<原始结果>\n插件A\n插件B\n</原始结果>" in task_text


def test_condenser_threshold_follows_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.capabilities, "tool_result_condense_threshold", 800)

    assert should_condense_tool_result("maibot_release_info", "x" * 800, success=True)
    assert not should_condense_tool_result("maibot_release_info", "x" * 799, success=True)
