from contextlib import contextmanager
from json import loads
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from src.common.database.database_model import ToolRecord
from src.services import database_service as service_module


@pytest.fixture
def tool_record_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ToolRecord.__table__.create(engine)

    @contextmanager
    def get_test_session(auto_commit: bool = True):
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                if auto_commit:
                    session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(service_module, "get_db_session", get_test_session)


def _tool_record_data(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tool_id": "tool-1",
        "session_id": "session-1",
        "tool_name": "echo",
        "tool_data": "{}",
        "tool_reasoning": "测试",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_db_save_returns_record_dict(tool_record_db: None) -> None:
    saved = await service_module.db_save(ToolRecord, _tool_record_data())

    assert saved["tool_id"] == "tool-1"
    assert saved["session_id"] == "session-1"
    assert saved["tool_name"] == "echo"


@pytest.mark.asyncio
async def test_db_save_updates_existing_record_by_key(tool_record_db: None) -> None:
    await service_module.db_save(ToolRecord, _tool_record_data())
    updated = await service_module.db_save(
        ToolRecord,
        _tool_record_data(tool_name="updated"),
        key_field="tool_id",
        key_value="tool-1",
    )

    assert updated["tool_name"] == "updated"
    records = await service_module.db_get(ToolRecord, filters={"tool_id": "tool-1"})
    assert isinstance(records, list)
    assert len(records) == 1
    assert records[0]["tool_name"] == "updated"


@pytest.mark.asyncio
async def test_db_save_unknown_field_raises(tool_record_db: None) -> None:
    await service_module.db_save(ToolRecord, _tool_record_data())

    with pytest.raises(ValueError, match="不存在字段 missing_field"):
        await service_module.db_save(
            ToolRecord,
            {"missing_field": "x"},
            key_field="tool_id",
            key_value="tool-1",
        )


@pytest.mark.asyncio
async def test_db_get_returns_empty_list_when_missing(tool_record_db: None) -> None:
    records = await service_module.db_get(ToolRecord, filters={"tool_id": "missing"})

    assert records == []


@pytest.mark.asyncio
async def test_db_get_single_result_returns_none_when_missing(tool_record_db: None) -> None:
    record = await service_module.db_get(
        ToolRecord,
        filters={"tool_id": "missing"},
        single_result=True,
    )

    assert record is None


@pytest.mark.asyncio
async def test_db_get_unknown_field_raises(tool_record_db: None) -> None:
    with pytest.raises(ValueError, match="不存在字段 missing_field"):
        await service_module.db_get(ToolRecord, filters={"missing_field": "x"})


@pytest.mark.asyncio
async def test_db_update_delete_count_success_and_unknown_field_raises(tool_record_db: None) -> None:
    await service_module.db_save(ToolRecord, _tool_record_data())

    updated = await service_module.db_update(
        ToolRecord,
        {"tool_name": "counted"},
        filters={"tool_id": "tool-1"},
    )
    assert updated == 1
    assert await service_module.db_count(ToolRecord, filters={"tool_name": "counted"}) == 1

    with pytest.raises(ValueError, match="不存在字段 missing_field"):
        await service_module.db_update(
            ToolRecord,
            {"missing_field": "x"},
            filters={"tool_id": "tool-1"},
        )
    with pytest.raises(ValueError, match="不存在字段 missing_field"):
        await service_module.db_delete(ToolRecord, filters={"missing_field": "x"})
    with pytest.raises(ValueError, match="不存在字段 missing_field"):
        await service_module.db_count(ToolRecord, filters={"missing_field": "x"})

    deleted = await service_module.db_delete(ToolRecord, filters={"tool_id": "tool-1"})
    assert deleted == 1
    assert await service_module.db_count(ToolRecord) == 0


@pytest.mark.asyncio
async def test_db_session_failure_raises(tool_record_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def failing_session(auto_commit: bool = True):
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(service_module, "get_db_session", failing_session)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service_module.db_save(ToolRecord, _tool_record_data())
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service_module.db_get(ToolRecord)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service_module.db_update(ToolRecord, {"tool_name": "x"})
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service_module.db_delete(ToolRecord, filters={"tool_id": "tool-1"})
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service_module.db_count(ToolRecord)


@pytest.mark.asyncio
async def test_store_tool_info_returns_record_dict(tool_record_db: None) -> None:
    saved = await service_module.store_tool_info(
        chat_stream=SimpleNamespace(session_id="session-1"),
        tool_id="call-1",
        tool_data={"ok": True},
        tool_name="echo",
        tool_reasoning="because",
    )

    assert saved["tool_id"] == "call-1"
    assert saved["session_id"] == "session-1"
    assert saved["tool_name"] == "echo"
    assert loads(saved["tool_data"]) == {"ok": True}


@pytest.mark.asyncio
async def test_store_tool_info_raises_when_save_fails(
    tool_record_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_save(*args, **kwargs):
        raise RuntimeError("保存失败")

    monkeypatch.setattr(service_module, "db_save", fail_save)

    with pytest.raises(RuntimeError, match="保存失败"):
        await service_module.store_tool_info(
            chat_stream=SimpleNamespace(session_id="session-1"),
            tool_id="call-1",
            tool_name="echo",
        )
