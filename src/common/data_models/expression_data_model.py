from datetime import datetime
from typing import List, Optional

import json

from src.common.database.database_model import Expression, ModifiedBy

from . import BaseDatabaseDataModel


class MaiExpression(BaseDatabaseDataModel[Expression]):
    def __init__(
        self,
        item_id: int,
        situation: str,
        style: str,
        # context: str,
        # up_content: str,
        content: List[str],
        count: int,
        last_active_time: datetime,
        create_time: datetime,
        session_id: Optional[str] = None,
        checked: bool = False,
        rejected: bool = False,
        modified_by: Optional[ModifiedBy] = None,
    ):
        self.item_id = item_id
        """自增主键ID"""
        self.situation = situation
        """表达方式使用情景"""
        self.style = style
        """表达方式风格"""
        # self.context = context
        # """表达方式上下文"""
        # self.up_content = up_content
        self.content: List[str] = content
        """内容列表"""
        self.count: int = count
        self.last_active_time: datetime = last_active_time or datetime.now()
        self.create_time: datetime = create_time or datetime.now()
        self.session_id: Optional[str] = session_id

        self.checked: bool = checked
        """是否已经被检查过"""
        self.rejected: bool = rejected
        """是否被拒绝但是未更新"""
        self.modified_by: Optional[ModifiedBy] = modified_by
        """最后修改者，标记用户或AI，为空表示未检查"""

    @classmethod
    def from_db_instance(cls, db_record: Expression):
        content_list = json.loads(db_record.content_list) if db_record.content_list else []
        for item in content_list:
            if not isinstance(item, str):
                raise ValueError(f"Content item must be a string, got {type(item)}")
        return cls(
            item_id=db_record.id,  # type: ignore
            situation=db_record.situation,
            style=db_record.style,
            # context=db_record.context,
            content=content_list,
            count=db_record.count,
            last_active_time=db_record.last_active_time,
            create_time=db_record.create_time,
            session_id=db_record.session_id,
            checked=db_record.checked,
            rejected=db_record.rejected,
            modified_by=db_record.modified_by,
        )

    def to_db_instance(self):
        for item in self.content:
            if not isinstance(item, str):
                raise ValueError(f"Content item must be a string, got {type(item)}")
        return Expression(
            id=self.item_id,
            situation=self.situation,
            style=self.style,
            # context=self.context,
            content_list=json.dumps(self.content),
            count=self.count,
            last_active_time=self.last_active_time,
            create_time=self.create_time,
            session_id=self.session_id,
            checked=self.checked,
            rejected=self.rejected,
            modified_by=self.modified_by,
        )
