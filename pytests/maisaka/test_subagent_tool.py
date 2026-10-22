from types import SimpleNamespace
from typing import Any, List

import asyncio
import json

import pytest

from src.config.config import global_config
from src.core.tooling import ToolAnnotation, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
)
from src.maisaka.builtin_tool import get_all_builtin_tool_specs, subagent
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.chat_loop_service import ChatResponse
from src.maisaka.context.messages import ToolResultMessage
from src.maisaka.context.usage import resolve_tool_definition_name
from src.maisaka.runtime import MaisakaHeartFlowChatting
from src.maisaka.subagent_tasks import SUBAGENT_MAX_RUNNING_TASKS, SubAgentTask, SubAgentTaskManager


@pytest.fixture(autouse=True)
def _synchronous_subagent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_config.capabilities, "advanced_sub_agent", False)


@pytest.fixture(autouse=True)
def monitor_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """收集子代理上报给观察页的事件，不触碰真实的事件账本。"""

    events: list[tuple[str, dict[str, Any]]] = []

    def recorder(event_name: str) -> Any:
        async def record(**kwargs: Any) -> None:
            events.append((event_name, kwargs))

        return record

    for event_name in ("started", "round", "tool_result", "finished"):
        monkeypatch.setattr(subagent, f"emit_subagent_{event_name}", recorder(event_name))
    return events


def _response(*, text: str = "", calls: List[tuple[str, dict[str, Any]]] | None = None) -> ChatResponse:
    """构造一条子代理模型响应：可带正文和若干工具调用。"""

    items: list[Any] = []
    if text:
        items.append(AssistantMessageItem(meta=ContextItemMeta.create(logical_turn_id="turn-1"), parts=(ContextTextPart(text),)))
    for index, (name, args) in enumerate(calls or []):
        items.append(
            FunctionCallItem(
                meta=ContextItemMeta.create(logical_turn_id="turn-1"),
                tool_call=ContextToolCall(
                    call_id=f"call-{name}-{index}",
                    func_name=name,
                    args_json=json.dumps(args).encode("utf-8"),
                ),
            )
        )
    return ChatResponse(
        output_items=tuple(items),
        request_messages=[],
        selected_history_count=0,
        tool_count=0,
        prompt_tokens=0,
        built_message_count=0,
        completion_tokens=0,
        total_tokens=0,
    )


class _Registry:
    def __init__(self) -> None:
        self.invocations: list[ToolInvocation] = []
        self.specs = [
            ToolSpec(name="docs_search", description="查文档"),
            ToolSpec(name="reply", description="发言"),
            ToolSpec(name="wipe_data", description="清数据", annotation=ToolAnnotation(destructive=True)),
        ]

    async def list_tools(self, context: Any = None) -> list[ToolSpec]:
        del context
        return list(self.specs)

    async def invoke(self, invocation: ToolInvocation, context: Any = None) -> ToolExecutionResult:
        del context
        self.invocations.append(invocation)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, content="文档内容")


def _build_tool_ctx(responses: List[ChatResponse]) -> tuple[BuiltinToolRuntimeContext, _Registry, list[dict[str, Any]]]:
    registry = _Registry()
    sub_agent_requests: list[dict[str, Any]] = []
    pending_responses = list(responses)

    async def run_sub_agent(**kwargs: Any) -> ChatResponse:
        sub_agent_requests.append(
            {
                "history": list(kwargs["extra_messages"]),
                "tool_names": [resolve_tool_definition_name(definition) for definition in kwargs["tool_definitions"]],
                "include_parent_context": kwargs["include_parent_context"],
            }
        )
        return pending_responses.pop(0)

    runtime = SimpleNamespace(
        log_prefix="[test]",
        session_id="s",
        _tool_registry=registry,
        _subagent_tasks=SubAgentTaskManager(),
        run_sub_agent=run_sub_agent,
    )
    engine = SimpleNamespace(
        _build_tool_availability_context=lambda: None,
        _build_tool_execution_context=lambda thought: ToolExecutionContext(session_id="s", reasoning=thought),
        _build_tool_invocation=lambda tool_call, thought: ToolInvocation(
            tool_name=tool_call.func_name,
            arguments=dict(tool_call.args or {}),
            call_id=tool_call.call_id,
            reasoning=thought,
        ),
    )
    return BuiltinToolRuntimeContext(engine, runtime), registry, sub_agent_requests


def _invocation(task: str, tools: list[str]) -> ToolInvocation:
    return ToolInvocation(tool_name="subagent", arguments={"task": task, "tools": tools}, call_id="call-subagent")


@pytest.mark.asyncio
async def test_subagent_runs_tool_rounds_in_isolated_context_and_returns_conclusion(
    monitor_events: list[tuple[str, dict[str, Any]]],
) -> None:
    tool_ctx, registry, requests = _build_tool_ctx(
        [
            _response(text="先查文档", calls=[("docs_search", {"query": "部署"})]),
            _response(text="部署需要 Python 3.12。"),
        ]
    )

    result = await subagent.handle_tool(tool_ctx, _invocation("查部署要求", ["docs_search", "reply"]))

    assert result.success
    assert result.content.startswith("[子代理结论｜工具使用：docs_search×1]\n部署需要 Python 3.12。")
    assert "未授权的工具：reply" in result.content
    assert [invocation.tool_name for invocation in registry.invocations] == ["docs_search"]
    assert all(request["include_parent_context"] is False for request in requests)
    assert requests[0]["tool_names"] == ["docs_search"]
    # 第二轮的子代理上下文里有任务说明、上一轮输出和对应的工具结果
    second_history = requests[1]["history"]
    assert second_history[0].content == "查部署要求"
    assert isinstance(second_history[-1], ToolResultMessage)
    assert second_history[-1].tool_call_id == "call-docs_search-0"
    assert second_history[-1].logical_turn_id == "turn-1"
    # 观察页按顺序收到：开始、带工具调用的一轮、工具结果、给出结论的一轮、结束
    assert [event_name for event_name, _ in monitor_events] == ["started", "round", "tool_result", "round", "finished"]
    started, first_round, tool_result, _, finished = [payload for _, payload in monitor_events]
    assert started["task_id"] == "subagent-1"
    assert started["call_id"] == "call-subagent"
    assert started["background"] is False
    assert [tool_call.func_name for tool_call in first_round["tool_calls"]] == ["docs_search"]
    assert tool_result["summary"] == "文档内容"
    assert finished["status"] == "completed"
    assert finished["conclusion"] == "部署需要 Python 3.12。"


@pytest.mark.asyncio
async def test_subagent_rejects_tools_only_the_planner_may_call() -> None:
    tool_ctx, registry, requests = _build_tool_ctx([])

    result = await subagent.handle_tool(tool_ctx, _invocation("替我发言", ["reply", "subagent", "subagent_stop", "wipe_data", "missing"]))

    assert not result.success
    assert requests == []
    assert registry.invocations == []


@pytest.mark.asyncio
async def test_subagent_blocks_ungranted_tool_calls_and_forces_conclusion_after_round_limit() -> None:
    looping_response = _response(calls=[("reply", {"text": "hi"})])
    tool_ctx, registry, requests = _build_tool_ctx(
        [*([looping_response] * subagent.SUBAGENT_MAX_ROUNDS), _response(text="没有查到结果。")]
    )

    result = await subagent.handle_tool(tool_ctx, _invocation("查点东西", ["docs_search"]))

    assert result.success
    assert "没有查到结果。" in result.content
    assert registry.invocations == []
    assert len(requests) == subagent.SUBAGENT_MAX_ROUNDS + 1
    assert requests[-1]["tool_names"] == []
    assert not requests[1]["history"][-1].success


def test_subagent_tools_follow_their_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    def exposed_names() -> set[str]:
        return {tool_spec.name for tool_spec in get_all_builtin_tool_specs()}

    monkeypatch.setattr(global_config.capabilities, "planner_subagent", False)
    monkeypatch.setattr(global_config.capabilities, "advanced_sub_agent", True)
    assert not exposed_names() & {"subagent", "subagent_status", "subagent_stop"}

    monkeypatch.setattr(global_config.capabilities, "planner_subagent", True)
    assert {"subagent", "subagent_status", "subagent_stop"} <= exposed_names()

    monkeypatch.setattr(global_config.capabilities, "advanced_sub_agent", False)
    assert "subagent" in exposed_names()
    assert not exposed_names() & {"subagent_status", "subagent_stop"}


def _build_background_tool_ctx(
    monkeypatch: pytest.MonkeyPatch, responses: List[ChatResponse]
) -> tuple[BuiltinToolRuntimeContext, list[SubAgentTask]]:
    """构造开启高级子代理时的工具上下文，并收集被送回的任务。"""

    monkeypatch.setattr(global_config.capabilities, "advanced_sub_agent", True)
    tool_ctx, _, _ = _build_tool_ctx(responses)
    delivered_tasks: list[SubAgentTask] = []
    tool_ctx.runtime.deliver_subagent_result = delivered_tasks.append
    return tool_ctx, delivered_tasks


@pytest.mark.asyncio
async def test_advanced_subagent_returns_task_id_then_delivers_conclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_ctx, delivered_tasks = _build_background_tool_ctx(
        monkeypatch,
        [
            _response(text="先查文档", calls=[("docs_search", {"query": "部署"})]),
            _response(text="部署需要 Python 3.12。"),
        ],
    )

    result = await subagent.handle_tool(tool_ctx, _invocation("查部署要求", ["docs_search"]))

    assert result.success
    assert result.structured_content["task_id"] == "subagent-1"
    assert "subagent-1" in result.content
    # 调用返回时子代理还没有跑完，结论不在工具结果里
    assert "部署需要 Python 3.12。" not in result.content
    assert delivered_tasks == []

    background_task = tool_ctx.runtime._subagent_tasks.get("subagent-1")
    await background_task.asyncio_task

    assert [task.task_id for task in delivered_tasks] == ["subagent-1"]
    assert background_task.status == "completed"
    assert background_task.conclusion == "部署需要 Python 3.12。"
    assert background_task.tool_usage == {"docs_search": 1}

    status_result = await subagent.handle_status_tool(
        tool_ctx, ToolInvocation(tool_name="subagent_status", arguments={"task_id": "subagent-1"}, call_id="call-status")
    )
    assert "已完成" in status_result.content
    assert "部署需要 Python 3.12。" in status_result.content


@pytest.mark.asyncio
async def test_advanced_subagent_can_be_stopped_without_delivering(
    monkeypatch: pytest.MonkeyPatch,
    monitor_events: list[tuple[str, dict[str, Any]]],
) -> None:
    tool_ctx, delivered_tasks = _build_background_tool_ctx(monkeypatch, [])
    release = asyncio.Event()

    async def blocked_sub_agent(**kwargs: Any) -> ChatResponse:
        del kwargs
        await release.wait()
        return _response(text="不应送回")

    tool_ctx.runtime.run_sub_agent = blocked_sub_agent

    await subagent.handle_tool(tool_ctx, _invocation("查点东西", ["docs_search"]))
    background_task = tool_ctx.runtime._subagent_tasks.get("subagent-1")
    await asyncio.sleep(0)

    stop_result = await subagent.handle_stop_tool(
        tool_ctx, ToolInvocation(tool_name="subagent_stop", arguments={"task_id": "subagent-1"}, call_id="call-stop")
    )
    with pytest.raises(asyncio.CancelledError):
        await background_task.asyncio_task

    assert stop_result.success
    assert background_task.status == "stopped"
    assert delivered_tasks == []
    assert monitor_events[-1][0] == "finished"
    assert monitor_events[-1][1]["status"] == "stopped"

    second_stop = await subagent.handle_stop_tool(
        tool_ctx, ToolInvocation(tool_name="subagent_stop", arguments={"task_id": "subagent-1"}, call_id="call-stop-2")
    )
    assert not second_stop.success


@pytest.mark.asyncio
async def test_advanced_subagent_limits_concurrent_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_ctx, _ = _build_background_tool_ctx(monkeypatch, [])
    release = asyncio.Event()

    async def blocked_sub_agent(**kwargs: Any) -> ChatResponse:
        del kwargs
        await release.wait()
        return _response(text="完成")

    tool_ctx.runtime.run_sub_agent = blocked_sub_agent

    results = [
        await subagent.handle_tool(tool_ctx, _invocation(f"任务{index}", ["docs_search"]))
        for index in range(SUBAGENT_MAX_RUNNING_TASKS + 1)
    ]

    assert [result.success for result in results] == [True] * SUBAGENT_MAX_RUNNING_TASKS + [False]
    await tool_ctx.runtime._subagent_tasks.cancel_all()
    assert tool_ctx.runtime._subagent_tasks.running_count == 0


@pytest.mark.asyncio
async def test_advanced_subagent_gets_more_rounds_than_synchronous(
    monkeypatch: pytest.MonkeyPatch,
    monitor_events: list[tuple[str, dict[str, Any]]],
) -> None:
    looping_response = _response(calls=[("docs_search", {"query": "部署"})])
    tool_ctx, _ = _build_background_tool_ctx(
        monkeypatch,
        [*([looping_response] * subagent.SUBAGENT_BACKGROUND_MAX_ROUNDS), _response(text="查到这里为止。")],
    )

    await subagent.handle_tool(tool_ctx, _invocation("查部署要求", ["docs_search"]))
    background_task = tool_ctx.runtime._subagent_tasks.get("subagent-1")
    await background_task.asyncio_task

    assert subagent.SUBAGENT_BACKGROUND_MAX_ROUNDS > subagent.SUBAGENT_MAX_ROUNDS
    assert monitor_events[0][1]["max_rounds"] == subagent.SUBAGENT_BACKGROUND_MAX_ROUNDS
    assert background_task.tool_usage == {"docs_search": subagent.SUBAGENT_BACKGROUND_MAX_ROUNDS}
    assert background_task.conclusion == "查到这里为止。"


def test_runtime_appends_subagent_result_as_latest_user_message() -> None:
    queued_messages: list[str] = []
    runtime = SimpleNamespace(
        _running=True,
        _chat_history=["更早的上下文"],
        log_prefix="[test]",
        _arm_forced_turn_state=lambda *, message_id, reason: False,
        _build_proactive_trigger_message=lambda task_id, description: f"{task_id}:{description}",
        _queue_proactive_turn=queued_messages.append,
    )
    task = SubAgentTask(task_id="subagent-7", description="查部署要求", granted_tools=["docs_search"])
    task.status = "completed"
    task.conclusion = "部署需要 Python 3.12。"
    task.tool_usage = {"docs_search": 2}

    MaisakaHeartFlowChatting.deliver_subagent_result(runtime, task)

    # 结论追加在历史末尾，之前的上下文原样不动
    assert runtime._chat_history[0] == "更早的上下文"
    result_message = runtime._chat_history[-1]
    assert result_message.role == "user"
    assert result_message.source_kind == "subagent_result"
    assert result_message.visible_text.startswith('<subagent_result id="subagent-7" status="completed">')
    assert "部署需要 Python 3.12。" in result_message.visible_text
    assert "docs_search×2" in result_message.visible_text
    assert queued_messages == ["subagent-7:子代理任务结束"]
