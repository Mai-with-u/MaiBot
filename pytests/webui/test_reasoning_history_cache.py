from pathlib import Path
import json
import os

import pytest

from src.webui.routers import reasoning_process as routes


def write_record(root: Path, session: str, stem: int, text: str) -> Path:
    path = root / "replyer" / session / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "metadata": {"model_name": "test-model", "duration_ms": 10},
        "output_items": [{"item_type": "AssistantMessageItem", "parts": [{"type": "text", "text": text}]}],
    }), encoding="utf-8")
    os.utime(path, ns=(stem * 1_000_000, stem * 1_000_000))
    return path


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "PROMPT_LOG_ROOT", tmp_path)
    monkeypatch.setattr(routes, "_list_session_infos", lambda stage, names: [
        routes.ReasoningPromptSessionInfo(name=name, display_name=name, chat_type="group") for name in names
    ])
    for session in ("chat-a", "chat-b"):
        for index in range(23):
            stem = 100_000 + index * 20_000
            write_record(tmp_path, session, stem, f"answer-{index}")
        # 同输出、十秒内重复；另一聊天流的相同输出必须保留。
        write_record(tmp_path, session, 100_001, "answer-0")
    routes._cached_replyer_prompt_file_record.cache_clear()
    routes._cached_replyer_output_matches.cache_clear()
    yield tmp_path
    routes._cached_replyer_prompt_file_record.cache_clear()
    routes._cached_replyer_output_matches.cache_clear()


def query(**overrides):
    params = dict(stage="replyer", session="chat-a", action="", search="", target_stem="", page=1, page_size=10)
    params.update(overrides)
    return routes.list_reasoning_prompt_files(**params).model_dump()


def test_cache_preserves_deduplication_search_and_pagination(history, monkeypatch):
    queries = [{}, {"page": 2}, {"page": 3}, {"target_stem": "100001"}, {"target_stem": "100000"},
               {"search": "answer-1"}, {"action": "missing"}, {"session": routes.ALL_GROUP_SESSIONS}]
    with monkeypatch.context() as uncached:
        uncached.setattr(routes, "_hydrate_replyer_prompt_file_record", routes._hydrate_prompt_file_record)
        expected = [query(**params) for params in queries]
    assert expected[0]["total"] == 23
    assert expected[-1]["total"] == 46

    reads = []
    load = routes._load_prompt_json

    def counted_load(path):
        reads.append(path)
        return load(path)

    monkeypatch.setattr(routes, "_load_prompt_json", counted_load)
    assert [query(**params) for params in queries] == expected
    assert len(set(reads)) == 48
    reads.clear()
    assert [query(**params) for params in queries] == expected
    assert reads == []


def test_cache_refreshes_only_changed_records_and_removes_deleted_records(history, monkeypatch):
    query()
    reads = []
    load = routes._load_prompt_json

    def counted_load(path):
        reads.append(path.name)
        return load(path)

    monkeypatch.setattr(routes, "_load_prompt_json", counted_load)
    # 原地等长改写，不依赖目录时间或总大小，重复数量应立即变化。
    changed = history / "replyer/chat-a/100001.json"
    stat = changed.stat()
    changed.write_bytes(changed.read_bytes().replace(b"answer-0", b"changed!"))
    os.utime(changed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    write_record(history, "chat-a", 900_000, "new answer")
    result = query()
    assert result["total"] == 25
    assert sorted(reads) == ["100001.json", "900000.json"]
    changed.unlink()
    reads.clear()
    assert query()["total"] == 24
    assert reads == []


def test_event_append_invalidates_summary_and_cached_models_are_isolated(history):
    result = query()
    assert result["total"] == 23
    event_path = history / "replyer/chat-a/100001.events.jsonl"
    event_path.write_text(json.dumps({
        "type": "succeeded", "recorded_at": "2026-11-01T00:00:00", "metadata": {}, "generation_attempts": [],
        "output_items": [{"item_type": "AssistantMessageItem", "parts": [{"type": "text", "text": "event reply"}]}],
    }) + "\n", encoding="utf-8")
    assert query()["total"] == 24
    records = routes._collect_prompt_file_records_for_session("replyer", "chat-a", {})
    items = routes._hydrate_prompt_file_records(records, include_output_preview=True)
    items[0].output_preview = "mutated"
    again = routes._hydrate_prompt_file_records(records, include_output_preview=True)
    assert again[0].output_preview != "mutated"
    event_path.unlink()
    assert query()["total"] == 23


def test_search_keeps_matching_full_output_beyond_preview_and_refreshes(history, monkeypatch):
    path = write_record(history, "chat-a", 900_000, "long reply " * 30 + "unique-tail")
    assert "unique-tail" not in query()["items"][0]["output_preview"]
    assert query(search="unique-tail")["total"] == 1

    reads = []
    load = routes._load_prompt_json

    def counted_load(file_path):
        reads.append(file_path)
        return load(file_path)

    monkeypatch.setattr(routes, "_load_prompt_json", counted_load)
    assert query(search="unique-tail")["total"] == 1
    assert reads == []
    stat = path.stat()
    path.write_bytes(path.read_bytes().replace(b"unique-tail", b"changed-end"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert query(search="unique-tail")["total"] == 0
    assert set(reads) == {path}
