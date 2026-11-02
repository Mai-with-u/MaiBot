from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.replyer.document_generator import DocumentReplyGenerator
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType, get_item_text
from src.plugin_runtime.hook_payloads import serialize_prompt_items
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult


@pytest.fixture
def generator(monkeypatch):
    service = BaseMaisakaReplyGenerator(llm_client_cls=lambda **kwargs: None, load_prompt_func=load_prompt)
    monkeypatch.setattr(service, "_build_personality_prompt", lambda: "PERSONA")
    monkeypatch.setattr(service, "_resolve_session_id", lambda stream_id: "")
    monkeypatch.setattr(service, "_is_retro_group_chat", lambda stream_id: True)
    monkeypatch.setattr(service, "_build_group_chat_attention_block", lambda session_id: "ATTENTION")
    monkeypatch.setattr(service, "_build_keyword_reaction_prompt", lambda **kwargs: "KEYWORD")
    monkeypatch.setattr(service, "_build_reply_attachment_prompt", lambda **kwargs: "ATTACHMENT")
    monkeypatch.setattr(service, "_select_temporary_reply_style", lambda: "")
    monkeypatch.setattr(service, "_select_reply_style", lambda: "STYLE")
    monkeypatch.setattr(service, "_build_retro_sender_name", lambda *args: "USER")
    return service


@pytest.mark.parametrize("creative", [False, True])
@pytest.mark.parametrize("name,identifier,is_mai", [
    ("ordinary", "ordinary", False), ("alias", "Mai_Replier-v1", True),
    ("mai-replier-test", "remote", True), ("alias", "v2p1-contract", True),
    ("v2p1-raw", "remote", True),
])
def test_actual_model_controls_profile(generator, monkeypatch, creative, name, identifier, is_mai):
    monkeypatch.setattr(global_config.chat, "creative_mode", creative)
    model = SimpleNamespace(name=name, model_identifier=identifier)
    assert generator._resolve_prompt_profile(model) == ("mai_replier" if is_mai else "retro" if creative else "standard")
    document = object.__new__(DocumentReplyGenerator)
    assert document._resolve_prompt_profile(model) == "document"


@pytest.mark.parametrize("mai", [False, True])
def test_retro_fields_and_light_template_are_separate(generator, mai):
    items = generator._build_request_messages(
        chat_history=[], reply_message=None, reply_reason="REFERENCE", think_level=0,
        expression_habits="EXPRESSION", reply_requirements="PLUGIN_REQUIREMENT",
        reply_tool_args={"_duplicate_target_reply_reminder": "DUPLICATE", "reply_style": "简短表达"},
        prompt_profile="mai_replier" if mai else "retro",
    )
    text = "\n".join(get_item_text(item) for item in items)
    assert len(items) == 2
    assert "PERSONA" in text and "PLUGIN_REQUIREMENT" in text and "REFERENCE" in text
    for field in ("ATTENTION", "KEYWORD", "EXPRESSION", "ATTACHMENT", "DUPLICATE"):
        assert (field in text) is not mai
    assert generator._select_retro_prompt_name(
        reply_message=None, stream_id=None, think_level=0, reply_tool_args={}, mai_replier=mai,
    ) == ("retro_replyer" if mai else "retro_replyer_light")


@pytest.mark.asyncio
@pytest.mark.parametrize("models", [["ordinary"], ["mai_replier"], ["mai_replier", "ordinary"], ["ordinary", "mai_replier"]])
@pytest.mark.parametrize("creative", [False, True])
async def test_hook_model_switch_and_retry_keep_actual_profile(generator, monkeypatch, models, creative):
    from src.desktop_pet.service import desktop_pet_service

    monkeypatch.setattr(global_config.chat, "creative_mode", creative)
    monkeypatch.setattr(desktop_pet_service, "notify_thinking", AsyncMock())
    monkeypatch.setattr(generator, "_persist_reply_preview", Mock())
    select = AsyncMock(return_value=MaisakaReplyContext(expression_habits="EXPRESSION", selected_expression_ids=[7]))
    monkeypatch.setattr(generator, "_build_reply_context", select)
    history = AsyncMock(return_value=[])
    observed = []
    after_calls = 0

    async def hook(name, **kwargs):
        nonlocal after_calls
        if name.endswith("before_request"):
            kwargs.update(model_name="plugin-choice", extra_prompt="PLUGIN_REQUIREMENT")
        elif name.endswith("before_model_request"):
            observed.append((kwargs["reply_tool_args"]["_replyer_prompt_profile"], kwargs["selected_expression_ids"]))
            # Existing Context Item rewriting remains supported without new top-level arguments.
            kwargs["items"] = kwargs["items"] + serialize_prompt_items([
                ContextItemBuilder().set_role(RoleType.User).add_text_content("HOOK_REWRITE").build(),
            ])
        else:
            after_calls += 1
            kwargs.update(response="PLUGIN_RESPONSE", retry=after_calls == 1, retry_reason="RETRY_REQUIREMENT")
        return HookDispatchResult(hook_name=name, kwargs=kwargs)

    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))

    async def generate(*, context_factory, options):
        assert options.model_name == "plugin-choice"
        for model in models:
            items = await context_factory(
                SimpleNamespace(api_provider=SimpleNamespace(client_type="test")),
                SimpleNamespace(name=model, model_identifier=model, visual=False),
            )
            text = "\n".join(get_item_text(item) for item in items)
            assert "PLUGIN_REQUIREMENT" in text and "HOOK_REWRITE" in text
            if after_calls:
                assert "RETRY_REQUIREMENT" in text
        return LLMResponseResult(output_items=(
            ContextItemBuilder().set_role(RoleType.Assistant).add_text_content("RESPONSE").build(),
        ), model_name=models[-1])

    generator.express_model = SimpleNamespace(task_name="replyer", generate_response_with_context=generate)
    success, result = await generator.generate_reply_with_context(chat_history=[], retro_history_builder=history)
    assert success and result.completion.response_text == "PLUGIN_RESPONSE"
    expected = [("mai_replier", []) if model == "mai_replier" else
                ("retro" if creative else "standard", [7]) for model in models]
    assert observed == expected * 2
    assert select.await_count == int("ordinary" in models)
    assert history.await_count == int(creative or "mai_replier" in models)
