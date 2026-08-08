from dataclasses import replace
from pathlib import Path

import base64
import json

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.generation_diagnostics import sanitize_generation_diagnostic
from src.llm_models.model_client.base_client import APIResponse, RequestTraceContext, ResponseRequest
from src.llm_models.payload_content.context_item import ContextItemBuilder
from src.llm_models.request_snapshot import (
    attach_request_snapshot,
    deserialize_persisted_context_items_snapshot,
    mark_request_succeeded,
    save_failed_request_snapshot,
    serialize_response_request_snapshot,
    update_failed_request_attempt,
)
from src.maisaka.display import preview_path_utils, prompt_cli_renderer
from src.webui.routers.reasoning_process import _extract_llm_error_display_title
import src.llm_models.request_snapshot as request_snapshot


def _build_provider() -> APIProvider:
    return APIProvider(
        name="test-provider",
        base_url="https://example.com/v1",
        auth_type="none",
        client_type="openai",
        default_headers={"Authorization": "secret"},
    )


def _build_model() -> ModelInfo:
    return ModelInfo(
        name="test-model",
        model_identifier="test-model-id",
        api_provider="test-provider",
    )


def _patch_snapshot_paths(monkeypatch, tmp_path: Path) -> None:
    snapshot_root = tmp_path / "logs" / "maisaka_prompt" / "llm_error"
    prompt_image_root = tmp_path / "data" / "prompt_imgs"
    prompt_audio_root = tmp_path / "data" / "prompt_audio"
    monkeypatch.setattr(request_snapshot, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(request_snapshot, "LLM_REQUEST_LOG_DIR", snapshot_root)
    monkeypatch.setattr(request_snapshot, "LLM_REQUEST_AUDIO_DIR", prompt_audio_root)
    monkeypatch.setattr(preview_path_utils, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(prompt_cli_renderer, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(prompt_cli_renderer, "DATA_IMAGE_DIR", tmp_path / "data" / "images")
    monkeypatch.setattr(prompt_cli_renderer, "DATA_EMOJI_DIR", tmp_path / "data" / "emoji")
    monkeypatch.setattr(prompt_cli_renderer, "DATA_PROMPT_IMAGE_DIR", prompt_image_root)


def test_failed_request_snapshot_aggregates_attempts_and_externalizes_image(monkeypatch, tmp_path: Path) -> None:
    _patch_snapshot_paths(monkeypatch, tmp_path)
    image_base64 = base64.b64encode(b"fake-png-content").decode("ascii")
    model_info = _build_model()
    provider = _build_provider()
    trace_context = RequestTraceContext(
        request_id="request-1",
        task_name="planner",
        request_type="maisaka.planner",
        started_at=1_700_000_000,
    )
    request = ResponseRequest(
        model_info=model_info,
        context_items=[
            ContextItemBuilder()
            .add_text_content("分析这张图片")
            .add_image_content(image_format="png", image_base64=image_base64)
            .build()
        ],
        trace_context=trace_context,
        max_tokens=256,
    )
    internal_request = serialize_response_request_snapshot(request)

    trace_context.attempt = 1
    trace_context.model_attempt = 1
    first_path = save_failed_request_snapshot(
        api_provider=provider,
        client_type="openai",
        error=RuntimeError("第一次失败"),
        internal_request=internal_request,
        model_info=model_info,
        operation="chat.completions.create",
        provider_request={"request_kwargs": {"messages": ["重复请求体"], "authorization": "secret"}},
        trace_context=trace_context,
    )
    trace_context.attempt = 2
    trace_context.model_attempt = 2
    second_path = save_failed_request_snapshot(
        api_provider=provider,
        client_type="openai",
        error=RuntimeError("第二次失败"),
        internal_request=internal_request,
        model_info=model_info,
        operation="chat.completions.create",
        provider_request={"request_kwargs": {"messages": ["重复请求体"]}},
        trace_context=trace_context,
    )

    assert first_path == second_path
    assert first_path is not None
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 6
    assert "messages" not in payload
    assert "output" not in payload
    assert payload["request"]["task_name"] == "planner"
    assert payload["metadata"]["status"] == "retrying"
    assert len(payload["generation_attempts"]) == 2
    assert image_base64 not in first_path.read_text(encoding="utf-8")
    image_part = payload["request_items"][0]["parts"][1]
    assert image_part["image_reference"]["base64_omitted"] is True
    assert (tmp_path / image_part["image_reference"]["image_path"]).is_file()
    first_attempt = payload["generation_attempts"][0]
    assert first_attempt["wire_request"]["request_kwargs"]["messages"] == ["重复请求体"]
    assert first_attempt["wire_request"]["request_kwargs"]["authorization"] == "[REDACTED]"
    assert payload["api_provider"]["default_headers"]["Authorization"] == "[已脱敏]"
    assert payload["request_parameters"]["max_tokens"] == 256
    restored_items = deserialize_persisted_context_items_snapshot(payload["request_items"])
    assert restored_items[0].parts[1].image_base64 == image_base64

    retry_error = RuntimeError("第二次失败")
    attach_request_snapshot(retry_error, second_path)
    update_failed_request_attempt(retry_error, status="retrying", retry_interval=3)
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["generation_attempts"][-1]["retry_interval"] == 3

    trace_context.attempt = 3
    trace_context.model_attempt = 3
    trace_context.generation_attempts.append(
        replace(
            trace_context.generation_attempts[-1],
            attempt_id="request-1:3",
            provider_attempt=3,
            model_attempt=3,
            status="succeeded",
            error=None,
        )
    )
    mark_request_succeeded(request, APIResponse())
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["status"] == "succeeded_after_retry"
    assert payload["generation_attempts"][-1]["status"] == "succeeded"


def test_failed_request_without_session_is_saved_under_system(monkeypatch, tmp_path: Path) -> None:
    _patch_snapshot_paths(monkeypatch, tmp_path)
    model_info = _build_model()
    trace_context = RequestTraceContext(request_id="system-request", task_name="utils", attempt=1)
    request = ResponseRequest(
        model_info=model_info,
        context_items=[ContextItemBuilder().add_text_content("测试").build()],
        trace_context=trace_context,
    )

    snapshot_path = save_failed_request_snapshot(
        api_provider=_build_provider(),
        client_type="openai",
        error=RuntimeError("失败"),
        internal_request=serialize_response_request_snapshot(request),
        model_info=model_info,
        operation="chat.completions.create",
        provider_request={},
        trace_context=trace_context,
    )

    assert snapshot_path is not None
    assert snapshot_path.parent.name == "system"


def test_llm_error_display_title_uses_final_status_and_latest_error() -> None:
    payload = {
        "metadata": {"status": "succeeded_after_retry"},
        "attempts": [
            {"error": {"message": "第一次失败"}},
            {"status": "succeeded"},
        ],
    }

    assert _extract_llm_error_display_title(payload) == "重试后成功 · 第一次失败"


def test_generation_diagnostic_sanitizes_credentials_private_fields_urls_and_binary() -> None:
    raw_binary = base64.b64encode(b"x" * (64 * 1024)).decode("ascii")

    sanitized = sanitize_generation_diagnostic(
        {
            "openai_api_key": "secret-key",
            "endpoint": "https://user:pass@example.test/v1?api_key=secret&region=cn",
            "encrypted_content": "encrypted-replay",
            "nested": {"thought_signature": "private-signature"},
            "image_base64": raw_binary,
            "bytes": b"binary-data",
        }
    )

    assert sanitized["openai_api_key"] == "[REDACTED]"
    assert sanitized["endpoint"] == "https://example.test/v1?api_key=%5BREDACTED%5D&region=cn"
    assert sanitized["encrypted_content"] == "[仅在内存 replay fragment 中保留]"
    assert sanitized["nested"]["thought_signature"] == "[仅在内存 replay fragment 中保留]"
    assert sanitized["image_base64"]["type"] == "omitted_binary"
    assert sanitized["bytes"]["type"] == "omitted_binary"
