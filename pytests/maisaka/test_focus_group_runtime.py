"""Focus 共享思考的跨会话路由、等待、并发输入和恢复边界。"""

from base64 import b64decode
from datetime import datetime, timedelta
from io import BytesIO
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
from src.llm_models.exceptions import (
    EmptyResponseException,
    LLMTaskTimeoutError,
    ModelAttemptFailed,
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
from src.maisaka.chat_loop_service import ChatResponse, MaisakaChatLoopService
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
from src.maisaka.focus.context import FocusCheckpointStore, FocusContextEntry
from src.maisaka.focus.group_runtime import FocusGroupRuntime
from src.maisaka.focus.manager import FocusModeManager
from src.maisaka.focus.tools import routed_schema
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting
from src.services.llm_service import LLMServiceClient


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


@pytest.fixture
def planner_failure_group(setup_group, monkeypatch):
    """运行真实 Focus/Planner 方法，只替换模型请求、插件 hook 和外部参考资料。"""
    group, members, trace = setup_group
    group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
    group._build_references = AsyncMock(return_value=[])

    async def hook(name, **kwargs):
        return SimpleNamespace(kwargs=kwargs)

    manager = SimpleNamespace(invoke_hook=AsyncMock(side_effect=hook), supervisors=[])
    monkeypatch.setattr(MaisakaChatLoopService, "_get_runtime_manager", staticmethod(lambda: manager))
    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", False)
    request = AsyncMock(side_effect=NetworkConnectionError("mock final model failure"))
    monkeypatch.setattr(LLMServiceClient, "generate_response_with_context", request)
    return group, members, trace, request


async def _register_user(group, member, message_id, *, at=True):
    message = _incoming(member.session_id, message_id, at=at)
    history = SessionBackedMessage.from_session_message(
        message,
        raw_message=message.raw_message,
        visible_text=message_id,
    )
    await group.register_message(member, message, history)
    return message


@pytest.mark.asyncio
async def test_final_planner_failure_stops_member_stage(planner_failure_group):
    """最终 Planner 故障应让共享组及所有成员进入失败停止状态。"""
    group, members, _, request = planner_failure_group
    try:
        await _register_user(group, members["A"], "first")
        await asyncio.wait_for(asyncio.shield(group._worker), 2)
        assert request.await_count == 1
        assert group.state == "failed"
        assert all(member._agent_state == member._STATE_STOP for member in members.values())
    finally:
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        NetworkConnectionError("offline"),
        EmptyResponseException(),
        LLMTaskTimeoutError("planner", "mock", 1),
        RespNotOkException(429),
        RespNotOkException(503),
    ],
)
async def test_new_user_recovers_failed_planner_without_queue_retry(planner_failure_group, error):
    """仅故障后新用户输入可唤醒 Worker，而非重试旧输入。"""
    group, members, trace, request = planner_failure_group
    request.side_effect = error
    completed = asyncio.Event()

    async def success(*args, **kwargs):
        completed.set()
        return LLMResponseResult.from_portable_output(response="new turn")

    try:
        await _register_user(group, members["A"], "first")
        old_worker = group._worker
        await asyncio.wait_for(asyncio.shield(old_worker), 2)
        group._ensure_worker()
        group._event.set()
        await asyncio.sleep(0)
        assert group._worker is old_worker
        assert request.await_count == 1
        request.side_effect = success
        await _register_user(group, members["B"], "fresh")
        assert group._worker is not old_worker
        await asyncio.wait_for(completed.wait(), 2)
        assert not group._failed
        assert any(message.processed_plain_text == "fresh" for message in group.history)
        assert request.await_count == 2
        assert not trace
    finally:
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("synthetic final planner failure"),
        ValueError("invalid request"),
        TimeoutError("unclassified timeout"),
        RespNotOkException(400),
        RespNotOkException(401),
        RespParseException(),
        ReqAbortException(),
        ModelAttemptFailed("unknown attempts"),
    ],
)
async def test_unclassified_planner_failures_remain_fail_stop(planner_failure_group, error):
    """无法确认来源或安全性的异常必须维持停止。"""
    group, members, trace, request = planner_failure_group
    request.side_effect = error
    try:
        await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        await _register_user(group, members["B"], "fresh")
        assert group._worker is worker
        assert group._failed
        assert group._recovery_token is None
        assert all(member._agent_state == "stop" for member in members.values())
        assert request.await_count == 1
        assert not trace
        assert any(
            message.processed_plain_text == "fresh"
            for entry in group._store.load()["entries"]
            for message in entry.messages
        )
    finally:
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["hook", "references", "tool_refresh"])
async def test_model_exception_outside_request_is_not_recoverable(planner_failure_group, boundary):
    """仅真实模型请求边界的异常有恢复资格。"""
    group, members, _, request = planner_failure_group
    failure = AsyncMock(side_effect=NetworkConnectionError("not the planner request"))
    if boundary == "hook":
        group._service._get_runtime_manager().invoke_hook = failure
    elif boundary == "references":
        group._build_references = failure
    else:
        group.tools.refresh = failure
    try:
        await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        await _register_user(group, members["B"], "fresh")
        assert group._worker is worker
        assert group._recovery_token is None
        failure.assert_awaited_once()
        request.assert_not_awaited()
    finally:
        await group.stop()


@pytest.mark.asyncio
async def test_plugin_appearing_during_request_build_blocks_recovery(planner_failure_group, monkeypatch):
    """请求构建期间加载过插件也不能忽略可能的副作用。"""
    group, members, _, request = planner_failure_group
    manager = group._service._get_runtime_manager()
    build = group._service._build_request_messages

    def plugin_loaded(*args, **kwargs):
        manager.supervisors = [object()]
        return build(*args, **kwargs)

    async def plugin_unloaded(name, **kwargs):
        manager.supervisors.clear()
        return SimpleNamespace(kwargs=kwargs)

    monkeypatch.setattr(group._service, "_build_request_messages", plugin_loaded)
    manager.invoke_hook.side_effect = plugin_unloaded
    try:
        await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        assert not manager.supervisors
        await _register_user(group, members["B"], "fresh")
        assert group._worker is worker
        assert group._recovery_token is None
        assert request.await_count == 1
    finally:
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["duplicate", "delayed", "internal", "no_history", "proactive", "sent"])
async def test_only_new_user_input_can_consume_recovery(planner_failure_group, signal):
    """机器人、重复、旧时间戳和内部消息不能消费恢复令牌。"""
    group, members, _, request = planner_failure_group
    try:
        old_message = await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        token = group._recovery_token
        assert token is not None
        message = _incoming("B", "not-new-user", at=True)
        history = SessionBackedMessage.from_session_message(
            message,
            raw_message=message.raw_message,
            visible_text="not-new-user",
        )
        if signal == "duplicate":
            await group.register_message(members["A"], old_message, _chat_history("first"))
        elif signal == "proactive":
            group.register_proactive(members["B"], message)
            await group._checkpoint_task
        elif signal == "sent":
            group.record_sent_message(members["B"], history)
            await group._checkpoint_task
        else:
            if signal == "delayed":
                message.received_at = datetime.fromtimestamp(token[1]) - timedelta(seconds=1)
            elif signal == "internal":
                history.source_kind = "guided_reply"
            else:
                history = None
            await group.register_message(members["B"], message, history)
        group._ensure_worker()
        assert group._worker is worker
        assert group._failed
        assert request.await_count == 1
    finally:
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidation", ["generation", "member", "remove", "cancel", "stop", "wait", "hydrate"])
async def test_recovery_token_cannot_cross_lifecycle_change(planner_failure_group, invalidation):
    """成员变化、取消或控制事件会撤销先前恢复资格。"""
    group, members, _, request = planner_failure_group
    try:
        await _register_user(group, members["A"], "first")
        await asyncio.wait_for(asyncio.shield(group._worker), 2)
        assert group._recovery_token is not None
        if invalidation == "generation":
            group._generation += 1
        elif invalidation == "member":
            members["A"]._running = False
        elif invalidation == "remove":
            await group.remove_member("A", clear_context=False)
        elif invalidation == "cancel":
            await group._cancel_worker()
        elif invalidation == "stop":
            await group.stop()
        elif invalidation == "wait":
            group.execute_control("wait", {"seconds": 60}, "A")
        else:
            group._hydrating = True
        worker = group._worker
        await _register_user(group, members["B"], "fresh")
        assert group._worker is worker
        assert group._failed
        assert request.await_count == 1
    finally:
        await group.stop()


@pytest.mark.asyncio
async def test_recovery_waits_for_checkpoint_and_concurrent_inputs_start_one_worker(planner_failure_group):
    """输入必须持久化后恢复，且并发只创建一个 Worker。"""
    group, members, _, request = planner_failure_group
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def checkpoint():
        entered.set()
        await release.wait()
        await FocusGroupRuntime.checkpoint(group)

    async def success(*args, **kwargs):
        completed.set()
        return LLMResponseResult.from_portable_output(response="new turn")

    tasks = []
    try:
        await _register_user(group, members["A"], "first")
        old_worker = group._worker
        await asyncio.wait_for(asyncio.shield(old_worker), 2)
        group.checkpoint = checkpoint
        tasks = [asyncio.create_task(_register_user(group, members[sid], "fresh-" + sid, at=False)) for sid in members]
        await asyncio.wait_for(entered.wait(), 2)
        assert group._failed
        assert group._worker is old_worker
        request.side_effect = success
        release.set()
        await asyncio.gather(*tasks)
        new_worker = group._worker
        assert new_worker is not old_worker
        assert group._recovery_token is None
        await asyncio.wait_for(completed.wait(), 2)
        group._ensure_worker()
        assert group._worker is new_worker
        assert request.await_count == 2
        assert all(any(message.processed_plain_text == "fresh-" + sid for message in group.history) for sid in members)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await group.stop()


@pytest.mark.asyncio
async def test_recovery_first_request_includes_new_user_before_debounce(planner_failure_group):
    """防抖期间的新消息必须进入恢复后的首轮 Planner 请求。"""
    group, members, _, request = planner_failure_group
    completed = asyncio.Event()
    observed = []

    async def success(context_factory, *args, **kwargs):
        observed.extend(context_factory(None))
        completed.set()
        return LLMResponseResult.from_portable_output(response="new turn")

    try:
        await _register_user(group, members["A"], "old-input")
        await asyncio.wait_for(asyncio.shield(group._worker), 2)
        members["B"]._wait_for_message_quiet_period = asyncio.Event().wait
        request.side_effect = success
        await _register_user(group, members["B"], "fresh-during-debounce", at=False)
        await asyncio.wait_for(completed.wait(), 2)
        assert any("fresh-during-debounce" in context_module.items.get_item_text(item) for item in observed)
        assert request.await_count == 2
    finally:
        await group.stop()


@pytest.mark.asyncio
async def test_pending_input_at_failure_cannot_restart_worker(planner_failure_group):
    """失败前排队的输入不可自行唤醒已结束的 Worker。"""
    group, members, _, request = planner_failure_group
    entered = asyncio.Event()
    release = asyncio.Event()

    async def failure(*args, **kwargs):
        entered.set()
        await release.wait()
        raise NetworkConnectionError("final request failed")

    request.side_effect = failure
    try:
        await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(entered.wait(), 2)
        await _register_user(group, members["B"], "old-pending")
        release.set()
        await asyncio.wait_for(asyncio.shield(worker), 2)
        await asyncio.gather(*group._debounce_tasks.values())
        group._ensure_worker()
        group._event.set()
        assert group._worker is worker
        assert request.await_count == 1
        assert group._pending["B"][0].messages[0].processed_plain_text == "old-pending"
        assert any(
            message.processed_plain_text == "old-pending"
            for entry in group._store.load()["entries"]
            for message in entry.messages
        )
    finally:
        release.set()
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_new_input_checkpoint_failure_revokes_recovery(planner_failure_group, monkeypatch, cancel):
    """新输入的 checkpoint 保存失败必须撤销恢复资格。"""
    group, members, _, request = planner_failure_group
    entered = asyncio.Event()
    task = None
    original_save = group._store.save

    async def interrupted_checkpoint():
        entered.set()
        await FocusGroupRuntime.checkpoint(group)

    try:
        await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        if cancel:
            await group._checkpoint_lock.acquire()
            group.checkpoint = interrupted_checkpoint
            task = asyncio.create_task(_register_user(group, members["B"], "cancelled"))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            group._checkpoint_lock.release()
            group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
        else:
            monkeypatch.setattr(group._store, "save", Mock(side_effect=OSError("disk full")))
            with pytest.raises(OSError, match="disk full"):
                await _register_user(group, members["B"], "unsaved")
            monkeypatch.setattr(group._store, "save", original_save)
        await _register_user(group, members["B"], "fresh")
        assert group._worker is worker
        assert group._failed
        assert group._recovery_token is None
        assert request.await_count == 1
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if group._checkpoint_lock.locked():
                group._checkpoint_lock.release()
        group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
        monkeypatch.setattr(group._store, "save", original_save)
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stage", "checkpoint", "cancel_checkpoint"])
async def test_worker_terminal_failure_cannot_be_recovered(planner_failure_group, failure):
    """旧 Worker 的异常结束或取消不允许被错误恢复。"""
    group, members, _, request = planner_failure_group
    entered = asyncio.Event()

    def stage(name, *args, **kwargs):
        if name == "失败":
            raise RuntimeError("failed stage update")

    async def checkpoint():
        if group._failed:
            if failure == "cancel_checkpoint":
                entered.set()
                await asyncio.Event().wait()
            raise OSError("failed terminal checkpoint")
        await FocusGroupRuntime.checkpoint(group)

    try:
        if failure == "stage":
            members["A"]._update_stage_status.side_effect = stage
        else:
            group.checkpoint = checkpoint
        await _register_user(group, members["A"], "first")
        worker = group._worker
        if failure == "cancel_checkpoint":
            await asyncio.wait_for(entered.wait(), 2)
            worker.cancel()
        expected = {"stage": RuntimeError, "checkpoint": OSError, "cancel_checkpoint": asyncio.CancelledError}[failure]
        with pytest.raises(expected):
            await asyncio.wait_for(asyncio.shield(worker), 2)
        group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
        await _register_user(group, members["B"], "fresh")
        assert group._failed
        assert group._worker is worker
        assert all(member._agent_state == "stop" for member in members.values())
        assert request.await_count == 1
    finally:
        group.checkpoint = lambda: FocusGroupRuntime.checkpoint(group)
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "unknown", "partial_send", "exception"])
async def test_tool_history_blocks_final_planner_recovery_even_after_compaction(
    planner_failure_group,
    monkeypatch,
    outcome,
):
    """执行过工具后即使压缩历史，也不得误判可安全恢复。"""
    group, members, trace, request = planner_failure_group

    async def tool(invocation, context=None):
        trace.append(invocation)
        if outcome == "exception":
            raise NetworkConnectionError("tool failure is not a Planner failure")
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=outcome == "success",
            content="tool outcome",
            metadata={"execution_state": outcome},
        )

    for member in members.values():
        member.provider.invoke = tool
    request.side_effect = [
        LLMResponseResult.from_portable_output(
            response="tool",
            tool_calls=[ToolCall("call", "echo", {"session_id": "A", "text": "action"})],
        ),
        NetworkConnectionError("final Planner failed after tool"),
    ]
    try:
        await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        assert request.await_count == 2
        assert len(trace) == 1
        assert group._active_batch is None
        # 真正压掉包含工具结果的旧段；不能仅靠当前 history 检测未知副作用。
        group.entries.extend(FocusContextEntry([_chat_history(str(i))], {"A"}) for i in range(6))
        monkeypatch.setattr(global_config.chat, "max_context_size", 2)
        monkeypatch.setattr(group_module, "build_context_summary", AsyncMock(return_value=None))
        _seed_planner_snapshot(group)
        await group._compact()
        assert not any(isinstance(message, ToolResultMessage) for message in group.history)
        await _register_user(group, members["B"], "fresh")
        assert group._worker is worker
        assert group._failed
        assert group._recovery_token is None
        assert request.await_count == 2
        assert len(trace) == 1
    finally:
        await group.stop()


@pytest.mark.asyncio
async def test_active_tool_batch_cannot_grant_recovery(planner_failure_group):
    """未闭合工具批次和副作用未明时禁止恢复。"""
    group, members, _, request = planner_failure_group
    group._active_batch = FocusContextEntry([], {"A"})
    try:
        await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        await _register_user(group, members["B"], "fresh")
        assert group._worker is worker
        assert group._recovery_token is None
        assert request.await_count == 1
    finally:
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_restore_keeps_baseline_format_contract_and_disables_recovery(
    planner_failure_group,
    monkeypatch,
    version,
):
    """恢复旧格式检查点不迁移数据且不授予新增恢复资格。"""
    from src.maisaka.focus.manager import focus_mode_manager

    group, members, _, request = planner_failure_group
    # 旧格式可能只剩摘要，无法从当前工具条目证明过去没有副作用。
    summary = ContextSummaryMessage(content="prior compacted history", timestamp=datetime.now())
    group.entries = [FocusContextEntry([summary], {"A"}, 1)]
    group._sequence = 1
    await group.checkpoint()
    payload = json.loads(group._store.path.read_text(encoding="utf-8"))
    payload["version"] = version
    group._store.path.write_text(json.dumps(payload), encoding="utf-8")
    original = group._store.path.read_bytes()
    monkeypatch.setattr(group_module.chat_manager, "get_existing_session_by_session_id", lambda sid: object())
    monkeypatch.setattr(focus_mode_manager, "is_enabled_for_session", lambda sid: True)
    monkeypatch.setattr(focus_mode_manager, "get_focus_scope_key", lambda sid: group.scope_id)
    if version == 2:
        # 官方基线仅接受顶层 V1；不能把拒绝未知版本改成隐式迁移或覆盖。
        with pytest.raises(ValueError, match="版本"):
            await group.restore()
        assert group._store.path.read_bytes() == original
        return
    try:
        await group.restore()
        assert group.history[0].content == summary.content
        assert group._store.path.read_bytes() == original
        await _register_user(group, members["A"], "first")
        worker = group._worker
        await asyncio.wait_for(asyncio.shield(worker), 2)
        await _register_user(group, members["B"], "fresh")
        assert group._worker is worker
        assert group._recovery_token is None
        assert request.await_count == 1
    finally:
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
