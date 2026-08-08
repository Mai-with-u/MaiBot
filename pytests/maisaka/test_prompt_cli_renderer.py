from src.maisaka.display.prompt_cli_renderer import (
    PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES,
    PromptCLIVisualizer,
)


def test_structured_prompt_keeps_full_provider_response_and_omits_only_large_base64() -> None:
    small_base64 = "YWJjZA=="
    large_base64 = "A" * ((PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES * 4 // 3) + 8)
    provider_response = {
        "id": "resp_test",
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_test",
                "summary": [{"type": "summary_text", "text": "先检索再回复"}],
                "encrypted_content": small_base64,
            },
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "最终回答"}],
                "large_blob": large_base64,
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }

    payload = PromptCLIVisualizer._build_structured_preview_payload(
        [{"role": "user", "content": "测试"}],
        request_kind="planner",
        selection_reason="测试完整响应",
        tool_definitions=None,
        output_content="最终回答",
        output_title="输出结果",
        output_tool_calls=None,
        metadata={"model_name": "test-model"},
        provider_response=provider_response,
        keep_base64=False,
    )

    assert payload["schema_version"] == 4
    stored_response = payload["provider_response"]
    assert stored_response["id"] == "resp_test"
    assert stored_response["output"][0]["summary"][0]["text"] == "先检索再回复"
    assert stored_response["output"][0]["encrypted_content"] == small_base64
    omitted_blob = stored_response["output"][1]["large_blob"]
    assert omitted_blob["type"] == "omitted_binary"
    assert omitted_blob["base64_omitted"] is True
    assert omitted_blob["size_bytes"] > PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES
    assert len(omitted_blob["sha256"]) == 64
    assert stored_response["usage"]["total_tokens"] == 30
