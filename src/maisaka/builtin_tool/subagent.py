"""subagent 内置工具：把一个自包含的任务主动派发给子代理。

子代理在独立上下文里运行，看不到当前聊天，只拿到 Planner 写的任务说明和被授权的工具；
它可以自己多轮调用这些工具，最后只把结论交还给 Planner，中间过程不进入 Planner 上下文。

默认情况下 Planner 等子代理跑完，结论作为本次工具结果返回。开启
``capabilities.advanced_sub_agent`` 后子代理改在后台运行：调用立刻返回任务 id，结论在任务结束时
作为一条新的 user 消息送回并唤醒 Planner，另外提供 subagent_status / subagent_stop 查看和终止任务。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncio
import json
import time

from src.common.logger import get_logger
from src.config.config import global_config
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.llm_models.payload_content.context_item import FunctionCallItem
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.context.messages import LLMContextMessage, ReferenceMessage, ReferenceMessageType, ToolResultMessage
from src.maisaka.monitor.events import (
    emit_subagent_finished,
    emit_subagent_round,
    emit_subagent_started,
    emit_subagent_tool_result,
)
from src.maisaka.subagent_tasks import (
    SUBAGENT_MAX_RUNNING_TASKS,
    SubAgentOutcome,
    SubAgentTask,
    format_tool_usage,
)

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_subagent")

SUBAGENT_TOOL_NAME = "subagent"
SUBAGENT_STATUS_TOOL_NAME = "subagent_status"
SUBAGENT_STOP_TOOL_NAME = "subagent_stop"
# 子代理最多进行的模型请求轮数；用完后再给一次不带工具的收尾机会。
# 同步运行时 Planner 在等结论，轮数要少；后台运行不挡着 Planner，可以放宽。
SUBAGENT_MAX_ROUNDS = 5
SUBAGENT_BACKGROUND_MAX_ROUNDS = 20
# 子代理上下文里单条工具结果的上限
SUBAGENT_TOOL_RESULT_MAX_CHARS = 20000
# 子代理内部上下文按条数计的窗口，足够容纳全部轮次
SUBAGENT_CONTEXT_MESSAGE_LIMIT = 60
# 状态列表里任务说明的展示长度
SUBAGENT_TASK_PREVIEW_CHARS = 80
# 这些工具负责发言和等待，只能由 Planner 自己调用；子代理也不能再派发或管理子代理
SUBAGENT_FORBIDDEN_TOOL_NAMES = frozenset(
    {
        SUBAGENT_TOOL_NAME,
        SUBAGENT_STATUS_TOOL_NAME,
        SUBAGENT_STOP_TOOL_NAME,
        "react_message",
        "reply",
        "send_emoji",
        "show_emoji_list",
        "tool_search",
        "wait",
    }
)

SUBAGENT_SYSTEM_PROMPT = (
    "你是一个被派发任务的子代理。派发者看不到你的工作过程，只会收到你最后一条不带工具调用的文字。\n"
    "工作方式：\n"
    "1. 任务说明就是你知道的全部背景，不要假设任何没有写出来的信息。\n"
    "2. 用提供的工具查清楚需要的事实；互不依赖的调用可以一次并行发出。\n"
    "3. 工具返回无结果或失败时换一种查法，确实查不到就如实说明，不要编造。\n"
    "4. 查清后直接输出结论：先给答案，再列出支撑它的关键事实；派发者后续可能用到的 id、链接、路径、版本号原样保留。\n"
    "5. 你不能向任何人发言，也不能把任务再派发出去。结论控制在 800 字以内。"
)
SUBAGENT_FINAL_ROUND_HINT = "可用的轮数已经用完，这一轮不能再调用工具。请根据已经拿到的结果直接给出结论，没有查清的部分如实说明。"

_STATUS_LABELS = {"running": "运行中", "completed": "已完成", "failed": "失败", "stopped": "已终止"}


def get_tool_spec() -> ToolSpec:
    """获取 subagent 工具声明。"""

    if global_config.capabilities.advanced_sub_agent:
        result_description = (
            "子代理在后台运行：调用后立刻返回任务 id，你可以继续处理聊天，不需要为它等待；"
            "它结束时结论会作为一条 <subagent_result> 消息送回给你，中间的原始结果不会进入你的上下文。"
            f"需要时用 {SUBAGENT_STATUS_TOOL_NAME} 查看进度，用 {SUBAGENT_STOP_TOOL_NAME} 终止不再需要的任务。"
        )
    else:
        result_description = "最后只把结论返回给你，中间的原始结果不会进入你的上下文。"
    return ToolSpec(
        name=SUBAGENT_TOOL_NAME,
        description=(
            "把一个自包含的查询或整理任务交给子代理。子代理在独立上下文里工作，可以自己多次调用你授权的工具，"
            f"{result_description}"
            "适合需要多次查询、交叉比对，或会返回大量原始内容的任务；只需要一次简单查询时直接调用对应工具即可。"
            "子代理看不到当前聊天，也不能发言：发言、等待和群管理操作必须由你自己完成。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "完整、自包含的任务说明。子代理看不到聊天记录，"
                        "要写清楚要查什么、相关的名称或关键词、以及你希望得到什么形式的结论。"
                    ),
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "授权子代理使用的工具名列表，只填查询、检索、读取类工具；"
                        "可以包含 deferred tools 列表里的工具，不需要先 tool_search。"
                    ),
                },
            },
            "required": ["task", "tools"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def get_status_tool_spec() -> ToolSpec:
    """获取 subagent_status 工具声明。"""

    return ToolSpec(
        name=SUBAGENT_STATUS_TOOL_NAME,
        description=(
            "查看后台子代理任务的状态。不填 task_id 时列出全部任务；填了则给出该任务的详情，已结束的任务会附上结论。"
            "子代理结束时结论会自动送回，一般不需要主动查询。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "要查看的任务 id，例如 subagent-1；不填则列出全部任务。"},
            },
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def get_stop_tool_spec() -> ToolSpec:
    """获取 subagent_stop 工具声明。"""

    return ToolSpec(
        name=SUBAGENT_STOP_TOOL_NAME,
        description="终止一个仍在运行的后台子代理任务。被终止的任务不会再送回结论。",
        parameters_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "minLength": 1, "description": "要终止的任务 id，例如 subagent-1。"},
            },
            "required": ["task_id"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def _is_grantable(tool_spec: ToolSpec) -> bool:
    """判断一个工具能否交给子代理使用。"""

    if not tool_spec.enabled or tool_spec.name in SUBAGENT_FORBIDDEN_TOOL_NAMES:
        return False
    # 明确标注为破坏性的工具不交给看不到聊天上下文的子代理
    return not (tool_spec.annotation is not None and tool_spec.annotation.destructive is True)


def _build_tool_result_message(tool_call: ToolCall, result: ToolExecutionResult, logical_turn_id: str) -> ToolResultMessage:
    """把子代理内部的工具执行结果转成它自己上下文里的 tool result。"""

    content = result.get_history_content()[:SUBAGENT_TOOL_RESULT_MAX_CHARS]
    if not content:
        content = "工具执行成功。" if result.success else f"工具 {tool_call.func_name} 执行失败。"
    return ToolResultMessage(
        content=content,
        timestamp=datetime.now(),
        tool_call_id=tool_call.call_id,
        logical_turn_id=logical_turn_id,
        tool_name=tool_call.func_name,
        success=result.success,
    )


async def _run_sub_agent_rounds(
    tool_ctx: BuiltinToolRuntimeContext,
    *,
    task_id: str,
    task: str,
    granted_specs: List[ToolSpec],
    tool_usage: Dict[str, int],
    max_rounds: int,
) -> SubAgentOutcome:
    """在独立上下文里多轮运行子代理，直到它给出结论或用完轮数。

    Args:
        tool_ctx: 内置工具运行上下文。
        task_id: 任务 id，用于在观察事件里标识这次运行。
        task: Planner 写给子代理的任务说明。
        granted_specs: 授权给子代理的工具声明。
        tool_usage: 工具调用次数；运行过程中原地更新，后台任务可以据此展示进度。
        max_rounds: 可以调用工具的轮数上限。
    """

    runtime = tool_ctx.runtime
    engine = tool_ctx.engine
    tool_registry = runtime._tool_registry
    granted_tool_names = {tool_spec.name for tool_spec in granted_specs}
    tool_definitions = [tool_spec.to_llm_definition() for tool_spec in granted_specs]
    sub_agent_history: List[LLMContextMessage] = [
        ReferenceMessage(
            content=task,
            timestamp=datetime.now(),
            reference_type=ReferenceMessageType.TOOL_HINT,
            remaining_uses_value=None,
            display_prefix="[派发给你的任务]",
        )
    ]
    execution_context = engine._build_tool_execution_context(task)
    conclusion = ""

    for round_index in range(max_rounds + 1):
        is_final_round = round_index == max_rounds
        if is_final_round:
            sub_agent_history.append(
                ReferenceMessage(
                    content=SUBAGENT_FINAL_ROUND_HINT,
                    timestamp=datetime.now(),
                    reference_type=ReferenceMessageType.TOOL_HINT,
                    remaining_uses_value=None,
                    display_prefix="[系统提示]",
                )
            )
        response = await runtime.run_sub_agent(
            context_message_limit=SUBAGENT_CONTEXT_MESSAGE_LIMIT,
            system_prompt=SUBAGENT_SYSTEM_PROMPT,
            extra_messages=sub_agent_history,
            tool_definitions=[] if is_final_round else tool_definitions,
            include_parent_context=False,
        )
        is_concluding_round = not response.tool_calls or is_final_round
        await emit_subagent_round(
            session_id=runtime.session_id,
            task_id=task_id,
            round_index=round_index + 1,
            content=(response.content or "").strip(),
            tool_calls=[] if is_concluding_round else list(response.tool_calls),
        )
        if is_concluding_round:
            conclusion = (response.content or "").strip()
            break

        sub_agent_history.extend(response.raw_messages)
        logical_turn_ids = {
            message.output_item.tool_call.call_id: message.output_item.meta.logical_turn_id
            for message in response.raw_messages
            if isinstance(message.output_item, FunctionCallItem)
        }
        for tool_call in response.tool_calls:
            logical_turn_id = str(logical_turn_ids[tool_call.call_id] or "")
            tool_started_at = time.time()
            if tool_call.func_name not in granted_tool_names:
                result = ToolExecutionResult(
                    tool_name=tool_call.func_name,
                    success=False,
                    error_message=f"工具 {tool_call.func_name} 没有授权给你。",
                )
            else:
                tool_usage[tool_call.func_name] = tool_usage.get(tool_call.func_name, 0) + 1
                result = await tool_registry.invoke(engine._build_tool_invocation(tool_call, task), execution_context)
                logger.info(
                    f"{runtime.log_prefix} 子代理调用工具 {tool_call.func_name} "
                    f"参数={json.dumps(tool_call.args or {}, ensure_ascii=False)[:200]} 成功={result.success}"
                )
            tool_result_message = _build_tool_result_message(tool_call, result, logical_turn_id)
            sub_agent_history.append(tool_result_message)
            await emit_subagent_tool_result(
                session_id=runtime.session_id,
                task_id=task_id,
                round_index=round_index + 1,
                call_id=tool_call.call_id,
                tool_name=tool_call.func_name,
                success=result.success,
                summary=tool_result_message.content,
                duration_ms=(time.time() - tool_started_at) * 1000,
            )

    logger.info(f"{runtime.log_prefix} 子代理任务结束 工具使用={format_tool_usage(tool_usage)} 结论={len(conclusion)}字")
    return SubAgentOutcome(conclusion=conclusion, tool_usage=tool_usage)


async def _run_and_report(
    tool_ctx: BuiltinToolRuntimeContext,
    *,
    task_id: str,
    task: str,
    granted_specs: List[ToolSpec],
    tool_usage: Dict[str, int],
    max_rounds: int,
) -> SubAgentOutcome:
    """运行子代理，并把完成或失败上报给观察页；被终止的任务由终止方上报。"""

    session_id = tool_ctx.runtime.session_id
    started_at = time.time()
    try:
        outcome = await _run_sub_agent_rounds(
            tool_ctx,
            task_id=task_id,
            task=task,
            granted_specs=granted_specs,
            tool_usage=tool_usage,
            max_rounds=max_rounds,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await emit_subagent_finished(
            session_id=session_id,
            task_id=task_id,
            status="failed",
            conclusion="",
            error_message=f"{type(exc).__name__}: {exc}",
            tool_usage=tool_usage,
            duration_ms=(time.time() - started_at) * 1000,
        )
        raise
    await emit_subagent_finished(
        session_id=session_id,
        task_id=task_id,
        status="completed" if outcome.conclusion else "failed",
        conclusion=outcome.conclusion,
        error_message="" if outcome.conclusion else "子代理没有给出结论",
        tool_usage=outcome.tool_usage,
        duration_ms=(time.time() - started_at) * 1000,
    )
    return outcome


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 subagent：运行一个带工具的多轮子代理，同步返回结论或在后台运行。"""

    del context
    task = str(invocation.arguments.get("task") or "").strip()
    if not task:
        return tool_ctx.build_failure_result(invocation.tool_name, "subagent 需要提供非空的 task。")
    raw_tool_names = invocation.arguments.get("tools")
    if not isinstance(raw_tool_names, list) or not raw_tool_names:
        return tool_ctx.build_failure_result(invocation.tool_name, "subagent 需要在 tools 里至少授权一个工具。")
    requested_tool_names = [str(tool_name).strip() for tool_name in raw_tool_names if str(tool_name).strip()]

    runtime = tool_ctx.runtime
    engine = tool_ctx.engine
    tool_registry = runtime._tool_registry
    if tool_registry is None:
        return tool_ctx.build_failure_result(invocation.tool_name, "统一工具注册表尚未初始化。")

    available_specs = {
        tool_spec.name: tool_spec
        for tool_spec in await tool_registry.list_tools(engine._build_tool_availability_context())
    }
    granted_specs: List[ToolSpec] = []
    rejected_tool_names: List[str] = []
    for tool_name in dict.fromkeys(requested_tool_names):
        tool_spec = available_specs.get(tool_name)
        if tool_spec is None or not _is_grantable(tool_spec):
            rejected_tool_names.append(tool_name)
            continue
        granted_specs.append(tool_spec)
    if not granted_specs:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"没有可以授权给子代理的工具：{'、'.join(rejected_tool_names)} 不存在，或属于只能由你自己调用的工具。",
        )

    granted_tool_names = sorted(tool_spec.name for tool_spec in granted_specs)
    rejected_note = (
        f"\n（未授权的工具：{'、'.join(rejected_tool_names)}，它们不存在或只能由你自己调用）" if rejected_tool_names else ""
    )

    task_manager = runtime._subagent_tasks
    run_in_background_mode = global_config.capabilities.advanced_sub_agent
    max_rounds = SUBAGENT_BACKGROUND_MAX_ROUNDS if run_in_background_mode else SUBAGENT_MAX_ROUNDS
    if run_in_background_mode and task_manager.running_count >= SUBAGENT_MAX_RUNNING_TASKS:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"已有 {task_manager.running_count} 个子代理在后台运行，达到上限；"
            f"等它们结束，或用 {SUBAGENT_STOP_TOOL_NAME} 终止不再需要的任务后再派发。",
        )

    task_id = task_manager.allocate_task_id()
    await emit_subagent_started(
        session_id=runtime.session_id,
        task_id=task_id,
        call_id=invocation.call_id,
        description=task,
        granted_tools=granted_tool_names,
        background=run_in_background_mode,
        max_rounds=max_rounds,
    )

    if run_in_background_mode:
        async def run_in_background(background_task: SubAgentTask) -> SubAgentOutcome:
            return await _run_and_report(
                tool_ctx,
                task_id=task_id,
                task=task,
                granted_specs=granted_specs,
                tool_usage=background_task.tool_usage,
                max_rounds=max_rounds,
            )

        background_task = task_manager.start(
            task_id=task_id,
            description=task,
            granted_tools=granted_tool_names,
            runner=run_in_background,
            on_finished=runtime.deliver_subagent_result,
        )
        logger.info(f"{runtime.log_prefix} 子代理任务已转入后台 task_id={background_task.task_id}")
        return tool_ctx.build_success_result(
            invocation.tool_name,
            f"子代理已在后台开始工作，任务 id：{background_task.task_id}。"
            "它结束时结论会作为一条 <subagent_result> 消息送回给你；现在可以继续处理聊天，不需要为它等待。"
            f"{rejected_note}",
            structured_content={
                "task_id": background_task.task_id,
                "granted_tools": granted_tool_names,
                "rejected_tools": rejected_tool_names,
            },
        )

    outcome = await _run_and_report(
        tool_ctx,
        task_id=task_id,
        task=task,
        granted_specs=granted_specs,
        tool_usage={},
        max_rounds=max_rounds,
    )
    usage_text = format_tool_usage(outcome.tool_usage)
    if not outcome.conclusion:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"子代理没有给出结论（工具使用：{usage_text}）。",
        )

    structured_content: Dict[str, Any] = {
        "task_id": task_id,
        "tool_usage": outcome.tool_usage,
        "granted_tools": granted_tool_names,
        "rejected_tools": rejected_tool_names,
    }
    return tool_ctx.build_success_result(
        invocation.tool_name,
        f"[子代理结论｜工具使用：{usage_text}]\n{outcome.conclusion}{rejected_note}",
        structured_content=structured_content,
    )


def _format_task_summary(task: SubAgentTask) -> str:
    """把一个任务整理成状态列表里的一行。"""

    preview = task.description.replace("\n", " ")
    if len(preview) > SUBAGENT_TASK_PREVIEW_CHARS:
        preview = f"{preview[:SUBAGENT_TASK_PREVIEW_CHARS]}…"
    return (
        f"- {task.task_id}｜{_STATUS_LABELS[task.status]}｜已用 {int(task.elapsed_seconds)} 秒｜"
        f"工具使用：{format_tool_usage(task.tool_usage)}｜任务：{preview}"
    )


async def handle_status_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 subagent_status：列出后台子代理任务，或给出单个任务的详情。"""

    del context
    task_manager = tool_ctx.runtime._subagent_tasks
    task_id = str(invocation.arguments.get("task_id") or "").strip()
    if not task_id:
        tasks = task_manager.list_tasks()
        if not tasks:
            return tool_ctx.build_success_result(invocation.tool_name, "当前没有后台子代理任务。")
        return tool_ctx.build_success_result(
            invocation.tool_name,
            "\n".join(_format_task_summary(task) for task in tasks),
            structured_content={"tasks": [{"task_id": task.task_id, "status": task.status} for task in tasks]},
        )

    task = task_manager.get(task_id)
    if task is None:
        return tool_ctx.build_failure_result(invocation.tool_name, f"没有 id 为 {task_id} 的子代理任务。")
    detail_lines = [_format_task_summary(task), f"授权工具：{'、'.join(task.granted_tools)}"]
    if task.status == "completed":
        detail_lines.append(f"结论：\n{task.conclusion}")
    elif task.status == "failed":
        detail_lines.append(f"失败原因：{task.error_message}")
    return tool_ctx.build_success_result(
        invocation.tool_name,
        "\n".join(detail_lines),
        structured_content={"task_id": task.task_id, "status": task.status},
    )


async def handle_stop_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 subagent_stop：终止一个仍在运行的后台子代理任务。"""

    del context
    task_manager = tool_ctx.runtime._subagent_tasks
    task_id = str(invocation.arguments.get("task_id") or "").strip()
    task = task_manager.get(task_id)
    if task is None:
        return tool_ctx.build_failure_result(invocation.tool_name, f"没有 id 为 {task_id} 的子代理任务。")
    if not task_manager.stop(task_id):
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"任务 {task_id} 已经结束（{_STATUS_LABELS[task.status]}），不需要终止。",
        )
    logger.info(f"{tool_ctx.runtime.log_prefix} 后台子代理任务已被终止 task_id={task_id}")
    await emit_subagent_finished(
        session_id=tool_ctx.runtime.session_id,
        task_id=task_id,
        status="stopped",
        conclusion="",
        error_message="",
        tool_usage=task.tool_usage,
        duration_ms=task.elapsed_seconds * 1000,
    )
    return tool_ctx.build_success_result(invocation.tool_name, f"已终止子代理任务 {task_id}，它不会再送回结论。")
