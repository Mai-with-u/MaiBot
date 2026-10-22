"""插件可复用真正的 Replyer 写作，生成结果不会作为聊天消息发送。"""

from maibot_sdk.context import PluginContext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.chat.message_receive.chat_manager import chat_manager
from src.chat.replyer import document_generator
from src.chat.replyer.document_generator import DocumentReplyGenerator
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.llm_models.payload_content.context_item import get_item_text
from src.plugin_runtime.capabilities.core import RuntimeCoreCapabilityMixin


@pytest.fixture
def generation(monkeypatch):
    session = SimpleNamespace(session_id="real-stream", platform="xiaoheihe", account_id="100")
    runtime = SimpleNamespace(
        _chat_history=["原有聊天消息"],
        run_sub_agent=AsyncMock(return_value=SimpleNamespace(content=' {"selected_ids":[12]} ')),
        enqueue_proactive_task=AsyncMock(),
    )
    monkeypatch.setattr(chat_manager, "get_existing_session_by_session_id", Mock(return_value=session))
    monkeypatch.setattr(heartflow_manager, "get_or_create_heartflow_chat", AsyncMock(return_value=runtime))
    result = SimpleNamespace(
        completion=SimpleNamespace(response_text=' {"title":"标题","paragraphs":["段一","段二"]} '),
        error_message="", selected_expression_ids=[12],
    )
    async def generate(**kwargs):
        assert await kwargs["sub_agent_runner"]("选择表达") == '{"selected_ids":[12]}'
        return True, result
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(side_effect=generate))
    factory = Mock(return_value=generator)
    monkeypatch.setattr(document_generator, "DocumentReplyGenerator", factory)
    return SimpleNamespace(session=session, runtime=runtime, generator=generator, factory=factory)


@pytest.mark.asyncio
async def test_sdk_call_generates_with_real_session_and_preserves_chat(generation):
    manager = RuntimeCoreCapabilityMixin()
    async def rpc(method, plugin_id, payload):
        assert method == "cap.call" and payload["capability"] == "maisaka.reply.generate"
        return await manager._cap_maisaka_reply_generate(plugin_id, payload["capability"], payload["args"])
    ctx = PluginContext("test.plugin", rpc_call=rpc)
    result = await ctx.call_capability(
        "maisaka.reply.generate", stream_id="real-stream", reference="原帖作者、评论者和我的浏览感想",
        format_instruction="返回标题与分段正文的 JSON",
    )
    assert result == {
        "success": True, "text": '{"title":"标题","paragraphs":["段一","段二"]}',
        "selected_expression_ids": [12],
    }
    generation.factory.assert_called_once_with(chat_stream=generation.session, request_type="plugin.replyer", model_name="")
    args = generation.generator.generate_reply_with_context.call_args.kwargs
    assert args["stream_id"] == "real-stream" and args["reply_message"] is None
    assert args["chat_history"] == []
    assert args["reply_tool_args"] == {
        "reply_reference": "原帖作者、评论者和我的浏览感想", "reply_style": "长回复",
        "_plugin_reply_prompt": "返回标题与分段正文的 JSON",
    }
    assert generation.runtime._chat_history == ["原有聊天消息"]
    generation.runtime.enqueue_proactive_task.assert_not_called()


@pytest.mark.parametrize("args", [
    {"reference": "材料"}, {"stream_id": "s", "reference": " "},
    {"stream_id": "s", "reference": []}, {"stream_id": "s", "reference": "x" * 500001},
    {"stream_id": "s", "reference": "材料", "format_instruction": []},
])
@pytest.mark.asyncio
async def test_invalid_arguments_never_start_replyer(generation, args):
    result = await RuntimeCoreCapabilityMixin()._cap_maisaka_reply_generate("p", "maisaka.reply.generate", args)
    assert not result["success"] and result["error"]
    generation.factory.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_stream_does_not_create_session(generation, monkeypatch):
    monkeypatch.setattr(chat_manager, "get_existing_session_by_session_id", Mock(return_value=None))
    result = await RuntimeCoreCapabilityMixin()._cap_maisaka_reply_generate("p", "maisaka.reply.generate", {
        "stream_id": "missing", "reference": "材料",
    })
    assert not result["success"] and "未找到" in result["error"]
    heartflow_manager.get_or_create_heartflow_chat.assert_not_called()
    generation.factory.assert_not_called()


@pytest.mark.parametrize("success,text,error", [(False, "", "模型错误"), (True, " ", "")])
@pytest.mark.asyncio
async def test_failure_and_empty_output_are_reported(generation, success, text, error):
    generation.generator.generate_reply_with_context.side_effect = None
    generation.generator.generate_reply_with_context.return_value = (success, SimpleNamespace(
        completion=SimpleNamespace(response_text=text), error_message=error,
    ))
    result = await RuntimeCoreCapabilityMixin()._cap_maisaka_reply_generate("p", "maisaka.reply.generate", {
        "stream_id": "real-stream", "reference": "材料",
    })
    assert not result["success"]
    assert result["error"] == (error if not success else "Replyer 返回了空文本")


@pytest.mark.parametrize("retro", [False, True])
def test_actual_replyer_prompts_keep_personality_materials_and_post_format(monkeypatch, retro):
    monkeypatch.setattr(global_config.debug, "replyer_retro_prompt", retro)
    monkeypatch.setattr(global_config.personality, "personality", "喜欢游戏和观察社区的测试人格")
    monkeypatch.setattr(global_config.personality, "multiple_probability", 0)
    session = SimpleNamespace(is_group_session=True, platform="xiaoheihe", group_id="post:123:thread:browse", user_id="")
    monkeypatch.setattr(chat_manager, "get_session_by_session_id", Mock(return_value=session))
    monkeypatch.setattr(chat_manager, "get_existing_session_by_session_id", Mock(return_value=session))
    generator = object.__new__(DocumentReplyGenerator)
    generator.chat_stream = None
    generator._load_prompt = load_prompt
    items = generator._build_request_messages(
        chat_history=[], reply_message=None, reply_reason="", stream_id="real-stream",
        reply_requirements='仅输出 {"title":"标题","paragraphs":["段一","段二"]}',
        reply_tool_args={"reply_reference": "真实浏览材料：原帖作者甲、评论者乙", "reply_style": "长回复"},
    )
    text = "\n".join(get_item_text(item) for item in items)
    assert "喜欢游戏和观察社区的测试人格" in text
    assert "原帖作者甲、评论者乙" in text
    assert '"paragraphs"' in text
    assert "你正在群里聊天" not in text
    assert "长回复" not in text
    assert '"paragraphs"' in get_item_text(items[-1])


def test_document_model_is_explicit_and_hook_choice_has_priority():
    generator = object.__new__(DocumentReplyGenerator)
    generator.document_model_name = "long-form-model"
    assert generator._build_generation_options(None).model_name == "long-form-model"
    assert generator._build_generation_options("hook-model").model_name == "hook-model"
