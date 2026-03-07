"""数据库服务模块

提供数据库操作相关的核心功能。
"""

import json
import time
import traceback
from typing import Any, Optional

from src.common.logger import get_logger

logger = get_logger("database_service")


def _to_dict(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    if isinstance(record, dict):
        return record
    if hasattr(record, "model_dump"):
        return record.model_dump()
    if hasattr(record, "__dict__"):
        return dict(record.__dict__)
    return {}


async def db_query(
    model_class,
    data: Optional[dict[str, Any]] = None,
    query_type: str = "get",
    filters: Optional[dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by: Optional[list[str]] = None,
    single_result: bool = False,
):
    try:
        if query_type not in ["get", "create", "update", "delete", "count"]:
            raise ValueError("query_type must be 'get' or 'create' or 'update' or 'delete' or 'count'")

        if query_type == "get":
            query = model_class.select()
            if filters:
                for field, value in filters.items():
                    query = query.where(getattr(model_class, field) == value)
            if order_by:
                query = query.order_by(*order_by)
            if limit:
                query = query.limit(limit)
            results = list(query.dicts())
            if single_result:
                return results[0] if results else None
            return results

        if query_type == "create":
            if not data:
                raise ValueError("创建记录需要提供data参数")
            record = model_class.create(**data)
            return _to_dict(record)

        query = model_class.select()
        if filters:
            for field, value in filters.items():
                query = query.where(getattr(model_class, field) == value)

        if query_type == "update":
            if not data:
                raise ValueError("更新记录需要提供data参数")
            return query.model_class.update(**data).where(*query.stmt._where_criteria).execute()

        if query_type == "delete":
            return model_class.delete().where(*query.stmt._where_criteria).execute()

        return query.count()
    except Exception as e:
        logger.error(f"[DatabaseService] 数据库操作出错: {e}")
        traceback.print_exc()
        if query_type == "get":
            return None if single_result else []
        return None


async def db_save(model_class, data: dict[str, Any], key_field: Optional[str] = None, key_value: Optional[Any] = None):
    try:
        if key_field and key_value is not None:
            record = model_class.get_or_none(getattr(model_class, key_field) == key_value)
            if record is not None:
                for field, value in data.items():
                    setattr(record, field, value)
                record.save()
                return _to_dict(record)

        new_record = model_class.create(**data)
        return _to_dict(new_record)
    except Exception as e:
        logger.error(f"[DatabaseService] 保存数据库记录出错: {e}")
        traceback.print_exc()
        return None


async def db_get(
    model_class,
    filters: Optional[dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by: Optional[str] = None,
    single_result: bool = False,
):
    try:
        query = model_class.select()
        if filters:
            for field, value in filters.items():
                query = query.where(getattr(model_class, field) == value)
        if order_by:
            query = query.order_by(order_by)
        if limit:
            query = query.limit(limit)
        results = list(query.dicts())
        if single_result:
            return results[0] if results else None
        return results
    except Exception as e:
        logger.error(f"[DatabaseService] 获取数据库记录出错: {e}")
        traceback.print_exc()
        return None if single_result else []


async def store_action_info(
    chat_stream=None,
    action_build_into_prompt: bool = False,
    action_prompt_display: str = "",
    action_done: bool = True,
    thinking_id: str = "",
    action_data: Optional[dict] = None,
    action_name: str = "",
    action_reasoning: str = "",
):
    try:
        from src.common.database.database_model import ActionRecords

        record_data = {
            "action_id": thinking_id or str(int(time.time() * 1000000)),
            "time": time.time(),
            "action_name": action_name,
            "action_data": json.dumps(action_data or {}, ensure_ascii=False),
            "action_done": action_done,
            "action_reasoning": action_reasoning,
            "action_build_into_prompt": action_build_into_prompt,
            "action_prompt_display": action_prompt_display,
        }

        if chat_stream:
            record_data.update(
                {
                    "chat_id": getattr(chat_stream, "stream_id", ""),
                    "chat_info_stream_id": getattr(chat_stream, "stream_id", ""),
                    "chat_info_platform": getattr(chat_stream, "platform", ""),
                }
            )
        else:
            record_data.update({"chat_id": "", "chat_info_stream_id": "", "chat_info_platform": ""})

        saved_record = await db_save(
            ActionRecords, data=record_data, key_field="action_id", key_value=record_data["action_id"]
        )
        if saved_record:
            logger.debug(f"[DatabaseService] 成功存储动作信息: {action_name} (ID: {record_data['action_id']})")
        else:
            logger.error(f"[DatabaseService] 存储动作信息失败: {action_name}")
        return saved_record
    except Exception as e:
        logger.error(f"[DatabaseService] 存储动作信息时发生错误: {e}")
        traceback.print_exc()
        return None
