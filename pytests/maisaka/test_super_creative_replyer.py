"""超级创意模式的两阶段生成、最终 Hook 输入和请求语言隔离。"""

from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, Mock

import asyncio
import json

import pytest

from src.chat.replyer import maisaka_generator_base
from src.chat.replyer.creative_language import load_reply_prompt
from src.chat.replyer.document_generator import DocumentReplyGenerator
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.i18n import get_locale, use_locale
from src.common.prompt_i18n import PROMPTS_ROOT, clear_prompt_cache, extract_prompt_placeholders, load_prompt, load_prompt_template
from src.config.config import global_config
from src.config.model_configs import ModelInfo
from src.desktop_pet.service import desktop_pet_service
from src.llm_models.payload_content.context_item import SystemMessageItem, UserMessageItem, get_item_text
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.display.prompt_preview_logger import PromptPreviewLogger
from src.maisaka.display.reasoning_steps import reasoning_trace_id
from src.maisaka.display.runtime_mixin import MaisakaRuntimeDisplayMixin
from src.maisaka.scenes.private.prompting import PrivatePromptProfile
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult
from src.webui.routers import reasoning_process


def _response(text: str, **kwargs: Any) -> LLMResponseResult:
    return LLMResponseResult.from_portable_output(
        response=text, model_name="actual-replyer", prompt_tokens=10, completion_tokens=5, total_tokens=15, **kwargs,
    )


@pytest.fixture
def replyer(monkeypatch):
    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", False)
    monkeypatch.setattr(global_config.experimental, "super_creative_mode", True)
    monkeypatch.setattr(global_config.experimental, "super_creative_language", "ja-JP")
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

    state.model_info = model_info

    async def generate_response_with_context(context_factory, options):
        client = SimpleNamespace(api_provider=SimpleNamespace(client_type="openai"))
        items = await context_factory(client, state.model_info)
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
    language_instruction = "今回の返信は日本語で"
    system = next(item for item in replyer.calls[0]["items"] if isinstance(item, SystemMessageItem))
    assert language_instruction in get_item_text(system)
    assert replyer.calls[0]["options"].model_name is None
    assert replyer.calls[1]["options"].model_name == "actual-replyer"
    assert get_item_text(replyer.calls[1]["items"][-1]).endswith("待翻译内容：月が笑っています。")
    assert len(replyer.calls[1]["items"]) == 1
    assert isinstance(replyer.calls[1]["items"][0], UserMessageItem)
    assert "请将以下内容翻译成中文" in get_item_text(replyer.calls[1]["items"][0])
    assert replyer.after_responses == ["月亮正在微笑。"]
    assert result.completion.response_text == "月亮正在微笑。"
    assert result.text_fragments == ["月亮正在微笑。"]
    assert "请将以下内容翻译成中文" in result.completion.request_prompt
    assert "月が笑っています。" in result.completion.request_prompt
    assert result.metrics.extra["replyer_translation_count"] == 1
    assert result.metrics.extra["replyer_attempt_count"] == 2
    assert result.metrics.extra["replyer_aggregate_total_tokens"] == 30


@pytest.mark.asyncio
async def test_creative_reply_and_translation_are_separate_reasoning_records(replyer, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", True)
    monkeypatch.setattr(PromptPreviewLogger, "_BASE_DIR", tmp_path)
    monkeypatch.setattr(PromptPreviewLogger, "_submit", PromptPreviewLogger._write_task)
    monkeypatch.setattr(reasoning_process, "PROMPT_LOG_ROOT", tmp_path)
    monkeypatch.setattr(
        "src.maisaka.display.prompt_cli_renderer._build_webui_local_base_url", lambda: "http://127.0.0.1:7999",
    )
    monkeypatch.setattr(
        "src.maisaka.display.runtime_mixin.runtime_context",
        lambda: SimpleNamespace(public_base_url="http://127.0.0.1:7999"),
    )
    replyer.responses[:] = [
        _response("月が笑っています。", reasoning="构思月亮的比喻"),
        _response("月亮正在微笑。", reasoning="保留拟人语气"),
    ]

    trace_token = reasoning_trace_id.set("c" * 32)
    try:
        success, result = await replyer.generator.generate_reply_with_context(chat_history=[], tool_call_id="reply-1")
    finally:
        reasoning_trace_id.reset(trace_token)

    assert success
    assert "replyer_translation" in result.monitor_detail["prompt_html_uri"]
    replyer.generator._persist_reply_preview.assert_not_called()
    MaisakaRuntimeDisplayMixin()._save_tool_prompt_panels(
        tool_name="reply", tool_call_id="reply-1", detail=result.monitor_detail,
    )
    expected = [
        ("replyer_creative", "月が笑っています。", "构思月亮的比喻"),
        ("replyer_translation", "月亮正在微笑。", "保留拟人语气"),
    ]
    expected_stages = {stage for stage, _, _ in expected}
    for stage, response, reasoning in expected:
        history = reasoning_process.list_reasoning_prompt_files(
            stage=stage, session="test-session", action="", search="", target_stem="", page=1, page_size=50,
        )
        assert set(history.stages) == expected_stages
        assert len(history.items) == 1
        record = history.items[0]
        assert record.output_preview == response
        payload = json.loads((tmp_path / record.json_path).read_text(encoding="utf-8"))
        assert payload["request"]["kind"] == stage
        assert payload["request"]["selection_reason"] == "调用ID: reply-1"
        reasoning_parts = [
            item["text_parts"] for item in payload["output_items"] if item["item_type"] == "ReasoningItem"
        ]
        assert reasoning_parts == [[reasoning]]
        related = reasoning_process.list_related_reasoning_records("test-session", stage, record.stem).records
        assert {item.stage for item in related} == expected_stages


@pytest.mark.asyncio
async def test_gemini_analysis_is_removed_before_translation(replyer, monkeypatch) -> None:
    monkeypatch.setattr(global_config.debug, "gemini_reply_compatibility", True)
    replyer.responses[0] = _response("<think>不应翻译</think>月が笑っています。")
    success, result = await replyer.generator.generate_reply_with_context(chat_history=[])

    assert success and result.text_fragments == ["月亮正在微笑。"]
    assert get_item_text(replyer.calls[1]["items"][-1]).endswith("待翻译内容：月が笑っています。")
    assert len(replyer.calls[1]["items"]) == 1
    assert isinstance(replyer.calls[1]["items"][0], UserMessageItem)
    assert "不应翻译" not in result.completion.request_prompt


@pytest.mark.asyncio
async def test_disabled_keeps_single_request_and_current_locale(replyer, monkeypatch) -> None:
    monkeypatch.setattr(global_config.experimental, "super_creative_mode", False)
    replyer.responses[:] = [_response("直接回复")]
    with use_locale("en-US"):
        original_items = replyer.generator._build_request_messages(
            chat_history=[], reply_message=None, reply_reason="",
        )
        success, result = await replyer.generator.generate_reply_with_context(chat_history=[])

    assert success and result.text_fragments == ["直接回复"]
    assert len(replyer.calls) == 1
    prompt = "\n".join(get_item_text(item) for item in replyer.calls[0]["items"])
    assert "今回の返信は日本語で" not in prompt
    original_system = next(item for item in original_items if isinstance(item, SystemMessageItem))
    actual_system = next(item for item in replyer.calls[0]["items"] if isinstance(item, SystemMessageItem))
    assert actual_system.parts == original_system.parts
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


@pytest.mark.parametrize("profile", ["standard", "retro", "document"])
def test_each_prompt_profile_loads_japanese_templates(replyer, monkeypatch, profile: str) -> None:
    personality = "喜欢观察月亮的中文人设"
    monkeypatch.setattr(global_config.personality, "personality", personality)
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

    expected = {"standard": "maisaka_replyer", "retro": "reply_group", "document": "maisaka_replyer_document"}
    assert (expected[profile], "ja-JP") in loaded
    system = next(item for item in items if isinstance(item, SystemMessageItem))
    assert personality in get_item_text(system)
    assert "今回の返信は日本語で" in get_item_text(system)
    # 除 system 的一句语言要求外，结构及正文应与对应日文原模板一致。
    with use_locale("ja-JP"):
        original = generator._build_request_messages(
            chat_history=[], reply_message=None, reply_reason="测试材料", reply_requirements="保留段落", prompt_profile=profile,
        )
    assert len(items) == len(original)
    assert [get_item_text(item) for item in items[1:]] == [get_item_text(item) for item in original[1:]]
    assert get_item_text(system).startswith(get_item_text(original[0]))


@pytest.mark.asyncio
async def test_parallel_prompt_builders_keep_separate_locales(replyer, monkeypatch) -> None:
    kwargs = dict(chat_history=[], reply_message=None, reply_reason="中文参考")
    with use_locale("en-US"):
        japanese, normal = await asyncio.gather(
            asyncio.to_thread(replyer.generator._build_generation_messages, super_creative_mode=True, **kwargs),
            asyncio.to_thread(replyer.generator._build_generation_messages, super_creative_mode=False, **kwargs),
        )
        assert get_locale() == "en-US"
    assert "今回の返信は日本語で" in get_item_text(japanese[0])
    assert "中文参考" in get_item_text(japanese[-1])
    assert "今回の返信は日本語で" not in get_item_text(normal[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("language,word", [("ru-RU", "по-русски"), ("fr-FR", "en français")])
async def test_language_uses_same_replyer_for_translation(replyer, monkeypatch, language, word):
    monkeypatch.setattr(global_config.experimental, "super_creative_language", language)
    success, result = await replyer.generator.generate_reply_with_context(chat_history=[])
    assert success and result.text_fragments == ["月亮正在微笑。"]
    assert len(replyer.calls) == 2
    assert word in get_item_text(replyer.calls[0]["items"][0])
    assert "如果已经是中文，不用翻译。" in get_item_text(replyer.calls[1]["items"][0])
    assert replyer.calls[1]["options"].model_name == "actual-replyer"
    assert result.metrics.extra["replyer_attempt_count"] == 2
    assert result.metrics.extra["replyer_aggregate_total_tokens"] == 30


@pytest.mark.parametrize("locale", ["ru-RU", "fr-FR"])
def test_translated_templates_keep_chinese_structure(locale, tmp_path):
    names = ("reply_group", "reply_group_mai_replier", "reply_private", "reply_private_followup",
             "maisaka_replyer", "maisaka_replyer_document", "reply_attachments")
    with use_locale(locale):
        for name in names:
            original = (PROMPTS_ROOT / "zh-CN" / f"{name}.prompt").read_text(encoding="utf-8")
            translated = load_reply_prompt(load_prompt, name)
            assert extract_prompt_placeholders(original) == extract_prompt_placeholders(translated)
            assert len(original.splitlines()) == len(translated.splitlines())
            assert translated != original
        # 与模板固定句相同的用户材料也必须原样保留，不能在填充后整体替换。
        material = "请注意把握聊天内容。"
        rendered = load_reply_prompt(load_prompt, "reply_attachments", attachments=material)
        assert rendered.startswith(material + "\n")
        custom_path = tmp_path / "zh-CN" / "reply_attachments.prompt"
        custom_path.parent.mkdir()
        original = (PROMPTS_ROOT / "zh-CN" / "reply_attachments.prompt").read_text(encoding="utf-8")
        custom_path.write_text("自定义首行\n" + original, encoding="utf-8")
        custom = load_reply_prompt(
            load_prompt, "reply_attachments", custom_prompts_root=tmp_path, attachments=material,
        )
        assert custom == "自定义首行\n" + rendered
        outdated_template = "旧自定义模板 {removed_name}"
        custom_path.write_text(outdated_template, encoding="utf-8")
        clear_prompt_cache()
        fallback = load_reply_prompt(
            load_prompt, "reply_attachments", custom_prompts_root=tmp_path, attachments=material,
        )
        assert fallback == rendered
        assert custom_path.read_text(encoding="utf-8") == outdated_template


@pytest.mark.parametrize("self_reply, shared_name, dedicated_name", [
    (False, "reply_private", "reply_private_mai_replier"),
    (True, "reply_private_followup", "reply_private_followup_mai_replier"),
])
def test_mai_replier_uses_dedicated_private_template(self_reply: bool, shared_name: str, dedicated_name: str) -> None:
    profile = PrivatePromptProfile()
    assert profile.select_chat_reply_template(self_reply=self_reply, mai_replier=False) == shared_name
    assert profile.select_chat_reply_template(self_reply=self_reply, mai_replier=True) == dedicated_name
    for locale in ("zh-CN", "en-US", "ja-JP"):
        shared = load_prompt_template(shared_name, locale=locale, include_custom=False)
        dedicated = load_prompt_template(dedicated_name, locale=locale, include_custom=False)
        assert dedicated.locale == locale
        assert extract_prompt_placeholders(dedicated.template) == extract_prompt_placeholders(shared.template)


@pytest.mark.parametrize("is_group, template", [(True, "reply_group"), (False, "reply_private")])
@pytest.mark.parametrize("style, think_level, instruction", [
    ("简短表达", 1, "用一到两句口语短句回复"),
    ("正常回复", 1, "篇幅与当前话题相称"),
    ("长回复", 1, "本次篇幅以说清问题为准"),
    ("", 0, "用一到两句口语短句回复"),
])
def test_creative_chat_uses_one_template_for_all_lengths(
    replyer, monkeypatch: pytest.MonkeyPatch, is_group: bool, template: str,
    style: str, think_level: int, instruction: str,
) -> None:
    generator = replyer.generator
    generator.chat_stream.is_group_session = is_group
    load = Mock(wraps=generator._load_prompt)
    monkeypatch.setattr(generator, "_load_prompt", load)

    with use_locale("zh-CN"):
        items = generator._build_request_messages(
            chat_history=[], reply_message=None, reply_reason="", prompt_profile="retro",
            reply_tool_args={"reply_style": style}, think_level=think_level,
        )

    assert [call.args[0] for call in load.call_args_list if call.args[0].startswith(("reply_group", "reply_private"))] == [
        template,
    ]
    assert get_item_text(items[-1]).count(instruction) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("creative", [False, True])
@pytest.mark.parametrize("super_creative", [False, True])
async def test_mai_replier_ignores_both_creative_modes(replyer, monkeypatch, creative, super_creative):
    monkeypatch.setattr(global_config.chat, "creative_mode", creative)
    monkeypatch.setattr(global_config.experimental, "super_creative_mode", super_creative)
    replyer.state.model_info = ModelInfo(
        model_type="llm", name="mai-replier", model_identifier="mai_replier", api_provider="test-provider",
    )
    load = Mock(wraps=replyer.generator._load_prompt)
    monkeypatch.setattr(replyer.generator, "_load_prompt", load)
    replyer.responses[:] = [_response("直接输出中文")]
    with use_locale("zh-CN"):
        success, result = await replyer.generator.generate_reply_with_context(
            chat_history=[], reply_tool_args={"reply_style": "简短表达"},
        )
    assert success and result.text_fragments == ["直接输出中文"]
    assert [call.args[0] for call in load.call_args_list if call.args[0].startswith("reply_group")] == [
        "reply_group_mai_replier",
    ]
    assert len(replyer.calls) == 1
    assert "今回の返信" not in get_item_text(replyer.calls[0]["items"][0])
    assert "你正在群里聊天" in get_item_text(replyer.calls[0]["items"][1])
    assert result.selected_expression_ids == []
    assert result.metrics.extra["replyer_translation_count"] == 0
    assert result.metrics.extra["replyer_super_creative_mode"] is False


@pytest.mark.asyncio
async def test_classical_chinese_generates_then_translates_to_modern_chinese(replyer, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "super_creative_language", "zh-classical")
    replyer.responses[:] = [_response("月色甚美，何不共赏？"), _response("月色真美，一起欣赏吧？")]
    with use_locale("en-US"):
        success, result = await replyer.generator.generate_reply_with_context(chat_history=[])
        assert get_locale() == "en-US"
    assert success and result.text_fragments == ["月色真美，一起欣赏吧？"]
    assert len(replyer.calls) == 2
    assert "中文文言文" in get_item_text(replyer.calls[0]["items"][0])
    assert "你正在群里聊天" in get_item_text(replyer.calls[0]["items"][1])
    translation = replyer.calls[1]["items"]
    assert len(translation) == 1 and isinstance(translation[0], UserMessageItem)
    assert "翻译成现代白话中文" in get_item_text(translation[0])
    assert "如果已经是中文，不用翻译。" not in get_item_text(translation[0])
    assert get_item_text(translation[0]).endswith("待翻译内容：月色甚美，何不共赏？")
    assert replyer.calls[1]["options"].model_name == "actual-replyer"
