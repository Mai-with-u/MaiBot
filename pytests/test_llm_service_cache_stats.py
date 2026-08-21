import json

from src.services.llm_service import LLMServiceClient


class _WireModel:
    def model_dump(self, *, exclude_none: bool) -> dict[str, object]:
        assert exclude_none is True
        return {
            "text": "保留普通文本",
            "inline_data": {"data": b"image-bytes", "mime_type": "image/png"},
        }


def test_wire_payload_cache_stats_omits_binary_content() -> None:
    image_base64 = "A" * 8192
    encrypted_content = "B" * 4096
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "保留消息正文"},
                    {"type": "image_url", "image_url": f"data:image/png;base64,{image_base64}"},
                ],
            }
        ],
        "encrypted_content": encrypted_content,
        "gemini_content": _WireModel(),
    }

    sanitized = LLMServiceClient._sanitize_wire_value_for_cache_stats(payload)
    serialized = json.dumps(sanitized, ensure_ascii=False)

    assert "保留消息正文" in serialized
    assert "保留普通文本" in serialized
    assert image_base64 not in serialized
    assert encrypted_content not in serialized
    image_summary = sanitized["messages"][0]["content"][1]["image_url"]
    assert image_summary["type"] == "omitted_binary"
    assert image_summary["media_type"] == "image/png"
    assert len(image_summary["sha256"]) == 64
    encrypted_summary = sanitized["encrypted_content"]
    assert encrypted_summary["type"] == "omitted_binary"
    assert len(encrypted_summary["sha256"]) == 64
    gemini_summary = sanitized["gemini_content"]["inline_data"]["data"]
    assert gemini_summary["type"] == "omitted_binary"
