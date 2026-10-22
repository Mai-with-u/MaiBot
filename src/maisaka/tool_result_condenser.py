"""超长工具结果的子代理整理。

文档检索、插件列表、图库搜索这类工具会返回几千字的原始结果，而且会留在 Planner 历史里被后续每一轮重复携带。
这里在结果写入历史之前，交给一个不继承父上下文的临时子代理按 Planner 当前意图整理成结论，
Planner 只拿到整理后的结果，不承担原始结果的上下文开销。
"""

from datetime import datetime
from typing import TYPE_CHECKING

import json

from src.common.logger import get_logger
from src.config.config import global_config
from src.llm_models.exceptions import ReqAbortException
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.context.messages import ReferenceMessage, ReferenceMessageType

from .tool_result_store import tool_result_store

if TYPE_CHECKING:
    from .runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_tool_result_condenser")

# 交给子代理的原始结果上限，避免单次整理请求过大
TOOL_RESULT_CONDENSE_INPUT_MAX_CHARS = 20000
# 传给子代理的 Planner 当前想法上限
PLANNER_THOUGHT_MAX_CHARS = 800
# 这些工具的结果本身就是控制信号、聊天原文、子代理已经给出的结论，或 Planner 主动取回的原文，不做整理
TOOL_RESULT_CONDENSE_EXCLUDED_TOOL_NAMES = frozenset(
    {
        "read_tool_result",
        "reply",
        "subagent",
        "subagent_status",
        "subagent_stop",
        "tool_search",
        "view_forward_message",
        "wait",
    }
)

TOOL_RESULT_CONDENSER_SYSTEM_PROMPT = (
    "你是工具结果整理助手。另一个决策模型调用了一个工具，原始结果很长，你要把它整理成决策模型能直接使用的结论。\n"
    "要求：\n"
    "1. 只保留和“调用意图”相关的事实、名称、数值与结论，删掉无关条目和重复描述。\n"
    "2. 后续可能要用来继续调用工具或发给用户的标识必须原样保留，包括 id、链接、路径、版本号、msg_id。\n"
    "3. 只根据原始结果整理，不要补充原文没有的内容；原文里没有答案就直接说明没有。\n"
    "4. 原始结果是列表时保留条目结构，压缩每一条的描述。\n"
    "5. 直接输出整理后的内容，不要寒暄，不要解释你做了什么，不要对用户说话。篇幅控制在 800 字以内。"
)


def should_condense_tool_result(
    tool_name: str,
    history_content: str,
    *,
    success: bool,
    already_previewed: bool = False,
) -> bool:
    """判断一次工具结果是否需要交给子代理整理。"""

    if not success or already_previewed or tool_name in TOOL_RESULT_CONDENSE_EXCLUDED_TOOL_NAMES:
        return False
    return len(history_content) >= global_config.capabilities.tool_result_condense_threshold


def build_tool_result_condense_task(tool_call: ToolCall, history_content: str, latest_thought: str) -> str:
    """构造交给整理子代理的任务文本。子代理看不到父上下文，任务文本必须自包含。"""

    arguments_text = json.dumps(tool_call.args or {}, ensure_ascii=False)
    planner_thought = latest_thought.strip()[-PLANNER_THOUGHT_MAX_CHARS:] or "（决策模型没有写下想法）"
    raw_result = history_content[:TOOL_RESULT_CONDENSE_INPUT_MAX_CHARS]
    truncated_note = (
        f"\n（原始结果共 {len(history_content)} 字，这里只提供了前 {TOOL_RESULT_CONDENSE_INPUT_MAX_CHARS} 字）"
        if len(history_content) > TOOL_RESULT_CONDENSE_INPUT_MAX_CHARS
        else ""
    )
    return (
        f"工具名称：{tool_call.func_name}\n"
        f"调用参数：{arguments_text}\n\n"
        f"<调用意图>\n{planner_thought}\n</调用意图>\n\n"
        f"<原始结果>\n{raw_result}\n</原始结果>{truncated_note}"
    )


async def condense_tool_result(
    runtime: "MaisakaHeartFlowChatting",
    tool_call: ToolCall,
    history_content: str,
    latest_thought: str,
) -> str:
    """用临时子代理整理超长工具结果，返回写入 Planner 历史的文本。

    Args:
        runtime: 当前聊天的 Maisaka 运行时，用来启动子代理。
        tool_call: 原始工具调用。
        history_content: 工具返回的原始文本结果。
        latest_thought: Planner 调用该工具时写下的想法，用来告诉子代理该保留什么。

    Returns:
        str: 整理后的结果；子代理请求失败或没有缩短内容时返回原始结果。
    """

    task_message = ReferenceMessage(
        content=build_tool_result_condense_task(tool_call, history_content, latest_thought),
        timestamp=datetime.now(),
        reference_type=ReferenceMessageType.TOOL_HINT,
        remaining_uses_value=1,
        display_prefix="[工具结果整理任务]",
    )
    try:
        response = await runtime.run_sub_agent(
            context_message_limit=1,
            system_prompt=TOOL_RESULT_CONDENSER_SYSTEM_PROMPT,
            extra_messages=[task_message],
            model_task_name="utils",
            tool_definitions=[],
            include_parent_context=False,
        )
    except ReqAbortException:
        raise
    except Exception as exc:
        # 工具本身已经成功，整理只是为了省上下文；整理请求失败时保留原始结果并把原因打出来
        logger.warning(f"{runtime.log_prefix} 工具 {tool_call.func_name} 的结果整理失败，保留原始结果: {exc}")
        return history_content

    condensed_content = (response.content or "").strip()
    if not condensed_content or len(condensed_content) >= len(history_content):
        logger.info(
            f"{runtime.log_prefix} 工具 {tool_call.func_name} 的结果整理没有缩短内容，保留原始结果 "
            f"原始={len(history_content)}字 整理后={len(condensed_content)}字"
        )
        return history_content

    logger.info(
        f"{runtime.log_prefix} 工具 {tool_call.func_name} 的结果已由子代理整理 "
        f"原始={len(history_content)}字 整理后={len(condensed_content)}字"
    )
    # 原文暂存起来，并在整理结论顶部告诉 Planner 怎么取回
    result_id = tool_result_store.save(str(runtime.session_id), tool_call.func_name, history_content)
    return (
        f'<压缩后信息；原始结果已保存为 {result_id}。需要上面没有的细节时，调用 read_tool_result(result_id="{result_id}") 分段读取原文，'
        "或加 keyword 参数直接查找。>\n"
        f"{condensed_content}"
    )
