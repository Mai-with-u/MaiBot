"""群聊里插件工具拿得到本轮是谁触发的（#2296）。

调用者 = 本轮触发消息的作者：请求模型前就固定，等待期间别人发言不改变它；
主动、静默、超时这类没有真人触发的轮次保持未知；通知、别的会话、机器人自己不算。
"""

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest

from src.core.tooling import ToolInvocation
from src.llm_models.exceptions import ReqAbortException
from src.maisaka import reasoning_engine
from src.maisaka.chat_loop_service import ChatResponse
from src.maisaka.reasoning_engine import MaisakaReasoningEngine, PlannerInterruptResult, TurnStartContext
from src.maisaka.scenes.group.scheduling import GroupTurnScheduler
from src.plugin_runtime.component_query import ComponentQueryService

SESSION = "group-session"
BOT = "10000"


def message(user_id: str, *, session_id: str = SESSION, is_notify: bool = False) -> Any:
    return SimpleNamespace(
        message_id=f"m-{user_id}",
        received_at=None,
        session_id=session_id,
        is_notify=is_notify,
        platform="qq",
        message_info=SimpleNamespace(
            user_info=SimpleNamespace(user_id=user_id),
            group_info=SimpleNamespace(group_id="g1"),
        ),
    )


def make_engine(monkeypatch: pytest.MonkeyPatch) -> MaisakaReasoningEngine:
    monkeypatch.setattr(reasoning_engine, "is_bot_self", lambda platform, user_id: platform == "qq" and user_id == BOT)
    runtime = SimpleNamespace(
        message_cache=[message("A")],
        _last_processed_index=0,
        log_prefix="test",
        _has_forced_turn_trigger=lambda: False,
        _fixed_message_debouncer=SimpleNamespace(consume=Mock()),
    )
    scheduler = GroupTurnScheduler(runtime)
    runtime._message_turn_scheduler = scheduler
    runtime._max_internal_rounds = 100
    runtime._max_context_size = 100
    runtime._chat_history = []
    runtime._last_processed_index = len(runtime.message_cache)
    runtime._get_effective_reply_frequency = lambda: 0.5
    runtime._is_focus_mode_active_for_current_chat = lambda: False
    runtime._chat_loop_service = SimpleNamespace(passive_replyer_override=None)
    runtime._running = True
    runtime._agent_state = "running"
    runtime._STATE_RUNNING = "running"
    runtime._STATE_WAIT = "wait"
    runtime.session_id = SESSION
    runtime._monitor_run_id = "run"
    runtime.chat_stream = SimpleNamespace(is_group_session=True, group_id="g1", user_id=None, platform="qq")
    runtime._enter_stop_state = Mock(side_effect=lambda: setattr(runtime, "_agent_state", "stop"))
    runtime._prepare_tool_prompt_records = Mock(return_value={})
    runtime._render_context_usage_panel = Mock()
    runtime._consume_forced_turn_reason = lambda: None
    runtime._start_planner_continuation = Mock()
    runtime._update_stage_status = Mock()
    runtime._internal_turn_queue = asyncio.Queue()
    engine = MaisakaReasoningEngine(runtime)
    engine._end_cycle = AsyncMock(return_value=SimpleNamespace(time_records={}))
    monkeypatch.setattr("src.maisaka.reasoning_engine.emit_planner_snapshot", AsyncMock())
    return engine


def planner_response() -> ChatResponse:
    return ChatResponse(
        output_items=(),
        request_messages=[],
        selected_history_count=0,
        tool_count=0,
        prompt_tokens=0,
        built_message_count=0,
        completion_tokens=0,
        total_tokens=0,
    )


def plugin_payload(engine: MaisakaReasoningEngine, caller: Any, arguments: Optional[dict] = None) -> dict:
    context = engine._build_tool_execution_context("想法", caller)
    return ComponentQueryService._build_tool_invocation_payload(
        ToolInvocation(tool_name="read_feed", arguments=dict(arguments or {})), context
    )


def test_group_tool_receives_the_turn_author(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    caller = engine._caller_anchor(message("A"))
    payload = plugin_payload(engine, caller)
    assert payload["user_id"] == "A"
    assert payload["group_id"] == "g1"
    # 工具参数里模型给了非空 user_id 的旧插件：仍以参数为准。
    assert plugin_payload(engine, caller, {"user_id": "target"})["user_id"] == "target"
    # 不知道调用者时不编一个。
    assert "user_id" not in plugin_payload(engine, None)


@pytest.mark.parametrize(
    "candidate",
    [
        message("A", is_notify=True),
        message("A", session_id="other-session"),
        message(BOT),
        message(""),
        None,
    ],
    ids=["notify", "other-session", "bot-self", "no-user-id", "none"],
)
def test_not_a_caller(monkeypatch: pytest.MonkeyPatch, candidate: Any) -> None:
    engine = make_engine(monkeypatch)
    assert engine._caller_anchor(candidate) is None


async def drive(
    engine: MaisakaReasoningEngine,
    turn: TurnStartContext,
    pending_by_round: List[list],
    *,
    during_first_request: Optional[Any] = None,
    interrupt_retry: Optional[list] = None,
) -> List[Optional[str]]:
    """跑一个内部循环，返回每次模型请求时这一轮记下的调用者。"""

    runtime = engine._runtime
    runtime._internal_turn_queue.put_nowait("message")
    engine._prepare_turn_start_context = AsyncMock(return_value=turn)
    engine._start_cycle_round = AsyncMock(return_value=(SimpleNamespace(cycle_id=1, time_records={}), "round"))
    engine._refresh_visual_placeholders_for_cycle = AsyncMock()
    engine._post_process_chat_history_after_cycle = AsyncMock()
    pending = list(pending_by_round)
    engine._ingest_pending_messages = AsyncMock(side_effect=lambda: pending.pop(0) if pending else [])
    rounds = len(pending_by_round) + 1
    callers: List[Optional[str]] = []
    interrupted = [interrupt_retry is not None]

    async def request(**kwargs: Any) -> None:
        state = kwargs["state"]
        callers.append(state.caller_message.message_info.user_info.user_id if state.caller_message else None)
        if during_first_request is not None and len(callers) == 1:
            runtime.message_cache.append(during_first_request)
        if interrupted[0]:
            interrupted[0] = False
            raise ReqAbortException("新消息打断")
        state.response = planner_response()

    async def actions(**kwargs: Any) -> bool:
        # 工具执行阶段用的是这一轮 state 上的调用者，而不是此刻最新的消息。
        assert kwargs["state"].caller_message is None or kwargs["state"].caller_message.session_id == SESSION
        return len(callers) >= rounds

    engine._run_planner_request = AsyncMock(side_effect=request)
    engine._handle_planner_response_actions = AsyncMock(side_effect=actions)
    engine._handle_planner_interrupt = AsyncMock(
        return_value=PlannerInterruptResult(response=planner_response(), extra_lines=[], retry_messages=interrupt_retry or [])
    )
    scheduler = runtime._message_turn_scheduler
    scheduler.finish_planner_turn = lambda: setattr(runtime, "_running", False)
    await asyncio.wait_for(engine.run_loop(), timeout=2)
    return callers


@pytest.mark.asyncio
async def test_caller_is_fixed_before_the_request_and_follows_new_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    a, b = message("A"), message("B")
    callers = await drive(
        engine,
        TurnStartContext([a], a, False, False, False),
        # 第 2 轮没有新消息（工具结果回来接着想），第 3 轮带着 B 的新消息。
        [[], [b]],
        during_first_request=b,
    )
    # 等模型期间 B 发言，第 1 轮仍是 A；没有新消息的后续轮次沿用 A；带着 B 的新消息时换成 B。
    assert callers == ["A", "A", "B"]


@pytest.mark.asyncio
async def test_proactive_turn_has_no_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    callers = await drive(engine, TurnStartContext([], message("A"), False, True, False), [])
    assert callers == [None]


@pytest.mark.asyncio
async def test_newest_message_from_bot_or_notify_leaves_caller_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    a = message("A")
    callers = await drive(engine, TurnStartContext([a], a, False, False, False), [[message(BOT)]])
    assert callers == ["A", None]


@pytest.mark.asyncio
async def test_interrupt_retry_takes_the_interrupting_message(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(monkeypatch)
    a, c = message("A"), message("C")
    callers = await drive(engine, TurnStartContext([a], a, False, False, False), [], interrupt_retry=[c])
    assert callers == ["A", "C"]


@pytest.mark.asyncio
async def test_subagent_calls_tools_for_the_turn_that_dispatched_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.tooling import ToolExecutionContext
    from src.maisaka.builtin_tool import subagent

    engine = make_engine(monkeypatch)
    started = {}
    runtime = engine._runtime
    runtime._tool_registry = SimpleNamespace(
        list_tools=AsyncMock(return_value=[SimpleNamespace(name="read_feed", enabled=True, annotation=None)])
    )
    runtime._subagent_tasks = SimpleNamespace(
        running_count=0,
        allocate_task_id=lambda: "t1",
        start=lambda **kwargs: started.update(kwargs) or SimpleNamespace(task_id="t1"),
    )
    runtime.deliver_subagent_result = Mock()
    monkeypatch.setattr(subagent, "emit_subagent_started", AsyncMock())
    run_and_report = AsyncMock()
    monkeypatch.setattr(subagent, "_run_and_report", run_and_report)
    tool_ctx = SimpleNamespace(
        runtime=runtime,
        engine=engine,
        build_failure_result=Mock(side_effect=AssertionError("不应失败")),
        build_success_result=Mock(),
    )
    caller = engine._caller_anchor(message("A"))
    invocation = ToolInvocation(tool_name="subagent", arguments={"task": "查说说", "tools": ["read_feed"]})

    await subagent.handle_tool(tool_ctx, invocation, ToolExecutionContext(metadata={"anchor_message": caller}))
    await started["runner"](SimpleNamespace(tool_usage={}))
    assert run_and_report.await_args.kwargs["caller_message"] is caller
