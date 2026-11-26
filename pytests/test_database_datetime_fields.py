from datetime import datetime
from typing import get_args

from pydantic_core import PydanticUndefined
from sqlalchemy import Column
from sqlmodel.main import FieldInfo

from src.common.database.database_model import JargonLearningRecord


def test_datetime_fields_declare_storage_type() -> None:
    missing = []
    for mapper in JargonLearningRecord.__mapper__.registry.mappers:
        model = mapper.class_
        if not model.__module__.startswith("src."):
            continue
        for prop in mapper.column_attrs:
            field = model.model_fields[prop.key]
            if field.annotation is not datetime and datetime not in get_args(field.annotation):
                continue
            if isinstance(field, FieldInfo) and (
                isinstance(field.sa_column, Column) or field.sa_type is not PydanticUndefined
            ):
                continue
            missing.append(f"{mapper.local_table.name}.{prop.columns[0].name}")

    assert not missing, "请为时间列显式声明数据库类型（sa_type 或 sa_column）：" + ", ".join(sorted(missing))
