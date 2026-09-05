from unittest.mock import AsyncMock

import pytest

from src.plugin_runtime.capabilities.data import RuntimeDataCapabilityMixin
from src.services import database_service


@pytest.mark.asyncio
async def test_database_get_success_returns_record_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    record = {"tool_id": "t-1", "session_id": "s-1"}
    monkeypatch.setattr(database_service, "db_get", AsyncMock(return_value=record))
    capability = RuntimeDataCapabilityMixin()

    result = await capability._cap_database_get(
        "demo.plugin",
        "database.get",
        {"model_name": "ToolRecord", "filters": {"tool_id": "t-1"}, "single_result": True},
    )

    assert result == record


@pytest.mark.asyncio
async def test_database_save_success_returns_record_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    record = {"tool_id": "t-1", "tool_name": "echo"}
    monkeypatch.setattr(database_service, "db_save", AsyncMock(return_value=record))
    capability = RuntimeDataCapabilityMixin()

    result = await capability._cap_database_save(
        "demo.plugin",
        "database.save",
        {"model_name": "ToolRecord", "data": {"tool_id": "t-1"}},
    )

    assert result == record


@pytest.mark.asyncio
async def test_database_capability_failures_return_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database_service, "db_get", AsyncMock(side_effect=RuntimeError("查询失败")))
    monkeypatch.setattr(database_service, "db_save", AsyncMock(side_effect=RuntimeError("保存失败")))
    monkeypatch.setattr(database_service, "db_delete", AsyncMock(side_effect=RuntimeError("删除失败")))
    monkeypatch.setattr(database_service, "db_count", AsyncMock(side_effect=RuntimeError("统计失败")))
    capability = RuntimeDataCapabilityMixin()

    get_result = await capability._cap_database_get(
        "demo.plugin",
        "database.get",
        {"model_name": "ToolRecord"},
    )
    save_result = await capability._cap_database_save(
        "demo.plugin",
        "database.save",
        {"model_name": "ToolRecord", "data": {"tool_id": "t-1"}},
    )
    delete_result = await capability._cap_database_delete(
        "demo.plugin",
        "database.delete",
        {"model_name": "ToolRecord", "filters": {"tool_id": "t-1"}},
    )
    count_result = await capability._cap_database_count(
        "demo.plugin",
        "database.count",
        {"model_name": "ToolRecord"},
    )
    query_result = await capability._cap_database_query(
        "demo.plugin",
        "database.query",
        {"model_name": "ToolRecord", "query_type": "get"},
    )

    assert get_result == {"success": False, "error": "查询失败"}
    assert save_result == {"success": False, "error": "保存失败"}
    assert delete_result == {"success": False, "error": "删除失败"}
    assert count_result == {"success": False, "error": "统计失败"}
    assert query_result == {"success": False, "error": "查询失败"}
