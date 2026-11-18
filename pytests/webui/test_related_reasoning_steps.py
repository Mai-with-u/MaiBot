from types import SimpleNamespace
import asyncio
import json

import pytest

from src.config.config import global_config
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.display.prompt_preview_logger import PromptPreviewLogger
from src.maisaka.display.reasoning_steps import (
    find_reasoning_steps, reasoning_trace_id, register_reasoning_step, remove_reasoning_step,
)
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.webui.routers import reasoning_process as routes


def test_related_steps_use_persistent_index_and_isolate_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "PROMPT_LOG_ROOT", tmp_path)
    trace = "a" * 32
    stages = ["memory_heuristic", "planner", "expression_selector", "reply_supplement", "replyer"]
    for index, stage in enumerate(stages):
        path = tmp_path / stage / "chat" / f"{100 + index}.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"metadata": {"reasoning_trace_id": trace}}), encoding="utf-8")
        register_reasoning_step(tmp_path, path, trace)
    other = tmp_path / "planner" / "other-chat" / "999.json"
    other.parent.mkdir(parents=True)
    other.write_text("{}", encoding="utf-8")
    register_reasoning_step(tmp_path, other, trace)
    stale = tmp_path / "emotion" / "chat" / "888.json"
    register_reasoning_step(tmp_path, stale, trace)
    monkeypatch.setattr(routes, "_list_stage_names", lambda: pytest.fail("关联索引不应扫描历史目录"))
    load = routes._load_prompt_json
    reads = []

    def counted_load(path):
        reads.append(path)
        return load(path)

    monkeypatch.setattr(routes, "_load_prompt_json", counted_load)
    records = routes.list_related_reasoning_records("chat", "replyer", "104").records
    assert [record.stage for record in records] == stages
    assert len(reads) == 1
    assert all(record.session == "chat" for record in records)
    remove_reasoning_step(tmp_path, tmp_path / "expression_selector/chat/102.json")
    assert len(find_reasoning_steps(tmp_path, trace, "chat")) == 5  # 含尚未清理的失效路径


def test_related_steps_keep_legacy_passive_source(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "PROMPT_LOG_ROOT", tmp_path)
    planner = tmp_path / "planner/chat/100.json"
    planner.parent.mkdir(parents=True)
    planner.write_text("{}", encoding="utf-8")
    reply = tmp_path / "replyer/chat/200.json"
    reply.parent.mkdir(parents=True)
    reply.write_text(json.dumps({
        "request": {"selection_reason": "调用ID: execution"},
        "metadata": {"source_planner_uri": "/api/webui/config/maisaka-prompt-preview?path=planner%2Fchat%2F100.json"},
    }), encoding="utf-8")
    assert [(r.stage, r.stem) for r in routes.list_related_reasoning_records("chat", "replyer", "200").records] == [
        ("planner", "100"), ("replyer", "200"),
    ]


@pytest.mark.asyncio
async def test_trace_propagates_to_threads_and_preview_write_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(PromptPreviewLogger, "_BASE_DIR", tmp_path)
    tasks = []
    monkeypatch.setattr(PromptPreviewLogger, "_submit", tasks.append)
    token = reasoning_trace_id.set("b" * 32)
    try:
        preview = await asyncio.to_thread(
            PromptCLIVisualizer.build_text_preview_access, "request", category="planner", chat_id="chat",
            request_kind="planner", subtitle="",
        )
    finally:
        reasoning_trace_id.reset(token)
    assert reasoning_trace_id.get() is None
    assert preview.payload["metadata"]["reasoning_trace_id"] == "b" * 32
    assert tasks[0].trace_id == "b" * 32
    assert json.loads(tasks[0].content)["metadata"]["reasoning_trace_id"] == tasks[0].trace_id


@pytest.mark.asyncio
async def test_memory_steps_record_actual_host_injections(monkeypatch):
    async def heuristic(**kwargs):
        return "recalled memory"

    async def profiles(**kwargs):
        return ["person profile"]

    async def images(**kwargs):
        return ""

    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", True)
    monkeypatch.setattr("src.maisaka.reasoning_engine.heuristic_memory_injector.build_injection_message", heuristic)
    monkeypatch.setattr("src.maisaka.reasoning_engine.build_person_profile_injection_messages", profiles)
    monkeypatch.setattr("src.maisaka.reasoning_engine.image_memory_injector.build_injection_message", images)
    saved = []
    monkeypatch.setattr(PromptCLIVisualizer, "build_text_preview_access", lambda *args, **kwargs: saved.append(kwargs))
    runtime = SimpleNamespace(session_id="chat", log_prefix="test")
    engine = MaisakaReasoningEngine(runtime)
    injected = await engine._build_planner_injected_user_messages(
        profile_message=None, source_messages=[], deferred_tools_reminder="",
    )
    assert injected == ["recalled memory", "person profile"]
    assert [record["category"] for record in saved] == ["memory_heuristic", "memory_profile", "memory_image"]
    assert saved[0]["output_items"][0].parts[0].text == "recalled memory"
    assert saved[1]["output_items"][0].parts[0].text == "person profile"
