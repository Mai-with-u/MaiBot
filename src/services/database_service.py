"""数据库服务模块。"""

import json
import time
import traceback
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, cast

from sqlalchemy import delete, func
from sqlmodel import SQLModel, select

from src.common.database.database import get_db_session
from src.common.database.database_model import ToolRecord
from src.common.logger import get_logger

if TYPE_CHECKING:
    from src.chat.message_receive.chat_manager import BotChatSession

logger = get_logger("database_service")


def _to_msgpack_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {_to_msgpack_value(key): _to_msgpack_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_msgpack_value(item) for item in value]
    return value


def _to_dict(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    if isinstance(record, dict):
        return _to_msgpack_value(record)
    if hasattr(record, "model_dump"):
        return _to_msgpack_value(record.model_dump())
    return _to_msgpack_value(dict(record.__dict__)) if hasattr(record, "__dict__") else {}


def _get_model_field(model_class: type[SQLModel], field_name: str) -> Any:
    field = getattr(model_class, field_name, None)
    if field is None:
        raise ValueError(f"{model_class.__name__} 不存在字段 {field_name}")
    return field


def _build_filters(model_class: type[SQLModel], filters: Optional[dict[str, Any]] = None) -> list[Any]:
    if not filters:
        return []
    return [_get_model_field(model_class, field_name) == value for field_name, value in filters.items()]


def _apply_order_by(statement: Any, model_class: type[SQLModel], order_by: Optional[str | list[str]] = None) -> Any:
    if not order_by:
        return statement

    order_fields = [order_by] if isinstance(order_by, str) else order_by
    clauses = []
    for item in order_fields:
        descending = item.startswith("-")
        field_name = item[1:] if descending else item
        field = _get_model_field(model_class, field_name)
        clauses.append(field.desc() if descending else field.asc())
    return statement.order_by(*clauses)


async def db_save(
    model_class: type[SQLModel],
    data: dict[str, Any],
    key_field: Optional[str] = None,
    key_value: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    try:
        with get_db_session() as session:
            record = None
            if key_field and key_value is not None:
                key_column = _get_model_field(model_class, key_field)
                record = session.exec(cast(Any, select(model_class).where(key_column == key_value))).first()

            if record is None:
                record = model_class(**data)
            else:
                for field_name, value in data.items():
                    _get_model_field(model_class, field_name)
                    setattr(record, field_name, value)

            session.add(record)
            session.flush()
            session.refresh(record)
            return _to_dict(record)
    except Exception as e:
        logger.error(f"[DatabaseService] 保存数据库记录出错: {e}")
        traceback.print_exc()
        return None


async def db_get(
    model_class: type[SQLModel],
    filters: Optional[dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by: Optional[str | list[str]] = None,
    single_result: bool = False,
) -> Optional[dict[str, Any]] | list[dict[str, Any]]:
    try:
        with get_db_session(auto_commit=False) as session:
            statement = select(model_class)
            if conditions := _build_filters(model_class, filters):
                statement = statement.where(*conditions)
            statement = _apply_order_by(statement, model_class, order_by)
            if limit:
                statement = statement.limit(limit)
            results = session.exec(cast(Any, statement)).all()
            data = [_to_dict(item) for item in results]
            if single_result:
                return data[0] if data else None
            return data
    except Exception as e:
        logger.error(f"[DatabaseService] 获取数据库记录出错: {e}")
        traceback.print_exc()
        return None if single_result else []


async def db_update(model_class: type[SQLModel], data: dict[str, Any], filters: Optional[dict[str, Any]] = None) -> int:
    try:
        with get_db_session() as session:
            statement = select(model_class)
            if conditions := _build_filters(model_class, filters):
                statement = statement.where(*conditions)
            records = session.exec(cast(Any, statement)).all()
            for record in records:
                for field_name, value in data.items():
                    _get_model_field(model_class, field_name)
                    setattr(record, field_name, value)
                session.add(record)
            return len(records)
    except Exception as e:
        logger.error(f"[DatabaseService] 更新数据库记录出错: {e}")
        traceback.print_exc()
        return 0


async def db_delete(model_class: type[SQLModel], filters: Optional[dict[str, Any]] = None) -> int:
    try:
        with get_db_session() as session:
            statement = delete(model_class)
            if conditions := _build_filters(model_class, filters):
                statement = statement.where(*conditions)
            result = session.exec(statement)
            return result.rowcount or 0
    except Exception as e:
        logger.error(f"[DatabaseService] 删除数据库记录出错: {e}")
        traceback.print_exc()
        return 0


async def db_count(model_class: type[SQLModel], filters: Optional[dict[str, Any]] = None) -> int:
    try:
        with get_db_session(auto_commit=False) as session:
            statement = select(func.count()).select_from(model_class)
            if conditions := _build_filters(model_class, filters):
                statement = statement.where(*conditions)
            result = session.exec(cast(Any, statement)).one()
            return int(result or 0)
    except Exception as e:
        logger.error(f"[DatabaseService] 统计数据库记录出错: {e}")
        traceback.print_exc()
        return 0


async def store_tool_info(
    chat_stream: "BotChatSession",
    tool_id: str = "",
    tool_data: Optional[dict[str, Any]] = None,
    tool_name: str = "",
    tool_reasoning: str = "",
) -> Optional[dict[str, Any]]:
    try:
        record_data = {
            "tool_id": tool_id or str(int(time.time() * 1000000)),
            "timestamp": datetime.now(),
            "session_id": chat_stream.session_id,
            "tool_name": tool_name,
            "tool_data": json.dumps(tool_data or {}, ensure_ascii=False),
            "tool_reasoning": tool_reasoning,
        }

        saved_record = await db_save(ToolRecord, data=record_data, key_field="tool_id", key_value=record_data["tool_id"])
        if saved_record:
            logger.debug(f"[DatabaseService] 成功存储工具信息: {tool_name} (ID: {record_data['tool_id']})")
        else:
            logger.error(f"[DatabaseService] 存储工具信息失败: {tool_name}")
        return saved_record
    except Exception as e:
        logger.error(f"[DatabaseService] 存储工具信息时发生错误: {e}")
        traceback.print_exc()
        return None


async def store_action_info(
    chat_stream: "BotChatSession",
    thinking_id: str = "",
    action_data: Optional[dict[str, Any]] = None,
    action_name: str = "",
    action_reasoning: str = "",
) -> Optional[dict[str, Any]]:
    """兼容旧接口，内部转发到 ``store_tool_info``。"""
    return await store_tool_info(
        chat_stream=chat_stream,
        tool_id=thinking_id,
        tool_data=action_data,
        tool_name=action_name,
        tool_reasoning=action_reasoning,
    )
