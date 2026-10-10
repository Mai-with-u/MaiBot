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
