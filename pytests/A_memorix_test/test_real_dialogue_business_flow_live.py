from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest
import pytest_asyncio

from A_memorix.core.runtime.sdk_memory_kernel import KernelSearchRequest, SDKMemoryKernel
from src.chat.brain_chat.PFC import pfc_KnowledgeFetcher as knowledge_module
from src.memory_system import chat_history_summarizer as summarizer_module
from src.person_info import person_info as person_info_module
from src.services import memory_service as memory_service_module
from src.services.memory_service import memory_service


pytestmark = pytest.mark.skipif(
    os.getenv("MAIBOT_RUN_LIVE_MEMORY_TESTS") != "1",
    reason="需要显式开启真实 embedding / self-check 集成测试",
)

DATA_FILE = Path(__file__).parent / "data" / "real_dialogues" / "private_alice_weekend.json"


def _load_dialogue_fixture() -> Dict[str, Any]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


class _KernelBackedRuntimeManager:
    is_running = True

    def __init__(self, kernel: SDKMemoryKernel) -> None:
        self.kernel = kernel

    async def invoke_plugin(
        self,
        *,
        method: str,
        plugin_id: str,
        component_name: str,
        args: Dict[str, Any] | None,
        timeout_ms: int,
    ):
        del method, plugin_id, timeout_ms
        payload = args or {}
        if component_name == "search_memory":
            return await self.kernel.search_memory(
                KernelSearchRequest(
                    query=str(payload.get("query", "") or ""),
                    limit=int(payload.get("limit", 5) or 5),
                    mode=str(payload.get("mode", "hybrid") or "hybrid"),
                    chat_id=str(payload.get("chat_id", "") or ""),
                    person_id=str(payload.get("person_id", "") or ""),
                    time_start=payload.get("time_start"),
                    time_end=payload.get("time_end"),
                    respect_filter=bool(payload.get("respect_filter", True)),
                    user_id=str(payload.get("user_id", "") or ""),
                    group_id=str(payload.get("group_id", "") or ""),
                )
            )

        handler = getattr(self.kernel, component_name)
        result = handler(**payload)
        return await result if inspect.isawaitable(result) else result


async def _wait_for_import_task(task_id: str, *, timeout_seconds: float = 60.0) -> Dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_seconds))
    while asyncio.get_running_loop().time() < deadline:
        detail = await memory_service.import_admin(action="get", task_id=task_id, include_chunks=True)
        task = detail.get("task") or {}
        status = str(task.get("status", "") or "")
        if status in {"completed", "completed_with_errors", "failed", "cancelled"}:
            return detail
        await asyncio.sleep(0.2)
    raise AssertionError(f"导入任务在等待窗口内未结束: {task_id}")


def _join_hit_content(search_result) -> str:
    return "\n".join(hit.content for hit in search_result.hits)


@pytest_asyncio.fixture
async def live_dialogue_env(monkeypatch, tmp_path):
    scenario = _load_dialogue_fixture()
    session_cfg = scenario["session"]
    session = SimpleNamespace(
        session_id=session_cfg["session_id"],
        platform=session_cfg["platform"],
        user_id=session_cfg["user_id"],
        group_id=session_cfg["group_id"],
    )
    fake_chat_manager = SimpleNamespace(
        get_session_by_session_id=lambda session_id: session if session_id == session.session_id else None,
        get_session_name=lambda session_id: session_cfg["display_name"] if session_id == session.session_id else session_id,
    )

    monkeypatch.setattr(memory_service_module, "get_plugin_runtime_manager", None)
    monkeypatch.setattr(summarizer_module, "_chat_manager", fake_chat_manager)
    monkeypatch.setattr(knowledge_module, "_chat_manager", fake_chat_manager)
    monkeypatch.setattr(person_info_module, "_chat_manager", fake_chat_manager)

    data_dir = (tmp_path / "a_memorix_data").resolve()
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config={
            "storage": {"data_dir": str(data_dir)},
            "advanced": {"enable_auto_save": False},
            "memory": {"base_decay_interval_hours": 24},
            "person_profile": {"refresh_interval_minutes": 5},
        },
    )
    manager = _KernelBackedRuntimeManager(kernel)
    monkeypatch.setattr(memory_service_module, "get_plugin_runtime_manager", lambda: manager)

    await kernel.initialize()
    try:
        yield {
            "scenario": scenario,
            "kernel": kernel,
            "session": session,
        }
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_live_runtime_self_check_passes(live_dialogue_env):
    report = await memory_service.runtime_admin(action="refresh_self_check")

    assert report["success"] is True
    assert report["report"]["ok"] is True
    assert report["report"]["encoded_dimension"] > 0


@pytest.mark.asyncio
async def test_live_import_flow_makes_fixture_searchable(live_dialogue_env):
    scenario = live_dialogue_env["scenario"]

    created = await memory_service.import_admin(
        action="create_paste",
        name="private_alice.json",
        input_mode="json",
        llm_enabled=False,
        content=json.dumps(scenario["import_payload"], ensure_ascii=False),
    )

    assert created["success"] is True
    detail = await _wait_for_import_task(created["task"]["task_id"])
    assert detail["task"]["status"] == "completed"

    search = await memory_service.search(
        scenario["search_queries"]["direct"],
        mode="search",
        respect_filter=False,
    )

    assert search.hits
    joined = _join_hit_content(search)
    for keyword in scenario["expectations"]["search_keywords"]:
        assert keyword in joined


@pytest.mark.asyncio
async def test_live_summarizer_flow_persists_summary_to_long_term_memory(live_dialogue_env):
    scenario = live_dialogue_env["scenario"]
    record = scenario["chat_history_record"]

    summarizer = summarizer_module.ChatHistorySummarizer(live_dialogue_env["session"].session_id)
    await summarizer._import_to_long_term_memory(
        record_id=record["record_id"],
        theme=record["theme"],
        summary=record["summary"],
        participants=record["participants"],
        start_time=record["start_time"],
        end_time=record["end_time"],
        original_text=record["original_text"],
    )

    search = await memory_service.search(
        scenario["search_queries"]["direct"],
        mode="search",
        chat_id=live_dialogue_env["session"].session_id,
    )

    assert search.hits
    joined = _join_hit_content(search)
    for keyword in scenario["expectations"]["search_keywords"]:
        assert keyword in joined


@pytest.mark.asyncio
async def test_live_person_fact_writeback_is_searchable(live_dialogue_env, monkeypatch):
    scenario = live_dialogue_env["scenario"]

    class _KnownPerson:
        def __init__(self, person_id: str) -> None:
            self.person_id = person_id
            self.is_known = True
            self.person_name = scenario["person"]["person_name"]

    monkeypatch.setattr(
        person_info_module,
        "get_person_id_by_person_name",
        lambda person_name: scenario["person"]["person_id"],
    )
    monkeypatch.setattr(person_info_module, "Person", _KnownPerson)

    await person_info_module.store_person_memory_from_answer(
        scenario["person"]["person_name"],
        scenario["person_fact"]["memory_content"],
        live_dialogue_env["session"].session_id,
    )

    search = await memory_service.search(
        scenario["search_queries"]["direct"],
        mode="search",
        chat_id=live_dialogue_env["session"].session_id,
        person_id=scenario["person"]["person_id"],
    )

    assert search.hits
    joined = _join_hit_content(search)
    for keyword in scenario["expectations"]["search_keywords"]:
        assert keyword in joined


@pytest.mark.asyncio
async def test_live_private_knowledge_fetcher_reads_long_term_memory(live_dialogue_env):
    scenario = live_dialogue_env["scenario"]

    await memory_service.ingest_text(
        external_id="fixture:knowledge_fetcher",
        source_type="dialogue_note",
        text=scenario["person_fact"]["memory_content"],
        chat_id=live_dialogue_env["session"].session_id,
        person_ids=[scenario["person"]["person_id"]],
        participants=[scenario["person"]["person_name"]],
        respect_filter=False,
    )

    fetcher = knowledge_module.KnowledgeFetcher(
        private_name=scenario["session"]["display_name"],
        stream_id=live_dialogue_env["session"].session_id,
    )
    knowledge_text, _ = await fetcher.fetch(scenario["search_queries"]["knowledge_fetcher"], [])

    for keyword in scenario["expectations"]["search_keywords"]:
        assert keyword in knowledge_text


@pytest.mark.asyncio
async def test_live_person_profile_contains_stable_traits(live_dialogue_env, monkeypatch):
    scenario = live_dialogue_env["scenario"]

    class _KnownPerson:
        def __init__(self, person_id: str) -> None:
            self.person_id = person_id
            self.is_known = True
            self.person_name = scenario["person"]["person_name"]

    monkeypatch.setattr(
        person_info_module,
        "get_person_id_by_person_name",
        lambda person_name: scenario["person"]["person_id"],
    )
    monkeypatch.setattr(person_info_module, "Person", _KnownPerson)

    await person_info_module.store_person_memory_from_answer(
        scenario["person"]["person_name"],
        scenario["person_fact"]["memory_content"],
        live_dialogue_env["session"].session_id,
    )

    profile = await memory_service.get_person_profile(
        scenario["person"]["person_id"],
        chat_id=live_dialogue_env["session"].session_id,
    )

    assert profile.evidence
    assert any(keyword in profile.summary for keyword in scenario["expectations"]["profile_keywords"])


@pytest.mark.asyncio
async def test_live_summary_flow_generates_queryable_episode(live_dialogue_env):
    scenario = live_dialogue_env["scenario"]
    record = scenario["chat_history_record"]

    summarizer = summarizer_module.ChatHistorySummarizer(live_dialogue_env["session"].session_id)
    await summarizer._import_to_long_term_memory(
        record_id=record["record_id"],
        theme=record["theme"],
        summary=record["summary"],
        participants=record["participants"],
        start_time=record["start_time"],
        end_time=record["end_time"],
        original_text=record["original_text"],
    )

    episodes = await memory_service.episode_admin(
        action="query",
        source=scenario["expectations"]["episode_source"],
        limit=5,
    )

    assert episodes["success"] is True
    assert int(episodes["count"]) >= 1
