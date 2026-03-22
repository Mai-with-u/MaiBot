from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import numpy as np
import pytest
import pytest_asyncio

from A_memorix.core.runtime import sdk_memory_kernel as kernel_module
from A_memorix.core.runtime.sdk_memory_kernel import KernelSearchRequest, SDKMemoryKernel
from src.chat.brain_chat.PFC import pfc_KnowledgeFetcher as knowledge_module
from src.memory_system import chat_history_summarizer as summarizer_module
from src.memory_system.retrieval_tools.query_long_term_memory import query_long_term_memory
from src.person_info import person_info as person_info_module
from src.services import memory_service as memory_service_module
from src.services.memory_service import MemorySearchResult, memory_service


DATA_FILE = Path(__file__).parent / "data" / "benchmarks" / "long_novel_memory_benchmark.json"
REPORT_FILE = Path(__file__).parent / "data" / "benchmarks" / "results" / "long_novel_memory_benchmark_report.json"


def _load_benchmark_fixture() -> Dict[str, Any]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


class _FakeEmbeddingAdapter:
    def __init__(self, dimension: int = 32) -> None:
        self.dimension = dimension

    async def _detect_dimension(self) -> int:
        return self.dimension

    async def encode(self, texts, dimensions=None):
        dim = int(dimensions or self.dimension)
        if isinstance(texts, str):
            sequence = [texts]
            single = True
        else:
            sequence = list(texts)
            single = False

        rows = []
        for text in sequence:
            vec = np.zeros(dim, dtype=np.float32)
            for ch in str(text or ""):
                code = ord(ch)
                vec[code % dim] += 1.0
                vec[(code * 7) % dim] += 0.5
            if not vec.any():
                vec[0] = 1.0
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            rows.append(vec)
        payload = np.vstack(rows)
        return payload[0] if single else payload


class _KnownPerson:
    def __init__(self, person_id: str, registry: Dict[str, str], reverse_registry: Dict[str, str]) -> None:
        self.person_id = person_id
        self.is_known = person_id in reverse_registry
        self.person_name = reverse_registry.get(person_id, "")
        self._registry = registry


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


async def _wait_for_import_task(task_id: str, *, max_rounds: int = 200, sleep_seconds: float = 0.05) -> Dict[str, Any]:
    for _ in range(max_rounds):
        detail = await memory_service.import_admin(action="get", task_id=task_id, include_chunks=True)
        task = detail.get("task") or {}
        status = str(task.get("status", "") or "")
        if status in {"completed", "completed_with_errors", "failed", "cancelled"}:
            return detail
        await asyncio.sleep(max(0.01, float(sleep_seconds)))
    raise AssertionError(f"导入任务在等待窗口内未结束: {task_id}")


def _join_hit_content(search_result: MemorySearchResult) -> str:
    return "\n".join(hit.content for hit in search_result.hits)


def _keyword_hits(text: str, keywords: List[str]) -> int:
    haystack = str(text or "")
    return sum(1 for keyword in keywords if keyword in haystack)


def _keyword_recall(text: str, keywords: List[str]) -> float:
    if not keywords:
        return 1.0
    return _keyword_hits(text, keywords) / float(len(keywords))


def _hit_blob(hit) -> str:
    meta = hit.metadata if isinstance(hit.metadata, dict) else {}
    return "\n".join(
        [
            str(hit.content or ""),
            str(hit.title or ""),
            str(hit.source or ""),
            json.dumps(meta, ensure_ascii=False),
        ]
    )


def _first_relevant_rank(search_result: MemorySearchResult, keywords: List[str], minimum_keyword_hits: int) -> int:
    for index, hit in enumerate(search_result.hits[:5], start=1):
        if _keyword_hits(_hit_blob(hit), keywords) >= max(1, int(minimum_keyword_hits or len(keywords))):
            return index
    return 0


def _episode_blob_from_items(items: List[Dict[str, Any]]) -> str:
    return "\n".join(
        (
            f"{item.get('title', '')}\n"
            f"{item.get('summary', '')}\n"
            f"{json.dumps(item.get('keywords', []), ensure_ascii=False)}\n"
            f"{json.dumps(item.get('participants', []), ensure_ascii=False)}"
        )
        for item in items
    )


def _episode_blob_from_hits(search_result: MemorySearchResult) -> str:
    chunks = []
    for hit in search_result.hits:
        meta = hit.metadata if isinstance(hit.metadata, dict) else {}
        chunks.append(
            "\n".join(
                [
                    str(hit.title or ""),
                    str(hit.content or ""),
                    json.dumps(meta.get("keywords", []) or [], ensure_ascii=False),
                    json.dumps(meta.get("participants", []) or [], ensure_ascii=False),
                ]
            )
        )
    return "\n".join(chunks)


async def _evaluate_episode_generation(*, session_id: str, episode_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    episode_source = f"chat_summary:{session_id}"
    payload = await memory_service.episode_admin(
        action="query",
        source=episode_source,
        limit=20,
    )
    items = payload.get("items") or []
    blob = _episode_blob_from_items(items)
    reports: List[Dict[str, Any]] = []
    success_rate = 0.0
    keyword_recall = 0.0

    for case in episode_cases:
        recall = _keyword_recall(blob, case["expected_keywords"])
        success = bool(items) and recall >= float(case.get("minimum_keyword_recall", 1.0))
        success_rate += 1.0 if success else 0.0
        keyword_recall += recall
        reports.append(
            {
                "query": case["query"],
                "success": success,
                "keyword_recall": recall,
                "episode_count": len(items),
                "top_episode": items[0] if items else None,
            }
        )

    total = max(1, len(episode_cases))
    return {
        "success_rate": round(success_rate / total, 4),
        "keyword_recall": round(keyword_recall / total, 4),
        "episode_count": len(items),
        "reports": reports,
    }


async def _evaluate_episode_admin_query(*, session_id: str, episode_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    reports: List[Dict[str, Any]] = []
    success_rate = 0.0
    keyword_recall = 0.0
    episode_source = f"chat_summary:{session_id}"

    for case in episode_cases:
        payload = await memory_service.episode_admin(
            action="query",
            source=episode_source,
            query=case["query"],
            limit=5,
        )
        items = payload.get("items") or []
        blob = "\n".join(
            f"{item.get('title', '')}\n{item.get('summary', '')}\n{json.dumps(item.get('keywords', []), ensure_ascii=False)}"
            for item in items
        )
        recall = _keyword_recall(blob, case["expected_keywords"])
        success = bool(items) and recall >= float(case.get("minimum_keyword_recall", 1.0))
        success_rate += 1.0 if success else 0.0
        keyword_recall += recall
        reports.append(
            {
                "query": case["query"],
                "success": success,
                "keyword_recall": recall,
                "episode_count": len(items),
                "top_episode": items[0] if items else None,
            }
        )

    total = max(1, len(episode_cases))
    return {
        "success_rate": round(success_rate / total, 4),
        "keyword_recall": round(keyword_recall / total, 4),
        "reports": reports,
    }


async def _evaluate_episode_search_mode(*, session_id: str, episode_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    reports: List[Dict[str, Any]] = []
    success_rate = 0.0
    keyword_recall = 0.0

    for case in episode_cases:
        result = await memory_service.search(
            case["query"],
            mode="episode",
            chat_id=session_id,
            respect_filter=False,
            limit=5,
        )
        blob = _episode_blob_from_hits(result)
        recall = _keyword_recall(blob, case["expected_keywords"])
        success = bool(result.hits) and recall >= float(case.get("minimum_keyword_recall", 1.0))
        success_rate += 1.0 if success else 0.0
        keyword_recall += recall
        reports.append(
            {
                "query": case["query"],
                "success": success,
                "keyword_recall": recall,
                "episode_count": len(result.hits),
                "top_episode": result.hits[0].to_dict() if result.hits else None,
            }
        )

    total = max(1, len(episode_cases))
    return {
        "success_rate": round(success_rate / total, 4),
        "keyword_recall": round(keyword_recall / total, 4),
        "reports": reports,
    }


async def _evaluate_tool_modes(*, session_id: str, dataset: Dict[str, Any]) -> Dict[str, Any]:
    search_case = dataset["search_cases"][0]
    episode_case = dataset["episode_cases"][0]
    aggregate_case = dataset["knowledge_fetcher_cases"][0]
    tool_cases = [
        {
            "name": "search",
            "kwargs": {
                "query": "蓝漆铁盒 北塔木梯",
                "mode": "search",
                "chat_id": session_id,
                "limit": 5,
            },
            "expected_keywords": ["蓝漆铁盒", "北塔木梯", "海潮图"],
            "minimum_keyword_recall": 0.67,
        },
        {
            "name": "time",
            "kwargs": {
                "query": "蓝漆铁盒 北塔",
                "mode": "time",
                "chat_id": session_id,
                "limit": 5,
                "time_expression": "最近7天",
            },
            "expected_keywords": ["蓝漆铁盒", "北塔木梯"],
            "minimum_keyword_recall": 0.67,
        },
        {
            "name": "episode",
            "kwargs": {
                "query": episode_case["query"],
                "mode": "episode",
                "chat_id": session_id,
                "limit": 5,
            },
            "expected_keywords": episode_case["expected_keywords"],
            "minimum_keyword_recall": 0.67,
        },
        {
            "name": "aggregate",
            "kwargs": {
                "query": aggregate_case["query"],
                "mode": "aggregate",
                "chat_id": session_id,
                "limit": 5,
            },
            "expected_keywords": aggregate_case["expected_keywords"],
            "minimum_keyword_recall": 0.67,
        },
    ]
    reports: List[Dict[str, Any]] = []
    success_rate = 0.0
    keyword_recall = 0.0

    for case in tool_cases:
        text = await query_long_term_memory(**case["kwargs"])
        recall = _keyword_recall(text, case["expected_keywords"])
        success = (
            "失败" not in text
            and "无法解析" not in text
            and "未找到" not in text
            and recall >= float(case["minimum_keyword_recall"])
        )
        success_rate += 1.0 if success else 0.0
        keyword_recall += recall
        reports.append(
            {
                "name": case["name"],
                "success": success,
                "keyword_recall": recall,
                "preview": text[:320],
            }
        )

    total = max(1, len(tool_cases))
    return {
        "success_rate": round(success_rate / total, 4),
        "keyword_recall": round(keyword_recall / total, 4),
        "reports": reports,
    }


@pytest_asyncio.fixture
async def benchmark_env(monkeypatch, tmp_path):
    dataset = _load_benchmark_fixture()
    session_cfg = dataset["session"]
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

    registry = {item["person_name"]: item["person_id"] for item in dataset["person_writebacks"]}
    reverse_registry = {value: key for key, value in registry.items()}

    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kwargs: _FakeEmbeddingAdapter())

    async def fake_self_check(**kwargs):
        return {"ok": True, "message": "ok", "encoded_dimension": 32}

    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", fake_self_check)
    monkeypatch.setattr(memory_service_module, "get_plugin_runtime_manager", None)
    monkeypatch.setattr(summarizer_module, "_chat_manager", fake_chat_manager)
    monkeypatch.setattr(knowledge_module, "_chat_manager", fake_chat_manager)
    monkeypatch.setattr(person_info_module, "_chat_manager", fake_chat_manager)
    monkeypatch.setattr(person_info_module, "get_person_id_by_person_name", lambda person_name: registry.get(str(person_name or "").strip(), ""))
    monkeypatch.setattr(
        person_info_module,
        "Person",
        lambda person_id: _KnownPerson(person_id=str(person_id or ""), registry=registry, reverse_registry=reverse_registry),
    )

    data_dir = (tmp_path / "a_memorix_benchmark_data").resolve()
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
            "dataset": dataset,
            "kernel": kernel,
            "session": session,
            "person_registry": registry,
        }
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_long_novel_memory_benchmark(benchmark_env):
    dataset = benchmark_env["dataset"]
    session_id = benchmark_env["session"].session_id

    created = await memory_service.import_admin(
        action="create_paste",
        name="long_novel_memory_benchmark.json",
        input_mode="json",
        llm_enabled=False,
        content=json.dumps(dataset["import_payload"], ensure_ascii=False),
    )
    assert created["success"] is True

    import_detail = await _wait_for_import_task(created["task"]["task_id"])
    assert import_detail["task"]["status"] == "completed"

    for record in dataset["chat_history_records"]:
        summarizer = summarizer_module.ChatHistorySummarizer(session_id)
        await summarizer._import_to_long_term_memory(
            record_id=record["record_id"],
            theme=record["theme"],
            summary=record["summary"],
            participants=record["participants"],
            start_time=record["start_time"],
            end_time=record["end_time"],
            original_text=record["original_text"],
        )

    for payload in dataset["person_writebacks"]:
        await person_info_module.store_person_memory_from_answer(
            payload["person_name"],
            payload["memory_content"],
            session_id,
        )

    await memory_service.episode_admin(action="process_pending", limit=100, max_retry=2)

    search_case_reports: List[Dict[str, Any]] = []
    search_accuracy_at_1 = 0.0
    search_recall_at_5 = 0.0
    search_precision_at_5 = 0.0
    search_mrr = 0.0
    search_keyword_recall = 0.0

    for case in dataset["search_cases"]:
        result = await memory_service.search(case["query"], mode="search", respect_filter=False, limit=5)
        joined = _join_hit_content(result)
        rank = _first_relevant_rank(result, case["expected_keywords"], case.get("minimum_keyword_hits", len(case["expected_keywords"])))
        relevant_hits = sum(
            1
            for hit in result.hits[:5]
            if _keyword_hits(_hit_blob(hit), case["expected_keywords"]) >= max(1, int(case.get("minimum_keyword_hits", len(case["expected_keywords"]))))
        )
        keyword_recall = _keyword_recall(joined, case["expected_keywords"])
        search_accuracy_at_1 += 1.0 if rank == 1 else 0.0
        search_recall_at_5 += 1.0 if rank > 0 else 0.0
        search_precision_at_5 += relevant_hits / float(max(1, min(5, len(result.hits))))
        search_mrr += 1.0 / float(rank) if rank > 0 else 0.0
        search_keyword_recall += keyword_recall
        search_case_reports.append(
            {
                "query": case["query"],
                "rank_of_first_relevant": rank,
                "relevant_hits_top5": relevant_hits,
                "keyword_recall_top5": keyword_recall,
                "top_hit": result.hits[0].to_dict() if result.hits else None,
            }
        )

    search_total = max(1, len(dataset["search_cases"]))

    writeback_reports: List[Dict[str, Any]] = []
    writeback_success_rate = 0.0
    writeback_keyword_recall = 0.0
    for payload in dataset["person_writebacks"]:
        query = " ".join(payload["expected_keywords"])
        result = await memory_service.search(
            query,
            mode="search",
            chat_id=session_id,
            person_id=payload["person_id"],
            respect_filter=False,
            limit=5,
        )
        joined = _join_hit_content(result)
        recall = _keyword_recall(joined, payload["expected_keywords"])
        success = bool(result.hits) and recall >= 0.67
        writeback_success_rate += 1.0 if success else 0.0
        writeback_keyword_recall += recall
        writeback_reports.append(
            {
                "person_id": payload["person_id"],
                "success": success,
                "keyword_recall": recall,
                "hit_count": len(result.hits),
            }
        )
    writeback_total = max(1, len(dataset["person_writebacks"]))

    knowledge_reports: List[Dict[str, Any]] = []
    knowledge_success_rate = 0.0
    knowledge_keyword_recall = 0.0
    fetcher = knowledge_module.KnowledgeFetcher(
        private_name=dataset["session"]["display_name"],
        stream_id=session_id,
    )
    for case in dataset["knowledge_fetcher_cases"]:
        knowledge_text, _ = await fetcher.fetch(case["query"], [])
        recall = _keyword_recall(knowledge_text, case["expected_keywords"])
        success = recall >= float(case.get("minimum_keyword_recall", 1.0))
        knowledge_success_rate += 1.0 if success else 0.0
        knowledge_keyword_recall += recall
        knowledge_reports.append(
            {
                "query": case["query"],
                "success": success,
                "keyword_recall": recall,
                "preview": knowledge_text[:300],
            }
        )
    knowledge_total = max(1, len(dataset["knowledge_fetcher_cases"]))

    profile_reports: List[Dict[str, Any]] = []
    profile_success_rate = 0.0
    profile_keyword_recall = 0.0
    profile_evidence_rate = 0.0
    for case in dataset["profile_cases"]:
        profile = await memory_service.get_person_profile(case["person_id"], chat_id=session_id)
        recall = _keyword_recall(profile.summary, case["expected_keywords"])
        has_evidence = bool(profile.evidence)
        success = recall >= float(case.get("minimum_keyword_recall", 1.0)) and has_evidence
        profile_success_rate += 1.0 if success else 0.0
        profile_keyword_recall += recall
        profile_evidence_rate += 1.0 if has_evidence else 0.0
        profile_reports.append(
            {
                "person_id": case["person_id"],
                "success": success,
                "keyword_recall": recall,
                "evidence_count": len(profile.evidence),
                "summary_preview": profile.summary[:240],
            }
        )
    profile_total = max(1, len(dataset["profile_cases"]))

    episode_generation_auto = await _evaluate_episode_generation(session_id=session_id, episode_cases=dataset["episode_cases"])
    episode_admin_query_auto = await _evaluate_episode_admin_query(session_id=session_id, episode_cases=dataset["episode_cases"])
    episode_search_mode_auto = await _evaluate_episode_search_mode(session_id=session_id, episode_cases=dataset["episode_cases"])
    episode_rebuild = await memory_service.episode_admin(
        action="rebuild",
        source=f"chat_summary:{session_id}",
    )
    episode_generation_after_rebuild = await _evaluate_episode_generation(session_id=session_id, episode_cases=dataset["episode_cases"])
    episode_admin_query_after_rebuild = await _evaluate_episode_admin_query(session_id=session_id, episode_cases=dataset["episode_cases"])
    episode_search_mode_after_rebuild = await _evaluate_episode_search_mode(session_id=session_id, episode_cases=dataset["episode_cases"])
    tool_modes = await _evaluate_tool_modes(session_id=session_id, dataset=dataset)

    report = {
        "dataset": dataset["meta"],
        "import": {
            "task_id": created["task"]["task_id"],
            "status": import_detail["task"]["status"],
            "paragraph_count": len(dataset["import_payload"]["paragraphs"]),
        },
        "metrics": {
            "search": {
                "accuracy_at_1": round(search_accuracy_at_1 / search_total, 4),
                "recall_at_5": round(search_recall_at_5 / search_total, 4),
                "precision_at_5": round(search_precision_at_5 / search_total, 4),
                "mrr": round(search_mrr / search_total, 4),
                "keyword_recall_at_5": round(search_keyword_recall / search_total, 4),
            },
            "writeback": {
                "success_rate": round(writeback_success_rate / writeback_total, 4),
                "keyword_recall": round(writeback_keyword_recall / writeback_total, 4),
            },
            "knowledge_fetcher": {
                "success_rate": round(knowledge_success_rate / knowledge_total, 4),
                "keyword_recall": round(knowledge_keyword_recall / knowledge_total, 4),
            },
            "profile": {
                "success_rate": round(profile_success_rate / profile_total, 4),
                "keyword_recall": round(profile_keyword_recall / profile_total, 4),
                "evidence_rate": round(profile_evidence_rate / profile_total, 4),
            },
            "tool_modes": {
                "success_rate": tool_modes["success_rate"],
                "keyword_recall": tool_modes["keyword_recall"],
            },
            "episode_generation_auto": {
                "success_rate": episode_generation_auto["success_rate"],
                "keyword_recall": episode_generation_auto["keyword_recall"],
                "episode_count": episode_generation_auto["episode_count"],
            },
            "episode_generation_after_rebuild": {
                "success_rate": episode_generation_after_rebuild["success_rate"],
                "keyword_recall": episode_generation_after_rebuild["keyword_recall"],
                "episode_count": episode_generation_after_rebuild["episode_count"],
                "rebuild_success": bool(episode_rebuild.get("success", False)),
            },
            "episode_admin_query_auto": {
                "success_rate": episode_admin_query_auto["success_rate"],
                "keyword_recall": episode_admin_query_auto["keyword_recall"],
            },
            "episode_admin_query_after_rebuild": {
                "success_rate": episode_admin_query_after_rebuild["success_rate"],
                "keyword_recall": episode_admin_query_after_rebuild["keyword_recall"],
                "rebuild_success": bool(episode_rebuild.get("success", False)),
            },
            "episode_search_mode_auto": {
                "success_rate": episode_search_mode_auto["success_rate"],
                "keyword_recall": episode_search_mode_auto["keyword_recall"],
            },
            "episode_search_mode_after_rebuild": {
                "success_rate": episode_search_mode_after_rebuild["success_rate"],
                "keyword_recall": episode_search_mode_after_rebuild["keyword_recall"],
                "rebuild_success": bool(episode_rebuild.get("success", False)),
            },
        },
        "cases": {
            "search": search_case_reports,
            "writeback": writeback_reports,
            "knowledge_fetcher": knowledge_reports,
            "profile": profile_reports,
            "tool_modes": tool_modes["reports"],
            "episode_generation_auto": episode_generation_auto["reports"],
            "episode_generation_after_rebuild": episode_generation_after_rebuild["reports"],
            "episode_admin_query_auto": episode_admin_query_auto["reports"],
            "episode_admin_query_after_rebuild": episode_admin_query_after_rebuild["reports"],
            "episode_search_mode_auto": episode_search_mode_auto["reports"],
            "episode_search_mode_after_rebuild": episode_search_mode_after_rebuild["reports"],
        },
    }

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))

    assert report["import"]["status"] == "completed"
    assert report["metrics"]["search"]["accuracy_at_1"] >= 0.35
    assert report["metrics"]["search"]["recall_at_5"] >= 0.6
    assert report["metrics"]["search"]["keyword_recall_at_5"] >= 0.8
    assert report["metrics"]["writeback"]["success_rate"] >= 0.66
    assert report["metrics"]["knowledge_fetcher"]["success_rate"] >= 0.66
    assert report["metrics"]["knowledge_fetcher"]["keyword_recall"] >= 0.75
    assert report["metrics"]["profile"]["success_rate"] >= 0.66
    assert report["metrics"]["profile"]["evidence_rate"] >= 1.0
    assert report["metrics"]["tool_modes"]["success_rate"] >= 0.75
    assert report["metrics"]["episode_generation_after_rebuild"]["rebuild_success"] is True
    assert report["metrics"]["episode_generation_after_rebuild"]["episode_count"] >= report["metrics"]["episode_generation_auto"]["episode_count"]
