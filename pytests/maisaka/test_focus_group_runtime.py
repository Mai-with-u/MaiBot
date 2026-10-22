"""Focus 共享思考的跨会话路由、等待、并发输入和恢复边界。"""

from base64 import b64decode
from datetime import datetime
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
from src.llm_models.payload_content.tool_option import ToolCall
from src.llm_models.payload_content.context_item import (
    ContextImagePart,
    ContextItemMeta,
    ReasoningItem,
    ReasoningRepresentation,
    RoleType,
)
from src.maisaka.chat_loop_service import ChatResponse
from src.maisaka import chat_loop_service as chat_service_module
from src.maisaka.context.messages import (
    InternalContextMessage,
    ModelOutputContextMessage,
    SessionBackedMessage,
    ToolResultMessage,
)
from src.maisaka.context.emoji_candidates import EmojiCandidateMessage
from src.maisaka.focus import group_runtime as group_module
from src.maisaka.focus import context as context_module
from src.maisaka.focus.context import FocusCheckpointStore, FocusContextEntry
from src.maisaka.focus.group_runtime import FocusGroupRuntime
from src.maisaka.focus.manager import FocusModeManager
from src.maisaka.focus.tools import routed_schema
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting


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


@pytest.mark.asyncio
async def test_compaction_keeps_source_progress_and_recent_complete_tool_turn(setup_group, monkeypatch):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 4)
    summary_requests = []

    clients = Mock()
    monkeypatch.setattr(chat_service_module, "LLMServiceClient", clients)

    class SummaryService(chat_service_module.MaisakaChatLoopService):
        async def chat_loop_step(self, messages, **kwargs):
            # 使用真实的请求注册与客户端选择，防止摘要请求在首次压缩时才暴露漏注册。
            self._get_llm_chat_client(kwargs["request_kind"])
            summary_requests.extend(message.processed_plain_text for message in messages)
            return _response("A 的事实；B 的未完成任务。")

    monkeypatch.setattr(group_module, "MaisakaChatLoopService", lambda **kwargs: SummaryService(**kwargs))
    group.entries = [FocusContextEntry([_history(str(i))], {"A" if i % 2 else "B"}, i) for i in range(1, 7)]
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
    await group._compact()
    assert group.entries[-1] is recent
    assert group.entries[0].session_ids == {"A", "B"}
    assert group.entries[0].progress["A"] > 0
    assert group.entries[0].progress["B"] > 0
    assert "session_ids=" in "\n".join(summary_requests)
    clients.assert_called_once_with(task_name="utils", request_type="maisaka.focus_summary", session_id="")


@pytest.mark.asyncio
async def test_empty_summary_preserves_original_context(setup_group, monkeypatch):
    group, _, _ = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 2)
    service = SimpleNamespace(chat_loop_step=AsyncMock(return_value=_response("")))
    monkeypatch.setattr(group_module, "MaisakaChatLoopService", lambda **kwargs: service)
    group.entries = [FocusContextEntry([_history(str(i))], {"A"}) for i in range(5)]
    original = list(group.entries)
    with pytest.raises(RuntimeError, match="原始内容已保留"):
        await group._compact()
    assert group.entries == original


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
