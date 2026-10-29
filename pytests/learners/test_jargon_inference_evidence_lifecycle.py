"""Issue #2316: real learner methods with SQLite; no external service calls."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import ast
import asyncio
import json

import pytest
from json_repair import repair_json
from sqlalchemy import Column, DateTime, Text, update
from sqlmodel import Field, Session, SQLModel, col, create_engine, select


class Jargon(SQLModel, table=True):
    __tablename__ = "jargons"
    id: int | None = Field(default=None, primary_key=True)
    content: str
    evidence_messages: str | None = Field(default=None, sa_column=Column(Text))
    meaning: str = ""
    session_id_dict: str = "{}"
    count: int = 4
    is_jargon: bool = False
    is_complete: bool = False
    is_global: bool = False
    last_inference_count: int = 0
    created_by: str = "AI"
    updated_timestamp: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime))


class Messages(SQLModel, table=True):
    __tablename__ = "mai_messages"
    id: int | None = Field(default=None, primary_key=True)
    platform: str
    message_id: str
    raw_content: bytes


def evidence(*ids):
    return json.dumps([[{"platform": "qq", "message_id": item}] for item in ids], ensure_ascii=False)


def get_row(engine, item_id):
    with Session(engine) as session:
        return session.get(Jargon, item_id)


def append_group(engine, item_id, message_id):
    with Session(engine) as session:
        item = session.get(Jargon, item_id)
        groups = json.loads(item.evidence_messages or "[]")
        groups.append([{"platform": "qq", "message_id": message_id}])
        item.evidence_messages = json.dumps(groups, ensure_ascii=False)
        session.commit()


@pytest.fixture
def env(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'jargon.sqlite').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def get_db_session(auto_commit=True):
        with Session(engine) as session:
            try:
                yield session
                if auto_commit:
                    session.commit()
            except Exception:
                session.rollback()
                raise

    path = Path(__file__).resolve().parents[2] / "src/learners/jargon_miner.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = deepcopy(next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "JargonMiner"))
    tree = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls],
        type_ignores=[],
    )
    ast.fix_missing_locations(tree)
    prompt = SimpleNamespace(add_context=lambda *args: None)
    ns = {
        "__name__": "jargon_real_methods",
        "OrderedDict": __import__("collections").OrderedDict,
        "datetime": datetime,
        "asyncio": asyncio,
        "json": json,
        "select": select,
        "update": update,
        "col": col,
        "Jargon": Jargon,
        "Messages": Messages,
        "JargonCreatedBy": SimpleNamespace(AI="AI", MANUAL="MANUAL"),
        "get_db_session": get_db_session,
        "MessageUtils": SimpleNamespace(from_db_record_msg_to_MaiSeq=lambda b: b.decode()),
        "extract_learning_text": lambda text: text,
        "logger": Mock(),
        "repair_json": repair_json,
        "llm_inference": SimpleNamespace(generate_response=AsyncMock()),
        "prompt_manager": SimpleNamespace(
            get_prompt=lambda *_: prompt, render_prompt=AsyncMock(return_value="test-prompt")
        ),
        "global_config": SimpleNamespace(bot=SimpleNamespace(nickname="test-bot")),
        "JARGON_INFERENCE_THRESHOLDS": [4, 8, 25, 100],
    }
    exec(compile(tree, str(path), "exec"), ns)
    cls = ns["JargonMiner"]
    cls._log_inference_prompt_preview = lambda *args, **kwargs: None
    hooks = SimpleNamespace(invoke_hook=AsyncMock(return_value=SimpleNamespace(aborted=False, kwargs={})))
    cls._get_runtime_manager = staticmethod(lambda: hooks)
    miner = cls("test-session", "test-session")

    with Session(engine) as session:
        session.add(Messages(platform="qq", message_id="old", raw_content=b"old-text"))
        session.add(Messages(platform="qq", message_id="new", raw_content=b"new-text"))
        session.add(Jargon(content="candidate", evidence_messages=evidence("old")))
        session.commit()
        ident = session.exec(select(Jargon).where(Jargon.content == "candidate")).first().id

    def obj():
        item = get_row(engine, ident)
        return SimpleNamespace(
            item_id=item.id,
            content=item.content,
            evidence_messages=item.evidence_messages,
            meaning=item.meaning,
            count=item.count,
            is_jargon=item.is_jargon,
            is_complete=item.is_complete,
            is_global=item.is_global,
            last_inference_count=item.last_inference_count,
            created_by=item.created_by,
        )

    def responses(*items):
        ns["llm_inference"].generate_response = AsyncMock(
            side_effect=[item if isinstance(item, Exception) else SimpleNamespace(response=item) for item in items]
        )

    yield SimpleNamespace(
        engine=engine,
        ident=ident,
        miner=miner,
        ns=ns,
        hooks=hooks,
        obj=obj,
        responses=responses,
    )
    engine.dispose()


GOOD1 = '{"no_info": false, "meaning": "useful"}'
GOOD2 = '{"meaning": "ordinary"}'
GOOD3 = '{"is_similar": false}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replies",
    [
        ("",),
        ("{}",),
        (GOOD1, ""),
        (GOOD1, "{}"),
        (GOOD1, GOOD2, ""),
        (GOOD1, GOOD2, "{}"),
    ],
    ids=["stage1-empty", "stage1-invalid", "stage2-empty", "stage2-invalid", "stage3-empty", "stage3-invalid"],
)
async def test_failed_inference_keeps_evidence(env, replies):
    env.responses(*replies)
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert row.evidence_messages == evidence("old")
    assert row.last_inference_count == 0
    assert row.meaning == ""


@pytest.mark.asyncio
async def test_aborted_hook_keeps_evidence(env):
    env.responses(GOOD1, GOOD2, GOOD3)
    env.hooks.invoke_hook = AsyncMock(return_value=SimpleNamespace(aborted=True, kwargs={}))
    await env.miner.infer_meaning(env.obj())
    assert get_row(env.engine, env.ident).evidence_messages == evidence("old")


@pytest.mark.asyncio
async def test_success_consumes_only_the_initial_snapshot(env):
    env.responses(GOOD1, GOOD2, GOOD3)
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert row.evidence_messages is None
    assert (row.meaning, row.is_jargon, row.last_inference_count) == ("useful", True, 4)


@pytest.mark.asyncio
async def test_no_info_updates_threshold_and_consumes_after_commit(env):
    env.responses('{"no_info": true, "meaning": ""}')
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert (row.evidence_messages, row.last_inference_count) == (None, 4)


@pytest.mark.asyncio
async def test_concurrent_append_survives_success(env):
    replies = [GOOD1, GOOD2, GOOD3]

    async def model(*args, **kwargs):
        if len(replies) == 3:
            append_group(env.engine, env.ident, "new")
        return SimpleNamespace(response=replies.pop(0))

    env.ns["llm_inference"].generate_response = AsyncMock(side_effect=model)
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert row.evidence_messages == evidence("new")
    assert row.meaning == "useful"


@pytest.mark.asyncio
async def test_concurrent_append_survives_failure(env):
    async def model(*args, **kwargs):
        append_group(env.engine, env.ident, "new")
        return SimpleNamespace(response="")

    env.ns["llm_inference"].generate_response = AsyncMock(side_effect=model)
    await env.miner.infer_meaning(env.obj())
    assert get_row(env.engine, env.ident).evidence_messages == evidence("old", "new")


@pytest.mark.asyncio
async def test_final_persist_error_does_not_consume(env, monkeypatch):
    env.responses(GOOD1, GOOD2, GOOD3)

    def fail(*args, **kwargs):
        raise RuntimeError("injected final write failure")

    monkeypatch.setattr(env.miner, "_modify_jargon_entry", fail)
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert (row.evidence_messages, row.meaning) == (evidence("old"), "")


@pytest.mark.asyncio
async def test_manual_jargon_is_unchanged(env):
    with Session(env.engine) as session:
        row = session.get(Jargon, env.ident)
        row.created_by = "MANUAL"
        session.commit()
    env.responses("")
    await env.miner.infer_meaning(env.obj())
    assert get_row(env.engine, env.ident).evidence_messages == evidence("old")


@pytest.mark.asyncio
async def test_stale_extraction_object_cannot_resurrect_consumed_groups(env):
    # Keep an ORM snapshot from before inference, then append new evidence
    # through the real extraction writer after the inference has committed.
    stale = get_row(env.engine, env.ident)
    env.responses(GOOD1, GOOD2, GOOD3)
    await env.miner.infer_meaning(env.obj())
    new_group = [[{"platform": "qq", "message_id": "new"}]]
    assert env.miner._update_jargon(stale, new_group) is True
    row = get_row(env.engine, env.ident)
    assert row.evidence_messages == evidence("new")
    assert row.count == 5
    assert json.loads(row.session_id_dict) == {"test-session": 1}


@pytest.mark.asyncio
async def test_no_valid_text_prunes_only_invalid_snapshot(env):
    # The old group cannot be learned, while a new group arrives just after
    # evidence loading and before the invalid-group cleanup transaction.
    with Session(env.engine) as session:
        msg = session.exec(select(Messages).where(Messages.message_id == "old")).first()
        msg.raw_content = b""
        session.commit()
    original_loader = env.miner._load_evidence_contexts

    def load_then_append(obj):
        result = original_loader(obj)
        append_group(env.engine, env.ident, "new")
        return result

    env.miner._load_evidence_contexts = load_then_append
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert row.evidence_messages == evidence("new")
    env.ns["llm_inference"].generate_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_inference_remains_inert_if_record_turns_manual(env):
    async def switched_to_manual(*args, **kwargs):
        with Session(env.engine) as session:
            row = session.get(Jargon, env.ident)
            row.created_by = "MANUAL"
            session.commit()
        return SimpleNamespace(response=GOOD1)

    env.ns["llm_inference"].generate_response = AsyncMock(side_effect=switched_to_manual)
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert row.evidence_messages == evidence("old")
    assert row.meaning == ""


@pytest.mark.asyncio
async def test_real_commit_exception_rolls_back_result_and_evidence(env):
    env.responses(GOOD1, GOOD2, GOOD3)
    original_session = env.ns["get_db_session"]

    @contextmanager
    def fail_final_commit(auto_commit=True):
        with original_session(auto_commit=auto_commit) as session:
            if not auto_commit:

                def fail():
                    raise RuntimeError("injected SQLite commit failure")

                session.commit = fail
            yield session

    env.ns["get_db_session"] = fail_final_commit
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert row.evidence_messages == evidence("old")
    assert row.meaning == ""
    assert row.last_inference_count == 0


@pytest.mark.asyncio
async def test_stale_inference_does_not_overwrite_newer_decision(env):
    pending = env.obj()
    with Session(env.engine) as session:
        row = session.get(Jargon, env.ident)
        row.last_inference_count = 4
        row.meaning = "more recent decision"
        row.evidence_messages = evidence("old", "new")
        session.commit()
    env.responses(GOOD1, GOOD2, GOOD3)
    await env.miner.infer_meaning(pending)
    row = get_row(env.engine, env.ident)
    assert row.meaning == "more recent decision"
    assert row.last_inference_count == 4
    assert row.evidence_messages == evidence("old", "new")


@pytest.mark.asyncio
async def test_model_exception_keeps_evidence(env):
    env.ns["llm_inference"].generate_response = AsyncMock(side_effect=RuntimeError("synthetic upstream API failure"))
    with pytest.raises(RuntimeError, match="synthetic upstream"):
        await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert (row.evidence_messages, row.last_inference_count) == (evidence("old"), 0)


@pytest.mark.asyncio
async def test_no_info_does_not_consume_newly_added_evidence(env):
    async def model(*args, **kwargs):
        append_group(env.engine, env.ident, "new")
        return SimpleNamespace(response='{"no_info": true, "meaning": ""}')

    env.ns["llm_inference"].generate_response = AsyncMock(side_effect=model)
    await env.miner.infer_meaning(env.obj())
    row = get_row(env.engine, env.ident)
    assert row.evidence_messages == evidence("new")
    assert row.last_inference_count == 4
