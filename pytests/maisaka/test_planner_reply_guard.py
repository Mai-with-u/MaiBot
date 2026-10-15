import pytest

from src.config.config import global_config
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.planner_reply_guard import has_unsent_reply_intent
from src.maisaka.tool_result_condenser import build_tool_result_condense_task, should_condense_tool_result


@pytest.mark.parametrize(
    "planner_content",
    [
        "牛牛在吐槽版本。\n\n不需要调用工具，直接回复即可。",
        "可以顺着调侃一句。\n\n调用工具：无必要，直接通过reply发送回复即可。",
        "决定：调用reply，回复SCh的消息（msg_id: \"1884926678\"），语气带点调侃。",
        "下一步动作：\n- 用 reply 把结论给出去。\n\n准备发送回复。",
        "计划：简短回喵七一句，自嘲刚才的撤回事件，把气氛收住。",
    ],
)
def test_detects_reply_intent_without_tool_call(planner_content: str) -> None:
    assert has_unsent_reply_intent(planner_content)


@pytest.mark.parametrize(
    "planner_content",
    [
        "",
        "群里没有新消息，也没人接话。保持观望，不发言。",
        "**决策：不调用任何工具，不进行回复。**",
        "计划：不发reply，也不贴表情，继续等下一个明确递话的话头。",
        "刚才已经回过“好耶”，此时再插进去反而刻意。\n\n下一步计划：等待群友接话，有新消息再判断是否回复。",
        "下一步计划：\n- 不再调用 reply，等二憨补充测试信息。",
    ],
)
def test_ignores_decisions_not_to_reply(planner_content: str) -> None:
    assert not has_unsent_reply_intent(planner_content)


def test_condenser_only_targets_long_successful_query_results() -> None:
    long_result = "x" * global_config.experimental.tool_result_condense_threshold

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
    monkeypatch.setattr(global_config.experimental, "tool_result_condense_threshold", 800)

    assert should_condense_tool_result("maibot_release_info", "x" * 800, success=True)
    assert not should_condense_tool_result("maibot_release_info", "x" * 799, success=True)
