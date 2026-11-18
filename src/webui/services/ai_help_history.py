"""WebUI 答疑历史及续聊上下文。"""

from typing import List, Tuple, cast

from pydantic import BaseModel, Field
from sqlmodel import delete, select

import time

from src.common.database.database import get_db_session
from src.common.database.database_model import WebUIHelpSession, WebUIHelpTurn

from .ai_help_artifacts import HelpArtifact
from .ai_search_models import AISearchResponse, AISearchSource


class HelpReply(BaseModel):
    answer: str
    suggestions: List[str]
    sources: List[AISearchSource]
    artifacts: List[HelpArtifact] = Field(default_factory=list)


class HelpTurnDetail(BaseModel):
    id: int
    question: str
    reply: HelpReply | None
    error: str


class HelpSessionDetail(BaseModel):
    id: str
    title: str
    updated_at: float
    turns: List[HelpTurnDetail]


def list_help_sessions() -> List[WebUIHelpSession]:
    with get_db_session() as db:
        sessions = db.exec(
            select(WebUIHelpSession).order_by(WebUIHelpSession.updated_at.desc(), WebUIHelpSession.id).limit(50)
        )
        return list(sessions)


def get_help_session(session_id: str) -> HelpSessionDetail | None:
    with get_db_session() as db:
        session = db.get(WebUIHelpSession, session_id)
        if session is None:
            return None
        turns = db.exec(select(WebUIHelpTurn).where(WebUIHelpTurn.session_id == session_id).order_by(WebUIHelpTurn.id))
        return HelpSessionDetail(
            id=session.id,
            title=session.title,
            updated_at=session.updated_at,
            turns=[
                HelpTurnDetail(
                    id=cast(int, turn.id),
                    question=turn.question,
                    reply=HelpReply.model_validate_json(turn.reply_json) if turn.reply_json is not None else None,
                    error=turn.error,
                )
                for turn in turns
            ],
        )


def delete_help_session(session_id: str) -> bool:
    """在同一事务中删除指定会话和它的全部答疑轮次。"""

    with get_db_session() as db:
        session = db.get(WebUIHelpSession, session_id)
        if session is None:
            return False
        db.exec(delete(WebUIHelpTurn).where(WebUIHelpTurn.session_id == session_id))
        db.delete(session)
        return True


def begin_help_turn(session_id: str, question: str, *, new_session: bool) -> Tuple[int, List[Tuple[str, str]]] | None:
    with get_db_session() as db:
        now = time.time()
        history: List[Tuple[str, str]] = []
        if new_session:
            db.add(WebUIHelpSession(id=session_id, title=question, updated_at=now))
            db.flush()
        else:
            session = db.get(WebUIHelpSession, session_id)
            if session is None:
                return None
            session.updated_at = now
            previous = db.exec(
                select(WebUIHelpTurn.question, WebUIHelpTurn.reply_json)
                .where(
                    WebUIHelpTurn.session_id == session_id,
                    WebUIHelpTurn.reply_json.is_not(None),
                    WebUIHelpTurn.error == "",
                )
                .order_by(WebUIHelpTurn.id.desc())
                .limit(8)
            )
            history = [
                (question, HelpReply.model_validate_json(cast(str, reply_json)).answer[:2000])
                for question, reply_json in previous
            ]
            history.reverse()

        turn = WebUIHelpTurn(session_id=session_id, question=question)
        db.add(turn)
        db.flush()
        assert turn.id is not None
        return turn.id, history


def finish_help_turn(turn_id: int, *, response: AISearchResponse | None = None, error: str = "") -> None:
    with get_db_session() as db:
        turn = db.get_one(WebUIHelpTurn, turn_id)
        if response is not None:
            turn.reply_json = HelpReply.model_validate(response, from_attributes=True).model_dump_json()
            turn.error = response.grounding_error
        else:
            turn.error = error
