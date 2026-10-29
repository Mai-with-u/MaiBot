import json
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from src.common.data_models.jargon_data_model import MaiJargon
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.common.database.database_model import Jargon, Messages
from src.common.utils.utils_message import MessageUtils
from src.learners import jargon_miner as module


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jargon.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine, tables=[Jargon.__table__, Messages.__table__])

    @contextmanager
    def session_scope():
        with Session(engine) as session:
            yield session
            session.commit()

    monkeypatch.setattr(module, "get_db_session", session_scope)
    with session_scope() as session:
        row = Jargon(content="term", meaning="", count=4, evidence_messages='[[{"platform":"p","message_id":"1"}]]')
        session.add(row)
        session.add(
            Messages(
                message_id="1",
                platform="p",
                timestamp=datetime.now(),
                user_id="u",
                user_nickname="u",
                session_id="session",
                raw_content=MessageUtils.from_MaiSeq_to_db_record_msg(MessageSequence([TextComponent("context")])),
            )
        )
        session.flush()
        item = MaiJargon.from_db_instance(row)
    yield engine, item
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "responses",
    [
        [""],
        ['{"meaning":"sense"}', ""],
        ['{"meaning":"sense"}', '{"meaning":"literal"}', ""],
        ['{"no_info":true}'],
    ],
)
async def test_failed_inference_retains_evidence(database, monkeypatch, responses):
    engine, item = database
    snapshot = item.evidence_messages
    miner = module.JargonMiner("session", "test")
    monkeypatch.setattr(miner, "_load_evidence_contexts", lambda _: (["context"], item.evidence_messages, False))
    monkeypatch.setattr(miner, "_log_inference_prompt_preview", lambda **_: None)
    monkeypatch.setattr(module.prompt_manager, "get_prompt", lambda _: SimpleNamespace(add_context=lambda *_: None))
    monkeypatch.setattr(module.prompt_manager, "render_prompt", AsyncMock(return_value="prompt"))
    monkeypatch.setattr(
        module.llm_inference,
        "generate_response",
        AsyncMock(side_effect=[SimpleNamespace(response=r) for r in responses]),
    )
    await miner.infer_meaning(item)
    with Session(engine) as session:
        row = session.get(Jargon, item.item_id)
        assert row.evidence_messages == snapshot
        assert row.last_inference_count == 0


@pytest.mark.asyncio
async def test_real_evidence_can_be_reloaded_after_failure(database, monkeypatch):
    engine, item = database
    miner = module.JargonMiner("session", "test")
    monkeypatch.setattr(miner, "_log_inference_prompt_preview", lambda **_: None)
    monkeypatch.setattr(module.prompt_manager, "get_prompt", lambda _: SimpleNamespace(add_context=lambda *_: None))
    monkeypatch.setattr(module.prompt_manager, "render_prompt", AsyncMock(return_value="prompt"))
    monkeypatch.setattr(module.llm_inference, "generate_response", AsyncMock(return_value=SimpleNamespace(response="")))
    await miner.infer_meaning(item)
    with Session(engine) as session:
        row = session.get(Jargon, item.item_id)
        reloaded = MaiJargon.from_db_instance(row)
        assert miner._should_infer_meaning(row)
    assert miner._load_evidence_contexts(reloaded)[0] == ["[1] context"]


@pytest.mark.parametrize("changed", [False, True])
def test_finalize_consumes_only_unchanged_snapshot(database, changed):
    engine, item = database
    snapshot = item.evidence_messages
    newer = json.dumps([[{"platform": "p", "message_id": "1"}], [{"platform": "p", "message_id": "2"}]])
    if changed:
        with Session(engine) as session:
            row = session.get(Jargon, item.item_id)
            row.evidence_messages = newer
            session.commit()
    item.meaning = "sense"
    item.last_inference_count = 4
    saved = module.JargonMiner("session", "test")._finalize_jargon_inference(item, snapshot)
    with Session(engine) as session:
        row = session.get(Jargon, item.item_id)
        assert saved is not changed
        assert row.meaning == ("" if changed else "sense")
        assert row.evidence_messages == (newer if changed else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "abort", "write_failure"])
async def test_inference_finalization(database, monkeypatch, outcome):
    engine, item = database
    snapshot = item.evidence_messages
    miner = module.JargonMiner("session", "test")
    monkeypatch.setattr(miner, "_load_evidence_contexts", lambda _: (["context"], snapshot, False))
    monkeypatch.setattr(miner, "_log_inference_prompt_preview", lambda **_: None)
    monkeypatch.setattr(module.prompt_manager, "get_prompt", lambda _: SimpleNamespace(add_context=lambda *_: None))
    monkeypatch.setattr(module.prompt_manager, "render_prompt", AsyncMock(return_value="prompt"))
    responses = ['{"meaning":"sense"}', '{"meaning":"literal"}', '{"is_similar":false}']
    monkeypatch.setattr(
        module.llm_inference,
        "generate_response",
        AsyncMock(side_effect=[SimpleNamespace(response=r) for r in responses]),
    )
    hook = AsyncMock(return_value=SimpleNamespace(aborted=outcome == "abort", kwargs={}))
    monkeypatch.setattr(miner, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=hook))
    if outcome == "write_failure":
        monkeypatch.setattr(
            miner, "_finalize_jargon_inference", lambda *_: (_ for _ in ()).throw(RuntimeError("write failed"))
        )
    await miner.infer_meaning(item)
    with Session(engine) as session:
        row = session.get(Jargon, item.item_id)
        assert row.evidence_messages == (None if outcome == "success" else snapshot)
        assert row.meaning == ("sense" if outcome == "success" else "")


@pytest.mark.asyncio
async def test_no_context_cleans_only_unchanged_evidence(database, monkeypatch):
    engine, item = database
    miner = module.JargonMiner("session", "test")
    monkeypatch.setattr(miner, "_load_evidence_contexts", lambda _: ([], None, True))
    await miner.infer_meaning(item)
    with Session(engine) as session:
        assert session.get(Jargon, item.item_id).evidence_messages is None


@pytest.mark.asyncio
async def test_no_context_preserves_changed_evidence(database, monkeypatch):
    engine, item = database
    newer = '[[{"platform":"p","message_id":"2"}]]'
    with Session(engine) as session:
        session.get(Jargon, item.item_id).evidence_messages = newer
        session.commit()
    miner = module.JargonMiner("session", "test")
    monkeypatch.setattr(miner, "_load_evidence_contexts", lambda _: ([], None, True))
    await miner.infer_meaning(item)
    with Session(engine) as session:
        assert session.get(Jargon, item.item_id).evidence_messages == newer


def test_database_failure_rolls_back_result_and_evidence(database):
    engine, item = database
    snapshot = item.evidence_messages
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TRIGGER reject_update BEFORE UPDATE ON jargons BEGIN SELECT RAISE(ABORT, 'failed'); END")
        )
    item.meaning = "sense"
    with pytest.raises(Exception, match="failed"):
        module.JargonMiner("session", "test")._finalize_jargon_inference(item, snapshot)
    with Session(engine) as session:
        row = session.get(Jargon, item.item_id)
        assert row.meaning == ""
        assert row.evidence_messages == snapshot
