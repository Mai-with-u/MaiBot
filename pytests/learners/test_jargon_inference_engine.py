"""真实 JargonMiner 方法在隔离 SQLite 中的推断、证据和竞争写回回归。"""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, col, create_engine, delete, select

import ast
import asyncio
import json
import logging
import sys
import time
import types
import typing

import pytest

from src.common.database.database_model import (
    Jargon,
    JargonCreatedBy,
    JargonInferenceState,
    JargonMeaningHistory,
    Messages,
)
from src.learners.jargon_inference_result import InferenceResult, evidence_hash

ROOT = Path(__file__).resolve().parents[2]


def load_class(path: Path, name: str, namespace: dict[str, Any]) -> type:
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    body.extend(item for item in parsed.body if isinstance(item, ast.ClassDef) and item.name == name)
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


@pytest.fixture
def engine_runtime(tmp_path: Path) -> Iterator[SimpleNamespace]:
    engine = create_engine(f"sqlite:///{tmp_path / 'jargon.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Jargon.__table__,
            JargonInferenceState.__table__,
            JargonMeaningHistory.__table__,
            Messages.__table__,
        ],
    )

    @contextmanager
    def db_session(auto_commit: bool = True) -> Iterator[Session]:
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
        jargon=SimpleNamespace(
            inference=SimpleNamespace(
                thresholds=[4, 8, 25, 100],
                stop_after_last_threshold=True,
                evidence_limit=50,
                history_limit=20,
            )
        ),
    )
    responses: list[Any] = [
        {"meaning": "完成任务的庆祝", "no_info": False},
        {"meaning": "飞机离地"},
        {"is_similar": False},
    ]
    calls: list[str] = []
    rate_calls: list[int] = []
    runtime = SimpleNamespace(before_response=None, abort_hook=False)

    async def generate(prompt: str, session_id: str) -> SimpleNamespace:
        calls.append(prompt)
        if runtime.before_response:
            runtime.before_response(len(calls))
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(response=json.dumps(result, ensure_ascii=False) if result is not None else "")

    async def rate_limit() -> None:
        rate_calls.append(len(calls))

    async def hook(name: str, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(aborted=runtime.abort_hook, kwargs=kwargs)

    async def render(template: Any) -> str:
        return template.name

    class Prompt:
        def __init__(self, name: str):
            self.name = name

        def add_context(self, *args: Any) -> None:
            pass

    namespace = {
        **vars(typing),
        "__name__": "src.learners.jargon_miner",
        "__package__": "src.learners",
        "OrderedDict": OrderedDict,
        "datetime": datetime,
        "asyncio": asyncio,
        "json": json,
        "time": time,
        "col": col,
        "select": select,
        "delete": delete,
        "text": text,
        "get_db_session": db_session,
        "global_config": config,
        "logger": logging.getLogger("jargon_test"),
        "Jargon": Jargon,
        "JargonCreatedBy": JargonCreatedBy,
        "Messages": Messages,
        "JargonInferenceState": JargonInferenceState,
        "JargonMeaningHistory": JargonMeaningHistory,
        "InferenceResult": InferenceResult,
        "replace": replace,
        "evidence_hash": evidence_hash,
        "HookAbortedError": lambda result: "aborted",
        "is_single_char_jargon": lambda value: len(value) <= 1,
        "JargonConfigUtils": SimpleNamespace(resolve_jargon_group_scope=lambda session_id: ({session_id}, None)),
        "MessageUtils": SimpleNamespace(from_db_record_msg_to_MaiSeq=lambda raw: raw.decode()),
        "extract_learning_text": lambda value: value,
        "parse_llm_json": lambda value, repair=False: json.loads(value),
        "llm_inference": SimpleNamespace(generate_response=generate),
        "prompt_manager": SimpleNamespace(get_prompt=Prompt, render_prompt=render),
    }
    from src.common.data_models import BaseDatabaseDataModel

    namespace["BaseDatabaseDataModel"] = BaseDatabaseDataModel
    mai_class = load_class(ROOT / "src/common/data_models/jargon_data_model.py", "MaiJargon", namespace)
    miner_class = load_class(ROOT / "src/learners/jargon_miner.py", "JargonMiner", namespace)
    miner = miner_class("chat-a", "测试群")
    miner._get_runtime_manager = lambda: SimpleNamespace(invoke_hook=hook)
    miner._log_inference_prompt_preview = lambda **kwargs: None
    evidence = json.dumps([[{"platform": "qq", "message_id": "m1"}]], ensure_ascii=False)
    with db_session() as session:
        session.add(
            Messages(
                message_id="m1",
                timestamp=datetime.now(),
                platform="qq",
                session_id="chat-a",
                user_id="test",
                user_nickname="test",
                raw_content="任务做完了，起飞！".encode(),
                processed_plain_text="任务做完了，起飞！",
            )
        )
        session.add(
            Jargon(
                id=1,
                content="起飞",
                meaning="旧含义",
                count=4,
                session_id_dict='{"chat-a": 4}',
                evidence_messages=evidence,
            )
        )

    runtime.engine = engine
    runtime.db_session = db_session
    runtime.config = config
    runtime.responses = responses
    runtime.calls = calls
    runtime.rate_calls = rate_calls
    runtime.rate_limit = rate_limit
    runtime.miner = miner
    runtime.mai_class = mai_class
    runtime.namespace = namespace
    runtime.evidence = evidence
    yield runtime
    engine.dispose()


def run_inference(runtime: SimpleNamespace) -> InferenceResult:
    return asyncio.run(
        runtime.miner._infer_meaning_by_id(
            1,
            before_request=runtime.rate_limit,
            source="manual",
            job_id="job-1",
        )
    )


def get_record(runtime: SimpleNamespace) -> Jargon:
    with runtime.db_session() as session:
        record = session.get(Jargon, 1)
        assert record is not None
        return record


def test_success_retains_evidence_records_history_and_counts_actual_requests(engine_runtime: SimpleNamespace) -> None:
    result = run_inference(engine_runtime)
    assert (result.status, result.requests, result.is_jargon) == ("succeeded", 3, True)
    assert len(engine_runtime.rate_calls) == 3
    record = get_record(engine_runtime)
    assert (record.meaning, record.count, record.last_inference_count) == ("完成任务的庆祝", 4, 4)
    assert json.loads(record.evidence_messages) == json.loads(engine_runtime.evidence)
    with engine_runtime.db_session() as session:
        history = session.exec(select(JargonMeaningHistory)).one()
        assert (history.previous_meaning, history.meaning, history.source, history.job_id) == (
            "旧含义",
            "完成任务的庆祝",
            "manual",
            "job-1",
        )


@pytest.mark.parametrize("stage", [0, 1, 2])
@pytest.mark.parametrize("failure", [None, ValueError("provider unavailable")])
def test_model_failures_preserve_old_meaning_and_evidence(
    engine_runtime: SimpleNamespace,
    stage: int,
    failure: Any,
) -> None:
    engine_runtime.responses[stage] = failure
    result = run_inference(engine_runtime)
    assert (result.status, result.requests) == ("failed", stage + 1)
    record = get_record(engine_runtime)
    assert (record.meaning, record.last_inference_count) == ("旧含义", 0)
    assert json.loads(record.evidence_messages) == json.loads(engine_runtime.evidence)
    with engine_runtime.db_session() as session:
        assert session.exec(select(JargonMeaningHistory)).all() == []


@pytest.mark.parametrize("payload", [{}, {"no_info": "false"}, {"meaning": 123}, {"meaning": ""}])
def test_invalid_context_response_is_failure_not_insufficient_info(
    engine_runtime: SimpleNamespace, payload: Any
) -> None:
    engine_runtime.responses[0] = payload
    result = run_inference(engine_runtime)
    assert result.status == "failed"
    assert get_record(engine_runtime).last_inference_count == 0


def test_insufficient_info_retains_meaning_and_reusable_evidence(engine_runtime: SimpleNamespace) -> None:
    engine_runtime.responses[0] = {"no_info": True}
    result = run_inference(engine_runtime)
    assert (result.status, result.requests) == ("insufficient_info", 1)
    record = get_record(engine_runtime)
    assert (record.meaning, record.last_inference_count) == ("旧含义", 4)
    assert record.evidence_messages


@pytest.mark.parametrize("protection", ["manual", "locked"])
def test_initial_manual_and_locked_records_do_not_call_model(engine_runtime: SimpleNamespace, protection: str) -> None:
    with engine_runtime.db_session() as session:
        if protection == "manual":
            record = session.get(Jargon, 1)
            record.created_by = JargonCreatedBy.MANUAL
            session.add(record)
        else:
            session.add(JargonInferenceState(jargon_id=1, locked=True))
    assert run_inference(engine_runtime).status == "protected"
    assert engine_runtime.calls == []


@pytest.mark.parametrize("mutation", ["manual", "locked", "meaning", "replacement"])
def test_inflight_user_changes_are_never_overwritten(engine_runtime: SimpleNamespace, mutation: str) -> None:
    def modify(stage: int) -> None:
        if stage != 1:
            return
        with engine_runtime.db_session() as session:
            record = session.get(Jargon, 1)
            if mutation == "manual":
                record.created_by = JargonCreatedBy.MANUAL
                record.meaning = "人工解释"
            elif mutation == "locked":
                session.add(JargonInferenceState(jargon_id=1, locked=True))
            elif mutation == "meaning":
                record.meaning = "较新解释"
            else:
                record.created_timestamp += timedelta(seconds=1)
            session.add(record)

    engine_runtime.before_response = modify
    result = run_inference(engine_runtime)
    assert result.status in {"protected", "stale"}
    assert get_record(engine_runtime).meaning != "完成任务的庆祝"
    with engine_runtime.db_session() as session:
        assert session.exec(select(JargonMeaningHistory)).all() == []


def test_concurrent_candidate_preserves_evidence_and_does_not_invalidate_meaning(
    engine_runtime: SimpleNamespace,
) -> None:
    def modify(stage: int) -> None:
        if stage != 1:
            return
        engine_runtime.miner._update_jargon(get_record(engine_runtime), [[{"platform": "qq", "message_id": "m2"}]])

    engine_runtime.before_response = modify
    result = run_inference(engine_runtime)
    assert result.status == "succeeded"
    record = get_record(engine_runtime)
    assert record.count == 5
    assert result.evidence_hash == evidence_hash(engine_runtime.evidence)
    assert result.evidence_hash != evidence_hash(record.evidence_messages)
    assert {group[0]["message_id"] for group in json.loads(record.evidence_messages)} == {"m1", "m2"}


def test_hook_abort_preserves_evidence(engine_runtime: SimpleNamespace) -> None:
    engine_runtime.abort_hook = True
    assert run_inference(engine_runtime).status == "protected"
    assert get_record(engine_runtime).evidence_messages


def test_history_retention_is_bounded(engine_runtime: SimpleNamespace) -> None:
    engine_runtime.config.jargon.inference.history_limit = 2
    with engine_runtime.db_session() as session:
        for i in range(5):
            session.add(JargonMeaningHistory(jargon_id=1, previous_meaning=str(i), meaning=str(i + 1)))
    assert run_inference(engine_runtime).status == "succeeded"
    with engine_runtime.db_session() as session:
        assert len(session.exec(select(JargonMeaningHistory)).all()) == 2


def test_lost_references_recover_only_original_session_evidence(engine_runtime: SimpleNamespace) -> None:
    with engine_runtime.db_session() as session:
        record = session.get(Jargon, 1)
        record.evidence_messages = None
        session.add(record)
        session.add(
            Messages(
                message_id="other",
                timestamp=datetime.now(),
                platform="qq",
                session_id="unrelated",
                user_id="test",
                user_nickname="test",
                raw_content="起飞是另一群的私有含义".encode(),
                processed_plain_text="起飞是另一群的私有含义",
            )
        )
    result = run_inference(engine_runtime)
    assert result.status == "succeeded"
    assert result.evidence_hash == evidence_hash(get_record(engine_runtime).evidence_messages)
    assert result.evidence_hash != evidence_hash(None)
    groups = json.loads(get_record(engine_runtime).evidence_messages)
    assert {ref["message_id"] for group in groups for ref in group} == {"m1"}


def test_missing_evidence_skips_model(engine_runtime: SimpleNamespace) -> None:
    with engine_runtime.db_session() as session:
        session.exec(delete(Messages))
    assert run_inference(engine_runtime).status == "missing_evidence"
    assert engine_runtime.calls == []
    assert get_record(engine_runtime).meaning == "旧含义"


@pytest.mark.parametrize(
    ("count", "last", "stop", "expected"),
    [
        (1, 0, True, False),
        (4, 0, True, True),
        (6, 4, True, False),
        (9, 4, True, True),
        (100, 100, True, False),
        (200, 100, True, False),
        (199, 100, False, False),
        (200, 100, False, True),
    ],
)
def test_threshold_decisions(
    engine_runtime: SimpleNamespace, count: int, last: int, stop: bool, expected: bool
) -> None:
    engine_runtime.config.jargon.inference.stop_after_last_threshold = stop
    record = get_record(engine_runtime)
    record.count, record.last_inference_count = count, last
    record.is_complete = True
    assert engine_runtime.miner._should_infer_meaning(record) is expected


def test_new_entry_threshold_one_uses_shared_queue(
    engine_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine_runtime.config.jargon.inference.thresholds = [1, 5]
    queued: list[int] = []

    async def enqueue(jargon_id: int, *args: Any) -> bool:
        queued.append(jargon_id)
        return True

    fake_service = types.ModuleType("src.learners.jargon_inference_service")
    fake_service.get_jargon_inference_service = lambda: SimpleNamespace(enqueue_auto=enqueue)
    monkeypatch.setitem(sys.modules, fake_service.__name__, fake_service)
    result = asyncio.run(
        engine_runtime.miner.process_extracted_entries(
            [
                {
                    "content": "开香槟",
                    "raw_content": set(),
                    "evidence_messages": [[{"platform": "qq", "message_id": "m1"}]],
                },
            ]
        )
    )
    assert result == (1, 0)
    assert len(queued) == 1
    with engine_runtime.db_session() as session:
        assert session.get(Jargon, queued[0]).count == 1


def test_evidence_limit_keeps_newest_groups(engine_runtime: SimpleNamespace) -> None:
    engine_runtime.config.jargon.inference.evidence_limit = 2
    groups = [[{"platform": "qq", "message_id": str(i)}] for i in range(5)]
    assert engine_runtime.miner._merge_evidence_message_groups(groups[:3], groups[2:]) == groups[-2:]


def test_commit_failure_rolls_back_meaning_and_history(
    engine_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Session.commit

    def fail_on_history(session: Session) -> None:
        if session.exec(select(JargonMeaningHistory)).first() is not None:
            raise RuntimeError("simulated commit failure")
        original(session)

    monkeypatch.setattr(Session, "commit", fail_on_history)
    result = run_inference(engine_runtime)
    assert result.status == "failed"
    assert get_record(engine_runtime).meaning == "旧含义"
    with engine_runtime.db_session() as session:
        assert session.exec(select(JargonMeaningHistory)).all() == []


@pytest.mark.parametrize("payload", [{}, {"meaning": ""}, {"meaning": False}])
def test_invalid_content_only_result_is_rejected(engine_runtime: SimpleNamespace, payload: Any) -> None:
    engine_runtime.responses[1] = payload
    result = run_inference(engine_runtime)
    assert (result.status, result.requests) == ("failed", 2)
    assert get_record(engine_runtime).last_inference_count == 0


def test_parallel_candidate_updates_preserve_all_counts_and_evidence(engine_runtime: SimpleNamespace) -> None:
    def update(index: int) -> None:
        stale_record = get_record(engine_runtime)
        engine_runtime.miner._update_jargon(stale_record, [[{"platform": "qq", "message_id": f"concurrent-{index}"}]])

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(update, range(6)))
    record = get_record(engine_runtime)
    assert record.count == 10
    assert json.loads(record.session_id_dict) == {"chat-a": 10}
    assert len(json.loads(record.evidence_messages)) == 7


def test_parallel_new_candidate_upsert_creates_one_record(engine_runtime: SimpleNamespace) -> None:
    def upsert(index: int) -> None:
        engine_runtime.miner._persist_extracted_entry(
            {
                "content": "开香槟",
                "raw_content": set(),
                "evidence_messages": [[{"platform": "qq", "message_id": f"upsert-{index}"}]],
            }
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(upsert, range(6)))
    with engine_runtime.db_session() as session:
        record = session.exec(select(Jargon).where(Jargon.content == "开香槟")).one()
        assert record.count == 6
        assert len(json.loads(record.evidence_messages)) == 6
