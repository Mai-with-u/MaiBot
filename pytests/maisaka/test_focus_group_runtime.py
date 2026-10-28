"""Focus 共享思考的跨会话路由、等待、并发输入和恢复边界。"""

from base64 import b64decode
from datetime import datetime
from io import BytesIO
from threading import Event as ThreadEvent
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, Mock

from PIL import Image

import asyncio
import json
import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.config.config import global_config
from src.core.tooling import ToolExecutionResult, ToolRegistry, ToolSpec
from src.files import service as file_service_module
from src.llm_models.exceptions import (
    EmptyResponseException,
    LLMTaskTimeoutError,
    NetworkConnectionError,
    ReqAbortException,
    RespNotOkException,
    RespParseException,
)
from src.llm_models.payload_content.tool_option import ToolCall
from src.llm_models.payload_content.context_item import (
    ContextImagePart,
    ContextItemMeta,
    ReasoningItem,
    ReasoningRepresentation,
    RoleType,
)
from src.maisaka.chat_loop_service import ChatResponse
from src.maisaka.context.messages import (
    InternalContextMessage,
    ModelOutputContextMessage,
    SessionBackedMessage,
    ToolResultMessage,
)
from src.maisaka.context.emoji_candidates import EmojiCandidateMessage
from src.maisaka.context import compaction as compaction_module
from src.maisaka.context.compaction import ContextSummaryMessage, PlannerRequestSnapshot
from src.maisaka.focus import group_runtime as group_module
from src.maisaka.focus import context as context_module
from src.maisaka.focus import manager as manager_module
from src.maisaka.focus.context import FocusCheckpointStore, FocusContextEntry
from src.maisaka.focus.group_runtime import FocusGroupRuntime
from src.maisaka.focus.manager import FocusModeManager
from src.maisaka.focus.tools import routed_schema
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting
from src.plugin_runtime import tool_provider as plugin_provider_module
from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import ComponentRegistry
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.tool_provider import PluginToolProvider


def _history(text: str) -> InternalContextMessage:
    return InternalContextMessage(
        raw_message=MessageSequence([TextComponent(text)]),
        visible_text=text,
        timestamp=datetime.now(),
    )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("representation", list(ReasoningRepresentation))
def test_checkpoint_restores_reasoning_representation(tmp_path, legacy, representation):
    item = ReasoningItem(
        meta=ContextItemMeta.create(logical_turn_id="reasoning-turn"),
        summary_parts=("已有摘要",),
        text_parts=("持续思考",),
        representation=representation,
    )
    store = FocusCheckpointStore("reasoning-restart", tmp_path)
    store.save([FocusContextEntry([ModelOutputContextMessage(item)], {"A", "B"})], {})
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    encoded = payload["entries"][0]["messages"][0]["item"]["fields"]["representation"]
    assert encoded["$enum_type"] == "ReasoningRepresentation"
    if legacy:
        del encoded["$enum_type"]
        store.path.write_text(json.dumps(payload), encoding="utf-8")
    restored = store.load()["entries"][0]
    assert restored.session_ids == {"A", "B"}
    assert restored.messages[0].output_item == item
    assert restored.messages[0].output_item.representation is representation


def test_checkpoint_preserves_emoji_visual_policy(tmp_path):
    emoji_buffer = BytesIO()
    Image.new("RGB", (500, 500), "red").save(emoji_buffer, format="GIF")
    message = _history("表情包")
    message.raw_message = MessageSequence([]).emoji(emoji_buffer.getvalue(), content="表情描述")
    store = FocusCheckpointStore("emoji-restart", tmp_path)
    store.save([FocusContextEntry([message], {"A"})], {})
    restored = store.load()["entries"][0].messages[0]
    parts = restored.to_context_item().parts
    assert len(parts) == 1
    assert isinstance(parts[0], ContextImagePart)
    with Image.open(BytesIO(b64decode(parts[0].image_base64))) as image:
        assert image.size == (256, 256)


@pytest.mark.parametrize("role", list(RoleType))
def test_checkpoint_role_enum_keeps_its_type(role):
    assert context_module._decode(context_module._encode(role)) is role
    assert context_module._decode({"$enum": role.value}) is role


def test_checkpoint_rejects_unknown_enum_type_and_invalid_member():
    with pytest.raises(KeyError):
        context_module._decode({"$enum": "raw_text", "$enum_type": "UnknownEnum"})
    with pytest.raises(ValueError):
        context_module._decode({"$enum": "raw_text", "$enum_type": "RoleType"})


def _incoming(sid: str, message_id: str, *, at: bool = False) -> SessionMessage:
    message = SessionMessage(message_id, datetime.now(), "test", received_at=datetime.now())
    message.session_id = sid
    message.message_info = MessageInfo(UserInfo("user-" + sid, "User " + sid))
    message.raw_message = MessageSequence([TextComponent(message_id)])
    message.processed_plain_text = message_id
    message.is_at = at
    return message


def _chat_history(text: str, sid: str = "A", source: str = "user") -> SessionBackedMessage:
    original = _incoming(sid, text)
    return SessionBackedMessage.from_session_message(
        original, raw_message=original.raw_message, visible_text=text, source_kind=source,
    )


def _response(text: str = "结束本轮思考", calls: List[ToolCall] | None = None) -> ChatResponse:
    result = LLMResponseResult.from_portable_output(response=text, tool_calls=calls or [])
    return ChatResponse(
        output_items=result.output_items,
        request_messages=[],
        selected_history_count=0,
        tool_count=len(calls or []),
        prompt_tokens=0,
        built_message_count=0,
        completion_tokens=0,
        total_tokens=0,
    )


class RecordingProvider:
    provider_name = "recording"
    provider_type = "plugin"

    def __init__(self, runtime, trace: List[Any]) -> None:
        self.runtime = runtime
        self.trace = trace
        self.specs = [
            ToolSpec(
                name="echo",
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                parameters_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                metadata={"visibility": "visible"},
            ),
            ToolSpec(
                name="wait",
                provider_name="maisaka_builtin",
                provider_type="builtin",
                parameters_schema={
                    "type": "object",
                    "properties": {"seconds": {"type": "integer"}},
                    "required": ["seconds"],
                },
                metadata={"builtin_stage": "both", "visibility": "visible"},
            ),
            ToolSpec(
                name="stop",
                provider_name="maisaka_builtin",
                provider_type="builtin",
                parameters_schema={"type": "object", "properties": {}},
                metadata={"builtin_stage": "action", "visibility": "visible"},
            ),
        ]

    async def list_tools(self, context=None):
        return self.specs

    async def invoke(self, invocation, context=None):
        self.trace.append(
            (context.session_id, invocation.tool_name, dict(invocation.arguments), list(self.runtime._chat_history))
        )
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, content=context.session_id)

    async def close(self):
        pass


class ChatServices:
    _STATE_RUNNING = "running"
    _STATE_WAIT = "wait"
    _STATE_STOP = "stop"
    update_deferred_tool_specs = MaisakaHeartFlowChatting.update_deferred_tool_specs
    search_deferred_tool_specs = MaisakaHeartFlowChatting.search_deferred_tool_specs
    discover_deferred_tools = MaisakaHeartFlowChatting.discover_deferred_tools

    def __init__(self, sid: str, group: FocusGroupRuntime, trace: List[Any]) -> None:
        self.session_id = sid
        self.session_name = "聊天 " + sid
        self.log_prefix = "[" + sid + "]"
        self.chat_stream = SimpleNamespace(
            session_id=sid, is_group_session=True, group_id=sid, user_id=None, platform="test"
        )
        self._running = True
        self._message_ingest_lock = asyncio.Lock()
        self._focus_group = group
        self._internal_loop_task = None
        self.message_cache = []
        self._last_processed_index = 0
        self._chat_history = [_history("只属于 " + sid)]
        self._agent_state = "stop"
        self._max_context_size = 10
        self.discovered_tool_names = set()
        self.deferred_tool_specs_by_name = {}
        self._update_stage_status = Mock()
        self._wait_for_message_quiet_period = AsyncMock()
        self._collect_pending_messages = Mock()
        self._clear_forced_turn_state = Mock()
        self._trigger_trimmed_history_learning = AsyncMock()
        self._stop_background_subagents = AsyncMock()
        self._clear_wait_state = Mock()
        self._ensure_background_tasks_running = Mock()
        self._schedule_message_turn = Mock()
        self._enter_stop_state = Mock()
        self.find_source_message_by_id = lambda mid: next(
            (msg for msg in self.message_cache if msg.message_id == mid), None
        )
        self.expand_message_id_aliases = lambda args: args
        self._reasoning_engine = MaisakaReasoningEngine(self)
        self._reasoning_engine._record_tool_execution_effects = AsyncMock()
        self._tool_registry = ToolRegistry()
        self.provider = RecordingProvider(self, trace)
        self._tool_registry.register_provider(self.provider)


@pytest.fixture
def setup_group(monkeypatch, tmp_path):
    group = FocusGroupRuntime("test-group")
    group._store = FocusCheckpointStore(group.scope_id, tmp_path)
    group.checkpoint = AsyncMock()
    group._emit_response = AsyncMock()
    group._roster = lambda: "A 和 B 的真实聊天列表"
    group.is_member_valid = lambda runtime: runtime._running and runtime._focus_group is group
    trace: List[Any] = []
    members = {sid: ChatServices(sid, group, trace) for sid in ("A", "B")}
    for runtime in members.values():
        group.add_member(runtime)
    monkeypatch.setattr(global_config.capabilities, "tool_result_condense", False)
    monkeypatch.setattr(group_module, "emit_flow_step", AsyncMock())
    return group, members, trace


@pytest.mark.asyncio
async def test_planner_failure_stops_members_and_only_new_input_recovers(setup_group):
    group, members, _ = setup_group
    group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    group._build_references = AsyncMock(return_value=[])
    group._service.chat_loop_step = AsyncMock(side_effect=TimeoutError("Planner 重试耗尽"))
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    first_worker = group._worker
    await asyncio.wait_for(asyncio.shield(first_worker), 2)
    assert group.state == "failed"
    assert all(member._agent_state == member._STATE_STOP for member in members.values())
    assert group._considered == {}
    assert group._store.load()["seen"] == ["A\0first"]

    # 就绪事件、后台任务和重复投递均不能重启原失败周期。
    group._event.set()
    group.register_proactive(members["B"], _incoming("B", "background"))
    await group._checkpoint_task
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("重复消息"))
    group._ensure_worker()
    assert group._worker is first_worker
    assert group._service.chat_loop_step.await_count == 1

    recovered = asyncio.Event()

    async def planner(*args, **kwargs):
        recovered.set()
        return _response()

    group._service.chat_loop_step = planner
    await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
    await asyncio.wait_for(recovered.wait(), 2)
    assert group._worker is not first_worker
    assert not group._failed
    assert group._considered == {"A": 1, "B": 3}
    assert group.has_message("A", "first")
    assert all(text in [message.processed_plain_text for message in group.history] for text in ("first", "new"))
    await group.stop()


@pytest.mark.asyncio
async def test_each_new_input_allows_only_one_failed_worker(setup_group):
    group, members, _ = setup_group
    group._build_references = AsyncMock(return_value=[])
    planner = AsyncMock(side_effect=TimeoutError("持续故障"))
    group._service.chat_loop_step = planner
    workers = []
    for index in range(3):
        await group.register_message(
            members["A"],
            _incoming("A", str(index), at=True),
            _chat_history(str(index)),
        )
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        workers.append(worker)
        group._ensure_worker()
        await asyncio.sleep(0)
        assert group._worker is worker
        assert planner.await_count == index + 1
        assert group.state == "failed"
    assert len(set(workers)) == 3
    assert group._considered == {}
    await group.stop()


@pytest.mark.asyncio
async def test_normal_recovery_input_is_in_first_snapshot_even_before_debounce(setup_group):
    group, members, _ = setup_group
    group._build_references = AsyncMock(return_value=[])
    group._service.chat_loop_step = AsyncMock(side_effect=TimeoutError("初次失败"))
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    await asyncio.wait_for(asyncio.shield(group._worker), 2)
    worker = group._worker
    # 没有可进入快照的新上下文时不能开放恢复。
    await group.register_message(members["B"], _incoming("B", "empty"), None)
    assert group._worker is worker
    await group._debounce_tasks["B"]
    members["B"]._wait_for_message_quiet_period = asyncio.Event().wait
    recovered = asyncio.Event()
    requests = []

    async def planner(history, **kwargs):
        requests.append(list(history))
        recovered.set()
        return _response()

    group._service.chat_loop_step = planner
    await group.register_message(members["B"], _incoming("B", "normal"), _chat_history("normal", "B"))
    await asyncio.wait_for(recovered.wait(), 2)
    assert not group._debounce_tasks["B"].done()
    assert any(message.processed_plain_text == "normal" for message in requests[0])
    assert len(requests) == 1
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        NetworkConnectionError("网络失败"),
        RespNotOkException(503),
        EmptyResponseException(),
        RespParseException(),
        LLMTaskTimeoutError("planner", "test", 1),
    ],
)
async def test_known_final_model_errors_recover_on_normal_message(setup_group, error):
    group, members, _ = setup_group
    group._build_references = AsyncMock(return_value=[])
    planner = AsyncMock(side_effect=[error, _response()])
    group._service.chat_loop_step = planner
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    await asyncio.wait_for(asyncio.shield(group._worker), 2)
    assert group.state == "failed"
    await group.register_message(members["A"], _incoming("A", "new"), _chat_history("new"))
    # 用同一 Worker 的空闲状态作为本轮已完成的屏障，不依赖固定 sleep 时长。
    idle = asyncio.Event()
    members["A"]._update_stage_status.side_effect = lambda stage, *a, **kw: idle.set() if stage == "空闲" else None
    await asyncio.wait_for(idle.wait(), 2)
    assert planner.await_count == 2
    assert group.state == "stop"
    assert group._considered == {"A": 2}
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [RuntimeError("程序错误"), ValueError("非法状态"), OSError("存储错误"), ReqAbortException()]
)
async def test_unknown_errors_and_request_abort_are_not_recoverable(setup_group, error):
    group, members, _ = setup_group
    group._build_references = AsyncMock(return_value=[])
    planner = AsyncMock(side_effect=error)
    group._service.chat_loop_step = planner
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    worker = group._worker
    await asyncio.wait_for(asyncio.shield(worker), 2)
    await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
    assert group.state == "failed"
    assert group._worker is worker
    assert planner.await_count == 1
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["ingress", "response", "final", "callback", "recovery"])
async def test_checkpoint_failure_is_fail_stop_even_if_next_save_succeeds(setup_group, monkeypatch, point):
    group, members, _ = setup_group
    group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    group._build_references = AsyncMock(return_value=[])
    planner = (
        AsyncMock(side_effect=TimeoutError("模型失败"))
        if point in {"final", "recovery"}
        else AsyncMock(return_value=_response())
    )
    group._service.chat_loop_step = planner
    save = group._store.save
    fail_save = False

    def save_or_fail(*args):
        if fail_save:
            raise OSError("故障注入：磁盘不可写")
        save(*args)

    monkeypatch.setattr(group._store, "save", save_or_fail)
    if point in {"ingress", "callback"}:
        fail_save = True
    if point == "callback":
        group.register_proactive(members["A"], _incoming("A", "background"))
        await group._checkpoint_task
    elif point == "ingress":
        with pytest.raises(OSError):
            await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    else:
        await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
        if point in {"response", "final"}:
            # create_task 尚未取得执行权，只让入站保存成功，故障发生在 Worker 内。
            fail_save = True
            with pytest.raises(OSError):
                await asyncio.wait_for(asyncio.shield(group._worker), 2)
        else:
            await asyncio.wait_for(asyncio.shield(group._worker), 2)
            fail_save = True
            with pytest.raises(OSError):
                await group.register_message(
                    members["B"], _incoming("B", "recover", at=True), _chat_history("recover", "B")
                )
    assert group.state == "failed"
    assert all(member._agent_state == member._STATE_STOP for member in members.values())
    worker = group._worker
    calls = planner.await_count
    fail_save = False
    await group.register_message(members["B"], _incoming("B", "later", at=True), _chat_history("later", "B"))
    assert group._worker is worker
    assert planner.await_count == calls
    assert group.state == "failed"
    await group.stop()


@pytest.mark.asyncio
async def test_messages_queued_before_failure_cannot_restart_worker(setup_group):
    group, members, _ = setup_group
    started, release = asyncio.Event(), asyncio.Event()

    async def planner(*args, **kwargs):
        started.set()
        await release.wait()
        raise TimeoutError("本次请求失败")

    group._build_references = AsyncMock(return_value=[])
    group._service.chat_loop_step = AsyncMock(side_effect=planner)
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    await asyncio.wait_for(started.wait(), 2)
    await group.register_message(members["B"], _incoming("B", "queued", at=True), _chat_history("queued", "B"))
    worker = group._worker
    release.set()
    await asyncio.wait_for(asyncio.shield(worker), 2)
    group._ensure_worker()
    assert group._worker is worker
    assert group._service.chat_loop_step.await_count == 1
    assert group._pending["B"][0].messages[0].processed_plain_text == "queued"
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("close", [False, True])
async def test_planner_cancellation_propagates_without_recovery_latch(setup_group, close):
    group, members, _ = setup_group
    started = asyncio.Event()

    async def planner(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    group._build_references = AsyncMock(return_value=[])
    group._service.chat_loop_step = AsyncMock(side_effect=planner)
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    await asyncio.wait_for(started.wait(), 2)
    worker = group._worker
    if close:
        await group.stop()
    else:
        worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert not group._failed
    if close:
        await group.register_message(members["B"], _incoming("B", "later", at=True), _chat_history("later", "B"))
        assert group._worker is None
    assert group._service.chat_loop_step.await_count == 1
    await group.stop()


@pytest.mark.asyncio
async def test_recovery_waits_for_final_checkpoint_and_starts_only_one_worker(setup_group):
    group, members, _ = setup_group
    final_save, release_save, recovered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def checkpoint():
        if asyncio.current_task() is group._worker and group._failed:
            final_save.set()
            await release_save.wait()

    async def planner(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("初次失败")
        recovered.set()
        return _response()

    group.checkpoint = checkpoint
    group._build_references = AsyncMock(return_value=[])
    group._service.chat_loop_step = planner
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    await asyncio.wait_for(final_save.wait(), 2)
    old_worker = group._worker
    inputs = [
        asyncio.create_task(
            group.register_message(
                members[sid],
                _incoming(sid, "new-" + sid, at=True),
                _chat_history("new-" + sid, sid),
            )
        )
        for sid in ("A", "B")
    ]
    await asyncio.sleep(0)
    assert not old_worker.done()
    assert group._worker is old_worker
    assert calls == 1
    release_save.set()
    await asyncio.wait_for(asyncio.gather(*inputs), 2)
    await asyncio.wait_for(recovered.wait(), 2)
    assert group._worker is not old_worker
    assert calls == 2
    await group.stop()


@pytest.mark.asyncio
async def test_final_save_failure_revokes_recovery_while_new_input_waits(setup_group, monkeypatch):
    group, members, _ = setup_group
    final_save, release_save, input_saved = asyncio.Event(), asyncio.Event(), asyncio.Event()
    save = group._store.save

    async def checkpoint():
        if asyncio.current_task() is group._worker and group._failed:
            final_save.set()
            await release_save.wait()
        await FocusGroupRuntime.checkpoint(group)
        if group.has_message("B", "new"):
            input_saved.set()

    group.checkpoint = checkpoint
    group._build_references = AsyncMock(return_value=[])
    group._service.chat_loop_step = AsyncMock(side_effect=TimeoutError("模型失败"))
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    await asyncio.wait_for(final_save.wait(), 2)
    worker = group._worker
    registration = asyncio.create_task(
        group.register_message(
            members["B"],
            _incoming("B", "new", at=True),
            _chat_history("new", "B"),
        )
    )
    await asyncio.wait_for(input_saved.wait(), 2)
    monkeypatch.setattr(group._store, "save", Mock(side_effect=OSError("最终保存失败")))
    release_save.set()
    with pytest.raises(OSError):
        await asyncio.wait_for(registration, 2)
    assert group._worker is worker
    assert group.state == "failed"
    monkeypatch.setattr(group._store, "save", save)
    await group.register_message(members["B"], _incoming("B", "later", at=True), _chat_history("later", "B"))
    assert group._worker is worker
    assert group._service.chat_loop_step.await_count == 1
    await group.stop()


def test_failed_stage_overrides_wait_without_changing_member_state_protocol(setup_group):
    group, members, _ = setup_group
    group.execute_control("wait", {"seconds": 60}, "A")
    group._failed = True
    group._set_stage("失败", "故障注入")
    assert group.state == "failed"
    assert all(member._agent_state == member._STATE_STOP for member in members.values())
    assert all(member._update_stage_status.call_args.args[0] == "失败" for member in members.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_round_trip", [False, True])
async def test_completed_tools_and_sent_receipts_are_preserved_without_replay(
    setup_group, monkeypatch, checkpoint_round_trip
):
    """恢复保留执行前及执行中的发送回执，不重放已完成工具。"""
    group, members, trace = setup_group
    if checkpoint_round_trip:
        group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    invoke = members["A"].provider.invoke

    async def echo_with_receipt(invocation, context=None):
        group.record_sent_message(members["A"], _history("工具执行期间的回执"))
        return await invoke(invocation, context)

    members["A"].provider.invoke = echo_with_receipt
    group._build_references = AsyncMock(return_value=[])
    completed = _response(calls=[ToolCall("sent-once", "echo", {"session_id": "A", "text": "sent"})])
    planner = AsyncMock(side_effect=[completed, TimeoutError("后续规划失败"), _response()])
    group._service.chat_loop_step = planner
    group.record_sent_message(members["A"], _history("已发送的消息"))
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    await asyncio.wait_for(asyncio.shield(group._worker), 2)
    receipt = next(message for message in group.history if isinstance(message, ToolResultMessage))
    assert receipt.tool_call_id == "sent-once"
    assert group._considered == {"A": 1}
    idle = asyncio.Event()
    members["A"]._update_stage_status.side_effect = lambda stage, *a, **kw: idle.set() if stage == "空闲" else None
    await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
    await asyncio.wait_for(idle.wait(), 2)
    assert len(trace) == 1
    assert receipt in group.history
    assert any(message.processed_plain_text == "已发送的消息" for message in group.history)
    assert any(message.processed_plain_text == "工具执行期间的回执" for message in group.history)
    assert planner.await_count == 3
    assert group.has_message("A", "first")
    await group.stop()
    if checkpoint_round_trip:
        saved = group._store.load()
        restored = FocusGroupRuntime(group.scope_id)
        restored._store = group._store
        monkeypatch.setattr(
            group_module.chat_manager, "get_existing_session_by_session_id", lambda sid: members[sid].chat_stream
        )
        monkeypatch.setattr(manager_module.focus_mode_manager, "is_enabled_for_session", lambda sid: True)
        monkeypatch.setattr(manager_module.focus_mode_manager, "get_focus_scope_key", lambda sid: group.scope_id)
        await restored.restore()
        for history in ([message for entry in saved["entries"] for message in entry.messages], restored.history):
            assert sum(message.processed_plain_text == "已发送的消息" for message in history) == 1
            assert sum(message.processed_plain_text == "工具执行期间的回执" for message in history) == 1
            receipts = [message for message in history if isinstance(message, ToolResultMessage)]
            assert len(receipts) == 1
            assert receipts[0].tool_call_id == "sent-once"
            assert receipts[0].success is True
        assert restored._can_recover_planner_failure()
        assert not restored._failed
        assert restored._ready == set()
        assert restored._worker is None
        assert len(trace) == 1
        assert planner.await_count == 3
        await restored.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unclosed", "unknown", "partial", "invalid", "partial-send", "unknown-send"])
async def test_uncertain_tool_or_send_history_prevents_recovery(setup_group, state):
    group, members, trace = setup_group
    group._build_references = AsyncMock(return_value=[])
    response = _response(calls=[ToolCall("never-replay", "echo", {"session_id": "A", "text": "old"})])
    batch = FocusContextEntry(list(response.raw_messages), {"A"})
    if state != "unclosed":
        metadata = {"execution_state": state}
        if state in {"partial-send", "unknown-send"}:
            metadata = {
                "sent_message_ids": ["sent-part"] if state == "partial-send" else [],
                "send_results": [{"success": True, "message_id": "sent-part"}] if state == "partial-send" else [],
            }
        batch.messages.append(
            ToolResultMessage(
                content="发送状态需核实",
                timestamp=datetime.now(),
                tool_call_id="never-replay",
                logical_turn_id="old-turn",
                tool_name="echo",
                success=False,
                metadata=metadata,
            )
        )
    group.entries.append(batch)
    planner = AsyncMock(side_effect=TimeoutError("后续请求失败"))
    group._service.chat_loop_step = planner
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    worker = group._worker
    await asyncio.wait_for(asyncio.shield(worker), 2)
    await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
    assert group._worker is worker
    assert group.state == "failed"
    assert planner.await_count == 1
    assert group.entries[0] is batch
    assert not trace
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["router", "provider"])
async def test_tool_timeout_is_not_a_recoverable_planner_timeout(setup_group, boundary):
    group, members, _ = setup_group
    group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    group._build_references = AsyncMock(return_value=[])
    target = group.tools if boundary == "router" else members["A"].provider
    target.invoke = AsyncMock(side_effect=TimeoutError("发送回执未知"))
    response = _response(calls=[ToolCall("uncertain", "echo", {"session_id": "A", "text": "old"})])
    planner = AsyncMock(side_effect=[response, TimeoutError("后续规划失败")])
    group._service.chat_loop_step = planner
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    worker = group._worker
    await asyncio.wait_for(asyncio.shield(worker), 2)
    saved = group._store.load()
    receipts = [
        message for entry in saved["entries"] for message in entry.messages if isinstance(message, ToolResultMessage)
    ]
    assert receipts[-1].metadata["execution_state"] == "unknown"
    await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
    assert group._worker is worker
    assert planner.await_count == (1 if boundary == "router" else 2)
    target.invoke.assert_awaited_once()
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["_compact", "refresh", "_build_references", "chat_loop_step"])
async def test_concurrent_ingress_save_failure_invalidates_inflight_turn(setup_group, monkeypatch, boundary):
    group, members, trace = setup_group
    group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    entered, release = asyncio.Event(), asyncio.Event()
    response = _response(calls=[ToolCall("must-not-send", "echo", {"session_id": "A", "text": "unsafe"})])
    planner = AsyncMock(return_value=response)
    group._service.chat_loop_step = planner
    group._build_references = AsyncMock(return_value=[])
    target = group.tools if boundary == "refresh" else group._service if boundary == "chat_loop_step" else group
    original = target.__getattribute__(boundary)

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(target, boundary, blocked)
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    await asyncio.wait_for(entered.wait(), 2)
    save = group._store.save
    monkeypatch.setattr(group._store, "save", Mock(side_effect=OSError("入站保存失败")))
    with pytest.raises(OSError):
        await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
    monkeypatch.setattr(group._store, "save", save)
    release.set()
    await asyncio.wait_for(asyncio.shield(group._worker), 2)
    assert group.state == "failed"
    assert planner.await_count == (1 if boundary == "chat_loop_step" else 0)
    assert not trace
    assert group._considered == {}
    await group.stop()


@pytest.mark.asyncio
async def test_tools_route_by_session_without_mutating_other_chat(setup_group):
    group, members, trace = setup_group
    await group.tools.refresh()
    for sid in ("B", "A"):
        invocation, result, runtime, _ = await group.tools.invoke("echo", {"session_id": sid, "text": sid}, sid, "指导")
        assert result.success
        assert runtime is members[sid]
        assert invocation.session_id == sid
    assert [row[0] for row in trace] == ["B", "A"]
    assert trace[0][2] == {"text": "B"}
    assert [message.processed_plain_text for message in trace[0][3]] == ["只属于 B"]
    assert [message.processed_plain_text for message in trace[1][3]] == ["只属于 A"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [{"text": "x"}, {"session_id": "", "text": "x"}, {"session_id": "outside", "text": "x"}, {"session_id": "A"}],
)
async def test_invalid_route_or_arguments_never_reach_provider(setup_group, arguments):
    group, _, trace = setup_group
    await group.tools.refresh()
    _, result, _, _ = await group.tools.invoke("echo", arguments, "call", "")
    assert not result.success
    assert not trace


@pytest.mark.asyncio
async def test_native_session_id_argument_is_preserved(setup_group):
    group, members, trace = setup_group
    spec = members["B"].provider.specs[0]
    spec.parameters_schema = {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"],
    }
    await group.tools.refresh()
    _, result, _, _ = await group.tools.invoke(
        "echo",
        {"session_id": "B", "tool_arguments": {"session_id": "remote-session"}},
        "call",
        "",
    )
    assert result.success
    assert trace[0][0] == "B"
    assert trace[0][2] == {"session_id": "remote-session"}


@pytest.mark.asyncio
async def test_schema_variants_keep_target_specific_constraints(setup_group):
    group, members, trace = setup_group
    members["B"].provider.specs[0].parameters_schema["properties"]["text"] = {"type": "integer"}
    await group.tools.refresh()
    _, result, _, _ = await group.tools.invoke("echo", {"session_id": "B", "text": "wrong"}, "bad", "")
    assert not result.success
    _, result, _, _ = await group.tools.invoke("echo", {"session_id": "B", "text": 3}, "good", "")
    assert result.success
    assert len(trace) == 1


@pytest.mark.asyncio
async def test_permission_change_between_definition_and_execution_is_rejected(setup_group):
    group, members, trace = setup_group
    await group.tools.refresh()
    members["A"].provider.specs.clear()
    _, result, _, _ = await group.tools.invoke("echo", {"session_id": "A", "text": "x"}, "call", "")
    assert not result.success
    assert not trace


@pytest.mark.asyncio
async def test_wait_closes_tool_batch_and_cancels_later_calls(setup_group):
    group, _, trace = setup_group
    await group.tools.refresh()
    response = _response(
        calls=[
            ToolCall("first", "echo", {"session_id": "A", "text": "first"}),
            ToolCall("waiting", "wait", {"session_id": "B", "seconds": 30}),
            ToolCall("last", "echo", {"session_id": "B", "text": "last"}),
        ]
    )
    batch = FocusContextEntry(list(response.raw_messages), {"A", "B"})
    _, paused = await group._execute_tools(response, batch, "turn", group._generation)
    assert paused
    assert [row[2] for row in trace] == [{"text": "first"}]
    results = [message for message in batch.messages if isinstance(message, ToolResultMessage)]
    assert len(results) == 3
    assert results[1].success
    assert not results[2].success
    assert group._wait_session_id == "B"
    assert group._wait_until is not None


@pytest.mark.asyncio
async def test_wait_queues_normal_messages_and_background_results_until_at(setup_group):
    group, members, _ = setup_group
    called = asyncio.Event()

    async def planner(*args, **kwargs):
        called.set()
        return _response()

    group._service.chat_loop_step = planner
    group.execute_control("wait", {"seconds": 60}, "A")
    await group.register_message(members["B"], _incoming("B", "normal"), _history("普通消息"))
    group.register_proactive(members["B"], _incoming("B", "background"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not called.is_set()
    await group.register_message(members["B"], _incoming("B", "at", at=True), _history("@ 消息"))
    await asyncio.wait_for(called.wait(), 1)
    assert group._wait_until is None
    assert any("普通消息" in message.processed_plain_text for message in group.history)
    assert any("background" in message.processed_plain_text for message in group.history)
    await group.stop()


@pytest.mark.asyncio
async def test_new_chat_message_does_not_cancel_inflight_planner(setup_group):
    group, members, _ = setup_group
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    requests = []

    async def planner(history, **kwargs):
        requests.append(list(history))
        if len(requests) == 1:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        return _response()

    group._service.chat_loop_step = planner
    members["B"]._wait_for_message_quiet_period = asyncio.Event().wait
    await group.register_message(members["A"], _incoming("A", "one"), _history("A 的新消息"))
    await asyncio.wait_for(first_started.wait(), 1)
    await group.register_message(members["B"], _incoming("B", "two", at=True), _history("B 的新消息"))
    assert len(requests) == 1
    assert not group._worker.cancelled()
    assert all("B 的新消息" not in message.processed_plain_text for message in requests[0])
    release_first.set()
    await asyncio.wait_for(second_started.wait(), 1)
    assert any("A 的新消息" in message.processed_plain_text for message in requests[1])
    assert any("B 的新消息" in message.processed_plain_text for message in requests[1])
    await group.stop()


@pytest.mark.asyncio
async def test_each_chat_debounces_independently(setup_group):
    group, members, _ = setup_group
    slow_ready = asyncio.Event()
    members["A"]._wait_for_message_quiet_period = slow_ready.wait
    called = asyncio.Event()
    requests = []

    async def planner(history, **kwargs):
        requests.append(list(history))
        called.set()
        return _response()

    group._service.chat_loop_step = planner
    await group.register_message(members["A"], _incoming("A", "slow"), _history("A 未结束防抖"))
    await group.register_message(members["B"], _incoming("B", "ready"), _history("B 已就绪"))
    await asyncio.wait_for(called.wait(), 1)
    assert any("B 已就绪" in message.processed_plain_text for message in requests[0])
    assert all("A 未结束防抖" not in message.processed_plain_text for message in requests[0])
    await group.stop()


@pytest.mark.asyncio
async def test_checkpoint_closes_unfinished_calls_without_replaying(setup_group):
    group, _, trace = setup_group
    response = _response(calls=[ToolCall("pending", "echo", {"session_id": "A", "text": "x"})])
    group.entries = [FocusContextEntry(list(response.raw_messages), {"A"})]
    await FocusGroupRuntime.checkpoint(group)
    payload = group._store.load()
    restored = [message for entry in payload["entries"] for message in entry.messages]
    results = [message for message in restored if isinstance(message, ToolResultMessage)]
    assert len(results) == 1
    assert results[0].metadata["execution_state"] == "unknown"
    assert results[0].tool_call_id == "pending"
    assert not trace
    assert not any(isinstance(message, ToolResultMessage) for message in group.history)


@pytest.mark.asyncio
async def test_clear_context_invalidates_mixed_summary_but_keeps_other_chat(setup_group):
    group, members, _ = setup_group
    group.entries = [
        FocusContextEntry([_history("A 原文")], {"A"}),
        FocusContextEntry([_history("A B 混合摘要")], {"A", "B"}),
        FocusContextEntry([_history("B 原文")], {"B"}),
    ]
    await group.remove_member("A", clear_context=True)
    assert [message.processed_plain_text for message in group.history] == ["B 原文"]
    assert "B" in group.members
    await group.stop()


def _seed_planner_snapshot(group):
    from src.config.model_configs import ModelInfo
    from src.llm_models.model_client.base_client import ResponseRequest
    group._service.planner_request_snapshot = PlannerRequestSnapshot(
        ResponseRequest(model_info=ModelInfo(model_type="llm", name="planner", model_identifier="remote", api_provider="test"),
                        context_items=[]), tuple(group.history),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("summary_fails", [False, True])
@pytest.mark.parametrize("outcome", ["unknown", "cancelled", "partial-send", "completed"])
async def test_compacted_tool_batch_keeps_recovery_safety(setup_group, monkeypatch, summary_fails, outcome):
    """真实批次被摘要或裁切后，未知调用仍阻断恢复，完成调用不阻断。"""
    group, members, trace = setup_group
    group._hydrating = True
    monkeypatch.setattr(global_config.chat, "max_context_size", 2)
    response = _response(calls=[ToolCall("old-call", "echo", {"session_id": "A", "text": "old"})])
    batch = FocusContextEntry(list(response.raw_messages), {"A", "B"})
    group.entries = [batch]
    await group.tools.refresh()
    if outcome == "cancelled":
        entered = asyncio.Event()

        async def interrupted(invocation, context=None):
            entered.set()
            await asyncio.Event().wait()

        members["A"].provider.invoke = interrupted
        group._worker = asyncio.create_task(group._execute_tools(response, batch, "turn", group._generation))
        await asyncio.wait_for(entered.wait(), 2)
        await group.remove_member("A", clear_context=False)
    else:
        if outcome == "unknown":
            members["A"].provider.invoke = AsyncMock(side_effect=TimeoutError("副作用后的异常"))
        elif outcome == "partial-send":
            members["A"].provider.invoke = AsyncMock(
                return_value=ToolExecutionResult(
                    tool_name="echo", success=False, metadata={"sent_message_ids": ["sent-part"], "send_results": []}
                )
            )
        await group._execute_tools(response, batch, "turn", group._generation)
    assert group._can_recover_planner_failure() is (outcome == "completed")
    group.entries.extend(FocusContextEntry([_chat_history(str(i), "B")], {"B"}) for i in range(6))
    summary = ContextSummaryMessage(content="旧批次摘要", timestamp=datetime.now(), remaining_uses_value=None)
    builder = AsyncMock(side_effect=TimeoutError("摘要失败")) if summary_fails else AsyncMock(return_value=summary)
    monkeypatch.setattr(group_module, "build_context_summary", builder)
    _seed_planner_snapshot(group)
    await group._compact()
    builder.assert_awaited_once()
    assert len(group.entries) == 6
    assert group.entries[0].messages == ([] if summary_fails else [summary])
    assert not any(isinstance(message, ToolResultMessage) for message in group.history)
    assert group._can_recover_planner_failure() is (outcome == "completed")
    await FocusGroupRuntime.checkpoint(group)
    restored = FocusGroupRuntime(group.scope_id)
    restored._store = group._store
    # 重启时所有旧成员均已失效，账本也不能随着材料过滤而消失。
    monkeypatch.setattr(group_module.chat_manager, "get_existing_session_by_session_id", lambda sid: None)
    await restored.restore()
    assert restored.entries == []
    assert restored._can_recover_planner_failure() is (outcome == "completed")
    assert restored._failed is (outcome != "completed")
    if outcome != "completed":
        member = ChatServices("B", restored, [])
        restored.add_member(member)
        await restored.register_message(member, _incoming("B", "after-restart", at=True), _chat_history("restart", "B"))
        assert restored.state == "failed"
        assert restored._worker is None
    await restored.stop()
    if outcome != "completed":
        group._hydrating = False
        group._build_references = AsyncMock(return_value=[])
        group._service.chat_loop_step = AsyncMock(side_effect=TimeoutError("后续规划失败"))
        await group.register_message(members["B"], _incoming("B", "first", at=True), _chat_history("first", "B"))
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
        assert group.state == "failed"
        assert group._recovery_token is None
        assert group._worker is worker
        assert group._service.chat_loop_step.await_count == 1
        assert not trace
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome, recoverable",
    [
        ("rpc-error", False),
        ("transport-failure", False),
        ("tool-failure", False),
        ("invalid-stop", False),
        ("completed", True),
        ("rejected", True),
    ],
)
async def test_plugin_result_safety_survives_compaction_and_restart(setup_group, monkeypatch, outcome, recoverable):
    """真实插件 Provider/RPC 回执进入 Focus 后，摘要和重启不能解除未知状态。"""
    group, members, trace = setup_group
    service = ComponentQueryService()
    registry = ComponentRegistry()
    registry.register_component("plugin_tool", "TOOL", "test_plugin", {})
    supervisor = SimpleNamespace(component_registry=registry, invoke_plugin=AsyncMock())
    monkeypatch.setattr(service, "_iter_supervisors", lambda: [supervisor])
    monkeypatch.setattr(plugin_provider_module, "component_query_service", service)
    monkeypatch.setattr(file_service_module, "get_file_service", lambda: SimpleNamespace(enabled=False, revoke=Mock()))
    if outcome == "rpc-error":
        supervisor.invoke_plugin.side_effect = RPCError(ErrorCode.E_TIMEOUT)
    elif outcome == "rejected":
        supervisor.invoke_plugin.side_effect = RPCError(ErrorCode.E_METHOD_NOT_ALLOWED)
    else:
        result = {
            "success": outcome != "tool-failure",
            "stop_after_execution": "true" if outcome == "invalid-stop" else False,
        }
        supervisor.invoke_plugin.return_value = SimpleNamespace(
            payload={"success": outcome != "transport-failure", "result": result}
        )
    members["A"]._tool_registry.register_provider(PluginToolProvider())
    response = _response(calls=[ToolCall("plugin-once", "plugin_tool", {"session_id": "A"})])
    batch = FocusContextEntry(list(response.raw_messages), {"A"})
    group.entries = [batch]
    await group.tools.refresh()
    await group._execute_tools(response, batch, "turn", group._generation)
    assert group._can_recover_planner_failure() is recoverable
    await FocusGroupRuntime.checkpoint(group)
    saved = group._store.load()
    receipt = next(
        message for entry in saved["entries"] for message in entry.messages if isinstance(message, ToolResultMessage)
    )
    assert (receipt.metadata.get("execution_state") == "unknown") is (not recoverable)
    assert bool(saved["unverified_tool_calls"]) is (not recoverable)

    monkeypatch.setattr(global_config.chat, "max_context_size", 2)
    group.entries.extend(FocusContextEntry([_chat_history(str(i))], {"A"}) for i in range(6))
    summary = ContextSummaryMessage(content="旧插件批次摘要", timestamp=datetime.now(), remaining_uses_value=None)
    monkeypatch.setattr(group_module, "build_context_summary", AsyncMock(return_value=summary))
    _seed_planner_snapshot(group)
    await group._compact()
    assert all(entry is not batch for entry in group.entries)
    assert not any(isinstance(message, ToolResultMessage) for message in group.history)
    assert group._can_recover_planner_failure() is recoverable
    await FocusGroupRuntime.checkpoint(group)
    restored = FocusGroupRuntime(group.scope_id)
    restored._store = group._store
    monkeypatch.setattr(group_module.chat_manager, "get_existing_session_by_session_id", lambda sid: None)
    await restored.restore()
    assert restored._can_recover_planner_failure() is recoverable
    assert restored._failed is (not recoverable)
    if not recoverable:
        member = ChatServices("A", restored, trace)
        restored.add_member(member)
        await restored.register_message(member, _incoming("A", "new", at=True), _chat_history("new"))
        assert restored._worker is None
    supervisor.invoke_plugin.assert_awaited_once()
    assert not trace
    await restored.stop()
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [False, True])
async def test_checkpoint_inflight_marker_clears_on_matching_receipt(setup_group, success):
    """快照的临时 unknown 只由原调用的确定结果解除，普通失败回执也可闭合。"""
    group, members, _ = setup_group
    response = _response(calls=[ToolCall("inflight", "echo", {"session_id": "A", "text": "x"})])
    batch = FocusContextEntry(list(response.raw_messages), {"A"})
    group.entries = [batch]
    await FocusGroupRuntime.checkpoint(group)
    assert group._store.load()["unverified_tool_calls"]
    assert not any(isinstance(message, ToolResultMessage) for message in group.history)
    other_response = _response(calls=[ToolCall("inflight", "echo", {"session_id": "A", "text": "other"})])
    other_batch = FocusContextEntry(list(other_response.raw_messages), {"A"})
    members["A"].provider.invoke = AsyncMock(return_value=ToolExecutionResult(tool_name="echo", success=success))
    await group.tools.refresh()
    await group._execute_tools(other_response, other_batch, "other", group._generation)
    # call_id 相同，但不属于原逻辑轮次，不能核实旧调用。
    assert not group._can_recover_planner_failure()
    await group._execute_tools(response, batch, "original", group._generation)
    assert group._can_recover_planner_failure()
    await FocusGroupRuntime.checkpoint(group)
    assert group._store.load()["unverified_tool_calls"] == []
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [False, True])
async def test_receipt_cannot_clear_compacted_call_from_another_turn(setup_group, success):
    """原批次完全移除后，同 ID 的其他轮次回执仍不能清除独立账本。"""
    group, _, trace = setup_group
    response = _response(calls=[ToolCall("reused-id", "echo", {"session_id": "A", "text": "x"})])
    group.entries = [FocusContextEntry(list(response.raw_messages), {"A"})]
    await FocusGroupRuntime.checkpoint(group)
    (original_key,) = group._unverified_tool_calls
    group.entries.clear()
    assert not group.entries
    assert group._unverified_tool_calls == {original_key}

    def receipt(turn):
        return FocusContextEntry(
            [
                ToolResultMessage(
                    content="已核实",
                    timestamp=datetime.now(),
                    tool_call_id="reused-id",
                    logical_turn_id=turn,
                    tool_name="echo",
                    success=success,
                    metadata={},
                )
            ],
            {"A"},
        )

    group.entries.append(receipt("another-turn"))
    assert original_key[0] != "another-turn"
    assert not group._can_recover_planner_failure()
    await FocusGroupRuntime.checkpoint(group)
    assert group._store.load()["unverified_tool_calls"] == [original_key]
    group.entries.append(receipt(original_key[0]))
    assert group._can_recover_planner_failure()
    await FocusGroupRuntime.checkpoint(group)
    assert group._store.load()["unverified_tool_calls"] == []
    assert not trace
    await group.stop()


@pytest.mark.asyncio
async def test_new_unverified_call_revokes_existing_recovery_eligibility(setup_group):
    """恢复令牌授予后出现未知状态时，输入保存成功仍不能消费旧资格。"""
    group, members, _ = setup_group
    group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    group._build_references = AsyncMock(return_value=[])
    group._service.chat_loop_step = AsyncMock(side_effect=TimeoutError("首轮失败"))
    await group.register_message(members["A"], _incoming("A", "first", at=True), _chat_history("first"))
    worker = group._worker
    await asyncio.wait_for(asyncio.shield(worker), 2)
    assert group._recovery_token is not None
    response = _response(calls=[ToolCall("unverified", "echo", {"session_id": "A", "text": "x"})])
    group.entries.append(FocusContextEntry(list(response.raw_messages), {"A"}))
    await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
    assert group._worker is worker
    assert group.state == "failed"
    assert group._service.chat_loop_step.await_count == 1
    await group.stop()


@pytest.mark.asyncio
async def test_receipt_save_failure_keeps_durable_unknown_and_fail_stop(setup_group, monkeypatch):
    """完成回执落盘失败时，旧 checkpoint 的 unknown 仍阻断重启和本实例恢复。"""
    group, members, _ = setup_group
    group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    response = _response(calls=[ToolCall("durable", "echo", {"session_id": "A", "text": "x"})])
    batch = FocusContextEntry(list(response.raw_messages), {"A"})
    group.entries = [batch]
    await group.checkpoint()
    await group.tools.refresh()
    write = group._store._write
    monkeypatch.setattr(group._store, "_write", Mock(side_effect=OSError("完成回执保存失败")))
    with pytest.raises(OSError):
        await group._execute_tools(response, batch, "turn", group._generation)
    assert group.state == "failed"
    assert group._recovery_token is None
    restored = FocusGroupRuntime(group.scope_id)
    restored._store = group._store
    monkeypatch.setattr(group_module.chat_manager, "get_existing_session_by_session_id", lambda sid: None)
    await restored.restore()
    assert restored._failed
    assert not restored._can_recover_planner_failure()
    monkeypatch.setattr(group._store, "_write", write)
    await group.register_message(members["B"], _incoming("B", "new", at=True), _chat_history("new", "B"))
    assert group.state == "failed"
    assert group._worker is None
    await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_clearing_members_and_purging_preserve_tool_recovery_state(setup_group, legacy, tmp_path):
    """在线清空和离线清理都不能把未知动作或旧版不完整状态变成安全。"""
    group, _, _ = setup_group
    group._hydrating = True
    response = _response(calls=[ToolCall("interrupted", "echo", {"session_id": "A", "text": "x"})])
    group.entries = [FocusContextEntry(list(response.raw_messages), {"A", "B"})]
    await group.remove_member("A", clear_context=True)
    assert group.entries == []
    assert not group._can_recover_planner_failure()
    await FocusGroupRuntime.checkpoint(group)
    if legacy:
        payload = json.loads(group._store.path.read_text(encoding="utf-8"))
        payload["version"] = 1
        del payload["unverified_tool_calls"], payload["tool_recovery_state_incomplete"]
        group._store.path.write_text(json.dumps(payload), encoding="utf-8")
    FocusCheckpointStore.purge_session("B", set(), tmp_path)
    payload = group._store.load()
    assert payload["version"] == 2
    assert payload["tool_recovery_state_incomplete"] is legacy
    assert bool(payload["unverified_tool_calls"]) is (not legacy)
    restored = FocusGroupRuntime(group.scope_id)
    restored._store = group._store
    await restored.restore()
    assert restored._failed
    assert not restored._can_recover_planner_failure()
    await restored.checkpoint()
    assert restored._store.load()["tool_recovery_state_incomplete"] is legacy
    await restored.stop()
    await group.stop()


@pytest.mark.parametrize("damage", ["missing-ledger", "missing-completeness", "invalid-key", "invalid-completeness"])
def test_checkpoint_rejects_damaged_v2_recovery_state(tmp_path, damage):
    """V2 恢复字段缺失或损坏时拒绝加载，不默认为安全。"""
    store = FocusCheckpointStore("damaged", tmp_path)
    store.save([], {})
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    if damage == "missing-ledger":
        del payload["unverified_tool_calls"]
    elif damage == "missing-completeness":
        del payload["tool_recovery_state_incomplete"]
    elif damage == "invalid-key":
        payload["unverified_tool_calls"] = [["", "call"]]
    else:
        payload["tool_recovery_state_incomplete"] = "false"
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="工具恢复状态"):
        store.load()


@pytest.mark.asyncio
async def test_cancelled_checkpoint_cannot_overwrite_newer_tool_ledger(setup_group, monkeypatch):
    """取消必须等线程内保存结束才释放锁，避免旧空账本覆盖较新的 unknown。"""
    group, _, _ = setup_group
    entered, release = asyncio.Event(), ThreadEvent()
    loop = asyncio.get_running_loop()
    save = group._store.save

    def slow_save(entries, state):
        if not state["unverified_tool_calls"]:
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError("测试未释放保存线程")
        save(entries, state)

    monkeypatch.setattr(group._store, "save", slow_save)
    first = asyncio.create_task(FocusGroupRuntime.checkpoint(group))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        first.cancel()
        await asyncio.sleep(0)
        assert not first.done()
        first.cancel()
        await asyncio.sleep(0)
        assert not first.done()
        response = _response(calls=[ToolCall("new-pending", "echo", {"session_id": "A", "text": "x"})])
        group.entries = [FocusContextEntry(list(response.raw_messages), {"A"})]
        second = asyncio.create_task(FocusGroupRuntime.checkpoint(group))
        await asyncio.sleep(0)
        assert not second.done()
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
    assert first.cancelled()
    assert second is not None and second.exception() is None
    assert group._store.load()["unverified_tool_calls"]
    monkeypatch.setattr(group._store, "save", save)
    await group.stop()


@pytest.mark.asyncio
async def test_compaction_keeps_source_progress_and_recent_complete_tool_turn(setup_group, monkeypatch):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 4)
    summary_requests = []

    class SummaryClient:
        async def generate_response_from_snapshot(self, request):
            summary_requests.extend(context_module.items.get_item_text(item) for item in request.context_items)
            assert request.tool_choice is None
            payload = {key: [] for key in compaction_module.STATE_LABELS}
            payload["background"] = ["A 的事实"]
            payload["pending"] = ["B 的未完成任务"]
            return LLMResponseResult.from_portable_output(response=json.dumps(payload))

    clients = Mock(return_value=SummaryClient())
    monkeypatch.setattr(compaction_module, "LLMServiceClient", clients)
    monkeypatch.setattr(compaction_module, "save_compaction_prompt_preview", Mock())
    group.entries = [
        FocusContextEntry([_chat_history(str(i), "A" if i % 2 else "B")], {"A" if i % 2 else "B"}, i)
        for i in range(1, 11)
    ]
    response = _response(calls=[ToolCall("recent", "echo", {"session_id": "B", "text": "recent"})])
    turn_id = next(message.output_item.meta.logical_turn_id for message in response.raw_messages if message.tool_calls)
    recent = FocusContextEntry(
        [
            *response.raw_messages,
            ToolResultMessage(
                content="done",
                timestamp=datetime.now(),
                tool_call_id="recent",
                logical_turn_id=turn_id,
                tool_name="echo",
            ),
        ],
        {"A", "B"},
    )
    group.entries.append(recent)
    _seed_planner_snapshot(group)
    await group._compact()
    assert group.entries[-1] is recent
    assert group.entries[0].session_ids == {"A", "B"}
    assert group.entries[0].progress["A"] > 0
    assert group.entries[0].progress["B"] > 0
    assert "session_ids=" in "\n".join(summary_requests)
    assert "Focus" in "\n".join(summary_requests)
    clients.assert_called_once_with(task_name="planner", request_type="maisaka.context_compaction", session_id=group.scope_id)
    assert isinstance(group.entries[0].messages[0], ContextSummaryMessage)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("摘要为空"), TimeoutError("硬超时"), RuntimeError("服务商重试耗尽")])
@pytest.mark.parametrize("keep_previous", [False, True])
async def test_failed_summary_discards_old_entries_and_preserves_progress(setup_group, monkeypatch, error, keep_previous):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 2)
    builder = AsyncMock(side_effect=error)
    monkeypatch.setattr(group_module, "build_context_summary", builder)
    previous = ContextSummaryMessage(content="上一份有效摘要", timestamp=datetime.now())
    group.entries = [
        *([FocusContextEntry([previous], {"A"})] if keep_previous else []),
        *[FocusContextEntry([_chat_history(str(i))], {"A"}, i, inputs={"A": [(i, f"message-{i}")]}) for i in range(1, 7)],
    ]
    original = list(group.entries)
    _seed_planner_snapshot(group)
    await group._compact()
    assert group.entries[1:] == original[-5:]
    assert group.entries[0].messages == ([previous] if keep_previous else [])
    assert group.entries[0].progress == {"A": 1}
    assert group.entries[0].inputs == {}
    assert group._service.planner_request_snapshot is None
    assert not group._failed
    await group._compact()
    builder.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_compaction_continues_to_planner_in_same_turn(setup_group, monkeypatch):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 2)
    builder = AsyncMock(side_effect=TimeoutError("服务商重试耗尽"))
    monkeypatch.setattr(group_module, "build_context_summary", builder)
    group.entries = [FocusContextEntry([_chat_history(str(i))], {"A"}, i) for i in range(1, 7)]
    _seed_planner_snapshot(group)
    planner = AsyncMock(return_value=_response())
    monkeypatch.setattr(group._service, "chat_loop_step", planner)
    monkeypatch.setattr(group, "_build_references", AsyncMock(return_value=[]))
    await group._run_turn()
    planner.assert_awaited_once()
    assert [message.processed_plain_text for message in planner.await_args.args[0]] == ["2", "3", "4", "5", "6"]
    assert not group._failed
    assert group._considered == {"A": 6}


def test_state_summary_survives_focus_checkpoint_round_trip():
    summary = ContextSummaryMessage(
        content="B 尚有未回答的问题", timestamp=datetime.now(), remaining_uses_value=None,
        display_prefix="[Focus 状态摘要 session_ids=A,B]",
    )
    restored = context_module.deserialize_message(context_module.serialize_message(summary))
    assert isinstance(restored, ContextSummaryMessage)
    assert restored.content == summary.content
    assert restored.display_prefix == summary.display_prefix
    assert restored.remaining_uses_value is None
    assert not restored.count_in_context


@pytest.mark.asyncio
@pytest.mark.parametrize("restored", [False, True])
async def test_focus_compaction_preserves_five_real_user_and_bot_messages(setup_group, monkeypatch, restored):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 2)
    originals = []
    for index in range(12):
        sid = "A" if index % 2 == 0 else "B"
        original = _incoming(sid, str(index))
        message = SessionBackedMessage.from_session_message(
            original, raw_message=original.raw_message, visible_text=str(index),
            source_kind="user" if index % 2 == 0 else "guided_reply",
        )
        if restored:
            message = context_module.deserialize_message(context_module.serialize_message(message))
        originals.append(FocusContextEntry([message], {sid}, index))
    group.entries = list(originals)
    summary = ContextSummaryMessage(content="共享摘要", timestamp=datetime.now(), remaining_uses_value=None)
    builder = AsyncMock(return_value=summary)
    monkeypatch.setattr(group_module, "build_context_summary", builder)
    _seed_planner_snapshot(group)
    await group._compact()
    assert group.entries[1:] == originals[-5:]
    assert group.entries[0].messages == [summary]
    builder.assert_awaited_once()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("source", ["user", "guided_reply"])
def test_checkpoint_counts_only_real_chat_messages(legacy, source):
    history = [_chat_history("真实消息", source=source), _history("内部资料"), *_response().raw_messages]
    records = [context_module.serialize_message(message) for message in history]
    if legacy:
        records[0]["kind"] = "content"
        # 旧检查点把内部资料也标成 counted，恢复时不能继续沿用这个计数。
        for record in records:
            if record["kind"] == "internal":
                record["kind"] = "content"
            record["counted"] = True
    restored = [context_module.deserialize_message(record) for record in records]
    assert [message.count_in_context for message in restored] == [True, *[False] * (len(restored) - 1)]
    assert context_module.serialize_message(restored[0])["kind"] == "chat"


def test_checkpoint_internal_user_source_does_not_count_as_real_chat():
    internal = _history("以 user 角色发送的内部上下文")
    internal.source_kind = "user"
    record = context_module.serialize_message(internal)
    assert record["kind"] == "internal"
    assert not context_module.deserialize_message(record).count_in_context


@pytest.mark.asyncio
async def test_focus_long_messages_do_not_trigger_compaction_below_count_threshold(setup_group, monkeypatch):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 100)
    group.entries = [FocusContextEntry([_chat_history(str(i) * 90000)], {"A"}) for i in range(3)]
    original = list(group.entries)
    builder = AsyncMock()
    monkeypatch.setattr(group_module, "build_context_summary", builder)
    _seed_planner_snapshot(group)
    await group._compact()
    assert group.entries == original
    builder.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_focus_clear_during_compaction_does_not_restore_removed_sources(setup_group, monkeypatch, fail):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 2)
    group.entries = [FocusContextEntry([_chat_history(str(i))], {"A"}) for i in range(6)]

    async def build(*args, **kwargs):
        group.entries.clear()
        group._generation += 1
        if fail:
            raise ValueError("摘要无效")
        return ContextSummaryMessage(content="已失效的 A 状态", timestamp=datetime.now(), remaining_uses_value=None)

    monkeypatch.setattr(group_module, "build_context_summary", build)
    _seed_planner_snapshot(group)
    await group._compact()
    assert group.entries == []


def test_routing_schema_does_not_mutate_native_spec():
    original = ToolSpec(name="x", parameters_schema={"type": "object", "properties": {}, "required": []})
    routed = routed_schema(original, "A")
    assert routed["required"] == ["session_id"]
    assert original.parameters_schema["properties"] == {}


@pytest.mark.asyncio
async def test_current_schema_change_is_checked_before_execution(setup_group):
    group, members, trace = setup_group
    await group.tools.refresh()
    members["A"].provider.specs[0].parameters_schema["properties"]["text"] = {"type": "integer"}
    _, result, _, _ = await group.tools.invoke("echo", {"session_id": "A", "text": "old"}, "call", "")
    assert not result.success
    assert not trace


@pytest.mark.asyncio
async def test_media_and_post_messages_do_not_split_tool_results(setup_group):
    group, members, _ = setup_group

    async def with_post(invocation, context=None):
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            content="ok",
            post_history_messages=[_history("素材 " + context.session_id)],
        )

    for member in members.values():
        member.provider.invoke = with_post
    await group.tools.refresh()
    response = _response(
        calls=[
            ToolCall("a", "echo", {"session_id": "A", "text": "a"}),
            ToolCall("b", "echo", {"session_id": "B", "text": "b"}),
        ]
    )
    batch = FocusContextEntry(list(response.raw_messages), {"A", "B"})
    await group._execute_tools(response, batch, "turn", group._generation)
    after_model = batch.messages[len(response.raw_messages) :]
    assert [message.role for message in after_model] == ["tool", "tool", "user", "user"]
    assert after_model[2].processed_plain_text == "素材 A"
    assert after_model[3].processed_plain_text == "素材 B"


@pytest.mark.asyncio
async def test_detach_without_clear_keeps_unprocessed_inputs_and_sent_messages(setup_group):
    group, members, _ = setup_group
    group._hydrating = True
    group._queue_entry("A", _history("尚未判断的 A 输入"))
    group.record_sent_message(members["A"], _history("A 已经发送的回复"))
    await group.remove_member("A", clear_context=False)
    assert [message.processed_plain_text for message in group.history] == ["尚未判断的 A 输入", "A 已经发送的回复"]
    group.add_member(members["A"])
    assert "A" in group._ready
    await group.stop()


@pytest.mark.asyncio
async def test_purge_removes_cleared_session_from_unloaded_checkpoints(setup_group, tmp_path):
    group, _, _ = setup_group
    group.entries = [
        FocusContextEntry([_history("混合摘要")], {"A", "B"}),
        FocusContextEntry([_history("B 内容")], {"B"}),
    ]
    group._seen.update({"A\0one": None, "B\0two": None})
    group.execute_control("wait", {"seconds": 30}, "A")
    await FocusGroupRuntime.checkpoint(group)
    FocusCheckpointStore.purge_session("A", set(), tmp_path)
    payload = group._store.load()
    assert [message.processed_plain_text for entry in payload["entries"] for message in entry.messages] == ["B 内容"]
    assert payload["seen"] == ["B\0two"]
    assert payload["wait_until"] is None


@pytest.mark.parametrize("damage", ["json", "version", "message", "ledger", "read", "write", "none"])
def test_purge_processes_all_files_and_reports_partial_failure(tmp_path, monkeypatch, damage):
    """坏文件不阻断后续清理；已提交清理、原始异常、V1/V2 账本均保留。"""
    response = _response(calls=[ToolCall("unverified", "echo", {"session_id": "A", "text": "x"})])
    entries = [FocusContextEntry(list(response.raw_messages), {"A"}), FocusContextEntry([_history("B 内容")], {"B"})]
    state = {
        "sequence": 2,
        "considered": {"A": 1, "B": 2},
        "seen": ["A\0one", "B\0two"],
        "wait_until": 123.0,
        "wait_session_id": "A",
        "wait_token": "wait-A",
        "consecutive_waits": 1,
    }
    stores = [
        FocusCheckpointStore(name, tmp_path) for name in ("broken", "target-v2", "identity", "target-v1", "unrelated")
    ]
    broken, target_v2, identity, target_v1, unrelated = stores
    for store in stores:
        store.save(entries if store is not unrelated else entries[1:], state)
    legacy = json.loads(target_v1.path.read_text(encoding="utf-8"))
    legacy["version"] = 1
    del legacy["unverified_tool_calls"], legacy["tool_recovery_state_incomplete"]
    target_v1.path.write_text(json.dumps(legacy), encoding="utf-8")
    excluded = tmp_path / "excluded.json"
    excluded.write_text("excluded invalid json", encoding="utf-8")

    expected_error = ValueError
    if damage != "none":
        identity_payload = json.loads(identity.path.read_text(encoding="utf-8"))
        identity_payload["scope_id"] = "not-the-file-identity"
        identity.path.write_text(json.dumps(identity_payload), encoding="utf-8")
        payload = json.loads(broken.path.read_text(encoding="utf-8"))
        if damage == "json":
            broken.path.write_text("{invalid json", encoding="utf-8")
            expected_error = json.JSONDecodeError
        elif damage in {"version", "message", "ledger"}:
            if damage == "version":
                payload["version"] = 999
            elif damage == "message":
                payload["entries"][0]["messages"][0]["item"] = {"$datetime": "invalid-date"}
            else:
                del payload["unverified_tool_calls"]
            broken.path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            expected_error = OSError
            if damage == "read":
                read = type(tmp_path).read_text

                def fail_read(path, *args, **kwargs):
                    if path == broken.path:
                        raise OSError("checkpoint read failed")
                    return read(path, *args, **kwargs)

                monkeypatch.setattr(type(tmp_path), "read_text", fail_read)
            else:
                write = FocusCheckpointStore._write

                def fail_write(store, payload):
                    if store.path == broken.path:
                        raise OSError("checkpoint write failed")
                    write(store, payload)

                monkeypatch.setattr(FocusCheckpointStore, "_write", fail_write)
    protected = {path: path.read_bytes() for path in (broken.path, identity.path, excluded)}
    # 固定坏文件在前、两个有效目标分布在错误之间和之后，不依赖文件系统枚举顺序。
    glob = type(tmp_path).glob
    monkeypatch.setattr(
        type(tmp_path),
        "glob",
        lambda path, pattern: (
            iter([store.path for store in stores] + [excluded]) if path == tmp_path else glob(path, pattern)
        ),
    )
    caught = None
    try:
        FocusCheckpointStore.purge_session("A", {excluded}, tmp_path)
    except Exception as exc:
        caught = exc

    for store in (target_v2, target_v1):
        saved = store.load()
        assert [message.processed_plain_text for entry in saved["entries"] for message in entry.messages] == ["B 内容"]
        assert saved["considered"] == {"B": 2}
        assert saved["seen"] == ["B\0two"]
        assert saved["wait_until"] is None
        assert saved["wait_session_id"] is None
        assert saved["wait_token"] is None
        assert saved["consecutive_waits"] == 0
        assert saved["version"] == 2
        assert saved["tool_recovery_state_incomplete"] is (store is target_v1)
        assert saved["unverified_tool_calls"] == [
            (response.raw_messages[0].output_item.meta.logical_turn_id, "unverified")
        ]
    assert unrelated.load()["seen"] == ["B\0two"]
    assert unrelated.load()["entries"][0].messages[0].processed_plain_text == "B 内容"
    assert excluded.read_bytes() == protected[excluded]
    if damage == "none":
        assert caught is None
    else:
        assert isinstance(caught, ExceptionGroup)
        assert len(caught.exceptions) == 2
        assert isinstance(caught.exceptions[0], expected_error)
        assert isinstance(caught.exceptions[1], ValueError)
        assert "身份不匹配" in str(caught.exceptions[1])
        for error, path in zip(caught.exceptions, (broken.path, identity.path), strict=True):
            assert any(str(path) in note for note in error.__notes__)
            assert path.read_bytes() == protected[path]


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("damaged", [False, True])
async def test_clear_context_propagates_partial_purge_failure(tmp_path, monkeypatch, live, damaged):
    """真实离线清理失败不能被在线实例淘汰吞掉，也不能跳过旧实例停止。"""
    from src.agent import planner_state
    from src.chat.heart_flow.heartflow_manager import HeartflowManager

    group = FocusGroupRuntime("purge-target")
    group._store = FocusCheckpointStore(group.scope_id, tmp_path)
    group.entries = [FocusContextEntry([_history("A 内容")], {"A"}), FocusContextEntry([_history("B 内容")], {"B"})]
    await group.checkpoint()
    paths = [group._store.path]
    if damaged:
        bad = tmp_path / "broken.json"
        bad.write_text("{invalid json", encoding="utf-8")
        paths.insert(0, bad)
    glob = type(tmp_path).glob
    monkeypatch.setattr(
        type(tmp_path), "glob", lambda path, pattern: iter(paths) if path == tmp_path else glob(path, pattern)
    )
    purge = FocusCheckpointStore.purge_session
    monkeypatch.setattr(FocusCheckpointStore, "purge_session", lambda sid, excluded: purge(sid, excluded, tmp_path))
    focus_manager = FocusModeManager()
    monkeypatch.setattr(manager_module.focus_mode_manager, "clear_context", focus_manager.clear_context)
    reset = AsyncMock()
    monkeypatch.setattr(planner_state, "reset_planner_session", reset)
    manager = HeartflowManager()
    chat = SimpleNamespace(stop=AsyncMock())
    if live:
        manager.heartflow_chat_list["A"] = chat

    if damaged:
        with pytest.raises(ExceptionGroup) as caught:
            await manager.clear_chat_history_context("A")
        assert len(caught.value.exceptions) == 1
        assert isinstance(caught.value.exceptions[0], json.JSONDecodeError)
        assert bad.read_text(encoding="utf-8") == "{invalid json"
        reset.assert_not_awaited()
    else:
        assert await manager.clear_chat_history_context("A") is live
        reset.assert_awaited_once_with("A")
    assert chat.stop.await_count == int(live)
    assert "A" not in manager.heartflow_chat_list
    saved = group._store.load()
    assert [message.processed_plain_text for entry in saved["entries"] for message in entry.messages] == ["B 内容"]


@pytest.mark.asyncio
@pytest.mark.parametrize("purge_fails", [False, True])
async def test_clear_context_stop_failure_preserves_purge_error(monkeypatch, purge_fails):
    """停止也失败时向上传播，异常链仍保留先前清理失败的原始错误。"""
    from src.agent import planner_state
    from src.chat.heart_flow.heartflow_manager import HeartflowManager

    purge_error = ExceptionGroup("部分清理失败", [ValueError("坏检查点")]) if purge_fails else None
    clear = AsyncMock(side_effect=purge_error)
    monkeypatch.setattr(manager_module.focus_mode_manager, "clear_context", clear)
    reset = AsyncMock()
    monkeypatch.setattr(planner_state, "reset_planner_session", reset)
    stop_error = OSError("停止失败")
    chat = SimpleNamespace(stop=AsyncMock(side_effect=stop_error))
    manager = HeartflowManager()
    manager.heartflow_chat_list["A"] = chat
    with pytest.raises(OSError) as caught:
        await manager.clear_chat_history_context("A")
    assert caught.value is stop_error
    assert caught.value.__context__ is purge_error
    clear.assert_awaited_once_with("A")
    chat.stop.assert_awaited_once()
    reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_emoji_mapping_survives_checkpoint_and_only_restores_to_target(setup_group):
    from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType

    group, members, _ = setup_group
    item = ContextItemBuilder().set_role(RoleType.User).add_text_content("表情候选").build()
    group.entries = [
        FocusContextEntry(
            [
                EmojiCandidateMessage(
                    item=item,
                    emoji_hashes={1: "hash-A"},
                    visible_text="表情候选",
                    timestamp=datetime.now(),
                    session_id="A",
                )
            ],
            {"A"},
        )
    ]
    await FocusGroupRuntime.checkpoint(group)
    group.entries = group._store.load()["entries"]
    group.add_member(members["A"])
    group.add_member(members["B"])
    assets = [message for message in members["A"]._chat_history if isinstance(message, EmojiCandidateMessage)]
    assert assets[0].emoji_hashes == {1: "hash-A"}
    assert not any(isinstance(message, EmojiCandidateMessage) for message in members["B"]._chat_history)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_regroup_transfers_unprocessed_real_messages_only(setup_group, monkeypatch, enabled):
    group, members, _ = setup_group
    group._hydrating = True
    member = members["A"]
    incoming = _incoming("A", "unprocessed")
    history = SessionBackedMessage.from_session_message(
        incoming,
        raw_message=incoming.raw_message,
        visible_text="未处理消息",
    )
    member.message_cache = [incoming]
    member._last_processed_index = 1
    member._chat_history.append(history)
    group._queue_entry("A", history)
    manager = FocusModeManager()
    manager._lock = asyncio.Lock()
    manager.groups[group.scope_id] = group
    monkeypatch.setattr(manager, "is_enabled_for_session", lambda *args, **kwargs: enabled)
    monkeypatch.setattr(manager, "get_focus_scope_key", lambda sid: "new-group")
    new_group = FocusGroupRuntime("new-group")
    new_group._hydrating = True
    new_group.checkpoint = AsyncMock()

    async def attach(runtime):
        runtime._focus_group = new_group
        new_group.add_member(runtime)

    monkeypatch.setattr(manager, "attach", attach)
    member._reasoning_engine._build_history_message = AsyncMock(return_value=history)
    await manager.reconcile(member)
    if enabled:
        assert new_group._pending["A"][0].messages[0].original_message is incoming
        assert member._focus_group is new_group
    else:
        assert member.message_cache[member._last_processed_index :] == [incoming]
        member._schedule_message_turn.assert_called_once()
    assert all("A" not in entry.session_ids for entry in group.entries)
    await group.stop()
    await new_group.stop()


@pytest.mark.asyncio
async def test_real_chat_services_share_one_planner_without_starting_individual_loops(monkeypatch, tmp_path):
    from src.config.config import config_manager
    from src.maisaka import runtime as runtime_module
    from src.maisaka.focus import manager as manager_module

    manager = FocusModeManager()
    monkeypatch.setattr(manager_module, "focus_mode_manager", manager)
    monkeypatch.setattr(runtime_module, "focus_mode_manager", manager)
    monkeypatch.setattr(global_config.experimental, "focus_mode", True)
    monkeypatch.setattr(global_config.experimental, "focus_groups", [])
    monkeypatch.setattr(global_config.experimental, "focus_chat_whitelist", [])
    monkeypatch.setattr(global_config.chat.reply_timing, "message_debounce_mode", "off")
    sessions = {
        sid: SimpleNamespace(
            session_id=sid,
            is_group_session=True,
            platform="test",
            group_id=sid,
            group_name="群 " + sid,
            user_id=None,
            account_id="bot",
            scope="main",
        )
        for sid in ("A", "B")
    }
    monkeypatch.setattr(manager_module.chat_manager, "get_session_by_session_id", lambda sid: sessions.get(sid))
    monkeypatch.setattr(manager_module.chat_manager, "get_session_name", lambda sid: "群 " + sid)
    monkeypatch.setattr(MaisakaHeartFlowChatting, "_emit_monitor_session_start", Mock())
    monkeypatch.setattr(MaisakaHeartFlowChatting, "_emit_monitor_message_ingested", Mock())
    monkeypatch.setattr(MaisakaHeartFlowChatting, "_ensure_expression_vector_history_backfill_running", Mock())
    monkeypatch.setattr(MaisakaHeartFlowChatting, "_restore_recent_context_from_db", AsyncMock())
    monkeypatch.setattr(group_module, "FocusCheckpointStore", lambda scope: FocusCheckpointStore(scope, tmp_path))
    a = MaisakaHeartFlowChatting("A")
    b = MaisakaHeartFlowChatting("B")
    try:
        await asyncio.gather(a.start(), b.start())
        group = a._focus_group
        assert b._focus_group is group
        assert a._internal_loop_task is None and b._internal_loop_task is None
        assert len(manager.groups) == 1
        requests = []
        both_seen = asyncio.Event()

        async def planner(history, **kwargs):
            requests.append(list(history))
            if all(
                any(
                    isinstance(message, SessionBackedMessage) and message.original_message.session_id == sid
                    for message in history
                )
                for sid in ("A", "B")
            ):
                both_seen.set()
            return _response()

        group._service.chat_loop_step = planner
        group._build_references = AsyncMock(return_value=[])
        group._emit_response = AsyncMock()
        # Provider 发现不影响此处验证，避免测试查询外部插件/MCP 服务。
        group.tools.refresh = AsyncMock()
        await a.register_message(_incoming("A", "message-A"))
        await b.register_message(_incoming("B", "message-B"))
        await asyncio.wait_for(both_seen.wait(), 2)
        assert all(
            isinstance(message, SessionBackedMessage) and message.original_message.session_id == "A"
            for message in a._chat_history
        )
        assert all(
            isinstance(message, SessionBackedMessage) and message.original_message.session_id == "B"
            for message in b._chat_history
        )
    finally:
        await a.stop()
        await b.stop()
        config_manager.unregister_reload_callback(manager.reconcile_all)


@pytest.mark.asyncio
async def test_nested_schema_refs_survive_session_id_wrapping_and_schema_union(setup_group):
    from jsonschema import validate

    group, members, trace = setup_group
    members["B"].provider.specs[0].parameters_schema = {
        "type": "object",
        "properties": {"session_id": {"$ref": "#/$defs/RemoteId"}},
        "$defs": {"RemoteId": {"type": "string", "minLength": 3}},
        "required": ["session_id"],
    }
    await group.tools.refresh()
    arguments = {"session_id": "B", "tool_arguments": {"session_id": "remote-session"}}
    validate(arguments, group.tools.routes["echo"].spec.parameters_schema)
    assert "anyOf" not in group.tools.routes["echo"].spec.parameters_schema
    _, result, _, _ = await group.tools.invoke("echo", arguments, "call", "")
    assert result.success
    assert trace[0][2] == {"session_id": "remote-session"}


@pytest.mark.asyncio
async def test_same_name_from_different_providers_keeps_routes_distinct(setup_group):
    group, members, trace = setup_group
    members["B"].provider.specs[0].provider_name = "other-provider"
    await group.tools.refresh()
    b_name = next(
        name
        for name, route in group.tools.routes.items()
        if "B" in route.variants and route.spec.provider_name == "other-provider"
    )
    assert b_name != "echo"
    _, rejected, _, _ = await group.tools.invoke("echo", {"session_id": "B", "text": "wrong"}, "wrong", "")
    assert not rejected.success
    _, accepted, _, _ = await group.tools.invoke(b_name, {"session_id": "B", "text": "right"}, "right", "")
    assert accepted.success
    assert trace[0][:3] == ("B", "echo", {"text": "right"})


def test_sharing_group_identity_is_stable_when_configuration_order_changes(monkeypatch):
    from src.config.official_configs import ChatStreamGroup, TargetItem
    from src.maisaka.focus import manager as manager_module

    sessions = {
        sid: SimpleNamespace(is_group_session=True, group_id=sid, platform="test", user_id=None) for sid in ("A", "B")
    }
    monkeypatch.setattr(manager_module.chat_manager, "get_session_by_session_id", lambda sid: sessions.get(sid))
    first = ChatStreamGroup(targets=[TargetItem(platform="test", item_id="A", rule_type="group")])
    second = ChatStreamGroup(targets=[TargetItem(platform="test", item_id="B", rule_type="group")])
    monkeypatch.setattr(global_config.experimental, "focus_groups", [first, second])
    manager = FocusModeManager()
    original = manager.get_focus_scope_key("A")
    global_config.experimental.focus_groups = [second, first]
    assert manager.get_focus_scope_key("A") == original
    assert manager.get_focus_scope_key("B") != original


@pytest.mark.asyncio
async def test_background_result_is_saved_while_entire_group_is_waiting(setup_group):
    group, members, _ = setup_group
    group._hydrating = True
    group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    group.execute_control("wait", {"seconds": 60}, "A")
    group.register_proactive(members["B"], _incoming("B", "background-finished"))
    await group._checkpoint_task
    payload = group._store.load()
    assert payload["wait_until"] is not None
    assert any(
        "background-finished" in message.processed_plain_text
        for entry in payload["entries"]
        for message in entry.messages
    )
    assert group._worker is None
    await group.stop()


@pytest.mark.asyncio
async def test_focus_no_snapshot_keeps_originals_until_planner_runs(setup_group, monkeypatch):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 2)
    group.entries = [FocusContextEntry([_chat_history(str(i))], {"A"}) for i in range(6)]
    original = list(group.entries)
    builder = AsyncMock()
    monkeypatch.setattr(group_module, "build_context_summary", builder)
    assert group._service.planner_request_snapshot is None
    await group._compact()
    assert group.entries == original
    builder.assert_not_awaited()
