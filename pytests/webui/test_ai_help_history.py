from collections import OrderedDict
from contextlib import contextmanager
from threading import Event
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

import asyncio
import json
import pytest

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v46_to_v47 import migrate_v46_to_v47
from src.common.database.database_model import WebUIHelpTurn
from src.webui.routers import search
from src.webui.services import ai_help_history
from src.webui.services.ai_help_artifacts import HelpArtifact
from src.webui.services.ai_search_models import AISearchCandidate, AISearchRequest, AISearchResponse


def test_help_history_persists_and_only_reuses_context_for_the_selected_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'help.db').as_posix()}")
    with engine.begin() as connection:
        migrate_v46_to_v47(MigrationExecutionContext(connection, 46, 47, 1, "v46_to_v47", 1))

    @contextmanager
    def get_session():
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    agent = AsyncMock(
        side_effect=[
            AISearchResponse(
                answer="Use the model settings",
                artifacts=[HelpArtifact(kind="markdown", title="Steps", content="1. Open settings")],
            ),
            HTTPException(status_code=502, detail="Agent unavailable"),
            AISearchResponse(answer="Open the model provider tab"),
            AISearchResponse(answer="Use the model settings"),
            AISearchResponse(answer="Ordinary search"),
        ]
    )
    monkeypatch.setattr(ai_help_history, "get_db_session", get_session)
    monkeypatch.setattr(search, "run_ai_search_agent", agent)
    monkeypatch.setattr(search, "_AI_SEARCH_CACHE", OrderedDict())
    app = FastAPI()
    app.include_router(search.router)

    def ask(client, question, help_session_id=None):
        response = client.post(
            "/search/ai/stream",
            json={
                "query": question,
                "candidates": [{"id": "settings", "title": "Model settings"}],
                "help_session_id": help_session_id,
            },
        )
        assert response.status_code == 200
        return [json.loads(line) for line in response.text.splitlines()]

    try:
        with TestClient(app) as client:
            first = ask(client, "Where do I set the model?", "new")
            session_id = first[0]["id"]
            assert first[0]["type"] == "session"
            assert first[-1]["type"] == "result"
            failure = ask(client, "Which tab?", session_id)[-1]
            assert failure["type"] == "error"

        with TestClient(app) as client:
            ask(client, "Can you be more specific?", session_id)
            assert agent.await_args.kwargs["history"] == [("Where do I set the model?", "Use the model settings")]
            separate = ask(client, "Where do I set the model?", "new")
            assert separate[0]["id"] != session_id
            assert agent.await_args.kwargs["history"] == []
            assert ask(client, "Find model settings")[0]["type"] == "progress"
            assert len(client.get("/search/help/sessions").json()) == 2

            turns = client.get(f"/search/help/sessions/{session_id}").json()["turns"]
            assert [turn["question"] for turn in turns] == [
                "Where do I set the model?",
                "Which tab?",
                "Can you be more specific?",
            ]
            assert turns[1]["error"] == failure["message"]
            assert turns[0]["reply"]["artifacts"] == [
                {"kind": "markdown", "title": "Steps", "content": "1. Open settings"}
            ]
            assert turns[2]["reply"]["answer"] == "Open the model provider tab"

            path = f"/search/help/sessions/{session_id}"
            deleted = client.delete(path)
            assert deleted.status_code == 204
            assert deleted.content == b""
            assert client.get(path).status_code == 404
            assert client.delete(path).status_code == 404
            assert [item["id"] for item in client.get("/search/help/sessions").json()] == [separate[0]["id"]]
            assert ask(client, "Can you continue?", session_id)[-1]["status"] == 404
            with Session(engine) as db:
                assert not db.exec(select(WebUIHelpTurn).where(WebUIHelpTurn.session_id == session_id)).all()
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted_write", ["begin", "finish"])
async def test_help_session_remains_reserved_until_inflight_sqlite_write_finishes(monkeypatch, interrupted_write):
    request = AISearchRequest(
        query="Help", candidates=[AISearchCandidate(id="help", title="Help")], help_session_id=uuid4()
    )
    loop = asyncio.get_running_loop()
    write_started = asyncio.Event()
    release_write = Event()
    outcomes = []

    def hold_write():
        loop.call_soon_threadsafe(write_started.set)
        assert release_write.wait(timeout=5)

    def begin(_session_id, _question, *, new_session):
        assert not new_session
        if interrupted_write == "begin":
            hold_write()
        return 1, []

    def finish(turn_id, *, response, error):
        assert turn_id == 1
        if interrupted_write == "finish":
            hold_write()
        outcomes.append((response, error))

    answer = AISearchResponse(answer="Finished")
    agent = AsyncMock(return_value=answer)
    monkeypatch.setattr(search, "begin_help_turn", begin)
    monkeypatch.setattr(search, "finish_help_turn", finish)
    monkeypatch.setattr(search, "run_ai_search_agent", agent)
    delete = Mock()
    monkeypatch.setattr(search, "delete_help_session", delete)

    stream = search._stream_ai_search_events(request)
    if interrupted_write == "finish":
        assert json.loads(await anext(stream))["type"] == "session"
    consumer = asyncio.create_task(anext(stream))
    try:
        await asyncio.wait_for(write_started.wait(), timeout=5)
        consumer.cancel()
        await asyncio.sleep(0)

        async with asyncio.timeout(2):
            conflict = [json.loads(event) async for event in search._stream_ai_search_events(request)]
        assert conflict[-1]["status"] == 409
        assert str(request.help_session_id) in search._ACTIVE_HELP_SESSIONS
        with pytest.raises(HTTPException) as rejected_delete:
            await search.remove_help_session(request.help_session_id)
        assert rejected_delete.value.status_code == 409
        delete.assert_not_called()
    finally:
        release_write.set()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        await stream.aclose()

    assert str(request.help_session_id) not in search._ACTIVE_HELP_SESSIONS
    if interrupted_write == "begin":
        agent.assert_not_awaited()
        assert outcomes == [(None, "答疑已中断")]
    else:
        agent.assert_awaited_once()
        assert outcomes == [(answer, "")]


@pytest.mark.asyncio
async def test_disconnected_deletion_holds_reservation_until_database_write_finishes(monkeypatch):
    session_id = uuid4()
    request = AISearchRequest(
        query="Help", candidates=[AISearchCandidate(id="help", title="Help")], help_session_id=session_id
    )
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release_write = Event()

    def delete_session(key):
        assert key == str(session_id)
        loop.call_soon_threadsafe(started.set)
        assert release_write.wait(timeout=5)
        return True

    monkeypatch.setattr(search, "delete_help_session", delete_session)
    begin = Mock()
    monkeypatch.setattr(search, "begin_help_turn", begin)

    deletion = asyncio.create_task(search.remove_help_session(session_id))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        deletion.cancel()
        await asyncio.sleep(0)

        with pytest.raises(HTTPException) as rejected_delete:
            await search.remove_help_session(session_id)
        assert rejected_delete.value.status_code == 409
        async with asyncio.timeout(2):
            conflict = [json.loads(event) async for event in search._stream_ai_search_events(request)]
        assert conflict[-1]["status"] == 409
        begin.assert_not_called()
    finally:
        release_write.set()
        with pytest.raises(asyncio.CancelledError):
            await deletion

    assert str(session_id) not in search._ACTIVE_HELP_SESSIONS
