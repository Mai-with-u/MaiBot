"""真实 HTTP、跨循环队列、推断引擎与 SQLite 的集成回归；仅模型和提示/Hook 使用替身。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Barrier, Event, Thread
from types import SimpleNamespace
from typing import Any, Awaitable, Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

import asyncio
import json
import time

import pytest

from src.common import runtime_loop
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.common.database.database_model import (
    ChatSession,
    Jargon,
    JargonCreatedBy,
    JargonInferenceJob,
    JargonInferenceState,
    JargonMeaningHistory,
    Messages,
)
from src.common.utils import utils_config
from src.common.utils.utils_message import MessageUtils
from src.config.official_configs import JargonConfig, JargonInferenceConfig
from src.learners import jargon_inference_service, jargon_miner
from src.learners.jargon_inference_result import evidence_hash
from src.llm_models.payload_content.context_item import build_portable_output_items
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult
from src.webui.routers import jargon, jargon_inference


@pytest.fixture
def integrated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """在独立主循环运行真实队列和推断引擎，让 HTTP 路由经生产桥接路径访问同一 SQLite。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'integration.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ChatSession.__table__,
            Messages.__table__,
            Jargon.__table__,
            JargonInferenceState.__table__,
            JargonInferenceJob.__table__,
            JargonMeaningHistory.__table__,
        ],
    )

    @contextmanager
    def db_session(auto_commit: bool = True) -> Iterator[Session]:
        """让 HTTP 请求、队列工作线程和断言分别使用会话，并统一提交或回滚。"""
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                if auto_commit:
                    session.commit()
            except BaseException:
                session.rollback()
                raise

    config = SimpleNamespace(
        bot=SimpleNamespace(nickname="麦麦"),
        jargon=JargonConfig(
            inference=JargonInferenceConfig(
                max_retries=0,
                max_concurrency=1,
                request_interval_seconds=0,
                scheduled_enabled=False,
            ),
            jargon_groups=[],
        ),
    )
    calls = []
    started = Event()
    release = asyncio.Event()
    controls = SimpleNamespace(pause=False, fail=False, is_similar=False)

    class Prompt:
        def __init__(self, name: str):
            self.name = name
            self.context: dict[str, Any] = {}

        def add_context(self, name: str, value: Any) -> None:
            self.context[name] = value

    async def render(template: Prompt) -> str:
        return json.dumps({"name": template.name, "context": template.context}, ensure_ascii=False)

    async def generate(prompt: str, *, session_id: str) -> LLMResponseResult:
        """按请求阶段返回固定模型结果，允许测试暂停请求或模拟服务失败。"""
        parsed = json.loads(prompt)
        calls.append((session_id, parsed))
        if controls.pause:
            started.set()
            await release.wait()
        if controls.fail:
            raise RuntimeError("模拟模型服务暂时不可用")
        outputs = {
            "jargon_inference_with_context": {"meaning": "任务完成后的庆祝", "no_info": False},
            "jargon_inference_content_only": {"meaning": "飞机离地"},
            "jargon_compare_inference": {"is_similar": controls.is_similar},
        }
        return LLMResponseResult(
            output_items=build_portable_output_items(
                content=json.dumps(outputs[parsed["name"]], ensure_ascii=False),
                logical_turn_id=f"test-{len(calls)}",
            ),
            model_name="test-model",
        )

    async def invoke_hook(name: str, **kwargs: Any) -> HookDispatchResult:
        return HookDispatchResult(hook_name=name, kwargs=kwargs)

    for module in (jargon_miner, jargon_inference_service, jargon, jargon_inference):
        monkeypatch.setattr(module, "get_db_session", db_session)
        monkeypatch.setattr(module, "global_config", config)
    monkeypatch.setattr(utils_config, "global_config", config)
    monkeypatch.setattr(jargon_miner, "llm_inference", SimpleNamespace(generate_response=generate))
    monkeypatch.setattr(jargon_miner, "prompt_manager", SimpleNamespace(get_prompt=Prompt, render_prompt=render))
    monkeypatch.setattr(
        jargon_miner.JargonMiner, "_get_runtime_manager", staticmethod(lambda: SimpleNamespace(invoke_hook=invoke_hook))
    )
    monkeypatch.setattr(
        jargon_miner.PromptCLIVisualizer,
        "build_prompt_preview_access",
        lambda *_, **__: SimpleNamespace(
            preview_web_uri="test-preview",
            reasoning_web_uri="test-reasoning",
            record_path=tmp_path / "prompt.json",
        ),
    )
    monkeypatch.setattr(jargon_inference, "_settings_lock", asyncio.Lock())

    evidence = json.dumps([[{"platform": "qq", "message_id": "m1"}]])
    with db_session() as session:
        session.add(ChatSession(session_id="chat-a", platform="qq", group_id="100", group_name="测试群", user_id=None))
        session.add(
            Messages(
                message_id="m1",
                timestamp=datetime.now(),
                platform="qq",
                session_id="chat-a",
                user_id="member",
                user_nickname="成员",
                processed_plain_text="任务做完了，起飞！",
                raw_content=MessageUtils.from_MaiSeq_to_db_record_msg(
                    MessageSequence([TextComponent("任务做完了，起飞！")])
                ),
            )
        )
        session.add(
            Jargon(
                id=1,
                content="起飞",
                meaning="",
                count=1,
                session_id_dict='{"chat-a":1}',
                evidence_messages=evidence,
            )
        )

    loop = asyncio.new_event_loop()
    thread = Thread(target=loop.run_forever, name="test-maibot-main-loop", daemon=True)
    thread.start()
    previous_loop = runtime_loop.get_main_loop()
    runtime_loop.set_main_loop(loop)
    service = jargon_inference_service.JargonInferenceService()
    monkeypatch.setattr(jargon_inference_service, "_service", service)

    def run(operation: Awaitable[Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(operation, loop).result(timeout=10)

    run(service.start())
    app = FastAPI()
    app.include_router(jargon.router, prefix="/api/webui")
    harness = SimpleNamespace(
        service=service,
        run=run,
        config=config,
        session=db_session,
        calls=calls,
        controls=controls,
        started=started,
        release=lambda: loop.call_soon_threadsafe(release.set),
        loop=loop,
        evidence=evidence,
    )
    try:
        with TestClient(app) as client:
            harness.client = client
            yield harness
    finally:
        run(harness.service.stop())
        run(loop.shutdown_asyncgens())
        runtime_loop.set_main_loop(previous_loop)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        assert not thread.is_alive()
        loop.close()
        engine.dispose()


def wait_job(runtime: Any, job_id: str) -> dict[str, Any]:
    """轮询任务进入终态后，再等待队列释放预留，确保 SQLite 写回已结束。"""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = runtime.client.get(f"/api/webui/jargon/inference/jobs/{job_id}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["job"]["status"] not in {"queued", "running", "cancelling"}:
            # 完成状态写入也完成后再读取 SQLite/重启服务。
            async def settled() -> None:
                """等待主循环释放正在持久化的任务项预留。"""
                while runtime.service._reserved:
                    await asyncio.sleep(0.01)

            runtime.run(settled())
            return result
        time.sleep(0.01)
    pytest.fail(f"任务 {job_id} 未在测试期限内完成")


def test_http_job_source_pagination_keeps_older_manual_tasks_reachable(integrated_runtime):
    """来源过滤先于分页，较多的自动任务不能挤掉旧的手动记录。"""
    runtime = integrated_runtime
    now = time.time()
    with runtime.session() as session:
        for index in range(55):
            session.add(
                JargonInferenceJob(
                    id=f"automatic-{index}",
                    source="automatic",
                    status="queued" if index == 0 else "completed",
                    created_at=now + index,
                    updated_at=now,
                    items_json="[]",
                )
            )
        for index in range(3):
            session.add(
                JargonInferenceJob(
                    id=f"manual-{index}",
                    source="manual",
                    status="completed",
                    created_at=now - 100 + index,
                    updated_at=now,
                    items_json="[]",
                )
            )
    response = runtime.client.get("/api/webui/jargon/inference/jobs?source=manual&page=2&page_size=2")
    assert response.status_code == 200, response.text
    page = response.json()
    assert (page["total"], page["page"], page["page_size"]) == (3, 2, 2)
    assert [job["id"] for job in page["items"]] == ["manual-0"]
    active = runtime.client.get("/api/webui/jargon/inference/jobs?active_only=true").json()
    assert active["total"] == 1
    assert [job["id"] for job in active["items"]] == ["automatic-0"]


@pytest.mark.parametrize(("recover_context", "is_jargon"), [(False, True), (True, False)])
def test_http_to_real_queue_engine_and_durable_history(integrated_runtime, monkeypatch, recover_context, is_jargon):
    """详情返回模型实际分类，重启后保留历史结论而非词条最新分类。"""
    runtime = integrated_runtime
    runtime.controls.is_similar = not is_jargon
    if recover_context:
        with runtime.session() as session:
            row = session.get(Jargon, 1)
            row.evidence_messages = None
            session.add(row)
    response = runtime.client.post(
        "/api/webui/jargon/inference/jobs", json={"ids": [1], "mode": "missing", "force": True}
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    finished = wait_job(runtime, job_id)
    assert finished["job"]["counts"] == {"succeeded": 1}, finished
    assert finished["items"][0]["is_jargon"] is is_jargon
    assert finished["items"][0]["requests"] == 3
    assert finished["items"][0]["session_name"] == "测试群"
    assert len(runtime.calls) == 3
    assert "任务做完了，起飞！" in runtime.calls[0][1]["context"]["raw_content_list"]
    with runtime.session() as session:
        record = session.get(Jargon, 1)
        assert (record.meaning, record.count, record.last_inference_count) == ("任务完成后的庆祝", 1, 1)
        assert record.evidence_messages
        state = session.get(JargonInferenceState, 1)
        assert state.status == "succeeded" and state.last_source == "manual"
        assert state.last_attempt_at and state.last_success_at
        assert state.last_attempt_evidence_hash == evidence_hash(record.evidence_messages)
        history = session.exec(select(JargonMeaningHistory)).one()
        assert record.is_jargon is history.is_jargon is is_jargon
        assert (history.job_id, history.source, history.previous_meaning, history.meaning) == (
            job_id,
            "manual",
            "",
            record.meaning,
        )
        record.is_jargon = not is_jargon
        session.add(record)
        saved = session.get(JargonInferenceJob, job_id)
        legacy_items = json.loads(saved.items_json)
        legacy_items[0].pop("is_jargon")
        session.add(JargonInferenceJob(id="legacy", status="completed", items_json=json.dumps(legacy_items)))

    runtime.run(runtime.service.stop())
    replacement = jargon_inference_service.JargonInferenceService()
    monkeypatch.setattr(jargon_inference_service, "_service", replacement)
    runtime.service = replacement
    runtime.run(replacement.start())
    restored = runtime.client.get(f"/api/webui/jargon/inference/jobs/{job_id}").json()
    assert restored["job"]["counts"] == {"succeeded": 1}
    assert restored["items"][0]["is_jargon"] is is_jargon
    legacy = runtime.client.get("/api/webui/jargon/inference/jobs/legacy").json()
    assert legacy["items"][0]["is_jargon"] is None
    assert len(runtime.calls) == 3  # 服务恢复只恢复记录，不重放已完成的模型请求。


def test_stop_restart_and_http_retry_preserve_original_evidence(integrated_runtime, monkeypatch):
    """中断时保留原证据和历史一致性，重启后通过 HTTP 重试能复用该证据。"""
    runtime = integrated_runtime
    runtime.controls.pause = True
    submitted = runtime.client.post("/api/webui/jargon/inference/jobs", json={"ids": [1]}).json()
    assert runtime.started.wait(5)
    runtime.run(runtime.service.stop())
    with runtime.session() as session:
        record = session.get(Jargon, 1)
        assert record.meaning == "" and record.evidence_messages
        assert session.get(JargonInferenceJob, submitted["id"]).status == "interrupted"
        assert session.get(JargonInferenceState, 1).status == "interrupted"
        assert session.exec(select(JargonMeaningHistory)).all() == []
    runtime.controls.pause = False
    replacement = jargon_inference_service.JargonInferenceService()
    monkeypatch.setattr(jargon_inference_service, "_service", replacement)
    runtime.service = replacement
    runtime.run(replacement.start())
    retried = runtime.client.post(f"/api/webui/jargon/inference/jobs/{submitted['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] != submitted["id"]
    result = wait_job(runtime, retried.json()["id"])
    assert result["job"]["counts"] == {"succeeded": 1}, result
    assert len(runtime.calls) == 4  # 已中断的一次请求，加重试后的三个推断阶段。


def test_config_rescan_deduplicates_with_inflight_http_request(integrated_runtime):
    runtime = integrated_runtime
    runtime.controls.pause = True
    original = runtime.client.post("/api/webui/jargon/inference/jobs", json={"ids": [1]}).json()
    assert runtime.started.wait(5)
    runtime.config.jargon.inference.thresholds = [1, 4]
    runtime.run(runtime.service.on_config_changed())
    duplicate = runtime.client.post("/api/webui/jargon/inference/jobs", json={"ids": [1]}).json()
    assert duplicate["counts"] == {"skipped": 1}
    runtime.release()
    assert wait_job(runtime, original["id"])["job"]["counts"] == {"succeeded": 1}
    assert len(runtime.calls) == 3
    with runtime.session() as session:
        assert len(session.exec(select(JargonMeaningHistory)).all()) == 1


def test_http_manual_edit_wins_over_running_model(integrated_runtime):
    runtime = integrated_runtime
    runtime.controls.pause = True
    original = runtime.client.post("/api/webui/jargon/inference/jobs", json={"ids": [1]}).json()
    assert runtime.started.wait(5)
    edited = runtime.client.patch("/api/webui/jargon/1", json={"meaning": "用户确认的含义", "is_jargon": True})
    assert edited.status_code == 200
    runtime.release()
    result = wait_job(runtime, original["id"])
    assert result["job"]["counts"] == {"protected": 1}
    assert result["items"][0]["is_jargon"] is None
    with runtime.session() as session:
        record = session.get(Jargon, 1)
        assert (record.meaning, record.created_by, record.count) == ("用户确认的含义", JargonCreatedBy.MANUAL, 1)
        assert session.exec(select(JargonMeaningHistory)).all() == []


@pytest.mark.parametrize("operation", ["lock", "restore"])
def test_concurrent_manual_controls_initialize_single_state(integrated_runtime, operation):
    """并发锁定或回退共用一条推断状态，成功回退各自留下历史。"""
    runtime = integrated_runtime
    with runtime.session() as session:
        session.add(JargonMeaningHistory(id=1, jargon_id=1, previous_meaning="人工旧含义", meaning="当前含义"))
        assert session.get(JargonInferenceState, 1) is None
    ready = Barrier(4)

    def request():
        """等所有请求线程到达屏障，再调用相同的锁定或回退入口。"""
        ready.wait(timeout=5)
        if operation == "restore":
            return runtime.client.post("/api/webui/jargon/inference/history/1/restore")
        return runtime.client.post("/api/webui/jargon/inference/lock/1", json={"locked": True})

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = [future.result(timeout=10) for future in [pool.submit(request) for _ in range(4)]]
    assert [(response.status_code, response.json()) for response in responses] == [(200, {"success": True})] * 4
    with runtime.session() as session:
        assert session.exec(select(JargonInferenceState)).one().locked
        if operation == "restore":
            assert session.get(Jargon, 1).meaning == "人工旧含义"
            assert len(session.exec(select(JargonMeaningHistory)).all()) == 5
