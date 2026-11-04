"""超级创意模式的两阶段生成、最终 Hook 输入和请求语言隔离。"""

from threading import Barrier
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, Mock

import asyncio
import pytest

from src.chat.replyer import maisaka_generator_base
from src.chat.replyer.document_generator import DocumentReplyGenerator
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.i18n import get_locale, use_locale
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.config.model_configs import ModelInfo
from src.desktop_pet.service import desktop_pet_service
from src.llm_models.payload_content.context_item import ContextItem, ContextItemBuilder, RoleType, get_item_text
from src.llm_models.payload_content.tool_option import ToolCall
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult


def _response(text: str, **kwargs: Any) -> LLMResponseResult:
    return LLMResponseResult.from_portable_output(
        response=text, model_name="actual-replyer", prompt_tokens=10, completion_tokens=5, total_tokens=15, **kwargs,
    )


@pytest.fixture
def replyer(monkeypatch):
    monkeypatch.setattr(global_config.chat, "super_creative_mode", True)
    monkeypatch.setattr(global_config.chat, "creative_mode", True)
    monkeypatch.setattr(global_config.debug, "gemini_reply_compatibility", False)
    monkeypatch.setattr(global_config.personality, "multiple_probability", 0)
    monkeypatch.setattr(maisaka_generator_base, "resolve_enable_visual_planner", lambda: False)
    monkeypatch.setattr(desktop_pet_service, "notify_thinking", AsyncMock())
    calls: List[Dict[str, Any]] = []
    responses = [_response("月が笑っています。"), _response("月亮正在微笑。")]
    after_responses: List[str] = []
    state = SimpleNamespace(retry_once=False)
    model_info = ModelInfo(
        model_type="llm", name="actual-replyer", model_identifier="provider-model", api_provider="test-provider",
    )

    async def generate_response_with_context(context_factory, options):
        client = SimpleNamespace(api_provider=SimpleNamespace(client_type="openai"))
        items = await context_factory(client, model_info)
        calls.append({"items": items, "options": options})
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    model = SimpleNamespace(task_name="replyer", generate_response_with_context=generate_response_with_context)
    generator = BaseMaisakaReplyGenerator(
        chat_stream=SimpleNamespace(session_id="test-session", is_group_session=True),
        llm_client_cls=lambda **kwargs: model,
        load_prompt_func=load_prompt,
    )
    monkeypatch.setattr(generator, "_persist_reply_preview", Mock())
    monkeypatch.setattr(generator, "_build_chat_attention_block", lambda session_id: "")
    monkeypatch.setattr(generator, "_build_keyword_reaction_prompt", lambda **kwargs: "")
    monkeypatch.setattr(generator, "_build_reply_context", AsyncMock(return_value=MaisakaReplyContext()))

    async def invoke_hook(name: str, **kwargs: Any) -> HookDispatchResult:
        if name == "maisaka.replyer.after_response":
            after_responses.append(kwargs["response"])
            if state.retry_once and len(after_responses) == 1:
                kwargs.update(retry=True, retry_reason="换个比喻")
        return HookDispatchResult(hook_name=name, kwargs=kwargs)

    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=invoke_hook))
    return SimpleNamespace(
        generator=generator, calls=calls, responses=responses, after_responses=after_responses, state=state,
    )


@pytest.mark.asyncio
async def test_enabled_translates_with_actual_replyer_and_hooks_receive_chinese(replyer) -> None:
    with use_locale("en-US"):
        success, result = await replyer.generator.generate_reply_with_context(chat_history=[], reply_reason="看看月亮")
        assert get_locale() == "en-US"

    assert success
    assert len(replyer.calls) == 2
    assert "今回の返信は必ず日本語で" in get_item_text(replyer.calls[0]["items"][-1])
    assert replyer.calls[0]["options"].model_name is None
    assert replyer.calls[1]["options"].model_name == "actual-replyer"
    assert get_item_text(replyer.calls[1]["items"][-1]) == "月が笑っています。"
    assert "简体中文" in get_item_text(replyer.calls[1]["items"][0])
    assert replyer.after_responses == ["月亮正在微笑。"]
    assert result.completion.response_text == "月亮正在微笑。"
    assert result.text_fragments == ["月亮正在微笑。"]
    assert "简体中文" in result.completion.request_prompt
    assert "月が笑っています。" in result.completion.request_prompt
    assert result.metrics.extra["replyer_translation_count"] == 1
    assert result.metrics.extra["replyer_attempt_count"] == 2
    assert result.metrics.extra["replyer_aggregate_total_tokens"] == 30


@pytest.mark.asyncio
async def test_gemini_analysis_is_removed_before_translation(replyer, monkeypatch) -> None:
    monkeypatch.setattr(global_config.debug, "gemini_reply_compatibility", True)
    replyer.responses[0] = _response("<think>不应翻译</think>月が笑っています。")
    success, result = await replyer.generator.generate_reply_with_context(chat_history=[])

    assert success and result.text_fragments == ["月亮正在微笑。"]
    assert get_item_text(replyer.calls[1]["items"][-1]) == "月が笑っています。"
    assert "不应翻译" not in result.completion.request_prompt


@pytest.mark.asyncio
async def test_disabled_keeps_single_request_and_current_locale(replyer, monkeypatch) -> None:
    monkeypatch.setattr(global_config.chat, "super_creative_mode", False)
    replyer.responses[:] = [_response("直接回复")]
    with use_locale("en-US"):
        success, result = await replyer.generator.generate_reply_with_context(chat_history=[])

    assert success and result.text_fragments == ["直接回复"]
    assert len(replyer.calls) == 1
    prompt = "\n".join(get_item_text(item) for item in replyer.calls[0]["items"])
    assert "今回の返信は必ず日本語で" not in prompt
    assert "You are" in prompt
    assert result.metrics.extra["replyer_translation_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "empty", "tool"])
async def test_failed_translation_never_exposes_japanese_as_reply(replyer, failure: str) -> None:
    translated = {
        "exception": RuntimeError("translation unavailable"),
        "empty": _response(""),
        "tool": _response("", tool_calls=[ToolCall(call_id="tool-1", func_name="unexpected", args={})]),
    }[failure]
    replyer.responses[1] = translated
    success, result = await replyer.generator.generate_reply_with_context(chat_history=[])

    assert not success
    assert result.error_code == "replyer_translation_failed"
    assert result.error_message
    assert result.completion.response_text == ""
    assert result.text_fragments == []
    assert replyer.after_responses == []
    assert len(replyer.calls) == 2


@pytest.mark.asyncio
async def test_hook_retry_regenerates_and_translates_each_reply(replyer) -> None:
    replyer.state.retry_once = True
    replyer.responses.extend([_response("星が踊っています。"), _response("星星正在跳舞。")])
    success, result = await replyer.generator.generate_reply_with_context(chat_history=[])

    assert success and result.text_fragments == ["星星正在跳舞。"]
    assert replyer.after_responses == ["月亮正在微笑。", "星星正在跳舞。"]
    assert len(replyer.calls) == 4
    assert [replyer.calls[index]["options"].model_name for index in (1, 3)] == ["actual-replyer"] * 2
    assert "月亮正在微笑。" in "\n".join(get_item_text(item) for item in replyer.calls[2]["items"])
    assert result.metrics.extra["replyer_translation_count"] == 2
    assert result.metrics.extra["replyer_attempt_count"] == 4
    assert result.metrics.extra["replyer_aggregate_total_tokens"] == 60


@pytest.mark.asyncio
async def test_super_creative_does_not_use_planner_snapshot(replyer) -> None:
    snapshot = SimpleNamespace(request=SimpleNamespace(detached_snapshot=Mock(side_effect=AssertionError("Planner reused"))))
    success, result = await replyer.generator.generate_reply_with_context(chat_history=[], planner_snapshot=snapshot)

    assert success and result.text_fragments == ["月亮正在微笑。"]
    snapshot.request.detached_snapshot.assert_not_called()
    assert len(replyer.calls) == 2


@pytest.mark.parametrize("profile", ["standard", "retro", "mai_replier", "document"])
def test_each_prompt_profile_loads_japanese_templates(replyer, monkeypatch, profile: str) -> None:
    generator = replyer.generator
    if profile == "document":
        generator = object.__new__(DocumentReplyGenerator)
        generator.chat_stream = replyer.generator.chat_stream
    loaded: List[Tuple[str, str]] = []

    def record_load(name: str, **kwargs: Any) -> str:
        loaded.append((name, get_locale()))
        return load_prompt(name, **kwargs)

    monkeypatch.setattr(generator, "_load_prompt", record_load, raising=False)
    with use_locale("en-US"):
        items = generator._build_generation_messages(
            super_creative_mode=True, chat_history=[], reply_message=None, reply_reason="测试材料",
            reply_requirements="保留段落", prompt_profile=profile,
        )
        assert get_locale() == "en-US"

    expected = {
        "standard": "maisaka_replyer", "retro": "retro_replyer",
        "mai_replier": "retro_replyer", "document": "maisaka_replyer_document",
    }
    assert (expected[profile], "ja-JP") in loaded
    assert all(locale == "ja-JP" for _, locale in loaded)
    assert "今回の返信は必ず日本語で" in get_item_text(items[-1])


@pytest.mark.asyncio
async def test_parallel_prompt_builders_keep_separate_locales(replyer, monkeypatch) -> None:
    barrier = Barrier(2)

    def build_messages(**kwargs: Any) -> List[ContextItem]:
        barrier.wait(timeout=5)
        return [ContextItemBuilder().set_role(RoleType.User).add_text_content(get_locale()).build()]

    monkeypatch.setattr(replyer.generator, "_build_request_messages", build_messages)
    with use_locale("en-US"):
        japanese, normal = await asyncio.gather(
            asyncio.to_thread(replyer.generator._build_generation_messages, super_creative_mode=True),
            asyncio.to_thread(replyer.generator._build_generation_messages, super_creative_mode=False),
        )
        assert get_locale() == "en-US"

    assert get_item_text(japanese[0]) == "ja-JP"
    assert get_item_text(normal[0]) == "en-US"
