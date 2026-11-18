from contextlib import contextmanager
from typing import Any, Dict, Iterator, Set

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import Expression, Jargon
from src.webui.routers import expression, jargon


@pytest.fixture
def export_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[Expression.__table__, Jargon.__table__])
    with Session(engine) as session:
        session.add_all([
            Expression(id=1, situation="a", style="a", content_list="[]", session_id="chat-a"),
            Expression(id=2, situation="b", style="b", content_list="[]", session_id="chat-b"),
            Expression(id=3, situation="global", style="global", content_list="[]", session_id=None),
            Jargon(id=1, content="a", meaning="a", session_id_dict='{"chat-a": 1}'),
            Jargon(id=2, content="b", meaning="b", session_id_dict='{"chat-a": 1, "chat-b": 2}'),
            Jargon(id=3, content="global", meaning="global", is_global=True),
        ])
        session.commit()

    @contextmanager
    def get_test_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(expression, "get_db_session", get_test_session)
    monkeypatch.setattr(jargon, "get_db_session", get_test_session)
    monkeypatch.setattr(expression, "get_chat_name", lambda chat_id, session: "测试聊天")
    yield
    engine.dispose()


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, {"a", "b", "global"}),
        ({"ids": [1, 2]}, {"a", "b"}),
        ({"chat_id": "chat-a"}, {"a"}),
        ({"chat_ids": ["chat-a", "chat-b", "chat-a"]}, {"a", "b"}),
        ({"chat_ids": []}, set()),
        ({"ids": []}, set()),
    ],
)
def test_expression_export_scopes(export_database: None, params: Dict[str, Any], expected: Set[str]) -> None:
    result = expression.export_expressions(expression.ExpressionExportRequest(**params))
    assert {item.situation for item in result.expressions} == expected
    assert result.count == len(expected)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, {"a", "b", "global"}),
        ({"ids": [1, 3]}, {"a", "global"}),
        ({"session_ids": ["chat-b"]}, {"b"}),
        ({"session_ids": ["chat-a", "chat-b", "chat-a"]}, {"a", "b"}),
        ({"session_ids": ["chat"]}, set()),
        ({"session_ids": []}, set()),
        ({"ids": []}, set()),
    ],
)
def test_jargon_export_scopes(export_database: None, params: Dict[str, Any], expected: Set[str]) -> None:
    result = jargon.export_jargons(jargon.JargonExportRequest(**params))
    assert {item.content for item in result.jargons} == expected
    assert result.count == len(expected)


def test_exports_reject_missing_selection(export_database: None) -> None:
    with pytest.raises(HTTPException, match="999"):
        expression.export_expressions(expression.ExpressionExportRequest(ids=[1, 999]))
    with pytest.raises(HTTPException, match="999"):
        jargon.export_jargons(jargon.JargonExportRequest(ids=[1, 999]))
