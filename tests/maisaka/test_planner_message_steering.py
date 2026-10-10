"""隔离配置初始化，验证实际请求方法的取消、重试与流式覆盖行为。"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, Set
from unittest.mock import AsyncMock, Mock

import ast
import asyncio
import copy
import inspect
import time

import pytest

from src.llm_models.exceptions import InterruptedStreamOutput, ReqAbortException


def load_methods(path: str, class_name: Optional[str], names: Set[str], namespace: Dict[str, Any]) -> Any:
    """执行源文件中的实际方法，避免导入应用时改写本地配置或启动数据库。"""

    source_path = Path(__file__).resolve().parents[2] / path
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    if class_name is None:
        body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
    else:
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
        node.bases = []
        node.body = [
            method
            for method in node.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name in names
        ]
        body = [node]
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *body],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace[class_name] if class_name else namespace[next(iter(names))]


@pytest.fixture
def interruptible():
    return load_methods(
        "src/llm_models/model_client/adapter_base.py",
        None,
        {"await_task_with_interrupt"},
        {"asyncio": asyncio, "logger": Mock()},
    )


@pytest.mark.asyncio
async def test_interrupt_cancels_a_request_without_stream_events(interruptible):
    started, cleaned, flag = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def stalled_request():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    request = asyncio.create_task(stalled_request())
    waiting = asyncio.create_task(interruptible(request, flag))
    await started.wait()
    flag.set()
    with pytest.raises(ReqAbortException):
        await asyncio.wait_for(waiting, timeout=0.5)
    assert request.cancelled()
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_interrupt_wins_when_output_is_already_completed(interruptible):
    flag = asyncio.Event()
    request = asyncio.create_task(asyncio.sleep(0, result="stale tool call"))
    await request
    flag.set()
    with pytest.raises(ReqAbortException):
        await interruptible(request, flag)


@pytest.mark.asyncio
async def test_preset_interrupt_prevents_request_from_starting(interruptible):
    flag, started = asyncio.Event(), Mock()
    flag.set()

    async def request_body():
        started()

    with pytest.raises(ReqAbortException):
        await interruptible(asyncio.create_task(request_body()), flag)
    started.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_flag", [False, True])
async def test_outer_cancellation_cleans_up_child_request(interruptible, with_flag):
    request = asyncio.create_task(asyncio.Event().wait())
    waiting = asyncio.create_task(interruptible(request, asyncio.Event() if with_flag else None))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert request.cancelled()


@pytest.mark.asyncio
async def test_normal_response_is_preserved(interruptible):
    assert await interruptible(asyncio.create_task(asyncio.sleep(0, result="ok")), asyncio.Event()) == "ok"


@pytest.mark.asyncio
async def test_nested_cancellation_preserves_stream_fragments(interruptible):
    """真实流处理器在外层 Planner 取消请求时，仍应交出已收到的片段。"""

    partial = InterruptedStreamOutput("推理到一半", "正文到一半", [{"name": "reply", "arguments": '{"text":'}], "deepseek")
    accumulator = SimpleNamespace(
        capture_event_metadata=Mock(), process_delta=Mock(),
        snapshot_interrupted_output=Mock(return_value=partial), close=Mock(),
    )
    handler = load_methods(
        "src/llm_models/model_client/openai_client.py", None, {"_default_stream_response_handler"},
        {"asyncio": asyncio, "ReqAbortException": ReqAbortException,
         "_OpenAIStreamAccumulator": Mock(return_value=accumulator), "_extract_usage_record": Mock(return_value=None)},
    )
    consumed, flag = asyncio.Event(), asyncio.Event()

    async def stream():
        yield SimpleNamespace(choices=[SimpleNamespace(delta="chunk")])
        consumed.set()
        await asyncio.Event().wait()

    async def provider():
        return await handler(
            stream(), flag, reasoning_parse_mode=None, tool_argument_parse_mode=None,
            reasoning_key="reasoning_content", logical_turn_id="turn", max_tokens=None, trace_context=None,
        )

    async def planner():
        return await interruptible(asyncio.create_task(provider()), flag)

    waiting = asyncio.create_task(interruptible(asyncio.create_task(planner()), flag))
    await consumed.wait()
    flag.set()
    with pytest.raises(ReqAbortException) as result:
        await asyncio.wait_for(waiting, timeout=0.5)
    assert result.value.partial_output is partial
    assert '{"text":' in partial.display_text()
    accumulator.close.assert_called_once()
    accumulator.process_delta.assert_called_once_with("chunk")


def test_snapshot_keeps_incomplete_tool_arguments():
    """从实际累积器提取原始工具参数，不能调用完整响应解析。"""

    import io

    accumulator_type = load_methods(
        "src/llm_models/model_client/openai_client.py", "_OpenAIStreamAccumulator",
        {"snapshot_interrupted_output"}, {"InterruptedStreamOutput": InterruptedStreamOutput},
    )
    accumulator = accumulator_type()
    accumulator.reasoning_buffer = io.StringIO("reasoning fragment")
    accumulator.content_buffer = io.StringIO("content fragment")
    accumulator.model_name = "deepseek"
    accumulator.tool_call_states = {0: SimpleNamespace(function_name="reply", arguments_buffer=io.StringIO('{"text":'))}
    partial = accumulator.snapshot_interrupted_output()
    assert partial.reasoning == "reasoning fragment"
    assert partial.content == "content fragment"
    assert partial.tool_calls == [{"name": "reply", "arguments": '{"text":'}]


@pytest.mark.asyncio
@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_planner_discards_interrupted_output_and_restarts_with_new_messages(interruptible, suppress_cancel):
    namespace = {
        "asyncio": asyncio,
        "ReqAbortException": ReqAbortException,
        "await_task_with_interrupt": interruptible,
        "global_config": SimpleNamespace(experimental=SimpleNamespace(planner_message_steering=True)),
        "logger": Mock(),
    }
    engine_class = load_methods(
        "src/maisaka/reasoning_engine.py",
        "MaisakaReasoningEngine",
        {"_run_interruptible_planner", "_ingest_pending_steering_messages"},
        namespace,
    )
    pending, requests = [], []
    started = asyncio.Event()
    runtime = SimpleNamespace(
        _chat_history=["initial"],
        _max_context_size=10,
        log_prefix="[test]",
        _has_pending_messages=lambda: bool(pending),
        _cycle_counter=1,
        _mark_message_turn_unscheduled=Mock(),
        _clear_message_debounce_required=Mock(),
        _unbind_planner_interrupt_flag=Mock(),
    )
    runtime._bind_planner_interrupt_flag = lambda flag: setattr(runtime, "flag", flag)

    async def planner(history, **kwargs):
        requests.append((list(history), kwargs["logical_turn_id"]))
        if len(requests) == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if suppress_cancel:
                    return "stale tool call"
                raise
        return "fresh tool call"

    def collect():
        messages = pending[:]
        pending.clear()
        return messages

    runtime._collect_pending_messages = collect
    runtime._chat_loop_service = SimpleNamespace(chat_loop_step=planner, set_interrupt_flag=Mock())
    engine = engine_class()
    engine._runtime, engine._active_logical_turn_id = runtime, "same-turn"
    engine._ingest_messages = AsyncMock(side_effect=lambda messages: runtime._chat_history.extend(messages))
    engine._emit_flow_step = AsyncMock()
    first = asyncio.create_task(engine._run_interruptible_planner())
    await started.wait()
    pending.extend(["new-1", "new-2"])
    runtime.flag.set()
    with pytest.raises(ReqAbortException):
        await asyncio.wait_for(first, timeout=0.5)
    assert await engine._ingest_pending_steering_messages() == ["new-1", "new-2"]
    assert await engine._run_interruptible_planner() == "fresh tool call"
    assert requests == [(["initial"], "same-turn"), (["initial", "new-1", "new-2"], "same-turn")]
    assert runtime._unbind_planner_interrupt_flag.call_args_list[0].kwargs["interrupted"] is True
    assert runtime._unbind_planner_interrupt_flag.call_args_list[1].kwargs["interrupted"] is False


def test_steering_bypasses_interrupt_limit_and_coalesces_each_request():
    config = SimpleNamespace(experimental=SimpleNamespace(planner_message_steering=True))
    namespace = {"asyncio": asyncio, "global_config": config, "logger": Mock(), "time": time}
    controller_class = load_methods(
        "src/maisaka/runtime.py",
        "PlannerInterruptController",
        {"__init__", "bind", "unbind", "request", "consecutive_count"},
        namespace,
    )
    runtime_class = load_methods(
        "src/maisaka/runtime.py", "MaisakaHeartFlowChatting", {"_request_planner_interrupt_for_message"}, namespace
    )
    controller = controller_class()
    runtime = SimpleNamespace(
        _planner_interrupt=controller,
        _planner_interrupt_max_consecutive_count=0,
        _agent_state="running",
        _STATE_RUNNING="running",
        log_prefix="[test]",
        message_cache=[],
    )
    for _ in range(5):
        flag = asyncio.Event()
        controller.bind(flag)
        runtime_class._request_planner_interrupt_for_message(runtime, SimpleNamespace(message_id="new"))
        assert flag.is_set()
        count = controller.consecutive_count
        runtime_class._request_planner_interrupt_for_message(runtime, SimpleNamespace(message_id="another"))
        assert controller.consecutive_count == count
        controller.unbind(flag, interrupted=True)
    config.experimental.planner_message_steering = False
    flag = asyncio.Event()
    controller.bind(flag)
    runtime_class._request_planner_interrupt_for_message(runtime, SimpleNamespace(message_id="disabled"))
    assert not flag.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_interrupted_planner_ingests_messages_once_and_preserves_wait_when_disabled(enabled):
    builder = Mock()
    for name in ("set_role", "set_meta", "add_text_content"):
        getattr(builder, name).return_value = builder
    namespace = {
        "time": time,
        "logger": Mock(),
        "global_config": SimpleNamespace(experimental=SimpleNamespace(planner_message_steering=enabled)),
        "ContextItemBuilder": lambda: builder,
        "ContextItemMeta": Mock(),
        "RoleType": SimpleNamespace(Assistant="assistant"),
        "ChatResponse": SimpleNamespace,
        "PlannerInterruptResult": lambda response, extra_lines, retry_messages: SimpleNamespace(
            retry_messages=retry_messages
        ),
    }
    engine_class = load_methods(
        "src/maisaka/reasoning_engine.py",
        "MaisakaReasoningEngine",
        {"_handle_planner_interrupt", "_ingest_pending_steering_messages"},
        namespace,
    )
    messages = [object(), object()]
    runtime = SimpleNamespace(
        _update_stage_status=Mock(),
        _chat_history=[],
        _max_internal_rounds=4,
        _has_pending_messages=lambda: True,
        _wait_for_message_quiet_period=AsyncMock(),
        _mark_message_turn_unscheduled=Mock(),
        _clear_message_debounce_required=Mock(),
        _collect_pending_messages=Mock(return_value=messages),
        _cycle_counter=1,
        log_prefix="[test]",
    )
    engine = engine_class()
    engine._runtime, engine._active_logical_turn_id = runtime, "same-turn"
    engine._ingest_messages, engine._emit_flow_step = AsyncMock(), AsyncMock()
    result = await engine._handle_planner_interrupt(
        exc=ReqAbortException("new message"),
        round_index=0,
        round_text="test",
        current_stage_started_at=time.time(),
        action_tool_count=1,
    )
    assert result.retry_messages == messages
    runtime._collect_pending_messages.assert_called_once()
    engine._ingest_messages.assert_awaited_once_with(messages)
    if enabled:
        runtime._wait_for_message_quiet_period.assert_not_awaited()
        engine._emit_flow_step.assert_awaited_once()
    else:
        runtime._wait_for_message_quiet_period.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("force_stream", [False, True])
async def test_stream_override_is_request_scoped(force_stream):
    model = SimpleNamespace(name="dsv4", force_stream_mode=False)
    model.model_copy = lambda update: SimpleNamespace(**{**vars(model), **update})
    namespace = {
        "time": time,
        "datetime": datetime,
        "inspect": inspect,
        "asyncio": asyncio,
        "logger": Mock(),
        "RequestType": SimpleNamespace(RESPONSE="response"),
        "RequestTraceContext": lambda **kwargs: SimpleNamespace(current_attempt_started_at=0, **kwargs),
        "LLMExecutionResult": SimpleNamespace,
        "normalize_context_images": copy.copy,
        "ReqAbortException": ReqAbortException,
    }
    orchestrator_class = load_methods(
        "src/llm_models/utils_model.py", "LLMOrchestrator", {"_execute_request"}, namespace
    )
    orchestrator = orchestrator_class()
    orchestrator.task_name, orchestrator.request_type = "planner", "maisaka.planner"
    orchestrator.model_for_task = SimpleNamespace(model_list=["dsv4"])
    orchestrator._resolve_effective_session_id = lambda value: value
    orchestrator._select_model = Mock(return_value=(model, object(), object()))
    orchestrator._build_client_request = Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
    orchestrator._attempt_request_on_model_with_timeout = AsyncMock(return_value=SimpleNamespace(usage=None))
    orchestrator._adjust_model_usage = Mock()
    result = await orchestrator._execute_request(
        "response", context_factory=lambda client: ["message"], force_stream_mode=force_stream
    )
    assert result.model_info.force_stream_mode is force_stream
    assert model.force_stream_mode is False
    request = orchestrator._attempt_request_on_model_with_timeout.call_args.args[2]
    assert request.model_info is result.model_info


def build_wait_runtime(enabled: bool, is_group: bool):
    """连接实际 wait 状态、消息调度与恢复方法，隔离其他启动依赖。"""

    namespace = {
        "asyncio": asyncio,
        "time": time,
        "datetime": datetime,
        "logger": Mock(),
        "global_config": SimpleNamespace(experimental=SimpleNamespace(planner_message_steering=enabled)),
        "focus_mode_manager": SimpleNamespace(can_decide=lambda *args, **kwargs: True),
        "is_dynamic_reply_trigger_enabled": lambda: False,
        "ToolResultMessage": SimpleNamespace,
        "TurnStartContext": lambda messages, trigger, timeout, proactive, silent, turn_id=None: SimpleNamespace(
            cached_messages=messages, trigger_message=trigger, logical_turn_id=turn_id
        ),
    }
    runtime_class = load_methods(
        "src/maisaka/runtime.py",
        "MaisakaHeartFlowChatting",
        {
            "_enter_wait_state",
            "_enter_running_state",
            "_cancel_wait_timeout_task",
            "_schedule_wait_timeout",
            "_consume_pending_wait_state",
            "_has_pending_wait_tool_call",
        },
        namespace,
    )
    scheduler_class = load_methods(
        "src/maisaka/turn_trigger/scheduler.py", "MessageTurnScheduler", {"schedule_message_turn"}, namespace
    )
    engine_class = load_methods(
        "src/maisaka/reasoning_engine.py",
        "MaisakaReasoningEngine",
        {"_prepare_turn_start_context", "_drain_ready_turn_triggers", "_build_wait_completed_message"},
        namespace,
    )
    runtime = runtime_class()
    runtime._STATE_RUNNING, runtime._STATE_WAIT = "running", "wait"
    runtime._agent_state, runtime._running = "running", True
    runtime.session_id, runtime.log_prefix = "test", "[test]"
    runtime.chat_stream = SimpleNamespace(is_group_session=is_group)
    runtime._wait_timeout_task = None
    runtime._internal_turn_queue, runtime._chat_history, pending = asyncio.Queue(), [], []
    runtime._is_reply_frequency_silent = lambda: False
    runtime._has_pending_messages = lambda: bool(pending)
    runtime._get_pending_message_count = lambda: len(pending)
    runtime._cancel_deferred_message_turn_task = Mock()
    runtime._clear_message_debounce_required = Mock()
    runtime._wait_for_message_quiet_period = AsyncMock()
    runtime._update_stage_status = Mock()
    runtime._mark_message_turn_unscheduled = lambda: setattr(runtime, "_message_turn_scheduled", False)

    def enqueue():
        runtime._message_turn_scheduled = True
        runtime._internal_turn_queue.put_nowait("message")

    def collect():
        batch = pending[:]
        pending.clear()
        return batch

    runtime._enqueue_message_turn = enqueue
    runtime._collect_pending_messages = collect
    scheduler = scheduler_class()
    scheduler._runtime = runtime
    runtime._schedule_message_turn = scheduler.schedule_message_turn
    engine = engine_class()
    engine._runtime = runtime
    engine._ingest_messages = AsyncMock(side_effect=lambda messages: runtime._chat_history.extend(messages))
    return runtime, engine, pending


@pytest.mark.asyncio
@pytest.mark.parametrize("is_group", [False, True])
@pytest.mark.parametrize("message_before_wait", [False, True])
async def test_quick_mode_wakes_wait_and_completes_original_tool_call(is_group, message_before_wait):
    runtime, engine, pending = build_wait_runtime(True, is_group)
    message = object()
    if message_before_wait:
        pending.append(message)
    runtime._enter_wait_state(seconds=30, tool_call_id="wait-1", logical_turn_id="original-turn")
    if not message_before_wait:
        assert runtime._wait_timeout_task is not None
        pending.append(message)
        runtime._schedule_message_turn()
    assert runtime._wait_timeout_task is None
    # 保留 wait 状态直到原工具批次收尾，防止外层循环清掉未补齐的工具关联。
    assert runtime._agent_state == "wait"
    assert runtime._has_pending_wait_tool_call()
    runtime._schedule_message_turn()
    assert runtime._internal_turn_queue.qsize() == 1
    turn = await engine._prepare_turn_start_context(runtime._internal_turn_queue.get_nowait())
    assert runtime._agent_state == "running"
    assert turn.logical_turn_id == "original-turn"
    assert turn.cached_messages == [message]
    assert runtime._chat_history[0].tool_call_id == "wait-1"
    assert runtime._chat_history[0].logical_turn_id == "original-turn"
    assert "新的用户输入" in runtime._chat_history[0].content
    assert runtime._chat_history[1] is message
    assert not runtime._has_pending_wait_tool_call()
    runtime._wait_for_message_quiet_period.assert_not_awaited()
    await asyncio.sleep(0)
    assert runtime._internal_turn_queue.empty()


@pytest.mark.asyncio
async def test_disabled_quick_mode_keeps_group_wait_until_timeout():
    runtime, engine, pending = build_wait_runtime(False, True)
    runtime._enter_wait_state(seconds=30, tool_call_id="wait-1", logical_turn_id="original-turn")
    pending.append(object())
    runtime._schedule_message_turn()
    assert runtime._agent_state == "wait"
    assert runtime._internal_turn_queue.empty()
    assert runtime._wait_timeout_task is not None
    turn = await engine._prepare_turn_start_context("message")
    assert turn.trigger_message is None
    assert runtime._has_pending_wait_tool_call()
    runtime._cancel_wait_timeout_task()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_quick_mode_does_not_wake_wait_without_messages():
    runtime, _, _ = build_wait_runtime(True, True)
    runtime._enter_wait_state(seconds=30, tool_call_id="wait-1", logical_turn_id="original-turn")
    timer = runtime._wait_timeout_task
    runtime._schedule_message_turn()
    assert runtime._agent_state == "wait"
    assert runtime._wait_timeout_task is timer
    assert runtime._internal_turn_queue.empty()
    runtime._cancel_wait_timeout_task()
    await asyncio.sleep(0)
