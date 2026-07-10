from __future__ import annotations

from pathlib import Path
from time import monotonic, sleep
from typing import Any, Dict, Generator
from uuid import uuid4

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.A_memorix import host_service as host_service_module
from src.A_memorix.core.runtime import sdk_memory_kernel as kernel_module
from src.A_memorix.core.utils import retrieval_tuning_manager as tuning_manager_module
from src.webui.dependencies import require_auth
from src.webui.routers import memory as memory_router_module


REQUEST_TIMEOUT_SECONDS = 30
IMPORT_TIMEOUT_SECONDS = 120
TUNING_TIMEOUT_SECONDS = 420

IMPORT_TERMINAL_STATUSES = {"completed", "completed_with_errors", "failed", "cancelled"}
TUNING_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class _FakeEmbeddingManager:
    def __init__(self, dimension: int = 64) -> None:
        self.default_dimension = dimension

    async def _detect_dimension(self) -> int:
        return self.default_dimension

    async def encode(self, text: Any, **kwargs: Any) -> Any:
        del kwargs
        import numpy as np

        def _encode_one(raw: Any) -> Any:
            content = str(raw or "")
            vector = np.zeros(self.default_dimension, dtype=np.float32)
            for index, byte in enumerate(content.encode("utf-8")):
                vector[index % self.default_dimension] += float((byte % 17) + 1)
            norm = float(np.linalg.norm(vector))
            if norm > 0:
                vector /= norm
            return vector

        if isinstance(text, (list, tuple)):
            return np.stack([_encode_one(item) for item in text]).astype(np.float32)
        return _encode_one(text).astype(np.float32)

    async def encode_batch(self, texts: Any, **kwargs: Any) -> Any:
        return await self.encode(texts, **kwargs)


def _build_test_config(data_dir: Path) -> Dict[str, Any]:
    return {
        "storage": {
            "data_dir": str(data_dir),
        },
        "advanced": {
            "enable_auto_save": False,
        },
        "embedding": {
            "dimension": 64,
            "batch_size": 4,
            "max_concurrent": 1,
            "retry": {
                "max_attempts": 1,
                "min_wait_seconds": 0.1,
                "max_wait_seconds": 0.2,
                "backoff_multiplier": 1.0,
            },
            "fallback": {
                "enabled": True,
                "allow_metadata_only_write": True,
                "probe_interval_seconds": 30,
            },
            "paragraph_vector_backfill": {
                "enabled": False,
                "interval_seconds": 60,
                "batch_size": 32,
                "max_retry": 2,
            },
        },
        "retrieval": {
            "enable_parallel": False,
            "enable_ppr": False,
            "top_k_paragraphs": 20,
            "top_k_relations": 10,
            "top_k_final": 10,
            "alpha": 0.5,
            "search": {
                "smart_fallback": {
                    "enabled": True,
                },
            },
            "sparse": {
                "enabled": True,
                "mode": "auto",
                "candidate_k": 80,
                "relation_candidate_k": 60,
            },
            "fusion": {
                "method": "weighted_rrf",
                "rrf_k": 60,
                "vector_weight": 0.7,
                "bm25_weight": 0.3,
            },
        },
        "threshold": {
            "percentile": 70.0,
            "min_results": 1,
        },
        "web": {
            "tuning": {
                "enabled": True,
                "poll_interval_ms": 300,
                "max_queue_size": 4,
                "default_objective": "balanced",
                "default_intensity": "quick",
                "default_sample_size": 4,
                "default_top_k_eval": 5,
                "eval_query_timeout_seconds": 1.0,
                "llm_retry": {
                    "max_attempts": 1,
                    "min_wait_seconds": 0.1,
                    "max_wait_seconds": 0.2,
                    "backoff_multiplier": 1.0,
                },
            },
        },
    }


def _assert_response_ok(response: Any) -> Dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("success", True) is True, payload
    return payload


def _wait_for_runtime_ready(client: TestClient, *, timeout_seconds: float = 30.0) -> Dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    last_payload: Dict[str, Any] = {}
    while monotonic() < deadline:
        response = client.get("/api/webui/memory/runtime/config")
        assert response.status_code == 200, response.text
        payload = response.json()
        last_payload = payload
        if payload.get("success", False) is True:
            return payload
        sleep(0.2)
    raise AssertionError(f"A_Memorix 运行时初始化超时: last_payload={last_payload}")


def _wait_for_import_task_terminal(client: TestClient, task_id: str, *, timeout_seconds: float = IMPORT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    last_payload: Dict[str, Any] = {}
    while monotonic() < deadline:
        response = client.get(
            f"/api/webui/memory/import/tasks/{task_id}",
            params={"include_chunks": True},
        )
        payload = _assert_response_ok(response)
        last_payload = payload
        task = payload.get("task") or {}
        status = str(task.get("status", "") or "")
        if status in IMPORT_TERMINAL_STATUSES:
            return task
        sleep(0.2)
    raise AssertionError(f"导入任务超时: task_id={task_id}, last_payload={last_payload}")


def _wait_for_tuning_task_terminal(client: TestClient, task_id: str, *, timeout_seconds: float = TUNING_TIMEOUT_SECONDS) -> Dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    last_payload: Dict[str, Any] = {}
    while monotonic() < deadline:
        response = client.get(
            f"/api/webui/memory/retrieval_tuning/tasks/{task_id}",
            params={"include_rounds": False},
        )
        payload = _assert_response_ok(response)
        last_payload = payload
        task = payload.get("task") or {}
        status = str(task.get("status", "") or "")
        if status in TUNING_TERMINAL_STATUSES:
            return task
        sleep(0.3)
    raise AssertionError(f"调优任务超时: task_id={task_id}, last_payload={last_payload}")


def _wait_for_query_hit(client: TestClient, query: str, *, timeout_seconds: float = 30.0) -> Dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    last_payload: Dict[str, Any] = {}
    while monotonic() < deadline:
        payload = _assert_response_ok(
            client.get(
                "/api/webui/memory/query/aggregate",
                params={"query": query, "limit": 20},
            )
        )
        last_payload = payload
        hits = payload.get("hits") or []
        if isinstance(hits, list) and len(hits) > 0:
            return payload
        sleep(0.2)
    raise AssertionError(f"检索命中超时: query={query}, last_payload={last_payload}")


def _get_source_item(client: TestClient, source_name: str) -> Dict[str, Any] | None:
    payload = _assert_response_ok(client.get("/api/webui/memory/sources"))
    items = payload.get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("source", "") or "") == source_name:
            return item
    return None


def _source_paragraph_count(item: Dict[str, Any] | None) -> int:
    payload = item or {}
    if "paragraph_count" in payload:
        return int(payload.get("paragraph_count", 0) or 0)
    return int(payload.get("count", 0) or 0)


def _wait_for_source_paragraph_count(
    client: TestClient,
    source_name: str,
    *,
    min_count: int,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    last_item: Dict[str, Any] = {}
    while monotonic() < deadline:
        item = _get_source_item(client, source_name)
        count = _source_paragraph_count(item)
        if count >= int(min_count):
            return item or {}
        if item:
            last_item = dict(item)
        sleep(0.2)
    raise AssertionError(
        f"等待来源段落计数超时: source={source_name}, min_count={min_count}, last_item={last_item}"
    )


def _create_multitype_upload_task(client: TestClient) -> str:
    structured_json = {
        "paragraphs": [
            {
                "content": "Alice 携带地图前往火星港。",
                "source": "integration-upload-json",
                "entities": ["Alice", "地图", "火星港"],
                "relations": [
                    {"subject": "Alice", "predicate": "携带", "object": "地图"},
                    {"subject": "Alice", "predicate": "前往", "object": "火星港"},
                ],
            }
        ]
    }
    extra_json = {
        "paragraphs": [
            {
                "content": "Carol 记录了一条补充说明。",
                "source": "integration-upload-json-extra",
                "entities": ["Carol"],
                "relations": [],
            }
        ]
    }
    payload_json = json.dumps(
        {
            "input_mode": "text",
            "llm_enabled": False,
            "file_concurrency": 2,
            "chunk_concurrency": 2,
            "dedupe_policy": "none",
        },
        ensure_ascii=False,
    )
    files = [
        ("files", ("integration-notes.txt", "Alice 在测试环境记录了一条长期记忆。".encode("utf-8"), "text/plain")),
        ("files", ("integration-diary.md", "# 日志\nBob 与 Alice 讨论了导图。".encode("utf-8"), "text/markdown")),
        ("files", ("integration-structured.json", json.dumps(structured_json, ensure_ascii=False).encode("utf-8"), "application/json")),
        ("files", ("integration-extra.json", json.dumps(extra_json, ensure_ascii=False).encode("utf-8"), "application/json")),
    ]

    response = client.post(
        "/api/webui/memory/import/upload",
        data={"payload_json": payload_json},
        files=files,
    )
    payload = _assert_response_ok(response)
    task_id = str((payload.get("task") or {}).get("task_id") or "").strip()
    assert task_id, payload
    return task_id


def _create_seed_paste_task(client: TestClient, *, source: str, unique_token: str) -> str:
    seed_payload = {
        "paragraphs": [
            {
                "content": f"Alice 在火星港携带地图并记录了口令 {unique_token}。",
                "source": source,
                "entities": ["Alice", "火星港", "地图"],
                "relations": [
                    {"subject": "Alice", "predicate": "前往", "object": "火星港"},
                    {"subject": "Alice", "predicate": "携带", "object": "地图"},
                ],
            },
            {
                "content": f"Bob 在火星港遇见 Alice，并重复口令 {unique_token}。",
                "source": source,
                "entities": ["Bob", "Alice", "火星港"],
                "relations": [
                    {"subject": "Bob", "predicate": "遇见", "object": "Alice"},
                    {"subject": "Bob", "predicate": "位于", "object": "火星港"},
                ],
            },
            {
                "content": "Carol 在翡翠仓库校准星图仪，并把星图仪交给 Alice 复核。",
                "source": source,
                "entities": ["Carol", "翡翠仓库", "星图仪", "Alice"],
                "relations": [
                    {"subject": "Carol", "predicate": "位于", "object": "翡翠仓库"},
                    {"subject": "Carol", "predicate": "校准", "object": "星图仪"},
                    {"subject": "Carol", "predicate": "交给", "object": "Alice"},
                ],
            },
            {
                "content": "Dylan 在土卫六码头转交冰层样本，接收人是 Carol。",
                "source": source,
                "entities": ["Dylan", "土卫六码头", "冰层样本", "Carol"],
                "relations": [
                    {"subject": "Dylan", "predicate": "位于", "object": "土卫六码头"},
                    {"subject": "Dylan", "predicate": "转交", "object": "冰层样本"},
                    {"subject": "Carol", "predicate": "接收", "object": "冰层样本"},
                ],
            },
            {
                "content": "Eve 在月面档案馆审核观测日志，日志提到火星港的地图缺页。",
                "source": source,
                "entities": ["Eve", "月面档案馆", "观测日志", "火星港", "地图"],
                "relations": [
                    {"subject": "Eve", "predicate": "位于", "object": "月面档案馆"},
                    {"subject": "Eve", "predicate": "审核", "object": "观测日志"},
                    {"subject": "观测日志", "predicate": "提到", "object": "火星港"},
                ],
            },
            {
                "content": "Frank 在深海实验室记录潮汐曲线，曲线用于校验星图仪。",
                "source": source,
                "entities": ["Frank", "深海实验室", "潮汐曲线", "星图仪"],
                "relations": [
                    {"subject": "Frank", "predicate": "位于", "object": "深海实验室"},
                    {"subject": "Frank", "predicate": "记录", "object": "潮汐曲线"},
                    {"subject": "潮汐曲线", "predicate": "校验", "object": "星图仪"},
                ],
            },
            {
                "content": "Grace 在北极中继站维修量子罗盘，并向 Dylan 汇报进度。",
                "source": source,
                "entities": ["Grace", "北极中继站", "量子罗盘", "Dylan"],
                "relations": [
                    {"subject": "Grace", "predicate": "位于", "object": "北极中继站"},
                    {"subject": "Grace", "predicate": "维修", "object": "量子罗盘"},
                    {"subject": "Grace", "predicate": "汇报", "object": "Dylan"},
                ],
            },
            {
                "content": "Heidi 在云杉观测塔整理风暴样本，样本标签来自翡翠仓库。",
                "source": source,
                "entities": ["Heidi", "云杉观测塔", "风暴样本", "翡翠仓库"],
                "relations": [
                    {"subject": "Heidi", "predicate": "位于", "object": "云杉观测塔"},
                    {"subject": "Heidi", "predicate": "整理", "object": "风暴样本"},
                    {"subject": "风暴样本", "predicate": "来自", "object": "翡翠仓库"},
                ],
            },
        ]
    }
    response = client.post(
        "/api/webui/memory/import/paste",
        json={
            "name": "integration-seed.json",
            "input_mode": "json",
            "llm_enabled": False,
            "content": json.dumps(seed_payload, ensure_ascii=False),
            "dedupe_policy": "none",
        },
    )
    payload = _assert_response_ok(response)
    task_id = str((payload.get("task") or {}).get("task_id") or "").strip()
    assert task_id, payload
    return task_id


@pytest.fixture(scope="module")
def integration_state(tmp_path_factory: pytest.TempPathFactory) -> Generator[Dict[str, Any], None, None]:
    tmp_root = tmp_path_factory.mktemp("memory_routes_integration")
    data_dir = (tmp_root / "data").resolve()
    staging_dir = (tmp_root / "upload_staging").resolve()
    artifacts_dir = (tmp_root / "artifacts").resolve()
    config_file = (tmp_root / "config" / "bot_config.toml").resolve()
    runtime_config = _build_test_config(data_dir)

    patches = pytest.MonkeyPatch()
    patches.setattr(host_service_module.a_memorix_host_service, "_read_config", lambda: dict(runtime_config))
    patches.setattr(host_service_module.a_memorix_host_service, "get_config_path", lambda: config_file)
    patches.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: _FakeEmbeddingManager(dimension=64),
    )
    patches.setattr(memory_router_module, "STAGING_ROOT", staging_dir)
    patches.setattr(tuning_manager_module, "artifacts_root", lambda: artifacts_dir)

    asyncio.run(host_service_module.a_memorix_host_service.stop())
    host_service_module.a_memorix_host_service._config_cache = None  # type: ignore[attr-defined]

    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: "ok"
    app.include_router(memory_router_module.router, prefix="/api/webui")
    app.include_router(memory_router_module.compat_router)

    unique_token = f"INTEG_TOKEN_{uuid4().hex[:12]}"
    source_name = f"integration-source-{uuid4().hex[:8]}"

    with TestClient(app) as client:
        _wait_for_runtime_ready(client)
        upload_task_id = _create_multitype_upload_task(client)
        upload_task = _wait_for_import_task_terminal(client, upload_task_id)

        seed_task_id = _create_seed_paste_task(client, source=source_name, unique_token=unique_token)
        seed_task = _wait_for_import_task_terminal(client, seed_task_id)
        assert str(seed_task.get("status", "") or "") in {"completed", "completed_with_errors"}, seed_task

        _wait_for_query_hit(client, unique_token, timeout_seconds=45.0)

        yield {
            "client": client,
            "upload_task": upload_task,
            "seed_task": seed_task,
            "source_name": source_name,
            "unique_token": unique_token,
            "tuning_probe_query": "Carol 翡翠仓库 校准 星图仪",
            "tuning_probe_phrase": "Carol 在翡翠仓库校准星图仪",
        }

    asyncio.run(host_service_module.a_memorix_host_service.stop())
    host_service_module.a_memorix_host_service._config_cache = None  # type: ignore[attr-defined]
    patches.undo()


def test_import_module_end_to_end_supports_multitype_upload(integration_state: Dict[str, Any]) -> None:
    upload_task = integration_state["upload_task"]

    assert str(upload_task.get("status", "") or "") in {"completed", "completed_with_errors"}, upload_task
    files = upload_task.get("files") or []
    assert isinstance(files, list)
    assert len(files) >= 4

    file_names = {str(item.get("name", "") or "") for item in files if isinstance(item, dict)}
    assert "integration-notes.txt" in file_names
    assert "integration-diary.md" in file_names
    assert "integration-structured.json" in file_names
    assert "integration-extra.json" in file_names


def test_retrieval_module_end_to_end_queries_seeded_data(integration_state: Dict[str, Any]) -> None:
    client = integration_state["client"]
    unique_token = integration_state["unique_token"]

    aggregate_payload = _wait_for_query_hit(client, unique_token, timeout_seconds=45.0)
    hits = aggregate_payload.get("hits") or []
    joined_content = "\n".join(str(item.get("content", "") or "") for item in hits if isinstance(item, dict))
    assert unique_token in joined_content

    graph_payload = _assert_response_ok(
        client.get(
            "/api/webui/memory/graph/search",
            params={"query": "Alice", "limit": 20},
        )
    )
    graph_items = graph_payload.get("items") or []
    assert isinstance(graph_items, list)
    assert any(str(item.get("type", "") or "") == "entity" for item in graph_items if isinstance(item, dict)), graph_items


def test_tuning_module_end_to_end_create_and_apply_best(integration_state: Dict[str, Any]) -> None:
    client = integration_state["client"]
    probe_query = integration_state["tuning_probe_query"]
    probe_phrase = integration_state["tuning_probe_phrase"]

    create_payload = _assert_response_ok(
        client.post(
            "/api/webui/memory/retrieval_tuning/tasks",
            json={
                "objective": "balanced",
                "intensity": "quick",
                "rounds": 3,
                "sample_size": 8,
                "top_k_eval": 6,
                "llm_enabled": False,
                "eval_query_timeout_seconds": 1.5,
                "seed": 20260403,
            },
        )
    )
    task_id = str((create_payload.get("task") or {}).get("task_id") or "").strip()
    assert task_id, create_payload

    task = _wait_for_tuning_task_terminal(client, task_id)
    assert str(task.get("status", "") or "") == "completed", task
    assert isinstance(task.get("recommended"), bool)

    split_stats = ((task.get("query_set_stats") or {}).get("split") or {})
    assert split_stats.get("strategy") == "category_balanced_holdout", task
    assert int(split_stats.get("train", 0) or 0) > 0, task
    assert int(split_stats.get("holdout", 0) or 0) > 0, task

    validation_summary = task.get("validation_summary") or {}
    assert int(validation_summary.get("holdout_case_count", 0) or 0) > 0, task
    assert isinstance(validation_summary.get("stable"), dict), task
    assert isinstance(validation_summary.get("online_like"), dict), task

    report_payload = _assert_response_ok(
        client.get(
            f"/api/webui/memory/retrieval_tuning/tasks/{task_id}/report",
            params={"format": "json"},
        )
    )
    report = json.loads(str(report_payload.get("content") or "{}"))
    assert report.get("recommended") == task.get("recommended")
    assert isinstance(report.get("profile_diff"), dict), report
    assert "embedding.*" in (report.get("non_tuned_retrieval_influencers") or []), report

    apply_payload = _assert_response_ok(
        client.post(
            f"/api/webui/memory/retrieval_tuning/tasks/{task_id}/apply-best",
            json={"validate": False},
        )
    )
    assert "applied" in apply_payload
    assert apply_payload.get("runtime_rebuilt") is True

    applied_profile = apply_payload.get("applied") or {}
    applied_retrieval = applied_profile.get("retrieval") if isinstance(applied_profile, dict) else {}
    profile_payload = _assert_response_ok(client.get("/api/webui/memory/retrieval_tuning/profile"))
    runtime_profile = profile_payload.get("runtime_profile") or profile_payload.get("profile") or {}
    runtime_retrieval = runtime_profile.get("retrieval") if isinstance(runtime_profile, dict) else {}
    assert runtime_retrieval.get("top_k_final") == applied_retrieval.get("top_k_final")
    assert runtime_retrieval.get("fusion") == applied_retrieval.get("fusion")

    probe_payload = _wait_for_query_hit(client, probe_query, timeout_seconds=45.0)
    probe_hits = probe_payload.get("hits") or []
    joined_probe_content = "\n".join(str(item.get("content", "") or "") for item in probe_hits if isinstance(item, dict))
    assert probe_phrase in joined_probe_content


def test_delete_module_end_to_end_preview_execute_restore(integration_state: Dict[str, Any]) -> None:
    client = integration_state["client"]
    unique_token = integration_state["unique_token"]
    source_name = integration_state["source_name"]

    before_source_item = _wait_for_source_paragraph_count(client, source_name, min_count=1, timeout_seconds=45.0)
    assert _source_paragraph_count(before_source_item) >= 1

    preview_payload = _assert_response_ok(
        client.post(
            "/api/webui/memory/delete/preview",
            json={
                "mode": "source",
                "selector": {"sources": [source_name]},
                "reason": "integration_delete_preview",
                "requested_by": "pytest_integration",
            },
        )
    )
    preview_counts = preview_payload.get("counts") or {}
    assert int(preview_counts.get("paragraphs", 0) or 0) >= 1, preview_payload

    execute_payload = _assert_response_ok(
        client.post(
            "/api/webui/memory/delete/execute",
            json={
                "mode": "source",
                "selector": {"sources": [source_name]},
                "reason": "integration_delete_execute",
                "requested_by": "pytest_integration",
            },
        )
    )
    operation_id = str(execute_payload.get("operation_id", "") or "").strip()
    assert operation_id, execute_payload

    after_delete_payload = _assert_response_ok(
        client.get(
            "/api/webui/memory/query/aggregate",
            params={"query": unique_token, "limit": 20},
        )
    )
    after_delete_hits = after_delete_payload.get("hits") or []
    after_delete_text = "\n".join(
        str(item.get("content", "") or "")
        for item in after_delete_hits
        if isinstance(item, dict)
    )
    assert unique_token not in after_delete_text
    assert int(execute_payload.get("deleted_paragraph_count", 0) or 0) >= 1, execute_payload

    _assert_response_ok(
        client.post(
            "/api/webui/memory/delete/restore",
            json={
                "operation_id": operation_id,
                "requested_by": "pytest_integration",
            },
        )
    )

    restored_source_item = _wait_for_source_paragraph_count(client, source_name, min_count=1, timeout_seconds=45.0)
    assert _source_paragraph_count(restored_source_item) >= 1

    operations_payload = _assert_response_ok(
        client.get(
            "/api/webui/memory/delete/operations",
            params={"limit": 20, "mode": "source"},
        )
    )
    operation_items = operations_payload.get("items") or []
    operation_ids = {
        str(item.get("operation_id", "") or "")
        for item in operation_items
        if isinstance(item, dict)
    }
    assert operation_id in operation_ids

    operation_detail_payload = _assert_response_ok(client.get(f"/api/webui/memory/delete/operations/{operation_id}"))
    detail_operation = operation_detail_payload.get("operation") or {}
    assert str(detail_operation.get("status", "") or "") == "restored"


def test_real_api_business_flow_import_query_graph_delete_restore(integration_state: Dict[str, Any]) -> None:
    client = integration_state["client"]
    flow_id = uuid4().hex[:12]
    source_name = f"business-flow-{flow_id}"
    access_code = f"ORBIT-{flow_id.upper()}"
    person_name = f"林澈-{flow_id[:6]}"
    location_name = f"苍穹观测站-{flow_id[:6]}"
    device_name = f"极光记录仪-{flow_id[:6]}"

    simulated_payload = {
        "paragraphs": [
            {
                "content": (
                    f"{person_name} 在 {location_name} 使用 {device_name} 记录极光，"
                    f"并把任务口令 {access_code} 写入值班日志。"
                ),
                "source": source_name,
                "entities": [person_name, location_name, device_name, access_code],
                "relations": [
                    {"subject": person_name, "predicate": "位于", "object": location_name},
                    {"subject": person_name, "predicate": "使用", "object": device_name},
                    {"subject": device_name, "predicate": "记录", "object": "极光"},
                ],
            },
            {
                "content": (
                    f"次日 {person_name} 将 {device_name} 交给巡检组，"
                    f"巡检确认口令仍为 {access_code}。"
                ),
                "source": source_name,
                "entities": [person_name, device_name, "巡检组", access_code],
                "relations": [
                    {"subject": person_name, "predicate": "交给", "object": "巡检组"},
                    {"subject": "巡检组", "predicate": "确认", "object": access_code},
                ],
            },
        ]
    }

    create_payload = _assert_response_ok(
        client.post(
            "/api/webui/memory/import/paste",
            json={
                "name": f"真实业务流-{flow_id}",
                "input_mode": "json",
                "llm_enabled": False,
                "content": json.dumps(simulated_payload, ensure_ascii=False),
                "dedupe_policy": "none",
            },
        )
    )
    task_id = str((create_payload.get("task") or {}).get("task_id") or "").strip()
    assert task_id, create_payload

    task = _wait_for_import_task_terminal(client, task_id)
    assert str(task.get("status", "") or "") in {"completed", "completed_with_errors"}, task

    source_item = _wait_for_source_paragraph_count(client, source_name, min_count=2, timeout_seconds=45.0)
    assert _source_paragraph_count(source_item) == 2

    relation_query_payload = _wait_for_query_hit(client, access_code, timeout_seconds=45.0)
    relation_hits = [
        item
        for item in (relation_query_payload.get("hits") or [])
        if isinstance(item, dict) and access_code in str(item.get("content", "") or "")
    ]
    assert relation_hits, relation_query_payload

    paragraph_query_payload = _wait_for_query_hit(
        client,
        f"{person_name} {location_name} {device_name}",
        timeout_seconds=45.0,
    )
    paragraph_hits = [
        item
        for item in (paragraph_query_payload.get("hits") or [])
        if isinstance(item, dict)
        and str(item.get("type", "") or "") == "paragraph"
        and person_name in str(item.get("content", "") or "")
    ]
    assert paragraph_hits, paragraph_query_payload

    paragraph_hit = paragraph_hits[0]
    paragraph_hash = str(paragraph_hit.get("hash", "") or "").strip()
    assert paragraph_hash, paragraph_hit

    paragraph_detail = _assert_response_ok(
        client.get(
            "/api/webui/memory/graph/paragraph-detail",
            params={"paragraph_hash": paragraph_hash, "evidence_node_limit": 80},
        )
    )
    paragraph_record = paragraph_detail.get("paragraph") or {}
    assert str(paragraph_record.get("source", "") or "") == source_name, paragraph_detail

    detail_graph = paragraph_detail.get("evidence_graph") or paragraph_detail
    detail_nodes = detail_graph.get("nodes") or []
    detail_node_contents = {
        str(item.get("content", "") or "")
        for item in detail_nodes
        if isinstance(item, dict)
    }
    assert person_name in detail_node_contents
    assert location_name in detail_node_contents
    assert device_name in detail_node_contents

    graph_search = _assert_response_ok(
        client.get(
            "/api/webui/memory/graph/search",
            params={"query": person_name, "limit": 20},
        )
    )
    graph_items = graph_search.get("items") or []
    assert any(
        isinstance(item, dict)
        and str(item.get("matched_value", "") or "") == person_name
        and str(item.get("entity_hash", "") or "").strip()
        for item in graph_items
    ), graph_search

    preview = _assert_response_ok(
        client.post(
            "/api/webui/memory/delete/preview",
            json={
                "mode": "source",
                "selector": {"sources": [source_name]},
                "reason": "business_flow_cleanup_preview",
                "requested_by": "pytest_business_flow",
            },
        )
    )
    assert int((preview.get("counts") or {}).get("paragraphs", 0) or 0) == 2, preview

    deleted = _assert_response_ok(
        client.post(
            "/api/webui/memory/delete/execute",
            json={
                "mode": "source",
                "selector": {"sources": [source_name]},
                "reason": "business_flow_cleanup_execute",
                "requested_by": "pytest_business_flow",
            },
        )
    )
    operation_id = str(deleted.get("operation_id", "") or "").strip()
    assert operation_id, deleted
    assert int(deleted.get("deleted_paragraph_count", 0) or 0) == 2, deleted

    deleted_query = _assert_response_ok(
        client.get(
            "/api/webui/memory/query/aggregate",
            params={"query": access_code, "limit": 20},
        )
    )
    assert all(
        access_code not in str(item.get("content", "") or "")
        for item in (deleted_query.get("hits") or [])
        if isinstance(item, dict)
    )

    _assert_response_ok(
        client.post(
            "/api/webui/memory/delete/restore",
            json={"operation_id": operation_id, "requested_by": "pytest_business_flow"},
        )
    )
    _wait_for_source_paragraph_count(client, source_name, min_count=2, timeout_seconds=45.0)
    restored_query = _wait_for_query_hit(client, access_code, timeout_seconds=45.0)
    assert any(
        access_code in str(item.get("content", "") or "")
        for item in (restored_query.get("hits") or [])
        if isinstance(item, dict)
    )
