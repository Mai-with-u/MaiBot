from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError

import asyncio
import pytest

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.data_models.reply_generation_data_models import LLMCompletionResult, ReplyGenerationResult
from src.config.model_configs import ModelInfo
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType, get_item_text
from src.maisaka import reply_supplement
from src.maisaka.builtin_tool import reply
from src.maisaka.context.compaction import PlannerRequestSnapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_succeeds", [True, False])
async def test_invalid_plan_is_regenerated_with_feedback(monkeypatch, retry_succeeds):
    request = ResponseRequest(
        model_info=ModelInfo(model_type="llm", model_identifier="test", name="test", api_provider="test"),
        context_items=[ContextItemBuilder().set_role(RoleType.System).add_text_content("Planner prefix").build()],
    )
    snapshot = PlannerRequestSnapshot(request=request, history=())
    resources = reply_supplement.SupplementResources({}, {}, {}, False, False, False)
    monkeypatch.setattr(reply_supplement.SupplementResources, "capture", lambda *args: resources)
    monkeypatch.setattr(reply_supplement.global_config.debug, "save_maisaka_reasoning", False)
    responses = ["刚睡醒，看到群里给我看饿了都。", '{"msg_id":null,"parts":[{"type":"reply"}]}']
    if not retry_succeeds:
        responses[1] = "还是聊天正文"
    requests = []

    async def generate(self, current_request):
        requests.append(current_request)
        return LLMResponseResult(output_items=(
            ContextItemBuilder().set_role(RoleType.Assistant).add_text_content(responses[len(requests) - 1]).build(),
        ))

    monkeypatch.setattr(reply_supplement.LLMServiceClient, "generate_response_from_snapshot", generate)
    if retry_succeeds:
        supplement = await reply_supplement.generate_reply_supplement(snapshot, "发言指导", "test", False)
        assert supplement.components == [None]
    else:
        with pytest.raises(ValidationError):
            await reply_supplement.generate_reply_supplement(snapshot, "发言指导", "test", False)
    assert len(requests) == 2
    assert "ValidationError" in get_item_text(requests[1].context_items[-1])
    assert all(item.response_format is None for item in requests)
    assert len(request.context_items) == 1


@pytest.mark.asyncio
async def test_cancellation_does_not_regenerate(monkeypatch):
    generate = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(reply_supplement, "_generate_reply_supplement_once", generate)
    with pytest.raises(asyncio.CancelledError):
        await reply_supplement.generate_reply_supplement(None, "指导", "test", False)
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_failed_supplement_keeps_body_generation_running(monkeypatch):
    supplement_failed = asyncio.Event()
    body = ReplyGenerationResult(completion=LLMCompletionResult(response_text="正文已生成"))

    async def generate_body(**kwargs):
        await supplement_failed.wait()
        await asyncio.sleep(0)
        return True, body

    async def generate_supplement():
        supplement_failed.set()
        raise ValueError("重生成后仍无效")

    runtime = SimpleNamespace(
        session_id="test", chat_stream=SimpleNamespace(platform="test"), _chat_history=[], log_prefix="test",
        _update_stage_status=Mock(), build_retro_reply_history=Mock(),
    )
    tool_ctx = SimpleNamespace(runtime=runtime, build_failure_result=Mock())
    extension = SimpleNamespace(prepare=AsyncMock(), targets=[], reply_id="test")
    monkeypatch.setattr(reply, "ReplyExtensionExecution", Mock(return_value=extension))
    monkeypatch.setattr(reply.replyer_manager, "get_replyer", Mock(return_value=SimpleNamespace(
        generate_reply_with_context=generate_body,
    )))
    # 在正常的正文后处理入口通过 Hook 中止，避免测试实际发送消息。
    aborted = reply.HookAbortedError(SimpleNamespace(hook_name="test", stopped_by="test", abort_reason="test"))
    hook = AsyncMock(side_effect=aborted)
    monkeypatch.setattr(reply, "_invoke_before_post_process_hook", hook)
    result = await reply.execute_reply(
        tool_ctx, {"msg_id": "message", "reply_reference": "指导"}, call_id="test",
        target_message=SimpleNamespace(message_id="message"), supplement_runner=generate_supplement,
    )
    assert result.structured_content["error_code"] == "plugin_aborted"
    assert hook.call_args.args[1] == "正文已生成"
    tool_ctx.build_failure_result.assert_not_called()
