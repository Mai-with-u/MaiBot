import logging

import pytest

from src.llm_models.exceptions import RespParseException
from src.llm_models.model_client.plugin_client import PluginLLMClient
from src.llm_models.payload_content.context_item import (
    CONTEXT_ITEM_SCHEMA_VERSION,
    AssistantMessageItem,
)


def test_plugin_item_response_uses_host_metadata_and_request_turn() -> None:
    response = PluginLLMClient._build_api_response(
        {
            "item_schema_version": CONTEXT_ITEM_SCHEMA_VERSION,
            "output_items": [
                {
                    "item_type": "AssistantMessageItem",
                    "meta": {
                        "item_id": "plugin-controlled-id",
                        "logical_turn_id": "plugin-controlled-turn",
                        "timestamp": "1970-01-01T00:00:00",
                    },
                    "parts": [{"type": "text", "text": "新版 Item 回复"}],
                    "response_group_id": "deprecated",
                    "ordinal": 99,
                    "unknown_extension": True,
                }
            ],
        },
        "test-model",
        "test-provider",
        logical_turn_id="request-turn",
        require_output_items=True,
    )

    assert len(response.output_items) == 1
    item = response.output_items[0]
    assert isinstance(item, AssistantMessageItem)
    assert item.meta.item_id != "plugin-controlled-id"
    assert item.meta.logical_turn_id == "request-turn"
    assert not hasattr(item.meta, "response_group_id")
    assert not hasattr(item.meta, "ordinal")


@pytest.mark.parametrize(
    "result",
    [
        {
            "item_schema_version": CONTEXT_ITEM_SCHEMA_VERSION + 1,
            "output_items": [],
        },
        {
            "item_schema_version": CONTEXT_ITEM_SCHEMA_VERSION,
            "output_items": [
                {
                    "item_type": "UserMessageItem",
                    "parts": [{"type": "text", "text": "非法非模型输出"}],
                }
            ],
        },
        {
            "item_schema_version": CONTEXT_ITEM_SCHEMA_VERSION,
            "output_items": "not-a-list",
        },
    ],
)
def test_plugin_item_response_rejects_invalid_contract(result: dict[str, object]) -> None:
    with pytest.raises(RespParseException):
        PluginLLMClient._build_api_response(
            result,
            "test-model",
            "test-provider",
            logical_turn_id="request-turn",
            require_output_items=True,
        )


def test_plugin_legacy_scalar_response_warns_for_one_version(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        response = PluginLLMClient._build_api_response(
            {"content": "旧版正文"},
            "test-model",
            "test-provider",
            logical_turn_id="request-turn",
            require_output_items=True,
        )

    assert response.content == "旧版正文"
    assert "旧版 content/reasoning/tool_calls 标量协议" in caplog.text
