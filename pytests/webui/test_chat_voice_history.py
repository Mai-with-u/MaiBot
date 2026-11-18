from base64 import b64encode
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, create_engine, select

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence
from src.common.database import database
from src.common.database.database_model import BinaryData, Messages
from src.common.utils.utils_message import MessageUtils
from src.maisaka.monitor.message_payload import build_monitor_message_media
from src.maisaka.monitor.voice_media import restore_legacy_voice_media
from src.webui.routers.chat.serializers import serialize_message_sequence


def test_voice_history_restores_original_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    BinaryData.__table__.create(engine)
    Messages.__table__.create(engine)
    monkeypatch.setattr(database, "initialize_database", lambda: None)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(engine, class_=Session, autoflush=False))
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))

    data = b"ID3" + bytes(range(256))
    message = SessionMessage(message_id="voice-1", timestamp=datetime.now(), platform="internal")
    message.message_info = MessageInfo(UserInfo("bot", "Bot"))
    message.session_id = "private-chat"
    message.reply_frequency = 1
    message.raw_message = MessageSequence([]).voice(data, "你好").voice(data, "再听一次")
    MessageUtils.store_message_to_db(message)

    with Session(engine) as session:
        history = SessionMessage.from_db_instance(session.exec(select(Messages)).one()).raw_message
        path = tmp_path / session.exec(select(BinaryData)).one().full_path
        old_sent = {"schema_version": 1, "session_id": message.session_id, "message_id": message.message_id, "media": []}
        other_chat = {**old_sent, "session_id": "other-chat"}
        restore_legacy_voice_media(session, [
            {"event": "message.sent", "data": old_sent}, {"event": "message.ingested", "data": other_chat},
        ])
    assert old_sent["media"] == build_monitor_message_media(message)
    assert [item["kind"] for item in old_sent["media"]] == ["voice", "voice"]
    assert not other_chat["media"]
    expected = {"type": "voice", "data": f"data:audio/mpeg;base64,{b64encode(data).decode()}"}
    assert path.read_bytes() == data
    assert serialize_message_sequence(message.raw_message) == [expected, expected]
    assert serialize_message_sequence(history) == [expected, expected]

    path.unlink()
    assert serialize_message_sequence(history) == [
        {"type": "text", "data": "你好"}, {"type": "text", "data": "再听一次"},
    ]
    engine.dispose()
