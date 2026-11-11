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
from src.common.utils.utils_jargon import parse_session_id_dict

ROOT = Path(__file__).resolve().parents[2]


def load_class(path: Path, name: str, namespace: dict[str, Any]) -> type:
    """从真实源码编译指定类，使用测试注入的依赖，不执行模块初始化。"""
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    body.extend(item for item in parsed.body if isinstance(item, ast.ClassDef) and item.name == name)
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


@pytest.fixture
def engine_runtime(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """在文件 SQLite 上运行真实推断引擎，按阶段注入模型输出和写回竞争。"""
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
        """使各推断阶段使用独立会话，并按调用方选项提交或回滚。"""
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
            learning_blacklist=[],
            inference=SimpleNamespace(
                thresholds=[4, 8, 25, 100],
                stop_after_last_threshold=True,
                evidence_limit=50,
                history_limit=20,
            ),
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
        """消费下一条模型输出或异常，并在返回前触发预设的并发操作。"""
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
        "parse_session_id_dict": parse_session_id_dict,
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
    """保护检查必须先于任何模型请求；人工来源和显式锁定分别验证。"""
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
    """推断开始后改变词条身份、人工内容或保护状态时，旧结果不得写入历史。"""

    def modify(stage: int) -> None:
        """在首次模型响应前提交用户操作，使其发生在读取快照与写回之间。"""
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
    """仅追加候选证据时允许写回，且本次证据哈希只涵盖已使用的消息。"""

    def modify(stage: int) -> None:
        """在首次响应前通过真实合并路径追加一条证据。"""
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
    """新推断历史计入容量上限，淘汰时保留最新的变更记录。"""
    engine_runtime.config.jargon.inference.history_limit = 2
    with engine_runtime.db_session() as session:
        for i in range(5):
            session.add(JargonMeaningHistory(jargon_id=1, previous_meaning=str(i), meaning=str(i + 1)))
    assert run_inference(engine_runtime).status == "succeeded"
    with engine_runtime.db_session() as session:
        retained = session.exec(select(JargonMeaningHistory).order_by(JargonMeaningHistory.id)).all()
        assert [(row.previous_meaning, row.meaning) for row in retained] == [("4", "5"), ("旧含义", "完成任务的庆祝")]


def test_lost_references_recover_only_original_session_evidence(engine_runtime: SimpleNamespace) -> None:
    """引用缺失时从归属会话恢复文字证据，即使其他群也提到了该词条。"""
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
    """合并重叠消息组时先去重，再按配置保留最近的组。"""
    engine_runtime.config.jargon.inference.evidence_limit = 2
    groups = [[{"platform": "qq", "message_id": str(i)}] for i in range(5)]
    assert engine_runtime.miner._merge_evidence_message_groups(groups[:3], groups[2:]) == groups[-2:]


def test_commit_failure_rolls_back_meaning_and_history(
    engine_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提交历史失败时，含义变更必须与历史一起回滚。"""
    original = Session.commit

    def fail_on_history(session: Session) -> None:
        """仅在事务包含待提交历史时注入错误，允许后续查询正常执行。"""
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


@pytest.mark.parametrize("stored_scope", ["{坏数据", "null", "[]"])
def test_damaged_local_candidate_does_not_drop_valid_match(engine_runtime: SimpleNamespace, stored_scope: str) -> None:
    """同名坏归属不参与匹配，也不能阻止更新另一个正常所属会话的记录。"""
    with engine_runtime.db_session() as session:
        damaged = session.get(Jargon, 1)
        damaged.session_id_dict = stored_scope
        session.add(damaged)
        session.add(Jargon(id=2, content="起飞", meaning="有效记录", count=2, session_id_dict='{"chat-a":2}'))
    outcome, matched = engine_runtime.miner._persist_extracted_entry(
        {"content": "起飞", "raw_content": set(), "evidence_messages": [[{"platform": "qq", "message_id": "m2"}]]}
    )
    assert (outcome, matched.id) == ("updated", 2)
    with engine_runtime.db_session() as session:
        assert session.get(Jargon, 1).session_id_dict == stored_scope
        assert session.get(Jargon, 1).count == 4
        assert session.get(Jargon, 2).count == 3
        assert json.loads(session.get(Jargon, 2).session_id_dict) == {"chat-a": 3}


@pytest.mark.parametrize("stored_scope", ["{坏数据", "null", "[]"])
def test_damaged_local_candidate_does_not_abort_extraction_batch(
    engine_runtime: SimpleNamespace, stored_scope: str
) -> None:
    """坏数据保持原归属；新候选仍可在当前会话创建，并继续保存后续词条。"""
    engine_runtime.config.jargon.inference.thresholds = [100]
    with engine_runtime.db_session() as session:
        damaged = session.get(Jargon, 1)
        damaged.session_id_dict = stored_scope
        session.add(damaged)
    result = asyncio.run(
        engine_runtime.miner.process_extracted_entries(
            [{"content": word, "raw_content": set(), "evidence_messages": []} for word in ("起飞", "另一词")]
        )
    )
    assert result == (2, 0)
    with engine_runtime.db_session() as session:
        assert session.get(Jargon, 1).session_id_dict == stored_scope
        assert session.get(Jargon, 1).count == 4
        created = session.exec(select(Jargon).where(Jargon.id != 1)).all()
        assert {record.content for record in created} == {"起飞", "另一词"}
        assert all(json.loads(record.session_id_dict) == {"chat-a": 1} for record in created)


@pytest.mark.parametrize("stored_scope", ["{坏数据", "null", "[]"])
def test_global_candidate_preserves_damaged_scope_while_merging_evidence(
    engine_runtime: SimpleNamespace, stored_scope: str
) -> None:
    """全局词条可继续累计命中和证据，但不得把未知的原始归属改写为空对象。"""
    with engine_runtime.db_session() as session:
        record = session.get(Jargon, 1)
        record.is_global = True
        record.session_id_dict = stored_scope
        session.add(record)
    outcome, matched = engine_runtime.miner._persist_extracted_entry(
        {"content": "起飞", "raw_content": set(), "evidence_messages": [[{"platform": "qq", "message_id": "m2"}]]}
    )
    assert (outcome, matched.id) == ("updated", 1)
    record = get_record(engine_runtime)
    assert record.session_id_dict == stored_scope
    assert record.count == 5
    assert len(json.loads(record.evidence_messages)) == 2


def test_global_candidate_merges_valid_scope_without_losing_other_sessions(engine_runtime: SimpleNamespace) -> None:
    """有效的全局归属仍累加当前会话，并保留其他会话已有计数。"""
    with engine_runtime.db_session() as session:
        record = session.get(Jargon, 1)
        record.is_global = True
        record.session_id_dict = '{"chat-a":4,"chat-b":7}'
        session.add(record)
    outcome, _ = engine_runtime.miner._persist_extracted_entry(
        {"content": "起飞", "raw_content": set(), "evidence_messages": []}
    )
    assert outcome == "updated"
    record = get_record(engine_runtime)
    assert record.count == 5
    assert json.loads(record.session_id_dict) == {"chat-a": 5, "chat-b": 7}


def test_reclassification_saves_new_meaning_in_record_and_history(engine_runtime: SimpleNamespace) -> None:
    engine_runtime.responses[0] = {"meaning": "飞机离地", "no_info": False}
    engine_runtime.responses[2] = {"is_similar": True}
    result = run_inference(engine_runtime)
    assert (result.status, result.meaning, result.is_jargon) == ("succeeded", "飞机离地", False)
    record = get_record(engine_runtime)
    assert (record.meaning, record.is_jargon) == ("飞机离地", False)
    with engine_runtime.db_session() as session:
        history = session.exec(select(JargonMeaningHistory)).one()
        assert (history.previous_meaning, history.meaning, history.is_jargon) == ("旧含义", "飞机离地", False)


@pytest.mark.parametrize("payload", [{}, {"is_similar": "false"}, {"is_similar": 0}, {"is_similar": None}])
def test_invalid_comparison_cannot_change_classification(engine_runtime: SimpleNamespace, payload: Any) -> None:
    engine_runtime.responses[2] = payload
    result = run_inference(engine_runtime)
    assert (result.status, result.requests) == ("failed", 3)
    record = get_record(engine_runtime)
    assert (record.meaning, record.last_inference_count) == ("旧含义", 0)
    with engine_runtime.db_session() as session:
        assert session.exec(select(JargonMeaningHistory)).all() == []


def test_unchanged_inference_does_not_evict_meaning_history(engine_runtime: SimpleNamespace) -> None:
    """结果未变化时更新推断计数，但不消耗历史保留名额。"""
    with engine_runtime.db_session() as session:
        record = session.get(Jargon, 1)
        record.meaning = "完成任务的庆祝"
        record.is_jargon = True
        session.add(record)
        session.add(JargonMeaningHistory(jargon_id=1, previous_meaning="更早解释", meaning=record.meaning))
    assert run_inference(engine_runtime).status == "succeeded"
    with engine_runtime.db_session() as session:
        history = session.exec(select(JargonMeaningHistory)).all()
        assert [(row.previous_meaning, row.meaning) for row in history] == [("更早解释", "完成任务的庆祝")]
    assert get_record(engine_runtime).last_inference_count == 4


def test_null_timestamps_keep_identity_during_recovery_and_writeback(engine_runtime: SimpleNamespace) -> None:
    """历史空时间戳保持原样，避免证据恢复后写回误判词条身份变化。"""
    with engine_runtime.db_session() as session:
        record = session.get(Jargon, 1)
        record.created_timestamp = None
        record.updated_timestamp = None
        record.evidence_messages = None
        session.add(record)
    loaded = engine_runtime.miner._load_jargon_by_id(1)
    assert loaded.created_timestamp is None
    assert run_inference(engine_runtime).status == "succeeded"
    record = get_record(engine_runtime)
    assert record.created_timestamp is None
    assert record.meaning == "完成任务的庆祝"
    assert record.evidence_messages is not None


def add_message(runtime: SimpleNamespace, message_id: str, content: str, **fields: Any) -> int:
    """保存真实消息行，允许测试独立设置平台、会话、原文和检索文本。"""
    values = dict(
        message_id=message_id,
        timestamp=datetime.now(),
        platform="qq",
        session_id="chat-a",
        user_id="test",
        user_nickname="test",
        raw_content=content.encode(),
        processed_plain_text=content,
    )
    values.update(fields)
    with runtime.db_session() as session:
        record = Messages(**values)
        session.add(record)
        session.flush()
        return record.id


def test_recovery_blacklist_matches_platform_and_keeps_neighbor_context(engine_runtime: SimpleNamespace) -> None:
    """平台与用户 ID 共同决定来源是否受限，被屏蔽的邻近消息仍可提供上下文。"""
    engine_runtime.config.jargon.learning_blacklist = [SimpleNamespace(platform="qq", user_id="test")]
    jargon = engine_runtime.miner._load_jargon_by_id(1)
    assert engine_runtime.miner._recover_evidence_groups(jargon, {"chat-a"}) == []
    # 同一用户 ID 在其他平台不受 QQ 黑名单影响；黑名单原消息仍可用于前后文。
    add_message(engine_runtime, "allowed", "终于起飞", platform="discord")
    groups = engine_runtime.miner._recover_evidence_groups(jargon, {"chat-a"})
    assert len(groups) == 1
    assert [(ref["platform"], ref["message_id"]) for ref in groups[0]] == [("qq", "m1"), ("discord", "allowed")]


def test_recovery_pages_past_media_only_matches(engine_runtime: SimpleNamespace) -> None:
    """即使检索首页只有媒体描述，也应继续查找正文中含词条的旧消息。"""
    engine_runtime.config.jargon.inference.evidence_limit = 1
    timestamp = datetime.now()
    for index in range(105):
        add_message(
            engine_runtime,
            f"media-{index}",
            "纯媒体没有词条正文",
            processed_plain_text="图片描述里起飞",
            timestamp=timestamp,
        )
    jargon = engine_runtime.miner._load_jargon_by_id(1)
    groups = engine_runtime.miner._recover_evidence_groups(jargon, {"chat-a"})
    assert len(groups) == 1
    assert "m1" in [ref["message_id"] for ref in groups[0]]


def test_recovery_neighbors_with_same_timestamp_are_in_id_order(engine_runtime: SimpleNamespace) -> None:
    """同一时间戳的消息按数据库 ID 确定邻居顺序和截取边界。"""
    with engine_runtime.db_session() as session:
        session.exec(delete(Messages))
    timestamp = datetime.now()
    for index in range(8):
        add_message(engine_runtime, f"tie-{index}", "起飞" if index == 4 else f"上下文 {index}", timestamp=timestamp)
    jargon = engine_runtime.miner._load_jargon_by_id(1)
    groups = engine_runtime.miner._recover_evidence_groups(jargon, {"chat-a"})
    assert [[ref["message_id"] for ref in group] for group in groups] == [[f"tie-{i}" for i in range(1, 7)]]


def test_ambiguous_legacy_refs_recover_exact_message_identity(engine_runtime: SimpleNamespace) -> None:
    """相同平台消息号出现在多个会话时，重建含会话和数据库 ID 的精确引用。"""
    with engine_runtime.db_session() as session:
        record = session.get(Jargon, 1)
        record.session_id_dict = '{"chat-a": 2, "chat-b": 2}'
        session.add(record)
    add_message(engine_runtime, "m1", "另一群也说起飞", session_id="chat-b")
    jargon = engine_runtime.miner._load_jargon_by_id(1)
    texts, cleaned, changed = engine_runtime.miner._load_evidence_contexts(jargon)
    assert changed is True
    groups = json.loads(cleaned)
    assert {group[0]["session_id"] for group in groups} == {"chat-a", "chat-b"}
    assert len({group[0]["database_id"] for group in groups}) == 2
    assert len(texts) == 2
    jargon.evidence_messages = cleaned
    reloaded, _, _ = engine_runtime.miner._load_evidence_contexts(jargon)
    assert reloaded == texts


def test_malformed_message_is_logged_and_does_not_block_other_evidence(
    engine_runtime: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    """解码失败的消息会被记录并剔除，其他有效来源仍能支持推断。"""
    malformed_id = add_message(engine_runtime, "bad", "起飞", raw_content=b"\xff")
    with engine_runtime.db_session() as session:
        record = session.get(Jargon, 1)
        record.evidence_messages = json.dumps([[{"platform": "qq", "message_id": "bad"}]])
        session.add(record)
    result = run_inference(engine_runtime)
    assert result.status == "succeeded"
    assert f"无法解码消息 id={malformed_id}" in caplog.text
    assert {
        ref["message_id"] for group in json.loads(get_record(engine_runtime).evidence_messages) for ref in group
    } == {"m1"}


def test_readonly_recovery_hash_detects_later_arriving_evidence(engine_runtime: SimpleNamespace) -> None:
    with engine_runtime.db_session() as session:
        session.exec(delete(Messages))
        record = session.get(Jargon, 1)
        record.evidence_messages = None
        session.add(record)
    jargon = engine_runtime.miner._load_jargon_by_id(1)
    assert engine_runtime.miner.recoverable_evidence_hash(jargon) == evidence_hash(None)
    add_message(engine_runtime, "new", "现在终于起飞")
    assert engine_runtime.miner.recoverable_evidence_hash(jargon) != evidence_hash(None)
    assert get_record(engine_runtime).evidence_messages is None


def test_shutdown_waits_for_started_commit_and_reports_actual_outcome(
    engine_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消到达提交线程后等待真实写回结果，即使任务被多次取消。"""
    from threading import Event

    started, release = Event(), Event()
    original = engine_runtime.miner._modify_jargon_entry

    def delayed_commit(*args: Any) -> str:
        started.set()
        assert release.wait(5), "test must release commit thread"
        return original(*args)

    monkeypatch.setattr(engine_runtime.miner, "_modify_jargon_entry", delayed_commit)

    async def run() -> InferenceResult:
        """等提交线程阻塞后发出取消，并在断言失败时也释放写回事务。"""
        task = asyncio.create_task(engine_runtime.miner._infer_meaning_by_id(1))
        assert await asyncio.to_thread(started.wait, 5)
        try:
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            assert get_record(engine_runtime).meaning == "旧含义"
        finally:
            release.set()
        return await task

    assert asyncio.run(run()).status == "succeeded"
    assert get_record(engine_runtime).meaning == "完成任务的庆祝"


@pytest.mark.parametrize("source_problem", ["corrupt", "unrelated", "blacklisted"])
def test_neighbor_only_groups_do_not_supply_false_evidence(
    engine_runtime: SimpleNamespace, source_problem: str
) -> None:
    """来源消息无效时，单独存在的邻近文字不能触发模型请求。"""
    add_message(engine_runtime, "neighbor", "只是普通上下文", user_id="neighbor")
    with engine_runtime.db_session() as session:
        source = session.exec(select(Messages).where(Messages.message_id == "m1")).one()
        if source_problem == "corrupt":
            source.raw_content = b"\xff"
        elif source_problem == "unrelated":
            source.raw_content = "正文里已经没有原词".encode()
        else:
            engine_runtime.config.jargon.learning_blacklist = [SimpleNamespace(platform="qq", user_id="test")]
        session.add(source)
        record = session.get(Jargon, 1)
        record.evidence_messages = json.dumps(
            [[{"platform": "qq", "message_id": "m1"}, {"platform": "qq", "message_id": "neighbor"}]]
        )
        session.add(record)
    assert run_inference(engine_runtime).status == "missing_evidence"
    assert engine_runtime.calls == []
    assert get_record(engine_runtime).meaning == "旧含义"
