"""Hook 中止、空正文分类与 Planner 工具历史的回归验证。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import json
import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.common.data_models.reply_generation_data_models import LLMCompletionResult, ReplyGenerationResult
from src.common.prompt_i18n import load_prompt
from src.maisaka.builtin_tool import reply, send_emoji
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext, PostProcessedReplyMessage
from src.maisaka.chat_loop_service import register_maisaka_hook_specs
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType
from src.plugin_runtime.host.hook_dispatcher import (
    HookAbortedError,
    HookDispatcher,
    HookDispatchResult,
    HookHandlerExecutionResult,
)
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.services import send_service


def dispatch_abort(hook_name, *, allow_abort=True, reason="消息已过时，请选择最新消息"):
    """通过真实合并逻辑模拟插件现有 RPC 返回协议。"""
    dispatcher = HookDispatcher()
    result = HookDispatchResult(hook_name=hook_name)
    dispatcher._merge_blocking_result(
        HookSpec(name=hook_name, allow_abort=allow_abort),
        SimpleNamespace(entry=SimpleNamespace(full_name="test.guard", error_policy="skip")),
        HookHandlerExecutionResult(
            handler_name="test.guard", plugin_id="test", action="abort", custom_result={"reason": reason},
        ),
        result,
    )
    return result


def test_abort_uses_only_aborting_handler_reason_and_respects_spec():
    result = dispatch_abort("test.hook")
    assert result.aborted and result.stopped_by == "test.guard"
    assert HookAbortedError(result).to_dict() == {
        "hook_name": "test.hook", "handler_name": "test.guard", "reason": "消息已过时，请选择最新消息",
    }
    assert not dispatch_abort("test.hook", allow_abort=False).aborted
    assert dispatch_abort("test.hook", reason="").aborted  # 旧插件无需提供原因。
    registry = HookSpecRegistry()
    register_maisaka_hook_specs(registry)
    assert registry.get_hook_spec("maisaka.replyer.after_response").allow_abort
    for name in ("maisaka.replyer.before_request", "maisaka.replyer.before_model_request", "maisaka.reply.before_post_process"):
        assert registry.get_hook_spec(name).allow_abort
    assert not registry.get_hook_spec("maisaka.planner.before_request").allow_abort


@pytest.fixture
def reply_context(monkeypatch):
    from src.desktop_pet.service import desktop_pet_service

    generator = BaseMaisakaReplyGenerator(llm_client_cls=lambda **kwargs: None, load_prompt_func=load_prompt)
    monkeypatch.setattr(generator, "_persist_reply_preview", Mock())
    monkeypatch.setattr(generator, "_resolve_session_id", lambda stream_id: "test-session")
    monkeypatch.setattr(desktop_pet_service, "notify_thinking", AsyncMock())
    target = SimpleNamespace(message_id="m1")
    runtime = SimpleNamespace(
        session_id="test-session", log_prefix="[test]", _chat_history=[],
        chat_stream=SimpleNamespace(platform="qq", is_group_session=True),
        find_source_message_by_id=lambda message_id: target,
        _update_stage_status=Mock(), record_planner_reply=Mock(),
        build_retro_reply_history=AsyncMock(return_value=[]),
        _reasoning_engine=SimpleNamespace(_refresh_chat_history_visual_placeholders=AsyncMock(return_value=0)),
    )
    context = SimpleNamespace(
        runtime=runtime, build_failure_result=BuiltinToolRuntimeContext.build_failure_result,
        post_process_reply_message_items_async=AsyncMock(return_value=[
            PostProcessedReplyMessage(sequence=MessageSequence([TextComponent("回复正文")]), quote_previous=False),
            PostProcessedReplyMessage(sequence=MessageSequence([TextComponent("第二段")]), quote_previous=False),
            PostProcessedReplyMessage(sequence=MessageSequence([TextComponent("第三段")]), quote_previous=False),
        ]),
    )
    monkeypatch.setattr(reply.replyer_manager, "get_replyer", lambda **kwargs: generator)
    extension = SimpleNamespace(
        prepare=AsyncMock(), targets=[], reply_id="reply-1", parameters={},
    )
    monkeypatch.setattr(reply, "ReplyExtensionExecution", lambda **kwargs: extension)
    return generator, context


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [
    ("model_empty", "model_empty_response"),
    ("rewrite_empty", "plugin_empty_response"),
    ("items_empty", "plugin_empty_response"),
    ("abort", "plugin_aborted"),
])
async def test_replyer_failure_reaches_planner_without_sending(reply_context, monkeypatch, mode, expected):
    generator, context = reply_context
    model_result = LLMResponseResult(output_items=() if mode == "model_empty" else (
        ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("模型正文").build(),
    ))
    generate = AsyncMock(return_value=model_result)
    generator.express_model = SimpleNamespace(task_name="replyer", generate_response_with_context=generate)

    async def hook(name, **kwargs):
        if name.endswith("after_response"):
            if mode == "abort":
                result = dispatch_abort(name)
                # 同时改写正文并要求重生成，也必须优先中止。
                result.kwargs = {**kwargs, "response": "新正文", "retry": True}
                return result
            if mode == "rewrite_empty":
                kwargs["response"] = ""
            if mode == "items_empty":
                kwargs["output_items"] = []
        return HookDispatchResult(hook_name=name, kwargs=kwargs)

    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))
    send = AsyncMock()
    monkeypatch.setattr(send_service, "_send_to_target_with_message", send)
    result = await reply.execute_reply(context, {"msg_id": "m1", "reply_reference": "接话"}, call_id="call1")
    assert not result.success
    history = json.loads(result.get_history_content())
    assert history["error_code"] == expected and history["msg_id"] == "m1"
    assert history["message"] == result.error_message
    if mode == "abort":
        assert history["hook_abort"]["handler_name"] == "test.guard"
        assert history["reason"] == "消息已过时，请选择最新消息"
        assert "勿原样重复调用" in history["message"]
    generate.assert_awaited_once()
    send.assert_not_awaited()
    context.runtime.record_planner_reply.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["maisaka.replyer.before_request", "maisaka.replyer.before_model_request"])
async def test_reply_aborts_before_provider_call(reply_context, monkeypatch, hook_name):
    generator, context = reply_context
    monkeypatch.setattr(generator, "_build_request_messages", lambda **kwargs: [])
    monkeypatch.setattr(generator, "_build_reply_context", AsyncMock(return_value=MaisakaReplyContext()))
    provider = AsyncMock()

    async def generate(*, context_factory, options):
        await context_factory(SimpleNamespace())
        return await provider()

    generator.express_model = SimpleNamespace(task_name="replyer", generate_response_with_context=generate)

    async def hook(name, **kwargs):
        return dispatch_abort(name) if name == hook_name else HookDispatchResult(hook_name=name, kwargs=kwargs)

    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))
    result = await reply.execute_reply(context, {"msg_id": "m1", "reply_reference": "接话"}, call_id="call1")
    history = json.loads(result.get_history_content())
    assert history["error_code"] == "plugin_aborted"
    assert history["hook_abort"]["hook_name"] == hook_name
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_process_abort_precedes_options_and_sending(reply_context, monkeypatch):
    generator, context = reply_context
    generated = ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="正文"))
    monkeypatch.setattr(generator, "generate_reply_with_context", AsyncMock(return_value=(True, generated)))
    hook = AsyncMock(return_value=dispatch_abort("maisaka.reply.before_post_process"))
    context.get_runtime_manager = lambda: SimpleNamespace(invoke_hook=hook)
    result = await reply.execute_reply(context, {"msg_id": "m1", "reply_reference": "接话"}, call_id="call1")
    assert result.structured_content["error_code"] == "plugin_aborted"
    context.post_process_reply_message_items_async.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", [
    "emoji.maisaka.before_select", "emoji.maisaka.after_select", "send_service.before_send",
])
async def test_emoji_abort_uses_same_planner_result(reply_context, monkeypatch, hook_name):
    from src.emoji_system import maisaka_tool

    _, context = reply_context
    emoji = SimpleNamespace(description="开心", file_hash="hash", full_path="unused")
    selector = AsyncMock(return_value=(emoji, "开心"))
    monkeypatch.setattr(send_emoji, "_select_emoji_with_sub_agent", selector)
    monkeypatch.setattr(maisaka_tool, "_serialize_emoji_for_hook", lambda value: {})
    monkeypatch.setattr(maisaka_tool, "resolve_stored_image_path", lambda value: value)
    monkeypatch.setattr(maisaka_tool.ImageUtils, "image_path_to_base64", lambda value: "image-base64")
    monkeypatch.setattr(maisaka_tool.chat_manager, "get_session_by_session_id", lambda value: None)

    async def hook(name, **kwargs):
        return dispatch_abort(name) if name == hook_name else HookDispatchResult(hook_name=name, kwargs=kwargs)

    monkeypatch.setattr(maisaka_tool, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))
    sender = AsyncMock(side_effect=HookAbortedError(dispatch_abort("send_service.before_send")))
    monkeypatch.setattr(send_service, "_send_to_target_with_message", sender)
    monkeypatch.setattr(send_service, "_build_message_sequence_from_custom_message", lambda *args: MessageSequence([]))
    result = await send_emoji.handle_tool(
        context, SimpleNamespace(arguments={"msg_id": "m1"}, tool_name="send_emoji", reasoning="参与聊天"),
    )
    history = json.loads(result.get_history_content())
    assert history["error_code"] == "plugin_aborted"
    assert history["hook_abort"]["hook_name"] == hook_name
    assert history["reason"] == "消息已过时，请选择最新消息"
    assert history["message"] == result.error_message
    if hook_name == "emoji.maisaka.before_select":
        selector.assert_not_awaited()
    if hook_name.startswith("emoji."):
        sender.assert_not_awaited()
    else:
        assert sender.await_args.kwargs["raise_on_hook_abort"]


def test_legacy_emoji_reason_is_normalized_once():
    dispatcher = HookDispatcher()
    result = HookDispatchResult(hook_name="emoji.maisaka.before_select")
    target = SimpleNamespace(entry=SimpleNamespace(full_name="test.guard", error_policy="skip"))
    execution = HookHandlerExecutionResult(
        handler_name="test.guard", plugin_id="test", action="abort", modified_kwargs={"abort_message": "旧版原因"},
    )
    dispatcher._merge_blocking_result(HookSpec(name=result.hook_name), target, execution, result)
    assert result.abort_reason == "旧版原因"
    execution.custom_result = {"reason": "新版原因"}
    dispatcher._merge_blocking_result(HookSpec(name=result.hook_name), target, execution, result)
    assert result.abort_reason == "新版原因"


@pytest.mark.asyncio
async def test_model_context_abort_does_not_retry_or_penalize(monkeypatch):
    from src.llm_models.utils_model import LLMOrchestrator, RequestType

    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.model_for_task = SimpleNamespace(model_list=["first", "second"])
    orchestrator.task_name = "replyer"
    orchestrator.request_type = "maisaka.replyer"
    monkeypatch.setattr(orchestrator, "_resolve_effective_session_id", lambda value: value)
    selection = AsyncMock(return_value=(SimpleNamespace(name="first"), SimpleNamespace(), SimpleNamespace()))
    monkeypatch.setattr(orchestrator, "_select_model", selection)
    usage = Mock()
    monkeypatch.setattr(orchestrator, "_adjust_model_usage", usage)
    provider = AsyncMock()
    monkeypatch.setattr(orchestrator, "_attempt_request_on_model_with_timeout", provider)

    async def context_factory(client):
        dispatch_abort("maisaka.replyer.before_model_request").raise_if_aborted()

    with pytest.raises(HookAbortedError):
        await orchestrator._execute_request(RequestType.RESPONSE, context_factory=context_factory)
    selection.assert_awaited_once()
    provider.assert_not_awaited()
    usage.assert_called_once_with("first", usage_penalty_delta=-1)


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["expression.select.before_select", "expression.select.after_selection"])
async def test_expression_selection_abort_only_skips_selection(monkeypatch, hook_name):
    from src.chat.replyer.maisaka_expression_selector import MaisakaExpressionSelector
    from src.config.config import global_config

    selector = MaisakaExpressionSelector()
    candidates = [{"id": index, "situation": "聊天", "style": "轻松", "count": 1} for index in range(10)]
    monkeypatch.setattr(global_config.expression, "use_vector_expression", False)
    monkeypatch.setattr(selector, "_can_use_expressions", lambda value: True)
    monkeypatch.setattr(selector, "_load_all_expression_candidates", lambda value: candidates)
    monkeypatch.setattr(selector, "_update_last_active_time", Mock())

    async def hook(name, **kwargs):
        return dispatch_abort(name) if name == hook_name else HookDispatchResult(hook_name=name, kwargs=kwargs)

    monkeypatch.setattr(selector, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))
    result = await selector.select_for_reply(
        session_id="test-session", chat_history=[], reply_message=None, reply_reason="接话", sub_agent_runner=None,
    )
    assert result.selected_expression_ids == []
    assert result.hook_abort["reason"] == "消息已过时，请选择最新消息"


@pytest.mark.asyncio
async def test_skipped_expression_selection_does_not_cancel_reply(reply_context, monkeypatch):
    generator, _ = reply_context
    details = HookAbortedError(dispatch_abort("expression.select.before_select")).to_dict()
    monkeypatch.setattr(generator, "_build_reply_context", AsyncMock(return_value=MaisakaReplyContext(hook_abort=details)))
    monkeypatch.setattr(generator, "_build_request_messages", lambda **kwargs: [])

    async def hook(name, **kwargs):
        return HookDispatchResult(hook_name=name, kwargs=kwargs)

    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))

    async def generate(*, context_factory, options):
        await context_factory(SimpleNamespace())
        return LLMResponseResult(output_items=(
            ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("照常回复").build(),
        ))

    generator.express_model = SimpleNamespace(task_name="replyer", generate_response_with_context=generate)
    success, result = await generator.generate_reply_with_context(chat_history=[])
    assert success and result.completion.response_text == "照常回复"
    assert result.metrics.extra["expression_selection_abort"] == details


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["expression.learn.after_extract", "expression.learn.before_upsert"])
async def test_learning_records_abort_reason_without_writing(monkeypatch, hook_name):
    from src.learners import expression_learner

    learner = expression_learner.ExpressionLearner("test-session")
    monkeypatch.setattr(learner, "_prepare_learning_messages", lambda messages: messages)
    monkeypatch.setattr(learner, "_build_multi_learning_messages", AsyncMock(return_value=[None, None, None]))
    monkeypatch.setattr(learner, "_log_learning_context_preview", Mock())
    monkeypatch.setattr(learner, "_filter_expressions", lambda *args: [("聊天", "轻松")])
    monkeypatch.setattr(learner, "_get_session_display_name", lambda value: "测试会话")
    writer = AsyncMock()
    monkeypatch.setattr(learner, "_upsert_expression_to_db", writer)
    monkeypatch.setattr(expression_learner, "parse_expression_response", lambda value: ([("聊天", "轻松", "1")], []))
    monkeypatch.setattr(expression_learner.prompt_manager, "get_prompt", lambda name: SimpleNamespace(add_context=Mock()))
    monkeypatch.setattr(expression_learner.prompt_manager, "render_prompt", AsyncMock(return_value="提示词"))
    monkeypatch.setattr(
        expression_learner.express_learn_model, "generate_response_with_context",
        AsyncMock(return_value=SimpleNamespace(response="模型输出")),
    )

    async def hook(name, **kwargs):
        return dispatch_abort(name) if name == hook_name else HookDispatchResult(hook_name=name, kwargs=kwargs)

    monkeypatch.setattr(learner, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))
    assert not await learner._run_learning_batch([], learning_session_id="test-session")
    assert learner.learning_status == "skipped"
    assert "消息已过时，请选择最新消息" in learner.learning_detail
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["send_service.after_build_message", "send_service.before_send"])
@pytest.mark.parametrize("raise_on_abort", [False, True])
async def test_send_abort_keeps_reason_and_never_calls_platform(monkeypatch, hook_name, raise_on_abort):
    message = SimpleNamespace(message_id="out1")
    monkeypatch.setattr(send_service, "_build_outbound_session_message", lambda **kwargs: message)

    async def hook(name, current, **kwargs):
        return (
            dispatch_abort(name) if name == hook_name else HookDispatchResult(hook_name=name, kwargs=kwargs),
            current,
        )

    monkeypatch.setattr(send_service, "_invoke_send_hook", hook)
    platform = Mock(side_effect=AssertionError("拦截后不得调用平台"))
    monkeypatch.setattr(send_service, "get_platform_io_manager", platform)
    call = send_service._send_to_target_with_message(
        MessageSequence([TextComponent("正文")]), "test-session", raise_on_hook_abort=raise_on_abort,
    )
    if raise_on_abort:
        with pytest.raises(HookAbortedError) as exc:
            await call
        assert exc.value.hook_name == hook_name
        assert exc.value.reason == "消息已过时，请选择最新消息"
    else:
        assert await call is None
    platform.assert_not_called()


@pytest.mark.asyncio
async def test_partial_send_abort_reaches_planner_and_preserves_receipts(reply_context, monkeypatch):
    generator, context = reply_context
    generated = ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="正文"))
    monkeypatch.setattr(generator, "generate_reply_with_context", AsyncMock(return_value=(True, generated)))
    monkeypatch.setattr(reply, "_invoke_before_post_process_hook", AsyncMock(return_value=("正文", {})))
    monkeypatch.setattr(reply, "_resolve_reply_quote", AsyncMock(return_value=(False, None)))
    send = AsyncMock(side_effect=[
        SimpleNamespace(message_id="sent1"), HookAbortedError(dispatch_abort("send_service.before_send")),
    ])
    monkeypatch.setattr(send_service, "_send_to_target_with_message", send)
    result = await reply.execute_reply(context, {"msg_id": "m1", "reply_reference": "接话"}, call_id="call1")
    history = json.loads(result.get_history_content())
    assert history["error_code"] == "plugin_aborted"
    assert history["hook_abort"]["hook_name"] == "send_service.before_send"
    assert history["sent_message_ids"] == ["sent1"]
    assert result.metadata["sent_message_ids"] == ["sent1"]
    assert [receipt["success"] for receipt in result.metadata["send_results"]] == [True, False]
    assert send.await_count == 2
    assert all(call.kwargs["raise_on_hook_abort"] for call in send.await_args_list)
    context.runtime.record_planner_reply.assert_not_called()
