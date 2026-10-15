from base64 import b64encode
from pathlib import Path

import json
import queue

import pytest

from src.llm_models.payload_content.context_item import ContextImagePart, ContextItemMeta, UserMessageItem
from src.maisaka.display.prompt_preview_logger import PromptPreviewLogger, _PreviewWriteTask
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.display import prompt_preview_logger


@pytest.fixture
def preview_store(monkeypatch, tmp_path):
    monkeypatch.setattr(PromptPreviewLogger, "_BASE_DIR", tmp_path / "logs" / "maisaka_prompt")
    monkeypatch.setattr(PromptPreviewLogger, "_IMAGE_DIR", tmp_path / "data" / "prompt_imgs")
    monkeypatch.setattr(PromptPreviewLogger, "_image_index_ready", False)
    monkeypatch.setattr(PromptPreviewLogger, "_images_by_preview", {})
    monkeypatch.setattr(PromptPreviewLogger, "_previews_by_image", {})
    monkeypatch.setattr(PromptPreviewLogger, "_get_max_preview_groups_per_chat", lambda: 1)
    return PromptPreviewLogger


def write_preview(store, stage: str, stem: str, image: Path):
    directory = store._BASE_DIR / stage / "chat"
    task = _PreviewWriteTask(
        chat_dir=directory,
        file_path=directory / f"{stem}.json",
        content=json.dumps({"image_path": image.as_posix()}),
        image_assets={image: b"image"},
    )
    store._write_task(task)
    return task.file_path


def test_overflow_deletes_exclusive_image_but_keeps_shared_image(preview_store):
    store = preview_store
    shared = store._IMAGE_DIR / f"{'a' * 64}.png"
    replacement = store._IMAGE_DIR / f"{'b' * 64}.png"
    first = write_preview(store, "planner", "1", shared)
    write_preview(store, "reply", "1", shared)
    write_preview(store, "planner", "2", replacement)
    assert not first.exists()
    assert shared.exists()
    store.clear_stage(store._BASE_DIR / "reply")
    assert not shared.exists()
    assert replacement.exists()
    write_preview(store, "planner", "3", shared)
    assert not replacement.exists()
    assert shared.exists()


def test_clear_stage_indexes_existing_json_and_legacy_preview(preview_store):
    store = preview_store
    image = store._IMAGE_DIR / f"{'a' * 64}.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    for stage, suffix, content in (
        ("planner", ".json", json.dumps({"image_path": str(image)})),
        ("reply", ".html", f'<img src="{image.as_uri()}">'),
    ):
        directory = store._BASE_DIR / stage / "chat"
        directory.mkdir(parents=True)
        (directory / f"1{suffix}").write_text(content, encoding="utf-8")
    assert store.clear_stage(store._BASE_DIR / "planner") == 1
    assert image.exists()
    assert store.clear_stage(store._BASE_DIR / "reply") == 1
    assert not image.exists()


def test_queued_preview_restores_image_deleted_by_clear(preview_store):
    store = preview_store
    image = store._IMAGE_DIR / f"{'a' * 64}.png"
    write_preview(store, "planner", "1", image)
    store.clear_stage(store._BASE_DIR / "planner")
    assert not image.exists()
    record = write_preview(store, "planner", "2", image)
    assert image.exists()
    assert record.exists()


@pytest.mark.parametrize("text_preview", [False, True])
def test_renderer_queues_images_with_record(preview_store, monkeypatch, text_preview):
    store = preview_store
    tasks = []
    monkeypatch.setattr(store, "_submit", tasks.append)
    monkeypatch.setattr(PromptCLIVisualizer, "_build_official_image_path", lambda *_: None)
    monkeypatch.setattr(
        PromptCLIVisualizer,
        "_build_image_cache_path",
        lambda *_: store._IMAGE_DIR / f"{'a' * 64}.png",
    )
    options = {
        "category": "planner",
        "chat_id": "test-chat",
        "request_kind": "planner",
        "output_items": [
            UserMessageItem(
                meta=ContextItemMeta.create(),
                parts=(ContextImagePart("png", b64encode(b"image").decode()),),
            )
        ],
    }
    if text_preview:
        access = PromptCLIVisualizer.build_text_preview_access("test", subtitle="test", **options)
    else:
        access = PromptCLIVisualizer.build_prompt_preview_access([], selection_reason="test", **options)
    assert len(tasks) == 1
    assert tasks[0].image_assets
    assert not access.record_path.exists()
    for path in tasks[0].image_assets:
        assert not path.exists()
    store._write_task(tasks[0])
    assert access.record_path.exists()
    for path in tasks[0].image_assets:
        assert path.read_bytes() == b"image"
    store.clear_stage(store._BASE_DIR / "planner")
    for path in tasks[0].image_assets:
        assert not path.exists()


def test_clear_stage_does_not_delete_official_images(preview_store, tmp_path):
    store = preview_store
    image = tmp_path / "data" / "images" / f"{'a' * 64}.png"
    write_preview(store, "planner", "1", image)
    store.clear_stage(store._BASE_DIR / "planner")
    assert image.exists()


def test_clear_stage_rejects_paths_outside_preview_root(preview_store, tmp_path):
    with pytest.raises(ValueError):
        preview_store.clear_stage(tmp_path)


def test_orphan_check_deletes_only_unreferenced_cache_images(preview_store):
    store = preview_store
    referenced = store._IMAGE_DIR / f"{'a' * 64}.png"
    orphan = store._IMAGE_DIR / f"{'b' * 64}.png"
    unrelated = store._IMAGE_DIR / "README.txt"
    write_preview(store, "planner", "1", referenced)
    orphan.write_bytes(b"orphan")
    unrelated.write_text("keep", encoding="utf-8")
    assert store.cleanup_orphan_images() == 1
    assert referenced.exists()
    assert not orphan.exists()
    assert unrelated.exists()


def test_orphan_check_refreshes_index_after_external_log_removal(preview_store):
    store = preview_store
    image = store._IMAGE_DIR / f"{'a' * 64}.png"
    record = write_preview(store, "planner", "1", image)
    assert store.cleanup_orphan_images() == 0
    record.unlink()
    assert store.cleanup_orphan_images() == 1
    assert not image.exists()


def test_orphan_check_preserves_images_when_record_scan_fails(preview_store):
    store = preview_store
    image = store._IMAGE_DIR / f"{'a' * 64}.png"
    record = write_preview(store, "planner", "1", image)
    record.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        store.cleanup_orphan_images()
    assert image.exists()
    assert not store._image_index_ready


@pytest.mark.parametrize("busy", [False, True])
def test_worker_checks_orphans_at_start_and_after_interval(preview_store, monkeypatch, busy):
    store = preview_store
    now = [0.0]
    checks = []
    writes = []

    class StopWorker(BaseException):
        pass

    class FakeQueue:
        calls = 0

        def get(self, timeout):
            self.calls += 1
            if self.calls > 1:
                raise StopWorker
            now[0] += timeout
            if busy:
                return _PreviewWriteTask(Path("chat"), Path("chat/1.json"), "{}", {})
            raise queue.Empty

        def task_done(self):
            pass

    monkeypatch.setattr(prompt_preview_logger.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(store, "cleanup_orphan_images", lambda: checks.append(now[0]))
    monkeypatch.setattr(store, "_write_task", writes.append)
    monkeypatch.setattr(store, "_write_queue", FakeQueue())
    with pytest.raises(StopWorker):
        store._writer_loop()
    assert checks == [0.0, store._ORPHAN_IMAGE_CHECK_INTERVAL_SECONDS]
    assert len(writes) == int(busy)


@pytest.fixture
def cached_image(preview_store, monkeypatch):
    path = preview_store._IMAGE_DIR / f"{'c' * 64}.png"
    monkeypatch.setattr(PromptCLIVisualizer, "_build_official_image_path", lambda *_: None)
    monkeypatch.setattr(PromptCLIVisualizer, "_build_image_cache_path", lambda *_: path)
    return path


def image_item():
    return UserMessageItem(
        meta=ContextItemMeta.create(),
        parts=(ContextImagePart("png", b64encode(b"image").decode()),),
    )


def test_failed_snapshot_saves_images_and_preserves_them_during_cleanup(preview_store, cached_image, monkeypatch):
    from src.config.model_configs import APIProvider, ModelInfo
    from src.llm_models import request_snapshot
    from src.llm_models.model_client.base_client import RequestTraceContext

    monkeypatch.setattr(request_snapshot, "LLM_REQUEST_LOG_DIR", preview_store._BASE_DIR / "llm_error")
    monkeypatch.setattr(request_snapshot, "_get_llm_request_snapshot_limit", lambda: 128)
    # 已建立索引时也必须马上登记失败记录，防止巡检或清空其他记录误删图片。
    preview_store._ensure_image_index()
    trace = RequestTraceContext()
    options = dict(
        api_provider=APIProvider(name="test", base_url="https://example.com", api_key="test-key"),
        client_type="openai",
        internal_request={
            "request_kind": "response",
            "context_items": request_snapshot.serialize_context_items_snapshot([image_item()]),
        },
        model_info=ModelInfo(name="test", model_identifier="test", api_provider="test"),
        operation="task.hard_timeout",
        provider_request={},
        trace_context=trace,
    )
    error = TimeoutError("timeout")
    path = request_snapshot.save_failed_request_snapshot(error=error, **options)
    assert path is not None and path.is_file()
    assert cached_image.read_bytes() == b"image"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "base64_omitted" in json.dumps(payload["request_items"])
    assert b64encode(b"image").decode() not in json.dumps(payload["request_items"])
    assert preview_store.cleanup_orphan_images() == 0

    request_snapshot.attach_request_snapshot(error, path)
    request_snapshot.update_failed_request_attempt(error, status="switching_model")
    request_snapshot.mark_request_final_failure(error)
    assert preview_store.cleanup_orphan_images() == 0
    assert cached_image.exists()
    preview_store.clear_stage(preview_store._BASE_DIR / "llm_error")
    assert not cached_image.exists()


@pytest.mark.parametrize("side", ["request", "output", "both"])
def test_reply_result_persists_images_at_finalization(preview_store, cached_image, monkeypatch, side):
    from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
    from src.common.data_models.reply_generation_data_models import ReplyGenerationResult, build_reply_monitor_detail
    from src.llm_models.request_snapshot import serialize_context_items_snapshot

    tasks = []
    monkeypatch.setattr(preview_store, "_submit", tasks.append)
    # 即使普通预览配置保留编码，回复监控仍应只携带图片引用。
    monkeypatch.setattr(PromptCLIVisualizer, "_should_keep_prompt_preview_json_base64", lambda: True)
    generator = BaseMaisakaReplyGenerator.__new__(BaseMaisakaReplyGenerator)
    generator.request_type = "maisaka.replyer"
    monkeypatch.setattr(generator, "_resolve_session_id", lambda _: "test-chat")
    items = serialize_context_items_snapshot([image_item()])
    result = ReplyGenerationResult()
    result.request_messages = items if side in {"request", "both"} else []
    result.output_items = items if side in {"output", "both"} else []
    generator._persist_reply_preview(result, stream_id="test-chat", reply_reason="test")
    assert len(tasks) == 1
    assert tasks[0].image_assets[cached_image] == b"image"
    assert not cached_image.exists()
    detail = build_reply_monitor_detail(result)
    assert b64encode(b"image").decode() not in json.dumps(detail)
    preview_store._write_task(tasks[0])
    assert cached_image.read_bytes() == b"image"
    assert preview_store.cleanup_orphan_images() == 0
    preview_store.clear_stage(tasks[0].chat_dir.parent)
    assert not cached_image.exists()


def test_image_collection_nested_scopes_do_not_mix_assets(preview_store, cached_image):
    other = cached_image.with_name(f"{'d' * 64}.png")
    with preview_store.collect_image_assets() as outer:
        preview_store.add_image_asset(cached_image, b"outer")
        with preview_store.collect_image_assets() as inner:
            preview_store.add_image_asset(other, b"inner")
        preview_store.add_image_asset(cached_image, b"updated")
    assert outer == {cached_image: b"updated"}
    assert inner == {other: b"inner"}
    with pytest.raises(RuntimeError, match="预览构建上下文"):
        preview_store.add_image_asset(cached_image, b"outside")


@pytest.mark.parametrize("side", ["request", "output", "both"])
def test_successful_reply_with_images_completes(preview_store, cached_image, monkeypatch, side):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import asyncio

    from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
    from src.common.data_models.llm_service_data_models import LLMResponseResult
    from src.llm_models.payload_content.context_item import AssistantMessageItem, ContextTextPart

    tasks = []
    monkeypatch.setattr(preview_store, "_submit", tasks.append)
    generator = BaseMaisakaReplyGenerator.__new__(BaseMaisakaReplyGenerator)
    generator.request_type = "maisaka.replyer"
    generator.chat_stream = None
    monkeypatch.setattr(generator, "_resolve_session_id", lambda _: "test-chat")
    monkeypatch.setattr(generator, "_build_reply_context", AsyncMock(return_value=MaisakaReplyContext()))
    requests = [image_item()] if side in {"request", "both"} else []
    monkeypatch.setattr(generator, "_build_request_messages", lambda **_: requests)
    monkeypatch.setattr(generator, "_resolve_enable_visual_message", lambda _: True)
    runtime = SimpleNamespace(invoke_hook=AsyncMock(return_value=SimpleNamespace(kwargs={})))
    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: runtime)
    parts = [ContextTextPart("hello")]
    if side in {"output", "both"}:
        parts.append(ContextImagePart("png", b64encode(b"image").decode()))
    response = LLMResponseResult(
        output_items=(AssistantMessageItem(meta=ContextItemMeta.create(), parts=tuple(parts)),),
        model_name="test",
    )

    async def generate(*, context_factory, options):
        await context_factory(SimpleNamespace(api_provider=SimpleNamespace(client_type="openai")))
        return response

    generator.express_model = SimpleNamespace(task_name="replyer", generate_response_with_context=generate)
    success, result = asyncio.run(generator.generate_reply_with_context(chat_history=[], stream_id="test-chat"))
    assert success and result.success
    assert result.completion.response_text == "hello"
    assert len(tasks) == 1
    assert tasks[0].image_assets[cached_image] == b"image"
    assert b64encode(b"image").decode() not in json.dumps(result.monitor_detail)
    preview_store._write_task(tasks[0])
    assert cached_image.read_bytes() == b"image"
