"""以真实 SQLite 验证聊天删除的黑话清理；只载入目标函数，避免启动聊天运行时。"""

from pathlib import Path
from typing import Any, Dict

from sqlalchemy import func
from sqlmodel import Session, SQLModel, col, create_engine, select

import ast
import json
import logging

from src.common.database.database_model import Jargon, JargonInferenceState, JargonMeaningHistory


def load_cleanup():
    path = Path(__file__).resolve().parents[2] / "src/webui/routers/chat/routes.py"
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        item for item in parsed.body if isinstance(item, ast.FunctionDef) and item.name == "_delete_or_unlink_jargons"
    )
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "Jargon": Jargon,
        "select": select,
        "func": func,
        "col": col,
        "json": json,
        "logger": logging.getLogger(__name__),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_delete_or_unlink_jargons"]


def test_chat_delete_then_sqlite_id_reuse_has_no_old_inference_metadata():
    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(
            engine, tables=[Jargon.__table__, JargonInferenceState.__table__, JargonMeaningHistory.__table__]
        )
        with Session(engine) as session:
            session.add(Jargon(id=1, content="旧词", meaning="旧含义", session_id_dict='{"deleted-chat":1}'))
            session.add(JargonInferenceState(jargon_id=1, locked=True, status="succeeded"))
            session.add(JargonMeaningHistory(jargon_id=1, previous_meaning="更早的含义", meaning="旧含义"))
            session.commit()
            assert load_cleanup()(session, "deleted-chat") == {"deleted": 1, "unlinked": 0, "removed_refs": 1}
            session.commit()
            replacement = Jargon(content="新词", meaning="", session_id_dict='{"new-chat":1}')
            session.add(replacement)
            session.commit()
            session.refresh(replacement)
            assert replacement.id == 1
            assert session.get(JargonInferenceState, replacement.id) is None
            assert session.exec(select(JargonMeaningHistory)).all() == []
    finally:
        engine.dispose()


def test_unlinking_shared_chat_preserves_other_chat_and_inference_metadata():
    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(
            engine, tables=[Jargon.__table__, JargonInferenceState.__table__, JargonMeaningHistory.__table__]
        )
        with Session(engine) as session:
            session.add(
                Jargon(
                    id=1,
                    content="共享词",
                    meaning="含义",
                    count=3,
                    session_id_dict='{"deleted-chat":1,"remaining-chat":2}',
                )
            )
            session.add(JargonInferenceState(jargon_id=1, locked=True))
            session.add(JargonMeaningHistory(jargon_id=1, previous_meaning="先前含义", meaning="含义"))
            session.commit()
            assert load_cleanup()(session, "deleted-chat") == {"deleted": 0, "unlinked": 1, "removed_refs": 1}
            session.commit()
            jargon = session.get(Jargon, 1)
            assert jargon is not None and json.loads(jargon.session_id_dict) == {"remaining-chat": 2}
            assert jargon.count == 2
            state = session.get(JargonInferenceState, 1)
            assert state is not None and state.locked
            assert len(session.exec(select(JargonMeaningHistory)).all()) == 1
    finally:
        engine.dispose()
