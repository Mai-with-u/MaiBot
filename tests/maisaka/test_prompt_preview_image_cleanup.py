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
