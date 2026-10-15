from pathlib import Path

import msgpack
import pytest
import sqlite3

from scripts.rebuild_high_frequency_terms import apply_rebuilt_rows, build_rebuilt_rows


def make_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE high_frequency_terms (id INTEGER PRIMARY KEY,chat_id TEXT,term TEXT,rank INTEGER,"
        "occurrence_count INTEGER,message_count INTEGER,frequency REAL,message_frequency REAL,"
        "created_at TEXT,updated_at TEXT)"
    )
    connection.execute("INSERT INTO high_frequency_terms VALUES (1,'chat','事件回应',1,500,500,1,1,'old','old')")
    connection.execute(
        "CREATE TABLE mai_messages (id INTEGER PRIMARY KEY,session_id TEXT,platform TEXT,user_id TEXT,"
        "is_notify INTEGER,message_id TEXT,raw_content BLOB)"
    )
    messages = [
        ("human", 0, "1", [{"type": "text", "data": "模型模型"}]),
        ("human", 0, "1", [{"type": "text", "data": "模型模型"}]),
        ("human", 1, "2", [{"type": "text", "data": "事件回应事件回应"}]),
        ("bot", 0, "3", [{"type": "text", "data": "机器人机器人"}]),
        ("human", 0, "4", [{"type": "image", "data": "图片描述图片描述"}]),
    ]
    for user_id, notify, message_id, components in messages:
        connection.execute(
            "INSERT INTO mai_messages (session_id,platform,user_id,is_notify,message_id,raw_content) "
            "VALUES ('chat','qq',?,?,?,?)",
            (user_id, notify, message_id, msgpack.packb(components)),
        )
    connection.commit()
    return connection


def test_rebuild_excludes_notifications_self_media_and_duplicate_messages(tmp_path: Path) -> None:
    with make_database() as connection:
        expected = connection.execute("SELECT * FROM high_frequency_terms ORDER BY id").fetchall()
        rows, report = build_rebuilt_rows(connection, {("qq", "bot")}, "now")
        assert [(row[1], row[3], row[4]) for row in rows] == [("模型", 2, 1)]
        assert report["excluded_notifications"] == 1
        assert report["excluded_self_messages"] == 1
        backup_path = tmp_path / "backup.sqlite3"
        apply_rebuilt_rows(connection, rows, expected, backup_path)
        assert connection.execute("SELECT term FROM high_frequency_terms").fetchall() == [("模型",)]
        with sqlite3.connect(backup_path) as backup:
            assert backup.execute("SELECT * FROM high_frequency_terms ORDER BY id").fetchall() == expected


def test_concurrent_learning_aborts_without_deleting_data(tmp_path: Path) -> None:
    with make_database() as connection:
        expected = connection.execute("SELECT * FROM high_frequency_terms ORDER BY id").fetchall()
        connection.execute("UPDATE high_frequency_terms SET occurrence_count=501")
        connection.commit()
        with pytest.raises(RuntimeError, match="词库发生变化"):
            apply_rebuilt_rows(connection, [], expected, tmp_path / "backup.sqlite3")
        assert connection.execute("SELECT occurrence_count FROM high_frequency_terms").fetchone()[0] == 501
