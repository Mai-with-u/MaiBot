from types import SimpleNamespace
from unittest.mock import Mock

from src.chat.replyer import maisaka_generator_base as generator_module
from src.common.data_models.reply_generation_data_models import ReplyGenerationResult
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType


def test_normal_replyer_persistence_returns_generation_link(monkeypatch):
    generator = object.__new__(generator_module.BaseMaisakaReplyGenerator)
    generator.request_type = "maisaka.replyer"
    monkeypatch.setattr(generator, "_resolve_session_id", lambda stream_id: stream_id)
    monkeypatch.setattr(generator_module.global_config.debug, "save_maisaka_reasoning", True)
    uri = "/api/webui/config/maisaka-prompt-preview?path=replyer%2Fsession%2F123.json"
    preview = SimpleNamespace(
        preview_web_uri=uri,
        payload={"request_items": [{"saved": "request"}], "output_items": [{"saved": "output"}]},
    )
    save = Mock(return_value=preview)
    monkeypatch.setattr(generator_module.PromptCLIVisualizer, "build_prompt_preview_access", save)
    result = ReplyGenerationResult(success=True)
    result.request_messages = [ContextItemBuilder().set_role(RoleType.User).add_text_content("你好").build()]
    generator._persist_reply_preview(result, stream_id="session", reply_reason="")
    assert result.prompt_html_uri == uri
    assert result.request_messages == preview.payload["request_items"]
    assert result.output_items == preview.payload["output_items"]
    save.assert_called_once()
    assert save.call_args.kwargs["category"] == "replyer"
    assert save.call_args.kwargs["request_kind"] == "maisaka.replyer"

